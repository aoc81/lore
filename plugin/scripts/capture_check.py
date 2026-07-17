#!/usr/bin/env python3
"""Stop hook: nudge the model to capture a durable learning -- once per turn.

Delivery: a `{"decision":"block","reason":...}` JSON object on STDOUT (exit 0).
This is the contract the Claude Code CLI, the VS Code extension, AND OpenAI
Codex all honor to push the reminder back as a continuation prompt, so the
model acts on it in the same turn.

DO NOT switch this back to the `stderr + exit 2` pattern: the Claude Code VS
Code extension (claude-vscode, verified on 2.1.191) silently discards a non-zero
Stop hook as a "non-blocking status code" -- the reminder lands in `hookErrors`
with `preventedContinuation:false` and NEVER reaches the model, so autonomous
capture looks dead even though the hook runs every turn.

Nudge gating (`captureNudge` in `.lore.json`):
  "always" -- nudge every turn (the pre-0.3 behavior).
  "smart"  -- (default) skip the nudge when the just-finished turn used no
              tools at all (a purely conversational turn produces no durable
              learning; nudging it only trains the model to answer the check
              mechanically). Detection reads the transcript tail and FAILS
              OPEN: if the turn can't be classified, the nudge fires.
  "off"    -- never nudge; capture stays manual via /lore:capture.

As `capture_check.py --compact` (a SessionStart hook with matcher "compact"):
after a compaction, re-inject a short lore reminder as `additionalContext` --
compaction is exactly where an uncaptured learning dies with the context, and
where the model forgets the store exists. (PreCompact cannot inject context,
so the post-compact SessionStart is the supported channel.)

`stop_hook_active` (read from stdin) makes the nudge fire once per stop, never
looping. The `--codex` flag is accepted for backward compat but no longer
changes behavior -- both targets use the same stdout object.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import find_project_dir, load_config  # noqa: E402

REMINDER = (
    "LORE CHECK -- required before you end this turn. Run the gate now: "
    "did this turn produce a learning that is BOTH non-obvious AND "
    "reusable (a fixed bug with its root cause, a non-obvious "
    "decision/convention, a gotcha that cost real time, or a user "
    "preference worth keeping)? If yes, CAPTURE IT NOW -- invoke the lore "
    "skill and write the file yourself, this turn. Do not ask, do not "
    "offer, do not defer it to a follow-up, and do not end the turn with "
    "a qualifying learning uncaptured -- capturing is an action you take, "
    "not one you suggest. ALWAYS end the turn with exactly ONE lore-check "
    "line so the result is visible every turn: either 'Lore -- captured: "
    "<path>' (or 'Lore -- updated: <path>') when something clears the "
    "gate, or 'Lore check -- no durable learning this turn.' when nothing "
    "does. Never end silently."
)

COMPACT_REMINDER = (
    "Context was just compacted. Lore reminder: this project has a learnings "
    "store at `{store}/` -- recall keeps surfacing relevant entries on each "
    "prompt, and the end-of-turn LORE CHECK still applies. If the "
    "pre-compaction work produced a durable, non-obvious learning that was "
    "not yet captured, capture it NOW via the lore skill, while you can "
    "still state it precisely -- anything uncaptured only survives as "
    "summary."
)

# Reading this many bytes from the transcript tail is enough to span any
# realistic turn while keeping the hook O(1) on huge sessions.
_TAIL_BYTES = 500_000


def _turn_used_tools(transcript_path):
    """Did the just-finished turn contain any tool_use?

    Walks the transcript JSONL tail backwards to the turn boundary (the last
    real user prompt -- a user entry whose content is text, not tool_result).
    Returns True/False, or None when the turn can't be classified (missing or
    lagging transcript, unknown shapes) -- callers treat None as "nudge".
    """
    try:
        p = Path(transcript_path)
        with p.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - _TAIL_BYTES))
            tail = f.read().decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    saw_assistant = False
    for line in reversed(tail.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        typ = obj.get("type")
        msg = obj.get("message") or {}
        content = msg.get("content")
        if typ == "assistant":
            saw_assistant = True
            if isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_use"
                    for b in content):
                return True
        elif typ == "user":
            if isinstance(content, str):
                return False if saw_assistant else None
            if isinstance(content, list):
                blocks = [b for b in content if isinstance(b, dict)]
                if any(b.get("type") == "tool_result" for b in blocks):
                    continue  # still inside the turn
                if any(b.get("type") == "text" for b in blocks):
                    return False if saw_assistant else None
    return None


def run_stop():
    """Emit the capture nudge as a stdout `decision: block` object."""
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    if data.get("stop_hook_active"):
        return 0  # already nudged for this stop; don't loop
    cfg = load_config(find_project_dir(data))
    mode = cfg["captureNudge"]
    if mode == "off":
        return 0
    if mode == "smart" and data.get("transcript_path"):
        used = _turn_used_tools(data["transcript_path"])
        if used is False:
            return 0  # purely conversational turn -- nothing to capture
    # Block the stop and feed the reminder back. stdout JSON is the only channel
    # the VS Code extension honors (stderr+exit-2 is discarded -- see module doc).
    print(json.dumps({"decision": "block", "reason": REMINDER}))
    return 0


def run_compact():
    """SessionStart(source=compact): re-inject lore awareness after compaction."""
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    project = find_project_dir(data)
    cfg = load_config(project)
    if cfg["captureNudge"] == "off":
        return 0
    store = project / cfg["storeDir"]
    if not store.is_dir():
        return 0
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": COMPACT_REMINDER.format(
                store=cfg["storeDir"]),
        }
    }))
    return 0


def main():
    if "--compact" in sys.argv[1:]:
        return run_compact()
    return run_stop()


if __name__ == "__main__":
    sys.exit(main())
