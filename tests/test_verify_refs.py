"""Tests for verify_refs.py: dates, dupes, config, index, git drift."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import _util  # noqa: F401
from _common import DEFAULTS, iter_entries
from _util import write_entry, write_skill
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


class TestFrontmatterValues(unittest.TestCase):
    """Values the hooks silently misread: a typo'd status, a non-ISO date."""

    def _check(self, cfg=None, **entry):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        proj = Path(td.name)
        write_entry(proj / "learnings", "x/e.md", title="E", **entry)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = verify_refs.cmd_check(list(iter_entries(proj / "learnings")),
                                       proj, cfg or dict(DEFAULTS), True)
        return rc, out.getvalue()

    def test_typoed_status_is_actionable(self):
        # Not a stale status, so recall serves the entry as LIVE guidance.
        rc, out = self._check(status="superseeded")
        self.assertEqual(rc, 1)
        self.assertIn("unknown status 'superseeded'", out)

    def test_unparseable_dates_are_actionable(self):
        rc, out = self._check(date="last week", verified="2026-02-30")
        self.assertEqual(rc, 1)
        self.assertIn("date 'last week'", out)
        self.assertIn("verified '2026-02-30'", out)

    def test_valid_values_are_clean(self):
        rc, out = self._check(date="2026-01-01", verified="2026-02-01")
        self.assertEqual(rc, 0)
        self.assertIn("OK", out)

    def test_configured_stale_status_is_valid(self):
        cfg = dict(DEFAULTS, staleStatuses=["archived"])
        self.assertEqual(self._check(cfg=cfg, status="archived")[0], 0)

    def test_stale_entries_are_not_value_checked(self):
        # Retired entries drive no live guidance and no freshness flags.
        self.assertEqual(self._check(status="superseded", date="someday")[0], 0)

    def test_unparseable_revisit_is_actionable(self):
        # A revisit date nobody can read is a reminder that never fires.
        rc, out = self._check(revisit="when the budget allows")
        self.assertEqual(rc, 1)
        self.assertIn("revisit 'when the budget allows'", out)


class TestRevisitReport(unittest.TestCase):
    """--report is the re-verify worklist /lore:sweep reads: due revisits too."""

    def _report(self, *specs):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        proj = Path(td.name)  # no .git: drift is skipped, revisits are not
        for rel, status, revisit in specs:
            write_entry(proj / "learnings", rel, title=rel, status=status,
                        revisit=revisit)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = verify_refs.cmd_report(
                list(iter_entries(proj / "learnings")), proj, dict(DEFAULTS))
        self.assertEqual(rc, 0)
        return out.getvalue()

    def test_due_live_entries_are_listed(self):
        out = self._report(("infra/no-backup.md", "current", "2020-01-01"),
                           ("infra/later.md", "current", "2999-01-01"),
                           ("infra/old.md", "superseded", "2020-01-01"))
        self.assertIn("Revisit due", out)
        self.assertIn("2020-01-01  learnings/infra/no-backup.md", out)
        self.assertNotIn("later.md", out)
        self.assertNotIn("old.md", out)

    def test_no_section_when_nothing_is_due(self):
        self.assertNotIn("Revisit due",
                         self._report(("a/b.md", "current", "2999-01-01")))


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

    def _regen(self, store, proj, cfg=None):
        entries = list(iter_entries(store))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            verify_refs.cmd_index(entries, proj, cfg or dict(DEFAULTS), store)
        return (store / "README.md").read_text(encoding="utf-8"), out.getvalue()

    def test_hand_written_text_above_the_index_survives(self):
        # `--index` used to rewrite the whole README from a hardcoded header,
        # silently destroying a human's own sections. It regenerates the INDEX.
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            store = proj / "learnings"
            write_entry(store, "ci/cache.md", title="Cache key")
            (store / "README.md").write_text(
                "# Our store\n\n## How we work\n\nWeekly dream pass, see "
                "WEEKLY_LOG.md.\n\n## Index (0 entries)\n\nold\n",
                encoding="utf-8")
            text, said = self._regen(store, proj)
        self.assertIn("## How we work", text)
        self.assertIn("Weekly dream pass, see WEEKLY_LOG.md.", text)
        self.assertIn("## Index (1 entries)", text)
        self.assertNotIn("old", text.split("## Index")[1])
        self.assertIn("kept", said)  # it has to SAY what it preserved

    def test_a_readme_without_an_index_heading_gets_the_default(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            store = proj / "learnings"
            write_entry(store, "ci/cache.md", title="Cache key")
            text, said = self._regen(store, proj)
        self.assertIn("One-fact-per-file record", text)
        self.assertIn("default preamble", said)

    def test_regenerating_twice_is_byte_identical(self):
        # The point of a documented sort: a regenerated index can be compared
        # with a committed one.
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            store = proj / "learnings"
            for i in range(4):
                write_entry(store, f"ci/e{i}.md", title="Same title")
            first, _ = self._regen(store, proj)
            second, _ = self._regen(store, proj)
            third, _ = self._regen(store, proj)
        self.assertEqual(first, second)
        self.assertEqual(second, third)

    def test_a_note_without_frontmatter_is_titled_by_its_heading(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            store = proj / "learnings"
            (store / "arch").mkdir(parents=True)
            (store / "arch" / "per-seat-metering.md").write_text(
                "# Per-seat metering is prepaid only via lots\n\nBody.\n",
                encoding="utf-8")
            text, _ = self._regen(store, proj)
        self.assertIn("[Per-seat metering is prepaid only via lots]"
                      "(arch/per-seat-metering.md)", text)

    def test_a_link_shaped_heading_falls_back_to_the_slug(self):
        # Some repos run a docs link-checker that reads `[..](..)` anywhere, so
        # copying such a heading into an index line invents a link to resolve.
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            store = proj / "learnings"
            (store / "arch").mkdir(parents=True)
            (store / "arch" / "linky.md").write_text(
                "# See [the doc](../other.md) for details\n\nBody.\n",
                encoding="utf-8")
            text, _ = self._regen(store, proj)
        self.assertIn("[linky](arch/linky.md)", text)
        self.assertNotIn("../other.md", text)


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

    def test_revisit_due_is_counted(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            store = proj / "learnings"
            write_entry(store, "a/due.md", title="Due", revisit="2020-01-01")
            write_entry(store, "a/later.md", title="Later",
                        revisit="2999-01-01")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                verify_refs.cmd_stats(list(iter_entries(store)), proj,
                                      dict(DEFAULTS), store)
        self.assertIn("revisit due (temporary decisions): 1", out.getvalue())


class TestRecallActivity(unittest.TestCase):
    def _activity(self, log_lines, extra_entries=()):
        """Build a store + recall log, return `_recall_activity`'s result."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        proj = Path(td.name)
        store = proj / "learnings"
        write_entry(store, "x/a.md", title="A")
        write_entry(store, "x/b.md", title="B")
        for rel in extra_entries:
            write_entry(store, rel, title=rel)
        (proj / ".git").mkdir()
        (proj / ".git" / "lore-recall.log").write_text(
            "".join(f"2026-07-17\t{kind}\t{path}\n" for kind, path in log_lines),
            encoding="utf-8")
        return verify_refs._recall_activity(list(iter_entries(store)), proj)

    def test_counts_and_never_surfaced(self):
        top, never, unread = self._activity([
            ("prompt", "learnings/x/a.md"), ("edit", "learnings/x/a.md")])
        self.assertEqual(top[0], (2, 0, "learnings/x/a.md"))
        self.assertEqual(never, 1)
        # No `read` event anywhere -> "never read" is unknowable, not True.
        self.assertIsNone(unread)

    def test_reads_are_counted_separately(self):
        top, _never, unread = self._activity([
            ("prompt", "learnings/x/a.md"), ("read", "learnings/x/a.md"),
            ("read", "learnings/x/a.md")])
        self.assertEqual(top[0], (1, 2, "learnings/x/a.md"))
        self.assertEqual(unread, [])  # a.md was read; b.md never surfaced

    def test_surfaced_but_never_read_is_reported(self):
        log = [("prompt", "learnings/x/a.md")] * verify_refs._UNREAD_MIN
        log += [("prompt", "learnings/x/b.md"), ("read", "learnings/x/b.md")]
        _top, _never, unread = self._activity(log)
        self.assertEqual(unread, [("learnings/x/a.md", verify_refs._UNREAD_MIN)])

    def test_below_threshold_is_not_reported(self):
        log = [("prompt", "learnings/x/a.md")] * (verify_refs._UNREAD_MIN - 1)
        log += [("read", "learnings/x/b.md")]
        _top, _never, unread = self._activity(log)
        self.assertEqual(unread, [])

    def test_none_without_log(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(verify_refs._recall_activity([], Path(td)))

    def test_linked_worktree_reads_the_shared_log(self):
        # A worktree's `.git` is a file; the hooks log to the shared `.git`.
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        shared = Path(td.name) / "main" / ".git"
        private = shared / "worktrees" / "wt"
        private.mkdir(parents=True)
        (private / "commondir").write_text("../..", encoding="utf-8")
        (shared / "lore-recall.log").write_text(
            "2026-07-17\tprompt\tlearnings/x/a.md\n", encoding="utf-8")
        wt = Path(td.name) / "wt"
        write_entry(wt / "learnings", "x/a.md", title="A")
        (wt / ".git").write_text(f"gitdir: {private}", encoding="utf-8")
        activity = verify_refs._recall_activity(
            list(iter_entries(wt / "learnings")), wt)
        self.assertIsNotNone(activity, "the worktree found no recall log")
        top, never, _unread = activity
        self.assertEqual(top, [(1, 0, "learnings/x/a.md")])
        self.assertEqual(never, 0)


class TestPersonalStoreStats(unittest.TestCase):
    def test_personal_entries_counted_and_attributed(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            store = proj / "learnings"
            write_entry(store, "x/a.md", title="A")
            write_entry(proj / "private", "prefs/p.md", title="P")
            (proj / ".git").mkdir()
            (proj / ".git" / "lore-recall.log").write_text(
                "2026-07-17\tprompt\tprivate/prefs/p.md\n", encoding="utf-8")
            cfg = dict(DEFAULTS, personalStoreDir="private")
            self.assertEqual(
                [e["title"] for e in verify_refs._personal_entries(proj, cfg)],
                ["P"])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                verify_refs.cmd_stats(list(iter_entries(store)), proj, cfg,
                                      store)
        text = out.getvalue()
        self.assertIn("personal store: 1 entries in private", text)
        # the personal entry surfaced, so only the team entry is "never surfaced"
        self.assertIn("never surfaced: 1", text)

    def test_absent_personal_store_prints_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            store = proj / "learnings"
            write_entry(store, "x/a.md", title="A")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                verify_refs.cmd_stats(list(iter_entries(store)), proj,
                                      dict(DEFAULTS), store)
        self.assertNotIn("personal store", out.getvalue())


class TestPersonalOnlyStore(unittest.TestCase):
    """A project may run personal-store-only: `personalStoreDir` set, no team store.

    Bailing on the team store alone used to kill every mode there, while recall
    and capture kept working — the store looked healthy and its maintenance
    tooling was silently gone.
    """

    def _run(self, proj, *argv):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(proj)}), \
                mock.patch("sys.argv", ["verify_refs.py", *argv]), \
                contextlib.redirect_stdout(out):
            rc = verify_refs.main()
        return rc, out.getvalue()

    def _project(self, with_team_store=False):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        proj = Path(td.name)
        (proj / ".lore.json").write_text(
            json.dumps({"personalStoreDir": ".lore/personal"}), encoding="utf-8")
        write_entry(proj / ".lore" / "personal", "prefs/p.md", title="P")
        if with_team_store:
            write_entry(proj / "learnings", "x/a.md", title="A")
        return proj

    def test_default_mode_runs_instead_of_bailing(self):
        rc, text = self._run(self._project())
        self.assertEqual(rc, 0)
        self.assertNotIn("run /lore:init first", text)
        self.assertIn("nothing to check", text)

    def test_stats_reports_the_personal_entries(self):
        rc, text = self._run(self._project(), "--stats")
        self.assertEqual(rc, 0)
        self.assertIn("personal store: 1 entries", text)

    def test_report_and_dupes_run(self):
        for flag in ("--report", "--dupes"):
            with self.subTest(flag=flag):
                rc, text = self._run(self._project(), flag)
                self.assertEqual(rc, 0)
                self.assertNotIn("run /lore:init first", text)

    def test_index_is_a_no_op_without_a_team_store(self):
        proj = self._project()
        rc, text = self._run(proj, "--index")
        self.assertEqual(rc, 0)
        self.assertIn("nothing to index", text)
        self.assertFalse((proj / "learnings").exists())

    def test_no_store_at_all_still_bails(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        rc, text = self._run(Path(td.name))
        self.assertEqual(rc, 0)
        self.assertIn("run /lore:init first", text)

    def test_populated_team_store_message_unchanged(self):
        rc, text = self._run(self._project(with_team_store=True))
        self.assertEqual(rc, 0)
        self.assertIn("all entries have valid frontmatter file refs", text)


class TestConsolidationChecks(unittest.TestCase):
    """The linter's half of the consolidated layer.

    Both links it creates can rot, in opposite directions, and both are silent:
    an entry stamped for a skill that is gone, and a skill citing a learning
    that is gone (a rule whose evidence has vanished).
    """

    def _run(self, proj, *argv):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(proj)}), \
                mock.patch("sys.argv", ["verify_refs.py", *argv]), \
                contextlib.redirect_stdout(out):
            rc = verify_refs.main()
        return rc, out.getvalue()

    def _project(self, consolidated_dirs=(".claude/skills/lore-*",)):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        proj = Path(td.name)
        cfg = {}
        if consolidated_dirs:
            cfg["consolidatedDirs"] = list(consolidated_dirs)
        (proj / ".lore.json").write_text(json.dumps(cfg), encoding="utf-8")
        (proj / "learnings").mkdir()
        return proj

    def test_clean_layer_passes_and_says_what_it_checked(self):
        proj = self._project()
        write_entry(proj / "learnings", "ci/a.md", title="A", tags=("ci",),
                    consolidated_into="lore-ci")
        write_skill(proj, "lore-ci", description="CI", tags=("ci",))
        rc, text = self._run(proj, "--strict")
        self.assertEqual(rc, 0)
        self.assertIn("1 consolidated skills", text)

    def test_stamp_pointing_at_a_missing_skill_is_actionable(self):
        proj = self._project()
        write_entry(proj / "learnings", "ci/a.md", title="A", tags=("ci",),
                    consolidated_into="lore-nope")
        rc, text = self._run(proj, "--strict")
        self.assertEqual(rc, 1)
        self.assertIn("point at a skill that does not exist", text)
        self.assertIn("lore-nope", text)

    def test_unset_consolidated_dirs_is_named_as_the_cause(self):
        # Stamped entries with no `consolidatedDirs` is the misconfiguration
        # that makes every skill invisible to recall -- say so, don't just list
        # 400 dangling stamps.
        proj = self._project(consolidated_dirs=())
        write_entry(proj / "learnings", "ci/a.md", title="A", tags=("ci",),
                    consolidated_into="lore-ci")
        write_skill(proj, "lore-ci", description="CI")
        rc, text = self._run(proj, "--strict")
        self.assertEqual(rc, 1)
        self.assertIn("`consolidatedDirs` is not set", text)

    def test_skill_citing_a_deleted_learning_is_actionable(self):
        proj = self._project()
        write_skill(proj, "lore-ci", description="CI", tags=("ci",),
                    body="- rule ([x](../../../learnings/ci/gone.md))")
        rc, text = self._run(proj, "--strict")
        self.assertEqual(rc, 1)
        self.assertIn("cite a learning that is gone", text)
        self.assertIn("learnings/ci/gone.md", text)

    def test_a_citation_written_twice_is_one_issue(self):
        proj = self._project()
        write_skill(proj, "lore-ci", description="CI", tags=("ci",),
                    body="- rule ([learnings/ci/gone.md]"
                         "(../../../learnings/ci/gone.md))")
        _rc, text = self._run(proj)
        self.assertEqual(text.count("cited learning no longer exists"), 1)

    def test_a_live_citation_passes(self):
        proj = self._project()
        write_entry(proj / "learnings", "ci/here.md", title="Here", tags=("ci",))
        write_skill(proj, "lore-ci", description="CI", tags=("ci",),
                    body="- rule ([here](../../../learnings/ci/here.md))")
        rc, _text = self._run(proj, "--strict")
        self.assertEqual(rc, 0)

    def test_links_outside_the_store_are_not_checked(self):
        proj = self._project()
        write_skill(proj, "lore-ci", description="CI", tags=("ci",),
                    body="see [the docs](../../../docs/missing.md)")
        rc, text = self._run(proj, "--strict")
        self.assertEqual(rc, 0)
        self.assertNotIn("cite a learning", text)

    def test_skill_file_refs_are_existence_checked(self):
        proj = self._project()
        write_skill(proj, "lore-ci", description="CI", tags=("ci",),
                    files=("gone/path.yml",))
        rc, text = self._run(proj, "--strict")
        self.assertEqual(rc, 1)
        self.assertIn("[consolidated skill]", text)
        self.assertIn("gone/path.yml", text)

    def test_frontmatter_the_host_cannot_parse_is_actionable(self):
        # 42 of 47 skills in one real pass. The skill still works as a file and
        # lore reads the description fine; the host throws the block away.
        proj = self._project()
        write_skill(proj, "lore-ci",
                    description="CI invariants: the always-on gate",
                    tags=("ci",))
        rc, text = self._run(proj, "--strict")
        self.assertEqual(rc, 1)
        self.assertIn("Claude Code cannot parse", text)
        self.assertIn("description", text)

    def test_a_quoted_description_is_not_flagged(self):
        proj = self._project()
        write_skill(proj, "lore-ci", description='"CI invariants: the gate"',
                    tags=("ci",))
        rc, text = self._run(proj, "--strict")
        self.assertEqual(rc, 0)
        self.assertNotIn("cannot parse", text)

    def test_stats_counts_unloadable_skills(self):
        proj = self._project()
        write_skill(proj, "lore-ci", description="CI: the gate", tags=("ci",))
        _rc, text = self._run(proj, "--stats")
        self.assertIn("frontmatter the host cannot parse: 1", text)

    def test_oversize_skill_is_a_note_not_a_failure(self):
        proj = self._project()
        write_skill(proj, "lore-ci", description="CI", tags=("ci",),
                    body=" ".join(["word"] * 501))
        rc, text = self._run(proj, "--strict")
        self.assertEqual(rc, 0, "a long skill is untidy, not broken")
        self.assertIn("over the 500-word limit", text)

    def test_stats_counts_consolidated_and_not(self):
        proj = self._project()
        write_entry(proj / "learnings", "ci/a.md", title="A", tags=("ci",),
                    consolidated_into="lore-ci")
        write_entry(proj / "learnings", "ci/b.md", title="B", tags=("ci",))
        write_skill(proj, "lore-ci", description="CI", tags=("ci",),
                    files=("x.yml",))
        rc, text = self._run(proj, "--stats")
        self.assertEqual(rc, 0)
        self.assertIn("consolidation:", text)
        self.assertIn("current entries consolidated: 1 / 2", text)
        self.assertIn("(1 not yet)", text)

    def test_stats_is_silent_without_a_consolidated_layer(self):
        proj = self._project(consolidated_dirs=())
        write_entry(proj / "learnings", "ci/a.md", title="A", tags=("ci",))
        rc, text = self._run(proj, "--stats")
        self.assertEqual(rc, 0)
        self.assertNotIn("consolidation:", text)

    def test_stats_flags_skills_without_file_refs(self):
        proj = self._project()
        write_skill(proj, "lore-ci", description="CI", tags=("ci",))
        _rc, text = self._run(proj, "--stats")
        self.assertIn("without `files:`: 1", text)


@unittest.skipUnless(GIT, "git not available")
class TestPersonalStorePublication(unittest.TestCase):
    """An in-repo personal store stays private only while git ignores it."""

    def _git(self, proj, *args):
        subprocess.run(
            [GIT, "-C", str(proj), "-c", "user.email=t@t", "-c",
             "user.name=t", *args],
            check=True, capture_output=True)

    def _project(self, gitignore=""):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        proj = Path(td.name)
        self._git(proj, "init", "-q")
        write_entry(proj / "notes", "prefs/diff.md", title="Show the diff first")
        if gitignore:
            (proj / ".gitignore").write_text(gitignore, encoding="utf-8")
        return proj

    def _warn(self, proj, personal="notes"):
        return verify_refs.personal_store_warning(
            proj, dict(DEFAULTS, personalStoreDir=personal))

    def test_unignored_store_is_flagged(self):
        self.assertIn("not gitignored", self._warn(self._project()) or "")

    def test_ignored_store_is_silent(self):
        self.assertIsNone(self._warn(self._project(gitignore="notes/\n")))

    def test_committed_store_is_flagged_even_once_ignored(self):
        proj = self._project(gitignore="notes/\n")
        self._git(proj, "add", "-f", "notes")
        self._git(proj, "commit", "-q", "-m", "oops")
        self.assertIn("committed", self._warn(proj) or "")

    def test_committed_store_offers_a_quoted_free_fix_command(self):
        proj = self._project(gitignore="notes/\n")
        self._git(proj, "add", "-f", "notes")
        self._git(proj, "commit", "-q", "-m", "oops")
        self.assertIn("git rm -r --cached -- notes", self._warn(proj) or "")

    def test_a_crafted_store_name_is_never_put_in_a_command(self):
        # `personalStoreDir` comes from .lore.json, i.e. from the repository,
        # and this warning is written to be pasted into a terminal.
        name = "notes; echo pwned #"
        proj = self._project()
        write_entry(proj / name, "prefs/a.md", title="Pref")
        self._git(proj, "add", "-f", "--", name)
        self._git(proj, "commit", "-q", "-m", "oops")
        warning = self._warn(proj, personal=name) or ""
        self.assertIn("committed", warning)
        self.assertNotIn("git rm -r --cached -- notes; echo", warning)
        self.assertIn("name needs quoting", warning)

    def test_store_outside_the_repository_is_not_checked(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        self.assertIsNone(self._warn(self._project(), personal=outside.name))

    def test_lint_and_stats_print_it(self):
        proj = self._project()
        (proj / ".lore.json").write_text(
            json.dumps({"personalStoreDir": "notes"}), encoding="utf-8")
        for argv in ([], ["--stats"]):
            out = io.StringIO()
            with self.subTest(argv=argv), \
                    mock.patch.dict(os.environ,
                                    {"CLAUDE_PROJECT_DIR": str(proj)}), \
                    mock.patch("sys.argv", ["verify_refs.py", *argv]), \
                    contextlib.redirect_stdout(out):
                self.assertEqual(verify_refs.main(), 0)
                self.assertIn("personalStoreDir 'notes'", out.getvalue())


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
