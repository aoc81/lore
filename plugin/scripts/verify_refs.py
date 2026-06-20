#!/usr/bin/env python3
"""Freshness tooling for the learnings store (stdlib only).

Modes:
  (default)   Existence check -- flag entries whose frontmatter `files:` paths no
              longer exist. `--strict` exits 1 on ACTIONABLE issues (a missing
              file on a non-stale entry; missing files on superseded entries are
              expected/informational).
  --report    Drift triage -- for each current entry, use `git log` to find
              referenced files changed AFTER the entry's `verified:` (or `date:`)
              baseline, ranked by gap. Best candidates for a re-verify. Heuristic.
  --index     Regenerate the store README index from entry frontmatter.

Does NOT lint `[[wiki-links]]`: a link with no target yet is an allowed
forward-reference, and prose can mention `[[...]]` literally -- so it's noise.
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import find_project_dir, iter_entries, load_config  # noqa: E402


def _parse_date(s):
    s = (s or "").split()[0]  # tolerate a trailing "# comment"
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def cmd_check(entries, project, cfg, strict):
    stale_set = set(cfg["staleStatuses"])
    issues = []
    for e in entries:
        actionable = e["status"] not in stale_set
        for ref in e["files"]:
            if not (project / ref).exists():
                issues.append((e, ref, actionable))
    if not issues:
        print("OK  learnings: all entries have valid frontmatter file refs.")
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
    return 1 if (strict and actionable) else 0


def _git_last_change(project, relpath):
    try:
        out = subprocess.run(
            ["git", "-C", str(project), "log", "-1", "--format=%cs", "--", relpath],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return _parse_date(out)


def cmd_report(entries, project, cfg):
    if not (project / ".git").exists():
        print("Drift triage: skipped (not a git repository).")
        return 0
    stale_set = set(cfg["staleStatuses"])
    cands = []
    for e in entries:
        if e["status"] in stale_set:
            continue
        base = _parse_date(e["verified"] or e["date"])
        if not base or not e["files"]:
            continue
        newest, newest_f = None, None
        for ref in e["files"]:
            if not (project / ref).exists():
                continue
            d = _git_last_change(project, ref)
            if d and (newest is None or d > newest):
                newest, newest_f = d, ref
        if newest and newest > base:
            cands.append(((newest - base).days, e, base, newest, newest_f))
    print("Drift triage -- current entries whose referenced code changed AFTER their")
    print("baseline (heuristic: a changed file may or may not invalidate the learning):")
    if not cands:
        print("  none.")
        return 0
    for gap, e, base, newest, f in sorted(cands, reverse=True):
        rel = e["path"].relative_to(project).as_posix()
        print(f"  {gap:5d}d  {rel}")
        print(f"          baseline {base}, code last changed {newest} ({f})")
    print("\n  -> Re-verify the top entries, then add/bump a 'verified:' date in their")
    print("     frontmatter so they drop off this list until the code moves again.")
    return 0


def cmd_index(entries, project, cfg, store):
    stale_set = set(cfg["staleStatuses"])
    by_cat = {}
    for e in entries:
        by_cat.setdefault(e["category"], []).append(e)
    out = [
        "# Learnings - knowledge store",
        "",
        "One-fact-per-file record of non-obvious, reusable learnings. Managed by the",
        "`lore` Claude Code plugin (recall hook + capture skill + freshness linter).",
        "",
        f"## Index ({len(entries)} entries)",
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
    if args.report:
        return cmd_report(entries, project, cfg)
    return cmd_check(entries, project, cfg, args.strict)


if __name__ == "__main__":
    sys.exit(main())
