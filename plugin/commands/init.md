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
3. Ask the user whether to install the **pre-push hook**. It does two things: a
   **blocking secret scan** (aborts the push if a learning quotes a likely key,
   token, or credential — the store is published by default, so this is the guard
   that keeps an auto-captured secret from leaking) and a **non-blocking freshness
   check** (warns when a learning references a deleted file). If yes:
   - Create `${CLAUDE_PROJECT_DIR}/.lore/` and copy `${CLAUDE_PLUGIN_ROOT}/scripts/scan_secrets.py`,
     `${CLAUDE_PLUGIN_ROOT}/scripts/verify_refs.py`, and `${CLAUDE_PLUGIN_ROOT}/scripts/_common.py`
     into it (so the hook is self-contained and survives plugin updates — the plugin's
     own dir is an ephemeral cache).
   - Stamp the copies: read `version` from `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json`
     and write it (just the version string) to `${CLAUDE_PROJECT_DIR}/.lore/VERSION`.
     The linter compares this stamp against the running plugin and tells the user to
     re-run `/lore:init` when the copies fall behind. (If `.lore/` already exists from
     a previous init, refresh the three scripts and the VERSION stamp.)
   - Install the hook: if the repo has no custom `core.hooksPath` and no existing
     `pre-push`, copy `${CLAUDE_PLUGIN_ROOT}/scripts/pre-push` to `.git/hooks/pre-push`
     and make it executable (`chmod +x`). If a custom `core.hooksPath` is set OR a
     `pre-push` already exists, do NOT overwrite — show the user the one-line snippet
     to add to their existing hook instead.
4. Ask the user whether to install the **CI guard** — `.github/workflows/lore.yml`,
   copied from `${CLAUDE_PLUGIN_ROOT}/templates/lore-ci.yml`. Why it matters: the
   pre-push hook in step 3 only protects **this** machine, so a teammate who never
   ran `/lore:init` can still push a secret into the store. The workflow runs the
   same two scripts from the committed `.lore/` copies on every PR, so nobody else
   has to install anything.
   - Only offer it when the repo has a GitHub remote (`git remote -v`) — otherwise
     mention the file exists and move on.
   - It requires the `.lore/` copies from step 3. If the user declined the pre-push
     hook, still copy the three scripts + `VERSION` into `.lore/` (they're what CI
     runs), and say so.
   - Never overwrite an existing `.github/workflows/lore.yml` — show the diff-worthy
     parts and let the user merge.
5. If `.lore.json` defines `personalStoreDir` (the private store recall reads
   alongside the team one), create that directory. When it resolves **inside** the
   repo, also add it to `.gitignore` — an un-ignored "private" store is a published
   store. Do not create anything when the key is absent; the personal store is opt-in.
6. Print a summary: store path, files created, whether the hook was installed.
   Remind the user that learnings are **committed and pushed by default (team-shared)**
   — so treat the store as published: never paste a secret/credential/PII into a
   learning (reference it, don't quote it). To keep the store private instead, add the
   store directory to `.gitignore`. The pre-push secret scan is a backstop, not a
   substitute for not writing secrets in the first place.
