"""Named mod setups ("profiles") and mod-list export/compare for co-op."""

import base64
import json
import os
import re
import time
import zlib

from . import osenv

PROFILE_DIR = os.path.join(osenv.config_dir(), "profiles")


def _safe(name):
    return re.sub(r"[^\w\- ]+", "_", name).strip() or "profile"


def _sort_key(name):
    """Alphabetical, ignoring case, with numbers in number order: "2" before "10"."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", name)]


def list_profiles():
    """Saved profile names, alphabetically."""
    if not os.path.isdir(PROFILE_DIR):
        return []
    out = []
    for f in os.listdir(PROFILE_DIR):
        if f.endswith(".json"):
            try:
                with open(os.path.join(PROFILE_DIR, f), encoding="utf-8") as fh:
                    out.append(json.load(fh).get("name") or f[:-5])
            except (OSError, ValueError):
                continue
    return sorted(out, key=_sort_key)


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


def profile_setup(data, mods, current_order):
    """(states, order) that loading `data` produces for the installed `mods`. Installed mods
    the profile doesn't list come out disabled, and keep their place after the profile's order."""
    wanted = {m["id"]: m.get("enabled", True) for m in data.get("mods", []) if isinstance(m, dict)}
    states = {mid: bool(wanted.get(mid, False)) for mid in mods}
    order = [i for i in data.get("order", []) if i in mods]
    order += [i for i in current_order if i not in order]
    return states, order


def matches(data, states, order, mods):
    """True if the current setup is exactly what loading this profile would give."""
    want_states, want_order = profile_setup(data, mods, order)
    return want_states == {mid: bool(states.get(mid, True)) for mid in mods} and want_order == list(order)


def _active_path():
    return os.path.join(PROFILE_DIR, ".active")


def get_active():
    """Name of the profile last loaded or saved, or None."""
    try:
        with open(_active_path(), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def set_active(name):
    try:
        if name:
            os.makedirs(PROFILE_DIR, exist_ok=True)
            with open(_active_path(), "w", encoding="utf-8") as f:
                f.write(name)
        elif os.path.exists(_active_path()):
            os.remove(_active_path())
    except OSError:
        pass


def load_profile(name):
    with open(_path(name), encoding="utf-8") as f:
        return json.load(f)


def delete_profile(name):
    try:
        os.remove(_path(name))
    except OSError:
        pass


# ---- share codes: the mod list as one short line of text to paste in a chat ----------------
#
# Packed bytes in URL-safe base64, after the "STS2:" prefix:
#   format (1 byte), game version, branch            strings are a length then UTF-8
#   number of Workshop mods, then for each (sorted by Workshop ID):
#     ID difference from the previous one * 2 + gameplay flag   (varint)
#     version fingerprint (2 bytes: CRC-32 of the version text, low 16 bits)
#   number of local mods, then for each: id, version, gameplay flag
# Workshop mods carry no id or name: the receiver matches the Workshop ID against its own mods
# and looks up titles on the Workshop. That keeps a mod to about 6 bytes (8 characters).

SHARE_PREFIX = "STS2:"
SHARE_FORMAT = 1
RE_SHARE = re.compile(re.escape(SHARE_PREFIX) + r"([A-Za-z0-9_-]{4,})")


def version_fingerprint(version):
    return zlib.crc32((version or "").strip().encode("utf-8")) & 0xFFFF


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _string(text):
    raw = (text or "").encode("utf-8")
    return _varint(len(raw)) + raw


class _Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def byte(self):
        self.pos += 1
        return self.data[self.pos - 1]

    def varint(self):
        n = shift = 0
        while True:
            b = self.byte()
            n |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                return n

    def string(self):
        n = self.varint()
        self.pos += n
        if self.pos > len(self.data):
            raise IndexError("truncated")
        return self.data[self.pos - n:self.pos].decode("utf-8")


def share_code(states, mods, game):
    enabled = [m for m in mods.values() if states.get(m.id, True)]
    workshop = sorted((m for m in enabled if m.workshop_id and m.workshop_id.isdigit()),
                      key=lambda m: int(m.workshop_id))
    local = [m for m in enabled if m not in workshop]
    out = bytearray([SHARE_FORMAT]) + _string(game.version_text) + _string(game.branch)
    out += _varint(len(workshop))
    prev = 0
    for m in workshop:
        wid = int(m.workshop_id)
        out += _varint((wid - prev) * 2 + int(m.affects_gameplay))
        out += version_fingerprint(m.version).to_bytes(2, "big")
        prev = wid
    out += _varint(len(local))
    for m in local:
        out += _string(m.id) + _string(m.version) + bytes([int(m.affects_gameplay)])
    return SHARE_PREFIX + base64.urlsafe_b64encode(bytes(out)).decode().rstrip("=")


def find_share_code(text):
    m = RE_SHARE.search(text or "")
    return m.group(0) if m else None


def parse_share_code(text):
    """Accepts a code, or any text containing one (e.g. a pasted chat message). Returns
    {"game_version", "branch", "mods": [...]}; Workshop entries have "id": None and a
    "version_fingerprint" instead of a version. Raises ValueError."""
    code = find_share_code(text)
    if not code:
        raise ValueError("That doesn't contain a mod list code. Codes start with " + SHARE_PREFIX)
    body = code[len(SHARE_PREFIX):]
    try:
        r = _Reader(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if r.byte() != SHARE_FORMAT:
            raise ValueError("This code was made by a newer version of STS2 Mod Loader. Update to read it.")
        data = {"game_version": r.string(), "branch": r.string(), "mods": []}
        wid = 0
        for _ in range(r.varint()):
            n = r.varint()
            wid += n >> 1
            fp = int.from_bytes(bytes([r.byte(), r.byte()]), "big")
            data["mods"].append({"id": None, "workshop_id": str(wid), "name": f"Workshop item {wid}",
                                 "version_fingerprint": fp, "affects_gameplay": bool(n & 1)})
        for _ in range(r.varint()):
            mod_id, version = r.string(), r.string()
            data["mods"].append({"id": mod_id, "name": mod_id, "version": version, "workshop_id": None,
                                 "affects_gameplay": bool(r.byte())})
    except ValueError as e:
        if "newer version" in str(e):
            raise
        raise ValueError("That mod list code is damaged or incomplete. Ask for it again and copy "
                         "the whole line.") from e
    except (IndexError, UnicodeDecodeError) as e:
        raise ValueError("That mod list code is damaged or incomplete. Ask for it again and copy "
                         "the whole line.") from e
    return data


def compare(theirs, states, mods, game):
    """Differences that matter for joining each other's co-op games."""
    by_wid = {m.workshop_id: mid for mid, m in mods.items() if m.workshop_id}
    their_mods = {}
    for t in theirs["mods"]:
        if not isinstance(t, dict):
            continue
        # Share codes identify Workshop mods by Workshop ID only; match them to installed mods.
        mid = t.get("id") or by_wid.get(t.get("workshop_id")) or f"workshop:{t.get('workshop_id')}"
        their_mods[mid] = dict(t, id=mid) if mid in mods else t
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
        elif "version_fingerprint" in t:
            if version_fingerprint(mods[mid].version) != t["version_fingerprint"]:
                diff["version"].append((mods[mid], t))
        elif t.get("version") and mods[mid].version != t["version"]:
            diff["version"].append((mods[mid], t))
    for mid, m in mine.items():
        if mid not in their_mods and m.affects_gameplay:
            diff["extra"].append(m)
    return diff


def diff_is_clean(diff):
    return not any(diff[k] for k in ("branch", "game_version", "missing", "disabled", "extra", "version"))
