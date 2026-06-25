#!/usr/bin/env python3
"""Surface relevant learnings from the project's store.

As a `UserPromptSubmit` hook (no args, hook JSON on stdin): tokenize the prompt,
match tokens against each learning's frontmatter (title + tags only -- never the
body), and print the top matches as `additionalContext`. Emits nothing when
nothing matches.

As a CLI (`recall.py --query "<text>"`): print the same ranked matches in plain
text -- used as the capture-time overlap check so a new learning UPDATES an
existing entry instead of creating a near-duplicate.

As a `PreToolUse` hook (`recall.py --pretool`, tool JSON on stdin): when about to
Edit/Write a file, surface learnings whose frontmatter `files:` lists that path --
edit-time recall, so a gotcha shows up exactly when you touch the code. Silent
unless a learning names the target file.

Entries whose status is superseded/obsolete/deprecated stay matchable (their
transferable principle is still useful) but get a 1-point rank penalty and are
flagged [SUPERSEDED] so they are never read as live guidance.

Each surfaced entry also carries cheap freshness flags so a possibly-stale
learning is never trusted blindly: a `current` entry that points at a
now-deleted file is flagged, and a long-unverified entry shows its age. The
costly git-drift check stays in verify_refs.py (--report), out of the hook.
"""
import datetime
import json
import os
import re
import sys
from pathlib import Path

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

# Flag an entry as possibly stale when its last check is older than this.
STALE_AFTER_MONTHS = 6
STALE_STATUSES = ("superseded", "obsolete", "deprecated")


def _months_since(iso):
    """Whole months between an ISO date and today; None if unparseable."""
    try:
        d = datetime.date.fromisoformat(iso)
    except ValueError:
        return None
    today = datetime.date.today()
    return (today.year - d.year) * 12 + (today.month - d.month)


def freshness_flags(e, project):
    """Cheap staleness signals for a surfaced entry (no git, no LLM).

    - A `current` entry whose `files:` lists a now-deleted path is a strong
      stale signal (the claim's anchor is gone) -> flag it.
    - An entry not verified in >= STALE_AFTER_MONTHS shows its age, so it is
      not trusted blindly. `date:` is the stamp when `verified:` is absent.
    """
    flags = []
    if e["status"] not in STALE_STATUSES:
        missing = [f for f in e["files"] if f and not (project / f).exists()]
        if missing:
            flags.append("! refs a deleted file")
    stamp = e["verified"] or e["date"]
    months = _months_since(stamp) if stamp else None
    if months is not None and months >= STALE_AFTER_MONTHS:
        label = "verified" if e["verified"] else "written"
        flags.append(f"{label} {months}mo ago")
    return flags


def rank_matches(text, store, cfg):
    """Rank store entries by title+tags token overlap with `text`.

    Returns [(stale_bool, entry), ...] best-first, capped to maxRecall.
    Same scorer for the prompt hook and the capture-time overlap check.
    """
    tokens = {t for t in re.findall(r"[a-z0-9_]{4,}", text.lower())
              if t not in STOP}
    if not tokens:
        return []
    stale_set = set(cfg["staleStatuses"])
    found = []
    for e in iter_entries(store):
        hay = (e["title"] + " " + " ".join(e["tags"])).lower()
        if not hay.strip():
            continue
        score = sum(1 for t in tokens if t in hay)
        if score:
            stale = e["status"] in stale_set
            # rank: effective score (stale -1), current-before-stale on ties
            found.append((score - (1 if stale else 0), 1 if stale else 0,
                          stale, e))
    found.sort(key=lambda r: (-r[0], r[1]))
    return [(stale, e) for _eff, _s, stale, e in found[: cfg["maxRecall"]]]


def _norm(p):
    p = p.strip().replace("\\", "/")
    return p[2:] if p.startswith("./") else p


def match_by_file(target_rel, store):
    """Learnings whose frontmatter `files:` names `target_rel` (exact rel path).

    Returns [(stale_bool, entry), ...]. The match key is the file you're about
    to edit, not prompt tokens -- this powers edit-time (PreToolUse) recall.
    """
    target = _norm(target_rel)
    matches = []
    for e in iter_entries(store):
        if any(_norm(f) == target for f in e["files"] if f):
            matches.append((e["status"] in STALE_STATUSES, e))
    return matches


def _format_entry(e, stale, project):
    rel = e["path"].relative_to(project).as_posix()
    bits = []
    if stale:
        bits.append("SUPERSEDED -- apply the principle, not the file/code refs")
    bits.extend(freshness_flags(e, project))
    tag = f"  [{' | '.join(bits)}]" if bits else ""
    return f"- {rel} - {e['title']}{tag}"


def run_hook():
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
    matches = rank_matches(prompt, store, cfg)
    if not matches:
        return
    lines = [_format_entry(e, stale, project) for stale, e in matches]
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


def run_query(text):
    project = find_project_dir()
    cfg = load_config(project)
    store = project / cfg["storeDir"]
    if not store.is_dir():
        print("No learnings store found.")
        return
    matches = rank_matches(text, store, cfg)
    if not matches:
        print("No related learnings found -- looks new; create a fresh entry.")
        return
    print("Related existing learnings (overlap check before you write):")
    for stale, e in matches:
        print(_format_entry(e, stale, project))
    print("\n-> If one is the SAME problem/area, UPDATE it instead of creating a")
    print("   near-duplicate. Only add a new file if none truly overlaps.")


def run_pretool():
    raw = sys.stdin.read()
    if not raw.strip():
        return
    try:
        data = json.loads(raw)
    except ValueError:
        return
    if (data.get("tool_name") or "") not in ("Edit", "Write", "MultiEdit",
                                             "NotebookEdit"):
        return
    tinput = data.get("tool_input") or {}
    fp = tinput.get("file_path") or tinput.get("notebook_path") or ""
    if not fp:
        return
    project = find_project_dir(data)
    cfg = load_config(project)
    store = project / cfg["storeDir"]
    if not store.is_dir():
        return
    try:
        rel = Path(fp).resolve().relative_to(project.resolve()).as_posix()
    except (ValueError, OSError):
        rel = _norm(fp)
    matches = match_by_file(rel, store)
    if not matches:
        return  # stay silent unless a learning names this file
    lines = [_format_entry(e, stale, project)
             for stale, e in matches[: cfg["maxRecall"]]]
    ctx = (
        f"Lore -- learnings recorded about `{rel}` (consider before editing):\n"
        + "\n".join(lines)
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": ctx,
        }
    }))


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--query":
        run_query(" ".join(argv[1:]))
    elif argv and argv[0] == "--pretool":
        run_pretool()
    else:
        run_hook()


if __name__ == "__main__":
    main()
