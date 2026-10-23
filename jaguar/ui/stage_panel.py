import multiprocessing
import sqlite3
from contextlib import closing

from PySide6.QtWidgets import QFileDialog

from jaguar.ui.exif_panel import ExifPanel
from jaguar.workers.import_worker import run_processing


class StagePanel(ExifPanel):
    def __init__(self, stage):
        self.stage = stage
        super().__init__()
        self.layout().itemAt(0).widget().setText("Overlay OCR" if stage == "ocr" else "Animal / person / vehicle detection")
        self.executable.clear()
        self.executable.setPlaceholderText("Local OCR model folder containing det and rec" if stage == "ocr" else "Local MegaDetector model folder containing checkpoints")
        self.layout().itemAt(2).widget().setText("Locate local models")
        self.start.setText("Start / resume " + stage)
        self.pause.setText("Pause " + stage)
        self.layout().itemAt(self.layout().count()-1).widget().setText("Local inference preserves original images and processing provenance.")

    def choose_tool(self):
        path = QFileDialog.getExistingDirectory(self, "OCR model folder" if self.stage == "ocr" else "MegaDetector model folder containing checkpoints")
        if path:
            self.executable.setText(path)

    def current_summary(self):
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute("SELECT id,status FROM processing_runs WHERE stage=? ORDER BY rowid DESC LIMIT 1", (self.stage,)).fetchone()
            if not row:
                return None
            counts = dict(connection.execute("SELECT status,count(*) FROM processing_records WHERE run_id=? GROUP BY status", (row[0],)))
            return {"run_id": row[0], "status": row[1], "total": connection.execute("SELECT count(*) FROM images").fetchone()[0], "succeeded": counts.get("succeeded", 0), "failed": counts.get("failed", 0)}

    def display(self, value):
        self.report.setText(f"{self.stage.upper()}: {value['status']} | Logical images: {value['total']}\nSucceeded: {value['succeeded']} | Failed: {value['failed']}")

    def show_errors(self):
        self.errors.clear()
        with closing(sqlite3.connect(self.database)) as connection:
            for image_id, error in connection.execute("SELECT image_id,error FROM processing_records WHERE run_id=(SELECT id FROM processing_runs WHERE stage=? ORDER BY rowid DESC LIMIT 1) AND status='failed' LIMIT 10", (self.stage,)):
                self.errors.addItem(f"{image_id}: {error}")

    def begin(self):
        if self.active() or not self.database:
            return
        if not self.executable.text().strip():
            self.report.setText("Local model files are required.")
            return
        context = multiprocessing.get_context("spawn")
        self.worker_error = None
        self.messages = context.Queue(maxsize=32)
        self.stop = context.Event()
        self.process = context.Process(target=run_processing, args=(str(self.database), self.stage, self.executable.text().strip(), self.stop, self.messages))
        try:
            self.process.start()
        except (OSError, RuntimeError):
            self.process = None
            self.messages.close()
            self.report.setText("The processing worker could not start.")
            return
        self.enable_inputs(False)
        self.pause.setEnabled(True)
        self.report.setText("Loading local models…")
        self.timer.start()
