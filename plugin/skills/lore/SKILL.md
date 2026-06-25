---
name: lore
description: Capture a reusable learning after finishing a task so the next task is easier. Use at the end of a non-trivial task, after fixing a tricky bug, after a non-obvious decision, or when the user says "/lore:capture", "capture this", or "remember this".
---

# Lore — capture guidance

The idea: **each unit of work should make the next one
easier.** After solving something non-obvious, spend a minute recording it in a
durable, searchable place so it is never re-discovered from scratch. This is the
*write* half of the loop; the recall hook is the *read* half.

## Act, don't ask

When something clears the gate below, **capture it in the same turn — invoke the
skill, write the file, and report what you did.** Capturing is an action you
take, not one you offer: don't ask permission, don't float it as a "by the way"
or a follow-up, and don't end the turn with a qualifying learning uncaptured.
**Always end with exactly one lore-check line** (see [Output](#output)) so the
check is visible every turn — the capture line when something clears the gate, or
`Lore check — no durable learning this turn.` when nothing does; never end
silently. The only thing worth asking is *where* a borderline item belongs —
never *whether* to capture it.

## When to use

- You fixed a bug whose root cause was not obvious from the symptom.
- You made a design/tooling decision with non-obvious rationale.
- You hit a gotcha (an API quirk, a build/deploy trap, a config footgun) that cost real time.
- You learned how the user wants to work (a preference/convention worth keeping).
- The user says `/lore:capture`, "capture this", or "remember this".

If none apply, capture nothing — but still emit the one-line `Lore check — no
durable learning this turn.` verdict (see [Output](#output)). Do **not**
manufacture a learning just to produce output.

## The gate — capture only if BOTH are true

1. **Non-obvious** — not derivable from the code, git history, or existing docs.
   Someone reading the repo cold would not already know it.
2. **Reusable** — it will plausibly matter again on a future task, not just in this
   one conversation.

Skip: things the repo already records (what a function does, a fix git blame
explains); one-off facts; restatements of existing docs (update those instead).

## Never write a secret into a learning

The store is **committed and pushed by default**, and you capture autonomously —
so a learning is effectively *published the moment you write it*. Never paste a
secret, credential, API key, token, private key, connection string, or personal
data into an entry. **Reference it, don't quote it**: write "the prod DB
password (in the team vault / 1Password)", never the value itself. The same goes
for a real bug repro — describe the shape of the input, not a live token. A
pre-push secret scan is the backstop, but the entry should never contain the
secret in the first place. If a learning can't be told without a secret, it
doesn't belong in the store.

## Steps

1. **Classify the track** — *bug* (a concrete failure diagnosed and fixed — capture
   the trap, not just the patch) or *knowledge* (a pattern, decision, or constraint).
2. **Overlap check** — before writing, run the deterministic overlap check so you
   UPDATE an existing entry instead of duplicating it:
   ```sh
   PY=$(command -v python3 || command -v python || command -v py)
   R="${CLAUDE_PLUGIN_ROOT:+${CLAUDE_PLUGIN_ROOT}/scripts/}recall.py"
   [ -f "$R" ] || R="$HOME/.codex/lore/recall.py"   # Codex install location
   "$PY" "$R" --query "<draft title + tags>"
   ```
   It lists the top existing entries by the same scorer the recall hook uses.
   High overlap with a listed entry → update it in place. Low/none → new file.
3. **Write one file** — `<storeDir>/<category>/<kebab-slug>.md` (no date in the filename).
4. **Refresh the index** — run `/lore:lint --index` so the store README lists it.

`storeDir` defaults to `learnings/` (override in `.lore.json`). `category` is
free-form — reuse an existing folder name when one fits, otherwise add one.

## Frontmatter

```markdown
---
title: <short, specific>
date: YYYY-MM-DD          # today, absolute
track: bug | knowledge
category: <free-form, e.g. build, ci, api, frontend, infra>
tags: [k1, k2, k3]        # recall matches the prompt against title + tags — use words a future prompt would
files: [path/to/code.ext] # the code this is about; the linter checks these still exist
status: current
verified: YYYY-MM-DD       # optional; set when you confirm the claim against current code
---
```

## Body — bug track

`## Problem` · `## Root Cause` · `## What Didn't Work` · `## Solution` · `## Prevention`

## Body — knowledge track

`## Context` · `## Guidance` · `## Why This Matters` · `## When To Apply`

## Authoring for low drift

- Reference code by **stable symbol** (function / class / constant), not line numbers — line numbers rot on the next edit.
- Keep `files:` complete and accurate — it's the surface the linter checks; code referenced only in prose escapes it.
- Tags are how recall finds the entry: include the vocabulary a future prompt would use.

## When the code changes later

- Claim still holds, or a fixed bug with prevention documented → leave `status: current` (it's now a regression guard).
- Referenced code removed or architecture inverted → set `status: superseded`, add a top banner pointing to the new authority, and keep the transferable principle. The recall hook automatically down-ranks and flags superseded entries so they're never read as live guidance.

## Output

**Always** end every turn with exactly one lore-check line — this is the visible
heartbeat, never skip it even when nothing was captured:

- `Lore — captured: learnings/ci/cache-key-includes-lockfile-hash.md (bug)`
- `Lore — updated: learnings/api/pagination-cursor-opaque.md`
- `Lore check — no durable learning this turn.`

## Anti-patterns

- A "learning" that just narrates what you did this session.
- Duplicating an entry instead of updating it.
- Dumping code that git already captures, with no *why*.
- Capturing a generic fact the model already knows.
