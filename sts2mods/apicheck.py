"""Static compatibility check: does every game/library function a mod calls still exist?

Mods are compiled against a specific version of the game's sts2.dll (and of library mods
like BaseLib). When the game renames or changes a method, the mod still loads, but the
first time that code runs it throws MissingMethodException. This module finds those
references ahead of time by comparing the mod's metadata with the installed assemblies.
"""

import hashlib
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field

from . import metadata as md, osenv
from .game import parse_version

CACHE_PATH = os.path.join(osenv.cache_dir(), "apicheck.json")
CACHE_VERSION = 3
VERSION_DIR = re.compile(r"^[a-z]?v?(\d+\.\d+(?:\.\d+)?)$", re.I)
VARIANT_SUFFIX = re.compile(r"\.(stable|beta\d*|public|legacy|v?\d+(?:[._]\d+)*)$", re.I)


@dataclass
class Provider:
    """Everything an assembly defines, indexed for lookups."""
    name: str
    types: dict = field(default_factory=dict)   # full name -> {"m": {name: [sig]}, "f": {name}, "base": name, "ext": bool}


def build_provider(path):
    asm = md.Assembly(path)
    prov = Provider(name=asm.name or os.path.splitext(os.path.basename(path))[0])
    ntypes = asm.count(md.TYPEDEF)
    nmethods, nfields = asm.count(md.METHODDEF), asm.count(md.FIELD)
    rows = [asm.row(md.TYPEDEF, i) for i in range(1, ntypes + 1)]
    for i, r in enumerate(rows, start=1):
        m_end = rows[i]["MethodList"] if i < ntypes else nmethods + 1
        f_end = rows[i]["FieldList"] if i < ntypes else nfields + 1
        methods = defaultdict(list)
        for m in range(r["MethodList"], min(m_end, nmethods + 1)):
            mr = asm.row(md.METHODDEF, m)
            methods[mr["Name"]].append(asm.member_sig(asm.blob(mr["Signature"])))
        fields = set()
        for f in range(r["FieldList"], min(f_end, nfields + 1)):
            fields.add(asm.row(md.FIELD, f)["Name"])
        base, ext = None, False
        bt, bi = r["Extends"]
        if bi:
            if bt == md.TYPEDEF:
                base = asm.typedef_name(bi)
            elif bt == md.TYPEREF:
                base, ext = asm.typeref_name(bi), True
            elif bt == md.TYPESPEC:
                inner = _typespec_generic(asm, bi)
                if inner and inner[0] == md.TYPEDEF:
                    base = asm.typedef_name(inner[1])
                else:
                    ext = True
        prov.types[asm.typedef_name(i)] = {"m": dict(methods), "f": fields, "base": base, "ext": ext}
    return prov


def _typespec_generic(asm, index):
    """For a TypeSpec that is a generic instantiation, return the generic type's coded index."""
    blob = asm.blob(asm.row(md.TYPESPEC, index)["Signature"])
    if len(blob) > 2 and blob[0] == 0x15:
        coded, _ = asm._tdor(blob, 2)
        return coded
    return None


@dataclass
class Problem:
    kind: str        # "missing-type", "missing-member", "changed-member"
    assembly: str    # which assembly the mod expected it in (sts2, BaseLib, ...)
    type: str
    member: str = ""
    sig: str = ""

    def describe(self):
        short_type = md.short_type(self.type)
        if self.kind == "missing-type":
            return f"{short_type}: whole type no longer exists"
        what = md.pretty_sig(self.member, self.sig)
        note = "parameters/return type changed" if self.kind == "changed-member" else "no longer exists"
        return f"{short_type}.{what}: {note}"


def check_assembly(path, providers):
    """Return the list of Problems for references from `path` into any provider assembly."""
    asm = md.Assembly(path)
    problems, seen = [], set()

    def assembly_of(coded):
        t, i = coded
        if t == md.TYPEREF:
            return asm.typeref_assembly(i), asm.typeref_name(i)
        if t == md.TYPESPEC:
            inner = _typespec_generic(asm, i)
            if inner and inner[0] == md.TYPEREF:
                return asm.typeref_assembly(inner[1]), asm.typeref_name(inner[1])
        return None, None

    for i in range(1, asm.count(md.TYPEREF) + 1):
        a = asm.typeref_assembly(i)
        if a in providers:
            name = asm.typeref_name(i)
            if name not in providers[a].types and ("missing-type", name) not in seen:
                seen.add(("missing-type", name))
                problems.append(Problem("missing-type", a, name))

    for i in range(1, asm.count(md.MEMBERREF) + 1):
        r = asm.row(md.MEMBERREF, i)
        parent_t, parent_i = r["Class"]
        if parent_t not in (md.TYPEREF, md.TYPESPEC):
            continue
        a, type_name = assembly_of((parent_t, parent_i))
        prov = providers.get(a)
        if not prov or type_name not in prov.types:
            continue    # missing types are already reported above
        name = r["Name"]
        sig = asm.member_sig(asm.blob(r["Signature"]))
        verdict = _lookup(prov, type_name, name, sig)
        if verdict in ("missing", "changed"):
            key = (type_name, name, sig)
            if key not in seen:
                seen.add(key)
                problems.append(Problem("missing-member" if verdict == "missing" else "changed-member",
                                        a, type_name, name, sig))
    return problems


def _lookup(prov, type_name, name, sig):
    """'ok', 'missing', 'changed', or 'unknown' (member could live in an external base type)."""
    is_field = sig.startswith("field:")
    name_seen = False
    t, depth = type_name, 0
    while t and depth < 30:
        info = prov.types.get(t)
        if info is None:
            return "unknown"
        if is_field:
            if name in info["f"]:
                return "ok"
        else:
            sigs = info["m"].get(name)
            if sigs:
                name_seen = True
                if sig in sigs or "?" in sigs or sig == "?":
                    return "ok"
        if info["ext"]:
            return "changed" if name_seen else "unknown"
        t, depth = info["base"], depth + 1
    return "changed" if name_seen else "missing"


# ---- per-mod orchestration --------------------------------------------------------------

def _variant_groups(mod, game_version):
    """Primary DLL plus groups of alternative DLLs the mod chooses between at runtime."""
    primary = os.path.join(mod.dir, f"{mod.id}.dll")
    groups = defaultdict(list)
    for path in mod.dll_paths():
        if os.path.realpath(path) == os.path.realpath(primary):
            continue
        rel_dir = os.path.relpath(os.path.dirname(path), mod.dir)
        vdir = next((VERSION_DIR.match(p) for p in reversed(rel_dir.split(os.sep)) if VERSION_DIR.match(p)), None)
        if vdir and game_version and parse_version(vdir.group(1)) > game_version:
            continue    # built for a newer game than this one; a loader won't pick it
        stem = os.path.splitext(os.path.basename(path))[0]
        role = VARIANT_SUFFIX.sub("", stem).lower()
        groups[role].append(path)
    return (primary if os.path.exists(primary) else None), groups


def _fingerprint(paths):
    h = hashlib.sha1()
    for p in sorted(paths):
        try:
            st = os.stat(p)
            h.update(f"{p}:{st.st_size}:{st.st_mtime_ns};".encode())
        except OSError:
            h.update(f"{p}:missing;".encode())
    return h.hexdigest()


@dataclass
class ModApiResult:
    problems: list           # [Problem] (best case across variants)
    checked: list            # dll paths that were checked
    error: str = ""


def _find_game_dll(game):
    """The game's own sts2.dll: data_sts2_<platform>_<arch>/sts2.dll on Linux and Windows,
    somewhere inside the .app bundle on macOS. The mods folder is skipped."""
    if not game.install_dir or not os.path.isdir(game.install_dir):
        return None
    for name in ("data_sts2_linuxbsd_x86_64", "data_sts2_windows_x86_64"):
        p = os.path.join(game.install_dir, name, "sts2.dll")
        if os.path.exists(p):
            return p
    mods_dir = os.path.normcase(os.path.realpath(game.mods_dir)) if game.mods_dir else None
    for dp, dirs, fs in os.walk(game.install_dir):
        dirs[:] = [d for d in dirs if os.path.normcase(os.path.realpath(os.path.join(dp, d))) != mods_dir]
        if "sts2.dll" in fs:
            return os.path.join(dp, "sts2.dll")
    return None


def check_mods(mods, game):
    """Return {mod_id: ModApiResult}. Results are cached by file fingerprints."""
    sts2_path = _find_game_dll(game)
    if not sts2_path:
        return {}

    provider_paths = {"sts2": sts2_path}
    for mod in mods.values():
        primary = os.path.join(mod.dir, f"{mod.id}.dll")
        if os.path.exists(primary):
            provider_paths.setdefault(f"mod:{mod.id}", primary)

    cache = _load_cache()
    providers = None
    results = {}
    for mod_id, mod in mods.items():
        primary, groups = _variant_groups(mod, game.version)
        all_paths = ([primary] if primary else []) + [p for g in groups.values() for p in g]
        if not all_paths:
            continue
        fp = _fingerprint(all_paths + sorted(provider_paths.values()))
        hit = cache.get(mod_id)
        if hit and hit.get("fp") == fp:
            results[mod_id] = ModApiResult([Problem(**p) for p in hit["problems"]], hit["checked"], hit.get("error", ""))
            continue
        if providers is None:
            providers = _load_providers(provider_paths)
        result = _check_mod(mod_id, primary, groups, providers)
        results[mod_id] = result
        cache[mod_id] = {"fp": fp, "problems": [p.__dict__ for p in result.problems],
                         "checked": result.checked, "error": result.error}
    _save_cache(cache)
    return results


def _load_providers(provider_paths):
    providers = {}
    for key, path in provider_paths.items():
        try:
            prov = build_provider(path)
        except (md.BadImage, OSError, ValueError):
            continue
        if key == "sts2" or prov.name not in providers:
            providers[prov.name] = prov
    return providers


def _check_mod(mod_id, primary, groups, providers):
    def run(path):
        own_name = None
        try:
            own_name = md.Assembly(path).name
            usable = {k: v for k, v in providers.items() if k != own_name}
            return check_assembly(path, usable)
        except (md.BadImage, OSError, ValueError) as e:
            raise RuntimeError(str(e))

    problems, checked, error = [], [], ""
    try:
        if primary:
            problems += run(primary)
            checked.append(primary)
        for role, paths in groups.items():
            best = None
            for p in paths:
                try:
                    probs = run(p)
                except RuntimeError:
                    continue
                if best is None or len(probs) < len(best[1]):
                    best = (p, probs)
            if best:
                checked.append(best[0])
                problems += best[1]
    except RuntimeError as e:
        error = str(e)
    return ModApiResult(problems, checked, error)


def _load_cache():
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") == CACHE_VERSION:
            return data.get("mods", {})
    except (OSError, ValueError):
        pass
    return {}


def _save_cache(mods_cache):
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": CACHE_VERSION, "mods": mods_cache}, f)
        os.replace(tmp, CACHE_PATH)
    except OSError:
        pass
