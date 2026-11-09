"""Reading and safely writing the game's settings.save (mod enable state + load order)."""

import glob
import json
import os
import shutil
import time

from . import game as game_mod, osenv

BACKUP_DIR = os.path.join(osenv.data_dir(), "backups")
KEEP_BACKUPS = 30


class GameRunningError(RuntimeError):
    pass


class Settings:
    def __init__(self, path):
        self.path = path
        self.mtime = os.path.getmtime(path)
        with open(path, encoding="utf-8-sig") as f:
            self.data = json.load(f)

    @property
    def _mod_settings(self):
        ms = self.data.get("mod_settings")
        if not isinstance(ms, dict):
            ms = self.data["mod_settings"] = {"mod_list": []}
        ms.setdefault("mod_list", [])
        return ms

    @property
    def mods_enabled(self):
        return self._mod_settings.get("mods_enabled", True) is not False

    def order(self):
        return [e.get("id") for e in self._mod_settings["mod_list"] if isinstance(e, dict)]

    def enabled_map(self):
        return {e["id"]: e.get("is_enabled", True) is not False
                for e in self._mod_settings["mod_list"] if isinstance(e, dict) and "id" in e}

    def is_enabled(self, mod_id):
        # A mod the game hasn't recorded yet is loaded, i.e. enabled.
        return self.enabled_map().get(mod_id, True)

    def changed_on_disk(self):
        try:
            return os.path.getmtime(self.path) != self.mtime
        except OSError:
            return True

    def save(self, states, order, mods, mods_enabled=None):
        """states: {id: bool}; order: list of ids (load order); mods: {id: Mod} for source info."""
        if game_mod.is_running():
            raise GameRunningError("Slay the Spire 2 is running. Close it first: "
                                   "it overwrites settings.save when it exits.")
        if self.changed_on_disk():
            # Re-read so we don't clobber anything the game wrote meanwhile.
            fresh = Settings(self.path)
            self.data, self.mtime = fresh.data, fresh.mtime
        ms = self._mod_settings
        existing = {e.get("id"): e for e in ms["mod_list"] if isinstance(e, dict)}
        new_list = []
        for mod_id in order:
            entry = dict(existing.get(mod_id) or {})
            entry["id"] = mod_id
            if mod_id in states:
                entry["is_enabled"] = bool(states[mod_id])
            entry.setdefault("is_enabled", True)
            if "source" not in entry and mod_id in mods:
                entry["source"] = mods[mod_id].source
            new_list.append(entry)
        # Keep entries for mods that aren't installed right now (e.g. unsubscribed) untouched.
        for mod_id, entry in existing.items():
            if mod_id not in order:
                new_list.append(entry)
        ms["mod_list"] = new_list
        if mods_enabled is not None:
            ms["mods_enabled"] = bool(mods_enabled)

        backup = make_backup(self.path)
        tmp = self.path + ".sts2ml.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        shutil.copymode(self.path, tmp)
        os.replace(tmp, self.path)
        self.mtime = os.path.getmtime(self.path)
        return backup


def make_backup(path):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"settings-{stamp}.save")
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(BACKUP_DIR, f"settings-{stamp}-{n}.save")
        n += 1
    shutil.copy2(path, dest)
    for old in list_backups()[KEEP_BACKUPS:]:
        try:
            os.remove(old)
        except OSError:
            pass
    return dest


def list_backups():
    return sorted(glob.glob(os.path.join(BACKUP_DIR, "settings-*.save")),
                  key=os.path.getmtime, reverse=True)


def restore_backup(backup, path):
    if game_mod.is_running():
        raise GameRunningError("Close Slay the Spire 2 before restoring a backup.")
    with open(backup, "rb") as f:
        content = f.read()   # read first: making a new backup may prune this one
    make_backup(path)
    with open(path, "wb") as f:
        f.write(content)
