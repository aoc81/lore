# Lore for OpenAI Codex

The same Lore — ambient recall + capture nudge + freshness — running on **OpenAI
Codex** instead of Claude Code. Codex shipped a Claude-compatible hooks system, so
this is a near 1:1 port: `recall.py` and the markdown store are reused **unchanged**,
and only the install/packaging and the Stop-hook output format differ.

## Why it ports cleanly

| Lore mechanism | Codex equivalent | Notes |
|---|---|---|
| Recall (`UserPromptSubmit`) | `UserPromptSubmit` hook → `hookSpecificOutput.additionalContext` | **Identical JSON contract** to Claude Code — `recall.py` runs verbatim. |
| Capture nudge (`Stop`) | `Stop` hook → `{"decision":"block","reason":...}` | Codex pushes the reminder as a continuation prompt; `capture_check.py --codex` emits this. Guarded by `stop_hook_active` (no loop). |
| Capture guidance (skill) | Agent Skill (`SKILL.md`) | Installed to `~/.agents/skills/lore/`. |
| Freshness linter | `verify_refs.py` | Run directly: `python3 ~/.codex/lore/verify_refs.py [--report\|--index\|--strict]`. |
| Store | `learnings/` markdown | Per-project, tool-agnostic — created on first capture or via `--store`. |

## Install

From a clone of this repo:

```sh
python3 codex/install.py
```

This (user-scoped, once per machine):
- copies the core scripts to `~/.codex/lore/`,
- writes `~/.codex/hooks.json` registering recall + capture with your **interpreter
  path baked in** (no `python3`/`python`/`py` ambiguity, no wrapper),
- installs the capture skill to `~/.agents/skills/lore/SKILL.md`,
- sets `[features] hooks = true` in `~/.codex/config.toml`.

Then **open Codex and run `/hooks`** to review and **trust** the new hooks — Codex
skips untrusted command hooks until you approve them (one time, by hash).

Per project, scaffold a store (optional — it's also created on first capture):

```sh
python3 codex/install.py --store      # creates ./learnings with a template + example
```

Uninstall: `python3 codex/install.py --uninstall` (leaves your stores and the
`[features]` flag untouched).

## How it behaves

- **Every prompt**, Codex runs `recall.py`, which matches your prompt against each
  learning's `title`+`tags` and injects the top matches as `additionalContext` —
  same ambient recall as on Claude Code.
- **At the end of each turn**, `capture_check.py --codex` returns a one-shot
  `LORE CHECK` reminder; if a durable learning came up, Codex invokes the `lore`
  skill to write it to `./learnings/`.

## Caveats / differences from Claude Code

- **Trust gate:** Codex won't run the hooks until you trust them via `/hooks`
  (security feature; expected).
- **`[features] hooks` default** is inconsistent across Codex docs, so the installer
  sets it explicitly to `true`.
- **No `/lore:*` slash commands.** Codex custom prompts are user-local and can't be
  namespaced/shared, so the Claude commands map to: install script (`init`), the
  `lore` skill + Stop hook (`capture`), and direct `verify_refs.py` calls (`lint`).
- **Config/paths:** `.lore.json`, `staleStatuses`, and the `learnings/` layout are
  identical to the Claude target — the [shared core](../plugin/scripts/) is the same code.

## What's shared vs Codex-specific

- **Shared (unchanged):** `plugin/scripts/{recall,verify_refs,_common}.py`,
  `plugin/skills/lore/SKILL.md`, `plugin/templates/*`, the store format.
- **Codex-specific:** this folder — `install.py`, `hooks.example.json`, this README,
  and the `--codex` output mode added to `plugin/scripts/capture_check.py`.
