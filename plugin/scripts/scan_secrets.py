#!/usr/bin/env python3
"""Secret scanner for the learnings store (stdlib only).

The lore store is committed and pushed by default, and capture is autonomous --
so a learning that quotes a real key, token, or connection string would be
published the moment it lands. This guard scans the store and FAILS (exit 1)
when it finds a likely secret, so the pre-push hook can block the push before
anything leaves the machine.

Scope: only the learnings store (default `learnings/`, or `.lore.json` storeDir),
not the whole repo -- that keeps false positives low enough for a blocking hook.

Allowlisting (for genuine false positives / illustrative examples):
  - put `lore:allow-secret` anywhere on the line, or
  - add regex strings under `secretAllow` in `.lore.json` (matched against the
    whole line; a match suppresses every finding on that line).

Usage:
  scan_secrets.py [PATH ...]   # default: the configured store dir
  scan_secrets.py --quiet      # print only on findings
Exit: 0 clean · 1 secret(s) found · 2 usage/IO error.
"""
import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import find_project_dir, load_config  # noqa: E402,E501

# (name, compiled pattern). High-signal patterns only -- this gates a blocking
# hook, so a false positive costs the user a real push. Each match is redacted
# before printing; we never echo the full secret back to the terminal/logs.
_RULES = [
    ("Private key block", r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
    ("AWS access key id", r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[0-9A-Z]{16}\b"),
    ("GitHub token", r"\b(?:ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z]{36}\b"),
    ("GitHub fine-grained PAT", r"\bgithub_pat_[0-9A-Za-z_]{22,}\b"),
    ("GitLab PAT", r"\bglpat-[0-9A-Za-z_\-]{20,}\b"),
    ("Slack token", r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    ("Slack webhook", r"https://hooks\.slack\.com/services/[A-Za-z0-9/_+\-]+"),
    ("Google API key", r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    ("Stripe live key", r"\b(?:sk|rk)_live_[0-9A-Za-z]{16,}\b"),
    # `(?!ant-)` keeps an Anthropic key from also matching here and being
    # reported twice under two vendor names.
    ("OpenAI key", r"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_\-]{20,}\b"),
    ("Anthropic key", r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b"),
    ("Twilio key", r"\bSK[0-9a-fA-F]{32}\b"),
    ("Sendgrid key", r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\b"),
    ("JWT", r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]{10,}\b"),
    ("Bearer token", r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{20,}"),
    ("Credentials in URL", r"\b[a-z][a-z0-9+.\-]*://[^\s:@/]+:([^\s:@/]{3,})@[^\s/]+"),
    # secret-ish assignment with a real-looking value (placeholders filtered below).
    # The keyword is deliberately NOT wrapped in `\b`: `_` is a word character, so
    # `\b` never fires between an underscore and a letter, and `db_password`,
    # `STRIPE_SECRET` and `aws_secret_access_key` — the dominant real-world
    # spellings — all slipped through. Affixes are flat character classes rather
    # than nested quantifiers, so backtracking stays linear.
    # The value must stay capture group 1: `_is_placeholder` reads `group(1)`.
    ("Hardcoded secret assignment",
     r"(?i)(?:^|[^A-Za-z0-9])[A-Za-z0-9_.-]{0,40}"
     r"(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|"
     r"auth[_-]?token|client[_-]?secret|private[_-]?key)"
     # `:-` is never an assignment: it is shell parameter expansion, and it put
     # the inner half of `${PUBLIC_API_KEY:-${FALLBACK:-}}` through as a value
     # even once the whole form was recognised as a placeholder. A separating
     # space is unaffected (`password: -x` still matches).
     r"[A-Za-z0-9_.-]{0,40}\s*(?::(?!-)|=)\s*"
     # `<...>`, `$(...)` and a PowerShell `(Verb-Noun ...)` call get their own
     # alternatives because they may contain spaces: the general branch stops
     # at the first one, so `<staging token>` would reach the filter below as
     # `<staging`. Each is exactly as wide as the placeholder form it feeds --
     # anything wider turns lines the general branch never matched (`<>`,
     # `(ask Will)`) into new findings.
     r"['\"]?(<[^>]+>|\$\([^)]*\)|\([a-z]+-[a-z]+\s[^)]*\)|[^\s'\"]{6,})['\"]?"),
]
RULES = [(name, re.compile(pat)) for name, pat in _RULES]

# Values that look like secrets but are obviously placeholders -- never flag.
# This filter, not the keyword match, is what decides the rule's precision: on a
# real 774-file store every single finding came from the assignment rule, and
# every one was something below that the filter failed to recognise.
_PLACEHOLDER = re.compile(
    r"^(?:"
    r"x{3,}|\.{3,}|\*{3,}|<[^>]+>|"
    # Interpolation and command substitution, as actually written in shell:
    # `${VAR}` was covered, `${VAR:-default}`, `${A:-${B:-}}` and `$(cmd arg)`
    # were not -- and a learning quotes the latter far more often.
    # A `$name` with digits is only a variable when it is spelled like one
    # (`$API_KEY2`); in lower case it is as likely a password that starts
    # with `$`.
    r"\$\{.*\}|\$\([^)]*\)|\$[a-z_]+|(?-i:\$[A-Z_][A-Z0-9_]*)|%[a-z0-9_]+%|"
    # A PowerShell call, `(Get-Content .env.local | ...)`. Any bracketed text
    # with a space in it is too wide: `(the real one is ...)` is prose.
    r"\([a-z]+-[a-z]+\s[^)]*\)|"
    r"your[_-].*|my[_-].*|some[_-].*|example.*|sample.*|dummy.*|fake.*|"
    r"test[_-].*|"
    # Values that announce they are not real: `local-test`, `postgres_dev`,
    # `...-token-change-if-shared`. The label only disarms a value made of
    # plain lower-case words -- one digit or capital anywhere and it stays a
    # finding, because `staging-8f7d6s5a` and `Hunter2-dev` are credentials
    # that happen to say where they are used.
    r"(?-i:(?:[a-z]+[_.:-])*"
    r"(?:test|local|localhost|dev|staging|example|sample|dummy|fake)"
    r"(?:[_.:-][a-z]+)*)|"
    r"localhost:\d{1,5}|"
    r"(?:[a-z]+[_.-])*change[_-]?(?:me|it|if|this|before)(?:[_.-][a-z]+)*|"
    r"changeme|placeholder|redacted|none|null|true|false|"
    r"pass|passwd|password|pwd|secret|user|username|admin|host|hostname"
    r")$",
    re.I,
)
# Punctuation that wraps a value without belonging to it. The store is Markdown,
# so the value group swallows the closing backtick of `inline code` and the
# sentence's own period: `import_secrets=False`). arrives as "False`)." and no
# anchored pattern above can match it. `>` is deliberately absent -- stripping it
# would break `<your-key>`.
_WRAP_HEAD = "'\"`"
_WRAP_TAIL = "'\"`)]},.;:!?"
ALLOW_INLINE = "lore:allow-secret"


def _unwrapped(val):
    """`val` and every progressively-unwrapped form of it, outermost first.

    One fully-stripped string cannot serve every value, because the same
    character is wrapper punctuation in one and the value itself in another:
    `${MY_KEY}`. needs its closing brace kept and its backtick dropped, while
    `import_secrets=False`). needs both dropped, and `....` needs nothing
    dropped at all. So trim one character at a time and let the caller test
    every form -- unwrapping can only add a chance to match, never remove one.
    """
    forms, cur = [val], val
    while cur:
        if cur[-1] in _WRAP_TAIL:
            cur = cur[:-1].strip()
        elif cur[0] in _WRAP_HEAD:
            cur = cur[1:].strip()
        else:
            break
        if cur and cur not in forms:
            forms.append(cur)
    return forms


def _redact(s):
    s = s.strip().strip("'\"")
    if len(s) <= 8:
        return s[0] + "***" if s else "***"
    return f"{s[:4]}...{s[-2:]} ({len(s)} chars)"


def _is_placeholder(match):
    # Rules with a capture group expose the value (assignment, URL password);
    # for the rest, judge the whole match.
    raw = (match.group(1) if match.lastindex else match.group(0)).strip()
    return any(_PLACEHOLDER.match(v) for v in _unwrapped(raw))


def scan_file(path, allow_res):
    findings = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return findings
    for n, line in enumerate(lines, 1):
        if ALLOW_INLINE in line or any(r.search(line) for r in allow_res):
            continue
        for name, rule in RULES:
            for m in rule.finditer(line):
                if _is_placeholder(m):
                    continue
                findings.append((n, name, _redact(m.group(0))))
                break  # one finding per rule per line is enough signal
    return findings


def iter_targets(paths):
    for p in paths:
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    yield f
        elif p.is_file():
            yield p


def main():
    ap = argparse.ArgumentParser(description="Scan the lore store for committed secrets.")
    ap.add_argument("paths", nargs="*", help="files/dirs to scan (default: the store dir)")
    ap.add_argument("--quiet", action="store_true", help="print only when secrets are found")
    args = ap.parse_args()

    project = find_project_dir()
    cfg = load_config(project)
    allow_res = []
    for pat in cfg.get("secretAllow") or []:
        try:
            allow_res.append(re.compile(pat))
        except re.error:
            pass  # a bad allow-regex must never crash the guard

    if args.paths:
        targets = [Path(p) if os.path.isabs(p) else project / p for p in args.paths]
    else:
        store = project / cfg["storeDir"]
        if not store.is_dir():
            if not args.quiet:
                print(f"No learnings store at {store} - nothing to scan.")
            return 0
        targets = [store]

    all_findings = []
    for f in iter_targets(targets):
        for n, name, redacted in scan_file(f, allow_res):
            try:  # display path relative to the project when possible (3.8-safe)
                rel = f.relative_to(project).as_posix()
            except ValueError:
                rel = f.as_posix()
            all_findings.append((rel, n, name, redacted))

    if not all_findings:
        if not args.quiet:
            print("OK  secret scan: no likely secrets in the learnings store.")
        return 0

    print(f"\nSECRETS FOUND  {len(all_findings)} likely secret(s) in the learnings store:\n")
    cur = None
    for rel, n, name, redacted in all_findings:
        if rel != cur:
            print(f"  {rel}")
            cur = rel
        print(f"    line {n}: {name} -> {redacted}")
    print("\n  -> Remove the secret from the learning (reference it, never quote it),")
    print("     ROTATE it if it was real, then retry. False positive? Add `lore:allow-secret`")
    print("     to the line, or a regex under `secretAllow` in .lore.json.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
