"""Named mod setups ("profiles") and mod-list export/compare for co-op."""

import base64
import json
import os
import re
import time
import zlib

from . import __version__, osenv

PROFILE_DIR = os.path.join(osenv.config_dir(), "profiles")
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


def modlist_data(states, mods, game):
    """The enabled mods plus game version/branch: what a friend needs to compare setups."""
    return {
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


def export_modlist(path, states, mods, game):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(modlist_data(states, mods, game), f, indent=2, ensure_ascii=False)


def import_modlist(path):
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("mods"), list):
        raise ValueError("This file isn't a mod list exported by STS2 Mod Loader.")
    return data


# ---- share codes: the same mod list as one line of text to paste in a chat ------------------

SHARE_PREFIX = "STS2MODS1:"
RE_SHARE = re.compile(re.escape(SHARE_PREFIX) + r"([A-Za-z0-9_-]+)")


def share_code(states, mods, game):
    """A compact, paste-able code for the enabled mods. Workshop mods are sent without their
    names (the receiver looks those up on the Workshop), which keeps the code short."""
    data = modlist_data(states, mods, game)
    rows = []
    for m in data["mods"]:
        row = [m["id"], m["version"] or "", m["workshop_id"] or "", 1 if m["affects_gameplay"] else 0]
        if not m["workshop_id"] and m["name"] != m["id"]:
            row.append(m["name"])
        rows.append(row)
    compact = {"g": data["game_version"], "b": data["branch"], "m": rows}
    raw = json.dumps(compact, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return SHARE_PREFIX + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode().rstrip("=")


def find_share_code(text):
    m = RE_SHARE.search(text or "")
    return m.group(0) if m else None


def parse_share_code(text):
    """Accepts a code, or any text containing one (e.g. a pasted chat message). Returns the same
    structure as import_modlist. Raises ValueError for anything else."""
    m = RE_SHARE.search(text or "")
    if not m:
        raise ValueError("That doesn't contain a mod list code. Codes start with " + SHARE_PREFIX)
    body = m.group(1)
    try:
        raw = zlib.decompress(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        compact = json.loads(raw.decode("utf-8"))
        mods = []
        for row in compact["m"]:
            mod_id, version, wid, gameplay = row[:4]
            mods.append({"id": mod_id, "name": row[4] if len(row) > 4 else mod_id, "version": version,
                         "workshop_id": wid or None, "affects_gameplay": bool(gameplay)})
    except (ValueError, KeyError, TypeError, IndexError, zlib.error) as e:
        raise ValueError("That mod list code is damaged or incomplete. Ask for it again and copy "
                         "the whole line.") from e
    return {"format": EXPORT_FORMAT, "game_version": compact.get("g"), "branch": compact.get("b"),
            "mods": mods}


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
