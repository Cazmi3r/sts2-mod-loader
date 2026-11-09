import argparse
import sys

from . import __version__


def check():
    from . import game as game_mod, logscan, mods as mods_mod, settings as settings_mod
    game = game_mod.locate()
    for p in game.problems:
        print("!", p)
    mods = mods_mod.scan(game)
    st = settings_mod.Settings(game.settings_path) if game.settings_path else None
    from . import apicheck
    api = apicheck.check_mods(mods, game)
    report = logscan.analyze(mods, game.logs_dir, live=game_mod.is_running(),
                             api_results=api, game_version_text=game.version_text, game=game)
    print(f"Game {game.version_text} on branch '{game.branch}', {len(mods)} mods installed")
    for g in report.global_issues:
        print(f"  [{g.severity}] {g.title}")
    icons = {"error": "✗", "warn": "!", "stale": "~", "ok": "✓", "untested": "?"}
    def sort_key(m):
        return (logscan.STATUS_RANK[report.status[m.id]], m.name.lower())
    for mod in sorted(mods.values(), key=sort_key):
        on = st.is_enabled(mod.id) if st else True
        issues = report.by_mod[mod.id]
        tested = report.tested.get(mod.id)
        when = f"  (tested {tested[0]})" if tested else "  (no data)"
        print(f"{icons[report.status[mod.id]]} {'on ' if on else 'off'} {mod.name} [{mod.id}] {mod.version}{when}")
        for i in issues:
            stale = f" (stale: {i.stale_note})" if i.stale else ""
            count = f" x{i.count}" if i.count > 1 else ""
            print(f"      [{i.severity}] {i.title}{count}{stale}")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--steam-helper"]:          # internal: one Steam request (see steamugc.py)
        from .steamugc import helper_main
        return helper_main(argv[1:])
    ap = argparse.ArgumentParser(prog="sts2-mod-loader")
    ap.add_argument("--check", action="store_true", help="print a health report and exit")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)
    if args.check:
        return check()
    from .gui import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
