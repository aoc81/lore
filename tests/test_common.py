"""Tests for _common.py: frontmatter, config, entry iteration, ref matching."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import _util  # noqa: F401  (puts plugin/scripts on sys.path)
from _common import (DEFAULTS, iter_entries, load_config, norm_rel,
                     parse_frontmatter, ref_exists, ref_matches)
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
