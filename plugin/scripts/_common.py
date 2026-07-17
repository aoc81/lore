"""Shared helpers for the lore hooks and scripts (stdlib only)."""
import fnmatch
import json
import os
import re
from pathlib import Path

DEFAULTS = {
    "storeDir": "learnings",
    "maxRecall": 5,
    "staleStatuses": ["superseded", "obsolete", "deprecated"],
    "secretAllow": [],  # regexes whose match on a line suppresses secret-scan findings
    "captureNudge": "smart",  # always | smart (skip tool-less turns) | off
}

_NUDGE_MODES = ("always", "smart", "off")


def find_project_dir(data=None):
    """The user's project root: hook stdin `cwd`, else $CLAUDE_PROJECT_DIR, else cwd."""
    if data and data.get("cwd"):
        return Path(data["cwd"])
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    return Path.cwd()


def _valid(key, value):
    """Type-check one `.lore.json` value; a bad type must never crash a hook."""
    if key == "storeDir":
        return isinstance(value, str) and bool(value.strip())
    if key == "maxRecall":
        return isinstance(value, int) and not isinstance(value, bool) and value > 0
    if key in ("staleStatuses", "secretAllow"):
        return (isinstance(value, list)
                and all(isinstance(x, str) for x in value))
    if key == "captureNudge":
        return isinstance(value, bool) or value in _NUDGE_MODES
    return False


def load_config(project):
    """Merge `.lore.json` (if present in the project root) over DEFAULTS.

    Unknown keys and wrongly-typed values are ignored (hooks must never die on
    a hand-edited config). `captureNudge` accepts booleans as shorthand:
    true -> "smart" (the default), false -> "off".
    """
    cfg = dict(DEFAULTS)
    f = Path(project) / ".lore.json"
    if f.is_file():
        try:
            user = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                cfg.update({k: v for k, v in user.items()
                            if k in DEFAULTS and _valid(k, v)})
        except (ValueError, OSError):
            pass
    if isinstance(cfg["captureNudge"], bool):
        cfg["captureNudge"] = "smart" if cfg["captureNudge"] else "off"
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
    """Yield a normalized dict per learning file under `store`.

    Skips README.md and any `_`/`.`-prefixed file (templates, partials) so they
    never count as entries, show up in the index, or match in recall.
    """
    store = Path(store)
    for p in sorted(store.rglob("*.md")):
        if p.name.lower() == "readme.md" or p.name[:1] in ("_", "."):
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


# --- file-reference matching -------------------------------------------------
# A frontmatter `files:` ref can be an exact path, a directory prefix (ends
# with "/"), or a glob (contains * ? [ ). Paths compare case-insensitively on
# Windows, where the filesystem itself is case-insensitive.

def norm_rel(p):
    """Normalize a relative path for comparison: /-separators, no leading ./"""
    p = str(p).strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _cmp(p):
    return p.casefold() if os.name == "nt" else p


def ref_matches(ref, rel):
    """True when `files:` ref `ref` covers the project-relative path `rel`."""
    ref, rel = _cmp(norm_rel(ref)), _cmp(norm_rel(rel))
    if not ref:
        return False
    if any(c in ref for c in "*?["):
        return fnmatch.fnmatchcase(rel, ref)
    if ref.endswith("/"):
        return rel.startswith(ref)
    return rel == ref


def ref_exists(project, ref):
    """True when a `files:` ref (path, dir prefix, or glob) resolves to
    something that still exists under `project`."""
    project = Path(project)
    ref = norm_rel(ref)
    if not ref:
        return True  # empty refs are noise, not staleness evidence
    if any(c in ref for c in "*?["):
        try:
            return next(iter(project.glob(ref)), None) is not None
        except (OSError, ValueError, NotImplementedError):
            return True  # an unresolvable glob must not flag the entry
    if ref.endswith("/"):
        return (project / ref.rstrip("/")).is_dir()
    return (project / ref).exists()
