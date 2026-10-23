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
from jaguar.ui.images_panel import ImagesPanel
from jaguar.ui.cameras_panel import CamerasPanel
from jaguar.ui.settings_panel import SettingsPanel
from jaguar.core.settings import read_settings
from jaguar.ui.export_panel import ExportPanel


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
        self.images_panel = ImagesPanel()
        self.detections_page = ImagesPanel(True)
        self.cameras_panel = CamerasPanel(self.images_panel,self.ocr_panel)
        self.settings_panel = SettingsPanel()
        self.settings_panel.saved.connect(self.apply_settings)
        self.dashboard = Dashboard()
        self.export_panel=ExportPanel(self.export_scope)
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
            page_map={'Images':self.images_panel,'Detections':self.detections_page,'Cameras':self.cameras_panel,'Settings':self.settings_panel,'Export':self.export_panel}
            if title in page_map:
                self.pages.addWidget(page_map[title])
                continue
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
        self.sidebar.currentRowChanged.connect(self.refresh_page)
        self.sidebar.setCurrentRow(0)
        self.statusBar().showMessage("Local processing workspace")

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
        self.export_panel.set_project(path)
        for panel in [self.images_panel,self.detections_page,self.cameras_panel,self.settings_panel]:
            panel.set_project(path)
        self.apply_settings()
        self.dashboard.set_project(path)
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
        if self.export_panel.active():
            self.statusBar().showMessage('The workbook is still being written. Closing is available after export finishes.')
            event.ignore()
            return
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
        return self.export_panel.active() or any(panel.active() for panel in [self.import_panel, self.exif_panel, self.ocr_panel, self.detection_panel, self.reconcile_panel])

    def apply_settings(self):
        if self.project_path:
            settings=read_settings(self.project_path)
            for panel,key in [(self.exif_panel,'exiftool_path'),(self.ocr_panel,'ocr_models'),(self.detection_panel,'detector_models')]:
                if not panel.active():
                    panel.executable.setText(settings[key])

    def refresh_page(self,index):
        panels={0:self.dashboard,3:self.images_panel,4:self.cameras_panel,5:self.detections_page,6:self.review_panel}
        if index in panels:
            panels[index].refresh()

    def export_scope(self):
        return {'camera_id':self.dashboard.camera.currentData(),'start_date':self.dashboard.start.text().strip() or None,'end_date':self.dashboard.end.text().strip() or None,'run_id':self.dashboard.run.currentData()}
