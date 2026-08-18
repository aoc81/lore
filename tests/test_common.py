"""Tests for _common.py: frontmatter, config, entry iteration, ref matching."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import _util  # noqa: F401  (puts plugin/scripts on sys.path)
from _common import (DEFAULTS, _FM_HEAD_CHARS, config_issues, fold,
                     is_under, iter_entries, iter_store_entries, load_config,
                     norm_rel, parse_frontmatter, personal_store,
                     read_frontmatter, ref_exists, ref_matches, rel_path,
                     stale_after_days, store_dirs, words)
from _util import write_entry


class TestParseFrontmatter(unittest.TestCase):
    def test_inline_list_and_scalars(self):
        fm = parse_frontmatter(
            '---\ntitle: "A thing"\ntags: [a, b, "c"]\nstatus: current\n---\nbody')
        self.assertEqual(fm["title"], "A thing")
        self.assertEqual(fm["tags"], ["a", "b", "c"])
        self.assertEqual(fm["status"], "current")

    def test_block_list(self):
        fm = parse_frontmatter("---\nfiles:\n  - a.py\n  - 'b.py'\n---\n")
        self.assertEqual(fm["files"], ["a.py", "b.py"])

    def test_empty_list_and_missing_block(self):
        self.assertEqual(parse_frontmatter("---\ntags: []\n---\n")["tags"], [])
        self.assertEqual(parse_frontmatter("no frontmatter here"), {})


class TestLoadConfig(unittest.TestCase):
    def _cfg(self, payload):
        with tempfile.TemporaryDirectory() as td:
            Path(td, ".lore.json").write_text(json.dumps(payload),
                                             encoding="utf-8")
            return load_config(td)

    def test_defaults_when_no_file(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = load_config(td)
        self.assertEqual(cfg["storeDir"], DEFAULTS["storeDir"])
        self.assertEqual(cfg["captureNudge"], "smart")

    def test_valid_overrides(self):
        cfg = self._cfg({"storeDir": "kb", "maxRecall": 3,
                         "captureNudge": "off"})
        self.assertEqual((cfg["storeDir"], cfg["maxRecall"],
                          cfg["captureNudge"]), ("kb", 3, "off"))

    def test_bad_types_are_ignored(self):
        cfg = self._cfg({"maxRecall": "5", "storeDir": 7,
                         "staleStatuses": "superseded",
                         "captureNudge": "sometimes", "unknown": 1})
        self.assertEqual(cfg["maxRecall"], 5)
        self.assertEqual(cfg["storeDir"], "learnings")
        self.assertEqual(cfg["staleStatuses"], DEFAULTS["staleStatuses"])
        self.assertEqual(cfg["captureNudge"], "smart")
        self.assertNotIn("unknown", cfg)

    def test_bool_shorthand(self):
        self.assertEqual(self._cfg({"captureNudge": True})["captureNudge"],
                         "smart")
        self.assertEqual(self._cfg({"captureNudge": False})["captureNudge"],
                         "off")

    def test_malformed_json_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, ".lore.json").write_text("{broken", encoding="utf-8")
            self.assertEqual(load_config(td)["maxRecall"],
                             DEFAULTS["maxRecall"])

    def test_new_keys_validate(self):
        cfg = self._cfg({"staleAfterMonths": 12, "stopWords": ["widget"]})
        self.assertEqual(cfg["staleAfterMonths"], 12)
        self.assertEqual(cfg["stopWords"], ["widget"])
        bad = self._cfg({"staleAfterMonths": 0, "stopWords": "widget"})
        self.assertEqual(bad["staleAfterMonths"], 6)
        self.assertEqual(bad["stopWords"], [])

    def test_personal_store_dir_accepts_empty_string(self):
        # "" is the documented off switch, so it must NOT be treated as invalid
        # (unlike storeDir, where empty would mean "the project root").
        cfg = self._cfg({"personalStoreDir": "~/.lore/learnings"})
        self.assertEqual(cfg["personalStoreDir"], "~/.lore/learnings")
        self.assertEqual(self._cfg({"personalStoreDir": ""})
                         ["personalStoreDir"], "")
        self.assertEqual(self._cfg({"personalStoreDir": 7})
                         ["personalStoreDir"], "")

    def test_stale_after_days(self):
        self.assertEqual(stale_after_days(DEFAULTS), 183)  # the old constant
        self.assertEqual(stale_after_days(dict(DEFAULTS, staleAfterMonths=12)),
                         365)


class TestConfigIssues(unittest.TestCase):
    def _issues(self, payload):
        with tempfile.TemporaryDirectory() as td:
            Path(td, ".lore.json").write_text(json.dumps(payload),
                                              encoding="utf-8")
            return config_issues(td)

    def test_reports_unknown_and_invalid_keys(self):
        unknown, invalid = self._issues({"maxRecal": 3, "storeDir": 7,
                                         "maxRecall": 4})
        self.assertEqual(unknown, ["maxRecal"])
        self.assertEqual(invalid, ["storeDir"])

    def test_clean_config_and_missing_file(self):
        self.assertEqual(self._issues({"maxRecall": 4}), ([], []))
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(config_issues(td), ([], []))


class TestTokenizing(unittest.TestCase):
    def test_accented_words_stay_whole_and_fold(self):
        self.assertEqual(words("autenticación"), ["autenticacion"])
        self.assertEqual(words("Año Español"), ["ano", "espanol"])
        self.assertEqual(fold("Título"), "titulo")

    def test_ascii_behavior_is_unchanged(self):
        self.assertEqual(words("Cache_key includes lockfile v2"),
                         ["cache_key", "includes", "lockfile", "v2"])


class TestIterEntries(unittest.TestCase):
    def test_skips_readme_templates_and_dotfiles(self):
        with tempfile.TemporaryDirectory() as td:
            store = Path(td)
            (store / "README.md").write_text("index", encoding="utf-8")
            (store / "_TEMPLATE.md").write_text(
                '---\ntitle: ""\n---\n', encoding="utf-8")
            (store / ".draft.md").write_text("x", encoding="utf-8")
            write_entry(store, "ci/cache.md", title="Cache key",
                        tags=("ci",), files=("a.yml",))
            write_entry(store, "api/pager.md")  # empty title -> stem
            entries = list(iter_entries(store))
        names = sorted(e["path"].name for e in entries)
        self.assertEqual(names, ["cache.md", "pager.md"])
        by_name = {e["path"].name: e for e in entries}
        self.assertEqual(by_name["pager.md"]["title"], "pager")
        self.assertEqual(by_name["cache.md"]["category"], "ci")


class TestReadFrontmatter(unittest.TestCase):
    """Only the head of an entry is read -- bodies never reach the hooks."""

    def _entry(self, body):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Path(td.name) / "e.md"
        p.write_text('---\ntitle: "T"\ntags: [a]\n---\n' + body,
                     encoding="utf-8")
        return p

    def test_huge_body_is_not_read(self):
        p = self._entry("x" * (_FM_HEAD_CHARS * 4))
        self.assertEqual(read_frontmatter(p)["title"], "T")

    def test_frontmatter_longer_than_the_head_still_parses(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Path(td.name) / "e.md"
        padding = "".join(f"pad{i}: x\n" for i in range(_FM_HEAD_CHARS // 4))
        p.write_text(f'---\n{padding}title: "Late"\n---\nbody\n',
                     encoding="utf-8")
        self.assertGreater(p.stat().st_size, _FM_HEAD_CHARS)
        self.assertEqual(read_frontmatter(p)["title"], "Late")

    def test_missing_file(self):
        self.assertIsNone(read_frontmatter(Path("does-not-exist.md")))


class TestRefMatching(unittest.TestCase):
    def test_norm_rel(self):
        self.assertEqual(norm_rel(".\\src\\a.py"), "src/a.py")
        self.assertEqual(norm_rel("./x/y"), "x/y")

    def test_exact_dir_and_glob(self):
        self.assertTrue(ref_matches("src/a.py", "src/a.py"))
        self.assertFalse(ref_matches("src/a.py", "src/b.py"))
        self.assertTrue(ref_matches("src/auth/", "src/auth/login.py"))
        self.assertFalse(ref_matches("src/auth/", "src/authx/login.py"))
        self.assertTrue(ref_matches("src/*.py", "src/a.py"))
        self.assertFalse(ref_matches("src/*.py", "lib/a.py"))

    @unittest.skipUnless(os.name == "nt", "Windows-only case folding")
    def test_windows_case_insensitive(self):
        self.assertTrue(ref_matches("Src\\Auth\\Login.py", "src/auth/login.py"))

    def test_ref_exists(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            (proj / "sub").mkdir()
            (proj / "sub" / "a.md").write_text("x", encoding="utf-8")
            self.assertTrue(ref_exists(proj, "sub/a.md"))
            self.assertFalse(ref_exists(proj, "sub/missing.md"))
            self.assertTrue(ref_exists(proj, "sub/"))
            self.assertFalse(ref_exists(proj, "nope/"))
            self.assertTrue(ref_exists(proj, "sub/*.md"))
            self.assertFalse(ref_exists(proj, "sub/*.py"))
            self.assertTrue(ref_exists(proj, ""))  # noise, not staleness


class TestPersonalStore(unittest.TestCase):
    """The private second store: resolution rules and the redundancy guard."""

    def _cfg(self, personal, store="learnings"):
        cfg = dict(DEFAULTS)
        cfg["storeDir"] = store
        cfg["personalStoreDir"] = personal
        return cfg

    def test_unset_means_off(self):
        self.assertIsNone(personal_store("/proj", self._cfg("")))
        self.assertIsNone(personal_store("/proj", dict(DEFAULTS)))

    def test_relative_resolves_under_project(self):
        with tempfile.TemporaryDirectory() as td:
            got = personal_store(td, self._cfg(".lore/personal"))
            self.assertEqual(got, Path(td) / ".lore" / "personal")

    def test_tilde_expands_to_home(self):
        got = personal_store("/proj", self._cfg("~/.lore/learnings"))
        self.assertEqual(got, Path.home() / ".lore" / "learnings")

    def test_absolute_is_kept(self):
        absolute = Path(tempfile.gettempdir()).resolve() / "lore-personal"
        got = personal_store("/proj", self._cfg(str(absolute)))
        self.assertEqual(got, absolute)

    def test_inside_team_store_is_refused(self):
        # It would be found twice AND committed -- the opposite of private.
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(
                personal_store(td, self._cfg("learnings/private")))
            self.assertIsNone(personal_store(td, self._cfg("learnings")))

    def test_store_dirs_order_and_flags(self):
        with tempfile.TemporaryDirectory() as td:
            stores = store_dirs(td, self._cfg("personal"))
        self.assertEqual([flag for _s, flag in stores], [False, True])
        self.assertEqual(stores[0][0].name, "learnings")
        self.assertEqual(stores[1][0].name, "personal")

    def test_store_dirs_team_only_when_unset(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(len(store_dirs(td, dict(DEFAULTS))), 1)


class TestIterStoreEntries(unittest.TestCase):
    def test_both_stores_team_first_and_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            write_entry(proj / "learnings", "a/team.md", title="Team")
            write_entry(proj / "personal", "b/mine.md", title="Mine")
            cfg = dict(DEFAULTS, personalStoreDir="personal")
            entries = list(iter_store_entries(store_dirs(proj, cfg)))
        self.assertEqual([e["title"] for e in entries], ["Team", "Mine"])
        self.assertEqual([e["personal"] for e in entries], [False, True])

    def test_missing_personal_store_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            write_entry(proj / "learnings", "a/team.md", title="Team")
            cfg = dict(DEFAULTS, personalStoreDir="nope")
            entries = list(iter_store_entries(store_dirs(proj, cfg)))
        self.assertEqual([e["title"] for e in entries], ["Team"])

    def test_single_path_arg_still_works(self):
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "learnings"
            write_entry(store, "a/e.md", title="E")
            entries = list(iter_store_entries(store))
        self.assertEqual([(e["title"], e["personal"]) for e in entries],
                         [("E", False)])


class TestRelPathAndIsUnder(unittest.TestCase):
    def test_inside_is_relative_outside_is_absolute(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            self.assertEqual(rel_path(proj, proj / "learnings" / "a.md"),
                             "learnings/a.md")
            outside = Path(td).parent / "elsewhere" / "b.md"
            self.assertEqual(rel_path(proj, outside), outside.as_posix())

    def test_is_under(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            self.assertTrue(is_under(proj, proj / "a" / "b.md"))
            self.assertTrue(is_under(proj, proj))
            self.assertFalse(is_under(proj / "a", proj / "b" / "c.md"))


if __name__ == "__main__":
    unittest.main()
