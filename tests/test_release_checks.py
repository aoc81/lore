"""Tests for .github/check_tag_version.py — the tag/manifest release gate.

Run as a subprocess against a throwaway tree rather than imported: the script
resolves the manifests from its own location, which is the behaviour worth
testing, and it is what CI actually invokes.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _util  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_REL = Path(".github") / "check_tag_version.py"


def _tree(plugin_version, market_version=None, entry_versions=("same",)):
    """A throwaway repo holding only what the script reads. Returns its Path.

    `market_version` defaults to `plugin_version`; an entry of "same" means the
    marketplace plugin entry agrees too.
    """
    td = tempfile.mkdtemp()
    root = Path(td)
    (root / SCRIPT_REL.parent).mkdir(parents=True)
    shutil.copy(ROOT / SCRIPT_REL, root / SCRIPT_REL)

    plugin_dir = root / "plugin" / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "lore", "version": plugin_version}),
        encoding="utf-8")

    market = {
        "name": "lore",
        "version": market_version or plugin_version,
        "plugins": [
            {"name": "lore",
             "version": plugin_version if v == "same" else v,
             "source": "./plugin"}
            for v in entry_versions
        ],
    }
    market_dir = root / ".claude-plugin"
    market_dir.mkdir(parents=True)
    (market_dir / "marketplace.json").write_text(json.dumps(market),
                                                 encoding="utf-8")
    return root


def _run(root, tag):
    env = dict(os.environ, TAG=tag)
    return subprocess.run([sys.executable, str(SCRIPT_REL)], cwd=str(root),
                          env=env, capture_output=True, text=True)


class TestCheckTagVersion(unittest.TestCase):
    def _tree(self, *a, **kw):
        root = _tree(*a, **kw)
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return root

    def test_all_fields_agree(self):
        r = _run(self._tree("1.2.3"), "v1.2.3")
        self.assertEqual(r.returncode, 0, r.stderr)
        # Say what was examined, not just that it passed.
        self.assertIn("all 3 version fields", r.stdout)

    def test_tag_without_the_v_prefix(self):
        self.assertEqual(_run(self._tree("1.2.3"), "1.2.3").returncode, 0)

    def test_plugin_manifest_behind_the_tag(self):
        # The v0.5.1 case: tag cut before the release bump landed.
        r = _run(self._tree("0.5.0"), "v0.5.1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("plugin/.claude-plugin/plugin.json: '0.5.0'", r.stderr)

    def test_marketplace_top_level_behind_the_tag(self):
        r = _run(self._tree("1.2.3", market_version="1.2.2"), "v1.2.3")
        self.assertEqual(r.returncode, 1)
        self.assertIn("marketplace.json: '1.2.2'", r.stderr)

    def test_nested_plugin_entry_behind_the_tag(self):
        # The easiest field to forget: it is the third one, inside an array.
        r = _run(self._tree("1.2.3", entry_versions=("1.2.2",)), "v1.2.3")
        self.assertEqual(r.returncode, 1)
        self.assertIn("marketplace.json: '1.2.2'", r.stderr)

    def test_empty_tag_is_not_a_pass(self):
        # Nothing to compare against must not read as agreement.
        r = _run(self._tree("1.2.3"), "")
        self.assertEqual(r.returncode, 2)

    def test_fewer_fields_than_expected_is_not_a_pass(self):
        # If the manifests lose a version field, the check silently covers less
        # than it claims -- so it refuses instead of passing over the remainder.
        r = _run(self._tree("1.2.3", entry_versions=()), "v1.2.3")
        self.assertEqual(r.returncode, 1)
        self.assertIn("changed shape", r.stderr)

    def test_the_real_manifests_pass_their_own_version(self):
        # Guards the wiring: the paths in the script still point at this repo's
        # manifests, whatever the current version happens to be.
        version = json.loads(
            (ROOT / "plugin" / ".claude-plugin" / "plugin.json")
            .read_text(encoding="utf-8"))["version"]
        r = _run(ROOT, f"v{version}")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
