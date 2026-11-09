"""Subscribing to and unsubscribing from Workshop items through Steam itself.

Steam only accepts Workshop subscriptions from the game they belong to, so this uses the copy
of Steam's API library that ships with Slay the Spire 2 and talks to the running Steam client
as the game (app id 2868840). Steam downloads the item exactly as if you had clicked Subscribe
on the Workshop page.

Each request runs in a short-lived child process (this app started again with --steam-helper,
which also works from the packaged Windows .exe): a crash in
native code can't take the window down, and Steam only sees "playing Slay the Spire 2" for the
second or two the request takes.
"""

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time

from . import APP_ID, osenv

LIB_NAMES = ("steam_api64.dll", "steam_api.dll") if osenv.IS_WINDOWS else \
    ("libsteam_api.dylib",) if osenv.IS_MAC else ("libsteam_api.so",)

# Callback ids (k_iSteamRemoteStorageCallbacks = 1300) for the async call results.
CB_SUBSCRIBE, CB_UNSUBSCRIBE = 1313, 1315
# { EResult m_eResult; PublishedFileId_t m_nPublishedFileId; } packed to 8 on Windows, 4 elsewhere.
RESULT_SIZE = 16 if osenv.IS_WINDOWS else 12

# Bits from ISteamUGC::GetItemState.
STATE_SUBSCRIBED, STATE_INSTALLED, STATE_NEEDS_UPDATE = 1, 4, 8
STATE_DOWNLOADING, STATE_DOWNLOAD_PENDING = 16, 32

ERESULT_TEXT = {2: "Steam reported a generic failure", 3: "Steam is offline",
                9: "the item doesn't exist or was removed", 15: "access denied (do you own the game?)",
                16: "Steam timed out", 25: "you've hit Steam's subscription limit",
                84: "Steam is rate-limiting requests; try again in a minute"}


class SteamError(RuntimeError):
    pass


def find_library(install_dir):
    """Steam's API library inside the game folder, or None."""
    if not install_dir or not os.path.isdir(install_dir):
        return None
    for dp, _dirs, files in os.walk(install_dir):
        if os.path.basename(dp) == "mods":
            _dirs[:] = []
            continue
        for name in LIB_NAMES:
            if name in files:
                return os.path.join(dp, name)
    return None


# ---- in the child process -----------------------------------------------------------------

def _fn(lib, name, restype, *argtypes):
    f = getattr(lib, name)
    f.restype, f.argtypes = restype, list(argtypes)
    return f


def _versioned(lib, prefix, newest, oldest):
    for v in range(newest, oldest - 1, -1):
        name = f"{prefix}{v:03d}"
        if hasattr(lib, name):
            return _fn(lib, name, ctypes.c_void_p)()
    raise SteamError(f"this Steam library has no {prefix}* interface")


class _Steam:
    def __init__(self, lib_path):
        os.environ["SteamAppId"] = os.environ["SteamGameId"] = APP_ID
        try:
            self.lib = ctypes.CDLL(lib_path)
        except OSError as e:
            raise SteamError(f"couldn't load Steam's library: {e}") from e
        lib = self.lib
        if hasattr(lib, "SteamAPI_InitFlat"):
            err = ctypes.create_string_buffer(1024)
            res = _fn(lib, "SteamAPI_InitFlat", ctypes.c_int, ctypes.c_char_p)(err)
            if res != 0:
                raise SteamError("couldn't connect to Steam: " +
                                 (err.value.decode(errors="replace") or f"code {res}") +
                                 ". Is Steam running and signed in?")
        else:
            init = getattr(lib, "SteamAPI_InitSafe", None) or lib.SteamAPI_Init
            init.restype = ctypes.c_bool
            if not init():
                raise SteamError("couldn't connect to Steam. Is Steam running and signed in?")
        self.run_callbacks = _fn(lib, "SteamAPI_RunCallbacks", None)
        self.ugc = _versioned(lib, "SteamAPI_SteamUGC_v", 30, 14)
        self.utils = _versioned(lib, "SteamAPI_SteamUtils_v", 20, 9)
        u64, vp, b = ctypes.c_uint64, ctypes.c_void_p, ctypes.c_bool
        self._subscribe = _fn(lib, "SteamAPI_ISteamUGC_SubscribeItem", u64, vp, u64)
        self._unsubscribe = _fn(lib, "SteamAPI_ISteamUGC_UnsubscribeItem", u64, vp, u64)
        self._download = _fn(lib, "SteamAPI_ISteamUGC_DownloadItem", b, vp, u64, b)
        self._state = _fn(lib, "SteamAPI_ISteamUGC_GetItemState", ctypes.c_uint32, vp, u64)
        self._done = _fn(lib, "SteamAPI_ISteamUtils_IsAPICallCompleted", b, vp, u64, ctypes.POINTER(b))
        self._result = _fn(lib, "SteamAPI_ISteamUtils_GetAPICallResult", b, vp, u64, vp, ctypes.c_int,
                           ctypes.c_int, ctypes.POINTER(b))

    def close(self):
        _fn(self.lib, "SteamAPI_Shutdown", None)()

    def _wait(self, call, callback_id, timeout=20):
        if not call:
            raise SteamError("Steam refused the request")
        failed = ctypes.c_bool(False)
        deadline = time.time() + timeout
        while not self._done(self.utils, call, ctypes.byref(failed)):
            if time.time() > deadline:
                raise SteamError("Steam didn't answer in time")
            self.run_callbacks()
            time.sleep(0.05)
        buf = ctypes.create_string_buffer(RESULT_SIZE)
        ok = self._result(self.utils, call, buf, RESULT_SIZE, callback_id, ctypes.byref(failed))
        if not ok or failed.value:
            raise SteamError("Steam didn't confirm the request")
        code = int.from_bytes(buf.raw[:4], "little", signed=True)
        if code != 1:
            raise SteamError(ERESULT_TEXT.get(code, f"Steam returned error {code}"))

    def subscribe(self, wid):
        self._wait(self._subscribe(self.ugc, int(wid)), CB_SUBSCRIBE)
        self._download(self.ugc, int(wid), True)      # start now, ahead of other downloads

    def unsubscribe(self, wid):
        self._wait(self._unsubscribe(self.ugc, int(wid)), CB_UNSUBSCRIBE)

    def state(self, wid):
        return int(self._state(self.ugc, int(wid)))


def _child(argv):
    action, lib_path, ids = argv[0], argv[1], argv[2:]
    out = {"ok": True, "results": {}}
    try:
        steam = _Steam(lib_path)
    except SteamError as e:
        return {"ok": False, "error": str(e)}
    try:
        for wid in ids:
            try:
                if action == "subscribe":
                    steam.subscribe(wid)
                elif action == "unsubscribe":
                    steam.unsubscribe(wid)
                out["results"][wid] = {"ok": True, "state": steam.state(wid)}
            except SteamError as e:
                out["results"][wid] = {"ok": False, "error": str(e)}
    finally:
        steam.close()
    return out


# ---- in the app ---------------------------------------------------------------------------

def _request(action, game, ids, timeout=60):
    lib = find_library(game.install_dir)
    if not lib:
        raise SteamError("Steam's library wasn't found in the game folder")
    if getattr(sys, "frozen", False):            # packaged app: the executable is the app itself
        cmd, env, cwd = [sys.executable], None, None
    else:
        pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cmd = [sys.executable, "-m", "sts2mods"]
        env = dict(os.environ, PYTHONPATH=pkg_parent + os.pathsep + os.environ.get("PYTHONPATH", ""))
        cwd = pkg_parent
    # The answer comes back in a file: windowed apps (pythonw, the .exe) have no usable stdout.
    fd, out = tempfile.mkstemp(prefix="sts2ml-steam-", suffix=".json")
    os.close(fd)
    try:
        try:
            proc = subprocess.run(cmd + ["--steam-helper", out, action, lib, *map(str, ids)],
                                  capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd,
                                  creationflags=0x08000000 if osenv.IS_WINDOWS else 0)
        except subprocess.TimeoutExpired as e:
            raise SteamError("Steam didn't answer in time") from e
        except OSError as e:
            raise SteamError(str(e)) from e
        try:
            with open(out, encoding="utf-8") as f:
                res = json.load(f)
        except (OSError, ValueError):
            err = (proc.stderr or "").strip().splitlines()
            raise SteamError("the Steam helper stopped unexpectedly" + (f": {err[-1]}" if err else ""))
    finally:
        try:
            os.remove(out)
        except OSError:
            pass
    if not res.get("ok"):
        raise SteamError(res.get("error") or "unknown error")
    return res["results"]


def available(game):
    return bool(find_library(game.install_dir))


def subscribe(game, ids):
    """Blocking. Returns {wid: {"ok": bool, "error"?: str, "state": int}}. Raises SteamError when
    Steam can't be reached at all."""
    return _request("subscribe", game, ids)


def unsubscribe(game, ids):
    return _request("unsubscribe", game, ids)


def item_states(game, ids):
    return {w: r.get("state", 0) for w, r in _request("state", game, ids).items()}


def helper_main(argv):
    """Entry point of the child process: --steam-helper <result file> <action> <lib> <ids...>."""
    out, args = argv[0], argv[1:]
    result = _child(args)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f)
    os._exit(0)     # skip interpreter teardown around the native library
