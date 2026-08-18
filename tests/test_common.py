"""Tests for _common.py: frontmatter, config, entry iteration, ref matching."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import _util  # noqa: F401  (puts plugin/scripts on sys.path)
from _common import (DEFAULTS, _FM_HEAD_CHARS, config_issues, fold,
                     iter_entries, load_config, norm_rel, parse_frontmatter,
                     read_frontmatter, ref_exists, ref_matches,
                     stale_after_days, words)
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


if __name__ == "__main__":
    unittest.main()
