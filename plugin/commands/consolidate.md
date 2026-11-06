---
description: Distil the store's entries into short per-topic skills, without losing recall
argument-hint: "[category] [--dry-run]"
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

Consolidate this project's learnings into per-topic **skills**: short summaries that
load on demand, so a store of hundreds of entries becomes something the agent can
actually carry. The entries stay where they are.

## Why this is additive, not a replacement

A store grows past the point where anyone reads it, and recall can only ever surface
`maxRecall` entries per prompt. A per-topic skill fixes both: one page, loaded when
the topic comes up. What it cannot do is replace the entries:

- A 500-word summary holds the rule. The entry holds the **root cause**, the repro,
  what didn't work, and the `files:` that make the gotcha appear **the moment you edit
  that code**. None of that survives the summary.
- Recall matches entries on `title` + `tags` and files on `files:`. Deleting the
  entries deletes the index — the lesson is still in git history, and nothing will
  ever surface it again.

So: **write the skill, mark the entries, delete nothing.** The only entries this
command will even propose deleting are ones already `superseded`/`obsolete` *and*
already absorbed. Verify the claim with the parity check in step 6 — don't take it
on trust.

## Preflight

`consolidatedDirs` must be set, or recall will not see a single skill you write:

```json
{ "consolidatedDirs": [".claude/skills/lore-*"], "consolidatedMaxWords": 500 }
```

Check it and read the worklist (pass `$ARGUMENTS` through — a bare category name
restricts the plan to it):

```sh
PY=$(command -v python3 || command -v python || command -v py)
C="${CLAUDE_PLUGIN_ROOT}/scripts/consolidate.py"
"$PY" "$C" ${ARGUMENTS:+--category "$ARGUMENTS"}
```

If `consolidatedDirs` is unset, show the user the one-line config and stop. Adding it
is their decision, not yours — it changes what every future prompt loads.

## Steps

1. **Baseline recall.** Before writing anything:

   ```sh
   "$PY" "$C" --snapshot .lore-recall-before.json
   ```

   This records what recall answers for every entry's own title+tags, which is the
   sharpest probe that exists for each entry. Keep the file out of the commit.

2. **Group by topic.** Work from the plan's groups (store categories), one topic per
   turn for a large store. Read the entries in the group — the bodies, not just the
   titles. Reuse an existing skill when the plan names one; otherwise create
   `.claude/skills/lore-<topic>/SKILL.md`.

3. **Write the skill.** Frontmatter, and all four fields matter:

   ```markdown
   ---
   name: lore-<topic>
   description: <what this covers, and when to load it — recall matches prompts against this line>
   tags: [<the union of the absorbed entries' tags>]
   files: [<the union of their files: refs — exact paths, dir prefixes, globs>]
   ---
   ```

   `tags:` and `files:` are **not optional decoration**. `tags:` is how a prompt
   reaches the skill; `files:` is how it reaches edit-time recall. A skill with no
   `files:` is invisible when the agent edits the code it describes, and the plan
   flags that. Keep both lists honest — union the real values, don't invent refs.
   (Claude Code reads only `name` and `description` from a skill; the extra keys are
   lore's index and are ignored by the loader.)

   Body: one bullet per rule, each with **what** and **why**, commands and thresholds
   verbatim, and a link to the entry it came from — a relative markdown link to the
   entry file, so the reader can get the full story and `/lore:lint` can verify the
   citation. **That link is load-bearing**, not a courtesy: recall only lets a skill
   stand in for an entry whose path or slug appears in the skill's body, and
   `--mark` refuses the entries you left uncited. If a rule is too compressed to
   name its source, it is too compressed to replace it. Stay under `consolidatedMaxWords` (500 by default); past it, **split by
   sub-topic** into a second skill with its own sharp `description` rather than
   writing 900 words nobody loads. Prioritize secret, CI, and deploy rules.

4. **Reconcile against the code.** A rule you cannot still verify does not go in the
   skill as live guidance. If the code moved, either fix the entry first or mark the
   entry `superseded` and leave the rule out.

5. **Mark the entries you absorbed** — never edit their frontmatter by hand:

   ```sh
   "$PY" "$C" --mark lore-<topic> learnings/<topic>/<a>.md learnings/<topic>/<b>.md
   ```

   This adds `consolidated_into:` and changes nothing else. The stamp tells recall
   it may hide the entry behind the skill, so **`--mark` refuses an entry the
   skill's body does not cite** — write the rule and its link first (step 3), then
   mark. That order is not bureaucracy: an entry stamped into a skill that never
   mentions it is a lesson recall will suppress in favour of a summary that does
   not contain it.

   Mark **one topic at a time, from what you actually wrote.** Never bulk-mark by
   guessing which skill a note belongs to — a scorer can match a note's words to a
   skill's name while the skill's body says nothing about it. `--force` exists for
   a citation written in a form this can't see; using it means you checked by hand.

6. **Prove recall survived.** This is the gate, not a formality:

   ```sh
   "$PY" "$C" --compare .lore-recall-before.json
   ```

   It checks **both** surfaces and exits non-zero if either lost something:

   - **prompt** — every baseline query still reaches its entry, or a skill that
     *cites* it. A skill answering without citing the entry counts as a loss.
   - **edit** — for every path an entry names in `files:`, that entry still
     surfaces within `maxRecall` when the path is edited.

   The second one is the half no retrieval benchmark can see, and the half that
   dies quietly: asking only "does anything still fire" passes while every entry
   has been displaced by a skill carrying a union of refs. **Do not commit while it
   reports a regression.** Report the numbers to the user verbatim, including how
   many (entry, path) pairs the edit check actually examined — a check that
   examined none must not be read as a pass.

7. **Journal the pass.**

   ```sh
   "$PY" "$C" --log "<one or two sentences: which topics, which skills, what you left>"
   ```

   Appends a dated block to `<storeDir>/WEEKLY_LOG.md`, newest first, with the
   corpus's **total body words** and the change since the last pass. Read that
   number: a large drop on a first pass is the duplication going, but a large
   drop on a later one means the pass **rewrote** the summaries instead of
   extending them — and that is the one failure every other number here hides,
   since the skills still look tidy, the entries are still marked, and recall
   parity still passes because the entries never left. Consolidate as a delta:
   add this week's rules to the existing skill, don't regenerate it.

8. **Lint and report.** Run `/lore:lint` (it checks `consolidated_into:` targets
   exist and that each skill's entry links still resolve), then tell the user: skills
   written or updated, entries marked, word counts against the limit, the parity
   numbers, and anything you deliberately left out of a skill.

## Pruning

Run it, read it, propose it — never delete on your own initiative:

```sh
"$PY" "$C" --prune
```

It lists only entries that are both not-live and already absorbed, with the `git rm`
command. Show the user the list and let them decide. A `current` entry is never a
prune candidate, however thoroughly it has been summarized.

## Do not

- Delete or move entries, or strip their `tags:`/`files:` — that is the recall index.
- Commit with `--compare` reporting a regression.
- Let a skill past the word limit instead of splitting it.
- Write a skill with no `tags:`/`files:` and call the topic consolidated.
- Paste a secret into a skill: skills are committed, exactly like entries.
