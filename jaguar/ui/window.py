from pathlib import Path
import sqlite3

from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QInputDialog, QLabel,
    QListWidget, QMainWindow, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from jaguar.db.projects import create_project, open_project


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JAGUAR Camera Trap Data Analyzer")
        self.resize(1200, 720)
        self.project = None
        self.project_path = None
        container = QWidget()
        layout = QHBoxLayout(container)
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(190)
        self.sidebar.addItems(["Dashboard", "Projects / Import", "Processing", "Images", "Cameras", "Detections", "Review Queue", "Export", "Settings"])
        layout.addWidget(self.sidebar)
        content = QVBoxLayout()
        self.project_label = QLabel("No project open")
        content.addWidget(self.project_label)
        actions = QHBoxLayout()
        for title, callback in [("Create project", self.create), ("Open project", self.open)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            actions.addWidget(button)
        content.addLayout(actions)
        self.pages = QStackedWidget()
        for title in [self.sidebar.item(i).text() for i in range(self.sidebar.count())]:
            page = QWidget()
            panel = QVBoxLayout(page)
            panel.addWidget(QLabel(title))
            message = "Project storage is available. Dashboard metrics will appear after approved processing stages are implemented." if title == "Dashboard" else "Gate 1 foundation. This workflow is scheduled for a later implementation gate."
            label = QLabel(message)
            label.setWordWrap(True)
            panel.addWidget(label)
            panel.addStretch()
            self.pages.addWidget(page)
        content.addWidget(self.pages)
        layout.addLayout(content, 1)
        self.setCentralWidget(container)
        self.sidebar.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.sidebar.setCurrentRow(0)
        self.statusBar().showMessage("Gate 1 | Local project storage")

    def load_project(self, path: Path):
        project = open_project(path)
        self.project = project
        self.project_path = path
        self.project_label.setText(f"Project: {project.name}")
        self.statusBar().showMessage("Project opened")

    def create(self):
        name, accepted = QInputDialog.getText(self, "Create project", "Project name")
        if not accepted:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Project location", "project.sqlite", "JAGUAR project (*.sqlite)")
        if path:
            try:
                create_project(Path(path), name)
                self.load_project(Path(path))
            except (OSError, ValueError, sqlite3.Error) as error:
                QMessageBox.warning(self, "Project could not be created", str(error))

    def open(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "JAGUAR project (*.sqlite)")
        if path:
            try:
                self.load_project(Path(path))
            except (OSError, ValueError, sqlite3.Error) as error:
                QMessageBox.warning(self, "Project could not be opened", str(error))
