---
description: Semantically re-verify drifted learnings against the current code, then update them
argument-hint: "[category or max count, optional]"
allowed-tools: Bash, Read, Edit, Grep, Glob
---

Do a semantic freshness sweep of the learnings store.

1. Get the drift candidates:
   ```sh
   PY=$(command -v python3 || command -v python || command -v py)
   "$PY" "${CLAUDE_PLUGIN_ROOT}/scripts/verify_refs.py" --report
   ```
   Also run it with no args to catch entries referencing deleted files.
2. For the top candidates (biggest gap first), READ the learning and the code it
   references, then judge whether the claim still holds:
   - **Still accurate** → bump its `verified:` to today.
   - **Path moved, claim holds** → fix the `files:` / inline refs (prefer stable symbols over line numbers), keep `current`.
   - **Architecture changed / claim now wrong** → set `status: superseded`, add a top banner pointing to the new authority, keep the transferable principle.
   - **Bug fixed + prevention already documented** → leave `current` (it's a regression guard).
3. **Verify against the actual code before editing** — the drift signal is a heuristic (a file changed), not proof the claim is wrong. Do not trust it blindly.
4. Regenerate the index: `verify_refs.py --index`.
5. Summarize what you changed and what you deliberately left.

Scale effort to `$ARGUMENTS` (a category to focus on, or a max number of entries);
default to the top handful by gap.
