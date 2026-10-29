#!/usr/bin/env python3
"""claude_sync: przenosi zestaw dodatków Claude Code / skilli z jednego komputera na drugi.

Na Macu (źródło):     python3 claude_export_mac.py   (albo claude_sync.py export), potem git commit + push
Na Windows (cel):     git pull, python claude_sync.py import            # podgląd: czego brakuje
                      python claude_sync.py import --apply    # doinstaluj brakujące

Eksport: marketplace'y (~/.claude/plugins/known_marketplaces.json), pluginy (`claude plugin list --json`),
skille `npx skills` (~/.agents/.skill-lock.json), nazwy lokalnych skilli (~/.claude/skills/*)
i kopie ich folderów w repo/skills.
Import dodaje brakujące marketplace'y (github / git), instaluje brakujące pluginy i skille z lock,
a skille, których nie da npx (lokalne albo usunięte z GitHuba), kopiuje z repo/skills.
Marketplace'ów z folderu nie da się zainstalować zdalnie — są tylko wypisane.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import update
from update import HOME, SAFE_NAME, REPO, json_body, read_json, run_ok, tool

PLUGINS_DIR = HOME / ".claude" / "plugins"
LOCAL_SKILLS = HOME / ".claude" / "skills"
BUNDLE = Path(__file__).resolve().parent / "skills"  # kopie folderów skilli w repo (eksport z Maca)
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
        "localSkills": [n for n in want.get("localSkills") or [] if n not in have["localSkills"]
                        and not n.startswith(".")],
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
        # skille z lock instaluje npx (i linkuje do ~/.claude/skills); kopia z repo dopiero, gdy npx ich nie da
        if name not in missing["skills"] and not (BUNDLE / name).is_dir():
            manual.append(f"lokalny skill {name}: brak w repo/skills — zrób eksport na Macu jeszcze raz")
    return cmds, manual


def bundle_skills() -> int:
    """Kopiuje foldery z ~/.claude/skills (linki rozwiązane) do repo/skills."""
    shutil.rmtree(BUNDLE, ignore_errors=True)
    names = [n for n in local_skill_dirs() if not n.startswith(".")]
    for n in names:
        shutil.copytree(LOCAL_SKILLS / n, BUNDLE / n, ignore=shutil.ignore_patterns(".git"))
    return len(names)


def copy_bundled(names: list[str]) -> list[str]:
    """Kopiuje z repo/skills te skille, których nadal brakuje w ~/.claude/skills."""
    copied = []
    for n in names:
        if SAFE_NAME.match(n) and (BUNDLE / n).is_dir() and not (LOCAL_SKILLS / n).exists():
            shutil.copytree(BUNDLE / n, LOCAL_SKILLS / n)
            copied.append(n)
    return copied


# MARK: - CLI

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    p = argparse.ArgumentParser(description="Przenosi pluginy Claude Code i skille między komputerami.")
    sub = p.add_subparsers(dest="cmd", required=True)
    default_file = Path(__file__).resolve().parent / "claude-setup.json"  # w repo, synchronizowany przez git
    e = sub.add_parser("export", help="zapisz zestaw z tego komputera")
    e.add_argument("file", type=Path, nargs="?", default=default_file)
    i = sub.add_parser("import", help="porównaj z plikiem i doinstaluj brakujące")
    i.add_argument("file", type=Path, nargs="?", default=default_file)
    i.add_argument("--apply", action="store_true", help="uruchom polecenia (domyślnie tylko podgląd)")
    args = p.parse_args(argv)

    if args.cmd == "export":
        snap = snapshot()
        args.file.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Zapisano {args.file}: {len(snap['marketplaces'])} marketplace'ów, {len(snap['plugins'])} pluginów, "
              f"{len(snap['skills'])} skilli (npx skills), {len(snap['localSkills'])} lokalnych skilli")
        print(f"Skopiowano {bundle_skills()} folderów skilli do {BUNDLE}")
        return 0

    want = read_json(args.file)
    if not isinstance(want, dict):
        p.error(f"nie mogę odczytać {args.file}")
    missing = diff(want, snapshot())
    cmds, manual = commands(missing)
    to_copy = [n for n in missing["localSkills"] if n not in missing["skills"] and (BUNDLE / n).is_dir()]
    if not cmds and not manual and not to_copy:
        print("Ten komputer ma już wszystko z pliku.")
        return 0
    print("Do zainstalowania:" if cmds else "Nic do zainstalowania automatycznie.")
    for c in cmds:
        print("  " + " ".join(c))
    if to_copy:
        print(f"Skopiuję z repo/skills do ~/.claude/skills ({len(to_copy)}): {', '.join(to_copy)}")
        print("  + te skille z npx, których npx już nie da (usunięte z GitHuba), jeśli są w repo/skills")
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
    copied = copy_bundled(missing["localSkills"])  # po npx: to, czego npx nie zainstalował
    if copied:
        print(f"Skopiowano z repo ({len(copied)}): {', '.join(copied)}")
    print(f"\nGotowe{f', błędy: {failed}' if failed else ''}. Pluginy działają w nowych sesjach.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
