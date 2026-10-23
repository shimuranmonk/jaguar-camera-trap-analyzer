import sqlite3
from contextlib import closing

from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QScrollArea, QVBoxLayout, QWidget)


class Dashboard(QScrollArea):
    def __init__(self):
        super().__init__()
        self.database = None
        self.setWidgetResizable(True)
        self.body = QWidget()
        self.layout = QVBoxLayout(self.body)
        self.layout.setSpacing(18)
        self.setWidget(self.body)
        self.refresh()

    def card(self, title, parent):
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 16, 18, 16)
        label = QLabel(title)
        label.setObjectName("muted")
        layout.addWidget(label)
        parent.addWidget(frame)
        return layout

    def refresh(self):
        def clear(layout):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().hide()
                    item.widget().deleteLater()
                elif item.layout():
                    clear(item.layout())
                    item.layout().deleteLater()
        clear(self.layout)
        title = QLabel("Project dashboard")
        title.setObjectName("heading")
        self.layout.addWidget(title)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Scope: all images in the open project"))
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        controls.addWidget(refresh)
        export = QPushButton("Excel export · planned")
        export.setEnabled(False)
        controls.addWidget(export)
        self.layout.addLayout(controls)
        if self.database is None:
            self.layout.addWidget(QLabel("Project statistics will appear after a project opens."))
            self.layout.addStretch()
            return
        try:
            with closing(sqlite3.connect(self.database, timeout=0.1)) as connection:
                images = connection.execute("SELECT count(*) FROM images").fetchone()[0]
                paths = connection.execute("SELECT count(*) FROM image_paths WHERE is_available=1").fetchone()[0]
                represented = connection.execute("SELECT count(DISTINCT image_id) FROM image_paths WHERE is_available=1").fetchone()[0]
                latest = "FROM exif_records WHERE rowid IN (SELECT max(rowid) FROM exif_records GROUP BY image_id)"
                exif = connection.execute("SELECT count(*) " + latest + " AND status='succeeded'").fetchone()[0]
                failures = connection.execute("SELECT count(*) " + latest + " AND status='failed'").fetchone()[0]
                formats = connection.execute("SELECT media_type,count(*) FROM images GROUP BY media_type").fetchall()
                coverage = [(label, connection.execute("SELECT count(*) " + latest + " AND json_extract(normalized_json,?) IS NOT NULL", (f"$.fields.{field}.value",)).fetchone()[0]) for label, field in [("Capture timestamp", "capture_timestamp"), ("Manufacturer", "make"), ("Camera model", "model"), ("Temperature", "temperature_c"), ("Latitude", "latitude")]]
                run = connection.execute("SELECT status FROM import_runs ORDER BY rowid DESC LIMIT 1").fetchone()
        except sqlite3.Error:
            self.layout.addWidget(QLabel("Project data is busy. Refresh will retry the statistics."))
            return
        tiles = QWidget()
        grid = QGridLayout(tiles)
        grid.setContentsMargins(0, 0, 0, 0)
        for index, (label, value) in enumerate([("Logical images", images), ("Available source paths", paths), ("Additional duplicate paths", paths-represented), ("EXIF extracted", exif), ("EXIF remaining / failed", images-exif), ("Latest EXIF failures", failures)]):
            holder = QVBoxLayout()
            tile = QWidget()
            tile.setLayout(holder)
            card = self.card(label, holder)
            number = QLabel(f"{value:,}")
            number.setObjectName("value")
            card.addWidget(number)
            grid.addWidget(tile, index // 3, index % 3)
        self.layout.addWidget(tiles)
        panels = QWidget()
        row = QHBoxLayout(panels)
        coverage_card = self.card("Metadata coverage · latest EXIF candidates", row)
        for label, count in coverage:
            coverage_card.addWidget(QLabel(f"{label}: {count:,} / {images:,}"))
            bar = QProgressBar()
            bar.setRange(0, max(1, images))
            bar.setValue(count)
            bar.setTextVisible(False)
            coverage_card.addWidget(bar)
        formats_card = self.card("Imported file formats", row)
        for media, count in formats:
            formats_card.addWidget(QLabel(f"{media}: {count:,}"))
        formats_card.addStretch()
        formats_card.addWidget(QLabel("Latest import: " + (run[0] if run else "Not started")))
        self.layout.addWidget(panels)
        note = QLabel("Animal/person/vehicle results and review progress will appear after their processing gates. Missing metadata remains empty.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        self.layout.addWidget(note)
        self.layout.addStretch()
