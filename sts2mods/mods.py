"""Scanning installed mods (Workshop + local mods folder) and static health checks."""

import json
import os
from dataclasses import dataclass, field

from . import vdf
from .game import format_version, parse_version

WORKSHOP = "steam_workshop"
LOCAL = "mods_directory"

ERROR, WARN, INFO = "error", "warn", "info"
SEVERITY_RANK = {ERROR: 0, WARN: 1, INFO: 2, None: 3}


@dataclass
class Issue:
    severity: str            # error / warn / info
    title: str               # one line, plain language
    detail: str = ""         # longer explanation or raw log excerpt
    count: int = 1
    source: str = "static"   # "static" or a log file name
    when: float = 0.0        # log mtime for log-derived issues
    stale: bool = False      # probably no longer true (see stale_note)
    stale_note: str = ""
    fix: tuple = None        # ("symlink", target_name, link_path)
    key: str = ""            # dedupe key


@dataclass
class Mod:
    id: str
    name: str
    version: str = ""
    author: str = ""
    description: str = ""
    min_game_version: str = ""
    has_dll: bool = False
    has_pck: bool = False
    affects_gameplay: bool = True
    dependencies: list = field(default_factory=list)   # [(id, min_version or None)]
    source: str = WORKSHOP
    workshop_id: str = None
    dir: str = ""
    manifest_path: str = ""
    size: int = 0
    installed_time: int = 0          # workshop timeupdated of installed files
    latest_time: int = 0             # workshop latest_timeupdated known to Steam
    shadowed: list = field(default_factory=list)   # other copies the game will NOT load
    static_issues: list = field(default_factory=list)

    @property
    def update_pending(self):
        return bool(self.latest_time and self.installed_time and self.latest_time > self.installed_time)

    @property
    def source_label(self):
        return "Workshop" if self.source == WORKSHOP else "Local"

    def dll_paths(self):
        out = []
        for root, _dirs, files in os.walk(self.dir):
            for f in files:
                if f.lower().endswith(".dll"):
                    out.append(os.path.join(root, f))
        return out


def _looks_like_manifest(data):
    return isinstance(data, dict) and isinstance(data.get("id"), str) and \
        ("has_dll" in data or "has_pck" in data)


def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def _read_manifests(root):
    for dirpath, _dirs, files in os.walk(root):
        for f in sorted(files):
            if not f.lower().endswith(".json"):
                continue
            path = os.path.join(dirpath, f)
            try:
                with open(path, encoding="utf-8-sig") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue
            if _looks_like_manifest(data):
                yield path, data


def _make_mod(path, data, source, workshop_id):
    deps = []
    for d in data.get("dependencies") or []:
        if isinstance(d, dict) and d.get("id"):
            deps.append((d["id"], d.get("min_version")))
        elif isinstance(d, str):
            deps.append((d, None))
    return Mod(
        id=data["id"],
        name=(data.get("name") or data["id"]).strip(),
        version=str(data.get("version") or ""),
        author=str(data.get("author") or ""),
        description=str(data.get("description") or ""),
        min_game_version=str(data.get("min_game_version") or ""),
        has_dll=bool(data.get("has_dll")),
        has_pck=bool(data.get("has_pck")),
        affects_gameplay=data.get("affects_gameplay", True) is not False,
        dependencies=deps,
        source=source,
        workshop_id=workshop_id,
        dir=os.path.dirname(path),
        manifest_path=path,
    )


def _workshop_times(acf_path):
    if not acf_path or not os.path.exists(acf_path):
        return {}, {}
    try:
        data = vdf.load(acf_path).get("AppWorkshop", {})
    except OSError:
        return {}, {}
    installed = {k: int(v.get("timeupdated", 0) or 0)
                 for k, v in (data.get("WorkshopItemsInstalled") or {}).items() if isinstance(v, dict)}
    latest = {k: int(v.get("latest_timeupdated", 0) or 0)
              for k, v in (data.get("WorkshopItemDetails") or {}).items() if isinstance(v, dict)}
    return installed, latest


def scan(game):
    """Return {mod_id: Mod} for the copy of each mod the game would actually load."""
    found = []
    installed_t, latest_t = _workshop_times(game.workshop_acf)
    if game.workshop_dir and os.path.isdir(game.workshop_dir):
        for wid in sorted(os.listdir(game.workshop_dir)):
            item = os.path.join(game.workshop_dir, wid)
            if not os.path.isdir(item):
                continue
            for path, data in _read_manifests(item):
                mod = _make_mod(path, data, WORKSHOP, wid)
                mod.size = _dir_size(item)
                mod.installed_time = installed_t.get(wid, 0)
                mod.latest_time = latest_t.get(wid, 0)
                found.append(mod)
    if game.mods_dir and os.path.isdir(game.mods_dir):
        for path, data in _read_manifests(game.mods_dir):
            mod = _make_mod(path, data, LOCAL, None)
            mod.size = _dir_size(mod.dir)
            found.append(mod)

    # Same rule as the game: if an id exists twice, the higher version wins.
    mods = {}
    for mod in found:
        cur = mods.get(mod.id)
        if cur is None:
            mods[mod.id] = mod
            continue
        if (parse_version(mod.version) or ()) > (parse_version(cur.version) or ()):
            mod.shadowed = cur.shadowed + [cur]
            mods[mod.id] = mod
        else:
            cur.shadowed.append(mod)

    for mod in mods.values():
        mod.static_issues = static_checks(mod, mods, game)
    return mods


def _file_issue(mod, ext, label):
    expected = os.path.join(mod.dir, f"{mod.id}.{ext}")
    if os.path.exists(expected):
        return None
    try:
        siblings = os.listdir(mod.dir)
    except OSError:
        siblings = []
    wrong_case = [s for s in siblings if s.lower() == f"{mod.id}.{ext}".lower()]
    if wrong_case:
        return Issue(ERROR, f"{label} file name has the wrong capitalization",
                     f"The game looks for '{mod.id}.{ext}' but the mod ships '{wrong_case[0]}'. "
                     "This works on Windows (case-insensitive) but not on Linux. "
                     "Fix creates a correctly named link to the existing file.",
                     fix=("symlink", wrong_case[0], expected), key=f"case-{ext}")
    return Issue(ERROR, f"{label} file is missing, so the mod won't load",
                 f"The manifest says the mod has a {label.lower()} file, but '{mod.id}.{ext}' "
                 "isn't in its folder. The download may be incomplete or the mod is packaged wrong. "
                 "Try unsubscribing and resubscribing, or tell the mod author.",
                 key=f"missing-{ext}")


def static_checks(mod, mods, game):
    issues = []
    need = parse_version(mod.min_game_version)
    if need and game.version and need > game.version:
        issues.append(Issue(ERROR, f"Needs game {format_version(need)} or newer",
                            f"You have {game.version_text}. The mod declares it needs "
                            f"{mod.min_game_version}.", key="min-game"))
    if mod.has_dll:
        i = _file_issue(mod, "dll", "Code (DLL)")
        if i:
            issues.append(i)
    if mod.has_pck:
        i = _file_issue(mod, "pck", "Asset pack (PCK)")
        if i:
            issues.append(i)
    for dep_id, min_v in mod.dependencies:
        dep = mods.get(dep_id)
        if dep is None:
            issues.append(Issue(ERROR, f"Requires {dep_id}, which isn't installed",
                                "Subscribe to the dependency on the Workshop.", key=f"dep-missing-{dep_id}"))
        elif min_v and parse_version(min_v) and parse_version(dep.version) and \
                parse_version(dep.version) < parse_version(min_v):
            issues.append(Issue(WARN, f"Requires {dep.name} {min_v}+, you have {dep.version}",
                                key=f"dep-version-{dep_id}"))
    if mod.id == RITSULIB_ID:
        cache = ritsulib_cache_issue(game)
        if cache:
            issues.append(cache)
    for other in mod.shadowed:
        issues.append(Issue(INFO, f"Also installed from {other.source_label} (v{other.version or '?'})",
                            f"The game loads the higher version (v{mod.version or '?'} from "
                            f"{mod.source_label}). Other copy: {other.dir}", key=f"shadow-{other.dir}"))
    return issues


RITSULIB_ID = "STS2-RitsuLib"
CACHE_TARGET = "/var/tmp/sts2-ritsulib"   # ext4, and visible inside Steam's game sandbox
MIN_NAME_MAX = 200


def _name_max(path):
    try:
        return os.statvfs(path).f_namemax
    except OSError:
        return 0


def ritsulib_cache_issue(game):
    """RitsuLib caches audio under user://ritsulib with ~170-character file names. Encrypted
    home folders (ecryptfs) only allow 143, so every write fails. Fix: keep that folder on an
    unencrypted file system via a symlink."""
    if not game.user_data or _name_max(game.user_data) >= MIN_NAME_MAX:
        return None
    link = os.path.join(game.user_data, "ritsulib")
    if os.path.islink(link):
        target = os.path.realpath(link)
        if os.path.isdir(target) and _name_max(target) >= MIN_NAME_MAX:
            return None
        why = "The folder it points to is gone (the system cleans /var/tmp after a while of no use)."
    else:
        why = "Its cache folder is inside your encrypted home folder."
    return Issue(ERROR, "Can't save its audio cache, so modded sounds fail",
                 f"Your home folder is encrypted (ecryptfs), which limits file names to "
                 f"{_name_max(game.user_data)} characters. RitsuLib's audio cache uses longer names. {why} "
                 f"Fix moves the cache to {CACHE_TARGET} (cache only, safe to lose) and links it back.",
                 fix=("redirect-dir", link, CACHE_TARGET), key="ritsulib-cache")


def apply_fix(fix):
    """Perform an Issue.fix. Raises OSError on failure."""
    kind = fix[0]
    if kind == "symlink":
        _, target, link = fix
        if not os.path.lexists(link):
            os.symlink(target, link)
    elif kind == "redirect-dir":
        import shutil
        _, link, target = fix
        os.makedirs(target, exist_ok=True)
        if os.path.islink(link):
            os.remove(link)
        elif os.path.isdir(link):
            shutil.rmtree(link)          # a cache folder: contents are regenerated
        os.symlink(target, link)
    else:
        raise OSError(f"unknown fix {kind}")


def dependents(mods, mod_id):
    return [m for m in mods.values() if any(d == mod_id for d, _ in m.dependencies)]


def dependency_closure(mods, mod_id, seen=None):
    """All installed mods that mod_id needs, recursively (excluding itself)."""
    seen = set() if seen is None else seen
    mod = mods.get(mod_id)
    if not mod:
        return seen
    for dep_id, _ in mod.dependencies:
        if dep_id in mods and dep_id not in seen:
            seen.add(dep_id)
            dependency_closure(mods, dep_id, seen)
    return seen


def dependent_closure(mods, mod_id, seen=None):
    """All installed mods that need mod_id, recursively."""
    seen = set() if seen is None else seen
    for m in dependents(mods, mod_id):
        if m.id not in seen:
            seen.add(m.id)
            dependent_closure(mods, m.id, seen)
    return seen


def is_library(mods, mod_id):
    return len(dependents(mods, mod_id)) >= 3
