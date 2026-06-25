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

`stop_hook_active` (read from stdin) makes the nudge fire once per stop, never
looping. The `--codex` flag is accepted for backward compat but no longer
changes behavior -- both targets use the same stdout object.
"""
import json
import sys

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


def main():
    """Emit the capture nudge as a stdout `decision: block` object."""
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    if data.get("stop_hook_active"):
        return 0  # already nudged for this stop; don't loop
    # Block the stop and feed the reminder back. stdout JSON is the only channel
    # the VS Code extension honors (stderr+exit-2 is discarded -- see module doc).
    print(json.dumps({"decision": "block", "reason": REMINDER}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
