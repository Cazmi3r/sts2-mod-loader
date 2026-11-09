"""Locating the game, its user data, its version and whether it is running."""

import glob
import json
import os
import re
from dataclasses import dataclass, field

from . import APP_ID, osenv, vdf

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
    user_data: str = None        # the game's user:// folder (settings.save, logs)
    settings_path: str = None    # active settings.save
    version: tuple = None
    version_text: str = "?"
    branch: str = "public"
    problems: list = field(default_factory=list)

    @property
    def mods_dir(self):
        exe_dir = osenv.game_exe_dir(self.install_dir)
        return os.path.join(exe_dir, "mods") if exe_dir else None

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


def _release_info_path(install_dir):
    """release_info.json sits next to the executable; on macOS that may be inside the .app."""
    exe_dir = osenv.game_exe_dir(install_dir)
    candidates = [os.path.join(install_dir, "release_info.json"),
                  os.path.join(exe_dir, "release_info.json"),
                  os.path.join(os.path.dirname(exe_dir), "Resources", "release_info.json")]
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


def locate():
    game = Game()
    for root in osenv.steam_roots():
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
            with open(_release_info_path(game.install_dir), encoding="utf-8-sig") as f:
                info = json.load(f)
            game.version_text = info.get("version", "?")
            game.version = parse_version(game.version_text)
        except (OSError, ValueError):
            game.problems.append("Could not read the game's release_info.json.")

    for p in osenv.game_user_data_candidates():
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
    """True if the game process exists (never matches this app)."""
    return osenv.is_process_running(PROCESS_NAME)


def launch():
    osenv.open_target(f"steam://rungameid/{APP_ID}")


def open_path(path):
    osenv.open_target(path)


def open_workshop_page(workshop_id):
    open_path(f"steam://url/CommunityFilePage/{workshop_id}")
