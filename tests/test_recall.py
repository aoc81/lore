"""Tests for recall.py: scorer, thresholds, file matching, telemetry."""
import tempfile
import unittest
from pathlib import Path

import _util  # noqa: F401
from _common import DEFAULTS
from _util import write_entry
import recall


CFG = dict(DEFAULTS)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.project = Path(self._td.name)
        self.store = self.project / "learnings"
        self.store.mkdir()

    def titles(self, matches):
        return [e["title"] for _stale, e in matches]


class TestRankMatches(StoreCase):
    def test_exact_word_surfaces(self):
        write_entry(self.store, "ci/cache.md", title="Cache key includes lockfile",
                    tags=("cache", "lockfile"))
        m = recall.rank_matches("why is the cache stale?", self.store, CFG)
        self.assertEqual(self.titles(m), ["Cache key includes lockfile"])

    def test_no_substring_false_positive(self):
        # "auth" must NOT surface an entry that only contains "author".
        write_entry(self.store, "docs/authors.md", title="Author guidelines",
                    tags=("docs",))
        m = recall.rank_matches("fix the auth flow", self.store, CFG)
        self.assertEqual(m, [])

    def test_prefix_overlap_counts_but_needs_two(self):
        write_entry(self.store, "t/one.md", title="Testing conventions",
                    tags=("tests", "conventions"))
        # one prefix hit (test->testing) alone is below the threshold...
        self.assertEqual(
            recall.rank_matches("run the test", self.store, CFG), [])
        # ...but prefix + exact clears it
        m = recall.rank_matches("test conventions", self.store, CFG)
        self.assertEqual(len(m), 1)

    def test_short_word_matches_exact_tag_only(self):
        write_entry(self.store, "ci/pipeline.md", title="Pipeline quirk",
                    tags=("ci", "deploy"))
        write_entry(self.store, "misc/other.md", title="On a CI-like topic",
                    tags=("misc",))
        m = recall.rank_matches("the ci run is broken", self.store, CFG)
        self.assertEqual(self.titles(m), ["Pipeline quirk"])

    def test_stale_penalty_ranks_current_first(self):
        write_entry(self.store, "a/old.md", title="Pagination cursor rules",
                    tags=("pagination",), status="superseded")
        write_entry(self.store, "a/new.md", title="Pagination cursor rules v2",
                    tags=("pagination",))
        m = recall.rank_matches("pagination cursor bug", self.store, CFG)
        self.assertEqual(len(m), 2)
        self.assertFalse(m[0][0], "current entry should rank first")
        self.assertTrue(m[1][0])

    def test_max_recall_cap(self):
        for i in range(8):
            write_entry(self.store, f"x/e{i}.md", title=f"Widget thing {i}",
                        tags=("widget",))
        cfg = dict(CFG, maxRecall=3)
        m = recall.rank_matches("widget", self.store, cfg)
        self.assertEqual(len(m), 3)


class TestMatchByFile(StoreCase):
    def test_exact_dir_and_glob_refs(self):
        write_entry(self.store, "a/exact.md", title="Exact",
                    files=("src/auth/login.py",))
        write_entry(self.store, "a/dir.md", title="Dir",
                    files=("src/auth/",))
        write_entry(self.store, "a/glob.md", title="Glob",
                    files=("src/*.py",))
        write_entry(self.store, "a/other.md", title="Other",
                    files=("lib/z.py",))
        got = {e["title"] for _s, e in
               recall.match_by_file("src/auth/login.py", self.store)}
        self.assertEqual(got, {"Exact", "Dir", "Glob"})


class TestFreshnessFlags(StoreCase):
    def test_deleted_ref_and_age(self):
        p = write_entry(self.store, "a/e.md", title="E",
                        files=("gone.py",), date="2019-01-01")
        e = {"path": p, "title": "E", "tags": [], "files": ["gone.py"],
             "status": "current", "date": "2019-01-01", "verified": "",
             "category": "a"}
        flags = recall.freshness_flags(e, self.project)
        self.assertTrue(any("deleted file" in f for f in flags))
        self.assertTrue(any("written" in f for f in flags))


class TestLogRecall(StoreCase):
    def _entry(self):
        p = write_entry(self.store, "a/e.md", title="E")
        return {"path": p}

    def test_writes_only_with_git_dir(self):
        e = self._entry()
        recall.log_recall(self.project, "prompt", [e])
        self.assertFalse((self.project / ".git" / "lore-recall.log").exists())
        (self.project / ".git").mkdir()
        recall.log_recall(self.project, "prompt", [e])
        recall.log_recall(self.project, "edit", [e])
        lines = (self.project / ".git" / "lore-recall.log").read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("\tprompt\tlearnings/a/e.md", lines[0])
        self.assertIn("\tedit\tlearnings/a/e.md", lines[1])


if __name__ == "__main__":
    unittest.main()
