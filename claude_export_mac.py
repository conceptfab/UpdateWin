#!/usr/bin/env python3
"""Eksport zestawu Claude Code z Maca — jeden plik, bez innych zależności.

Uruchom w Terminalu:   python3 claude_export_mac.py [--push] [plik]
Wynik:                 claude-setup.json + skills/ (kopie folderów skilli i modów) w repo, obok skryptu;
                       --push: od razu git commit + push. Na Windows: UpdateWin → „Synchronizuj z Maca”.

Zbiera: marketplace'y (~/.claude/plugins/known_marketplaces.json), aktywne pluginy (`claude plugin list --json`),
skille `npx skills` (~/.agents/.skill-lock.json), nazwy lokalnych skilli i modów (~/.claude/skills/*).
Format = ten sam co `claude_sync.py export`.
"""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()
PUSH = "--push" in sys.argv[1:]
_args = [a for a in sys.argv[1:] if a != "--push"]
OUT = Path(_args[0]).expanduser() if _args else Path(__file__).resolve().parent / "claude-setup.json"
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
        return {}
    r = subprocess.run([claude, "plugin", "list", "--json"], capture_output=True, text=True,
                       env={**os.environ, "NO_COLOR": "1"}, timeout=120)
    text = r.stdout
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if r.returncode != 0 or not starts:
        print(f"!! `claude plugin list --json` nie zadziałało: {(r.stderr or r.stdout).strip()[-300:]}")
        return {}
    data = json.loads(text[min(starts):])
    installed = data.get("installed", []) if isinstance(data, dict) else data
    # zakres user: synced = logowanie aplikacji Claude, project = osobno w każdym repo
    return {p["id"]: bool(p.get("enabled")) for p in installed
            if "@" in (p.get("id") or "") and p.get("scope", "user") == "user"}


def skills():
    lock = read_json(HOME / ".agents/.skill-lock.json") or {}
    return {n: {"source": s.get("source"), "sourceType": s.get("sourceType")}
            for n, s in (lock.get("skills") or {}).items()}


def local_skills():
    d = HOME / ".claude/skills"
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.is_dir() else []


def bundle_skills(dest):
    """Kopie folderów ~/.claude/skills (linki z npx rozwiązane) — na Windows import bierze stąd to, czego npx nie da."""
    shutil.rmtree(dest, ignore_errors=True)
    names = [n for n in local_skills() if not n.startswith(".")]
    for n in names:
        shutil.copytree(HOME / ".claude/skills" / n, dest / n, ignore=shutil.ignore_patterns(".git", ".DS_Store"))
    return names


def git(*args):
    print("$ git " + " ".join(args), flush=True)
    return subprocess.run(["git", "-C", str(OUT.parent), *args]).returncode == 0


def push():
    paths = [OUT.name, "skills"]
    if not git("add", "-A", "--", *paths):
        return False
    if subprocess.run(["git", "-C", str(OUT.parent), "diff", "--cached", "--quiet"]).returncode == 0:
        print("Bez zmian od ostatniego eksportu.")
        return True
    return git("commit", "-m", f"claude setup {platform.node()}", "--", *paths) and git("push")


def main():
    states = plugins()
    # version 2 = tylko aktywne pluginy; import na Windows robi z tego lustro 1:1
    snap = {"version": 2, "exportedAt": datetime.now().isoformat(timespec="seconds"), "host": platform.node(),
            "marketplaces": marketplaces(),
            "plugins": sorted(p for p, on in states.items() if on),
            "disabledPlugins": sorted(p for p, on in states.items() if not on),
            "skills": skills(), "localSkills": local_skills()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    bundled = bundle_skills(OUT.parent / "skills")
    print(f"Zapisano {OUT}")
    print(f"  marketplace'y:    {len(snap['marketplaces'])}")
    print(f"  aktywne pluginy:  {len(snap['plugins'])}  (wyłączone, nieprzenoszone: {len(snap['disabledPlugins'])})")
    print(f"  skille (npx):     {len(snap['skills'])}")
    print(f"  skille i mody:    {len(snap['localSkills'])}  (foldery skopiowane do {OUT.parent / 'skills'}: {len(bundled)})")
    if PUSH:
        if not push():
            print("!! git commit / push nie powiódł się")
            return 1
    else:
        print("\nTeraz: python3 claude_export_mac.py --push   (albo git add claude-setup.json skills, commit, push)")
    print("Na Windows: UpdateWin → „Synchronizuj z Maca”  (albo git pull i python claude_sync.py import --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
