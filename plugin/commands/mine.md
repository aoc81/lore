---
description: Bootstrap the store from git history — mine past fixes, reverts, and churn for learnings
argument-hint: "[--since <date>] [--max-commits N] [--top N] [path ...]"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

Fix the **cold start**: an empty store never surfaces anything, so recall looks
dead and nobody captures. The repo's history already holds the first entries —
mine it, then write the ones that clear the gate.

1. Get the ranked candidates (read-only, heuristic):
   ```sh
   PY=$(command -v python3 || command -v python || command -v py)
   "$PY" "${CLAUDE_PLUGIN_ROOT}/scripts/mine_history.py" $ARGUMENTS
   ```
   It scores commits on reverts, fix/bug vocabulary, bodies that explain a *why*,
   and file churn — and marks any candidate an existing learning may already
   cover. It also lists **churn hot spots**: files rewritten so often that the
   gotcha is probably undocumented, even with no fix commit attached.

2. For each candidate, in ranked order, **read the evidence before judging it**:
   ```sh
   git show --stat <sha>          # then git show <sha> for the diff itself
   ```
   The commit message alone is not enough — the *why* is usually in the diff.

3. Apply the **lore gate** to each one, exactly as in the skill: capture only if
   it is BOTH non-obvious (not derivable from reading the code) AND reusable
   (it will plausibly matter again). Most commits fail this. Specifically skip:
   - a fix whose diff makes the cause self-evident,
   - anything `git blame` already explains as well as an entry would,
   - a "learning" that just restates the changelog.

4. Write the survivors via the **lore skill** — one file each, the normal
   frontmatter and body template, `date:` today, and `verified:` today only when
   you actually checked the claim against current code. Set `files:` to the paths
   that still exist (a mined commit may reference code since deleted — if the
   anchor is gone, either point at its replacement or skip the entry).
   - If a candidate showed an **overlap**, UPDATE that entry (add the new
     evidence, bump `verified:`) instead of creating a near-duplicate.
   - Mine **bug-track** entries from fix/revert commits, **knowledge-track**
     entries from hot spots and decision commits.

5. Scan and index once at the end:
   ```sh
   PY=$(command -v python3 || command -v python || command -v py)
   "$PY" "${CLAUDE_PLUGIN_ROOT}/scripts/scan_secrets.py"
   "$PY" "${CLAUDE_PLUGIN_ROOT}/scripts/verify_refs.py" --index
   ```
   A mined entry can quote a config line from an old diff — fix any finding
   before ending the turn.

6. Report a short table: candidate → captured (path) / updated (path) / skipped
   (one-line reason). Say plainly how many you skipped and why.

**Quality bar over volume.** Cap a single run at roughly **5–8 entries**; a
store full of thin, mined restatements is worse than an empty one, because
recall then surfaces noise and the agent learns to ignore it. If fewer than five
candidates clear the gate, write fewer — and say so. Re-run `/lore:mine` later
(with `--since` past the last run) to go deeper; the overlap marker keeps the
second pass from duplicating the first.

Scale to `$ARGUMENTS`: no args mines the last 400 commits repo-wide; pass paths
to focus on one subsystem, or `--since 2025-01-01` to bound the window.
