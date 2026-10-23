import os
import tempfile
import unittest
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from jaguar.db.projects import create_project
from jaguar.ui.window import MainWindow
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage, QColor


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_window_launch_and_project_open(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "project.sqlite"
            project = create_project(path, "Survey")
            window = MainWindow()
            window.resize(1366, 768)
            window.show()
            self.app.processEvents()
            window.load_project(path)
            self.assertEqual(window.project, project)
            self.assertEqual(window.sidebar.count(), 9)
            self.assertEqual(window.project_label.text(), "Project: Survey")
            window.close()

    def test_import_worker_runs_while_ui_events_continue(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "images"
            root.mkdir()
            image = QImage(16, 12, QImage.Format.Format_RGB32)
            image.fill(QColor("red"))
            self.assertTrue(image.save(str(root / "a.png"), "PNG"))
            path = Path(folder) / "project.sqlite"
            create_project(path, "Import survey")
            window = MainWindow()
            window.load_project(path)
            panel = window.import_panel
            panel.roots.addItem(str(root))
            ticks = []
            timer = QTimer()
            timer.setInterval(10)
            timer.timeout.connect(lambda: ticks.append(True))
            timer.start()
            panel.begin()
            try:
                deadline = time.monotonic() + 20
                while panel.active() and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
                self.assertFalse(panel.active())
                self.assertIn("Imported paths: 1", panel.report.text())
                self.assertGreater(len(ticks), 2)
            finally:
                timer.stop()
                if panel.active():
                    panel.stop.set()
                    panel.process.join(5)
                    if panel.process.is_alive():
                        panel.process.terminate()
                        panel.process.join(5)
                    panel.poll()
                window.close()
