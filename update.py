#!/usr/bin/env python3
"""UpdateWin: check and update npm, npx cache, pip, the Claude Code / Codex CLIs and their add-ons (see SPEC.md)."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()
# Variables that make a child Python load another interpreter's stdlib: the GUI runs from a uv venv,
# and C:\tools\python\python.exe inheriting them fails with "SRE module mismatch".
PY_LEAKS = ("__PYVENV_LAUNCHER__", "PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "VIRTUAL_ENV")
ENV = {**{k: v for k, v in os.environ.items() if k not in PY_LEAKS},
       "NO_COLOR": "1", "NO_UPDATE_NOTIFIER": "1", "PYTHONUTF8": "1",  # pip: UTF-8, not cp1250
       "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1"}
CREATIONFLAGS = 0  # gui.py sets CREATE_NO_WINDOW: no console window flashes under pythonw
# Names passed to commands: no leading "-" (option injection), no cmd.exe metacharacters (npm.cmd, claude.cmd).
SAFE_NAME = re.compile(r"^[@\w][\w@./-]*$")
REPO = re.compile(r"^[\w.-]+/[\w.-]+$")


@dataclass
class Item:
    name: str
    installed: str | None
    latest: str
    dirs: list[Path] = field(default_factory=list)  # npx: cache directories to remove


class Skip(Exception):
    """The tool or its data is not on this machine."""


# MARK: - Helpers

def tool(name: str) -> str:
    path = shutil.which(name)  # finds npm.cmd / claude.exe through PATHEXT on Windows
    if not path:
        raise Skip(f"brak {name} na PATH")
    return path


def run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=ENV, creationflags=CREATIONFLAGS)


def run_ok(args: list[str], timeout: int = 300) -> str:
    r = run(args, timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)}: exit {r.returncode} {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


def call(args: list[str]) -> bool:
    """Runs with live output (progress bars, prompts)."""
    print(f"$ {' '.join(args)}", flush=True)
    return subprocess.run(args, env=ENV).returncode == 0


def safe(names: list[str]) -> list[str]:
    return [n for n in names if SAFE_NAME.match(n)]


def json_body(text: str):
    """JSON from the first '{' / '[' (tools sometimes print warnings first)."""
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError("brak JSON w wyjściu")
    return json.loads(text[min(starts):])


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def vkey(v: str | None) -> tuple:
    # shortcut: numeric parts only (1.10.0 > 1.9.9); prerelease tags are ignored.
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


# MARK: - npm (global)

def parse_npm_outdated(stdout: str) -> list[Item]:
    if not stdout.strip():
        return []
    data = json_body(stdout)
    if isinstance(data.get("error"), dict):
        raise RuntimeError(data["error"].get("summary") or data["error"].get("code") or "npm error")
    items = []
    for name in sorted(data):
        d = data[name]
        d = d[0] if isinstance(d, list) and d else d  # same package in several locations
        if not isinstance(d, dict):
            continue
        latest = d.get("latest") or d.get("wanted")
        if latest and re.match(r"^\d+\.\d+", latest):  # linked / git entries have no registry version
            items.append(Item(name, d.get("current"), latest))
    return items


def check_npm() -> list[Item]:
    r = run([tool("npm"), "outdated", "-g", "--json"])
    if r.returncode not in (0, 1):  # exit 1 = "outdated packages exist"
        raise RuntimeError(f"npm outdated: exit {r.returncode} {r.stderr.strip()[-300:]}")
    # npm-installed claude / codex belong to the `cli` source; listing them twice would update them twice.
    return [i for i in parse_npm_outdated(r.stdout) if i.name not in CLIS.values()]


def update_npm(items: list[Item]) -> bool:
    names = safe([i.name for i in items])
    # Bare `npm install -g` would install the current directory.
    return not names or call([tool("npm"), "install", "-g", *[f"{n}@latest" for n in names]])


# MARK: - npx cache

def npx_root() -> Path:
    lines = run_ok([tool("npm"), "config", "get", "cache"], 60).strip().splitlines()
    return Path(lines[-1].strip()) / "_npx"


def scan_npx(root: Path) -> list[tuple[str, str | None, Path, bool]]:
    """(name, cached version, _npx/<hash> dir, pinned to an exact version) per cached package."""
    entries = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        manifest = read_json(d / "package.json") or {}
        requested = (manifest.get("_npx") or {}).get("packages") or []
        for name in sorted(manifest.get("dependencies") or {}):
            if not SAFE_NAME.match(name):
                continue
            version = (read_json(d / "node_modules" / name / "package.json") or {}).get("version")
            pinned = any(re.fullmatch(re.escape(name) + r"@\d+\.\d+\.\d+([-+][\w.+-]+)?", r) for r in requested)
            entries.append((name, version, d, pinned))
    return entries


def npm_view(npm: str, name: str) -> str | None:
    r = run([npm, "view", name, "version"], 60)
    if r.returncode != 0:
        if "E404" in r.stdout + r.stderr:
            return None
        raise RuntimeError(f"npm view {name}: {(r.stderr or r.stdout).strip()[-200:]}")
    lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    return lines[-1] if lines else None


def check_npx() -> list[Item]:
    root = npx_root()
    entries = [e for e in scan_npx(root) if not e[3]]  # pinned: deleting brings the same version back
    if not entries:
        raise Skip("pusty cache npx")
    oldest: dict[str, str | None] = {}
    dirs: dict[str, list[Path]] = {}
    for name, version, d, _ in entries:
        if name not in oldest or (version and vkey(version) < vkey(oldest[name])):
            oldest[name] = version
        dirs.setdefault(name, []).append(d)
    npm = tool("npm")

    def lookup(name):
        try:
            return npm_view(npm, name), None
        except Exception as e:  # noqa: BLE001 - collected and reported below
            return None, e

    with ThreadPoolExecutor(4) as ex:
        results = dict(zip(oldest, ex.map(lookup, oldest)))
    errors = [e for _, e in results.values() if e]
    if errors and len(errors) == len(results):
        raise RuntimeError(f"npm view: {errors[0]}")
    return [Item(n, oldest[n], latest, dirs[n]) for n, (latest, _) in results.items()
            if latest and latest != oldest[n]]


def update_npx(items: list[Item]) -> bool:
    root = npx_root().resolve()
    for d in sorted({d for i in items for d in i.dirs}):
        if d.resolve().parent != root:  # never delete outside <cache>/_npx
            print(f"pomijam {d}: poza {root}")
            continue
        print(f"usuwam {d}", flush=True)
        shutil.rmtree(d, ignore_errors=True)
    return True


# MARK: - pip

def parse_pip_outdated(stdout: str) -> list[Item]:
    if not stdout.strip():
        return []
    return [Item(e["name"], e.get("version"), e["latest_version"])
            for e in json_body(stdout) if e.get("latest_version")]


def check_pip(python: str) -> list[Item]:
    # --not-required: only what the user installed; dependencies follow them, so requirements stay consistent.
    return parse_pip_outdated(run_ok([python, "-m", "pip", "list", "--outdated", "--not-required", "--format=json"]))


def update_pip(python: str, items: list[Item]) -> bool:
    names = safe([i.name for i in items])
    return not names or call([python, "-m", "pip", "install", "--upgrade", *names])


# MARK: - Claude Code / Codex CLI

# Command -> npm package with its releases. `claude update` / `codex update` know their own install method
# (native installer, npm, ...), so one update command fits all of them.
CLIS = {"claude": "@anthropic-ai/claude-code", "codex": "@openai/codex"}


def cli_version(path: str) -> str | None:
    m = re.search(r"\d+\.\d+\.\d+", run_ok([path, "--version"], 60))
    return m.group(0) if m else None


def claude_channel() -> str:
    """npm dist-tag Claude Code updates from: `autoUpdatesChannel` in ~/.claude/settings.json."""
    channel = (read_json(HOME / ".claude" / "settings.json") or {}).get("autoUpdatesChannel")
    return channel if channel in ("latest", "stable") else "latest"


def check_cli() -> list[Item]:
    paths = {name: shutil.which(name) for name in CLIS}
    if not any(paths.values()):
        raise Skip("brak claude i codex na PATH")
    npm = tool("npm")
    items = []
    for name, path in paths.items():
        if not path:
            continue
        tag = claude_channel() if name == "claude" else "latest"
        installed, latest = cli_version(path), npm_view(npm, f"{CLIS[name]}@{tag}")
        if latest and vkey(installed) < vkey(latest):
            items.append(Item(name, installed, latest))
    return items


def update_cli(items: list[Item]) -> bool:
    return all([call([tool(i.name), "update"]) for i in items if i.name in CLIS])


# MARK: - Claude Code plugins

def claude_catalog_version(name: str, root: Path) -> str | None:
    """Like Claude Code: <source>/.claude-plugin/plugin.json, else the marketplace.json entry's version."""
    catalog = read_json(root / ".claude-plugin" / "marketplace.json") or {}
    entry = next((p for p in catalog.get("plugins") or [] if p.get("name") == name), None)
    if not entry:
        return None
    if isinstance(entry.get("source"), str):
        version = (read_json(root / entry["source"] / ".claude-plugin" / "plugin.json") or {}).get("version")
        if version:
            return version
    return entry.get("version")


def parse_claude_plugins(stdout: str, plugins_dir: Path) -> list[Item]:
    data = json_body(stdout)
    installed = data.get("installed", []) if isinstance(data, dict) else data
    known = read_json(plugins_dir / "known_marketplaces.json") or {}
    items, seen = [], set()
    for p in installed:
        pid = p.get("id") or ""
        if pid in seen or "@" not in pid:  # one row per plugin, whatever the scopes
            continue
        seen.add(pid)
        name, market = pid.rsplit("@", 1)
        location = (known.get(market) or {}).get("installLocation")  # none for @synced / @skills-dir
        latest = claude_catalog_version(name, Path(location)) if location else None
        if latest and latest != p.get("version"):
            items.append(Item(pid, p.get("version"), latest))
    return items


def check_claude(refresh: bool) -> list[Item]:
    claude = tool("claude")
    if refresh:  # best effort, like `brew update`: one offline marketplace must not fail the check
        run([claude, "plugin", "marketplace", "update"], 600)
    return parse_claude_plugins(run_ok([claude, "plugin", "list", "--json"]), HOME / ".claude" / "plugins")


def update_claude(items: list[Item]) -> bool:
    claude = tool("claude")
    return all([call([claude, "plugin", "update", n]) for n in safe([i.name for i in items])])


# MARK: - Codex plugins

def parse_codex_plugins(stdout: str, cache_dir: Path) -> list[Item]:
    data = json_body(stdout)
    items = []
    for p in data.get("installed", []) if isinstance(data, dict) else data:
        folder = cache_dir / p.get("marketplaceName", "") / p.get("name", "")
        latest = p.get("version")
        cached = [d.name for d in folder.iterdir() if d.is_dir() and not d.name.startswith(".")] if folder.is_dir() else []
        if latest and cached and latest not in cached:
            items.append(Item(p["pluginId"], max(cached, key=vkey), latest))
    return items


def check_codex(refresh: bool) -> list[Item]:
    codex = tool("codex")
    if refresh:
        run([codex, "plugin", "marketplace", "upgrade"], 600)
    return parse_codex_plugins(run_ok([codex, "plugin", "list", "--json"]), HOME / ".codex" / "plugins" / "cache")


def update_codex(items: list[Item]) -> bool:
    codex = tool("codex")
    return all([call([codex, "plugin", "add", n]) for n in safe([i.name for i in items])])


# MARK: - Agent skills (`npx skills`, ~/.agents)

SKILL_LOCK = HOME / ".agents" / ".skill-lock.json"


def github_tree(repo: str) -> dict[str, str]:
    """Folder path -> tree sha on the default branch."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "UpdateWin"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/git/trees/HEAD?recursive=1", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            tree = json.load(r).get("tree", [])
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}
        raise
    return {e["path"]: e["sha"] for e in tree if e.get("type") == "tree"}


def compare_skills(lock: dict, trees: dict[str, dict[str, str]]) -> list[Item]:
    items = []
    for name, s in sorted((lock.get("skills") or {}).items()):
        folder = os.path.dirname(s.get("skillPath", ""))
        upstream = trees.get(s.get("source"), {}).get(folder)
        # Missing upstream = skill moved or removed; `skills update` cannot fix it.
        if upstream and upstream != s.get("skillFolderHash"):
            items.append(Item(name, (s.get("skillFolderHash") or "")[:7], upstream[:7]))
    return items


def check_skills() -> list[Item]:
    lock = read_json(SKILL_LOCK)
    if not lock or not lock.get("skills"):
        raise Skip("brak skilli npx na tym komputerze (~/.agents/.skill-lock.json)")
    repos = sorted({s["source"] for s in lock["skills"].values()
                    if s.get("sourceType") == "github" and REPO.match(s.get("source", ""))})
    trees, errors = {}, []
    for repo in repos:  # shortcut: sequential, ~10 repos; parallelize if it grows
        try:
            trees[repo] = github_tree(repo)
        except Exception as e:  # noqa: BLE001 - reported below
            errors.append(f"{repo}: {e}")
    if repos and not trees:
        raise RuntimeError("GitHub niedostępny: " + "; ".join(errors[:3]) + " (limit? ustaw GITHUB_TOKEN)")
    if errors:  # partial: still report what could be checked, but say what was not
        print(f"[uwaga: nie sprawdzono {len(errors)} repo, np. {errors[0]}; limit? ustaw GITHUB_TOKEN]", end=" ")
    return compare_skills(lock, trees)


def update_skills(items: list[Item]) -> bool:
    # shortcut: `skills update` refreshes every skill in the lock, not only the outdated ones.
    return call([tool("npx"), "-y", "skills", "update"])


# MARK: - Main

def sources(args) -> dict:
    return {
        "npm": (check_npm, update_npm),
        "npx": (check_npx, update_npx),
        "pip": (lambda: check_pip(args.python), lambda items: update_pip(args.python, items)),
        "cli": (check_cli, update_cli),
        "claude": (lambda: check_claude(not args.no_refresh), update_claude),
        "codex": (lambda: check_codex(not args.no_refresh), update_codex),
        "skills": (check_skills, update_skills),
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # "→" on a cp1252 console / redirected output
    all_sources = ["npm", "npx", "pip", "cli", "claude", "codex", "skills"]
    parser = argparse.ArgumentParser(description="Sprawdza i aktualizuje npm, npx, pip, Claude Code i Codex oraz ich dodatki.")
    parser.add_argument("--update", action="store_true", help="zaktualizuj nieaktualne")
    parser.add_argument("-y", "--yes", action="store_true", help="bez pytania o potwierdzenie")
    parser.add_argument("--only", default=",".join(all_sources), help="źródła po przecinku: " + ",".join(all_sources))
    parser.add_argument("--no-refresh", action="store_true", help="nie odświeżaj marketplace'ów claude/codex")
    parser.add_argument("--python", default=sys.executable, help="Python dla pip (domyślnie bieżący)")
    args = parser.parse_args(argv)

    selected = [s.strip() for s in args.only.split(",") if s.strip()]
    unknown = set(selected) - set(all_sources)
    if unknown:
        parser.error(f"nieznane źródła: {', '.join(sorted(unknown))}")

    table = sources(args)
    found: dict[str, list[Item]] = {}
    failed = False
    for name in selected:
        print(f"== {name}", end=" ", flush=True)
        try:
            items = table[name][0]()
        except Skip as e:
            print(f"— pominięte ({e})")
            continue
        except Exception as e:  # noqa: BLE001 - one source must not stop the others
            print(f"— BŁĄD: {e}")
            failed = True
            continue
        print(f"({len(items)} do aktualizacji)")
        for i in items:
            print(f"   {i.name:45} {i.installed or '—':>14} → {i.latest}")
        if items:
            found[name] = items

    if not args.update or not found:
        if not found and not failed:
            print("Wszystko aktualne.")
        return 1 if failed else 0
    total = sum(len(v) for v in found.values())
    if not args.yes and input(f"Zaktualizować {total} pozycji? [t/N] ").strip().lower() not in ("t", "tak", "y", "yes"):
        return 1 if failed else 0
    for name, items in found.items():
        print(f"\n== aktualizacja: {name}")
        if not table[name][1](items):
            print(f"!! {name}: aktualizacja zakończona błędem")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
