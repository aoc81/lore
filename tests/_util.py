"""Shared helpers for the lore test suite (stdlib only)."""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugin" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def write_entry(store, relpath, title="", tags=(), files=(), status="current",
                date="", verified="", body="Body."):
    """Write a minimal learning file under `store` and return its Path."""
    p = Path(store) / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = ["---", f'title: "{title}"']
    if date:
        fm.append(f"date: {date}")
    fm.append(f"tags: [{', '.join(tags)}]")
    fm.append(f"files: [{', '.join(files)}]")
    fm.append(f"status: {status}")
    if verified:
        fm.append(f"verified: {verified}")
    fm.append("---")
    p.write_text("\n".join(fm) + "\n\n" + body + "\n", encoding="utf-8")
    return p
