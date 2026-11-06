"""Shared helpers for the lore hooks and scripts (stdlib only)."""
import fnmatch
import json
import os
import re
import unicodedata
from pathlib import Path

DEFAULTS = {
    "storeDir": "learnings",
    "maxRecall": 5,
    "staleStatuses": ["superseded", "obsolete", "deprecated"],
    "secretAllow": [],  # regexes whose match on a line suppresses secret-scan findings
    "captureNudge": "smart",  # always | smart (skip tool-less turns) | off
    "staleAfterMonths": 6,  # freshness threshold, shared by recall + the linter
    "stopWords": [],  # extra recall stop words, ADDED to the built-in list
    # Optional PRIVATE second store, read by recall alongside the team store.
    # "" = off. May be absolute, ~-prefixed, or project-relative.
    "personalStoreDir": "",
    # Optional CONSOLIDATED layers: globs of skill dirs (or SKILL.md files)
    # holding topic summaries distilled from many entries, e.g.
    # [".claude/skills/lore-*"]. [] = off, and then nothing below changes.
    "consolidatedDirs": [],
    # Body-word budget for one consolidated skill (the /lore:consolidate limit).
    "consolidatedMaxWords": 500,
}

_NUDGE_MODES = ("always", "smart", "off")

# One month of the freshness threshold, in days (365.25 / 12): 6mo -> 183d, the
# value verify_refs.py used to hardcode.
_DAYS_PER_MONTH = 30.44


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
    if key == "personalStoreDir":
        return isinstance(value, str)  # "" is the documented off switch
    if key in ("maxRecall", "staleAfterMonths", "consolidatedMaxWords"):
        return isinstance(value, int) and not isinstance(value, bool) and value > 0
    if key in ("staleStatuses", "secretAllow", "stopWords", "consolidatedDirs"):
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


def config_issues(project):
    """`(unknown_keys, invalid_keys)` in `.lore.json` -- for the linter to report.

    `load_config` swallows both (a hook must never die on a hand-edited config),
    which means a typo like `maxRecal` silently does nothing. This is where that
    becomes visible; both lists are empty when the file is absent or unparseable.
    """
    f = Path(project) / ".lore.json"
    if not f.is_file():
        return [], []
    try:
        user = json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return [], []
    if not isinstance(user, dict):
        return [], []
    unknown = sorted(k for k in user if k not in DEFAULTS)
    invalid = sorted(k for k in user if k in DEFAULTS and not _valid(k, user[k]))
    return unknown, invalid


def stale_after_days(cfg):
    """The freshness threshold in days (the linter's unit; recall uses months)."""
    return int(round(cfg["staleAfterMonths"] * _DAYS_PER_MONTH))


# --- the two stores: team (committed) + personal (private) --------------------
# The team store is committed and published; a personal preference ("always run
# the tests before you tell me you're done") does not belong in a teammate's PR.
# `personalStoreDir` is the private half: recall reads it alongside the team
# store, but it is never indexed, linted, or secret-scanned -- it isn't pushed.

def is_under(parent, path):
    """True when `path` resolves inside `parent` (or is `parent` itself)."""
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


def personal_store(project, cfg):
    """Resolved personal store Path, or None when unset/redundant.

    The configured value may be absolute, `~`-prefixed (`~/.lore/learnings`),
    or project-relative (`.lore/personal`). Returns None when the key is empty
    or when it lands inside the team store, where its entries would be found
    twice and committed anyway. Existence is the caller's business.
    """
    raw = str(cfg.get("personalStoreDir") or "").strip()
    if not raw:
        return None
    try:
        p = Path(raw).expanduser()
    except RuntimeError:  # no home dir to expand ~ against
        return None
    if not p.is_absolute():
        p = Path(project) / p
    if is_under(Path(project) / cfg["storeDir"], p):
        return None
    return p


def store_dirs(project, cfg):
    """`[(store, is_personal), ...]` to search: team store first, then personal."""
    stores = [(Path(project) / cfg["storeDir"], False)]
    personal = personal_store(project, cfg)
    if personal is not None:
        stores.append((personal, True))
    return stores


def normalize_stores(stores):
    """Accept either one store path or a `[(store, is_personal), ...]` list."""
    if isinstance(stores, (list, tuple)):
        return [(Path(s), bool(flag)) for s, flag in stores]
    return [(Path(stores), False)]


def rel_path(project, path):
    """Display path for an entry: project-relative inside, absolute outside.

    Personal entries usually live outside the repo (`~/.lore/learnings`), where
    `relative_to` would raise -- so they are shown, logged, and deduped by
    absolute path instead.
    """
    path = Path(path)
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return path.as_posix()


# --- text tokenizing (shared by recall and the dupe finder) ------------------
# Unicode-aware and diacritic-folding on purpose: `[a-z0-9_]+` split accented
# words ("autenticación" -> "autenticaci" + "n"), which quietly broke recall for
# anyone prompting in a non-English language. Folding also lets a prompt written
# with accents match tags written without them (a very common mix).

WORD_RE = re.compile(r"\w+", re.UNICODE)


def fold(text):
    """Casefold and strip diacritics: 'Autenticación' -> 'autenticacion'."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in decomposed
                   if not unicodedata.combining(c)).casefold()


def words(text):
    """Diacritic-folded word tokens of `text` (accented words stay whole)."""
    return WORD_RE.findall(fold(text))


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


# Only the frontmatter is ever needed here, and this runs on every prompt and
# every edit -- so read a bounded head instead of whole entries. 8 KB spans any
# realistic frontmatter block; the rare entry that overflows it pays a full read.
_FM_HEAD_CHARS = 8192


def read_frontmatter(path):
    """Frontmatter dict of one entry, read from a bounded head of the file.

    Returns None when the file can't be read at all. An unterminated head (a
    frontmatter block longer than the window) falls back to a full read, so
    correctness never depends on the window size.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(_FM_HEAD_CHARS)
    except OSError:
        return None
    fm = parse_frontmatter(head)
    if fm or not head.startswith("---") or len(head) < _FM_HEAD_CHARS:
        return fm
    try:
        return parse_frontmatter(
            Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def iter_entries(store, personal=False):
    """Yield a normalized dict per learning file under `store`.

    Skips README.md, the consolidation journal, and any `_`/`.`-prefixed file
    (templates, partials) so they never count as entries, show up in the index,
    or match in recall. `personal` tags every yielded entry as coming from the
    private store.
    """
    store = Path(store)
    skip = ("readme.md", LOG_NAME.lower())
    for p in sorted(store.rglob("*.md")):
        if p.name.lower() in skip or p.name[:1] in ("_", "."):
            continue
        fm = read_frontmatter(p)
        if fm is None:
            continue
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
            "personal": personal,
            # Set by /lore:consolidate: the consolidated skill that now carries
            # this entry's rules. The entry itself is never deleted for it.
            "consolidated_into": str(fm.get("consolidated_into") or ""),
            "consolidated": False,
            "description": "",
            # Empty = "match on the title", which is what an entry does.
            # Only consolidated skills override the matched text; see
            # iter_consolidated.
            "match_text": "",
        }


def iter_store_entries(stores):
    """Entries from every existing store in `stores`, team store first.

    `stores` is what `store_dirs()` returns, or a single path (the historical
    single-store call shape, kept so callers that only ever mean the team store
    stay readable).
    """
    for store, personal in normalize_stores(stores):
        if not store.is_dir():
            continue
        for e in iter_entries(store, personal=personal):
            yield e


# --- the consolidated layer --------------------------------------------------
# A store grows past the point where anyone reads it: 450 one-fact entries are
# a good archive and a bad briefing. `/lore:consolidate` distils them into a
# handful of topic skills (`.claude/skills/lore-ci/SKILL.md`), each short enough
# to load on demand. Those skills are a SECOND index for recall, not a
# replacement: the entries stay on disk, marked `consolidated_into:`, because
# only they carry the root cause, the repro, and the `files:` that power
# edit-time recall. Recall reads both layers and prefers the skill when the two
# overlap -- see recall.rank_matches.
#
# `consolidatedDirs` is empty by default, so a project that never consolidates
# behaves exactly as it did before this existed.

SKILL_FILE = "SKILL.md"

# The consolidation journal, kept inside the store. Named here (not just in
# consolidate.py) because `iter_entries` has to skip it: it is a dated log with
# no frontmatter, and left unskipped it would count as an entry everywhere.
LOG_NAME = "WEEKLY_LOG.md"


def consolidated_paths(project, cfg):
    """Resolved SKILL.md paths for every configured consolidated layer.

    Each `consolidatedDirs` glob may name skill DIRECTORIES
    (`.claude/skills/lore-*`, the usual form -- the SKILL.md inside each hit is
    used) or the markdown files themselves. Unresolvable globs are skipped
    rather than raised: this runs inside hooks.
    """
    project = Path(project)
    out = []
    for pattern in cfg.get("consolidatedDirs") or []:
        pattern = norm_rel(pattern)
        if not pattern:
            continue
        try:
            hits = sorted(project.glob(pattern))
        except (OSError, ValueError, NotImplementedError, IndexError):
            continue
        for h in hits:
            if h.is_dir():
                f = h / SKILL_FILE
                if f.is_file():
                    out.append(f)
            elif h.is_file() and h.suffix.lower() == ".md":
                out.append(h)
    return list(dict.fromkeys(out))


def _discriminating_tokens(names):
    """Per-name token sets with the family-wide tokens removed.

    Consolidated skills share a prefix by convention (`lore-ci`, `lore-auth`,
    ...), and a token present in EVERY name discriminates nothing. Left in, a
    prompt that merely says "lore" would exact-match that token on all 47
    skills and inject five of them at random. Nothing is dropped when there is
    only one skill, where there is no family to speak of.
    """
    sets = [set(words(n)) for n in names]
    if len(sets) < 2:
        return sets
    common = set.intersection(*sets)
    return [s - common for s in sets]


def iter_consolidated(project, cfg):
    """Yield a normalized entry dict per consolidated skill, recall-shaped.

    A skill is indexed by its `name` (as the title) and its `description`
    (routing prose, matched like a title), plus `tags:` and `files:` when the
    skill carries them -- which is what keeps edit-time recall working after a
    consolidation pass. The discriminating words of the name are also folded
    into `tags`, so a short prompt word ("ci", "api") still reaches `lore-ci`:
    recall matches short words against tags only, and a curated skill name is
    exactly that kind of vocabulary.

    `match_text` is what recall scores the skill on -- the discriminating name
    words plus the description, NOT the raw title. The family prefix is in the
    title and must not be scored: in a project with 47 `lore-*` skills, a
    prompt that merely says "lore" would otherwise exact-match every one of
    them and inject five at random.
    """
    raw = []
    for p in consolidated_paths(project, cfg):
        fm = read_frontmatter(p)
        if fm is None:
            continue
        raw.append((p, fm, str(fm.get("name") or p.parent.name)))
    token_sets = _discriminating_tokens([n for _p, _f, n in raw])
    for (p, fm, name), name_tokens in zip(raw, token_sets):
        tags = fm.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        files = fm.get("files") or []
        if isinstance(files, str):
            files = [files]
        description = str(fm.get("description") or "")
        tags = [str(t) for t in tags]
        tags += [t for t in sorted(name_tokens) if t not in set(words(" ".join(tags)))]
        yield {
            "path": p,
            "title": name,
            "tags": tags,
            "files": [str(f) for f in files],
            "status": str(fm.get("status") or "current").lower(),
            "date": str(fm.get("date") or ""),
            "verified": str(fm.get("verified") or ""),
            "category": str(fm.get("category") or "consolidated"),
            "personal": False,
            "consolidated_into": "",
            "consolidated": True,
            "description": description,
            "match_text": " ".join(sorted(name_tokens)) + " " + description,
        }


def consolidated_ids(project, entry):
    """Every name a `consolidated_into:` value may use for this skill.

    Entries are written by hand as often as by the command, so all the obvious
    spellings resolve: the skill `name`, its directory name, and the
    project-relative path of either the directory or the SKILL.md.
    """
    p = Path(entry["path"])
    cands = [entry["title"], p.parent.name, rel_path(project, p),
             rel_path(project, p.parent)]
    return {_cmp(norm_rel(c)) for c in cands if c}


def resolve_consolidated(project, entry, consolidated):
    """The consolidated skill an entry's `consolidated_into:` names, or None."""
    ref = _cmp(norm_rel(entry.get("consolidated_into") or ""))
    if not ref:
        return None
    for c in consolidated:
        if ref in consolidated_ids(project, c):
            return c
    return None


def skill_body(path):
    """A consolidated skill's body text (everything after the frontmatter)."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.match(r"^---\s*\n.*?\n---\s*(?:\n|$)", text, re.S)
    return text[m.end():] if m else text


def body_words(path):
    """Word count of a consolidated skill's body -- the /lore:consolidate budget."""
    return len(skill_body(path).split())


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
