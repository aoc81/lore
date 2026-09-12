"""Tests for the packaging manifests: marketplace.json, plugin.json, hooks.json.

These guard installability — a manifest that drops a required field makes
`/plugin marketplace add` fail for everyone, with nothing in the test suite
to catch it.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN_MANIFEST = ROOT / "plugin" / ".claude-plugin" / "plugin.json"
HOOKS = ROOT / "plugin" / "hooks" / "hooks.json"

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


if __name__ == "__main__":
    unittest.main()
