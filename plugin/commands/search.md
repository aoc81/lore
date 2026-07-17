---
description: Search the learnings store by title/tags (same scorer the recall hook uses)
argument-hint: "<what you're looking for>"
allowed-tools: Bash, Read
---

Search the lore store for learnings matching the user's query, on demand —
without waiting for the recall hook to guess.

Resolve a Python interpreter and run the plugin's `recall.py --query`:

```sh
PY=$(command -v python3 || command -v python || command -v py)
"$PY" "${CLAUDE_PLUGIN_ROOT}/scripts/recall.py" --query "$ARGUMENTS"
```

It ranks entries by title+tags word overlap — the exact same scorer the
automatic recall hook uses, so this is also the honest way to test *why* a
learning does or doesn't surface.

Then:
- If matches were found, **Read the top one or two files** that look genuinely
  relevant and answer the user's question from them (cite the file paths).
- If nothing matched but the user is clearly looking for something that should
  exist, say so — and suggest better tags if you find the entry by other means
  (e.g. Grep over the store), since a miss here means recall would miss it too.
