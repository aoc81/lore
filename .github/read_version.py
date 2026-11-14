#!/usr/bin/env python3
"""Print the plugin manifest's version, for the shell steps in the workflows.

A `python -c` one-liner doing this inside a YAML `run:` block has to survive two
layers of quoting; a file does not. Stdlib only, so CI needs no install step.
"""
import json
import sys
from pathlib import Path

MANIFEST = (Path(__file__).resolve().parents[1]
            / "plugin" / ".claude-plugin" / "plugin.json")


def main():
    version = json.loads(MANIFEST.read_text(encoding="utf-8")).get("version")
    if not version:
        print(f"no 'version' in {MANIFEST}", file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
