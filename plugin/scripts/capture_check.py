#!/usr/bin/env python3
"""Stop hook: nudge the model to capture a durable learning -- once per turn.

Default (Claude Code): writes the reminder to stderr and exits 2, which feeds it
back to the model. With `--codex` (OpenAI Codex Stop hook): prints a
{"decision":"block","reason":...} object on stdout instead -- Codex's documented
way to push a continuation prompt (additionalContext is not supported on Stop).
Both honor `stop_hook_active` so the nudge fires once per stop, never loops.
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
    codex = "--codex" in sys.argv[1:]
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    if data.get("stop_hook_active"):
        return 0  # already nudged for this stop; don't loop
    if codex:
        print(json.dumps({"decision": "block", "reason": REMINDER}))
        return 0  # Codex reads the continuation prompt from stdout JSON
    sys.stderr.write(REMINDER)
    return 2  # Claude: stderr feedback + exit 2


if __name__ == "__main__":
    sys.exit(main())
