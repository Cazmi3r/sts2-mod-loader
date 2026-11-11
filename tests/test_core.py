import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sts2mods import game as game_mod  # noqa: E402
from sts2mods import logscan, mods as mods_mod, profiles, settings as settings_mod, vdf, workshop  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def manifest(mod_id, deps=(), **extra):
    data = {"id": mod_id, "name": extra.pop("name", mod_id), "version": extra.pop("version", "1.0.0"),
            "has_dll": extra.pop("has_dll", True), "has_pck": extra.pop("has_pck", False),
            "dependencies": [{"id": d} for d in deps]}
    data.update(extra)
    return json.dumps(data)


class FakeGame(game_mod.Game):
    pass


class Sandbox(unittest.TestCase):
    """A fake Steam library + user data dir in a temp folder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sts2ml-test-")
        self.workshop = os.path.join(self.tmp, "workshop")
        self.local = os.path.join(self.tmp, "game", "mods")
        self.user = os.path.join(self.tmp, "user")
        os.makedirs(self.workshop)
        os.makedirs(self.local)
        os.makedirs(os.path.join(self.user, "logs"))
        self.settings_path = os.path.join(self.user, "steam", "1", "settings.save")
        write(self.settings_path, json.dumps({"volume_master": 0.5, "mod_settings": {
            "mod_list": [{"id": "Lib", "is_enabled": True, "source": "steam_workshop"},
                         {"id": "Gone", "is_enabled": True, "source": "steam_workshop"}],
            "mods_enabled": True}}, indent=2))
        self.game = FakeGame(install_dir=os.path.join(self.tmp, "game"), workshop_dir=self.workshop,
                             user_data=self.user, settings_path=self.settings_path,
                             version=(0, 111, 0), version_text="v0.111.0", branch="public-beta")
        self.patches = [
            mock.patch.object(settings_mod, "BACKUP_DIR", os.path.join(self.tmp, "backups")),
            mock.patch.object(game_mod, "is_running", return_value=False),
            mock.patch.object(logscan, "HISTORY_PATH", os.path.join(self.tmp, "history.json")),
            mock.patch.object(profiles, "PROFILE_DIR", os.path.join(self.tmp, "profiles")),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp)

    def add_workshop_mod(self, wid, mod_id, files=None, **kw):
        d = os.path.join(self.workshop, wid)
        write(os.path.join(d, f"{mod_id}.json"), manifest(mod_id, **kw))
        for f in files if files is not None else [f"{mod_id}.dll"]:
            write(os.path.join(d, f), "x")
        return d


class TestVdf(unittest.TestCase):
    def test_nested(self):
        data = vdf.loads('"A" { "b" "1" "C" { "d" "x\\"y" } // comment\n "e" "2" }')
        self.assertEqual(data, {"A": {"b": "1", "C": {"d": 'x"y'}, "e": "2"}})


class TestVersions(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(game_mod.parse_version("v0.111.0"), (0, 111, 0))
        self.assertEqual(game_mod.parse_version("1.2"), (1, 2, 0))
        self.assertIsNone(game_mod.parse_version("咕咕嘎嘎"))


class TestScan(Sandbox):
    def test_scan_and_static_checks(self):
        self.add_workshop_mod("1", "Lib")
        self.add_workshop_mod("2", "Char", deps=["Lib", "Missing"])
        self.add_workshop_mod("3", "CaseBug", has_pck=True, files=["CaseBug.dll", "Casebug.pck"])
        self.add_workshop_mod("4", "Future", min_game_version="0.200.0")
        mods = mods_mod.scan(self.game)
        self.assertEqual(set(mods), {"Lib", "Char", "CaseBug", "Future"})
        keys = lambda m: {i.key for i in mods[m].static_issues}  # noqa: E731
        self.assertIn("dep-missing-Missing", keys("Char"))
        self.assertNotIn("dep-missing-Lib", keys("Char"))
        self.assertIn("min-game", keys("Future"))
        if os.path.exists(os.path.join(self.tmp, "GAME")):
            # Windows/macOS ignore capitalization: the game finds the file, so nothing to report.
            self.assertNotIn("case-pck", keys("CaseBug"))
        else:
            self.assertIn("case-pck", keys("CaseBug"))
            fix = next(i.fix for i in mods["CaseBug"].static_issues if i.fix)
            self.assertEqual(fix[0], "symlink")
            self.assertEqual(fix[1], "Casebug.pck")

    def test_duplicate_prefers_higher_version(self):
        self.add_workshop_mod("1", "Dup", version="1.0.0")
        write(os.path.join(self.local, "Dup", "Dup.json"), manifest("Dup", version="2.0.0"))
        write(os.path.join(self.local, "Dup", "Dup.dll"), "x")
        mods = mods_mod.scan(self.game)
        self.assertEqual(mods["Dup"].source, mods_mod.LOCAL)
        self.assertEqual(len(mods["Dup"].shadowed), 1)

    def test_dependency_closures(self):
        self.add_workshop_mod("1", "Lib")
        self.add_workshop_mod("2", "Mid", deps=["Lib"])
        self.add_workshop_mod("3", "Top", deps=["Mid"])
        mods = mods_mod.scan(self.game)
        self.assertEqual(mods_mod.dependency_closure(mods, "Top"), {"Mid", "Lib"})
        self.assertEqual(mods_mod.dependent_closure(mods, "Lib"), {"Mid", "Top"})


class TestShortFileNames(Sandbox):
    """Encrypted home folders cap file names at 143 characters. The fix moves the game's whole
    data folder to an unencrypted disk and links it back."""

    def test_relocate_user_data(self):
        base = os.path.join(self.tmp, "unencrypted")
        os.makedirs(base)
        small = lambda p: 255 if "unencrypted" in os.path.realpath(p) else 143  # noqa: E731
        with mock.patch.object(mods_mod, "_name_max", side_effect=small), \
                mock.patch.object(mods_mod, "RELOCATE_BASE", base), \
                mock.patch.object(mods_mod.osenv, "IS_LINUX", True):
            iss = mods_mod.filesystem_issue(self.game)
            self.assertEqual(iss.fix[0], "relocate-user-data")
            os.makedirs(os.path.dirname(iss.fix[2]))
            mods_mod.apply_fix(iss.fix)
            self.assertTrue(os.path.islink(self.user))
            self.assertEqual(json.loads(read(self.settings_path))["volume_master"], 0.5)  # saves kept
            self.assertIsNone(mods_mod.filesystem_issue(self.game))
            report = logscan.analyze({}, self.game.logs_dir, use_history=False, game=self.game)
            self.assertFalse(any(i.key == "short-file-names" for i in report.global_issues))

    def test_not_needed_on_normal_filesystem(self):
        with mock.patch.object(mods_mod, "_name_max", return_value=255):
            self.assertIsNone(mods_mod.filesystem_issue(self.game))


class TestSettings(Sandbox):
    def test_save_preserves_other_keys_and_unknown_mods(self):
        self.add_workshop_mod("1", "Lib")
        self.add_workshop_mod("2", "New")
        mods = mods_mod.scan(self.game)
        st = settings_mod.Settings(self.settings_path)
        self.assertTrue(st.is_enabled("New"))           # not listed yet -> enabled
        backup = st.save({"Lib": False, "New": True}, ["New", "Lib"], mods)
        self.assertTrue(os.path.exists(backup))
        data = json.loads(read(self.settings_path))
        self.assertEqual(data["volume_master"], 0.5)
        ids = [e["id"] for e in data["mod_settings"]["mod_list"]]
        self.assertEqual(ids, ["New", "Lib", "Gone"])   # order kept, uninstalled entry kept
        self.assertFalse(data["mod_settings"]["mod_list"][1]["is_enabled"])
        self.assertEqual(data["mod_settings"]["mod_list"][0]["source"], "steam_workshop")

    def test_refuses_while_running(self):
        st = settings_mod.Settings(self.settings_path)
        with mock.patch.object(game_mod, "is_running", return_value=True):
            with self.assertRaises(settings_mod.GameRunningError):
                st.save({}, [], {})

    def test_restore(self):
        st = settings_mod.Settings(self.settings_path)
        original = read(self.settings_path)
        backup = st.save({"Lib": False}, ["Lib"], {})
        settings_mod.restore_backup(backup, self.settings_path)
        self.assertEqual(read(self.settings_path), original)


LOG = """\
[INFO] Found mod manifest file {ws}/1/Lib.json
[INFO] Found mod manifest file {ws}/2/Char.json
[ERROR] Tried to load mod with id Branchy, but the current Steam branch public-beta does not lie in the min/max steam branches the mod supports! Max supported: public
[INFO] Skipping loading mod Off, it is set to disabled in settings
[INFO] Loading assembly DLL {ws}/2/Char.dll
[INFO] Calling initializer method of type CharNs.Main for Char, Version=1.0.0.0, Culture=neutral, PublicKeyToken=null
[ERROR] Exception thrown when calling mod initializer of type CharNs.Main: System.Reflection.TargetInvocationException: Exception has been thrown by the target of an invocation.
 ---> System.TypeLoadException: Could not load type 'X' from assembly 'sts2'.
   at CharNs.Main.Init()
[INFO] Mod List:
  * Lib Mod [Lib] (1.0.0) workshopId=1
  * Char Mod [Char] (1.0.0) workshopId=2
[ERROR] [Lib] something odd happened 42
ERROR: No loader found for resource: res://Char/images/a.png (expected type: unknown)
[ERROR] Received connection failed exception while joining game: Failed to connect to the host. Reason: ModMismatch
"""


class TestLogscan(Sandbox):
    def setUp(self):
        super().setUp()
        self.add_workshop_mod("1", "Lib")
        self.add_workshop_mod("2", "Char", deps=["Lib"])
        self.add_workshop_mod("3", "Branchy")
        self.mods = mods_mod.scan(self.game)
        for m in self.mods.values():                 # make mod files older than the log
            os.utime(m.manifest_path, (1, 1))

    def test_attribution(self):
        write(os.path.join(self.user, "logs", "godot.log"), LOG.format(ws=self.workshop))
        rep = logscan.analyze(self.mods, os.path.join(self.user, "logs"))
        keys = lambda m: {i.key for i in rep.by_mod[m]}  # noqa: E731
        self.assertIn("init-crash", keys("Char"))
        self.assertIn("assets", keys("Char"))
        self.assertIn("tag", keys("Lib"))
        self.assertIn("branch", keys("Branchy"))
        self.assertEqual(rep.status["Char"], logscan.S_ERROR)
        self.assertEqual(rep.status["Lib"], logscan.S_WARN)
        self.assertTrue(any(i.key == "mismatch" for i in rep.global_issues))

    def test_crash_detection_and_history(self):
        crash = LOG.format(ws=self.workshop) + "[ERROR] [Lib] fatal thing\n"
        write(os.path.join(self.user, "logs", "godot.log"), crash)
        rep = logscan.analyze(self.mods, os.path.join(self.user, "logs"))
        self.assertIn("crash", {i.key for i in rep.by_mod["Lib"]})
        # Not a crash while the session is still live:
        rep_live = logscan.analyze(self.mods, os.path.join(self.user, "logs"), live=True)
        self.assertNotIn("crash", {i.key for i in rep_live.by_mod["Lib"]})
        # Logs rotate away; history still remembers.
        os.remove(os.path.join(self.user, "logs", "godot.log"))
        rep2 = logscan.analyze(self.mods, os.path.join(self.user, "logs"))
        self.assertIn("init-crash", {i.key for i in rep2.by_mod["Char"]})
        self.assertEqual(rep2.status["Char"], logscan.S_ERROR)

    def test_updated_mod_marks_stale(self):
        write(os.path.join(self.user, "logs", "godot.log"), LOG.format(ws=self.workshop))
        os.utime(os.path.join(self.user, "logs", "godot.log"), (1000, 1000))
        os.utime(self.mods["Char"].manifest_path, (5000, 5000))
        rep = logscan.analyze(self.mods, os.path.join(self.user, "logs"))
        self.assertEqual(rep.status["Char"], logscan.S_STALE)


class TestProfiles(Sandbox):
    def test_roundtrip_and_compare(self):
        self.add_workshop_mod("1", "Lib")
        self.add_workshop_mod("2", "Char", deps=["Lib"])
        self.add_workshop_mod("3", "Mine")
        mods = mods_mod.scan(self.game)
        profiles.save_profile("Co-op", {"Lib": True, "Char": False, "Mine": True}, ["Lib", "Char", "Mine"], mods)
        self.assertEqual(profiles.list_profiles(), ["Co-op"])
        self.assertFalse(next(m for m in profiles.load_profile("Co-op")["mods"] if m["id"] == "Char")["enabled"])

        theirs = {"branch": "public", "game_version": "v0.111.0", "mods": [
            {"id": "Lib", "version": "1.0.0"}, {"id": "Char", "version": "0.9"},
            {"id": "Other", "workshop_id": "9"}, {"id": "Emotes", "affects_gameplay": False}]}
        diff = profiles.compare(theirs, {"Lib": True, "Char": True, "Mine": True}, mods, self.game)
        self.assertEqual(diff["branch"], ("public-beta", "public"))
        self.assertEqual([t["id"] for t in diff["missing"]], ["Other"])
        self.assertEqual([m.id for m in diff["extra"]], ["Mine"])
        self.assertEqual([p[0].id for p in diff["version"]], ["Char"])
        self.assertEqual(len(diff["optional_only"]), 1)
        self.assertFalse(profiles.diff_is_clean(diff))

        code = profiles.share_code({"Lib": True, "Char": False, "Mine": True}, mods, self.game)
        self.assertEqual({m["workshop_id"] for m in profiles.parse_share_code(code)["mods"]}, {"1", "3"})


class TestShareCode(Sandbox):
    def test_roundtrip_inside_chat_message(self):
        self.add_workshop_mod("1", "Lib")
        write(os.path.join(self.local, "Mine", "Mine.json"), manifest("Mine", name="My Local Mod"))
        write(os.path.join(self.local, "Mine", "Mine.dll"), "x")
        mods = mods_mod.scan(self.game)
        code = profiles.share_code({"Lib": True, "Mine": True}, mods, self.game)
        self.assertTrue(code.startswith(profiles.SHARE_PREFIX))
        theirs = profiles.parse_share_code(f"yo use this:\n{code}\nthanks")
        self.assertEqual(theirs["branch"], "public-beta")
        self.assertEqual([m["workshop_id"] for m in theirs["mods"] if m["workshop_id"]], ["1"])
        self.assertEqual([m["id"] for m in theirs["mods"] if not m["workshop_id"]], ["Mine"])
        self.assertTrue(profiles.diff_is_clean(profiles.compare(theirs, {"Lib": True, "Mine": True},
                                                                mods, self.game)))

    def test_compare_by_workshop_id(self):
        self.add_workshop_mod("3747500076", "Lib")
        self.add_workshop_mod("3747501308", "Char", version="2.0.0")
        mods = mods_mod.scan(self.game)
        code = profiles.share_code({"Lib": True, "Char": True}, mods, self.game)
        mods["Char"].version = "2.0.1"                      # I have a newer copy
        diff = profiles.compare(profiles.parse_share_code(code), {"Lib": False, "Char": True}, mods, self.game)
        self.assertEqual([t["id"] for t in diff["disabled"]], ["Lib"])     # resolved from Workshop ID
        self.assertEqual([p[0].id for p in diff["version"]], ["Char"])
        theirs = profiles.parse_share_code(code)
        diff = profiles.compare(theirs, {}, {}, self.game)
        self.assertEqual(sorted(t["workshop_id"] for t in diff["missing"]), ["3747500076", "3747501308"])

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            profiles.parse_share_code("hello")
        with self.assertRaises(ValueError):
            profiles.parse_share_code(profiles.SHARE_PREFIX + "abc")


class TestWorkshopBrowse(unittest.TestCase):
    def test_query_item_id(self):
        self.assertEqual(workshop.query_item_id("3776911445"), "3776911445")
        self.assertEqual(workshop.query_item_id(
            "https://steamcommunity.com/sharedfiles/filedetails/?id=3776911445&searchtext="), "3776911445")
        self.assertIsNone(workshop.query_item_id("baselib"))
        self.assertIsNone(workshop.query_item_id("mod 2"))

    def test_parse_browse(self):
        html = ('<a href="https://steamcommunity.com/sharedfiles/filedetails/?id=111111">'
                '<img></a><a href="https://steamcommunity.com/sharedfiles/filedetails/?id=111111">A</a>'
                '<a href="https://steamcommunity.com/sharedfiles/filedetails/?id=222222">B</a>'
                '{\\"current_page\\":1,\\"total_pages\\":120,\\"total_count\\":3596}')
        self.assertEqual(workshop.parse_browse(html), (["111111", "222222"], 120))
        self.assertEqual(workshop.parse_browse("<html></html>"), ([], None))

    def test_english_ratio(self):
        self.assertEqual(workshop.english_ratio({"title": "Better Map", "description": "[b]Shows more[/b]"}), 1.0)
        self.assertEqual(workshop.english_ratio({"title": "更好的地图", "description": "显示更多"}), 0.0)
        self.assertEqual(workshop.english_ratio({}), 1.0)
        # Bilingual titles are shown even though most of their letters are Chinese.
        self.assertTrue(workshop.looks_english({"title": "BetterMap 更好的地图模组", "description": ""}))
        self.assertTrue(workshop.looks_english({"title": "ARAM: Mayhem / 海克斯大乱斗", "description": "全中文介绍"}))
        # Chinese listings with only filler English stay hidden.
        self.assertFalse(workshop.looks_english({"title": "【CN】角色禁用 Mod Beta v1.2", "description": "显示更多"}))
        self.assertFalse(workshop.looks_english({"title": "更好的地图", "description": "显示更多"}))
        self.assertTrue(workshop.looks_english({"title": "更好的地图", "description": "Shows more of the map"}))

    def test_browse_offline(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            self.assertIsNone(workshop.browse("baselib"))
        self.assertEqual(workshop.browse(" 3776911445 "), (["3776911445"], 1))


@unittest.skipUnless(game_mod.locate().install_dir, "game not installed")
class TestApiCheckOnRealGame(unittest.TestCase):
    """Smoke test against the real install: parses sts2.dll and every mod without crashing."""

    def test_real(self):
        from sts2mods import apicheck
        g = game_mod.locate()
        mods = mods_mod.scan(g)
        with mock.patch.object(apicheck, "CACHE_PATH", os.path.join(tempfile.mkdtemp(), "c.json")):
            res = apicheck.check_mods(mods, g)
        self.assertTrue(res)
        self.assertFalse([mid for mid, r in res.items() if r.error], "metadata parse errors")


class TestSslContext(unittest.TestCase):
    def setUp(self):
        from sts2mods import osenv
        self.osenv = osenv

    def test_missing_only_on_a_mac_python_without_certificates(self):
        with mock.patch.object(self.osenv, "IS_MAC", True), \
                mock.patch.object(self.osenv, "_python_has_ca_certs", return_value=False):
            self.assertTrue(self.osenv.certificates_missing())
        with mock.patch.object(self.osenv, "IS_MAC", True), \
                mock.patch.object(self.osenv, "_python_has_ca_certs", return_value=True):
            self.assertFalse(self.osenv.certificates_missing())
        with mock.patch.object(self.osenv, "IS_MAC", False), \
                mock.patch.object(self.osenv, "_python_has_ca_certs", return_value=False):
            self.assertFalse(self.osenv.certificates_missing())

    def test_uses_pythons_own_settings(self):
        ctx = self.osenv.ssl_context()
        self.assertIs(ctx, self.osenv.ssl_context())
        self.assertEqual(ctx.verify_mode, __import__("ssl").CERT_REQUIRED)


if __name__ == "__main__":
    unittest.main()
