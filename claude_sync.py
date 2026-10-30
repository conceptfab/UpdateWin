#!/usr/bin/env python3
"""claude_sync: ustawia na drugim komputerze ten sam aktywny zestaw Claude Code / skilli (1:1).

Na Macu (źródło):     python3 claude_export_mac.py --push   (albo claude_sync.py export --push)
Na Windows (cel):     git pull, python claude_sync.py import            # podgląd zmian
                      python claude_sync.py import --apply    # wykonaj
W GUI: przycisk „Eksportuj na Windows” (Mac) / „Synchronizuj z Maca” (Windows).

Eksport: marketplace'y (~/.claude/plugins/known_marketplaces.json), aktywne pluginy (`claude plugin list --json`,
zakres user), skille `npx skills` (~/.agents/.skill-lock.json), nazwy lokalnych skilli i modów (~/.claude/skills/*)
i kopie ich folderów w repo/skills.
Import dodaje brakujące marketplace'y (github / git), instaluje / włącza pluginy aktywne na Macu, wyłącza
pozostałe, instaluje i usuwa skille z lock, kopiuje (lub podmienia) skille i mody z repo/skills, a te, których
na Macu nie ma, przenosi do ~/.claude/skills/.trash. Marketplace'ów z folderu nie da się zainstalować zdalnie —
są tylko wypisane.
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import update
from update import HOME, SAFE_NAME, REPO, json_body, read_json, run_ok, tool

PLUGINS_DIR = HOME / ".claude" / "plugins"
LOCAL_SKILLS = HOME / ".claude" / "skills"
ROOT = Path(__file__).resolve().parent  # repo, synchronizowane przez git
BUNDLE = ROOT / "skills"  # kopie folderów skilli w repo (eksport z Maca)
SETUP_FILE = ROOT / "claude-setup.json"
SKIP = (".git", ".DS_Store")  # nie kopiowane ani nie porównywane
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


def plugin_states() -> dict[str, bool]:
    """plugin@marketplace -> enabled, user scope only (synced = the Claude app's login, project = per repo)."""
    try:
        data = json_body(run_ok([tool("claude"), "plugin", "list", "--json"], 120))
    except update.Skip:
        return {}
    installed = data.get("installed", []) if isinstance(data, dict) else data
    return {p["id"]: bool(p.get("enabled")) for p in installed
            if "@" in (p.get("id") or "") and p.get("scope", "user") == "user"}


def local_skill_lock() -> dict[str, dict]:
    lock = read_json(HOME / ".agents" / ".skill-lock.json") or {}
    return {n: {"source": s.get("source"), "sourceType": s.get("sourceType")}
            for n, s in (lock.get("skills") or {}).items()}


def local_skill_dirs() -> list[str]:
    return sorted(p.name for p in LOCAL_SKILLS.iterdir() if p.is_dir()) if LOCAL_SKILLS.is_dir() else []


def snapshot() -> dict:
    states = plugin_states()
    return {"version": 2, "exportedAt": datetime.now().isoformat(timespec="seconds"), "host": platform.node(),
            "marketplaces": local_marketplaces(),
            "plugins": sorted(p for p, on in states.items() if on),  # tylko aktywne
            "disabledPlugins": sorted(p for p, on in states.items() if not on),
            "skills": local_skill_lock(), "localSkills": local_skill_dirs()}


# MARK: - Różnica i plan

def same_tree(a: Path, b: Path) -> bool:
    def files(root: Path) -> dict[str, bytes]:
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
                if p.is_file() and not any(part in SKIP for part in p.relative_to(root).parts)}
    try:
        return files(a) == files(b)
    except OSError:
        return False


def diff(want: dict, have: dict) -> dict:
    """Co zmienić, żeby ten komputer miał ten sam aktywny zestaw co plik.

    Plik w wersji 2 (tylko aktywne pluginy) = lustro 1:1: nadmiarowe pluginy są wyłączane, skille usuwane.
    Starszy plik (wszystkie zainstalowane, bez stanu) tylko dokłada brakujące.
    """
    mirror = want.get("version", 1) >= 2
    active, disabled = set(have["plugins"]), set(have.get("disabledPlugins") or [])
    # @synced daje logowanie aplikacji Claude, @skills-dir (mody) przychodzą jako foldery w localSkills
    wanted = [p for p in want.get("plugins") or [] if p not in active
              and not p.endswith(("@synced", "@skills-dir"))]
    want_skills, want_local = want.get("skills") or {}, set(want.get("localSkills") or [])
    return {
        "marketplaces": {n: s for n, s in (want.get("marketplaces") or {}).items()
                         if n not in have["marketplaces"]},
        "plugins": [p for p in wanted if p not in disabled],
        "enable": [p for p in wanted if p in disabled] if mirror else [],  # stary plik: wyłączone też w liście
        "disable": sorted(active - set(want.get("plugins") or [])) if mirror else [],
        "skills": {n: s for n, s in want_skills.items() if n not in have["skills"]},
        "removeSkills": sorted(n for n in have["skills"] if n not in want_skills) if mirror else [],
        "localSkills": [n for n in want.get("localSkills") or [] if n not in have["localSkills"]
                        and not n.startswith(".")],
        # npx-owe skille z nadmiarowych sprząta `skills remove`
        "trashLocal": [n for n in have["localSkills"] if n not in want_local and not n.startswith(".")
                       and n not in have["skills"]] if mirror else [],
        # lokalne skille i mody (spoza npx) z inną zawartością niż kopia z Maca
        "replaceLocal": [n for n in have["localSkills"] if n in want_local and n not in want_skills
                         and (BUNDLE / n).is_dir() and not same_tree(BUNDLE / n, LOCAL_SKILLS / n)] if mirror else [],
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
    for verb in ("enable", "disable"):
        cmds += [["claude", "plugin", verb, pid, "--scope", "user"] for pid in missing.get(verb, [])
                 if SAFE_NAME.match(pid)]
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
    remove = update.safe(missing.get("removeSkills", []))
    if remove:
        cmds.append(["npx", "-y", "skills", "remove", "-g", "-y", *remove])
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
        shutil.copytree(LOCAL_SKILLS / n, BUNDLE / n, ignore=shutil.ignore_patterns(*SKIP))
    return len(names)


def copy_bundled(names: list[str]) -> list[str]:
    """Kopiuje z repo/skills te skille, których nadal brakuje w ~/.claude/skills."""
    copied = []
    for n in names:
        if SAFE_NAME.match(n) and (BUNDLE / n).is_dir() and not (LOCAL_SKILLS / n).exists():
            shutil.copytree(BUNDLE / n, LOCAL_SKILLS / n)
            copied.append(n)
    return copied


def to_trash(names: list[str]) -> list[str]:
    """Przenosi skille do ~/.claude/skills/.trash/<nazwa>-<czas> (Claude Code ich tam nie widzi; da się cofnąć)."""
    stamp, moved = f"{datetime.now():%Y%m%d-%H%M%S}", []
    for n in names:
        src = LOCAL_SKILLS / n
        if SAFE_NAME.match(n) and (src.exists() or src.is_symlink()):
            (LOCAL_SKILLS / ".trash").mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(LOCAL_SKILLS / ".trash" / f"{n}-{stamp}"))
            moved.append(n)
    return moved


# MARK: - Import (CLI i GUI)

@dataclass
class Plan:
    want: dict
    missing: dict
    cmds: list[list[str]]
    manual: list[str]
    to_copy: list[str]

    @property
    def empty(self) -> bool:
        return not (self.cmds or self.manual or self.to_copy or self.missing["trashLocal"]
                    or self.missing["replaceLocal"])

    @property
    def source(self) -> str:
        return f"{self.want.get('host') or '?'}, {self.want.get('exportedAt') or 'data nieznana'}"

    def sections(self) -> list[tuple[str, list[str]]]:
        m = self.missing
        return [(t, n) for t, n in (
            ("Dodaj marketplace", list(m["marketplaces"])), ("Zainstaluj plugin", m["plugins"]),
            ("Włącz plugin", m["enable"]), ("Wyłącz plugin", m["disable"]),
            ("Zainstaluj skill (npx)", list(m["skills"])), ("Usuń skill (npx)", m["removeSkills"]),
            ("Skopiuj skill / mod z Maca", self.to_copy),
            ("Podmień skill / mod na wersję z Maca", m["replaceLocal"]),
            ("Przenieś do .trash (nie ma na Macu)", m["trashLocal"])) if n]

    def describe(self) -> str:
        lines = []
        if self.want.get("version", 1) < 2:
            lines.append("!! Plik ze starego eksportu (bez stanu włączony/wyłączony) — tylko dokładam brakujące. "
                         "Dla zestawu 1:1 zrób eksport na Macu jeszcze raz.")
        lines.append(f"Zestaw z: {self.source}")
        for title, names in self.sections():
            lines.append(f"\n{title} ({len(names)}):")
            lines += [f"  {n}" for n in names]
        if self.manual:
            lines.append("\nRęcznie:")
            lines += [f"  - {x}" for x in self.manual]
        return "\n".join(lines)


def make_plan(file: Path) -> Plan:
    want = read_json(file)
    if not isinstance(want, dict):
        raise RuntimeError(f"nie mogę odczytać {file}")
    missing = diff(want, snapshot())
    cmds, manual = commands(missing)
    to_copy = [n for n in missing["localSkills"] if n not in missing["skills"] and (BUNDLE / n).is_dir()]
    return Plan(want, missing, cmds, manual, to_copy)


def apply(plan: Plan) -> bool:
    failed = 0
    for c in plan.cmds:
        exe = tool(c[0])  # claude.cmd / npx.cmd przez PATHEXT
        if not update.call([exe, *c[1:]]):
            failed += 1
    trashed = to_trash(plan.missing["trashLocal"] + plan.missing["replaceLocal"])
    if trashed:
        print(f"Przeniesiono do {LOCAL_SKILLS / '.trash'} ({len(trashed)}): {', '.join(trashed)}")
    # po npx: to, czego npx nie zainstalował, i podmieniane
    copied = copy_bundled(plan.missing["localSkills"] + plan.missing["replaceLocal"])
    if copied:
        print(f"Skopiowano z repo ({len(copied)}): {', '.join(copied)}")
    print(f"\nGotowe{f', błędy: {failed}' if failed else ''}. Zmiany działają w nowych sesjach Claude Code.")
    return not failed


def git_pull() -> bool:
    """Najnowszy eksport z Maca; best effort — bez sieci zostaje lokalny plik."""
    try:
        return update.call([tool("git"), "-C", str(ROOT), "pull", "--ff-only"])
    except update.Skip:
        return False


def export(file: Path) -> dict:
    snap = snapshot()
    file.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Zapisano {file}: {len(snap['marketplaces'])} marketplace'ów, {len(snap['plugins'])} aktywnych pluginów, "
          f"{len(snap['skills'])} skilli (npx skills), {len(snap['localSkills'])} lokalnych skilli")
    print(f"Skopiowano {bundle_skills()} folderów skilli do {BUNDLE}")
    return snap


def publish(file: Path) -> bool:
    """git commit + push eksportu (tylko claude-setup.json i skills/)."""
    git = tool("git")
    paths = [str(file.relative_to(ROOT)) if file.is_relative_to(ROOT) else str(file), "skills"]
    if not update.call([git, "-C", str(ROOT), "add", "-A", "--", *paths]):
        return False
    if update.run([git, "-C", str(ROOT), "diff", "--cached", "--quiet"]).returncode == 0:
        print("Bez zmian od ostatniego eksportu.")
        return True
    return (update.call([git, "-C", str(ROOT), "commit", "-m", f"claude setup {platform.node()}", "--", *paths])
            and update.call([git, "-C", str(ROOT), "push"]))


# MARK: - CLI

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    p = argparse.ArgumentParser(description="Przenosi pluginy Claude Code i skille między komputerami.")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="zapisz aktywny zestaw z tego komputera")
    e.add_argument("file", type=Path, nargs="?", default=SETUP_FILE)
    e.add_argument("--push", action="store_true", help="po eksporcie git commit + push")
    i = sub.add_parser("import", help="porównaj z plikiem i ustaw ten sam zestaw (1:1)")
    i.add_argument("file", type=Path, nargs="?", default=SETUP_FILE)
    i.add_argument("--apply", action="store_true", help="uruchom polecenia (domyślnie tylko podgląd)")
    args = p.parse_args(argv)

    if args.cmd == "export":
        export(args.file)
        return 0 if not args.push or publish(args.file) else 1
    try:
        plan = make_plan(args.file)
    except RuntimeError as err:
        p.error(str(err))
    if plan.empty:
        print("Ten komputer ma już ten sam zestaw.")
        return 0
    print(plan.describe())
    if not args.apply:
        print("\nPodgląd. Uruchom z --apply, żeby wykonać polecenia.")
        return 0
    return 0 if apply(plan) else 1


if __name__ == "__main__":
    sys.exit(main())
