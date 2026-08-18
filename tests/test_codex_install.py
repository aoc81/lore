"""Tests for codex/install.py: hook registration, idempotency, the CI guard."""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

import _util  # noqa: F401  (puts plugin/scripts on sys.path)

REPO = Path(__file__).resolve().parents[1]


def _load_installer():
    """Import codex/install.py by path -- it isn't an importable package."""
    spec = importlib.util.spec_from_file_location(
        "codex_install", REPO / "codex" / "install.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


install = _load_installer()


class TestHookGroups(unittest.TestCase):
    def setUp(self):
        self.groups = install._hook_groups(Path("/codex/lore"))

    def test_registers_all_four_events(self):
        # Codex gained PreToolUse and SessionStart(compact) after the first
        # port; parity with the Claude plugin depends on all four being wired.
        self.assertEqual(set(self.groups),
                         {"UserPromptSubmit", "PreToolUse", "Stop",
                          "SessionStart"})

    def test_pretool_matches_the_tool_codex_reports(self):
        # Codex routes every edit through apply_patch, whatever the alias.
        matcher = self.groups["PreToolUse"]["matcher"]
        self.assertIn("apply_patch", matcher)
        cmd = self.groups["PreToolUse"]["hooks"][0]["command"]
        self.assertIn("recall.py", cmd)
        self.assertTrue(cmd.endswith("--pretool"))

    def test_session_start_is_scoped_to_compaction(self):
        group = self.groups["SessionStart"]
        self.assertEqual(group["matcher"], "compact")
        self.assertTrue(group["hooks"][0]["command"].endswith("--compact"))

    def test_prompt_and_stop_take_no_flags(self):
        for event in ("UserPromptSubmit", "Stop"):
            cmd = self.groups[event]["hooks"][0]["command"]
            self.assertNotIn("--", cmd.split('" "')[-1].rstrip('"'))


class TestMergeHooks(unittest.TestCase):
    def _merge_twice(self, initial=None):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / "hooks.json"
        if initial is not None:
            path.write_text(json.dumps(initial), encoding="utf-8")
        lore_dir = Path(td.name) / "lore"
        install._merge_hooks(path, lore_dir)
        install._merge_hooks(path, lore_dir)  # re-install must not duplicate
        return json.loads(path.read_text(encoding="utf-8"))

    def test_idempotent(self):
        data = self._merge_twice()
        for event in install._hook_groups(Path("/x")):
            self.assertEqual(len(data["hooks"][event]), 1, event)

    def test_keeps_foreign_hooks(self):
        mine = {"matcher": "", "hooks": [
            {"type": "command", "command": "echo not-lore"}]}
        data = self._merge_twice({"hooks": {"Stop": [mine]}})
        commands = [h["command"] for g in data["hooks"]["Stop"]
                    for h in g["hooks"]]
        self.assertIn("echo not-lore", commands)
        self.assertEqual(len(data["hooks"]["Stop"]), 2)

    def test_survives_a_corrupt_file(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / "hooks.json"
        path.write_text("{not json", encoding="utf-8")
        install._merge_hooks(path, Path(td.name) / "lore")
        self.assertIn("UserPromptSubmit",
                      json.loads(path.read_text(encoding="utf-8"))["hooks"])


class TestInstallCI(unittest.TestCase):
    def _repo(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return Path(td.name)

    def _install(self, repo):
        """Run install_ci with its progress output swallowed."""
        with contextlib.redirect_stdout(io.StringIO()):
            install.install_ci(repo)

    def test_copies_guard_scripts_and_workflow(self):
        repo = self._repo()
        self._install(repo)
        for name in ("scan_secrets.py", "verify_refs.py", "_common.py"):
            self.assertTrue((repo / ".lore" / name).is_file(), name)
        wf = repo / ".github" / "workflows" / "lore.yml"
        self.assertTrue(wf.is_file())
        text = wf.read_text(encoding="utf-8")
        # The workflow must run the COMMITTED copies, not a plugin path.
        self.assertIn(".lore/scan_secrets.py", text)
        self.assertIn(".lore/verify_refs.py --strict", text)

    def test_version_stamp_matches_the_plugin(self):
        repo = self._repo()
        self._install(repo)
        stamped = (repo / ".lore" / "VERSION").read_text(encoding="utf-8").strip()
        manifest = json.loads(
            (REPO / "plugin" / ".claude-plugin" / "plugin.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(stamped, manifest["version"])

    def test_existing_workflow_is_never_overwritten(self):
        repo = self._repo()
        wf = repo / ".github" / "workflows" / "lore.yml"
        wf.parent.mkdir(parents=True)
        wf.write_text("# mine\n", encoding="utf-8")
        self._install(repo)
        self.assertEqual(wf.read_text(encoding="utf-8"), "# mine\n")


if __name__ == "__main__":
    unittest.main()
