"""Shared helpers for the lore hooks and scripts (stdlib only)."""
import json
import os
import re
from pathlib import Path

DEFAULTS = {
    "storeDir": "learnings",
    "maxRecall": 5,
    "staleStatuses": ["superseded", "obsolete", "deprecated"],
    "secretAllow": [],  # regexes whose match on a line suppresses secret-scan findings
}


def find_project_dir(data=None):
    """The user's project root: hook stdin `cwd`, else $CLAUDE_PROJECT_DIR, else cwd."""
    if data and data.get("cwd"):
        return Path(data["cwd"])
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    return Path.cwd()


def load_config(project):
    """Merge `.lore.json` (if present in the project root) over DEFAULTS."""
    cfg = dict(DEFAULTS)
    f = Path(project) / ".lore.json"
    if f.is_file():
        try:
            user = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                cfg.update({k: v for k, v in user.items() if k in DEFAULTS})
        except (ValueError, OSError):
            pass
    return cfg


def _unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def parse_frontmatter(text):
    """Parse the leading `--- ... ---` YAML-ish block (simple subset). Returns dict."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S)
    if not m:
        return {}
    lines = m.group(1).splitlines()
    fm, i = {}, 0
    while i < len(lines):
        km = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", lines[i])
        if not km:
            i += 1
            continue
        key, rest = km.group(1), km.group(2).strip()
        if rest == "":
            # block list: subsequent `  - item` lines
            vals, j = [], i + 1
            while j < len(lines):
                lm = re.match(r"^\s*-\s*(.+)$", lines[j])
                if lm:
                    vals.append(_unquote(lm.group(1)))
                    j += 1
                else:
                    break
            fm[key] = vals
            i = j
            continue
        if rest == "[]":
            fm[key] = []
        elif rest.startswith("[") and rest.endswith("]"):
            fm[key] = [_unquote(x) for x in rest[1:-1].split(",") if x.strip()]
        else:
            fm[key] = _unquote(rest)
        i += 1
    return fm


def iter_entries(store):
    """Yield a normalized dict per learning file under `store` (skips README.md)."""
    store = Path(store)
    for p in sorted(store.rglob("*.md")):
        if p.name.lower() == "readme.md":
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = parse_frontmatter(text)
        tags = fm.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        files = fm.get("files") or []
        if isinstance(files, str):
            files = [files]
        yield {
            "path": p,
            "title": str(fm.get("title") or p.stem),
            "tags": [str(t) for t in tags],
            "files": [str(f) for f in files],
            "status": str(fm.get("status") or "current").lower(),
            "date": str(fm.get("date") or ""),
            "verified": str(fm.get("verified") or ""),
            "category": str(fm.get("category") or p.parent.name),
        }
