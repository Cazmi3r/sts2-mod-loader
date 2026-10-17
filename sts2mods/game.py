"""Locating the game, its user data, its version and whether it is running."""

import glob
import json
import os
import re
import subprocess
from dataclasses import dataclass, field

from . import APP_ID, vdf

HOME = os.path.expanduser("~")
STEAM_ROOT_CANDIDATES = [
    "~/.steam/steam",
    "~/.steam/debian-installation",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",
]
USER_DATA_CANDIDATES = [
    "~/.local/share/SlayTheSpire2",
    "~/.var/app/com.valvesoftware.Steam/.local/share/SlayTheSpire2",
]
PROCESS_NAME = "SlayTheSpire2"


def parse_version(text):
    """'v0.111.0' -> (0, 111, 0). Returns None for anything unparseable."""
    if not text:
        return None
    m = re.match(r"\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(text))
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


def format_version(v):
    return ".".join(str(x) for x in v) if v else "?"


@dataclass
class Game:
    steam_root: str = None
    library: str = None          # steam library containing the game
    install_dir: str = None
    workshop_dir: str = None     # .../workshop/content/<appid>
    workshop_acf: str = None
    user_data: str = None        # ~/.local/share/SlayTheSpire2
    settings_path: str = None    # active settings.save
    version: tuple = None
    version_text: str = "?"
    branch: str = "public"
    problems: list = field(default_factory=list)

    @property
    def mods_dir(self):
        return os.path.join(self.install_dir, "mods") if self.install_dir else None

    @property
    def logs_dir(self):
        return os.path.join(self.user_data, "logs") if self.user_data else None


def _library_paths(steam_root):
    paths = [steam_root]
    for rel in ("steamapps/libraryfolders.vdf", "config/libraryfolders.vdf"):
        p = os.path.join(steam_root, rel)
        if os.path.exists(p):
            try:
                data = vdf.load(p).get("libraryfolders", {})
            except OSError:
                continue
            for entry in data.values():
                if isinstance(entry, dict) and entry.get("path"):
                    paths.append(entry["path"])
    seen, out = set(), []
    for p in paths:
        rp = os.path.realpath(p)
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def locate():
    game = Game()
    for cand in STEAM_ROOT_CANDIDATES:
        root = os.path.expanduser(cand)
        if not os.path.isdir(root):
            continue
        for lib in _library_paths(root):
            acf = os.path.join(lib, "steamapps", f"appmanifest_{APP_ID}.acf")
            if os.path.exists(acf):
                game.steam_root, game.library = root, lib
                try:
                    manifest = vdf.load(acf).get("AppState", {})
                except OSError:
                    manifest = {}
                installdir = manifest.get("installdir", "Slay the Spire 2")
                game.install_dir = os.path.join(lib, "steamapps", "common", installdir)
                beta = (manifest.get("UserConfig", {}) or {}).get("BetaKey") or \
                       (manifest.get("MountedConfig", {}) or {}).get("BetaKey")
                game.branch = beta or "public"
                game.workshop_dir = os.path.join(lib, "steamapps", "workshop", "content", APP_ID)
                game.workshop_acf = os.path.join(lib, "steamapps", "workshop", f"appworkshop_{APP_ID}.acf")
                break
        if game.install_dir:
            break
    if not game.install_dir:
        game.problems.append("Could not find Slay the Spire 2 in any Steam library.")
    else:
        try:
            with open(os.path.join(game.install_dir, "release_info.json")) as f:
                info = json.load(f)
            game.version_text = info.get("version", "?")
            game.version = parse_version(game.version_text)
        except (OSError, ValueError):
            game.problems.append("Could not read the game's release_info.json.")

    for cand in USER_DATA_CANDIDATES:
        p = os.path.expanduser(cand)
        if os.path.isdir(p):
            game.user_data = p
            break
    if game.user_data:
        saves = glob.glob(os.path.join(game.user_data, "*", "*", "settings.save"))
        saves = [s for s in saves if os.sep + "modded" + os.sep not in s]
        if saves:
            game.settings_path = max(saves, key=os.path.getmtime)
    if not game.settings_path:
        game.problems.append("No settings.save found. Launch the game once so it creates one.")
    return game


def is_running():
    """True if the game process exists (checked via /proc, never matches ourselves)."""
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/comm") as f:
                if f.read().strip() == PROCESS_NAME:
                    return True
        except OSError:
            continue
    return False


def launch():
    subprocess.Popen(["xdg-open", f"steam://rungameid/{APP_ID}"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def open_path(path):
    subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def open_workshop_page(workshop_id):
    open_path(f"steam://url/CommunityFilePage/{workshop_id}")
