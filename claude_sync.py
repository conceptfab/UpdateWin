#!/usr/bin/env python3
"""claude_sync: przenosi zestaw dodatków Claude Code / skilli z jednego komputera na drugi.

Na Macu (źródło):     python3 claude_sync.py export claude-setup.json
Na Windows (cel):     python claude_sync.py import claude-setup.json            # podgląd: czego brakuje
                      python claude_sync.py import claude-setup.json --apply    # doinstaluj brakujące

Eksport: marketplace'y (~/.claude/plugins/known_marketplaces.json), pluginy (`claude plugin list --json`),
skille `npx skills` (~/.agents/.skill-lock.json) i nazwy lokalnych skilli (~/.claude/skills/*).
Import dodaje brakujące marketplace'y (github / git), instaluje brakujące pluginy i skille z lock.
Lokalnych skilli i marketplace'ów z folderu nie da się zainstalować zdalnie — są tylko wypisane.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import update
from update import HOME, SAFE_NAME, REPO, json_body, read_json, run_ok, tool

PLUGINS_DIR = HOME / ".claude" / "plugins"
LOCAL_SKILLS = HOME / ".claude" / "skills"
GIT_URL = re.compile(r"^https://[\w.-]+/[\w./~-]+$")


# MARK: - Odczyt stanu tego komputera

def marketplace_source(entry: dict) -> dict | None:
    """{"type": "github", "repo": "o/r"} | {"type": "git", "url": ...} | {"type": "directory", "path": ...}."""
    src = entry.get("source") or {}
    kind = src.get("source")
    if kind == "github" and REPO.match(src.get("repo", "")):
        return {"type": "github", "repo": src["repo"]}
    if kind in ("git", "url") and GIT_URL.match(src.get("url", "")):
        return {"type": "git", "url": src["url"]}
    if kind in ("directory", "file"):
        return {"type": "directory", "path": src.get("path", "")}
    return None


def local_marketplaces() -> dict[str, dict]:
    known = read_json(PLUGINS_DIR / "known_marketplaces.json") or {}
    return {name: s for name, e in known.items() if isinstance(e, dict) and (s := marketplace_source(e))}


def local_plugins() -> list[str]:
    try:
        data = json_body(run_ok([tool("claude"), "plugin", "list", "--json"], 120))
    except update.Skip:
        return []
    installed = data.get("installed", []) if isinstance(data, dict) else data
    return sorted({p["id"] for p in installed if "@" in (p.get("id") or "")})


def local_skill_lock() -> dict[str, dict]:
    lock = read_json(HOME / ".agents" / ".skill-lock.json") or {}
    return {n: {"source": s.get("source"), "sourceType": s.get("sourceType")}
            for n, s in (lock.get("skills") or {}).items()}


def local_skill_dirs() -> list[str]:
    return sorted(p.name for p in LOCAL_SKILLS.iterdir() if p.is_dir()) if LOCAL_SKILLS.is_dir() else []


def snapshot() -> dict:
    return {"marketplaces": local_marketplaces(), "plugins": local_plugins(),
            "skills": local_skill_lock(), "localSkills": local_skill_dirs()}


# MARK: - Różnica i plan

def diff(want: dict, have: dict) -> dict:
    return {
        "marketplaces": {n: s for n, s in (want.get("marketplaces") or {}).items()
                         if n not in have["marketplaces"]},
        "plugins": [p for p in want.get("plugins") or [] if p not in have["plugins"]],
        "skills": {n: s for n, s in (want.get("skills") or {}).items() if n not in have["skills"]},
        "localSkills": [n for n in want.get("localSkills") or [] if n not in have["localSkills"]],
    }


def commands(missing: dict) -> tuple[list[list[str]], list[str]]:
    """(polecenia do uruchomienia, rzeczy do zrobienia ręcznie)."""
    cmds, manual = [], []
    for name, s in sorted(missing["marketplaces"].items()):
        if s["type"] == "github":
            cmds.append(["claude", "plugin", "marketplace", "add", s["repo"]])
        elif s["type"] == "git":
            cmds.append(["claude", "plugin", "marketplace", "add", s["url"]])
        else:
            manual.append(f"marketplace {name}: folder na Macu ({s.get('path')}) — skopiuj go i "
                          f"`claude plugin marketplace add <folder>`")
    for pid in missing["plugins"]:
        if SAFE_NAME.match(pid) and not pid.endswith(("@synced", "@skills-dir")):
            cmds.append(["claude", "plugin", "install", pid])
        else:
            manual.append(f"plugin {pid}: nie z marketplace'u — pomijam")
    by_repo: dict[str, list[str]] = {}
    for name, s in sorted(missing["skills"].items()):
        if s.get("sourceType") == "github" and REPO.match(s.get("source") or "") and SAFE_NAME.match(name):
            by_repo.setdefault(s["source"], []).append(name)
        else:
            manual.append(f"skill {name}: źródło {s.get('source')} — doinstaluj ręcznie")
    for repo, names in sorted(by_repo.items()):
        args = ["npx", "-y", "skills", "add", repo, "-g", "-y"]
        for n in names:
            args += ["--skill", n]
        cmds.append(args)
    for name in missing["localSkills"]:
        manual.append(f"lokalny skill {name}: skopiuj ~/.claude/skills/{name} z Maca")
    return cmds, manual


# MARK: - CLI

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    p = argparse.ArgumentParser(description="Przenosi pluginy Claude Code i skille między komputerami.")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="zapisz zestaw z tego komputera")
    e.add_argument("file", type=Path)
    i = sub.add_parser("import", help="porównaj z plikiem i doinstaluj brakujące")
    i.add_argument("file", type=Path)
    i.add_argument("--apply", action="store_true", help="uruchom polecenia (domyślnie tylko podgląd)")
    args = p.parse_args(argv)

    if args.cmd == "export":
        snap = snapshot()
        args.file.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Zapisano {args.file}: {len(snap['marketplaces'])} marketplace'ów, {len(snap['plugins'])} pluginów, "
              f"{len(snap['skills'])} skilli (npx skills), {len(snap['localSkills'])} lokalnych skilli")
        return 0

    want = read_json(args.file)
    if not isinstance(want, dict):
        p.error(f"nie mogę odczytać {args.file}")
    missing = diff(want, snapshot())
    cmds, manual = commands(missing)
    if not cmds and not manual:
        print("Ten komputer ma już wszystko z pliku.")
        return 0
    print("Do zainstalowania:" if cmds else "Nic do zainstalowania automatycznie.")
    for c in cmds:
        print("  " + " ".join(c))
    if manual:
        print("Ręcznie:")
        for m in manual:
            print("  - " + m)
    if not args.apply:
        print("\nPodgląd. Uruchom z --apply, żeby wykonać polecenia.")
        return 0
    failed = 0
    for c in cmds:
        exe = tool(c[0])  # claude.cmd / npx.cmd przez PATHEXT
        if not update.call([exe, *c[1:]]):
            failed += 1
    print(f"\nGotowe{f', błędy: {failed}' if failed else ''}. Pluginy działają w nowych sesjach.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
