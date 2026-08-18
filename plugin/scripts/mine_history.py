#!/usr/bin/env python3
"""Mine the git history for learning candidates -- the cold-start fix (stdlib only).

An empty store is the failure mode of this whole idea: recall has nothing to
surface, so the plugin looks dead, so nobody captures, so the store stays empty.
The material for the first two dozen entries is already in the repo -- fix and
revert commits, the files everyone keeps touching, and commit bodies written by
someone who had just finished paying for the lesson. This ranks that material so
the agent can write real entries on day one instead of week six.

Read-only and heuristic: it runs `git log`, scores commits, and prints
candidates. Deciding what is genuinely non-obvious AND reusable -- and writing
the entries -- stays the agent's job (`/lore:mine`), under the same gate as any
other capture.

Signals (see SIGNALS): a revert (someone shipped the wrong thing and had to undo
it), fix/bug vocabulary in the subject, a body that explains a *why* ("root
cause", "turns out", "porque"), and churn -- a file rewritten 20 times is where
the undocumented gotchas live. Release/merge/typo/formatting commits are
filtered out; so are commits that only touch the learnings store itself.

Candidates already covered by an existing learning are marked rather than
dropped, using the SAME scorer the recall hook uses -- so a second run after a
capture session shows what is left, and an overlap suggests UPDATE, not a
near-duplicate.

Usage:
  mine_history.py [--max-commits N] [--since DATE] [--top N] [PATH ...]
Exit: always 0 (a repo with no git, or no candidates, is not an error).
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (find_project_dir, fold, load_config,  # noqa: E402
                     norm_rel, rel_path, store_dirs)
from recall import rank_matches  # noqa: E402

# git log record/field separators: ASCII RS/US never appear in commit text,
# unlike any newline- or pipe-based format.
_REC, _FLD = "\x1e", "\x1f"

# (points, name, regex) -- matched against folded (lowercased, unaccented) text.
# English + Spanish, because the store and the prompts are bilingual in practice
# and an English-only keyword list silently mines nothing in a Spanish repo.
SIGNALS = [
    (3, "revert", re.compile(
        r"\brevert(s|ed|ing)?\b|\brevierte\b|\bdeshace\b")),
    (2, "fix/bug", re.compile(
        r"\bfix(es|ed|ing)?\b|\bbug(s|fix)?\b|\bhotfix\b|\bregression\b"
        r"|\bbroken?\b|\bcrash(es|ed)?\b|\bleak(s|ing)?\b|\bdeadlock\b"
        r"|\brace\s+condition\b|\btimeout(s)?\b|\bflaky\b|\bworkaround\b"
        r"|\barregl(a|o|ar|ado)\b|\bcorrig(e|io|ir)\b|\bcorreccion\b"
        r"|\bfallo(s)?\b|\berror(es)?\b|\brompe\b|\broto\b")),
    (2, "explains why", re.compile(
        r"\broot cause\b|\bturns out\b|\bthe reason\b|\bbecause\b|\bgotcha\b"
        r"|\bbeware\b|\bcareful\b|\bnote that\b|\binstead of\b|\bhad to\b"
        r"|\bapparently\b|\bit seems\b|\bwhy\b"
        r"|\bporque\b|\bresulta que\b|\bla razon\b|\bmotivo\b|\bojo\b"
        r"|\bcuidado\b|\ben lugar de\b|\bhabia que\b|\bpor eso\b")),
]

# Subjects that never carry a learning -- filtered before scoring so they can
# never crowd out a real candidate through churn alone.
NOISE = re.compile(
    r"^(?:wip\b|merge\b|revert \"?merge|bump\b|release\b|v?\d+\.\d+\.\d+\s*$"
    r"|chore(?:\(|:)|typo\b|formatting\b|reformat|lint\b|whitespace\b"
    r"|rename\b|prettier\b|black\b|initial commit\b)", re.I)

# A body this long is someone explaining themselves, not a one-liner.
_BODY_LONG, _BODY_SOME = 200, 60
# A commit touching more than this is a sweep/vendor drop, not a lesson.
_MAX_FILES = 40
# Score needed to be shown at all. Deliberately above "fix keyword + focused
# change": a bare `Fix X` with no body, no churn, and no revert is the single
# most common commit in any repo, and showing all of them buries the real
# candidates. A plain fix must bring a second piece of evidence.
MIN_SCORE = 4
# Churn ranking: files at or above this many commits count as a hot spot.
_HOT_MIN = 4


def git_log(project, max_commits=400, since="", paths=()):
    """Raw `git log` output for mining, or None when git can't be used.

    One subprocess: `--format` writes the metadata block, `--name-only` appends
    the file list, and the RS/US separators keep the two apart even when a
    commit body contains blank lines. Merges are excluded -- their file lists
    are the union of both sides, which would poison churn.
    """
    fmt = f"{_REC}%H{_FLD}%cs{_FLD}%s{_FLD}%b{_FLD}"
    args = ["git", "-C", str(project), "log", "--no-merges",
            f"--format={fmt}", "--name-only", "-n", str(int(max_commits))]
    if since:
        args += ["--since", since]
    if paths:
        args += ["--"] + [str(p) for p in paths]
    try:
        # Decode as UTF-8 explicitly: commit MESSAGES are prose, and letting
        # the process locale decide (cp1252 on Windows) mangles every accent
        # and em-dash -- which then mis-scores the Spanish signal regexes.
        out = subprocess.run(args, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def parse_log(raw):
    """Parse `git_log` output into [{sha, date, subject, body, files}, ...]."""
    commits = []
    for rec in (raw or "").split(_REC):
        if not rec.strip():
            continue
        parts = rec.split(_FLD)
        if len(parts) < 5:
            continue
        sha, date, subject, body, tail = parts[:5]
        files = []
        for line in tail.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith('"') and line.endswith('"'):
                line = line[1:-1]  # git-quoted path; good enough for display
            files.append(norm_rel(line))
        commits.append({
            "sha": sha.strip(), "date": date.strip(),
            "subject": subject.strip(), "body": body.strip(), "files": files,
        })
    return commits


def churn_counts(commits):
    """{path: number of commits that touched it} across the scanned window."""
    counts = {}
    for c in commits:
        for f in c["files"]:
            counts[f] = counts.get(f, 0) + 1
    return counts


def _store_rels(project, cfg):
    """Store paths as project-relative prefixes, to skip lore's own commits."""
    rels = []
    for store, _personal in store_dirs(project, cfg):
        rel = rel_path(project, store)
        if not Path(rel).is_absolute():
            rels.append(rel.rstrip("/") + "/")
    return rels


def score_commit(c, churn, store_rels=()):
    """`(score, [reasons])` for one commit -- 0 means "not a candidate".

    Zero is returned for noise subjects, sweeps, and commits that only touch
    the learnings store (capturing a learning about capturing learnings is a
    loop, not a lesson).
    """
    subject, files = c["subject"], c["files"]
    if not subject or NOISE.match(subject):
        return 0, []
    if len(files) > _MAX_FILES:
        return 0, []
    if files and store_rels and all(
            any(f.startswith(s) for s in store_rels) for f in files):
        return 0, []
    text = fold(subject + "\n" + c["body"])
    score, reasons = 0, []
    for points, name, rx in SIGNALS:
        if rx.search(text):
            score += points
            reasons.append(name)
    body_len = len(c["body"])
    if body_len >= _BODY_LONG:
        score += 2
        reasons.append(f"detailed body ({body_len} chars)")
    elif body_len >= _BODY_SOME:
        score += 1
        reasons.append("has a body")
    hot = max((churn.get(f, 0) for f in files), default=0)
    if hot >= _HOT_MIN:
        score += 1
        reasons.append(f"hot file ({hot} commits)")
    if 1 <= len(files) <= 3 and score:
        score += 1
        reasons.append("focused change")
    return score, reasons


def candidates(commits, project, cfg, min_score=MIN_SCORE):
    """Scored, ranked candidates: [(score, reasons, commit), ...] best-first."""
    churn = churn_counts(commits)
    store_rels = _store_rels(project, cfg)
    out = []
    for c in commits:
        score, reasons = score_commit(c, churn, store_rels)
        if score >= min_score:
            out.append((score, reasons, c))
    # Two stable passes: newest-first, then score-first -- so equal scores are
    # broken by recency (ISO dates can't be negated inside one sort key).
    out.sort(key=lambda r: r[2]["date"], reverse=True)
    out.sort(key=lambda r: -r[0])
    return out


def _overlap(c, stores, cfg, project):
    """An existing learning that may already cover this commit, or None."""
    if not stores:
        return None
    matches = rank_matches(c["subject"], stores, cfg)
    if not matches:
        return None
    _stale, e = matches[0]
    return rel_path(project, e["path"]), e["title"]


def report(commits, project, cfg, top=15):
    """Print the candidate report. Returns the number of candidates found."""
    cands = candidates(commits, project, cfg)
    stores = [(s, p) for s, p in store_dirs(project, cfg) if s.is_dir()]
    churn = churn_counts(commits)
    print("Lore history mining -- learning candidates from git "
          "(read-only, heuristic).")
    print(f"  scanned {len(commits)} commits, found {len(cands)} candidate(s)"
          f"{f', showing top {top}' if len(cands) > top else ''}.")
    if not cands:
        print("\n  none. Either the window is too small (--max-commits / "
              "--since) or this")
        print("  history is mostly mechanical commits. Capture from live work "
              "instead.")
        return 0
    print("")
    for i, (score, reasons, c) in enumerate(cands[:top], 1):
        print(f"  [{i}] score {score}  {c['sha'][:9]}  {c['date']}  "
              f"{c['subject'][:96]}")
        print(f"      signals: {', '.join(reasons)}")
        shown = ", ".join(c["files"][:4])
        more = f" (+{len(c['files']) - 4} more)" if len(c["files"]) > 4 else ""
        print(f"      files: {shown or '(none)'}{more}")
        hit = _overlap(c, stores, cfg, project)
        if hit:
            print(f"      overlap: {hit[0]} -- \"{hit[1]}\"  "
                  "(UPDATE that entry instead of adding a near-duplicate)")
        print(f"      read it: git show {c['sha'][:9]}")
        print("")
    hot = sorted(((n, f) for f, n in churn.items() if n >= _HOT_MIN),
                 reverse=True)[:8]
    if hot:
        print("  churn hot spots (most-rewritten files -- where undocumented")
        print("  gotchas accumulate; worth a learning even with no fix commit):")
        for n, f in hot:
            print(f"    {n:4d}x  {f}")
        print("")
    print("  -> For each candidate: `git show <sha>` for the diff and the full")
    print("     message, then apply the lore gate (non-obvious AND reusable).")
    print("     Skip anything the code already makes obvious. A commit is")
    print("     evidence, not a learning -- write the trap, not the changelog.")
    return len(cands)


def main():
    ap = argparse.ArgumentParser(
        description="Mine git history for learning candidates (cold start).")
    ap.add_argument("paths", nargs="*",
                    help="limit mining to these paths (default: whole repo)")
    ap.add_argument("--max-commits", type=int, default=400,
                    help="how many commits to scan (default 400)")
    ap.add_argument("--since", default="",
                    help="only commits after this date (git --since syntax)")
    ap.add_argument("--top", type=int, default=15,
                    help="how many candidates to print (default 15)")
    args = ap.parse_args()
    try:  # a commit subject with an em-dash must not crash a cp1252 console
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError, ValueError):
        pass

    project = find_project_dir()
    cfg = load_config(project)
    raw = git_log(project, max_commits=args.max_commits, since=args.since,
                  paths=args.paths)
    if raw is None:
        print("History mining: skipped (not a git repository, or git is "
              "unavailable).")
        return 0
    commits = parse_log(raw)
    if not commits:
        print("History mining: no commits in that window "
              "(try a larger --max-commits or drop --since).")
        return 0
    report(commits, project, cfg, top=max(1, args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
