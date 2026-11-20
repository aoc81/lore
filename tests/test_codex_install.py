"""Tests for codex/install.py: hook registration, idempotency, the CI guard."""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _util  # noqa: F401  (puts plugin/scripts on sys.path)

REPO = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")


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


def _quiet(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        rc = fn(*args)
    return rc, out.getvalue()


def _git_repo(test):
    td = tempfile.TemporaryDirectory()
    test.addCleanup(td.cleanup)
    repo = Path(td.name)
    subprocess.run([GIT, "-C", str(repo), "init", "-q"], check=True,
                   capture_output=True)
    return repo


@unittest.skipUnless(GIT, "needs git")
class TestInstallHook(unittest.TestCase):
    """`--hook` is /lore:init's pre-push step, which Codex did not have."""

    def test_installs_the_hook_and_its_scripts(self):
        repo = _git_repo(self)
        rc, _ = _quiet(install.install_hook, repo)
        self.assertEqual(rc, 0)
        hook = repo / ".git" / "hooks" / "pre-push"
        self.assertTrue(hook.is_file())
        self.assertIn("scan_secrets.py", hook.read_text(encoding="utf-8"))
        if os.name != "nt":
            self.assertTrue(os.access(hook, os.X_OK))
        for name in ("scan_secrets.py", "verify_refs.py", "_common.py",
                     "VERSION"):
            self.assertTrue((repo / ".lore" / name).is_file(), name)

    def test_from_a_subdirectory_targets_the_repo_root(self):
        repo = _git_repo(self)
        sub = repo / "src" / "deep"
        sub.mkdir(parents=True)
        _quiet(install.install_hook, sub)
        self.assertTrue((repo / ".lore" / "scan_secrets.py").is_file())
        self.assertFalse((sub / ".lore").exists())
        self.assertTrue((repo / ".git" / "hooks" / "pre-push").is_file())

    def test_foreign_hook_is_never_overwritten(self):
        repo = _git_repo(self)
        hook = repo / ".git" / "hooks" / "pre-push"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\nexec ruff check\n", encoding="utf-8")
        _rc, out = _quiet(install.install_hook, repo)
        self.assertEqual(hook.read_text(encoding="utf-8"),
                         "#!/bin/sh\nexec ruff check\n")
        self.assertIn("scan_secrets.py --quiet || exit 1", out)

    def test_own_hook_is_refreshed(self):
        repo = _git_repo(self)
        hook = repo / ".git" / "hooks" / "pre-push"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\npython .lore/scan_secrets.py\n",
                        encoding="utf-8")
        _quiet(install.install_hook, repo)
        self.assertEqual(hook.read_text(encoding="utf-8"),
                         (REPO / "plugin" / "scripts" / "pre-push")
                         .read_text(encoding="utf-8"))

    def test_custom_hooks_path_is_left_alone(self):
        repo = _git_repo(self)
        subprocess.run([GIT, "-C", str(repo), "config", "core.hooksPath",
                        ".githooks"], check=True)
        _rc, out = _quiet(install.install_hook, repo)
        self.assertFalse((repo / ".githooks").exists())
        self.assertFalse((repo / ".git" / "hooks" / "pre-push").exists())
        self.assertIn("scan_secrets.py --quiet || exit 1", out)

    def test_outside_a_repo_fails(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        rc, _ = _quiet(install.install_hook, Path(td.name))
        self.assertEqual(rc, 1)
        self.assertFalse((Path(td.name) / ".lore").exists())


class _CodexInstall(unittest.TestCase):
    """A real global install into a throwaway CODEX_HOME."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.home = Path(td.name)
        env = {"CODEX_HOME": str(self.home / ".codex"),
               "CODEX_SKILLS_DIR": str(self.home / "skills")}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None
                                 else os.environ.__setitem__(k, v)
                                 for k, v in saved.items()])
        _quiet(install.install)
        self.lore = self.home / ".codex" / "lore"

    def run_script(self, name, *args, cwd):
        env = {k: v for k, v in os.environ.items()
               if k != "CLAUDE_PROJECT_DIR"}
        out = subprocess.run([sys.executable, str(self.lore / name), *args],
                             cwd=cwd, env=env, capture_output=True, text=True,
                             timeout=60)
        return out.stdout + out.stderr


class TestInstallStampsVersion(_CodexInstall):
    def test_stamp_matches_the_plugin(self):
        manifest = json.loads(
            (REPO / "plugin" / ".claude-plugin" / "plugin.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(
            (self.lore / "VERSION").read_text(encoding="utf-8").strip(),
            manifest["version"])


@unittest.skipUnless(GIT, "needs git")
class TestMessagesNameCodexCommands(_CodexInstall):
    """Run from a Codex install, the scripts must not send anyone to /lore:*."""

    def _project(self):
        repo = _git_repo(self)
        _quiet(install.scaffold_store, repo)
        return repo

    def test_missing_hook_points_at_install_hook(self):
        out = self.run_script("verify_refs.py", cwd=self._project())
        self.assertIn("no pre-push hook", out)
        self.assertIn("codex/install.py --hook", out)
        self.assertNotIn("/lore:init", out)

    def test_stale_copies_are_detected(self):
        # Without a version to compare against, an outdated .lore/ was silent.
        repo = self._project()
        _quiet(install.install_hook, repo)
        (repo / ".lore" / "VERSION").write_text("0.0.1\n", encoding="utf-8")
        out = self.run_script("verify_refs.py", "--stats", cwd=repo)
        self.assertIn("from lore 0.0.1", out)
        self.assertIn("codex/install.py --hook", out)
        self.assertNotIn("/lore:init", out)

    def test_installed_hook_silences_the_warning(self):
        repo = self._project()
        _quiet(install.install_hook, repo)
        out = self.run_script("verify_refs.py", cwd=repo)
        self.assertNotIn("pre-push", out)
        self.assertNotIn("note: .lore/", out)

    def test_missing_store_points_at_install_store(self):
        repo = _git_repo(self)
        for script in ("verify_refs.py", "consolidate.py"):
            with self.subTest(script=script):
                out = self.run_script(script, cwd=repo)
                self.assertIn("codex/install.py --store", out)
                self.assertNotIn("/lore:init", out)


if __name__ == "__main__":
    unittest.main()
