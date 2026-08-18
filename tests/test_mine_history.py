"""Tests for mine_history.py: log parsing, scoring signals, churn, report."""
import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _util  # noqa: F401  (puts plugin/scripts on sys.path)
from _common import DEFAULTS
from _util import write_entry
import mine_history as mine

GIT = shutil.which("git")

CFG = dict(DEFAULTS)


def raw_log(*commits):
    """Build git-log output in the format `git_log` produces.

    commits: (sha, date, subject, body, [files]) tuples.
    """
    out = []
    for sha, date, subject, body, files in commits:
        out.append(mine._REC + mine._FLD.join([sha, date, subject, body, ""])
                   + "\n".join(files) + "\n")
    return "".join(out)


class TestParseLog(unittest.TestCase):
    def test_fields_and_files(self):
        raw = raw_log(("abc123", "2026-01-02", "Fix the thing",
                       "Root cause: the cache key.\n\nSecond para.",
                       ["src/a.py", "src/b.py"]))
        got = mine.parse_log(raw)
        self.assertEqual(len(got), 1)
        c = got[0]
        self.assertEqual(c["sha"], "abc123")
        self.assertEqual(c["date"], "2026-01-02")
        self.assertEqual(c["subject"], "Fix the thing")
        self.assertIn("Root cause", c["body"])
        self.assertEqual(c["files"], ["src/a.py", "src/b.py"])

    def test_body_with_blank_lines_does_not_split_the_record(self):
        raw = raw_log(("a", "2026-01-01", "S", "one\n\ntwo\n\nthree", ["f.py"]))
        got = mine.parse_log(raw)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["files"], ["f.py"])

    def test_empty_body_and_no_files(self):
        got = mine.parse_log(raw_log(("a", "2026-01-01", "S", "", [])))
        self.assertEqual(got[0]["body"], "")
        self.assertEqual(got[0]["files"], [])

    def test_quoted_paths_are_unquoted(self):
        got = mine.parse_log(raw_log(("a", "2026-01-01", "S", "", ['"sp ace.py"'])))
        self.assertEqual(got[0]["files"], ["sp ace.py"])

    def test_garbage_is_ignored(self):
        self.assertEqual(mine.parse_log(""), [])
        self.assertEqual(mine.parse_log("no separators here"), [])


def commit(subject, body="", files=("src/a.py",), sha="abc1234", date="2026-01-01"):
    return {"sha": sha, "date": date, "subject": subject, "body": body,
            "files": list(files)}


class TestScoreCommit(unittest.TestCase):
    def test_revert_scores_highest_signal(self):
        score, reasons = mine.score_commit(commit("Revert \"use the new cache\""), {})
        self.assertIn("revert", reasons)
        self.assertGreaterEqual(score, 3)

    def test_fix_with_explanation_qualifies(self):
        score, reasons = mine.score_commit(
            commit("Fix flaky auth test",
                   "Root cause: the token clock skew. " + "x" * 200), {})
        self.assertIn("fix/bug", reasons)
        self.assertIn("explains why", reasons)
        self.assertGreaterEqual(score, mine.MIN_SCORE)

    def test_spanish_signals_are_recognized(self):
        score, reasons = mine.score_commit(
            commit("Corrige el fallo de sesion",
                   "Resulta que la cookie era de host, por eso fallaba."), {})
        self.assertIn("fix/bug", reasons)
        self.assertIn("explains why", reasons)
        self.assertGreaterEqual(score, mine.MIN_SCORE)

    def test_noise_subjects_score_zero(self):
        for subject in ("wip", "Merge branch 'main'", "bump deps to 2.0",
                        "chore: lint", "typo in README", "v1.2.3",
                        "release 4.0", "reformat everything",
                        "whitespace cleanup", "Initial commit"):
            score, _ = mine.score_commit(
                commit(subject, "Root cause: fix bug " + "x" * 300), {})
            self.assertEqual(score, 0, subject)

    def test_sweeping_commit_is_skipped(self):
        many = [f"src/f{i}.py" for i in range(mine._MAX_FILES + 1)]
        score, _ = mine.score_commit(
            commit("Fix everything", "root cause " + "x" * 300, many), {})
        self.assertEqual(score, 0)

    def test_store_only_commit_is_skipped(self):
        # Capturing a learning about capturing learnings is a loop, not a lesson.
        score, _ = mine.score_commit(
            commit("Fix the learning about the cache",
                   "root cause: it was wrong " + "x" * 300,
                   ["learnings/ci/a.md", "learnings/README.md"]),
            {}, store_rels=["learnings/"])
        self.assertEqual(score, 0)
        # ... but a commit that also touches real code still counts
        score, _ = mine.score_commit(
            commit("Fix the cache key", "root cause " + "x" * 300,
                   ["learnings/ci/a.md", "src/cache.py"]),
            {}, store_rels=["learnings/"])
        self.assertGreaterEqual(score, mine.MIN_SCORE)

    def test_hot_file_adds_a_point(self):
        churn = {"src/a.py": mine._HOT_MIN}
        cold, _ = mine.score_commit(commit("Fix the login redirect"), {})
        hot, reasons = mine.score_commit(commit("Fix the login redirect"), churn)
        self.assertEqual(hot, cold + 1)
        self.assertTrue(any("hot file" in r for r in reasons))

    def test_bare_subject_is_not_a_candidate(self):
        score, _ = mine.score_commit(commit("Add a helper method", ""), {})
        self.assertLess(score, mine.MIN_SCORE)

    def test_empty_subject_scores_zero(self):
        self.assertEqual(mine.score_commit(commit(""), {})[0], 0)


class TestChurn(unittest.TestCase):
    def test_counts_per_file(self):
        commits = [commit("a", files=("x.py", "y.py")),
                   commit("b", files=("x.py",))]
        self.assertEqual(mine.churn_counts(commits), {"x.py": 2, "y.py": 1})


class TestCandidates(unittest.TestCase):
    def _project(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return Path(td.name)

    def test_ranked_best_first_and_filtered(self):
        proj = self._project()
        # Distinct files per commit: nothing here is a churn hot spot, so each
        # score comes purely from the message.
        commits = [
            commit("Fix nothing much", "", files=("src/1.py",), sha="c1"),
            commit("Revert \"the cache change\"",
                   "Root cause: key collision. " + "x" * 250,
                   files=("src/2.py",), sha="c2"),
            commit("Fix the login redirect", "because the path was absolute",
                   files=("src/3.py",), sha="c3"),
            commit("bump version", "root cause " + "x" * 300,
                   files=("src/4.py",), sha="c4"),  # noise subject
        ]
        got = mine.candidates(commits, proj, CFG)
        shas = [c["sha"] for _s, _r, c in got]
        self.assertEqual(shas[0], "c2")          # strongest signals first
        self.assertIn("c3", shas)
        self.assertNotIn("c1", shas)
        self.assertNotIn("c4", shas)

    def test_equal_scores_break_by_recency(self):
        proj = self._project()
        old = commit("Fix the parser", "because of X", sha="old",
                     date="2020-01-01")
        new = commit("Fix the parser", "because of X", sha="new",
                     date="2026-01-01")
        got = mine.candidates([old, new], proj, CFG)
        self.assertEqual([c["sha"] for _s, _r, c in got], ["new", "old"])


class TestReport(unittest.TestCase):
    def _report(self, commits, cfg=None, store_entries=()):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        proj = Path(td.name)
        for rel, title, tags in store_entries:
            write_entry(proj / "learnings", rel, title=title, tags=tags)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mine.report(commits, proj, cfg or CFG)
        return out.getvalue()

    def test_lists_candidates_with_signals_and_command(self):
        text = self._report([commit(
            "Fix the cache key collision",
            "Root cause: the key omitted the lockfile hash. " + "x" * 250,
            sha="deadbeef12")])
        self.assertIn("Fix the cache key collision", text)
        self.assertIn("signals:", text)
        self.assertIn("git show deadbeef1", text)

    def test_overlap_with_existing_entry_is_flagged(self):
        text = self._report(
            [commit("Fix the cache key collision",
                    "Root cause: lockfile hash missing. " + "x" * 250)],
            store_entries=[("ci/cache.md", "Cache key must include the lockfile",
                            ("cache", "lockfile"))])
        self.assertIn("overlap:", text)
        self.assertIn("learnings/ci/cache.md", text)
        self.assertIn("UPDATE", text)

    def test_no_candidates_says_so(self):
        text = self._report([commit("wip", "")])
        self.assertIn("none.", text)
        self.assertIn("Capture from live work", text)

    def test_hot_spots_section(self):
        commits = [commit(f"Fix bug {i}", "because reasons",
                          files=("src/hot.py",), sha=f"s{i}")
                   for i in range(mine._HOT_MIN)]
        text = self._report(commits)
        self.assertIn("churn hot spots", text)
        self.assertIn("src/hot.py", text)


@unittest.skipUnless(GIT, "git not available")
class TestGitLog(unittest.TestCase):
    def _git(self, proj, *args):
        subprocess.run(
            [GIT, "-C", str(proj), "-c", "user.email=t@t", "-c",
             "user.name=t", *args], check=True, capture_output=True)

    def test_end_to_end_on_a_real_repo(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            self._git(proj, "init", "-q")
            (proj / "src").mkdir()
            (proj / "src" / "app.py").write_text("v1", encoding="utf-8")
            self._git(proj, "add", ".")
            self._git(proj, "commit", "-q", "-m",
                      "Fix the session cookie scope\n\n"
                      "Root cause: it was set host-only, so the subdomain "
                      "never saw it. Beware: the framework default is wrong.")
            (proj / "src" / "app.py").write_text("v2", encoding="utf-8")
            self._git(proj, "commit", "-qam", "wip")

            raw = mine.git_log(proj)
            self.assertIsNotNone(raw)
            commits = mine.parse_log(raw)
            self.assertEqual(len(commits), 2)
            self.assertEqual(commits[1]["files"], ["src/app.py"])
            cands = mine.candidates(commits, proj, CFG)
        self.assertEqual(len(cands), 1)  # the "wip" commit is filtered out
        self.assertIn("session cookie", cands[0][2]["subject"])

    def test_non_repo_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(mine.git_log(Path(td) / "not-a-repo"))


if __name__ == "__main__":
    unittest.main()
