"""Tests for verify_refs.py: dates, dupes, config, index, git drift."""
import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import _util  # noqa: F401
from _common import DEFAULTS, iter_entries
from _util import write_entry
import verify_refs

GIT = shutil.which("git")


class TestParseDate(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(verify_refs._parse_date("2026-01-02"),
                         date(2026, 1, 2))
        self.assertEqual(verify_refs._parse_date("2026-01-02  # note"),
                         date(2026, 1, 2))
        self.assertIsNone(verify_refs._parse_date(""))
        self.assertIsNone(verify_refs._parse_date("not-a-date"))


class TestDupes(unittest.TestCase):
    def _entries(self, specs):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        store = Path(td.name)
        for rel, title, tags in specs:
            write_entry(store, rel, title=title, tags=tags)
        return list(iter_entries(store))

    def test_overlapping_pair_found(self):
        entries = self._entries([
            ("ci/a.md", "Docker cache key includes lockfile", ("docker", "cache")),
            ("ci/b.md", "Docker build cache and the lockfile", ("docker", "cache")),
            ("api/c.md", "Pagination cursor is opaque", ("pagination",)),
        ])
        pairs = verify_refs._dupe_pairs(entries)
        self.assertEqual(len(pairs), 1)
        _n, a, b, shared = pairs[0]
        self.assertGreaterEqual(len(shared), 3)

    def test_generic_words_do_not_pair(self):
        entries = self._entries([
            ("a/a.md", "Fix the bug and the error", ("misc",)),
            ("b/b.md", "Why not fix an issue", ("other",)),
        ])
        self.assertEqual(verify_refs._dupe_pairs(entries), [])

    def test_category_variants(self):
        entries = self._entries([
            ("x/a.md", "Aaa bbb ccc", ()), ("x/b.md", "Ddd eee fff", ()),
        ])
        entries[0]["category"] = "CI"
        entries[1]["category"] = "ci"
        groups = verify_refs._category_variants(entries)
        self.assertEqual(groups, [{"CI", "ci"}])


class TestVersionWarning(unittest.TestCase):
    def test_warns_only_on_mismatch(self):
        plug = verify_refs.plugin_version()
        self.assertTrue(plug)  # test tree includes the manifest
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            self.assertIsNone(verify_refs.version_warning(proj))  # no stamp
            (proj / ".lore").mkdir()
            (proj / ".lore" / "VERSION").write_text(plug, encoding="utf-8")
            self.assertIsNone(verify_refs.version_warning(proj))  # in sync
            (proj / ".lore" / "VERSION").write_text("0.0.1", encoding="utf-8")
            self.assertIn("re-run /lore:init",
                          verify_refs.version_warning(proj))


class TestConfigWarning(unittest.TestCase):
    def _warn(self, payload):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        Path(td.name, ".lore.json").write_text(payload, encoding="utf-8")
        return verify_refs.config_warning(Path(td.name))

    def test_typo_gets_a_suggestion(self):
        warn = self._warn('{"maxRecal": 3}')
        self.assertIn("unknown key 'maxRecal'", warn)
        self.assertIn("did you mean 'maxRecall'", warn)

    def test_invalid_value_reported(self):
        self.assertIn("'maxRecall' has an invalid value",
                      self._warn('{"maxRecall": "5"}'))

    def test_silent_when_clean(self):
        self.assertIsNone(self._warn('{"maxRecall": 5}'))
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(verify_refs.config_warning(Path(td)))


class TestIndex(unittest.TestCase):
    def test_writes_grouped_index(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            store = proj / "learnings"
            write_entry(store, "ci/cache.md", title="Cache key")
            write_entry(store, "ci/old.md", title="Old way",
                        status="superseded")
            write_entry(store, "api/pager.md", title="Pagination")
            entries = list(iter_entries(store))
            with contextlib.redirect_stdout(io.StringIO()):
                rc = verify_refs.cmd_index(entries, proj, dict(DEFAULTS), store)
            text = (store / "README.md").read_text(encoding="utf-8")
            # the regenerated README must not become an entry itself
            self.assertEqual(len(list(iter_entries(store))), 3)
        self.assertEqual(rc, 0)
        self.assertIn("## Index (3 entries)", text)
        self.assertIn("### ci (2)", text)
        self.assertIn("[Cache key](ci/cache.md)", text)
        self.assertIn("[Old way](ci/old.md) - **[SUPERSEDED]**", text)


class TestStatsThreshold(unittest.TestCase):
    def test_unverified_count_follows_staleAfterMonths(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            store = proj / "learnings"
            old = (date.today() - timedelta(days=100)).isoformat()
            write_entry(store, "a/e.md", title="E", date=old)
            entries = list(iter_entries(store))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                verify_refs.cmd_stats(entries, proj, dict(DEFAULTS), store)
            self.assertIn("not verified in >6mo: 0", out.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                verify_refs.cmd_stats(entries, proj,
                                      dict(DEFAULTS, staleAfterMonths=2), store)
            self.assertIn("not verified in >2mo: 1", out.getvalue())


class TestRecallActivity(unittest.TestCase):
    def test_counts_and_never_surfaced(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            store = proj / "learnings"
            a = write_entry(store, "x/a.md", title="A")
            write_entry(store, "x/b.md", title="B")
            (proj / ".git").mkdir()
            rel = a.relative_to(proj).as_posix()
            (proj / ".git" / "lore-recall.log").write_text(
                f"2026-07-17\tprompt\t{rel}\n2026-07-17\tedit\t{rel}\n",
                encoding="utf-8")
            entries = list(iter_entries(store))
            top, never = verify_refs._recall_activity(entries, proj)
        self.assertEqual(top[0], (2, "learnings/x/a.md"))
        self.assertEqual(never, 1)

    def test_none_without_log(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(verify_refs._recall_activity([], Path(td)))


@unittest.skipUnless(GIT, "git not available")
class TestGitDrift(unittest.TestCase):
    def _git(self, proj, *args):
        subprocess.run(
            [GIT, "-C", str(proj), "-c", "user.email=t@t", "-c",
             "user.name=t", *args],
            check=True, capture_output=True)

    def test_drift_candidate_detected(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            self._git(proj, "init", "-q")
            (proj / "src").mkdir()
            (proj / "src" / "app.py").write_text("v1", encoding="utf-8")
            self._git(proj, "add", ".")
            self._git(proj, "commit", "-q", "-m", "c1")

            store = proj / "learnings"
            write_entry(store, "a/drifted.md", title="Drifted",
                        files=("src/app.py",), date="2000-01-01")
            write_entry(store, "a/dirref.md", title="DirRef",
                        files=("src/",), date="2000-01-01")
            write_entry(store, "a/fresh.md", title="Fresh",
                        files=("src/app.py",), date="2000-01-01",
                        verified="2999-01-01")
            entries = list(iter_entries(store))
            cands = verify_refs._drift_candidates(entries, proj, set())
        titles = sorted(c[1]["title"] for c in cands)
        self.assertEqual(titles, ["DirRef", "Drifted"])

    def test_last_changes_maps_ref_forms(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            self._git(proj, "init", "-q")
            (proj / "lib").mkdir()
            (proj / "lib" / "x.py").write_text("x", encoding="utf-8")
            self._git(proj, "add", ".")
            self._git(proj, "commit", "-q", "-m", "c1")
            got = verify_refs._git_last_changes(
                proj, ["lib/x.py", "lib/", "lib/*.py"])
        self.assertEqual(set(got), {"lib/x.py", "lib/", "lib/*.py"})
        self.assertTrue(all(d is not None for d in got.values()))


if __name__ == "__main__":
    unittest.main()
