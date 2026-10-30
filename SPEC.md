# UpdateWin — specyfikacja

> Skrypt Pythona dla Windows, który sprawdza i aktualizuje globalne pakiety npm, cache npx, pakiety pip
> oraz dodatki Claude Code i Codex (pluginy, skille). Port źródeł z UpdateHub (macOS).

| | |
|---|---|
| **Wersja** | 1.0 — 2026-10-29 |
| **Platforma** | Windows 10/11 (działa też na macOS/Linux) |
| **Wymagania** | Python 3.10+, tylko biblioteka standardowa (GUI: PyQt6 6.5+) |
| **Plik** | `update.py`, GUI `gui.py` (testy: `test_update.py`, `test_gui.py`) |

## 1. Użycie

```
python update.py                      # sprawdź wszystko, pokaż tabelę
python update.py --update             # sprawdź i zaktualizuj (pyta o potwierdzenie)
python update.py --update -y          # bez pytania
python update.py --only npm,skills    # wybrane źródła
python update.py --only cli           # tylko same Claude Code i Codex
python update.py --no-refresh         # bez odświeżania marketplace'ów (szybciej, offline)
python update.py --python C:\Python313\python.exe   # inny Python dla pip
```

### 1.1 GUI (PyQt6)

```
UpdateWin.bat                         # Windows: dwuklik (uv, inaczej py/python + pip install PyQt6)
UpdateWin.command                     # macOS: dwuklik w Finderze
uv run gui.py                         # uv sam doinstaluje PyQt6 (nagłówek PEP 723)
pip install PyQt6 && python gui.py    # bez uv
```

Okno sprawdza wszystkie źródła równolegle zaraz po starcie. Nieaktualne pozycje mają pola wyboru (domyślnie zaznaczone), „Aktualizuj (N)” aktualizuje zaznaczone po potwierdzeniu. Wyjście poleceń trafia na żywo do „Dziennika” (stdin zamknięty — bez promptów). Po aktualizacji te źródła są sprawdzane ponownie. „Python dla pip” domyślnie wskazuje Pythona z PATH z pominięciem venv-ów (także środowiska `uv run`). Motyw jasny/ciemny według systemu. Zamknięcie okna w trakcie operacji wymaga potwierdzenia.

Dziennik jest też dopisywany do pliku `%LOCALAPPDATA%\UpdateWin\update.log` (macOS/Linux: `~/.updatewin.log`), z nagłówkiem sesji (system, wersja Pythona); powyżej 1 MB poprzedni plik przechodzi do `update.log.1`. Nieobsłużone wyjątki (wątek GUI i wątki robocze) trafiają z pełnym tracebackiem do dziennika. Procesy potomne nie dziedziczą `__PYVENV_LAUNCHER__`, `PYTHONHOME`, `PYTHONPATH`, `PYTHONEXECUTABLE` ani `VIRTUAL_ENV` (GUI działa w venv uv; bez tego Python dla pip ładował cudzą bibliotekę standardową — „SRE module mismatch”). Polecenia uruchamiane z GUI nie otwierają okien konsoli (`CREATE_NO_WINDOW`), a `PYTHONUTF8=1` wymusza wyjście pip w UTF-8.

Kod wyjścia: `0` — wszystko się udało, `1` — któreś źródło lub aktualizacja zakończyły się błędem.

## 2. Źródła

| ID | Co | Sprawdzanie | Aktualizacja |
|---|---|---|---|
| `npm` | globalne pakiety npm | `npm outdated -g --json` (exit 1 = są nieaktualne) | `npm install -g <pkg>@latest …` |
| `npx` | cache npx | `<npm config get cache>\_npx\<hash>\package.json` + `node_modules\<pkg>\package.json`; najnowsza z `npm view <pkg> version` (4 równolegle) | usunięcie katalogów `_npx\<hash>` z tym pakietem (następne `npx` pobierze najnowszą) |
| `pip` | pakiety Pythona | `<python> -m pip list --outdated --not-required --format=json` | `<python> -m pip install --upgrade <pkg> …` |
| `cli` | same CLI `claude` i `codex` | `<cli> --version` vs `npm view <pakiet>@<kanał> version` (`@anthropic-ai/claude-code`, `@openai/codex`) | `claude update`, `codex update` |
| `claude` | pluginy Claude Code | `claude plugin marketplace update` → `claude plugin list --json`; najnowsza z katalogu marketplace'u | `claude plugin update <plugin@marketplace>` |
| `codex` | pluginy Codex | `codex plugin marketplace upgrade` → `codex plugin list --json` | `codex plugin add <plugin@marketplace>` |
| `skills` | skille agentów (`~/.agents`, wspólne dla Claude Code i Codex) | `~/.agents/.skill-lock.json` vs sha folderu na GitHubie | `npx -y skills update` |

### 2.1 Szczegóły
- **npm**: wpisy bez wersji w rejestrze (`linked`, `git`) pomijane. Wpis, którego wartość jest listą (pakiet w kilku miejscach), bierze pierwszy element.
- **npx**: pakiety zamówione z dokładną wersją (`npx pkg@1.2.3`, np. przypięte serwery MCP) są pomijane — usunięcie przywróci tę samą wersję. Jeśli pakiet jest w kilku katalogach, „zainstalowana” = najstarsza wersja.
- **pip**: `--not-required` — tylko pakiety, od których nic nie zależy (to, co użytkownik zainstalował sam); zależności aktualizują się razem z nimi, więc nie rozjeżdżają się wymagania. Domyślny Python = ten, który uruchamia skrypt (`sys.executable`). Gdy katalog Pythona nie jest zapisywalny, pip sam przechodzi na instalację `--user`.
- **cli**: kanał Claude Code = `autoUpdatesChannel` z `~/.claude/settings.json` (`latest` / `stable`, domyślnie `latest`); Codex zawsze `latest`. Brak jednego z CLI na PATH = pomijany; brak obu = źródło pominięte. `claude update` / `codex update` same wybierają metodę instalacji (instalator natywny, npm), więc źródło `npm` nie pokazuje już `@anthropic-ai/claude-code` ani `@openai/codex` (bez podwójnej aktualizacji). Działa też przy `autoUpdates: false`. Na Windows aktualizacja CLI, które akurat działa (np. otwarta sesja Codex z npm), może się nie udać — zamknij je i spróbuj ponownie.
- **claude**: katalogi w `~/.claude/plugins/known_marketplaces.json` (`installLocation`). Najnowsza wersja: `<source>/.claude-plugin/plugin.json`, inaczej `version` wpisu w `.claude-plugin/marketplace.json`. Pluginy bez lokalnego katalogu (`@synced`, `@skills-dir`, źródła zdalne) pomijane. Ten sam plugin w kilku zakresach = jeden wiersz. Zmiana działa w nowych sesjach.
- **codex**: `version` z `plugin list` = wersja w marketplace; zainstalowana = folder wersji w `~/.codex/plugins/cache/<marketplace>/<plugin>/`. Brak folderu z tą wersją = nieaktualny. Bez folderu w ogóle — pomijany.
- **skills**: `skillFolderHash` w lock = sha drzewa folderu skilla w git. Najnowszy = sha tego folderu w `GET api.github.com/repos/<repo>/git/trees/HEAD?recursive=1` (jedno zapytanie na repo). Skille usunięte z repo pomijane. Wersje pokazywane jako 7 znaków sha. `GITHUB_TOKEN` (opcjonalny) podnosi limit z 60 do 5000 zapytań/h. Gdy część repo nie odpowie (np. limit), wynik dla reszty jest pokazany z ostrzeżeniem; gdy żadne — błąd źródła. `skills update` aktualizuje wszystkie skille z lock naraz.
- Odświeżanie marketplace'ów (claude/codex) jest „best effort”: błąd jednego marketplace'u nie przerywa sprawdzania.

## 2.2 Przenoszenie zestawu między komputerami (`claude_sync.py`)

Logowanie synchronizuje tylko pluginy i skille aplikacji Claude. Pluginy Claude Code CLI (`~/.claude/plugins`), skille `npx skills` (`~/.agents`) oraz lokalne skille i mody (`~/.claude/skills/*`, mody = `<nazwa>@skills-dir`) każdy komputer ma osobno. Cel: Windows ma ten sam **aktywny** zestaw co Mac (1:1).

**GUI**: na Macu przycisk „Eksportuj na Windows” (eksport + `git commit` + `git push` plików `claude-setup.json` i `skills/`), na Windows „Synchronizuj z Maca” (`git pull --ff-only` → podgląd zmian → po potwierdzeniu wykonanie → ponowne sprawdzenie `claude` i `skills`).

```
python3 claude_export_mac.py --push                        # Mac (bez zależności); albo claude_sync.py export --push
python claude_sync.py import                               # Windows: podgląd zmian
python claude_sync.py import --apply                       # Windows: wykonaj
```

- Eksport (`version: 2`): tylko pluginy **włączone** w zakresie `user` (`disabledPlugins` — informacyjnie), marketplace'y, lock `npx skills`, nazwy folderów `~/.claude/skills/*` i ich kopie w `skills/` (linki rozwiązane, bez `.git` / `.DS_Store`), `exportedAt`, `host`.
- Import po kolei: `claude plugin marketplace add <repo|url>` → `claude plugin install` (brakujące) → `claude plugin enable` (zainstalowane, ale wyłączone) → `claude plugin disable` (aktywne tu, nieaktywne na Macu) → `npx -y skills add <repo> -g -y --skill <name> …` → `npx -y skills remove -g -y <name> …` (nie ma ich na Macu) → foldery: lokalne skille / mody, których nie ma na Macu, i te z inną zawartością idą do `~/.claude/skills/.trash/<nazwa>-<czas>` (Claude Code ich tam nie widzi; do odzyskania), brakujące i podmieniane kopiowane z `skills/`.
- Pluginy nadmiarowe są wyłączane, nie odinstalowywane. Pomijane po obu stronach: `@synced` (logowanie aplikacji Claude), zakres `project`.
- Plik ze starego eksportu (bez `version`) — tylko dokładanie brakujących, z ostrzeżeniem w podglądzie.
- Tylko wypisywane (ręcznie): marketplace'y z lokalnego folderu, skille z lock spoza GitHuba.
- Walidacja jak w `update.py` (`SAFE_NAME`, `REPO`, adresy git tylko `https://`).

## 3. Zachowanie
1. Brak narzędzia (np. `codex` nie na PATH, brak lock skilli) → źródło oznaczone „pominięte”, bez błędu.
2. Błąd sprawdzania jednego źródła nie zatrzymuje pozostałych; jest pokazany w podsumowaniu.
3. Aktualizacje uruchamiane po kolei, wyjście narzędzi na żywo w konsoli (bez przechwytywania), żeby było widać postęp i prompty.
4. Po aktualizacji ponowne sprawdzenie nie jest wykonywane (uruchom skrypt jeszcze raz).

## 4. Bezpieczeństwo
- Nazwy pakietów/pluginów przekazywane do poleceń są walidowane (`^[@\w./-]+$`, bez wiodącego `-`) — chroni przed wstrzyknięciem opcji i znaków specjalnych `cmd.exe` przy uruchamianiu `npm.cmd`/`claude.cmd`.
- Polecenia uruchamiane listą argumentów, nigdy przez `shell=True`.
- Sieć: tylko `api.github.com` (skille), rejestry, z których korzystają same narzędzia, i `git pull` / `git push` repo UpdateWin (synchronizacja).
- Synchronizacja niczego nie kasuje na stałe: pluginy są wyłączane, foldery skilli przenoszone do `.trash`; jedynie `skills remove` usuwa skille npx (do ponownej instalacji z GitHuba).
- Usuwanie (npx) tylko katalogów wewnątrz `<cache>\_npx`.

## 5. Poza zakresem
- Rozszerzenia aplikacji Claude Desktop i dodatki ChatGPT (aktualizowane przez same aplikacje / po stronie serwera).
- pipx, uv, conda, winget, Chocolatey, Scoop.
- Harmonogram (można użyć Harmonogramu zadań Windows: `python update.py --update -y`).
