import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from jaguar.db.projects import create_project
from jaguar.ui.window import MainWindow


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
