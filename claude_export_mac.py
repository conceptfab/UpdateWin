#!/usr/bin/env python3
"""Eksport zestawu Claude Code z Maca — jeden plik, bez innych zależności.

Uruchom w Terminalu:   python3 claude_export_mac.py
Wynik:                 ~/Desktop/claude-setup.json  (przenieś go na Windows i zrób `claude_sync.py import`)

Zbiera: marketplace'y (~/.claude/plugins/known_marketplaces.json), pluginy (`claude plugin list --json`),
skille `npx skills` (~/.agents/.skill-lock.json), nazwy lokalnych skilli (~/.claude/skills/*).
Format = ten sam co `claude_sync.py export`.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
OUT = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else HOME / "Desktop" / "claude-setup.json"
REPO = re.compile(r"^[\w.-]+/[\w.-]+$")
GIT_URL = re.compile(r"^https://[\w.-]+/[\w./~-]+$")


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def find_claude():
    found = shutil.which("claude")
    if found:
        return found
    for p in (HOME / ".local/bin/claude", HOME / ".claude/local/claude",
              Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")):
        if p.exists():
            return str(p)
    return None


def marketplaces():
    out = {}
    for name, e in (read_json(HOME / ".claude/plugins/known_marketplaces.json") or {}).items():
        src = (e or {}).get("source") or {}
        kind = src.get("source")
        if kind == "github" and REPO.match(src.get("repo", "")):
            out[name] = {"type": "github", "repo": src["repo"]}
        elif kind in ("git", "url") and GIT_URL.match(src.get("url", "")):
            out[name] = {"type": "git", "url": src["url"]}
        elif kind in ("directory", "file"):
            out[name] = {"type": "directory", "path": src.get("path", "")}
    return out


def plugins():
    claude = find_claude()
    if not claude:
        print("!! nie znalazłem `claude` — pluginy pominięte (marketplace'y i skille i tak zapisane)")
        return []
    r = subprocess.run([claude, "plugin", "list", "--json"], capture_output=True, text=True,
                       env={**os.environ, "NO_COLOR": "1"}, timeout=120)
    text = r.stdout
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if r.returncode != 0 or not starts:
        print(f"!! `claude plugin list --json` nie zadziałało: {(r.stderr or r.stdout).strip()[-300:]}")
        return []
    data = json.loads(text[min(starts):])
    installed = data.get("installed", []) if isinstance(data, dict) else data
    return sorted({p["id"] for p in installed if "@" in (p.get("id") or "")})


def skills():
    lock = read_json(HOME / ".agents/.skill-lock.json") or {}
    return {n: {"source": s.get("source"), "sourceType": s.get("sourceType")}
            for n, s in (lock.get("skills") or {}).items()}


def local_skills():
    d = HOME / ".claude/skills"
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.is_dir() else []


def main():
    snap = {"marketplaces": marketplaces(), "plugins": plugins(), "skills": skills(), "localSkills": local_skills()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Zapisano {OUT}")
    print(f"  marketplace'y: {len(snap['marketplaces'])}")
    print(f"  pluginy:       {len(snap['plugins'])}")
    print(f"  skille (npx):  {len(snap['skills'])}")
    print(f"  skille lokalne:{len(snap['localSkills']):>2}  (do skopiowania ręcznie z ~/.claude/skills)")
    print("\nNa Windows:  python claude_sync.py import claude-setup.json --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
