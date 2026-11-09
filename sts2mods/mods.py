"""Scanning installed mods (Workshop + local mods folder) and static health checks."""

import json
import os
import subprocess
from dataclasses import dataclass, field

from . import osenv, vdf
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
                     "This works on Windows and macOS (they ignore capitalization) but not on Linux. "
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
    for other in mod.shadowed:
        issues.append(Issue(INFO, f"Also installed from {other.source_label} (v{other.version or '?'})",
                            f"The game loads the higher version (v{mod.version or '?'} from "
                            f"{mod.source_label}). Other copy: {other.dir}", key=f"shadow-{other.dir}"))
    return issues


# Encrypted home folders (ecryptfs) cap file names at 143 characters. Mods that save files with
# longer names into the game's data folder break, often without a clear error. The fix moves the
# whole data folder (saves included) to /srv, which is on the normal unencrypted disk and visible
# inside Steam's game runtime, and leaves a link in its place, so the game finds it as before.
RELOCATE_BASE = "/srv/sts2-mod-loader"
MIN_NAME_MAX = 200


def _name_max(path):
    if not hasattr(os, "statvfs"):      # Windows: NTFS allows 255 characters
        return 255
    try:
        return os.statvfs(path).f_namemax       # follows the link once the folder is moved
    except OSError:
        return 0


def short_name_limit(game):
    """The file-name length limit of the game's data folder if it's too short for mods, else None."""
    if not osenv.IS_LINUX or not game.user_data:
        return None
    n = _name_max(game.user_data)
    return n if 0 < n < MIN_NAME_MAX else None


def relocation_fix(game):
    if not short_name_limit(game):
        return None
    import getpass
    target = os.path.join(RELOCATE_BASE, getpass.getuser(), os.path.basename(game.user_data))
    return ("relocate-user-data", game.user_data, target)


def filesystem_issue(game):
    """A global Issue (with a fix) when the game's data folder can't hold long file names."""
    fix = relocation_fix(game)
    if not fix:
        return None
    return Issue(ERROR, "Your home folder is encrypted, so mods that save long file names break",
                 f"Encrypted home folders only allow file names up to {short_name_limit(game)} "
                 "characters. Mods that cache or save files with longer names fail, sometimes "
                 "silently (missing sounds, settings that don't stick, crashes). Fix moves the game's "
                 f"data folder, saves included, to {fix[2]} on your normal disk and leaves a link "
                 "in its place. You'll be asked for your password once to create that folder. "
                 "Files there aren't encrypted.",
                 fix=fix, key="short-file-names")


def _relocate_user_data(source, target):
    import shutil
    if os.path.islink(source):
        raise OSError(f"{source} is already a link")
    parent = os.path.dirname(target)
    if not os.path.isdir(parent):
        # /srv belongs to root: create our folder there once, owned by this user.
        subprocess.run(["pkexec", "install", "-d", "-m", "700", "-o", str(os.getuid()),
                        "-g", str(os.getgid()), parent], check=False)
        if not os.path.isdir(parent):
            raise OSError(f"Couldn't create {parent} (password prompt cancelled or failed).")
    if os.path.exists(target):
        raise OSError(f"{target} already exists. Move or delete it first.")
    tmp = target + ".partial"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(source, tmp, symlinks=False, ignore_dangling_symlinks=True)
    os.rename(tmp, target)
    old = source + ".moving"
    os.rename(source, old)
    try:
        os.symlink(target, source)
    except OSError:
        os.rename(old, source)        # put everything back as it was
        raise
    shutil.rmtree(old, ignore_errors=True)


def apply_fix(fix):
    """Perform an Issue.fix. Raises OSError on failure."""
    kind = fix[0]
    if kind == "symlink":
        _, target, link = fix
        if not os.path.lexists(link):
            os.symlink(target, link)
    elif kind == "relocate-user-data":
        _relocate_user_data(fix[1], fix[2])
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
