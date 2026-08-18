#!/usr/bin/env python3
"""Freshness tooling for the learnings store (stdlib only).

Modes:
  (default)   Existence check -- flag entries whose frontmatter `files:` paths no
              longer exist. `--strict` exits 1 on ACTIONABLE issues (a missing
              file on a non-stale entry; missing files on superseded entries are
              expected/informational). Also reports `.lore.json` keys the hooks
              silently ignore (a typo like `maxRecal`), which is invisible at
              hook time by design.
  --report    Drift triage -- for each current entry, use `git log` to find
              referenced files changed AFTER the entry's `verified:` (or `date:`)
              baseline, ranked by gap. Best candidates for a re-verify. Heuristic.
  --stats     Store-health summary -- counts by status/category, drift backlog,
              entries unverified for a long time, recall activity (from the local
              `.git/lore-recall.log`: surfaced vs actually READ, plus entries
              that keep surfacing and never get opened), near-duplicate count,
              and a soft dangling-link count. Also counts the private
              `personalStoreDir`, which no other mode touches.
  --dupes     Near-duplicate report -- entry pairs whose title+tags share enough
              vocabulary to be merge candidates, plus category names that look
              like variants of each other (ci vs CI vs build-ci).
  --index     Regenerate the store README index from entry frontmatter.

Default/--report/--stats/--dupes are read-only; --index rewrites the store README.
The `[[wiki-link]]` count in --stats is SOFT: a link with no target yet is an
allowed forward-reference (a topic not captured yet), never an error.

`files:` refs may be exact paths, directory prefixes (`src/auth/`), or globs
(`src/auth/*.py`) -- all modes understand the three forms.
"""
import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (DEFAULTS, config_issues, find_project_dir,  # noqa: E402
                     iter_entries, load_config, norm_rel, personal_store,
                     ref_exists, ref_matches, rel_path, stale_after_days,
                     words)

_DATE_LINE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Words too generic to count as duplicate-signal on their own.
_DUPE_STOP = {
    "the", "and", "for", "with", "not", "use", "when", "how", "why", "fix",
    "bug", "issue", "error", "problem",
}
_DUPE_MIN_SHARED = 3

# How often an entry must have surfaced before "never read" means anything.
# One or two surfacings with no read is normal; a dozen is a tagging problem.
_UNREAD_MIN = 3

SCRIPT_DIR = Path(__file__).resolve().parent


def _parse_date(s):
    parts = (s or "").split()  # tolerate a trailing "# comment"
    if not parts:
        return None  # empty git output (e.g. an untracked file) -> no date
    try:
        return datetime.strptime(parts[0], "%Y-%m-%d").date()
    except ValueError:
        return None


# --- .lore/ hook-copy version check ------------------------------------------

def plugin_version():
    """The plugin's own version, when running from the plugin tree (else None)."""
    manifest = SCRIPT_DIR.parent / ".claude-plugin" / "plugin.json"
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        return None


def version_warning(project):
    """Warn when the project's `.lore/` hook copies lag behind the plugin.

    `/lore:init` copies the pre-push scripts into `<repo>/.lore/` and stamps
    `.lore/VERSION`; those copies never auto-update. Returns a warning string
    or None. Silent when either side is unknown (old installs, .lore copy runs).
    """
    plug = plugin_version()
    stamp = Path(project) / ".lore" / "VERSION"
    if not plug or not stamp.is_file():
        return None
    try:
        local = stamp.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if local and local != plug:
        return (f"note: .lore/ hook scripts are from lore {local}, plugin is "
                f"{plug} -- re-run /lore:init to refresh the pre-push hook.")
    return None


def config_warning(project):
    """Warn about `.lore.json` keys the hooks silently ignore, or None.

    A typo (`maxRecal`) or a wrong type reads as "my setting does nothing" with
    no explanation, because `load_config` deliberately never fails. The linter
    is where that surfaces.
    """
    unknown, invalid = config_issues(project)
    if not unknown and not invalid:
        return None
    bits = []
    for k in unknown:
        near = difflib.get_close_matches(k, DEFAULTS, n=1, cutoff=0.7)
        hint = f" (did you mean '{near[0]}'?)" if near else ""
        bits.append(f"unknown key '{k}'{hint}")
    for k in invalid:
        bits.append(f"'{k}' has an invalid value (default kept)")
    return "note: .lore.json -- ignored: " + "; ".join(bits) + "."


# --- default mode: file-ref existence check ----------------------------------

def cmd_check(entries, project, cfg, strict):
    stale_set = set(cfg["staleStatuses"])
    issues = []
    for e in entries:
        actionable = e["status"] not in stale_set
        for ref in e["files"]:
            if ref and not ref_exists(project, ref):
                issues.append((e, ref, actionable))
    notes = [n for n in (config_warning(project), version_warning(project)) if n]
    if not issues:
        print("OK  learnings: all entries have valid frontmatter file refs.")
        for n in notes:
            print(f"  {n}")
        return 0
    actionable = [i for i in issues if i[2]]
    print(f"\nlearnings file-ref check: {len(issues)} issue(s), {len(actionable)} actionable:")
    cur = None
    for e, ref, act in issues:
        rel = e["path"].relative_to(project).as_posix()
        if rel != cur:
            print(f"\n  {rel} [status: {e['status'] or 'current'}]")
            cur = rel
        note = "" if act else f"  (expected -- entry is {e['status']})"
        print(f"    - referenced file no longer exists: {ref}{note}")
    print("\n  -> A 'current' entry with a missing file is likely STALE: fix the path,")
    print("     mark status: superseded, or re-verify the claim against the code.")
    for n in notes:
        print(f"  {n}")
    return 1 if (strict and actionable) else 0


# --- drift triage (git) ------------------------------------------------------

_GIT_CHUNK = 150  # refs per git invocation (Windows command-length headroom)


def _git_show_prefix(project):
    try:
        out = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "--show-prefix"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip().replace("\\", "/")
    except (OSError, subprocess.SubprocessError):
        return None


def _git_last_changes(project, refs):
    """Last-commit date per `files:` ref, streaming ONE `git log` per chunk.

    `git log --name-only` walks history newest-first, so the first commit whose
    file list matches a ref gives that ref's most recent change; we stop as soon
    as every ref is resolved. Refs may be paths, dir prefixes, or globs -- git
    pathspecs narrow the walk and `ref_matches` does the exact attribution.
    Returns {normalized_ref: date}.
    """
    want = [norm_rel(r) for r in dict.fromkeys(refs) if r]
    if not want:
        return {}
    prefix = _git_show_prefix(project)
    if prefix is None:
        return {}
    found = {}
    for i in range(0, len(want), _GIT_CHUNK):
        chunk = [r for r in want[i:i + _GIT_CHUNK] if r not in found]
        if not chunk:
            continue
        args = (["git", "-C", str(project), "log", "--format=%cs",
                 "--name-only", "--"] + chunk)
        try:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL,
                                    text=True, errors="replace")
        except OSError:
            return found
        remaining = set(chunk)
        cur = None
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                if not line:
                    continue
                if _DATE_LINE.match(line):
                    cur = _parse_date(line)
                    continue
                path = line
                if path.startswith('"') and path.endswith('"'):
                    path = path[1:-1]  # git-quoted path; good enough here
                if prefix and path.startswith(prefix):
                    path = path[len(prefix):]
                if cur is None:
                    continue
                for r in [r for r in remaining if ref_matches(r, path)]:
                    found[r] = cur
                    remaining.discard(r)
                if not remaining:
                    break
        finally:
            try:
                proc.stdout.close()
                proc.terminate()
                proc.wait(timeout=5)
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
    return found


def _drift_candidates(entries, project, stale_set):
    """Current entries whose referenced code changed after their baseline date.

    Returns [(gap_days, entry, base_date, newest_date, newest_ref), ...].
    """
    tracked, all_refs = [], set()
    for e in entries:
        if e["status"] in stale_set:
            continue
        base = _parse_date(e["verified"] or e["date"])
        if not base or not e["files"]:
            continue
        refs = [r for r in e["files"] if r and ref_exists(project, r)]
        if not refs:
            continue
        tracked.append((e, base, refs))
        all_refs.update(norm_rel(r) for r in refs)
    dates = _git_last_changes(project, all_refs)
    cands = []
    for e, base, refs in tracked:
        newest, newest_f = None, None
        for ref in refs:
            d = dates.get(norm_rel(ref))
            if d and (newest is None or d > newest):
                newest, newest_f = d, ref
        if newest and newest > base:
            cands.append(((newest - base).days, e, base, newest, newest_f))
    return cands


def cmd_report(entries, project, cfg):
    if not (project / ".git").exists():
        print("Drift triage: skipped (not a git repository).")
        return 0
    cands = _drift_candidates(entries, project, set(cfg["staleStatuses"]))
    print("Drift triage -- current entries whose referenced code changed AFTER their")
    print("baseline (heuristic: a changed file may or may not invalidate the learning):")
    if not cands:
        print("  none.")
        return 0
    for gap, e, base, newest, f in sorted(cands, key=lambda c: c[0], reverse=True):
        rel = e["path"].relative_to(project).as_posix()
        print(f"  {gap:5d}d  {rel}")
        print(f"          baseline {base}, code last changed {newest} ({f})")
    print("\n  -> Re-verify the top entries, then add/bump a 'verified:' date in their")
    print("     frontmatter so they drop off this list until the code moves again.")
    return 0


# --- near-duplicate detection ------------------------------------------------

def _entry_tokens(e):
    toks = words(e["title"] + " " + " ".join(e["tags"]))
    return {w for w in toks if len(w) >= 3 and w not in _DUPE_STOP}


def _dupe_pairs(entries):
    """Entry pairs sharing >= _DUPE_MIN_SHARED meaningful title/tag words."""
    toks = [(e, _entry_tokens(e)) for e in entries]
    pairs = []
    for i in range(len(toks)):
        for j in range(i + 1, len(toks)):
            shared = toks[i][1] & toks[j][1]
            if len(shared) >= _DUPE_MIN_SHARED:
                pairs.append((len(shared), toks[i][0], toks[j][0],
                              sorted(shared)))
    pairs.sort(key=lambda p: -p[0])
    return pairs


def _category_variants(entries):
    """Category names that normalize to the same key (ci vs CI vs build_ci)."""
    groups = {}
    for e in entries:
        cat = e["category"]
        key = re.sub(r"[-_\s]+", "", cat.lower())
        groups.setdefault(key, set()).add(cat)
    return sorted(v for v in groups.values() if len(v) > 1)


def cmd_dupes(entries, project):
    pairs = _dupe_pairs(entries)
    print("Near-duplicate triage -- pairs sharing title/tag vocabulary (merge")
    print("candidates; the capture-time overlap check only sees ONE machine, so")
    print("parallel captures from teammates land here):")
    if not pairs:
        print("  none.")
    for n, a, b, shared in pairs:
        ra = a["path"].relative_to(project).as_posix()
        rb = b["path"].relative_to(project).as_posix()
        print(f"\n  {n} shared: {', '.join(shared)}")
        print(f"    - {ra}")
        print(f"    - {rb}")
    variants = _category_variants(entries)
    if variants:
        print("\nCategory variants (same name, different spelling -- pick one):")
        for group in variants:
            print(f"  - {' / '.join(sorted(group))}")
    if pairs or variants:
        print("\n  -> Merge true duplicates into ONE entry (keep the better body,")
        print("     union the tags), mark the loser superseded or delete it, then")
        print("     regenerate the index (--index).")
    return 0


# --- stats -------------------------------------------------------------------

def _age_days(stamp):
    d = _parse_date(stamp)
    return None if d is None else (datetime.now().date() - d).days


def _dangling_links(entries):
    """Soft set of [[slug]] refs not resolving to a store filename stem.

    Forward-refs are allowed (a topic not captured yet), so this is purely
    informational -- never an error. Tolerates [[slug|alias]].
    """
    stems = {e["path"].stem for e in entries}
    link_re = re.compile(r"\[\[([^\]]+)\]\]")
    dangling = set()
    for e in entries:
        try:
            text = e["path"].read_text(encoding="utf-8")
        except OSError:
            continue
        for raw in link_re.findall(text):
            name = raw.split("|")[0].strip()
            if name and name not in stems:
                dangling.add(name)
    return dangling


def _recall_activity(entries, project):
    """Recall telemetry from `.git/lore-recall.log`, or None when absent.

    Returns `(top, never, unread)`:
      top    -- [(surfaced, reads, rel), ...] the 3 most-surfaced entries
      never  -- how many entries have never surfaced at all
      unread -- entries surfaced >= _UNREAD_MIN times that were never READ,
                or None when the log holds no `read` events at all.

    The `read` events come from the PostToolUse hook, so an install that
    predates it (or a target with no Read tool, e.g. Codex) has none -- and then
    "never read" would be vacuously true for every entry, which is why `unread`
    is None rather than a full list. The log is local, inside `.git`, never
    committed. Absent log -> None (no git, or nothing has surfaced yet).
    """
    log = project / ".git" / "lore-recall.log"
    if not log.is_file():
        return None
    surfaced, reads = {}, {}
    try:
        for line in log.read_text(encoding="utf-8",
                                  errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            bucket = reads if parts[1] == "read" else surfaced
            bucket[parts[2]] = bucket.get(parts[2], 0) + 1
    except OSError:
        return None
    rels = {rel_path(project, e["path"]) for e in entries}
    top = sorted(((n, reads.get(p, 0), p) for p, n in surfaced.items()
                  if p in rels), reverse=True)[:3]
    never = len(rels - set(surfaced))
    unread = None
    if reads:
        unread = sorted((p, n) for p, n in surfaced.items()
                        if p in rels and n >= _UNREAD_MIN and p not in reads)
    return top, never, unread


def _personal_entries(project, cfg):
    """Entries in the private store, for the stats view only.

    The personal store is deliberately outside every other mode: it is not
    committed, so there is nothing to index, no README to regenerate, and no
    push boundary to secret-scan. Stats still counts it, because "recall found
    nothing" is confusing when half your entries are invisible here.
    """
    p = personal_store(project, cfg)
    if p is None or not p.is_dir():
        return []
    return list(iter_entries(p, personal=True))


def cmd_stats(entries, project, cfg, store):
    stale_set = set(cfg["staleStatuses"])
    stale_days = stale_after_days(cfg)
    by_status, by_cat = {}, {}
    deleted_refs = unverified_old = 0
    oldest = None
    for e in entries:
        st = e["status"] or "current"
        by_status[st] = by_status.get(st, 0) + 1
        by_cat[e["category"]] = by_cat.get(e["category"], 0) + 1
        if e["status"] not in stale_set and any(
                f and not ref_exists(project, f) for f in e["files"]):
            deleted_refs += 1
        age = _age_days(e["verified"] or e["date"])
        if age is not None:
            if age >= stale_days:
                unverified_old += 1
            if oldest is None or age > oldest[0]:
                oldest = (age, e)

    drift = (len(_drift_candidates(entries, project, stale_set))
             if (project / ".git").exists() else None)
    dangling = _dangling_links(entries)
    dupes = len(_dupe_pairs(entries))

    def counts(d):
        return " | ".join(f"{k} {v}" for k, v in
                          sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))

    months = cfg["staleAfterMonths"]
    print(f"Lore store health  ({store.relative_to(project).as_posix()})")
    print(f"  entries: {len(entries)}")
    print(f"  by status:    {counts(by_status)}")
    print(f"  by category:  {counts(by_cat)}")
    print("")
    print("  freshness:")
    tail = "   -> verify_refs.py (no args) lists them" if deleted_refs else ""
    print(f"    deleted file refs (current): {deleted_refs}{tail}")
    if drift is None:
        print("    drift backlog: n/a (not a git repository)")
    else:
        tail = "   -> verify_refs.py --report" if drift else ""
        print(f"    drift backlog (code changed since verified): {drift}{tail}")
    print(f"    not verified in >{months}mo: {unverified_old}")
    if oldest:
        rel = oldest[1]["path"].relative_to(project).as_posix()
        print(f"    oldest stamp: {rel}  (~{oldest[0] // 30}mo)")
    print("")
    personal = _personal_entries(project, cfg)
    activity = _recall_activity(entries + personal, project)
    if activity is not None:
        top, never, unread = activity
        print("  recall activity (local .git/lore-recall.log):")
        for n, r, p in top:
            print(f"    {n:4d}x surfaced, read {r}x  {p}")
        tail = "   -> tags may not match how you prompt" if never else ""
        print(f"    never surfaced: {never}{tail}")
        if unread is None:
            print("    reads: none logged yet (they come from the PostToolUse "
                  "Read hook -> reinstall the plugin if it isn't running)")
        else:
            tail = ("   -> misleading title/tags, or noise" if unread else "")
            print(f"    surfaced >={_UNREAD_MIN}x but never read: "
                  f"{len(unread)}{tail}")
            for p, n in unread[:3]:
                print(f"      - {p}  ({n}x)")
        print("")
    if personal:
        print(f"  personal store: {len(personal)} entries in "
              f"{cfg['personalStoreDir']} (private -- recalled, never "
              "indexed/scanned)")
        print("")
    tail = "   -> verify_refs.py --dupes" if dupes else ""
    print(f"  near-duplicate pairs (title/tag overlap): {dupes}{tail}")
    print(f"  links: dangling [[refs]] (soft; forward-refs ok): {len(dangling)}")
    for n in (config_warning(project), version_warning(project)):
        if n:
            print(f"\n  {n}")
    return 0


# --- index -------------------------------------------------------------------

def cmd_index(entries, project, cfg, store):
    stale_set = set(cfg["staleStatuses"])
    by_cat = {}
    status_counts = {}
    for e in entries:
        by_cat.setdefault(e["category"], []).append(e)
        st = e["status"] or "current"
        status_counts[st] = status_counts.get(st, 0) + 1
    summary = " | ".join(f"{v} {k}" for k, v in
                         sorted(status_counts.items(), key=lambda kv: (-kv[1], kv[0])))
    out = [
        "# Learnings - knowledge store",
        "",
        "One-fact-per-file record of non-obvious, reusable learnings. Managed by the",
        "`lore` Claude Code plugin (recall hook + capture skill + freshness linter).",
        "",
        f"## Index ({len(entries)} entries)",
        "",
        f"_{summary}_",
        "",
    ]
    for cat in sorted(by_cat):
        items = sorted(by_cat[cat], key=lambda e: e["title"].lower())
        out.append(f"### {cat} ({len(items)})")
        out.append("")
        for e in items:
            rel = e["path"].relative_to(store).as_posix()
            tag = f" - **[{e['status'].upper()}]**" if e["status"] in stale_set else ""
            out.append(f"- [{e['title']}]({rel}){tag}")
        out.append("")
    out.append("---")
    out.append("_Generated by `lore` (verify_refs.py --index); regenerate after adding entries._")
    (store / "README.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote {(store / 'README.md').relative_to(project).as_posix()} "
          f"- {len(entries)} entries, {len(by_cat)} categories")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Lore — freshness tooling for the learnings store.")
    ap.add_argument("--report", action="store_true", help="git drift triage")
    ap.add_argument("--stats", action="store_true", help="store-health summary")
    ap.add_argument("--dupes", action="store_true", help="near-duplicate triage")
    ap.add_argument("--index", action="store_true", help="regenerate store README")
    ap.add_argument("--strict", action="store_true", help="exit 1 on actionable issues")
    args = ap.parse_args()

    project = find_project_dir()
    cfg = load_config(project)
    store = project / cfg["storeDir"]
    if not store.is_dir():
        print(f"No learnings store at {store} - run /lore:init first.")
        return 0
    entries = list(iter_entries(store))
    if args.index:
        return cmd_index(entries, project, cfg, store)
    if args.stats:
        return cmd_stats(entries, project, cfg, store)
    if args.dupes:
        return cmd_dupes(entries, project)
    if args.report:
        return cmd_report(entries, project, cfg)
    return cmd_check(entries, project, cfg, args.strict)


if __name__ == "__main__":
    sys.exit(main())
