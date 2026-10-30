#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["PyQt6>=6.5"]
# ///
"""UpdateWin GUI: the update.py sources in a PyQt6 window with checkboxes and a live log.

Run: `uv run gui.py` (installs PyQt6 on the fly) or `pip install PyQt6` and `python gui.py`.
"""
from __future__ import annotations

import contextlib
import io
import os
import platform
import shutil
import subprocess
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette, QTextCursor
from PyQt6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                             QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

import claude_sync
import update

MAC = sys.platform == "darwin"  # Mac = źródło zestawu Claude Code, Windows = cel (claude_sync.py)
TITLES = {"npm": "Globalne pakiety npm", "npx": "Cache npx", "pip": "Pakiety pip", "cli": "Claude Code i Codex",
          "claude": "Pluginy Claude Code", "codex": "Pluginy Codex", "skills": "Skille agentów"}
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
ITEM, STATE = Qt.ItemDataRole.UserRole, Qt.ItemDataRole.UserRole + 1
LOG_FILE = (Path(os.environ.get("LOCALAPPDATA", update.HOME)) / "UpdateWin" / "update.log" if os.name == "nt"
            else update.HOME / ".updatewin.log")

THEMES = {
    "light": dict(bg="#f5f5f3", surface="#ffffff", border="#e3e3df", text="#1d1d1b", muted="#74746f",
                  accent="#3d5afe", accent_hover="#2f4ae0", sel="#eef0ff", ok="#2b8a3e", warn="#d9480f",
                  error="#c92a2a"),
    "dark": dict(bg="#151517", surface="#1d1d20", border="#2d2d32", text="#ececee", muted="#8d8d94",
                 accent="#6c80ff", accent_hover="#8394ff", sel="#262a40", ok="#69db7c", warn="#ffa94d",
                 error="#ff8787"),
}

QSS = """
QMainWindow, #root {{ background: {bg}; }}
QWidget {{ color: {text}; font-size: 13px; }}
QLabel {{ background: transparent; }}
#title {{ font-size: 22px; font-weight: 600; }}
#subtitle, #optionLabel, #sectionLabel {{ color: {muted}; }}
#sectionLabel {{ font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
QPushButton {{ background: {surface}; border: 1px solid {border}; border-radius: 8px; padding: 8px 18px; }}
QPushButton:hover {{ border-color: {muted}; }}
QPushButton:disabled {{ color: {muted}; }}
QPushButton#primary {{ background: {accent}; color: #ffffff; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: {accent_hover}; }}
QPushButton#primary:disabled {{ background: {border}; color: {muted}; }}
QLineEdit {{ background: {surface}; border: 1px solid {border}; border-radius: 7px; padding: 5px 9px; }}
QLineEdit:focus {{ border-color: {accent}; }}
QTreeWidget, QPlainTextEdit {{ background: {surface}; border: 1px solid {border}; border-radius: 10px; }}
QTreeWidget {{ padding: 4px; outline: 0; }}
QPlainTextEdit {{ padding: 8px; color: {muted}; }}
QTreeWidget::item {{ padding: 5px 2px; border: none; }}
QTreeWidget::item:selected, QTreeWidget::item:hover {{ background: {sel}; color: {text}; }}
QHeaderView::section {{ background: {surface}; color: {muted}; border: none; border-bottom: 1px solid {border};
                        padding: 6px 8px; font-size: 12px; }}
QProgressBar {{ background: transparent; border: none; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 1px; }}
QSplitter::handle {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {muted}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; height: 0; }}
"""


def system_python() -> str:
    """Python for pip: under `uv run` / a venv, sys.executable is that venv, not the user's Python."""
    if sys.prefix == sys.base_prefix:
        return sys.executable
    # uv puts its envs and its base interpreter first on PATH; skip all of them.
    base = Path(sys.base_prefix).resolve()
    path = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                           if p and not (Path(p).parent / "pyvenv.cfg").exists()
                           and not Path(p).resolve().is_relative_to(base))
    names = ("python", "python3") if os.name == "nt" else ("python3", "python")  # python3.exe = Store stub
    return next((found for n in names if (found := shutil.which(n, path=path))), sys.executable)


def stream_call(args: list[str]) -> bool:
    """update.call for the GUI: same command, output streamed line by line to sys.stdout (the log)."""
    print(f"$ {' '.join(args)}", flush=True)
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                          text=True, encoding="utf-8", errors="replace", env=update.ENV,
                          creationflags=update.CREATIONFLAGS) as p:
        for line in p.stdout:
            print(line, end="", flush=True)
    return p.returncode == 0


class LogStream(io.TextIOBase):
    def __init__(self, signal):
        self.signal = signal

    def write(self, s: str) -> int:
        if s:
            self.signal.emit(s)
        return len(s)


class Bus(QObject):
    """Signals from worker threads, delivered on the GUI thread."""
    log = pyqtSignal(str)
    checked = pyqtSignal(str, str, object)  # source, "ok" | "skip" | "error", items | message
    finished = pyqtSignal(object)  # sources to re-check after an update, or None
    planned = pyqtSignal(object)  # claude_sync.Plan | Exception


class Window(QMainWindow):
    def __init__(self, log_path: Path | None = None):
        super().__init__()
        self.setWindowTitle("UpdateWin")
        self.resize(1000, 700)
        self.setMinimumSize(760, 520)
        self.busy = False
        self.table: dict = {}
        self.frame = 0
        self.bus = Bus()
        self.bus.log.connect(self.append_log)
        self.bus.checked.connect(self.on_checked)
        self.bus.finished.connect(self.on_finished)
        self.bus.planned.connect(self.on_planned)
        self.mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.mono.setPointSizeF(self.font().pointSizeF())

        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 20, 24, 18)
        outer.setSpacing(14)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(QLabel("UpdateWin", objectName="title"))
        self.subtitle = QLabel("npm · npx · pip · Claude Code · Codex · skille", objectName="subtitle")
        titles.addWidget(self.subtitle)
        header.addLayout(titles)
        header.addStretch()
        self.sync_btn = QPushButton("Eksportuj na Windows" if MAC else "Synchronizuj z Maca", clicked=self.sync)
        self.sync_btn.setToolTip("Ten sam aktywny zestaw pluginów, skilli i modów Claude Code na Macu i Windows (1:1)")
        self.check_btn = QPushButton("Sprawdź ponownie", clicked=lambda: self.check())
        self.update_btn = QPushButton("Aktualizuj", objectName="primary", clicked=self.update_selected)
        for b in (self.sync_btn, self.check_btn, self.update_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            header.addWidget(b)
        outer.addLayout(header)

        options = QHBoxLayout()
        options.setSpacing(10)
        self.refresh = QCheckBox("Odśwież marketplace'y Claude / Codex", checked=True)
        self.python = QLineEdit(system_python())
        self.python.setFont(self.mono)
        options.addWidget(self.refresh)
        options.addSpacing(16)
        options.addWidget(QLabel("Python dla pip", objectName="optionLabel"))
        options.addWidget(self.python, 1)
        outer.addLayout(options)

        self.progress = QProgressBar(textVisible=False, maximum=1)
        self.progress.setFixedHeight(2)
        outer.addWidget(self.progress)

        self.tree = QTreeWidget(columnCount=4, uniformRowHeights=True, animated=True, indentation=20)
        self.tree.setHeaderLabels(["Nazwa", "Zainstalowana", "Najnowsza", "Status"])
        h = self.tree.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.headerItem().setTextAlignment(3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.tree.itemChanged.connect(self.refresh_count)
        self.rows: dict[str, QTreeWidgetItem] = {}
        bold = QFont(self.font())
        bold.setWeight(QFont.Weight.DemiBold)
        for name in update.sources(SimpleNamespace(python="", no_refresh=True)):
            row = QTreeWidgetItem(self.tree, [TITLES.get(name, name), "", "", ""])
            row.setFont(0, bold)
            row.setTextAlignment(3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if name == "skills":
                row.setToolTip(0, "`npx skills update` aktualizuje wszystkie skille naraz")
            self.rows[name] = row
            self.set_status(row, "idle", "")

        log_box = QWidget()
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(0, 6, 0, 0)
        log_layout.setSpacing(6)
        log_layout.addWidget(QLabel("DZIENNIK", objectName="sectionLabel"))
        self.log = QPlainTextEdit(readOnly=True, maximumBlockCount=5000)
        self.log.setFont(self.mono)
        log_layout.addWidget(self.log)

        split = QSplitter(Qt.Orientation.Vertical, childrenCollapsible=False)
        split.addWidget(self.tree)
        split.addWidget(log_box)
        split.setSizes([460, 170])
        outer.addWidget(split, 1)

        self.spinner = QTimer(self, interval=90, timeout=self.spin)
        QGuiApplication.styleHints().colorSchemeChanged.connect(self.apply_theme)
        self.apply_theme()
        self.refresh_count()
        self.open_log_file(log_path)

    # MARK: - Log

    def open_log_file(self, path: Path | None):
        self.log_file = None
        if path:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists() and path.stat().st_size > 1_000_000:
                    path.replace(path.with_name(path.name + ".1"))  # shortcut: one previous file kept
                self.log_file = open(path, "a", encoding="utf-8")
            except OSError as e:
                self.append_log(f"!! nie można zapisać dziennika do {path}: {e}\n")
        self.append_log(f"=== UpdateWin {datetime.now():%Y-%m-%d %H:%M:%S} · {platform.platform()} · "
                        f"Python {platform.python_version()}{f' · {path}' if self.log_file else ''}\n")

    def append_log(self, text: str):
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()
        if self.log_file:
            try:
                self.log_file.write(text)
                self.log_file.flush()
            except OSError:  # disk full / file gone: keep the on-screen log, an exception here would abort Qt
                self.log_file = None

    def report_exception(self, exc_type, exc, tb):
        """sys/threading excepthook: PyQt6 aborts on an exception in a slot, without a trace under pythonw."""
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        self.bus.log.emit(f"\n!! nieobsłużony wyjątek:\n{text}")
        if sys.stderr:
            sys.stderr.write(text)

    # MARK: - Look

    def apply_theme(self, *_):
        dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        self.colors = THEMES["dark" if dark else "light"]
        c = {k: QColor(v) for k, v in self.colors.items()}
        pal = QPalette()
        for role, color in ((QPalette.ColorRole.Window, c["bg"]), (QPalette.ColorRole.Base, c["surface"]),
                            (QPalette.ColorRole.AlternateBase, c["bg"]), (QPalette.ColorRole.Text, c["text"]),
                            (QPalette.ColorRole.WindowText, c["text"]), (QPalette.ColorRole.Button, c["surface"]),
                            (QPalette.ColorRole.ButtonText, c["text"]), (QPalette.ColorRole.Highlight, c["accent"]),
                            (QPalette.ColorRole.HighlightedText, QColor("#ffffff")),
                            (QPalette.ColorRole.PlaceholderText, c["muted"]), (QPalette.ColorRole.Mid, c["border"])):
            pal.setColor(role, color)
        QApplication.instance().setPalette(pal)
        self.setStyleSheet(QSS.format(**self.colors))
        for row in self.rows.values():
            self.set_status(row, *row.data(0, STATE))

    def set_status(self, row: QTreeWidgetItem, state: str, text: str):
        row.setData(0, STATE, (state, text))
        color = {"ok": "ok", "outdated": "warn", "error": "error"}.get(state, "muted")
        row.setText(3, text)
        row.setForeground(3, QColor(self.colors[color]) if hasattr(self, "colors") else QColor())

    def spin(self):
        self.frame = (self.frame + 1) % len(SPINNER)
        for row in self.rows.values():
            if row.data(0, STATE)[0] == "checking":
                row.setText(3, f"{SPINNER[self.frame]}  sprawdzanie")

    def set_busy(self, busy: bool):
        self.busy = busy
        self.progress.setMaximum(0 if busy else 1)  # 0 = indeterminate animation
        for w in (self.sync_btn, self.check_btn, self.refresh, self.python):
            w.setEnabled(not busy)
        self.spinner.start() if busy else self.spinner.stop()
        self.refresh_count()

    # MARK: - Selection

    def plan(self) -> dict[str, list[update.Item]]:
        """Checked items per source, in source order."""
        out = {}
        for name, row in self.rows.items():
            items = [row.child(i).data(0, ITEM) for i in range(row.childCount())
                     if row.child(i).checkState(0) == Qt.CheckState.Checked]
            if items:
                out[name] = items
        return out

    def refresh_count(self, *_):
        n = sum(len(v) for v in self.plan().values())
        self.update_btn.setText(f"Aktualizuj ({n})" if n else "Aktualizuj")
        self.update_btn.setEnabled(bool(n) and not self.busy)

    # MARK: - Check

    def check(self, names: list[str] | None = None):
        if self.busy:
            return
        names = names or list(self.rows)
        python = self.python.text().strip() or sys.executable
        self.table = update.sources(SimpleNamespace(python=python, no_refresh=not self.refresh.isChecked()))
        self.tree.blockSignals(True)
        for name in names:
            row = self.rows[name]
            row.takeChildren()
            row.setFlags(Qt.ItemFlag.ItemIsEnabled)
            row.setData(0, Qt.ItemDataRole.CheckStateRole, None)
            row.setToolTip(3, "")
            self.set_status(row, "checking", "")
        self.tree.blockSignals(False)
        self.spin()
        self.set_busy(True)
        self.append_log(f"\n— sprawdzanie: {', '.join(names)} ({datetime.now():%H:%M:%S})\n")
        threading.Thread(target=self._check_all, args=(names,), daemon=True).start()

    def _check_all(self, names: list[str]):
        with contextlib.redirect_stdout(LogStream(self.bus.log)), ThreadPoolExecutor(len(names)) as ex:
            list(ex.map(self._check_one, names))
        self.bus.finished.emit(None)

    def _check_one(self, name: str):
        try:
            self.bus.checked.emit(name, "ok", self.table[name][0]())
        except update.Skip as e:
            self.bus.checked.emit(name, "skip", str(e))
        except Exception as e:  # noqa: BLE001 - one source must not stop the others
            self.bus.checked.emit(name, "error", str(e))

    def on_checked(self, name: str, state: str, payload):
        row = self.rows[name]
        if state == "skip":
            self.set_status(row, "skip", f"pominięte: {payload}")
            row.setToolTip(3, payload)
            self.append_log(f"-- {name}: pominięte ({payload})\n")
            return
        if state == "error":
            self.set_status(row, "error", "błąd")
            row.setToolTip(3, payload)
            self.append_log(f"!! {name}: {payload}\n")
            return
        if not payload:
            self.set_status(row, "ok", "✓  aktualne")
            return
        self.tree.blockSignals(True)
        self.set_status(row, "outdated", f"{len(payload)} do aktualizacji")
        row.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
        for item in payload:
            child = QTreeWidgetItem(row, [item.name, item.installed or "—", item.latest, ""])
            child.setData(0, ITEM, item)
            child.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            child.setCheckState(0, Qt.CheckState.Checked)
            child.setForeground(1, QColor(self.colors["muted"]))
            for c in (1, 2):
                child.setFont(c, self.mono)
        row.setExpanded(True)
        self.tree.blockSignals(False)
        self.refresh_count()

    # MARK: - Update

    def update_selected(self):
        plan = self.plan()
        total = sum(len(v) for v in plan.values())
        if not plan or self.busy:
            return
        summary = "\n".join(f"• {TITLES.get(n, n)}: {len(v)}" for n, v in plan.items())
        if QMessageBox.question(self, "Aktualizacja", f"Zaktualizować {total} pozycji?\n\n{summary}") \
                != QMessageBox.StandardButton.Yes:
            return
        self.set_busy(True)
        threading.Thread(target=self._update_all, args=(plan,), daemon=True).start()

    def _update_all(self, plan: dict[str, list[update.Item]]):
        failed = []
        with contextlib.redirect_stdout(LogStream(self.bus.log)):
            for name, items in plan.items():
                print(f"\n== aktualizacja: {name}")
                try:
                    ok = self.table[name][1](items)
                except Exception as e:  # noqa: BLE001 - reported in the log, next source still runs
                    print(e)
                    ok = False
                if not ok:
                    print(f"!! {name}: aktualizacja zakończona błędem")
                    failed.append(name)
            print(f"\n— koniec aktualizacji{': błędy w ' + ', '.join(failed) if failed else ''}")
        self.bus.finished.emit(list(plan))

    # MARK: - Mac -> Windows (claude_sync)

    def sync(self):
        if self.busy:
            return
        if MAC and QMessageBox.question(
                self, "Eksport", "Zapisać aktywny zestaw (pluginy, skille, mody) do repo i wysłać go "
                                 "(git commit + push)?") != QMessageBox.StandardButton.Yes:
            return
        self.set_busy(True)
        self.append_log(f"\n— {'eksport zestawu' if MAC else 'synchronizacja z Maca'} ({datetime.now():%H:%M:%S})\n")
        threading.Thread(target=self._export if MAC else self._plan_sync, daemon=True).start()

    def _export(self):
        with contextlib.redirect_stdout(LogStream(self.bus.log)):
            try:
                claude_sync.export(claude_sync.SETUP_FILE)
                print("Wysłane — na Windows: „Synchronizuj z Maca”." if claude_sync.publish(claude_sync.SETUP_FILE)
                      else "!! git commit / push nie powiódł się")
            except Exception as e:  # noqa: BLE001 - reported in the log
                print(f"!! eksport: {e}")
        self.bus.finished.emit(None)

    def _plan_sync(self):
        with contextlib.redirect_stdout(LogStream(self.bus.log)):
            if not claude_sync.git_pull():
                print("!! git pull nie powiódł się — używam lokalnego claude-setup.json")
            try:
                plan = claude_sync.make_plan(claude_sync.SETUP_FILE)
            except Exception as e:  # noqa: BLE001 - shown by on_planned
                plan = e
        self.bus.planned.emit(plan)

    def on_planned(self, plan):
        self.set_busy(False)
        if isinstance(plan, Exception):
            self.append_log(f"!! synchronizacja: {plan}\n")
            QMessageBox.warning(self, "Synchronizacja z Maca", str(plan))
            return
        if plan.empty:
            self.append_log(f"Ten komputer ma już ten sam zestaw co Mac ({plan.source}).\n")
            QMessageBox.information(self, "Synchronizacja z Maca", "Ten komputer ma już ten sam zestaw co Mac.")
            return
        details = plan.describe()
        self.append_log(details + "\n")
        summary = "\n".join(f"• {title}: {len(names)}" for title, names in plan.sections())
        box = QMessageBox(QMessageBox.Icon.Question, "Synchronizacja z Maca",
                          f"Ustawić tu ten sam zestaw co na Macu?\n({plan.source})\n\n{summary}",
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setDetailedText(details)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        self.set_busy(True)
        threading.Thread(target=self._apply_sync, args=(plan,), daemon=True).start()

    def _apply_sync(self, plan):
        with contextlib.redirect_stdout(LogStream(self.bus.log)):
            try:
                claude_sync.apply(plan)
            except Exception as e:  # noqa: BLE001 - reported in the log
                print(f"!! synchronizacja: {e}")
        self.bus.finished.emit(["claude", "skills"])

    def on_finished(self, recheck):
        self.set_busy(False)
        if recheck:
            self.check(recheck)
            return
        outdated = sum(r.childCount() for r in self.rows.values())
        errors = sum(r.data(0, STATE)[0] == "error" for r in self.rows.values())
        parts = [f"Sprawdzono o {datetime.now():%H:%M}",
                 f"{outdated} do aktualizacji" if outdated else "wszystko aktualne"]
        if errors:
            parts.append(f"błędy: {errors}")
        self.subtitle.setText("  ·  ".join(parts))

    def closeEvent(self, event):
        # Quitting mid-install can leave npm/pip half-updated.
        if self.busy and QMessageBox.question(self, "UpdateWin", "Trwa operacja. Zamknąć mimo to?") \
                != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        event.accept()


def main() -> int:
    update.call = stream_call  # live output goes to the log, not the (absent) console
    update.CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("UpdateWin")
    w = Window(LOG_FILE)
    sys.excepthook = w.report_exception
    threading.excepthook = lambda a: w.report_exception(a.exc_type, a.exc_value, a.exc_traceback)
    w.show()
    QTimer.singleShot(0, w.check)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
