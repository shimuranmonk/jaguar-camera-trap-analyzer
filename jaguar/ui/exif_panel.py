import multiprocessing
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QLabel, QLineEdit, QPushButton

from jaguar.ui.import_panel import ImportPanel
from jaguar.workers.import_worker import run_exif


class ExifPanel(ImportPanel):
    def __init__(self):
        super().__init__()
        self.roots.hide()
        self.add.hide()
        self.remove.hide()
        self.layout().itemAt(0).widget().setText("EXIF metadata extraction")
        self.executable = QLineEdit(os.environ.get("JAGUAR_EXIFTOOL", ""))
        self.executable.setPlaceholderText("Path to exiftool.exe")
        self.layout().insertWidget(1, self.executable)
        browse = QPushButton("Locate ExifTool executable")
        browse.clicked.connect(self.choose_tool)
        self.layout().insertWidget(2, browse)
        self.start.setText("Extract / resume EXIF")
        self.pause.setText("Pause EXIF extraction")
        self.layout().addWidget(QLabel("Raw responses and normalized candidates remain separate. Overlay OCR follows in Gate 4."))

    def choose_tool(self):
        path, _ = QFileDialog.getOpenFileName(self, "ExifTool executable", "", "Executable (*.exe)")
        if path:
            self.executable.setText(path)

    def set_project(self, path):
        self.database = Path(path)
        self.enable_inputs(True)
        value = self.current_summary()
        if value:
            self.display(value)
        else:
            self.report.setText("EXIF extraction has not run for this project.")
        self.show_errors()

    def current_summary(self):
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute("SELECT id,status FROM exif_runs ORDER BY rowid DESC LIMIT 1").fetchone()
            if not row:
                return None
            counts = dict(connection.execute("SELECT status,count(*) FROM exif_records WHERE run_id=? GROUP BY status", (row[0],)))
            return {"run_id": row[0], "status": row[1], "total": connection.execute("SELECT count(*) FROM images").fetchone()[0], "succeeded": counts.get("succeeded", 0), "failed": counts.get("failed", 0)}

    def show_errors(self):
        self.errors.clear()
        with closing(sqlite3.connect(self.database)) as connection:
            for path, error in connection.execute("SELECT source_path,error FROM exif_records WHERE run_id=(SELECT id FROM exif_runs ORDER BY rowid DESC LIMIT 1) AND status='failed' LIMIT 10"):
                self.errors.addItem(f"{path}: {error}")

    def display(self, value):
        self.report.setText(f"EXIF run: {value['status']} | Logical images: {value['total']}\nSucceeded: {value['succeeded']} | Failed: {value['failed']}")

    def begin(self):
        if self.active() or not self.database:
            return
        if not self.executable.text().strip():
            self.report.setText("An ExifTool executable is required.")
            return
        context = multiprocessing.get_context("spawn")
        self.worker_error = None
        self.messages = context.Queue(maxsize=32)
        self.stop = context.Event()
        self.process = context.Process(target=run_exif, args=(str(self.database), self.executable.text().strip(), self.stop, self.messages))
        try:
            self.process.start()
        except (OSError, RuntimeError):
            self.process = None
            self.messages.close()
            self.report.setText("The EXIF worker could not start.")
            return
        self.enable_inputs(False)
        self.pause.setEnabled(True)
        self.report.setText("Extracting EXIF metadata…")
        self.timer.start()
