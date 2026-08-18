# Lore for OpenAI Codex

The same Lore — ambient recall + capture nudge + freshness — running on **OpenAI
Codex** instead of Claude Code. Codex shipped a Claude-compatible hooks system, so
this is a near 1:1 port: `recall.py`, `capture_check.py`, and the markdown store
are reused **unchanged** — only the install/packaging differ. (The Stop hook emits
the same `{"decision":"block",...}` object on both targets, so there is no
Codex-specific output mode.)

Codex's hook surface has since grown to cover `PreToolUse` and
`SessionStart(compact)`, so **edit-time recall and the post-compaction reminder now
work here too** — the two gaps this page used to list. One difference remains, and
it's a missing tool rather than a missing hook: see
[Caveats](#caveats--differences-from-claude-code).

## Why it ports cleanly

| Lore mechanism | Codex equivalent | Notes |
|---|---|---|
| Recall (`UserPromptSubmit`) | `UserPromptSubmit` hook → `hookSpecificOutput.additionalContext` | **Identical JSON contract** to Claude Code — `recall.py` runs verbatim. |
| Edit-time recall (`PreToolUse`) | `PreToolUse` hook, matcher `apply_patch\|Edit\|Write` → `additionalContext` | Codex reports every edit as **`apply_patch`** with the patch text in `tool_input.command` (no `file_path`), so `recall.py` reads the targets out of the `*** Update File:` headers — one patch can surface learnings for several files at once. |
| Post-compaction reminder (`SessionStart`) | `SessionStart` hook, matcher `compact` → `additionalContext` | Same event, same matcher, same payload as the Claude target. |
| Capture nudge (`Stop`) | `Stop` hook → `{"decision":"block","reason":...}` | Codex pushes the reminder as a continuation prompt; `capture_check.py` emits this on stdout — the same object Claude Code now uses. Guarded by `stop_hook_active` (no loop). |
| Capture guidance (skill) | Agent Skill (`SKILL.md`) | Installed to `~/.agents/skills/lore/`. |
| Freshness linter | `verify_refs.py` | Run directly: `python3 ~/.codex/lore/verify_refs.py [--report\|--stats\|--dupes\|--index\|--strict]`. |
| Cold-start mining | `mine_history.py` | Run directly: `python3 ~/.codex/lore/mine_history.py [--since …]`, then write the entries via the skill (the Claude target wraps this as `/lore:mine`). |
| Secret scan | `scan_secrets.py` | Run directly: `python3 ~/.codex/lore/scan_secrets.py` before sharing the store; `--ci` installs the same check as a PR gate. |
| Store | `learnings/` markdown | Per-project, tool-agnostic — created on first capture or via `--store`. Plus the optional private `personalStoreDir`, read by recall the same way. |

## Install

Clone the repo and run the installer:

```sh
git clone https://github.com/aoc81/lore
cd lore
python3 codex/install.py
```

This (user-scoped, once per machine):
- copies the core scripts to `~/.codex/lore/`,
- writes `~/.codex/hooks.json` registering **four** hooks — prompt recall, edit-time
  recall, the capture nudge, and the post-compaction reminder — with your
  **interpreter path baked in** (no `python3`/`python`/`py` ambiguity, no wrapper),
- installs the capture skill to `~/.agents/skills/lore/SKILL.md`,
- sets `[features] hooks = true` in `~/.codex/config.toml`.

Then **open Codex and run `/hooks`** to review and **trust** the new hooks — Codex
skips untrusted command hooks until you approve them (one time, by hash).

Per project, scaffold a store (optional — it's also created on first capture):

```sh
python3 codex/install.py --store      # creates ./learnings with a template + example
```

Per repo, add the team-wide CI guard (optional but recommended for a shared store —
a git hook only protects the machine it's on):

```sh
python3 codex/install.py --ci         # .lore/ script copies + .github/workflows/lore.yml
```

Commit both: the workflow runs the committed `.lore/` copies, so the PR check and
the local scan can never drift apart. Re-run `--ci` after upgrading Lore.

Uninstall: `python3 codex/install.py --uninstall` (leaves your stores and the
`[features]` flag untouched).

## How it behaves

- **Every prompt**, Codex runs `recall.py`, which matches your prompt against each
  learning's `title`+`tags` and injects the top matches as `additionalContext` —
  same ambient recall as on Claude Code, across both the team store and (when
  configured) `personalStoreDir`.
- **Every edit**, the `PreToolUse` hook surfaces any learning whose `files:` covers
  a path in the patch — once per entry per session, so a long refactor doesn't
  repeat itself.
- **After a compaction**, the `SessionStart(compact)` hook re-injects a short lore
  reminder, the moment an uncaptured learning would otherwise die with the context.
- **At the end of each turn**, `capture_check.py` returns a one-shot `LORE CHECK`
  reminder as a `decision:block` continuation prompt; if a durable learning came
  up, Codex invokes the `lore` skill to write it to `./learnings/`.
- The nudge honors `captureNudge` in `.lore.json` (`always` / `smart` / `off`).
  `smart` (the default) skips turns that used no tools; the detection reads the
  transcript referenced by the hook's `transcript_path` and **fails open** — if
  Codex doesn't provide one or the format differs, the nudge simply fires as before.

## Caveats / differences from Claude Code

- **Trust gate:** Codex won't run the hooks until you trust them via `/hooks`
  (security feature; expected). Trust is per-command, so re-approve after an upgrade
  — and after this version, which registers two hooks the earlier port didn't have.
- **`[features] hooks` default** is inconsistent across Codex docs, so the installer
  sets it explicitly to `true`.
- **No read telemetry.** The Claude target logs `kind=read` from a `PostToolUse`
  hook matched on the `Read` tool, which is how `/lore:stats` separates "surfaced"
  from "actually used". Codex has no `Read` tool — file reads go through `Bash` or
  an MCP server — so there is no reliable event to attribute a store read to, and
  stats on Codex reports surfacings only. Everything else is at parity.
- **No `/lore:*` slash commands.** Codex custom prompts are user-local and can't be
  namespaced/shared, so the Claude commands map to: install script (`init`), the
  `lore` skill + Stop hook (`capture`), and direct `verify_refs.py` /
  `mine_history.py` calls (`lint`, `mine`).
- **Config/paths:** `.lore.json` (`staleStatuses`, `staleAfterMonths`, `stopWords`, …)
  and the `learnings/` layout are identical to the Claude target — the [shared core](../plugin/scripts/) is the same code.

## What's shared vs Codex-specific

- **Shared (unchanged):** `plugin/scripts/{recall,capture_check,verify_refs,scan_secrets,mine_history,_common}.py`,
  `plugin/skills/lore/SKILL.md`, `plugin/templates/*` (incl. the CI workflow),
  the store format.
- **Codex-specific:** this folder — `install.py`, `hooks.example.json`, and this README.
