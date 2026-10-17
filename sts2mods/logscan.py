"""Reading the game's godot logs and turning errors into per-mod health issues.

The game keeps godot.log (latest session) plus a few rotated godot<timestamp>.log files.
For each mod we report issues from the newest session in which the game actually tried
to load it, so disabling a mod doesn't make its old problems disappear.
"""

import dataclasses
import glob
import json
import os
import re
import time
from dataclasses import dataclass, field

from .mods import ERROR, INFO, SEVERITY_RANK, WARN, Issue

FRAMEWORK_PREFIXES = ("MegaCrit", "System", "Godot", "HarmonyLib", "MonoMod", "Microsoft",
                      "Steamworks", "Sentry", "Internal", "DMD", "Mono", "Interop")

RE_BRANCH = re.compile(r"\[ERROR\] Tried to load mod with id (\S+), but the current Steam branch "
                       r"(\S+) does not lie.*?Max supported: (\S+)")
RE_MISSING_FILE = re.compile(r"Mod manifest for mod (\S+) declares that it should load "
                             r"(an assembly|a PCK), but no .*? at path (.+?) was found")
RE_NOTHING_LOADED = re.compile(r"Neither a DLL nor a PCK was loaded for mod (\S+)")
RE_LOAD_DLL = re.compile(r"\[INFO\] Loading assembly DLL (.+\.dll)\s*$")
RE_CALL_INIT = re.compile(r"Calling initializer method of type ([\w.`+<>]+) for ([^,\s]+)")
RE_INIT_FAIL = re.compile(r"\[ERROR\] Exception thrown when calling mod initializer of type ([\w.`+<>]+)")
RE_SKIP = re.compile(r"\[INFO\] Skipping loading mod (\S+), it is set to disabled")
RE_ORDER = re.compile(r"^\[INFO\]\s+\d+: .*\(([^()\s]+)\)\s*$")
RE_MODLIST = re.compile(r"^\s+\* .*\[([^\]]+)\] \(([^)]*)\)")
RE_TAG_ERROR = re.compile(r"^\[ERROR\] \[([^\]]+)\]")
RE_FOR_MOD = re.compile(r"for mod '([^']+)'")
RE_RES = re.compile(r"res://([^/'\s]+)/")
RE_EXC = re.compile(r"((?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Exception|Error)): (.*)")
RE_FRAME = re.compile(r"((?:<unknown>\.)?[A-Za-z_][\w`<>|]*(?:\.[\w`<>|]+)+)\(")
RE_MODMISMATCH = re.compile(r"Failed to connect to the host\. Reason: (\w+)")
RE_DIVERGENCE = re.compile(r"State divergence message received")
EXIT_MARKERS = ("still in use at exit", "ObjectDB instances leaked at exit")

API_MISMATCH = ("MissingMethodException", "MissingFieldException", "TypeLoadException",
                "MissingMemberException", "EntryPointNotFoundException")


@dataclass
class LogSession:
    path: str
    mtime: float
    attempted: set = field(default_factory=set)     # mod ids the game tried to load
    versions: dict = field(default_factory=dict)    # id -> version string from the Mod List
    issues: dict = field(default_factory=dict)      # id -> {key: Issue}
    global_issues: list = field(default_factory=list)
    clean_exit: bool = False
    crash_mod: str = None        # mod whose error was the last thing logged before an unclean end
    crash_key: str = None        # issue key of that last error

    @property
    def label(self):
        return time.strftime("%b %d %H:%M", time.localtime(self.mtime))


class Attributor:
    """Maps stack frames / tags / resource paths to mod ids."""

    def __init__(self, mods):
        self.mods = mods
        self.by_dll_dir = {}
        self.dll_blobs = None
        self.ns_cache = {}
        self.lower_ids = {m.lower(): m for m in mods}
        self.lower_names = {mod.name.lower(): mid for mid, mod in mods.items()}
        self.dll_stems = {}
        for mid, mod in mods.items():
            for p in mod.dll_paths():
                stem = os.path.splitext(os.path.basename(p))[0].lower()
                self.dll_stems.setdefault(stem, mid)
                self.dll_stems.setdefault(stem.split(".")[0], mid)

    def by_path(self, path):
        path = os.path.realpath(path)
        best = None
        for mid, mod in self.mods.items():
            d = os.path.realpath(mod.dir)
            if path == d or path.startswith(d + os.sep):
                if best is None or len(d) > len(os.path.realpath(self.mods[best].dir)):
                    best = mid
        return best

    def by_tag(self, tag):
        t = tag.lower()
        if t in self.lower_ids:
            return self.lower_ids[t]
        if t in self.lower_names:
            return self.lower_names[t]
        if t in self.dll_stems:
            return self.dll_stems[t]
        for lid, mid in self.lower_ids.items():
            if len(lid) >= 4 and t.endswith(lid):
                return mid
        return None

    def by_res(self, folder):
        f = folder.lower()
        return self.lower_ids.get(f) or self.dll_stems.get(f)

    def _blobs(self):
        if self.dll_blobs is None:
            self.dll_blobs = []
            for mid, mod in self.mods.items():
                for p in mod.dll_paths():
                    try:
                        with open(p, "rb") as f:
                            self.dll_blobs.append((mid, f.read()))
                    except OSError:
                        pass
        return self.dll_blobs

    def by_frame(self, frame):
        """frame like 'Miko.Scripts.Cards.Sever.OnPlay' -> mod id, or None for game/framework code."""
        if frame.startswith("<unknown>."):
            frame = frame[len("<unknown>."):]
        parts = [p for p in frame.split(".") if p]
        if not parts or parts[0].startswith(FRAMEWORK_PREFIXES) or parts[0].startswith("<"):
            return None
        if frame in self.ns_cache:
            return self.ns_cache[frame]
        result = self.dll_stems.get(parts[0].lower())
        if result is None:
            # Namespaces are stored NUL-terminated in the DLL's metadata string heap.
            for n in range(len(parts) - 2, 0, -1):
                needle = b"\x00" + ".".join(parts[:n]).encode() + b"\x00"
                hits = {mid for mid, blob in self._blobs() if needle in blob}
                if len(hits) == 1:
                    result = hits.pop()
                    break
                if hits:
                    named = [h for h in hits if h.lower() in frame.lower()]
                    result = named[0] if named else sorted(hits)[0]
                    break
        self.ns_cache[frame] = result
        return result


SAMPLE_SEP = "\n─────\n"


def _add(session, mod_id, issue):
    bucket = session.issues.setdefault(mod_id, {})
    key = issue.key or issue.title
    cur = bucket.get(key)
    if cur:
        cur.count += 1
        if issue.detail and issue.detail not in cur.detail and cur.detail.count(SAMPLE_SEP) < 4:
            cur.detail += SAMPLE_SEP + issue.detail
    else:
        issue.source, issue.when = os.path.basename(session.path), session.mtime
        bucket[key] = issue


def _short(text, n=300):
    text = text.strip()
    return text if len(text) <= n else text[:n] + "…"


def _exception_block(lines, i):
    """Collect an error header plus its indented continuation (inner exceptions, frames)."""
    block = [lines[i]]
    j = i + 1
    while j < len(lines) and j - i < 80:
        line = lines[j]
        if line.startswith((" ", "\t", "--->", "---")) or line.startswith("   at "):
            block.append(line)
            j += 1
        else:
            break
    return block, j


def _blame(block, attributor, mods):
    """Pick the mod whose code is closest to the throw; libraries only if nobody else."""
    from .mods import is_library
    candidates = []
    for line in block[1:] + block[:1]:
        for frame in RE_FRAME.findall(line):
            mid = attributor.by_frame(frame)
            if mid and mid not in candidates:
                candidates.append(mid)
    for mid in candidates:
        if not is_library(mods, mid):
            return mid
    return candidates[0] if candidates else None


def _exception_summary(block):
    """Innermost exception type + message, e.g. ('MissingMethodException', 'Method not found: ...')."""
    best = None
    for line in block:
        m = RE_EXC.search(line)
        if m and "TargetInvocationException" not in m.group(1) and "AggregateException" not in m.group(1):
            best = (m.group(1).split(".")[-1], m.group(2))
    if best is None:
        m = RE_EXC.search(block[0])
        if m:
            best = (m.group(1).split(".")[-1], m.group(2))
    return best


def parse_log(path, mods, attributor):
    session = LogSession(path=path, mtime=os.path.getmtime(path))
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return session

    init_type_to_mod = {}
    last_dll_mod = None
    last_error = (None, -1, None)   # (mod_id, line index where its block ended, issue key)
    session.clean_exit = any(m in l for l in lines[-40:] for m in EXIT_MARKERS)
    i = 0
    while i < len(lines):
        line = lines[i]

        m = RE_LOAD_DLL.search(line)
        if m:
            last_dll_mod = attributor.by_path(m.group(1))
            if last_dll_mod:
                session.attempted.add(last_dll_mod)
            i += 1
            continue
        m = RE_CALL_INIT.search(line)
        if m:
            if last_dll_mod:
                init_type_to_mod[m.group(1)] = last_dll_mod
            i += 1
            continue
        m = RE_ORDER.match(line)
        if m and m.group(1) in mods:
            session.attempted.add(m.group(1))
            i += 1
            continue
        m = RE_MODLIST.match(line)
        if m and m.group(1) in mods:
            session.attempted.add(m.group(1))
            session.versions[m.group(1)] = m.group(2)
            i += 1
            continue
        m = RE_SKIP.search(line)
        if m:
            session.attempted.discard(m.group(1))
            i += 1
            continue

        m = RE_BRANCH.search(line)
        if m:
            _add(session, m.group(1), Issue(
                ERROR, f"Not made for the {m.group(2)} branch (newest supported: {m.group(3)})",
                "The mod author marked this mod as incompatible with your game branch on the "
                "Workshop. Switch branches in Steam (game Properties → Betas) or wait for an update.",
                key="branch"))
            i += 1
            continue
        m = RE_MISSING_FILE.search(line)
        if m:
            ext = "dll" if "assembly" in m.group(2) else "pck"
            _add(session, m.group(1), Issue(
                ERROR, f"Expected {ext.upper()} file not found", _short(m.group(3)), key=f"missing-{ext}"))
            i += 1
            continue
        m = RE_NOTHING_LOADED.search(line)
        if m:
            _add(session, m.group(1), Issue(
                ERROR, "Didn't load at all (no code or assets found)",
                "The game found the manifest but none of the mod's files. Usually a packaging "
                "problem on the mod author's side or an incomplete download.", key="nothing-loaded"))
            i += 1
            continue
        m = RE_MODMISMATCH.search(line)
        if m:
            session.global_issues.append(Issue(
                WARN, f"Last co-op join failed: {m.group(1)}",
                "You and the host must have the same gameplay mods enabled, at the same versions. "
                "Use Compare with friend to see the differences.", key="mismatch"))
            i += 1
            continue
        if RE_DIVERGENCE.search(line):
            session.global_issues.append(Issue(
                WARN, "Co-op desync (state divergence) in a recent session",
                "Players' game states drifted apart. Usually caused by mismatched mods or a mod "
                "that isn't multiplayer-safe.", key="divergence"))
            i += 1
            continue

        is_error = line.startswith("[ERROR]") or line.startswith("ERROR:")
        if not is_error:
            i += 1
            continue

        block, nxt = _exception_block(lines, i)
        text = "\n".join(block)

        m = RE_INIT_FAIL.search(line)
        if m:
            mod_id = init_type_to_mod.get(m.group(1)) or attributor.by_frame(m.group(1) + ".x")
            summary = _exception_summary(block)
            if mod_id:
                last_error = (mod_id, nxt, "init-crash")
                api = summary and summary[0] in API_MISMATCH
                _add(session, mod_id, Issue(
                    ERROR, "Crashed while starting up" + (" (built for a different game version)" if api else ""),
                    _short(f"{summary[0]}: {summary[1]}" if summary else text, 500), key="init-crash"))
            i = nxt
            continue

        m = RE_RES.search(line) if ("No loader found" in line or "Failed to load resource" in line
                                   or "Cannot open file" in line or "Error loading resource" in line) else None
        if m:
            mod_id = attributor.by_res(m.group(1))
            if mod_id:
                last_error = (mod_id, nxt, "assets")
                res = re.search(r"res://[^'\s)]+", line)
                _add(session, mod_id, Issue(
                    WARN, "Some images or scenes failed to load (asset pack missing or outdated)",
                    res.group(0) if res else _short(line, 200), key="assets"))
            i = nxt
            continue

        summary = _exception_summary(block)
        tag = RE_TAG_ERROR.match(line)
        mod_id = None
        fm = RE_FOR_MOD.search(line)
        if fm and fm.group(1) in mods:
            mod_id = fm.group(1)
        if mod_id is None and len(block) > 1:
            mod_id = _blame(block, attributor, mods)
        if mod_id is None and tag:
            mod_id = attributor.by_tag(tag.group(1))
        if mod_id is None:
            i = nxt
            continue
        last_error = (mod_id, nxt, None)

        if summary and summary[0] == "PathTooLongException":
            last_error = (mod_id, nxt, "path-too-long")
            _add(session, mod_id, Issue(
                ERROR, "File names too long for your file system (writes failing)",
                _short(f"{summary[0]}: {summary[1]}", 300), key="path-too-long"))
        elif "ContentPack" in line and "Failed" in line:
            _add(session, mod_id, Issue(
                ERROR, "Some of its content (cards, relics, potions…) failed to register",
                _short(re.sub(r"^\[ERROR\]\s*(\[[^\]]+\]\s*)*", "", line), 500), key="contentpack"))
        elif summary and summary[0] in API_MISMATCH:
            _add(session, mod_id, Issue(
                ERROR, "Calls game code that no longer exists (built for a different game version)",
                _short(f"{summary[0]}: {summary[1]}", 500) +
                "\nThe feature that hit this is broken until the mod is updated for your game version.",
                key="api"))
        elif summary:
            _add(session, mod_id, Issue(
                WARN, f"Error while playing: {summary[0]}",
                _short(f"{summary[0]}: {summary[1]}", 500), key="exc:" + summary[0]))
        else:
            msg = re.sub(r"^\[ERROR\]\s*(\[[^\]]+\]\s*)*", "", line).strip() or line.strip()
            _add(session, mod_id, Issue(
                WARN, "Logged errors", _short(msg, 400), key="tag"))
        i = nxt
    if not session.clean_exit and last_error[0] and last_error[1] >= len(lines) - 15:
        session.crash_mod, session.crash_key = last_error[0], last_error[2]
    return session


def log_files(logs_dir):
    if not logs_dir or not os.path.isdir(logs_dir):
        return []
    files = glob.glob(os.path.join(logs_dir, "godot*.log"))
    return sorted(files, key=os.path.getmtime, reverse=True)


HISTORY_PATH = os.path.expanduser("~/.local/share/sts2-mod-loader/history.json")

# Overall per-mod status, worst first.
S_ERROR, S_WARN, S_STALE, S_OK, S_UNTESTED = "error", "warn", "stale", "ok", "untested"
STATUS_RANK = {S_ERROR: 0, S_WARN: 1, S_STALE: 2, S_UNTESTED: 3, S_OK: 4}


@dataclass
class HealthReport:
    by_mod: dict                 # id -> [Issue]
    status: dict                 # id -> S_*
    tested: dict                 # id -> (label, timestamp) of the session the verdict comes from
    global_issues: list
    sessions: list               # LogSession, newest first


def _load_history():
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_history(history):
    try:
        os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
        tmp = HISTORY_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=1, ensure_ascii=False)
        os.replace(tmp, HISTORY_PATH)
    except OSError:
        pass


def _issue_from_dict(d):
    fields = {f.name for f in dataclasses.fields(Issue)}
    d = {k: v for k, v in d.items() if k in fields}
    if isinstance(d.get("fix"), list):
        d["fix"] = tuple(d["fix"])
    return Issue(**d)


def api_issues(result, game_version_text):
    """Turn an apicheck.ModApiResult into Issues (one per target assembly)."""
    if not result or not result.problems:
        return []
    by_asm = {}
    for p in result.problems:
        by_asm.setdefault(p.assembly, []).append(p)
    out = []
    for asm, probs in by_asm.items():
        lines = [p.describe() for p in probs[:20]]
        if len(probs) > 20:
            lines.append(f"…and {len(probs) - 20} more")
        n = len(probs)
        if asm == "sts2":
            title = (f"Built for a different game version: {n} call{'s' if n != 1 else ''} "
                     f"into the game won't work on {game_version_text}")
        else:
            title = f"Built for a different version of {asm}: {n} call{'s' if n != 1 else ''} won't work"
        out.append(Issue(ERROR, title,
                         "The mod loads, but whatever uses these (cards, relics, events…) will throw "
                         "an error when it happens in game:\n• " + "\n• ".join(lines),
                         source="code check", key=f"api-check-{asm}"))
    return out


def analyze(mods, logs_dir, live=False, use_history=True, api_results=None, game_version_text="?"):
    """live=True means the newest log belongs to a session that is still running."""
    attributor = Attributor(mods)
    sessions = [parse_log(p, mods, attributor) for p in log_files(logs_dir)]
    history = _load_history() if use_history else {}
    history_changed = False
    by_mod, status, tested = {}, {}, {}

    for mod_id, mod in mods.items():
        issues = list(mod.static_issues) + api_issues((api_results or {}).get(mod_id), game_version_text)
        mod_mtime = _mod_mtime(mod)
        session = next((s for s in sessions if mod_id in s.attempted), None)
        if session:
            logged = [dataclasses.replace(i) for i in session.issues.get(mod_id, {}).values()]
            crashed = session.crash_mod == mod_id and not (live and session is sessions[0])
            if crashed:
                logged.append(Issue(ERROR, "The game stopped right after an error from this mod (likely crash)",
                                    "The log ends in the middle of this mod's error, with no normal "
                                    "shutdown. If the game crashed then, this mod is the prime suspect.",
                                    key="crash", source=os.path.basename(session.path), when=session.mtime))
            ver_then = session.versions.get(mod_id)
            for iss in logged:
                if (ver_then and mod.version and ver_then != mod.version) or mod_mtime > session.mtime:
                    iss.stale, iss.stale_note = True, "the mod has been updated since, so this may be fixed"
            issues.extend(logged)
            tested[mod_id] = (session.label, session.mtime)
            prev = history.get(mod_id)
            if use_history and (not prev or prev.get("session_time", 0) <= session.mtime):
                history[mod_id] = {
                    "version": ver_then or mod.version,
                    "session_time": session.mtime,
                    "issues": [dataclasses.asdict(i) for i in logged],
                }
                history_changed = True
        elif mod_id in history:
            h = history[mod_id]
            for d in h.get("issues", []):
                iss = _issue_from_dict(d)
                if h.get("version") != mod.version or mod_mtime > h.get("session_time", 0):
                    iss.stale, iss.stale_note = True, "the mod has been updated since, so this may be fixed"
                issues.append(iss)
            tested[mod_id] = (time.strftime("%b %d %H:%M", time.localtime(h.get("session_time", 0))),
                              h.get("session_time", 0))

        # Log errors about long paths are history once the file system problem is fixed.
        if not any(i.key == "ritsulib-cache" for i in mod.static_issues):
            for iss in issues:
                if iss.key == "path-too-long" and not iss.stale:
                    iss.stale, iss.stale_note = True, "the file system problem behind this has been fixed"
        # A crash that followed an error we now consider fixed is probably fixed too.
        crash_cause = (session.crash_key if session else None)
        for iss in issues:
            if iss.key == "crash" and not iss.stale and crash_cause and \
                    any(i.key == crash_cause and i.stale for i in issues):
                iss.stale = True
                iss.stale_note = "it happened right after an error that has since been fixed"

        # The Workshop branch check runs even for disabled mods: use the newest report.
        if not any(i.key == "branch" for i in issues):
            for s in sessions:
                branch = s.issues.get(mod_id, {}).get("branch")
                if branch:
                    branch = dataclasses.replace(branch)
                    branch.stale = mod_mtime > s.mtime
                    branch.stale_note = "the mod has been updated since, so this may be fixed" if branch.stale else ""
                    issues.append(branch)
                    break

        issues = _dedupe(issues)
        issues.sort(key=lambda i: (i.stale, SEVERITY_RANK[i.severity]))
        by_mod[mod_id] = issues
        status[mod_id] = _status(issues, mod_id in tested)

    if history_changed:
        _save_history(history)
    global_issues = sessions[0].global_issues if sessions else []
    return HealthReport(by_mod=by_mod, status=status, tested=tested,
                        global_issues=_dedupe(global_issues), sessions=sessions)


def _status(issues, was_tested):
    live = [i.severity for i in issues if not i.stale]
    if ERROR in live:
        return S_ERROR
    if WARN in live:
        return S_WARN
    if any(i.stale and i.severity in (ERROR, WARN) for i in issues):
        return S_STALE
    return S_OK if was_tested else S_UNTESTED


def _mod_mtime(mod):
    try:
        return os.path.getmtime(mod.manifest_path)
    except OSError:
        return 0


def _dedupe(issues):
    out = {}
    for iss in (dataclasses.replace(i) for i in issues):
        k = iss.key or iss.title
        if k in out:
            if out[k].source == iss.source:
                out[k].count += iss.count
            else:   # same problem seen by two different checks: don't double count
                out[k].count = max(out[k].count, iss.count)
        else:
            out[k] = iss
    return list(out.values())
