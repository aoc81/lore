---
description: Capture a durable learning from the current work into the store
argument-hint: "[what to capture, optional]"
---

Apply the **lore** skill to capture a learning from the current work now.

$ARGUMENTS

Follow the skill exactly:
- Apply the gate — capture only if the learning is BOTH non-obvious AND reusable.
- Do the overlap check first; if a similar entry exists, UPDATE it instead of duplicating.
- Write one file under the store directory (default `learnings/`, or `.lore.json`
  `storeDir`) using the frontmatter schema and the bug/knowledge body template.
- Run the secret scanner on the file you just wrote (the skill shows the command)
  and fix any finding before ending the turn.
- Then refresh the index by running the linter with `--index`.

If nothing from this session clears the gate, say so plainly and stop — do not
manufacture a learning.
