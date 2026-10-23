from pathlib import Path
import sqlite3

from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QInputDialog, QLabel,
    QListWidget, QMainWindow, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget, QTabWidget)

from jaguar.db.projects import create_project, open_project
from jaguar.ui.import_panel import ImportPanel
from jaguar.ui.exif_panel import ExifPanel
from jaguar.ui.dashboard import Dashboard
from jaguar.ui.theme import STYLE
from jaguar.ui.stage_panel import StagePanel
from jaguar.ui.review_panel import ReviewPanel


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JAGUAR Camera Trap Data Analyzer")
        self.resize(1200, 720)
        self.setStyleSheet(STYLE)
        self.project = None
        self.project_path = None
        self.import_panel = ImportPanel()
        self.exif_panel = ExifPanel()
        self.ocr_panel = StagePanel("ocr")
        self.detection_panel = StagePanel("detect")
        self.reconcile_panel = StagePanel("reconcile")
        self.review_panel = ReviewPanel()
        self.dashboard = Dashboard()
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
            if title == "Review Queue":
                self.pages.addWidget(self.review_panel)
                continue
            if title == "Dashboard":
                self.pages.addWidget(self.dashboard)
                continue
            if title == "Processing":
                processing = QTabWidget()
                processing.addTab(self.exif_panel, "EXIF")
                processing.addTab(self.ocr_panel, "Overlay OCR")
                processing.addTab(self.detection_panel, "Detection")
                processing.addTab(self.reconcile_panel, "Reconcile")
                self.pages.addWidget(processing)
                continue
            if title == "Projects / Import":
                self.pages.addWidget(self.import_panel)
                continue
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
        self.sidebar.currentRowChanged.connect(lambda index: self.dashboard.refresh() if index == 0 else None)
        self.sidebar.setCurrentRow(0)
        self.statusBar().showMessage("Gate 3 | EXIF metadata extraction")

    def load_project(self, path: Path):
        if self.processing_active():
            raise ValueError("The current import must pause before another project opens.")
        project = open_project(path)
        self.project = project
        self.project_path = path
        self.import_panel.set_project(path)
        self.exif_panel.set_project(path)
        self.ocr_panel.set_project(path)
        self.detection_panel.set_project(path)
        self.reconcile_panel.set_project(path)
        self.review_panel.set_project(path)
        self.dashboard.database = path
        self.dashboard.refresh()
        self.project_label.setText(f"Project: {project.name}")
        self.statusBar().showMessage("Project opened")

    def create(self):
        if self.processing_active():
            QMessageBox.information(self, "Import active", "The current import must pause before another project opens.")
            return
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
        if self.processing_active():
            QMessageBox.information(self, "Import active", "The current import must pause before another project opens.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "JAGUAR project (*.sqlite)")
        if path:
            try:
                self.load_project(Path(path))
            except (OSError, ValueError, sqlite3.Error) as error:
                QMessageBox.warning(self, "Project could not be opened", str(error))

    def closeEvent(self, event):
        if self.processing_active():
            self.import_panel.request_pause()
            self.exif_panel.request_pause()
            self.ocr_panel.request_pause()
            self.detection_panel.request_pause()
            self.reconcile_panel.request_pause()
            self.statusBar().showMessage("Import is pausing. The window can close after the checkpoint completes.")
            event.ignore()
        else:
            event.accept()

    def processing_active(self):
        return any(panel.active() for panel in [self.import_panel, self.exif_panel, self.ocr_panel, self.detection_panel, self.reconcile_panel])
