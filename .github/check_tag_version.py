#!/usr/bin/env python3
"""Assert both manifests declare the version named by the tag in `$TAG`.

Four things have to agree before a version here is real: `plugin.json`, the
marketplace entry, the git tag, and the GitHub release. The first three can only
be fixed cheaply BEFORE anyone installs -- moving a published tag is
destructive -- so they are checked the moment a tag is pushed.

`v0.5.1` once pointed at a tree whose manifest still said `0.5.0`, because the
tag was cut before the `chore: release` bump landed. Nothing revealed it: the
tag, the release and the manifest each looked right on their own.

Stdlib only, like the rest of this repo, so CI needs no install step.

Usage: TAG=v1.2.3 python .github/check_tag_version.py
Exit:  0 agree - 1 disagree - 2 nothing to check against.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_REL = "plugin/.claude-plugin/plugin.json"
MARKET_REL = ".claude-plugin/marketplace.json"
# A tag is worth refusing over fewer fields than we expect to find: the point of
# the check is that every published surface agrees, so finding less than the
# whole set means the manifests moved and this script did not follow.
EXPECTED_FIELDS = 3


def _versions():
    """Every version field in the two manifests, as `{where: [version, ...]}`."""
    plugin = json.loads((ROOT / PLUGIN_REL).read_text(encoding="utf-8"))
    market = json.loads((ROOT / MARKET_REL).read_text(encoding="utf-8"))
    return {
        PLUGIN_REL: [plugin.get("version")],
        MARKET_REL: [market.get("version")]
        + [p.get("version") for p in market.get("plugins") or []],
    }


def main():
    tag = (os.environ.get("TAG") or "").strip()
    if not tag:
        print("TAG is empty -- no tag to check the tree against.",
              file=sys.stderr)
        return 2
    want = tag[1:] if tag.startswith("v") else tag

    found = _versions()
    total = sum(len(v) for v in found.values())
    mismatched = [f"{where}: {got!r}" for where, got_list in found.items()
                  for got in got_list if got != want]

    if total < EXPECTED_FIELDS:
        print(f"::error::found only {total} version field(s), expected "
              f"{EXPECTED_FIELDS} -- the manifests changed shape and this "
              "check no longer covers them all.", file=sys.stderr)
        return 1
    if mismatched:
        print(f"::error::{tag} claims a version its own tree does not contain "
              f"(expected {want!r}):", file=sys.stderr)
        for line in mismatched:
            print(f"  {line}", file=sys.stderr)
        print("  -> A tag cut before the `chore: release` bump lands ships a "
              "tree that disagrees with its own version. Delete the tag, land "
              "the bump, then re-tag.", file=sys.stderr)
        return 1

    print(f"OK  {tag}: all {total} version fields in the tagged tree say "
          f"{want}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
