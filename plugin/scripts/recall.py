#!/usr/bin/env python3
"""UserPromptSubmit hook: surface relevant learnings from the project's store.

Reads the hook JSON from stdin, tokenizes the prompt, matches tokens against
each learning's frontmatter (title + tags only -- never the body), and prints
the top matches as `additionalContext`. Emits nothing when nothing matches.

Entries whose status is superseded/obsolete/deprecated stay matchable (their
transferable principle is still useful) but get a 1-point rank penalty and are
flagged [SUPERSEDED] so they are never read as live guidance.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import find_project_dir, iter_entries, load_config  # noqa: E402

# Generic words that would over-match. Domain words are intentionally absent.
STOP = {
    "this", "that", "with", "from", "have", "your", "they", "them", "then",
    "than", "what", "when", "which", "where", "while", "about", "into", "over",
    "under", "also", "just", "like", "make", "made", "need", "want", "some",
    "more", "most", "very", "here", "there", "their", "been", "being", "does",
    "done", "using", "used", "still", "only", "will", "would", "should",
    "could", "please", "again", "same", "such", "each", "both", "file", "files",
    "code", "work", "works", "working", "thing", "things", "look", "looking",
    "check", "perhaps", "maybe", "sort", "without", "because", "after",
    "before", "these", "those",
}


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return
    try:
        data = json.loads(raw)
    except ValueError:
        return
    prompt = str(data.get("prompt") or "")
    if not prompt.strip():
        return

    project = find_project_dir(data)
    cfg = load_config(project)
    store = project / cfg["storeDir"]
    if not store.is_dir():
        return

    tokens = {t for t in re.findall(r"[a-z0-9_]{4,}", prompt.lower()) if t not in STOP}
    if not tokens:
        return

    stale_set = set(cfg["staleStatuses"])
    found = []
    for e in iter_entries(store):
        hay = (e["title"] + " " + " ".join(e["tags"])).lower()
        if not hay.strip():
            continue
        score = sum(1 for t in tokens if t in hay)
        if score:
            stale = e["status"] in stale_set
            # rank key: effective score (stale -1), then current-before-stale on ties
            found.append((score - (1 if stale else 0), 1 if stale else 0, stale, e))
    if not found:
        return

    found.sort(key=lambda r: (-r[0], r[1]))
    lines = []
    for _eff, _s, stale, e in found[: cfg["maxRecall"]]:
        rel = e["path"].relative_to(project).as_posix()
        tag = (
            "  [SUPERSEDED -- historical; apply the principle, not the file/code refs]"
            if stale else ""
        )
        lines.append(f"- {rel} - {e['title']}{tag}")

    ctx = (
        "Possibly-relevant prior learnings (Read a file only if it applies to "
        "this task; otherwise ignore):\n" + "\n".join(lines)
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": ctx,
        }
    }))


if __name__ == "__main__":
    main()
