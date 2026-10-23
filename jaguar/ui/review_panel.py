import json
import sqlite3
from contextlib import closing

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QInputDialog, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from jaguar.core.reconciliation import FIELDS, Reconciler, correction, set_review_status
from jaguar.core.stages import run_stage


class ReviewPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.database = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Review queue · raw observations remain unchanged"))
        row = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItems(["open", "deferred", "resolved", "superseded", "all"])
        self.filter.currentTextChanged.connect(self.refresh)
        row.addWidget(self.filter)
        for title, action in [("Refresh", self.refresh), ("Resolve", lambda: self.change_status("resolved")), ("Defer", lambda: self.change_status("deferred")), ("Reopen", lambda: self.change_status("open")), ("Correct field", self.correct), ("Revert correction", lambda: self.correct(True))]:
            button = QPushButton(title)
            button.clicked.connect(action)
            row.addWidget(button)
        layout.addLayout(row)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Image", "Review reason", "Status", "Evidence"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

    def set_project(self, path):
        self.database = path
        self.refresh()

    def refresh(self):
        if not self.database:
            return
        condition = "" if self.filter.currentText() == "all" else " WHERE r.status=?"
        args = () if not condition else (self.filter.currentText(),)
        with closing(sqlite3.connect(self.database)) as connection:
            rows = connection.execute("SELECT r.id,r.image_id,(SELECT relative_path FROM image_paths WHERE image_id=r.image_id LIMIT 1),r.reason,r.status,r.detail_json FROM reviews r" + condition + " ORDER BY r.created_at DESC LIMIT 500", args).fetchall()
        self.table.setRowCount(len(rows))
        for index, (review_id,image_id,path,reason,status,evidence) in enumerate(rows):
            for column,value in enumerate([path or image_id,reason,status,evidence]):
                item = QTableWidgetItem(str(value or ""))
                item.setData(Qt.ItemDataRole.UserRole, (review_id,image_id))
                self.table.setItem(index,column,item)
        self.table.resizeColumnsToContents()

    def selected(self):
        row = self.table.currentRow()
        return self.table.item(row,0).data(Qt.ItemDataRole.UserRole) if row >= 0 else None

    def change_status(self, status):
        selected = self.selected()
        if not selected:
            return
        try:
            set_review_status(self.database, selected[0], status)
            self.refresh()
        except (ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self,"Review could not be saved",str(error))

    def correct(self, revert=False):
        selected = self.selected()
        if not selected:
            return
        field, accepted = QInputDialog.getItem(self,"Metadata field","Field",sorted(FIELDS),0,False)
        if not accepted:
            return
        value = None
        if not revert:
            value, accepted = QInputDialog.getText(self,"Correction","Value (ISO date/time; blank clears the selected value)")
            if not accepted:
                return
            value = value.strip() or None
        reason, accepted = QInputDialog.getText(self,"Correction reason","Reason")
        if not accepted:
            return
        try:
            correction(self.database,selected[1],field,value,reason,revert=bool(revert))
            with closing(sqlite3.connect(self.database)) as connection:
                settings=json.loads(connection.execute("SELECT settings_json FROM projects").fetchone()[0])
            engine=Reconciler(settings.get("timestamp_tolerance_seconds",0),settings.get("temperature_tolerance_c",1))
            run_stage(self.database,"reconcile",{"image_ids":[selected[1]]},engine)
            self.refresh()
        except (ValueError,sqlite3.Error) as error:
            QMessageBox.warning(self,"Correction could not be saved",str(error))
