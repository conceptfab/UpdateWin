# UpdateWin — poprawki z 2026-10-29

## 1. Błąd `pip` — „SRE module mismatch” (POPRAWKA)

**Przyczyna:** `UpdateWin.bat` uruchamia GUI przez `uv run`, czyli z venv. Launcher venv na Windows ustawia `__PYVENV_LAUNCHER__` (oraz `VIRTUAL_ENV`), a `C:\tools\python\python.exe` dziedziczy te zmienne. Przez to ładuje bibliotekę standardową z Pythona uv (`...\uv\python\cpython-3.14...\Lib`), która nie pasuje do jego własnego modułu `_sre` → `AssertionError: SRE module mismatch`.

**Plik:** `update.py`
**Miejsce:** stała `ENV` (góra pliku, sekcja importów)

Zamień:

```python
ENV = {**os.environ, "NO_COLOR": "1", "NO_UPDATE_NOTIFIER": "1", "PYTHONUTF8": "1",  # pip: UTF-8, not cp1250
       "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1"}
```

na:

```python
# Variables that make a child Python load another interpreter's stdlib: the GUI runs from a uv venv,
# and C:\tools\python\python.exe inheriting them fails with "SRE module mismatch".
PY_LEAKS = ("__PYVENV_LAUNCHER__", "PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "VIRTUAL_ENV")
ENV = {**{k: v for k, v in os.environ.items() if k not in PY_LEAKS},
       "NO_COLOR": "1", "NO_UPDATE_NOTIFIER": "1", "PYTHONUTF8": "1",  # pip: UTF-8, not cp1250
       "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1"}
```

**Plik:** `test_update.py`
**Miejsce:** klasa `UpdateTests`, nowy test na końcu

```python
    def test_env_drops_venv_variables_for_child_python(self):
        for name in update.PY_LEAKS:
            self.assertNotIn(name, update.ENV)
```

---

## 2. Tylko 2 pluginy Claude Code na Windows — nowy skrypt `claude_sync.py` (NOWY PLIK)

**Przyczyna:** logowanie synchronizuje tylko pluginy i skille **aplikacji Claude** (z konta lub organizacji). Pluginy **Claude Code CLI** (`~/.claude/plugins`) i skille `npx skills` (`~/.agents`) każdy komputer ma osobno. „Skille agentów: pominięte” oznacza, że na Windows nie ma jeszcze `~/.agents/.skill-lock.json`.

**Plik:** `claude_sync.py` (nowy, obok `update.py`, sama biblioteka standardowa)

Użycie:

```bash
# 1. Mac: zapisz zestaw
python3 claude_sync.py export claude-setup.json

# 2. przenieś claude-setup.json na Windows (np. do F:\___APPS\UpdateWin)

# 3. Windows: podgląd, czego brakuje
python claude_sync.py import claude-setup.json

# 4. Windows: doinstaluj
python claude_sync.py import claude-setup.json --apply
```

Co robi `import --apply` (w tej kolejności):

| Co | Polecenie |
|---|---|
| brakujące marketplace'y (GitHub / git) | `claude plugin marketplace add <owner/repo or url>` |
| brakujące pluginy | `claude plugin install <plugin@marketplace>` |
| brakujące skille `npx skills` | `npx -y skills add <repo> -g -y --skill <name> …` (jedno polecenie na repo) |

Tylko wypisuje (do zrobienia ręcznie):

- marketplace'y z lokalnego folderu na Macu,
- pluginy `@synced` / `@skills-dir`,
- lokalne skille z `~/.claude/skills/*`. Te najlepiej trzymać w repo git i podpiąć przez junction, jak w poprzedniej odpowiedzi.

Nazwy i repo są walidowane tak samo jak w `update.py` (`SAFE_NAME`, `REPO`, adresy tylko `https://`). Polecenia idą listą argumentów, bez `shell=True`.
