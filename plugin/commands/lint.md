---
description: Run the learnings freshness linter (file-ref check; --report drift; --dupes; --index regen)
argument-hint: "[--report | --dupes | --index | --strict]"
allowed-tools: Bash
---

Run the lore freshness linter against this project and show its output verbatim.

Resolve a Python interpreter and run the plugin's `verify_refs.py`, passing through
any arguments the user supplied:

```sh
PY=$(command -v python3 || command -v python || command -v py)
"$PY" "${CLAUDE_PLUGIN_ROOT}/scripts/verify_refs.py" $ARGUMENTS
```

Modes: no args = file-reference existence check (plus a note about any `.lore.json`
keys the hooks ignore — typos, wrong types) · `--report` = git drift triage
(entries whose code changed since they were verified) · `--dupes` = near-duplicate
triage (entry pairs with overlapping title/tags, e.g. parallel captures from
teammates, plus category-name variants) · `--index` = regenerate the store README ·
`--strict` = exit non-zero on actionable issues (useful in CI).

After running, briefly summarize what it found and suggest the next step (fix a path,
mark an entry `superseded`, or run `/lore:sweep` to semantically re-verify).
