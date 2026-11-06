"""Tests for the packaging manifests: marketplace.json, plugin.json, hooks.json.

These guard installability — a manifest that drops a required field makes
`/plugin marketplace add` fail for everyone, with nothing in the test suite
to catch it.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN_MANIFEST = ROOT / "plugin" / ".claude-plugin" / "plugin.json"
HOOKS = ROOT / "plugin" / "hooks" / "hooks.json"
SKILL = ROOT / "plugin" / "skills" / "lore" / "SKILL.md"
SH = shutil.which("sh")

# Fields Claude Code reads at the top level of marketplace.json. Anything else
# is ignored at load time (`claude plugin validate` warns about it).
MARKETPLACE_KEYS = {"name", "owner", "description", "version", "metadata", "plugins"}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestMarketplaceManifest(unittest.TestCase):
    def setUp(self):
        self.mk = load(MARKETPLACE)

    def test_required_fields(self):
        self.assertEqual(self.mk.get("name"), "lore")
        self.assertIsInstance(self.mk.get("plugins"), list)
        self.assertTrue(self.mk["plugins"])

    def test_owner_present_and_named(self):
        # Without `owner`, adding the marketplace fails with "owner: Invalid input".
        owner = self.mk.get("owner")
        self.assertIsInstance(owner, dict, "marketplace.json needs an `owner` object")
        self.assertTrue(owner.get("name"), "`owner.name` is required")

    def test_no_unknown_top_level_fields(self):
        self.assertEqual(set(self.mk) - MARKETPLACE_KEYS, set())

    def test_plugin_entries_resolve(self):
        for entry in self.mk["plugins"]:
            self.assertTrue(entry.get("name"))
            source = entry.get("source")
            self.assertTrue(source, "each plugin entry needs a `source`")
            src = (ROOT / source).resolve()
            self.assertTrue(src.is_dir(), f"source {source} is not a directory")
            self.assertTrue((src / ".claude-plugin" / "plugin.json").is_file(),
                            f"source {source} has no plugin.json")


class TestPluginManifest(unittest.TestCase):
    def setUp(self):
        self.plugin = load(PLUGIN_MANIFEST)

    def test_required_fields(self):
        self.assertEqual(self.plugin.get("name"), "lore")
        self.assertTrue(self.plugin.get("description"))
        self.assertRegex(self.plugin.get("version", ""), r"^\d+\.\d+\.\d+$")

    def test_versions_agree_across_manifests(self):
        mk = load(MARKETPLACE)
        entry = next(e for e in mk["plugins"] if e["name"] == self.plugin["name"])
        self.assertEqual(entry["version"], self.plugin["version"])
        self.assertEqual(mk["version"], self.plugin["version"])


class TestHooksManifest(unittest.TestCase):
    def test_commands_point_at_shipped_scripts(self):
        hooks = load(HOOKS)["hooks"]
        seen = 0
        for matchers in hooks.values():
            for matcher in matchers:
                for hook in matcher["hooks"]:
                    self.assertEqual(hook["type"], "command")
                    m = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/(\S+?)\"", hook["command"])
                    self.assertIsNotNone(m, f"unparsable command: {hook['command']}")
                    script = ROOT / "plugin" / m.group(1)
                    self.assertTrue(script.is_file(), f"missing script: {m.group(1)}")
                    seen += 1
        self.assertTrue(seen)


class TestPluginRootInContent(unittest.TestCase):
    """Skill and command text: only the literal `${CLAUDE_PLUGIN_ROOT}` works.

    Claude Code substitutes that exact token when it loads the content; the
    variable is NOT in the environment of the Bash tool that runs the
    commands. Any other spelling -- `$CLAUDE_PLUGIN_ROOT`,
    `${CLAUDE_PLUGIN_ROOT:+...}` -- reaches the shell unset.
    """

    BAD = re.compile(r"\$CLAUDE_PLUGIN_ROOT\b|\$\{CLAUDE_PLUGIN_ROOT(?!\})")

    def test_only_the_substituted_literal_is_used(self):
        files = (sorted((ROOT / "plugin" / "skills").rglob("*.md"))
                 + sorted((ROOT / "plugin" / "commands").glob("*.md")))
        self.assertTrue(files)
        for f in files:
            with self.subTest(file=f.relative_to(ROOT).as_posix()):
                self.assertEqual(
                    self.BAD.findall(f.read_text(encoding="utf-8")), [])


def _skill_locators():
    """`(var, script, lines)` for each skill snippet locating a bundled script."""
    found = []
    text = SKILL.read_text(encoding="utf-8")
    for block in re.findall(r"```sh\n(.*?)```", text, re.S):
        lines = [ln.strip() for ln in block.splitlines()]
        for ln in lines:
            m = re.match(r'([A-Z])="\$\{CLAUDE_PLUGIN_ROOT.*?(\w+\.py)"', ln)
            if m:
                var = m.group(1)
                keep = [x for x in lines
                        if x.startswith((f"{var}=", f'[ -f "${var}" ]'))]
                found.append((var, m.group(2), keep))
    return found


@unittest.skipUnless(SH and os.name != "nt", "needs a POSIX sh")
class TestSkillFindsItsScripts(unittest.TestCase):
    """The capture skill's overlap check and secret scan must find their script.

    Simulated per target, as the model's Bash tool would run the snippet:
    Claude Code substitutes the literal token, Codex substitutes nothing and
    has the scripts installed under ~/.codex/lore.
    """

    def _resolve(self, var, lines, home, substitute):
        script = "\n".join(lines)
        if substitute:
            script = script.replace("${CLAUDE_PLUGIN_ROOT}",
                                    (ROOT / "plugin").as_posix())
        env = {k: v for k, v in os.environ.items()
               if k != "CLAUDE_PLUGIN_ROOT"}
        env["HOME"] = home
        out = subprocess.run([SH, "-c", f'{script}\nprintf %s "${var}"'],
                             cwd=home, env=env, capture_output=True,
                             text=True)
        return out.stdout

    def test_both_snippets_are_found(self):
        scripts = sorted(s for _v, s, _l in _skill_locators())
        self.assertEqual(scripts, ["recall.py", "scan_secrets.py"])

    def test_claude_code_uses_the_plugin_copy(self):
        for var, script, lines in _skill_locators():
            with self.subTest(script=script), \
                    tempfile.TemporaryDirectory() as home:
                self.assertEqual(
                    self._resolve(var, lines, home, substitute=True),
                    (ROOT / "plugin" / "scripts" / script).as_posix())

    def test_codex_falls_back_to_the_installed_copy(self):
        for var, script, lines in _skill_locators():
            with self.subTest(script=script), \
                    tempfile.TemporaryDirectory() as home:
                installed = Path(home) / ".codex" / "lore" / script
                installed.parent.mkdir(parents=True)
                installed.write_text("", encoding="utf-8")
                self.assertEqual(
                    self._resolve(var, lines, home, substitute=False),
                    installed.as_posix())


if __name__ == "__main__":
    unittest.main()
