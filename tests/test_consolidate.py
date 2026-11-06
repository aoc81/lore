"""Tests for the consolidated layer: indexing, recall preference, consolidate.py.

The property under test throughout is the one that makes consolidation safe to
merge: distilling entries into a topic skill must never make a learning
unreachable. Several of these tests encode exactly the failure mode that
motivated the feature -- summarize, delete the entries, lose recall.
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import _util  # noqa: F401
import consolidate
import recall
from _common import (DEFAULTS, body_words, consolidated_paths,
                     iter_consolidated, iter_entries, resolve_consolidated)
from _util import write_entry, write_skill

CFG = dict(DEFAULTS, consolidatedDirs=[".claude/skills/lore-*"])


class LayerCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.project = Path(self._td.name)
        self.store = self.project / "learnings"
        self.store.mkdir()

    def stores(self):
        return [(self.store, False)]

    def consolidated(self, cfg=CFG):
        return list(iter_consolidated(self.project, cfg))

    def rels(self, matches):
        return [recall.rel_path(self.project, e["path"]) for _s, e in matches]

    def rank(self, text, cfg=CFG):
        return self.rels(recall.rank_matches(
            text, self.stores(), cfg, self.consolidated(cfg), self.project))

    def by_file(self, target, cfg=CFG):
        return self.rels(recall.match_by_file(
            [target], self.stores(), cfg, self.consolidated(cfg), self.project))

    def run_cli(self, argv):
        """Run consolidate.py's main() in-process; return (rc, stdout)."""
        out = io.StringIO()
        with mock_cwd(self.project), contextlib.redirect_stdout(out):
            import sys
            old = sys.argv
            sys.argv = ["consolidate.py"] + argv
            try:
                rc = consolidate.main()
            finally:
                sys.argv = old
        return rc, out.getvalue()


@contextlib.contextmanager
def mock_cwd(path):
    """Point find_project_dir() at `path` for both modules under test."""
    import os
    old = os.environ.get("CLAUDE_PROJECT_DIR")
    os.environ["CLAUDE_PROJECT_DIR"] = str(path)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = old


class TestIndexing(LayerCase):
    def test_off_by_default(self):
        write_skill(self.project, "lore-ci", description="CI invariants")
        self.assertEqual(consolidated_paths(self.project, dict(DEFAULTS)), [])
        self.assertEqual(list(iter_consolidated(self.project, dict(DEFAULTS))),
                         [])

    def test_glob_resolves_skill_dirs(self):
        write_skill(self.project, "lore-ci", description="CI")
        write_skill(self.project, "lore-auth", description="Auth")
        write_skill(self.project, "other", description="Not ours")
        names = sorted(c["title"] for c in self.consolidated())
        self.assertEqual(names, ["lore-auth", "lore-ci"])

    def test_glob_may_name_the_markdown_file(self):
        write_skill(self.project, "lore-ci", description="CI")
        cfg = dict(DEFAULTS,
                   consolidatedDirs=[".claude/skills/lore-ci/SKILL.md"])
        self.assertEqual([c["title"] for c in self.consolidated(cfg)],
                         ["lore-ci"])

    def test_unresolvable_glob_is_ignored(self):
        cfg = dict(DEFAULTS, consolidatedDirs=["", "does/not/exist/*"])
        self.assertEqual(self.consolidated(cfg), [])

    def test_name_and_description_are_the_match_surface(self):
        write_skill(self.project, "lore-elasticsearch",
                    description="Index lifecycle and shard sizing.")
        c = self.consolidated()[0]
        self.assertEqual(c["title"], "lore-elasticsearch")
        self.assertIn("elasticsearch", c["tags"])
        self.assertIn("shard", c["match_text"])
        self.assertTrue(c["consolidated"])

    def test_tags_and_files_are_read_when_present(self):
        write_skill(self.project, "lore-ci", description="CI",
                    tags=("cache", "lockfile"), files=("ci.yml", "scripts/"))
        c = self.consolidated()[0]
        self.assertEqual(c["files"], ["ci.yml", "scripts/"])
        for t in ("cache", "lockfile", "ci"):
            self.assertIn(t, c["tags"])

    def test_family_prefix_is_not_a_match_token(self):
        # 47 skills all named lore-*: a prompt saying "lore" must not inject
        # five of them at random, so the shared token discriminates nothing.
        for n, d in (("lore-ci", "Required checks and pipeline traps."),
                     ("lore-auth", "Token refresh and session rules."),
                     ("lore-frontend", "Build and state rules.")):
            write_skill(self.project, n, description=d)
        self.assertEqual(self.rank("the lore store is odd"), [])
        self.assertEqual(self.rank("frontend problem"),
                         [".claude/skills/lore-frontend/SKILL.md"])

    def test_single_skill_keeps_every_token(self):
        write_skill(self.project, "lore-ci", description="CI")
        c = self.consolidated()[0]
        self.assertIn("lore", c["tags"])


class TestRecallPreference(LayerCase):
    def setUp(self):
        super().setUp()
        write_entry(self.store, "ci/cache.md",
                    title="Cache key must include the lockfile hash",
                    tags=("ci", "cache", "lockfile"),
                    files=(".github/workflows/ci.yml",),
                    consolidated_into="lore-ci")
        write_entry(self.store, "ci/runner.md",
                    title="Runner disk fills during cache restore",
                    tags=("ci", "runner", "disk"),
                    files=("scripts/restore.sh",))
        write_skill(self.project, "lore-ci",
                    description="CI invariants: cache keys and required checks.",
                    tags=("ci", "cache", "lockfile"),
                    files=(".github/workflows/ci.yml",))

    def test_skill_surfaces_and_is_flagged(self):
        # The flag has to be there, so a summary is never read as the entry's
        # full account. Other flags (freshness) may sit beside it.
        (self.project / ".github" / "workflows").mkdir(parents=True)
        (self.project / ".github" / "workflows" / "ci.yml").touch()
        out = recall._format_entry(self.consolidated()[0], False,
                                   self.project, CFG)
        self.assertIn("[consolidated]", out)

    def test_skill_replaces_the_entry_it_absorbed(self):
        got = self.rank("cache key lockfile hash")
        self.assertIn(".claude/skills/lore-ci/SKILL.md", got)
        self.assertNotIn("learnings/ci/cache.md", got)

    def test_unabsorbed_entry_still_surfaces(self):
        self.assertIn("learnings/ci/runner.md", self.rank("runner disk full"))

    def test_absorbed_entry_survives_when_the_skill_does_not_match(self):
        # The stamp alone must not hide an entry: with no matching skill in the
        # result set there is nothing to prefer, so the entry is all there is.
        write_entry(self.store, "ci/orphan.md",
                    title="Pagination cursor is opaque",
                    tags=("pagination", "cursor"),
                    consolidated_into="lore-nonexistent")
        self.assertEqual(self.rank("pagination cursor"),
                         ["learnings/ci/orphan.md"])

    def test_skill_inherits_the_rank_of_what_it_absorbed(self):
        # An entry scoring higher than the skill must not drag the skill down
        # the list when it takes that entry's place.
        write_entry(self.store, "ci/loud.md", title="Widget telemetry batching",
                    tags=("widget", "telemetry", "batching"))
        write_entry(self.store, "ci/quiet.md",
                    title="Widget telemetry batching window and retries",
                    tags=("widget", "telemetry", "batching", "retries"),
                    consolidated_into="lore-widget")
        write_skill(self.project, "lore-widget", description="Widget rules.",
                    tags=("widget",))
        got = self.rank("widget telemetry batching retries window")
        self.assertEqual(got[0], ".claude/skills/lore-widget/SKILL.md")

    def test_edit_recall_prefers_the_skill_on_a_shared_path(self):
        self.assertEqual(self.by_file(".github/workflows/ci.yml"),
                         [".claude/skills/lore-ci/SKILL.md"])

    def test_edit_recall_keeps_the_entry_on_a_path_only_it_knows(self):
        # The safety property: a skill's frontmatter cannot list every path its
        # absorbed entries covered, and the entry is the only thing that knows
        # about this one.
        write_entry(self.store, "ci/deploy.md", title="Deploy needs a tag push",
                    tags=("deploy",), files=("scripts/deploy.sh",),
                    consolidated_into="lore-ci")
        self.assertEqual(self.by_file("scripts/deploy.sh"),
                         ["learnings/ci/deploy.md"])

    def test_skill_without_files_is_absent_from_edit_recall(self):
        write_skill(self.project, "lore-auth", description="Auth rules.")
        write_entry(self.store, "auth/jwt.md", title="JWT refresh quirk",
                    tags=("auth",), files=("src/auth/session.py",),
                    consolidated_into="lore-auth")
        self.assertEqual(self.by_file("src/auth/session.py"),
                         ["learnings/auth/jwt.md"])

    def test_stamp_resolves_by_dir_name_and_path(self):
        for ref in ("lore-ci", ".claude/skills/lore-ci",
                    ".claude/skills/lore-ci/SKILL.md"):
            write_entry(self.store, "ci/cache.md",
                        title="Cache key must include the lockfile hash",
                        tags=("ci", "cache", "lockfile"),
                        consolidated_into=ref)
            entry = next(e for e in iter_entries(self.store)
                         if e["path"].name == "cache.md")
            self.assertIsNotNone(
                resolve_consolidated(self.project, entry, self.consolidated()),
                f"{ref} should resolve")


class TestBackwardCompatibility(LayerCase):
    """A project with no `consolidatedDirs` must behave exactly as in 0.5.1."""

    def setUp(self):
        super().setUp()
        write_entry(self.store, "ci/cache.md", title="Cache key and lockfile",
                    tags=("ci", "cache", "lockfile"),
                    files=(".github/workflows/ci.yml",),
                    consolidated_into="lore-ci")
        write_skill(self.project, "lore-ci", description="CI invariants.",
                    tags=("ci", "cache"), files=(".github/workflows/ci.yml",))

    def test_prompt_recall_unchanged(self):
        self.assertEqual(self.rank("cache lockfile problem", dict(DEFAULTS)),
                         ["learnings/ci/cache.md"])

    def test_edit_recall_unchanged(self):
        self.assertEqual(
            self.by_file(".github/workflows/ci.yml", dict(DEFAULTS)),
            ["learnings/ci/cache.md"])

    def test_default_signature_takes_no_consolidated_layer(self):
        # The 0.5.1 call shape (no consolidated arg at all) still works.
        m = recall.rank_matches("cache lockfile", self.store, dict(DEFAULTS))
        self.assertEqual(len(m), 1)


class TestJournalIsNotAnEntry(LayerCase):
    def test_weekly_log_never_counts_as_a_learning(self):
        write_entry(self.store, "ci/a.md", title="Real entry", tags=("ci",))
        (self.store / "WEEKLY_LOG.md").write_text(
            "# Consolidation log\n\n## 2026-11-06\n\nDid a pass.\n",
            encoding="utf-8")
        titles = [e["title"] for e in iter_entries(self.store)]
        self.assertEqual(titles, ["Real entry"])


class TestBodyWords(LayerCase):
    def test_counts_body_only(self):
        p = write_skill(self.project, "lore-ci", description="A description "
                        "that is deliberately quite long and wordy indeed",
                        body="one two three four five")
        self.assertEqual(body_words(p), 5)


class TestSetFrontmatterKey(LayerCase):
    def test_adds_key_and_preserves_everything_else(self):
        p = write_entry(self.store, "ci/a.md", title="Title", tags=("ci", "x"),
                        files=("a.py",), body="## Problem\n\nText.")
        before = p.read_text(encoding="utf-8")
        self.assertEqual(
            consolidate.set_frontmatter_key(p, "consolidated_into", "lore-ci"),
            "set")
        after = p.read_text(encoding="utf-8")
        self.assertIn("consolidated_into: lore-ci", after)
        for line in before.splitlines():
            self.assertIn(line, after.splitlines(),
                          "no pre-existing line may be rewritten")

    def test_replaces_an_existing_value(self):
        p = write_entry(self.store, "ci/a.md", title="T", tags=("ci",),
                        consolidated_into="lore-old")
        consolidate.set_frontmatter_key(p, "consolidated_into", "lore-new")
        text = p.read_text(encoding="utf-8")
        self.assertIn("consolidated_into: lore-new", text)
        self.assertNotIn("lore-old", text)

    def test_idempotent(self):
        p = write_entry(self.store, "ci/a.md", title="T", tags=("ci",),
                        consolidated_into="lore-ci")
        self.assertEqual(
            consolidate.set_frontmatter_key(p, "consolidated_into", "lore-ci"),
            "unchanged")

    def test_refuses_a_file_without_frontmatter(self):
        p = self.store / "ci" / "plain.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("no frontmatter here\n", encoding="utf-8")
        self.assertTrue(consolidate.set_frontmatter_key(
            p, "consolidated_into", "lore-ci").startswith("skip:"))
        self.assertEqual(p.read_text(encoding="utf-8"), "no frontmatter here\n")


class TestParityCheck(LayerCase):
    def setUp(self):
        super().setUp()
        (self.project / ".lore.json").write_text(
            json.dumps({"consolidatedDirs": [".claude/skills/lore-*"]}),
            encoding="utf-8")
        write_entry(self.store, "ci/cache.md",
                    title="Cache key must include the lockfile hash",
                    tags=("ci", "cache", "lockfile"),
                    files=(".github/workflows/ci.yml",))
        write_entry(self.store, "auth/jwt.md",
                    title="JWT refresh takes an options object",
                    tags=("auth", "jwt", "refresh"),
                    files=("src/auth/session.py",))
        self.snap = self.project / "before.json"

    def snapshot(self):
        rc, out = self.run_cli(["--snapshot", str(self.snap)])
        self.assertEqual(rc, 0, out)
        return out

    def test_snapshot_records_one_query_per_entry(self):
        self.snapshot()
        data = json.loads(self.snap.read_text(encoding="utf-8"))
        self.assertEqual(len(data["queries"]), 2)
        self.assertTrue(all(v for v in data["queries"].values()),
                        "every entry must answer its own query")

    def test_absorbed_entries_are_not_a_regression(self):
        self.snapshot()
        write_skill(self.project, "lore-ci",
                    description="CI invariants: cache keys and lockfiles.",
                    tags=("ci", "cache", "lockfile"),
                    files=(".github/workflows/ci.yml",))
        rc, _out = self.run_cli(
            ["--mark", "lore-ci", str(self.store / "ci" / "cache.md")])
        self.assertEqual(rc, 0)
        rc, out = self.run_cli(["--compare", str(self.snap)])
        self.assertEqual(rc, 0, out)
        self.assertIn("REGRESSED (no longer reachable):  0", out)

    def test_deleting_an_entry_with_no_skill_is_a_regression(self):
        # Exactly the "dream = forget" failure: summarize nothing, delete the
        # notes, and the lesson is reachable by nothing.
        self.snapshot()
        (self.store / "auth" / "jwt.md").unlink()
        rc, out = self.run_cli(["--compare", str(self.snap)])
        self.assertEqual(rc, 1)
        self.assertIn("REGRESSED (no longer reachable):  1", out)
        self.assertIn("learnings/auth/jwt.md", out)

    def test_deleting_an_entry_whose_skill_answers_is_excused(self):
        self.snapshot()
        write_skill(self.project, "lore-auth",
                    description="Auth: JWT refresh options and CORS hosts.",
                    tags=("auth", "jwt", "refresh"),
                    files=("src/auth/session.py",))
        (self.store / "auth" / "jwt.md").unlink()
        rc, out = self.run_cli(["--compare", str(self.snap)])
        self.assertEqual(rc, 0, out)

    def test_marking_without_a_real_skill_is_still_a_regression(self):
        # A stamp is not consolidation: if the skill does not exist, deleting
        # the entry loses the lesson and --compare must say so.
        self.snapshot()
        self.run_cli(["--mark", "lore-auth",
                      str(self.store / "auth" / "jwt.md")])
        (self.store / "auth" / "jwt.md").unlink()
        rc, _out = self.run_cli(["--compare", str(self.snap)])
        self.assertEqual(rc, 1)

    def test_compare_rejects_a_file_that_is_not_a_snapshot(self):
        bad = self.project / "bad.json"
        bad.write_text('{"nope": 1}', encoding="utf-8")
        rc, out = self.run_cli(["--compare", str(bad)])
        self.assertEqual(rc, 2)
        self.assertIn("not a lore recall snapshot", out)

    def test_extra_queries_file_is_merged(self):
        q = self.project / "q.txt"
        q.write_text("# comment\nhow do i rotate the deploy token\n\n",
                     encoding="utf-8")
        rc, out = self.run_cli(["--snapshot", str(self.snap),
                                "--queries", str(q)])
        self.assertEqual(rc, 0, out)
        data = json.loads(self.snap.read_text(encoding="utf-8"))
        self.assertIn("how do i rotate the deploy token", data["queries"])
        self.assertEqual(len(data["queries"]), 3)


class TestPlanAndPrune(LayerCase):
    def setUp(self):
        super().setUp()
        (self.project / ".lore.json").write_text(
            json.dumps({"consolidatedDirs": [".claude/skills/lore-*"]}),
            encoding="utf-8")

    def test_plan_groups_unconsolidated_entries_by_category(self):
        write_entry(self.store, "ci/a.md", title="A", tags=("ci",))
        write_entry(self.store, "ci/b.md", title="B", tags=("ci",))
        write_entry(self.store, "auth/c.md", title="C", tags=("auth",))
        rc, out = self.run_cli([])
        self.assertEqual(rc, 0)
        self.assertIn("ci (2 entries)", out)
        self.assertIn("auth (1 entries)", out)
        self.assertIn("not consolidated: 3", out)

    def test_plan_warns_when_consolidated_dirs_is_unset(self):
        (self.project / ".lore.json").unlink()
        write_entry(self.store, "ci/a.md", title="A", tags=("ci",))
        _rc, out = self.run_cli([])
        self.assertIn("consolidatedDirs", out)
        self.assertIn("recall will NOT see", out)

    def test_plan_flags_a_skill_over_the_word_limit(self):
        write_skill(self.project, "lore-ci", description="CI",
                    body=" ".join(["word"] * 600))
        _rc, out = self.run_cli([])
        self.assertIn("over limit", out)

    def test_plan_flags_a_skill_with_no_file_refs(self):
        write_skill(self.project, "lore-ci", description="CI")
        _rc, out = self.run_cli([])
        self.assertIn("no `files:` frontmatter", out)
        self.assertIn("invisible to edit-time recall", out)

    def test_prune_proposes_only_stale_and_consolidated(self):
        write_skill(self.project, "lore-ci", description="CI", tags=("ci",))
        write_entry(self.store, "ci/live.md", title="Live", tags=("ci",),
                    consolidated_into="lore-ci")
        write_entry(self.store, "ci/stale-unmarked.md", title="Stale alone",
                    tags=("ci",), status="superseded")
        write_entry(self.store, "ci/both.md", title="Stale and absorbed",
                    tags=("ci",), status="obsolete",
                    consolidated_into="lore-ci")
        rc, out = self.run_cli(["--prune"])
        self.assertEqual(rc, 0)
        self.assertIn("learnings/ci/both.md", out)
        self.assertNotIn("learnings/ci/live.md", out)
        self.assertNotIn("stale-unmarked.md", out)
        self.assertIn("git rm", out)

    def test_prune_never_deletes(self):
        write_skill(self.project, "lore-ci", description="CI", tags=("ci",))
        p = write_entry(self.store, "ci/both.md", title="Stale", tags=("ci",),
                        status="superseded", consolidated_into="lore-ci")
        self.run_cli(["--prune"])
        self.assertTrue(p.is_file(), "--prune must only propose")

    def test_prune_explains_why_current_entries_are_kept(self):
        write_skill(self.project, "lore-ci", description="CI", tags=("ci",))
        write_entry(self.store, "ci/live.md", title="Live", tags=("ci",),
                    consolidated_into="lore-ci")
        _rc, out = self.run_cli(["--prune"])
        self.assertIn("none.", out)
        self.assertIn("edit-time recall", out)


class TestMarkCommand(LayerCase):
    def setUp(self):
        super().setUp()
        (self.project / ".lore.json").write_text(
            json.dumps({"consolidatedDirs": [".claude/skills/lore-*"]}),
            encoding="utf-8")

    def test_mark_keeps_tags_and_files(self):
        write_skill(self.project, "lore-ci", description="CI", tags=("ci",))
        p = write_entry(self.store, "ci/a.md", title="A", tags=("ci", "cache"),
                        files=("ci.yml",))
        rc, _out = self.run_cli(["--mark", "lore-ci", str(p)])
        self.assertEqual(rc, 0)
        entry = next(iter(iter_entries(self.store)))
        self.assertEqual(entry["tags"], ["ci", "cache"])
        self.assertEqual(entry["files"], ["ci.yml"])
        self.assertEqual(entry["consolidated_into"], "lore-ci")

    def test_mark_warns_about_an_unknown_skill(self):
        p = write_entry(self.store, "ci/a.md", title="A", tags=("ci",))
        _rc, out = self.run_cli(["--mark", "lore-nope", str(p)])
        self.assertIn("no consolidated skill named 'lore-nope'", out)

    def test_mark_needs_a_skill_and_an_entry(self):
        rc, out = self.run_cli(["--mark", "lore-ci"])
        self.assertEqual(rc, 2)
        self.assertIn("needs a skill name", out)


class TestJournalCommand(LayerCase):
    def setUp(self):
        super().setUp()
        (self.project / ".lore.json").write_text(
            json.dumps({"consolidatedDirs": [".claude/skills/lore-*"]}),
            encoding="utf-8")
        write_skill(self.project, "lore-ci", description="CI", tags=("ci",))
        write_entry(self.store, "ci/a.md", title="A", tags=("ci",),
                    consolidated_into="lore-ci")
        write_entry(self.store, "ci/b.md", title="B", tags=("ci",))

    def log(self):
        return (self.store / "WEEKLY_LOG.md").read_text(encoding="utf-8")

    def test_creates_the_log_with_counts(self):
        rc, _out = self.run_cli(["--log", "First pass over ci."])
        self.assertEqual(rc, 0)
        text = self.log()
        self.assertIn("First pass over ci.", text)
        self.assertIn("2 current - 1 consolidated, 1 not", text)
        self.assertIn("deleted by this pass: 0", text)

    def test_appends_newest_first(self):
        self.run_cli(["--log", "Older pass."])
        self.run_cli(["--log", "Newer pass."])
        text = self.log()
        self.assertLess(text.index("Newer pass."), text.index("Older pass."))
        self.assertEqual(text.count("# Consolidation log"), 1)


if __name__ == "__main__":
    unittest.main()
