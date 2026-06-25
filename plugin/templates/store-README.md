# Learnings - knowledge store

One-fact-per-file record of non-obvious, reusable learnings for this project,
managed by the [`lore`](https://github.com/) Claude Code plugin.

- The **recall hook** surfaces relevant entries into context when you prompt.
- The **capture skill** (`/lore:capture`, or automatically at the end of a task) adds new ones.
- The **linter** (`/lore:lint`) checks freshness; `/lore:lint --index` regenerates this file into a category index.

Add an entry by copying `_TEMPLATE.md` into a category folder. See `examples/` for a
sample, and run `/lore:lint --index` once you have real entries.

> **This store is committed and pushed by default — treat it as published.** Never
> quote a secret, credential, token, or PII in a learning; reference it instead. The
> pre-push hook runs a blocking secret scan (`/lore:scan`) as a backstop.
