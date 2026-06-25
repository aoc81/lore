---
description: Scan the learnings store for committed secrets (keys, tokens, credentials)
argument-hint: "[path ...]"
allowed-tools: Bash
---

Scan the lore store for likely secrets before it's shared, and show the output verbatim.

The store is committed and pushed by default, so a learning that quotes a real key
or token would be published. Run the plugin's `scan_secrets.py`:

```sh
PY=$(command -v python3 || command -v python || command -v py)
"$PY" "${CLAUDE_PLUGIN_ROOT}/scripts/scan_secrets.py" $ARGUMENTS
```

No args scans the configured store dir (default `learnings/`). Exit 0 = clean,
exit 1 = at least one likely secret.

If it flags something:
- **Real secret** → remove it from the learning (reference it, don't quote it), and
  **rotate** the credential, since it may already be in git history.
- **False positive** → add `lore:allow-secret` to that line, or a regex under
  `secretAllow` in `.lore.json`.

This is the same check the pre-push hook runs as a blocking guard.
