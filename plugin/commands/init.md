---
description: Scaffold the lore store in this project (and optionally install the freshness git hook)
allowed-tools: Bash, Read, Write, Edit
---

Set up the lore store for THIS project. Do not invent any learnings —
this only scaffolds.

1. Determine the store directory: read `${CLAUDE_PROJECT_DIR}/.lore.json` key
   `storeDir` if that file exists, else default to `learnings`.
2. If the store directory does not exist, create it and seed it from the plugin templates:
   - `${CLAUDE_PLUGIN_ROOT}/templates/store-README.md` → `<storeDir>/README.md`
   - `${CLAUDE_PLUGIN_ROOT}/templates/_TEMPLATE.md` → `<storeDir>/_TEMPLATE.md`
   - `${CLAUDE_PLUGIN_ROOT}/templates/example-learning.md` → `<storeDir>/examples/example-learning.md`
   If it already exists, leave existing files untouched and just report what's there.
3. Ask the user whether to install the **non-blocking pre-push freshness hook**
   (warns when a learning references a deleted file). If yes:
   - Create `${CLAUDE_PROJECT_DIR}/.lore/` and copy `${CLAUDE_PLUGIN_ROOT}/scripts/verify_refs.py`
     and `${CLAUDE_PLUGIN_ROOT}/scripts/_common.py` into it (so the hook is self-contained
     and survives plugin updates — the plugin's own dir is an ephemeral cache).
   - Install the hook: if the repo has no custom `core.hooksPath` and no existing
     `pre-push`, copy `${CLAUDE_PLUGIN_ROOT}/scripts/pre-push` to `.git/hooks/pre-push`
     and make it executable (`chmod +x`). If a custom `core.hooksPath` is set OR a
     `pre-push` already exists, do NOT overwrite — show the user the one-line snippet
     to add to their existing hook instead.
4. Print a summary: store path, files created, whether the hook was installed.
   Remind the user that learnings are **committed by default (team-shared)** — to keep
   them private, add the store directory to `.gitignore`.
