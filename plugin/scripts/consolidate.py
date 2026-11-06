#!/usr/bin/env python3
"""Deterministic half of `/lore:consolidate` (stdlib only).

A store that grows past a few hundred entries is a good archive and a bad
briefing: nobody reads 450 files, and recall can only ever show `maxRecall` of
them. Consolidation distils them into a handful of topic SKILLS, each short
enough to load on demand -- `.claude/skills/lore-<topic>/SKILL.md`, which recall
indexes through `consolidatedDirs`.

Grouping entries by topic and writing the summaries is the agent's job (see
commands/consolidate.md). This script owns the parts that must not be improvised:

  (default)        --plan: the worklist. Topic groups of entries not yet
                   consolidated, the existing skills with their word budgets,
                   and what is safe to prune.
  --snapshot FILE  Record what recall answers TODAY, as a baseline.
  --compare FILE   Replay that baseline against the store as it is now and
                   report what became unreachable. Exits 1 on a regression --
                   this is the check that makes a consolidation safe to merge.
  --mark SKILL F.. Stamp `consolidated_into: SKILL` on entries. It does not
                   delete, move, or rewrite anything else: `tags:` and `files:`
                   stay, because they are what edit-time recall matches on.
  --prune          List entries that are safe to DELETE (stale AND already
                   consolidated) and print the command. It never deletes.
  --log TEXT       Append a dated entry to the store's WEEKLY_LOG.md.

NOTHING here deletes an entry. "Dream = forget" is a good instinct about
attention and a bad one about storage: the entry holds the root cause, the
repro, the dead ends, and the `files:` that make a gotcha appear at the moment
you edit the code -- none of which survives a 500-word summary. So entries are
marked, not removed, and the only deletions this tool will even propose are
entries that are both superseded/obsolete AND already consolidated.
"""
import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (LOG_NAME, body_words, find_project_dir,  # noqa: E402
                     iter_consolidated, iter_entries, load_config,
                     personal_store, rel_path, resolve_consolidated,
                     shell_safe, store_dirs)
from recall import rank_matches  # noqa: E402

SNAPSHOT_VERSION = 2


def _entries(project, cfg):
    store = Path(project) / cfg["storeDir"]
    return list(iter_entries(store)) if store.is_dir() else []


def _live_stores(project, cfg):
    """The stores recall would actually search, same as the hook sees them."""
    return [(s, p) for s, p in store_dirs(project, cfg) if s.is_dir()]


# --- plan --------------------------------------------------------------------

def _skill_for_topic(topic, consolidated):
    """The existing skill whose name ends in `topic`, when there is one."""
    key = re.sub(r"[-_\s]+", "", topic.lower())
    for c in consolidated:
        if re.sub(r"[-_\s]+", "", c["title"].lower()).endswith(key):
            return c
    return None


def cmd_plan(project, cfg, entries, consolidated, category=None):
    limit = cfg["consolidatedMaxWords"]
    stale_set = set(cfg["staleStatuses"])
    dirs = ", ".join(cfg["consolidatedDirs"]) or "(consolidatedDirs unset)"
    print(f"Consolidation plan  ({cfg['storeDir']} -> {dirs})")
    print("")

    if not cfg["consolidatedDirs"]:
        print("  .lore.json has no `consolidatedDirs`, so recall will NOT see any")
        print("  skill you write. Add it before consolidating:")
        print('    { "consolidatedDirs": [".claude/skills/lore-*"] }')
        print("")

    print(f"  consolidated skills: {len(consolidated)}")
    if consolidated:
        sizes = [(c, body_words(c["path"])) for c in consolidated]
        print(f"    body words: limit {limit}, largest "
              f"{max(n for _c, n in sizes)}")
        for c, n in sorted([s for s in sizes if s[1] > limit],
                           key=lambda t: -t[1]):
            print(f"    ! over limit: {rel_path(project, c['path'])} "
                  f"({n} words) -> split it by sub-topic")
        no_refs = [c for c in consolidated if not c["files"]]
        if no_refs:
            print(f"    no `files:` frontmatter: {len(no_refs)}"
                  "   -> invisible to edit-time recall")
            for c in no_refs[:5]:
                print(f"      - {rel_path(project, c['path'])}")

    live = [e for e in entries if e["status"] not in stale_set]
    done, dangling, todo = [], [], []
    for e in live:
        if not e["consolidated_into"]:
            todo.append(e)
        elif resolve_consolidated(project, e, consolidated) is not None:
            done.append(e)
        else:
            dangling.append(e)
    print("")
    print(f"  current entries: {len(live)}")
    print(f"    consolidated:     {len(done)}")
    print(f"    not consolidated: {len(todo)}")
    if dangling:
        print(f"    ! consolidated_into names a skill recall cannot see: "
              f"{len(dangling)}")
        for e in dangling[:5]:
            print(f"      - {rel_path(project, e['path'])} -> "
                  f"{e['consolidated_into']}")

    groups = {}
    for e in todo:
        groups.setdefault(e["category"] or "uncategorized", []).append(e)
    if category:
        groups = {k: v for k, v in groups.items() if k == category}
    print("")
    if not groups:
        print("  nothing to group: every current entry is already consolidated.")
    else:
        print("  topic groups to work through (entries not yet consolidated):")
    for topic in sorted(groups, key=lambda t: (-len(groups[t]), t)):
        items = groups[topic]
        skill = _skill_for_topic(topic, consolidated)
        if skill is None:
            where = f"NEW skill (suggest: lore-{topic})"
        else:
            used = body_words(skill["path"])
            where = (f"{rel_path(project, skill['path'])} "
                     f"({used} words, {max(limit - used, 0)} left)")
        print("")
        print(f"    {topic} ({len(items)} entries) -> {where}")
        for e in items:
            print(f"      - {rel_path(project, e['path'])}")
            print(f"          {e['title']}")
            if e["tags"]:
                print(f"          tags: {', '.join(e['tags'][:8])}")
            if e["files"]:
                print(f"          files: {', '.join(e['files'][:8])}")

    prunable = _prunable(project, entries, consolidated, stale_set)
    print("")
    print(f"  prune candidates (stale AND consolidated): {len(prunable)}"
          f"{'   -> --prune' if prunable else ''}")
    print("")
    print("  next: --snapshot before you write anything, then --mark every entry")
    print("        you absorbed, then --compare to prove recall still finds it.")
    return 0


# --- recall parity -----------------------------------------------------------

def _queries(project, entries, extra_file=None):
    """`{query: primary_entry_path_or_None}` -- the parity probes.

    An entry's own title + tags is the sharpest possible probe for it: it is
    literally what the scorer matches on. That entry is the query's PRIMARY --
    the one result the query exists to check. If a query stops reaching its
    primary (or the skill that absorbed it), consolidation broke recall for the
    prompt most likely to need it.

    Queries from `extra_file` have no primary: they are the project's own
    golden questions, checked for an answer rather than for a specific entry.
    """
    qs = {}
    for e in entries:
        q = " ".join([e["title"]] + list(e["tags"])).strip()
        if q and q not in qs:
            qs[q] = rel_path(project, e["path"])
    if extra_file:
        try:
            for line in Path(extra_file).read_text(
                    encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    qs.setdefault(line, None)
        except OSError as exc:
            print(f"warning: could not read {extra_file}: {exc}")
    return qs


def _answers(project, cfg, queries, stores, consolidated):
    out = {}
    for q in queries:
        matches = rank_matches(q, stores, cfg, consolidated, project)
        out[q] = [rel_path(project, e["path"]) for _s, e in matches]
    return out


def cmd_snapshot(project, cfg, entries, consolidated, path, extra_file):
    stores = _live_stores(project, cfg)
    queries = _queries(project, entries, extra_file)
    answers = _answers(project, cfg, list(queries), stores, consolidated)
    Path(path).write_text(json.dumps({
        "version": SNAPSHOT_VERSION,
        "taken": datetime.date.today().isoformat(),
        "maxRecall": cfg["maxRecall"],
        "queries": answers,
        # The entry each query exists to find. Without it, --compare cannot
        # tell a lost lesson from a result pushed out of the top maxRecall.
        "primaries": {q: p for q, p in queries.items() if p},
    }, indent=1, sort_keys=True), encoding="utf-8")
    answered = sum(1 for v in answers.values() if v)
    print(f"Recall snapshot written to {path}")
    print(f"  {len(answers)} queries, {answered} answered by at least one entry.")
    print("  Consolidate, then re-run with --compare to prove nothing was lost.")
    return 0


def _reachable(project, lost, after, entries_by_rel, consolidated):
    """True when a path missing from a query's answers is still REACHABLE.

    Three legitimate reasons: the entry is in the answers after all; the skill
    that absorbed it answers the query instead; or the entry is gone from disk
    entirely (a pruned stale entry) and the query is still answered by
    something. Anything else is a real loss of recall.
    """
    if lost in after:
        return True
    e = entries_by_rel.get(lost)
    if e is None:
        return bool(after)  # entry deleted; the topic is still reachable
    target = resolve_consolidated(project, e, consolidated)
    return target is not None and rel_path(project, target["path"]) in after


def cmd_compare(project, cfg, entries, consolidated, path):
    try:
        snap = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Cannot read snapshot {path}: {exc}")
        return 2
    before = snap.get("queries")
    if not isinstance(before, dict):
        print(f"{path} is not a lore recall snapshot.")
        return 2
    stores = _live_stores(project, cfg)
    after = _answers(project, cfg, list(before), stores, consolidated)
    by_rel = {rel_path(project, e["path"]): e for e in entries}

    primaries = snap.get("primaries")
    if not isinstance(primaries, dict):
        primaries = {}

    # Two different things drop out of a query's answers, and only one is a
    # failure. The PRIMARY is the entry the query exists to find; losing it
    # (and the skill that absorbed it) means a lesson became unreachable.
    # Anything else that leaves the top `maxRecall` was incidental to that
    # query and has its own query of its own -- adding a layer reshuffles the
    # tail by design, and reporting that as a regression buries the real one.
    # A v1 snapshot carries no primaries; every path is then treated as one,
    # which is the old, noisier behaviour.
    kept, absorbed, regressed, reshuffled = 0, 0, [], 0
    for q, was in before.items():
        now = after.get(q, [])
        primary = primaries.get(q)
        checked = [primary] if primary else list(was)
        bad = [p for p in checked
               if not _reachable(project, p, now, by_rel, consolidated)]
        if bad:
            regressed.append((q, bad, now))
            continue
        if primary and primary not in now:
            absorbed += 1
        elif [p for p in was if p not in now]:
            reshuffled += 1
            kept += 1
        else:
            kept += 1

    print(f"Recall parity check -- {len(before)} queries replayed against the "
          "store as it is now")
    print(f"  primary still found:              {kept}")
    print(f"  primary now answered by its skill: {absorbed}")
    print(f"  REGRESSED (no longer reachable):  {len(regressed)}")
    if reshuffled:
        print(f"    (of those, {reshuffled} saw other results leave the top "
              f"{cfg['maxRecall']} -- expected when a layer is added)")
    for q, bad, now in regressed[:20]:
        print("")
        print(f"    query: {q[:110]}")
        for p in bad:
            print(f"      lost: {p}")
        print(f"      now:  {', '.join(now) or '(nothing)'}")
    if len(regressed) > 20:
        print(f"\n    ... and {len(regressed) - 20} more.")
    if regressed:
        print("")
        print("  -> Each lost entry is now reachable by NOTHING. Fix by one of:")
        print("     add the entry's tags/files to the skill that should cover it,")
        print("     stamp the entry with `--mark <skill>`, or restore the entry.")
        return 1
    print("")
    print("  OK -- every query still reaches its entry, or the skill that "
          "absorbed it.")
    return 0


# --- mark --------------------------------------------------------------------

_FM = re.compile(r"^(---[ \t]*\n)(.*?)(\n---[ \t]*(?:\n|$))", re.S)


def set_frontmatter_key(path, key, value):
    """Set one frontmatter key in place, leaving every other byte untouched.

    Returns "set", "unchanged", or a reason starting with "skip:". Deliberately
    line-surgical rather than parse-and-rewrite: an entry is hand-written prose
    and its frontmatter formatting belongs to its author, not to this script.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        return f"skip: unreadable ({exc})"
    m = _FM.match(text)
    if not m:
        return "skip: no frontmatter block"
    lines = m.group(2).splitlines()
    pat = re.compile(rf"^{re.escape(key)}:\s*(.*)$")
    for i, line in enumerate(lines):
        hit = pat.match(line)
        if hit:
            if hit.group(1).strip().strip("\"'") == value:
                return "unchanged"
            lines[i] = f"{key}: {value}"
            break
    else:
        lines.append(f"{key}: {value}")
    p.write_text(m.group(1) + "\n".join(lines) + m.group(3) + text[m.end():],
                 encoding="utf-8")
    return "set"


def cmd_mark(project, cfg, consolidated, skill, targets):
    if skill not in {c["title"] for c in consolidated}:
        dirs = cfg["consolidatedDirs"] or "(consolidatedDirs unset)"
        print(f"warning: no consolidated skill named '{skill}' is visible under "
              f"{dirs}.")
        print("         Marking anyway, but /lore:lint will flag these entries "
              "until the skill exists.")
    rc = 0
    for t in targets:
        result = set_frontmatter_key(t, "consolidated_into", skill)
        print(f"  {result:<30} {rel_path(project, Path(t))}")
        if result.startswith("skip:"):
            rc = 1
    print("")
    print("  Entries keep their tags, files, and body -- only the stamp changed.")
    return rc


# --- prune (proposes only) ---------------------------------------------------

def _prunable(project, entries, consolidated, stale_set):
    """Entries that are BOTH not-live and already absorbed by a visible skill."""
    out = []
    for e in entries:
        if e["status"] not in stale_set:
            continue
        target = resolve_consolidated(project, e, consolidated)
        if target is not None:
            out.append((e, target))
    return out


def cmd_prune(project, cfg, entries, consolidated):
    stale_set = set(cfg["staleStatuses"])
    cands = _prunable(project, entries, consolidated, stale_set)
    print("Prune proposal -- entries safe to delete, because they are BOTH")
    print(f"  not live (status in {sorted(stale_set)}) AND absorbed by a skill")
    print("  recall can see. Nothing else is ever proposed, and this command")
    print("  deletes nothing itself.")
    print("")
    if not cands:
        kept = [e for e in entries
                if e["status"] not in stale_set and e["consolidated_into"]]
        print("  none.")
        if kept:
            print(f"  ({len(kept)} consolidated entries are still `current`. "
                  "Being summarized is not")
            print("   a reason to delete one: it holds the root cause, the "
                  "repro, and the `files:`")
            print("   that drive edit-time recall, none of which fits in a "
                  "summary.)")
        return 0
    rels = []
    for e, target in cands:
        rel = rel_path(project, e["path"])
        rels.append(rel)
        print(f"  - {rel}  [{e['status']}] -> {target['title']}")
    safe = [r for r in rels if shell_safe(r)]
    unsafe = [r for r in rels if r not in safe]
    print("")
    print("  Review each one, then delete the ones you agree with:")
    if safe:
        print("    git rm -- " + " ".join(safe))
    if unsafe:
        print("")
        print("  These names contain characters a shell would interpret, so they")
        print("  are NOT in the command above -- delete them by hand:")
        for r in unsafe:
            print(f"    - {r}")
    print("")
    print("  Then re-run `--compare` and `/lore:lint` before committing.")
    return 0


# --- journal -----------------------------------------------------------------

_LOG_HEADER = """# Consolidation log

Dated record of every `/lore:consolidate` pass: what was distilled into which
skills, and what was pruned. Newest first. Appended by `consolidate.py --log`.
"""


_TOTAL_RE = re.compile(r"^- total body words: (\d+)", re.M)


def cmd_log(project, cfg, entries, consolidated, text):
    store = Path(project) / cfg["storeDir"]
    if not store.is_dir():
        print(f"No team store at {store} -- nothing to journal.")
        return 1
    log = store / LOG_NAME
    stale_set = set(cfg["staleStatuses"])
    live = [e for e in entries if e["status"] not in stale_set]
    done = [e for e in live
            if resolve_consolidated(project, e, consolidated) is not None]
    sizes = [body_words(c["path"]) for c in consolidated]
    widest, total = max(sizes, default=0), sum(sizes)
    try:
        existing = log.read_text(encoding="utf-8") if log.is_file() \
            else _LOG_HEADER
    except OSError as exc:
        print(f"Cannot read {log}: {exc}")
        return 1
    # The total, with its delta, is the only trace a CONTEXT COLLAPSE leaves.
    # Rewriting a whole corpus of summaries instead of extending it can shed
    # most of it in one pass while every other number here still looks healthy
    # -- the skills are fewer-but-shorter, the entries are all still marked,
    # and recall parity passes because the entries never left. A big drop is
    # expected on a FIRST pass (that is the duplication going); a big drop on a
    # later one means the pass rewrote rather than extended.
    prev = _TOTAL_RE.search(existing)
    delta = ""
    if prev:
        was = int(prev.group(1))
        change = total - was
        delta = f" (was {was}, {change:+d})"
        if was and total < was * 0.7:
            delta += "  <- shed >30% of the corpus: rewrite, not a delta?"
    block = [
        f"## {datetime.date.today().isoformat()}",
        "",
        text.strip(),
        "",
        f"- skills: {len(consolidated)} (largest {widest} words, limit "
        f"{cfg['consolidatedMaxWords']})",
        f"- total body words: {total}{delta}",
        f"- entries: {len(live)} current - {len(done)} consolidated, "
        f"{len(live) - len(done)} not",
        "- entries deleted by this pass: 0 (consolidation marks; it does not "
        "forget)",
        "",
    ]
    # Newest entry first: a journal you have to scroll to the end of is one
    # nobody reads. Split the file at its first dated section.
    m = re.search(r"\n## ", existing)
    head = existing[:m.start()].rstrip() + "\n\n" if m else \
        existing.rstrip() + "\n\n"
    rest = existing[m.start():].lstrip("\n") if m else ""
    log.write_text(head + "\n".join(block) + (rest and "\n" + rest),
                   encoding="utf-8")
    print(f"Appended to {rel_path(project, log)}")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Lore - consolidate entries into topic skills without "
                    "losing recall.")
    ap.add_argument("--plan", action="store_true",
                    help="worklist of topic groups (the default)")
    ap.add_argument("--category", help="restrict --plan to one category")
    ap.add_argument("--snapshot", metavar="FILE",
                    help="record what recall answers today")
    ap.add_argument("--compare", metavar="FILE",
                    help="replay a snapshot; exit 1 if anything became "
                         "unreachable")
    ap.add_argument("--queries", metavar="FILE",
                    help="extra parity queries, one per line (with --snapshot)")
    ap.add_argument("--mark", nargs="+", metavar="SKILL ENTRY",
                    help="stamp `consolidated_into: SKILL` on the given entries")
    ap.add_argument("--prune", action="store_true",
                    help="list entries safe to delete (never deletes)")
    ap.add_argument("--log", metavar="TEXT",
                    help="append a dated entry to the store's " + LOG_NAME)
    args = ap.parse_args()

    project = find_project_dir()
    cfg = load_config(project)
    store = Path(project) / cfg["storeDir"]
    personal = personal_store(project, cfg)
    if not store.is_dir() and not (personal is not None and personal.is_dir()):
        print(f"No learnings store at {store} - run /lore:init first.")
        return 0
    entries = _entries(project, cfg)
    consolidated = list(iter_consolidated(project, cfg))

    if args.snapshot:
        return cmd_snapshot(project, cfg, entries, consolidated,
                            args.snapshot, args.queries)
    if args.compare:
        return cmd_compare(project, cfg, entries, consolidated, args.compare)
    if args.mark:
        if len(args.mark) < 2:
            print("--mark needs a skill name and at least one entry path.")
            return 2
        return cmd_mark(project, cfg, consolidated, args.mark[0], args.mark[1:])
    if args.prune:
        return cmd_prune(project, cfg, entries, consolidated)
    if args.log is not None:
        return cmd_log(project, cfg, entries, consolidated, args.log)
    return cmd_plan(project, cfg, entries, consolidated, args.category)


if __name__ == "__main__":
    sys.exit(main())
