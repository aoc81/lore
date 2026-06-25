---
description: Show learnings store health — counts, drift backlog, stale-age, dangling links
argument-hint: ""
allowed-tools: Bash
---

Show a one-screen health snapshot of the learnings store.

Resolve a Python interpreter and run the plugin's `verify_refs.py --stats`
(read-only — it never writes):

```sh
PY=$(command -v python3 || command -v python || command -v py)
"$PY" "${CLAUDE_PLUGIN_ROOT}/scripts/verify_refs.py" --stats
```

After running, call out anything worth acting on and suggest the next step:

- **deleted file refs (current)** > 0 → run `verify_refs.py` (no args) for the
  list; fix the path or mark the entry `superseded`.
- a large **drift backlog** → `/lore:lint --report` to triage, then `/lore:sweep`
  to semantically re-verify the top entries.
- entries **not verified in >6mo** → candidates for a re-check + a fresh
  `verified:` stamp.

The dangling-`[[link]]` count is **soft**: a link to a not-yet-written learning
is an allowed forward-reference, not an error.
