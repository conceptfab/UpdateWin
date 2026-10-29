#!/bin/sh
# UpdateWin - dwuklik w Finderze uruchamia gui.py.
# Przez uv, jesli jest (sam pobierze PyQt6); inaczej python3 z doinstalowaniem PyQt6.
# Dziennik: ~/.updatewin.log
cd "$(dirname "$0")" || exit 1

if command -v uv >/dev/null 2>&1; then
    exec uv run --quiet gui.py
fi

python3 -c "import PyQt6" 2>/dev/null || python3 -m pip install --user PyQt6 || exit 1
exec python3 gui.py
