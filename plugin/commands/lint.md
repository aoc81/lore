---
description: Run the learnings freshness linter (file-ref check; --report drift; --index regen)
argument-hint: "[--report | --index | --strict]"
allowed-tools: Bash
---

Run the lore freshness linter against this project and show its output verbatim.

Resolve a Python interpreter and run the plugin's `verify_refs.py`, passing through
any arguments the user supplied:

```sh
PY=$(command -v python3 || command -v python || command -v py)
"$PY" "${CLAUDE_PLUGIN_ROOT}/scripts/verify_refs.py" $ARGUMENTS
```

Modes: no args = file-reference existence check · `--report` = git drift triage
(entries whose code changed since they were verified) · `--index` = regenerate the
store README · `--strict` = exit non-zero on actionable issues (useful in CI).

After running, briefly summarize what it found and suggest the next step (fix a path,
mark an entry `superseded`, or run `/lore:sweep` to semantically re-verify).
