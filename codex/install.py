#!/usr/bin/env python3
"""Install the Lore recall + capture hooks for OpenAI Codex (stdlib only).

Default: a GLOBAL (user-scoped) install. Copies the shared Lore scripts into
$CODEX_HOME/lore, registers the UserPromptSubmit (recall) and Stop (capture)
hooks in $CODEX_HOME/hooks.json with the absolute interpreter path baked in,
installs the capture skill under ~/.agents/skills/lore, and enables
`[features] hooks = true` in config.toml.

  python3 codex/install.py            # global install (once per machine)
  python3 codex/install.py --store    # scaffold a learnings/ store in THIS project
  python3 codex/install.py --uninstall

Why bake the interpreter path: Codex hook `command` is a single shell string with
no args array and no plugin-root variable, and `python3`/`python`/`py` differ per
OS. Baking `sys.executable` (the interpreter that ran this installer) makes the
hook command work without a wrapper. Recall then runs on every prompt in every
project; the store lives per-project in ./learnings (created on first capture).
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent            # repo/codex
SRC = HERE.parent / "plugin"                       # shared core lives under plugin/
SCRIPTS = SRC / "scripts"
SKILL_SRC = SRC / "skills" / "lore" / "SKILL.md"
TEMPLATES = SRC / "templates"
CORE = ["recall.py", "capture_check.py", "verify_refs.py", "scan_secrets.py", "_common.py"]


def codex_home():
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def skills_root():
    # Documented user-scope Codex skills path; env override is for testing.
    return Path(os.environ.get("CODEX_SKILLS_DIR") or (Path.home() / ".agents" / "skills"))


def _is_ours(command, lore_dir):
    return lore_dir.as_posix() in command


def _merge_hooks(hooks_path, lore_dir):
    py = sys.executable
    recall = f'"{py}" "{(lore_dir / "recall.py").as_posix()}"'
    capture = f'"{py}" "{(lore_dir / "capture_check.py").as_posix()}"'
    groups = {
        "UserPromptSubmit": {"matcher": "", "hooks": [
            {"type": "command", "command": recall, "timeout": 30}]},
        "Stop": {"matcher": "", "hooks": [
            {"type": "command", "command": capture, "timeout": 30}]},
    }
    data = {}
    if hooks_path.is_file():
        try:
            data = json.loads(hooks_path.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    if not isinstance(data, dict):
        data = {}
    hooks = data.setdefault("hooks", {})
    for event, group in groups.items():
        existing = hooks.get(event)
        existing = existing if isinstance(existing, list) else []
        kept = []
        for g in existing:  # drop our prior group (idempotent), keep the user's
            cmds = " ".join(
                h.get("command", "") for h in g.get("hooks", []) if isinstance(h, dict))
            if not _is_ours(cmds, lore_dir):
                kept.append(g)
        kept.append(group)
        hooks[event] = kept
    hooks_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _enable_hooks_feature(config_path):
    text = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    if "hooks = true" in text.lower() or "hooks=true" in text.lower():
        return "already enabled"
    if "[features]" in text:
        out = []
        for ln in text.splitlines():
            out.append(ln)
            if ln.strip() == "[features]":
                out.append("hooks = true")
        text = "\n".join(out) + "\n"
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n[features]\nhooks = true\n"
    config_path.write_text(text, encoding="utf-8")
    return "enabled"


def install():
    if not SCRIPTS.is_dir():
        sys.exit(f"Cannot find core scripts at {SCRIPTS}; run this from the Lore repo.")
    home = codex_home()
    lore_dir = home / "lore"
    lore_dir.mkdir(parents=True, exist_ok=True)
    for name in CORE:
        shutil.copy2(SCRIPTS / name, lore_dir / name)
    tdst = lore_dir / "templates"
    if tdst.exists():
        shutil.rmtree(tdst)
    shutil.copytree(TEMPLATES, tdst)
    skill_dir = skills_root() / "lore"
    skill_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SKILL_SRC, skill_dir / "SKILL.md")
    _merge_hooks(home / "hooks.json", lore_dir)
    feat = _enable_hooks_feature(home / "config.toml")

    print("Lore installed for Codex.")
    print(f"  scripts : {lore_dir}")
    print(f"  hooks   : {home / 'hooks.json'}  (UserPromptSubmit -> recall, Stop -> capture)")
    print(f"  skill   : {skill_dir / 'SKILL.md'}")
    print(f"  config  : [features] hooks {feat}")
    print(f"  python  : {sys.executable}")
    print("\nNext:")
    print("  1. Open Codex and run /hooks to REVIEW AND TRUST the new hooks")
    print("     (Codex skips untrusted command hooks until you approve them).")
    print("  2. In a project:  python3 codex/install.py --store   (scaffold ./learnings)")
    print("     or just start working — the store is created on first capture.")
    return 0


def scaffold_store(target):
    store = Path(target).resolve() / "learnings"
    if store.exists():
        print(f"Store already exists: {store}")
        return 0
    (store / "examples").mkdir(parents=True, exist_ok=True)
    shutil.copy2(TEMPLATES / "store-README.md", store / "README.md")
    shutil.copy2(TEMPLATES / "_TEMPLATE.md", store / "_TEMPLATE.md")
    shutil.copy2(TEMPLATES / "example-learning.md", store / "examples" / "example-learning.md")
    print(f"Scaffolded learnings store at {store}")
    print("  Committed and pushed by default (a team store) — treat it as PUBLISHED:")
    print("  never quote a secret/credential/PII in a learning. Scan before sharing:")
    print(f"    {sys.executable} {codex_home() / 'lore' / 'scan_secrets.py'}")
    print("  To keep it private instead, add 'learnings/' to .gitignore.")
    return 0


def uninstall():
    home = codex_home()
    lore_dir = home / "lore"
    hooks_path = home / "hooks.json"
    if hooks_path.is_file():
        try:
            data = json.loads(hooks_path.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
        for event in ("UserPromptSubmit", "Stop"):
            groups = data.get("hooks", {}).get(event, [])
            kept = [g for g in groups if not _is_ours(
                " ".join(h.get("command", "") for h in g.get("hooks", [])), lore_dir)]
            if isinstance(data.get("hooks"), dict):
                data["hooks"][event] = kept
        hooks_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if lore_dir.exists():
        shutil.rmtree(lore_dir)
    skill_dir = skills_root() / "lore"
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    print("Removed Lore Codex hooks/scripts/skill. "
          "([features] hooks left as-is; project learnings stores untouched.)")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Install Lore for OpenAI Codex.")
    ap.add_argument("--store", nargs="?", const=".", metavar="DIR",
                    help="scaffold a learnings/ store in DIR (default: current project)")
    ap.add_argument("--uninstall", action="store_true", help="remove the Codex install")
    args = ap.parse_args()
    if args.uninstall:
        return uninstall()
    if args.store is not None:
        return scaffold_store(args.store)
    return install()


if __name__ == "__main__":
    sys.exit(main())
