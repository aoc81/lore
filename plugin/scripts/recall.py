#!/usr/bin/env python3
"""Surface relevant learnings from the project's store.

As a `UserPromptSubmit` hook (no args, hook JSON on stdin): tokenize the prompt,
match tokens against each learning's frontmatter (title + tags only -- never the
body), and print the top matches as `additionalContext`. Emits nothing when
nothing matches.

As a CLI (`recall.py --query "<text>"`): print the same ranked matches in plain
text -- used as the capture-time overlap check so a new learning UPDATES an
existing entry instead of creating a near-duplicate, and by `/lore:search`.

As a `PreToolUse` hook (`recall.py --pretool`, tool JSON on stdin): when about to
Edit/Write a file, surface learnings whose frontmatter `files:` covers that path
(exact, directory prefix `dir/`, or glob) -- edit-time recall, so a gotcha shows
up exactly when you touch the code. Silent unless a learning names the target.
An entry already surfaced this way is not re-injected for the rest of the
session (a 10-edit refactor injects it once, not ten times); the session id and
the entries shown are kept in `.git/lore-recall-seen.json`, local only.

Matching is word-boundary, not substring: an exact word hit scores 2, a >=4-char
prefix overlap (stemming-ish: "test"/"testing") scores 1, and an entry needs a
score of 2 to surface at all -- so "auth" never matches "author" and one weak
prefix hit never injects noise. Short prompt words (2-3 chars: "ci", "api",
"aws") match only EXACT tags, where they are curated vocabulary. Tokens are
Unicode and diacritic-folded, so non-English prompts tokenize correctly and
"autenticación" matches a tag written "autenticacion".

Entries whose status is superseded/obsolete/deprecated stay matchable (their
transferable principle is still useful) but get a 1-point rank penalty and are
flagged [SUPERSEDED] so they are never read as live guidance.

Each surfaced entry also carries cheap freshness flags so a possibly-stale
learning is never trusted blindly: a `current` entry that points at a
now-deleted file is flagged, and a long-unverified entry shows its age. The
costly git-drift check stays in verify_refs.py (--report), out of the hook.

Every surfaced entry is also appended to `.git/lore-recall.log` (local only,
never committed -- it lives inside .git) so `/lore:stats` can report which
entries actually get used and which never surface.
"""
import datetime
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (find_project_dir, fold, iter_entries,  # noqa: E402
                     load_config, norm_rel, ref_exists, ref_matches, words)

# Generic words that would over-match. Domain words are intentionally absent.
# Entries are diacritic-folded (see _common.fold), so accented fillers are
# listed in their folded form ("tambien", not "también").
STOP = {
    "this", "that", "with", "from", "have", "your", "they", "them", "then",
    "than", "what", "when", "which", "where", "while", "about", "into", "over",
    "under", "also", "just", "like", "make", "made", "need", "want", "some",
    "more", "most", "very", "here", "there", "their", "been", "being", "does",
    "done", "using", "used", "still", "only", "will", "would", "should",
    "could", "please", "again", "same", "such", "each", "both", "file", "files",
    "code", "work", "works", "working", "thing", "things", "look", "looking",
    "check", "perhaps", "maybe", "sort", "without", "because", "after",
    "before", "these", "those",
    # Spanish fillers -- without these, "para"/"este"/"quiero" reach the scorer
    # and prefix-match real vocabulary ("para" -> "parameters").
    "para", "pero", "este", "esta", "esto", "estos", "estas", "eso", "esos",
    "esas", "como", "cuando", "donde", "cual", "cuales", "quien", "quienes",
    "porque", "aunque", "mientras", "entonces", "ademas", "segun", "desde",
    "hasta", "entre", "sobre", "sino", "tambien", "ahora", "luego", "aqui",
    "alli", "algun", "alguna", "algunos", "algunas", "cada", "otro",
    "otra", "otros", "otras", "mismo", "misma", "mismos", "toda",
    "todas", "solo", "muy", "mas", "bien", "siempre", "nunca",
    "quiero", "quieres", "quiere", "necesito", "necesita", "necesitas",
    "puede", "puedo", "podemos", "poder", "debe", "debes", "deberia",
    "hacer", "hace", "hacen", "haciendo", "tiene", "tienen", "tener",
    "estan", "esten", "seria", "sido", "sean", "usar", "usando", "usado",
    "mirar", "revisar", "funciona", "funcionar", "archivo", "archivos",
    "codigo", "cosa", "cosas", "favor", "gracias",
    # Deliberately NOT stopped: "todo"/"todos"/"algo" are Spanish fillers but
    # also real English/domain tokens (TODO comments, "algo"). Add them per
    # project via `stopWords` in .lore.json if they hurt more than they help.
}

# An entry must reach this score to surface (one exact word, or two prefixes).
MIN_SCORE = 2

# Titles come from a store that may be shared/PR-reviewed by others and are
# injected into context verbatim, so they are capped and stripped of control
# characters -- recall's no-bodies rule loses its point if a `title:` can carry
# an arbitrary payload. See the Security section of the README.
_TITLE_MAX = 140
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _months_since(iso):
    """Whole months between an ISO date and today; None if unparseable."""
    try:
        d = datetime.date.fromisoformat(iso)
    except ValueError:
        return None
    today = datetime.date.today()
    return (today.year - d.year) * 12 + (today.month - d.month)


def freshness_flags(e, project, cfg):
    """Cheap staleness signals for a surfaced entry (no git, no LLM).

    - A `current` entry whose `files:` lists a now-deleted path is a strong
      stale signal (the claim's anchor is gone) -> flag it.
    - An entry not verified in >= `staleAfterMonths` shows its age, so it is
      not trusted blindly. `date:` is the stamp when `verified:` is absent.

    Both thresholds come from `cfg`, so a project that redefines
    `staleStatuses` or `staleAfterMonths` gets the same answer everywhere.
    """
    flags = []
    if e["status"] not in set(cfg["staleStatuses"]):
        missing = [f for f in e["files"] if f and not ref_exists(project, f)]
        if missing:
            flags.append("! refs a deleted file")
    stamp = e["verified"] or e["date"]
    months = _months_since(stamp) if stamp else None
    if months is not None and months >= cfg["staleAfterMonths"]:
        label = "verified" if e["verified"] else "written"
        flags.append(f"{label} {months}mo ago")
    return flags


def score_entry(tokens, short, e):
    """Word-boundary score of one entry against prompt tokens.

    tokens: >=4-char prompt words (exact hay word = 2, >=4-char prefix
    overlap = 1). short: 2-3 char prompt words, matched only against exact
    tag words -- tags are curated, so "ci"/"api"/"aws" stay findable without
    letting short common words over-match titles.
    """
    hay_words = set(words(e["title"] + " " + " ".join(e["tags"])))
    if not hay_words:
        return 0
    long_hay = [w for w in hay_words if len(w) >= 4]
    score = 0
    for t in tokens:
        if t in hay_words:
            score += 2
        elif any(w.startswith(t) or t.startswith(w) for w in long_hay):
            score += 1
    if short:
        tag_words = set(words(" ".join(e["tags"])))
        score += sum(2 for s in short if s in tag_words)
    return score


def stop_words(cfg):
    """Built-in stop words plus any `stopWords` from `.lore.json` (folded)."""
    return STOP | {fold(w) for w in cfg["stopWords"] if w.strip()}


def rank_matches(text, store, cfg):
    """Rank store entries by title+tags word overlap with `text`.

    Returns [(stale_bool, entry), ...] best-first, capped to maxRecall.
    Same scorer for the prompt hook and the capture-time overlap check.
    """
    stop = stop_words(cfg)
    toks = words(text)
    tokens = {w for w in toks if len(w) >= 4 and w not in stop}
    short = {w for w in toks if 2 <= len(w) < 4 and w not in stop}
    if not tokens and not short:
        return []
    stale_set = set(cfg["staleStatuses"])
    found = []
    for e in iter_entries(store):
        score = score_entry(tokens, short, e)
        if score >= MIN_SCORE:
            stale = e["status"] in stale_set
            # rank: effective score (stale -1), current-before-stale on ties
            found.append((score - (1 if stale else 0), 1 if stale else 0,
                          stale, e))
    found.sort(key=lambda r: (-r[0], r[1]))
    return [(stale, e) for _eff, _s, stale, e in found[: cfg["maxRecall"]]]


def match_by_file(target_rel, store, cfg):
    """Learnings whose frontmatter `files:` covers `target_rel`, best-first.

    A ref matches as an exact path, a directory prefix (`src/auth/`), or a
    glob (`src/auth/*.py`). The match key is the file you're about to edit,
    not prompt tokens -- this powers edit-time (PreToolUse) recall.

    Current entries sort before stale ones (`staleStatuses` from cfg, same as
    the prompt scorer) so that truncating to `maxRecall` can never drop a live
    learning in favour of a superseded one; ties keep store order.
    """
    stale_set = set(cfg["staleStatuses"])
    matches = []
    for e in iter_entries(store):
        if any(ref_matches(f, target_rel) for f in e["files"] if f):
            matches.append((e["status"] in stale_set, e))
    matches.sort(key=lambda m: m[0])  # stable: False (current) first
    return matches


def _safe_title(title):
    """One-line, length-capped title -- injected content, so treated as data."""
    t = " ".join(_CTRL.sub(" ", str(title)).split())
    if len(t) > _TITLE_MAX:
        t = t[:_TITLE_MAX - 3].rstrip() + "..."
    return t


def _format_entry(e, stale, project, cfg):
    rel = e["path"].relative_to(project).as_posix()
    bits = []
    if stale:
        bits.append("SUPERSEDED -- apply the principle, not the file/code refs")
    bits.extend(freshness_flags(e, project, cfg))
    tag = f"  [{' | '.join(bits)}]" if bits else ""
    return f"- {rel} - {_safe_title(e['title'])}{tag}"


# --- local usage telemetry ---------------------------------------------------
_LOG_MAX_BYTES = 262144
_LOG_KEEP_LINES = 1500


def log_recall(project, kind, entries):
    """Append surfaced entries to `.git/lore-recall.log` (never committed).

    Best-effort and silent: telemetry must never break the hook. Skipped when
    `.git` is not a directory (bare repos, worktrees, no git).
    """
    try:
        git_dir = Path(project) / ".git"
        if not git_dir.is_dir():
            return
        log = git_dir / "lore-recall.log"
        today = datetime.date.today().isoformat()
        lines = [
            f"{today}\t{kind}\t{e['path'].relative_to(project).as_posix()}\n"
            for e in entries
        ]
        if log.exists() and log.stat().st_size > _LOG_MAX_BYTES:
            tail = log.read_text(encoding="utf-8",
                                 errors="replace").splitlines(True)
            log.write_text("".join(tail[-_LOG_KEEP_LINES:]), encoding="utf-8")
        with log.open("a", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception:
        pass


# --- session dedupe for edit-time recall -------------------------------------
# PreToolUse fires on every Edit/Write, so a refactor that touches one file ten
# times would inject the same learning ten times. Remember what this session has
# already been shown (in .git, like the recall log -- local, never committed).
_SEEN_MAX = 400


def _seen_file(project):
    git_dir = Path(project) / ".git"
    return git_dir / "lore-recall-seen.json" if git_dir.is_dir() else None


def unseen_matches(project, session_id, matches):
    """Drop matches already surfaced at edit time in this session, and record
    the rest.

    Fails open: with no session id, no `.git`, or an unreadable/corrupt state
    file, every match is returned (a missed dedupe is noise; a wrongly dropped
    learning is a miss). A new `session_id` resets the state.
    """
    path = _seen_file(project)
    if not session_id or path is None:
        return matches
    try:
        seen = []
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("session") == session_id:
                seen = [s for s in data.get("seen") or [] if isinstance(s, str)]
        known = set(seen)
        fresh, added = [], []
        for stale, e in matches:
            rel = e["path"].relative_to(project).as_posix()
            if rel in known:
                continue
            known.add(rel)
            added.append(rel)
            fresh.append((stale, e))
        if added:
            path.write_text(json.dumps({
                "session": session_id,
                "seen": (seen + added)[-_SEEN_MAX:],
            }), encoding="utf-8")
        return fresh
    except (OSError, ValueError, TypeError):
        return matches


def run_hook():
    raw = sys.stdin.read()
    if not raw.strip():
        return
    try:
        data = json.loads(raw)
    except ValueError:
        return
    prompt = str(data.get("prompt") or "")
    if not prompt.strip():
        return
    project = find_project_dir(data)
    cfg = load_config(project)
    store = project / cfg["storeDir"]
    if not store.is_dir():
        return
    matches = rank_matches(prompt, store, cfg)
    if not matches:
        return
    log_recall(project, "prompt", [e for _s, e in matches])
    lines = [_format_entry(e, stale, project, cfg) for stale, e in matches]
    ctx = (
        "Possibly-relevant prior learnings (Read a file only if it applies to "
        "this task; otherwise ignore):\n" + "\n".join(lines)
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": ctx,
        }
    }))


def run_query(text):
    project = find_project_dir()
    cfg = load_config(project)
    store = project / cfg["storeDir"]
    if not store.is_dir():
        print("No learnings store found.")
        return
    matches = rank_matches(text, store, cfg)
    if not matches:
        print("No related learnings found -- looks new; create a fresh entry.")
        return
    print("Related existing learnings (overlap check before you write):")
    for stale, e in matches:
        print(_format_entry(e, stale, project, cfg))
    print("\n-> If one is the SAME problem/area, UPDATE it instead of creating a")
    print("   near-duplicate. Only add a new file if none truly overlaps.")


def run_pretool():
    raw = sys.stdin.read()
    if not raw.strip():
        return
    try:
        data = json.loads(raw)
    except ValueError:
        return
    if (data.get("tool_name") or "") not in ("Edit", "Write", "MultiEdit",
                                             "NotebookEdit"):
        return
    tinput = data.get("tool_input") or {}
    fp = tinput.get("file_path") or tinput.get("notebook_path") or ""
    if not fp:
        return
    project = find_project_dir(data)
    cfg = load_config(project)
    store = project / cfg["storeDir"]
    if not store.is_dir():
        return
    try:
        rel = Path(fp).resolve().relative_to(project.resolve()).as_posix()
    except (ValueError, OSError):
        rel = norm_rel(fp)
    matches = match_by_file(rel, store, cfg)
    if not matches:
        return  # stay silent unless a learning names this file
    # sorted current-first by match_by_file, so the cap drops stale entries
    # first; then anything this session already saw is suppressed.
    matches = unseen_matches(project, str(data.get("session_id") or ""),
                             matches[: cfg["maxRecall"]])
    if not matches:
        return  # already surfaced for this session -- don't repeat it
    log_recall(project, "edit", [e for _s, e in matches])
    lines = [_format_entry(e, stale, project, cfg) for stale, e in matches]
    ctx = (
        f"Lore -- learnings recorded about `{rel}` (consider before editing):\n"
        + "\n".join(lines)
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": ctx,
        }
    }))


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--query":
        run_query(" ".join(argv[1:]))
    elif argv and argv[0] == "--pretool":
        run_pretool()
    else:
        run_hook()


if __name__ == "__main__":
    main()
