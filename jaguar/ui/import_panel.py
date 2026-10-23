import json
import multiprocessing
import queue
import sqlite3
from contextlib import closing
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QLabel, QListWidget, QPushButton, QVBoxLayout, QWidget

from jaguar.ingest.service import latest_summary, latest_errors
from jaguar.workers.import_worker import run_import


class ImportPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.database = None
        self.process = None
        self.messages = None
        self.stop = None
        self.worker_error = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Source folders"))
        self.roots = QListWidget()
        layout.addWidget(self.roots)
        self.add = QPushButton("Add folder")
        self.add.clicked.connect(self.choose_folder)
        layout.addWidget(self.add)
        self.remove = QPushButton("Remove selected folder")
        self.remove.clicked.connect(lambda: self.roots.takeItem(self.roots.currentRow()))
        layout.addWidget(self.remove)
        self.start = QPushButton("Start / resume import")
        self.start.clicked.connect(self.begin)
        layout.addWidget(self.start)
        self.pause = QPushButton("Pause import")
        self.pause.clicked.connect(self.request_pause)
        self.pause.setEnabled(False)
        layout.addWidget(self.pause)
        self.report = QLabel("A project must be opened before import.")
        self.report.setWordWrap(True)
        layout.addWidget(self.report)
        self.errors = QListWidget()
        self.errors.setMaximumHeight(140)
        layout.addWidget(self.errors)
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.poll)
        self.enable_inputs(False)

    def enable_inputs(self, enabled):
        self.add.setEnabled(enabled)
        self.remove.setEnabled(enabled)
        self.start.setEnabled(enabled)

    def active(self):
        return self.process is not None

    def set_project(self, path):
        self.database = Path(path)
        self.roots.clear()
        with closing(sqlite3.connect(path)) as connection:
            previous = connection.execute("SELECT roots_json FROM import_runs ORDER BY rowid DESC LIMIT 1").fetchone()
            roots = json.loads(previous[0]) if previous else [row[0] for row in connection.execute("SELECT folder_path FROM source_roots ORDER BY added_at")]
            for root in roots:
                self.roots.addItem(root)
        self.enable_inputs(True)
        value = self.current_summary()
        if value:
            self.display(value)
            self.show_errors()
            if value["status"] in {"running", "paused"}:
                self.report.setText(self.report.text() + "\nA previous import can resume with the same folders.")
        else:
            self.errors.clear()
            self.report.setText("No images imported. Original files remain in their source folders.")

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Source folder")
        if folder and folder not in [self.roots.item(i).text() for i in range(self.roots.count())]:
            self.roots.addItem(folder)

    def begin(self):
        if self.active() or not self.database:
            return
        roots = [self.roots.item(i).text() for i in range(self.roots.count())]
        if not roots:
            self.report.setText("At least one source folder is required.")
            return
        context = multiprocessing.get_context("spawn")
        self.worker_error = None
        self.messages = context.Queue(maxsize=32)
        self.stop = context.Event()
        self.process = context.Process(target=run_import, args=(str(self.database), roots, self.stop, self.messages))
        try:
            self.process.start()
        except (OSError, RuntimeError):
            self.process = None
            self.messages.close()
            self.report.setText("The import worker could not start.")
            return
        self.enable_inputs(False)
        self.pause.setEnabled(True)
        self.report.setText("Scanning source folders…")
        self.timer.start()

    def request_pause(self):
        if self.stop:
            self.stop.set()
            self.pause.setEnabled(False)
            self.report.setText("Pausing after the current checkpoint…")

    def display(self, value):
        self.report.setText(f"Import: {value['status']} | Discovered files: {value['discovered']}\n"
            f"Imported paths: {value['imported']} | Unique images in run: {value['unique_images']} | Duplicate paths in run: {value['duplicates']}\n"
            f"Pending: {value['pending']} | Failed files: {value['failed']} | Folder scan errors: {value['scan_errors']}")
        if value.get("last_error"):
            self.report.setText(self.report.text() + "\n" + value["last_error"])

    def show_errors(self):
        self.errors.clear()
        for path, message in latest_errors(self.database):
            self.errors.addItem(f"{path}: {message}")

    def current_summary(self):
        return latest_summary(self.database)

    def poll(self):
        for _ in range(32):
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            if "error" in message:
                self.worker_error = message["error"]
                self.report.setText(message["error"])
            else:
                self.display(message.get("final", message))
        if self.process is not None and not self.process.is_alive():
            exitcode = self.process.exitcode
            self.process.join(timeout=0)
            self.process.close()
            self.process = None
            self.timer.stop()
            self.messages.close()
            self.enable_inputs(True)
            self.pause.setEnabled(False)
            value = self.current_summary()
            if value:
                self.display(value)
                self.show_errors()
                if value["status"] == "running" or exitcode:
                    self.report.setText(self.report.text() + "\nWorker interrupted. The import can resume from committed checkpoints.")
            if self.worker_error:
                self.report.setText(self.report.text() + "\n" + self.worker_error)
