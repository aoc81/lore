# Learnings - knowledge store

One-fact-per-file record of non-obvious, reusable learnings for this project,
managed by the [`lore`](https://github.com/) Claude Code plugin.

- The **recall hook** surfaces relevant entries into context when you prompt, and again
  when the agent is about to edit a file one of them is about.
- The **capture skill** (`/lore:capture`, or automatically at the end of a task) adds new ones;
  `/lore:mine` proposes a first batch from this repo's git history.
- The **linter** (`/lore:lint`) checks freshness; `/lore:lint --index` regenerates this file into a category index.
- `/lore:stats` shows which entries actually earn their keep (surfaced vs actually read).

Add an entry by copying `_TEMPLATE.md` into a category folder. See `examples/` for a
sample, and run `/lore:lint --index` once you have real entries.

> **This store is committed and pushed by default — treat it as published.** Never
> quote a secret, credential, token, or PII in a learning; reference it instead. The
> pre-push hook runs a blocking secret scan (`/lore:scan`) as a backstop, and the
> optional CI workflow runs the same scan on every PR for teammates who don't have
> the hook installed.
>
> A learning about **how one person likes to work** belongs in the private
> `personalStoreDir` instead (see the plugin's README) — recall reads it too, but it
> never lands here.
