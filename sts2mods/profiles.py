"""Named mod setups ("profiles") and mod-list export/compare for co-op."""

import json
import os
import re
import time

from . import __version__

PROFILE_DIR = os.path.expanduser("~/.config/sts2-mod-loader/profiles")
EXPORT_FORMAT = "sts2-mod-loader/modlist-1"


def _safe(name):
    return re.sub(r"[^\w\- ]+", "_", name).strip() or "profile"


def list_profiles():
    if not os.path.isdir(PROFILE_DIR):
        return []
    out = []
    for f in sorted(os.listdir(PROFILE_DIR)):
        if f.endswith(".json"):
            try:
                with open(os.path.join(PROFILE_DIR, f), encoding="utf-8") as fh:
                    out.append(json.load(fh).get("name") or f[:-5])
            except (OSError, ValueError):
                continue
    return out


def _path(name):
    return os.path.join(PROFILE_DIR, _safe(name) + ".json")


def save_profile(name, states, order, mods):
    os.makedirs(PROFILE_DIR, exist_ok=True)
    data = {
        "name": name,
        "saved": time.strftime("%Y-%m-%d %H:%M"),
        "order": order,
        "mods": [{"id": mid, "enabled": bool(states.get(mid, True)),
                  "name": mods[mid].name if mid in mods else mid,
                  "workshop_id": mods[mid].workshop_id if mid in mods else None}
                 for mid in order],
    }
    with open(_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_profile(name):
    with open(_path(name), encoding="utf-8") as f:
        return json.load(f)


def delete_profile(name):
    try:
        os.remove(_path(name))
    except OSError:
        pass


def export_modlist(path, states, mods, game):
    data = {
        "format": EXPORT_FORMAT,
        "tool": f"sts2-mod-loader {__version__}",
        "exported": time.strftime("%Y-%m-%d %H:%M"),
        "game_version": game.version_text,
        "branch": game.branch,
        "mods": [{"id": m.id, "name": m.name, "version": m.version, "workshop_id": m.workshop_id,
                  "affects_gameplay": m.affects_gameplay}
                 for m in sorted(mods.values(), key=lambda m: m.name.lower())
                 if states.get(m.id, True)],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def import_modlist(path):
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("mods"), list):
        raise ValueError("This file isn't a mod list exported by STS2 Mod Loader.")
    return data


def compare(theirs, states, mods, game):
    """Differences that matter for joining each other's co-op games."""
    their_mods = {m["id"]: m for m in theirs["mods"] if isinstance(m, dict) and m.get("id")}
    mine = {mid: m for mid, m in mods.items() if states.get(mid, True)}
    diff = {"branch": None, "game_version": None, "missing": [], "disabled": [], "extra": [],
            "version": [], "optional_only": []}
    if theirs.get("branch") and theirs["branch"] != game.branch:
        diff["branch"] = (game.branch, theirs["branch"])
    if theirs.get("game_version") and theirs["game_version"] != game.version_text:
        diff["game_version"] = (game.version_text, theirs["game_version"])
    for mid, t in their_mods.items():
        gameplay = t.get("affects_gameplay", True) is not False
        if mid not in mods:
            (diff["missing"] if gameplay else diff["optional_only"]).append(t)
        elif mid not in mine:
            (diff["disabled"] if gameplay else diff["optional_only"]).append(t)
        elif t.get("version") and mods[mid].version != t["version"]:
            diff["version"].append((mods[mid], t))
    for mid, m in mine.items():
        if mid not in their_mods and m.affects_gameplay:
            diff["extra"].append(m)
    return diff


def diff_is_clean(diff):
    return not any(diff[k] for k in ("branch", "game_version", "missing", "disabled", "extra", "version"))
