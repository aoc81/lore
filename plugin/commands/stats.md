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
- **revisit due** > 0 → decisions taken "for now" whose `revisit:` date has come;
  `/lore:lint --report` lists them and `/lore:sweep` re-checks them.
- entries **not verified in >6mo** → candidates for a re-check + a fresh
  `verified:` stamp.
- **never surfaced** > 0 in recall activity → those entries' tags don't match how
  anyone prompts; improve the tags (or prune the entry). The activity data comes
  from `.git/lore-recall.log`, written locally by the recall hook — never committed.
- **surfaced ≥3x but never read** > 0 → the sharper signal: those entries keep
  winning the scorer and the agent still never opens them. The title/tags promise
  something the body doesn't deliver, so either rewrite the title to match the
  content or drop the entry. (A `reads: not tracked yet` line instead means the
  `PostToolUse` Read hook isn't running — reinstall the plugin to measure it.)
- a **consolidation** block → how many entries are consolidated and how many aren't,
  plus the skills' word counts. A skill **without `files:`** is invisible to
  edit-time recall for the code it describes — add the refs. A **missing skill** or
  a **skill citing a deleted learning** means the layer has rotted: run `/lore:lint`
  for the list. A large "not yet" count is the cue for `/lore:consolidate`.
- a **personal store** line → entries in the private `personalStoreDir`. They're
  recalled alongside the team store but never indexed, linted, or secret-scanned,
  because they're never pushed.
- **near-duplicate pairs** > 0 → run `/lore:lint --dupes` and merge true duplicates.
- a **version note** at the bottom → the `.lore/` pre-push hook copies are from an
  older plugin; re-run `/lore:init` to refresh them.
- a **hook warning** at the bottom → nothing scans the store for secrets before a
  push on this machine: no `pre-push` hook, one that does not run the scan (`/lore:init`
  never overwrites one it didn't write, and never installs into a custom
  `core.hooksPath`), or the hook's `.lore/scan_secrets.py` gone missing. An uninstalled
  hook can't report itself, so this is the only place it shows. It stops once
  `python .lore/scan_secrets.py --quiet || exit 1` is in the hook that runs — or in the
  pre-push stage of husky, pre-commit or lefthook when the hook is their shim. Also
  silent when a workflow runs the scan in CI, which covers teammates too, or when the
  project has no team store to publish.

The dangling-`[[link]]` count is **soft**: a link to a not-yet-written learning
is an allowed forward-reference, not an error.
