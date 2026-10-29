@echo off
rem ============================================================
rem  UpdateWin - okno do sprawdzania i aktualizacji
rem  npm, npx, pip, pluginow Claude Code / Codex i skilli.
rem
rem  Dwuklik uruchamia gui.py:
rem    1. przez uv, jesli jest zainstalowany (sam pobierze PyQt6),
rem    2. inaczej przez Pythona (py / python), doinstalowujac PyQt6.
rem  Dziennik: %LOCALAPPDATA%\UpdateWin\update.log
rem ============================================================
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if not errorlevel 1 (
    uv run --quiet gui.py
    goto done
)

set "PY=python"
where py >nul 2>nul && set "PY=py -3"

%PY% -c "import PyQt6" >nul 2>nul
if errorlevel 1 (
    echo Instaluje PyQt6...
    %PY% -m pip install --user PyQt6
    if errorlevel 1 goto failed
)
%PY% gui.py

:done
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo UpdateWin zakonczyl sie bledem - szczegoly powyzej
echo oraz w %LOCALAPPDATA%\UpdateWin\update.log
pause
exit /b 1
