import contextlib
import io
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import update

try:
    import gui
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
except ImportError:  # PyQt6 is optional; the CLI tests still run
    gui = None


@unittest.skipUnless(gui, "PyQt6 not installed")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_stream_call_logs_output_and_returns_status(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertTrue(gui.stream_call([sys.executable, "-c", "print('hej')"]))
            self.assertFalse(gui.stream_call([sys.executable, "-c", "import sys; sys.exit(3)"]))
        self.assertIn("hej\n", out.getvalue())

    def test_checked_rows_build_the_update_plan(self):
        w = gui.Window()
        a, b = update.Item("a", "1.0", "2.0"), update.Item("b", None, "3.0")
        w.on_checked("npm", "ok", [a, b])
        w.on_checked("pip", "skip", "brak pip")
        w.on_checked("claude", "error", "boom")
        w.on_checked("codex", "ok", [])
        self.assertEqual(w.plan(), {"npm": [a, b]})
        self.assertEqual(w.update_btn.text(), "Aktualizuj (2)")
        w.rows["npm"].child(1).setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(w.plan(), {"npm": [a]})
        self.assertEqual(w.rows["npm"].checkState(0), Qt.CheckState.PartiallyChecked)
        self.assertEqual(w.rows["claude"].text(3), "błąd")
        w.close()
        self.app.processEvents()

    def test_update_streams_to_log_and_asks_for_recheck(self):
        w = gui.Window()
        logged, finished = [], []
        w.bus.log.connect(logged.append)
        w.bus.finished.connect(finished.append)
        w.table = {"npm": (None, lambda items: gui.stream_call([sys.executable, "-c", "print('zrobione')"])),
                   "pip": (None, lambda items: 1 / 0)}
        w._update_all({"npm": [update.Item("a", "1", "2")], "pip": [update.Item("b", "1", "2")]})
        self.app.processEvents()
        text = "".join(logged)
        self.assertIn("zrobione", text)
        self.assertIn("!! pip: aktualizacja zakończona błędem", text)
        self.assertNotIn("!! npm", text)
        self.assertEqual(finished, [["npm", "pip"]])


if __name__ == "__main__":
    unittest.main()
