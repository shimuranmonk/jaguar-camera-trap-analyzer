import json
import sqlite3
from contextlib import closing
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPen, QPixmap, QImageReader
from PySide6.QtWidgets import (QComboBox, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget)
from jaguar.ingest.service import fingerprint


class ImagesPanel(QWidget):
    def __init__(self, detection_only=False):
        super().__init__()
        self.database = None
        self.offset = 0
        self.limit = 100
        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search filenames")
        self.search.returnPressed.connect(self.reset)
        filters.addWidget(self.search)
        self.category = QComboBox()
        self.category.addItems(["All images", "animal", "person", "vehicle", "no_accepted_detection", "Open review"])
        self.category.currentTextChanged.connect(self.reset)
        filters.addWidget(self.category)
        for label, action in [("Refresh", self.reset), ("Previous", self.previous), ("Next", self.next)]:
            button=QPushButton(label)
            button.clicked.connect(action)
            filters.addWidget(button)
        self.count=QLabel()
        filters.addWidget(self.count)
        layout.addLayout(filters)
        split=QSplitter()
        self.table=QTableWidget(0,3)
        self.table.setHorizontalHeaderLabels(["Image", "Selected timestamp", "Camera/site label"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.selected)
        split.addWidget(self.table)
        detail=QWidget()
        panel=QVBoxLayout(detail)
        self.caption=QLabel("An image can be selected for inspection.")
        self.caption.setWordWrap(True)
        panel.addWidget(self.caption)
        self.scene=QGraphicsScene()
        self.view=QGraphicsView(self.scene)
        self.view.setMinimumHeight(240)
        panel.addWidget(self.view,2)
        self.tabs=QTabWidget()
        self.texts={}
        for title in ["Selected values", "EXIF", "Overlay OCR", "Detections"]:
            editor=QPlainTextEdit()
            editor.setReadOnly(True)
            self.texts[title]=editor
            self.tabs.addTab(editor,title)
        panel.addWidget(self.tabs,1)
        split.addWidget(detail)
        split.setSizes([450,650])
        layout.addWidget(split)

    def set_project(self,path):
        self.database=path
        self.reset()

    def reset(self):
        self.offset=0
        self.refresh()

    def previous(self):
        self.offset=max(0,self.offset-self.limit)
        self.refresh()

    def next(self):
        if self.table.rowCount()==self.limit:
            self.offset+=self.limit
            self.refresh()

    def refresh(self):
        if not self.database:
            return
        condition="WHERE EXISTS(SELECT 1 FROM image_paths p WHERE p.image_id=i.id AND instr(lower(p.relative_path),lower(?))>0)"
        args=[self.search.text()]
        category=self.category.currentText()
        latest="(SELECT r.id FROM processing_records r JOIN processing_runs x ON x.id=r.run_id WHERE r.image_id=i.id AND x.stage='detect' ORDER BY r.rowid DESC LIMIT 1)"
        if category in {"animal","person","vehicle"}:
            condition+=" AND EXISTS(SELECT 1 FROM detections d WHERE d.image_id=i.id AND d.record_id="+latest+" AND d.class=?)"
            args.append(category)
        elif category=="no_accepted_detection":
            condition+=" AND EXISTS(SELECT 1 FROM processing_records r WHERE r.id="+latest+" AND r.status='succeeded' AND json_extract(r.payload_json,'$.outcome')='no_accepted_detection')"
        elif category=="Open review":
            condition+=" AND EXISTS(SELECT 1 FROM reviews WHERE image_id=i.id AND status='open')"
        with closing(sqlite3.connect(self.database)) as connection:
            total=connection.execute("SELECT count(*) FROM images i "+condition,args).fetchone()[0]
            rows=connection.execute("SELECT i.id,(SELECT relative_path FROM image_paths WHERE image_id=i.id ORDER BY is_available DESC LIMIT 1),(SELECT value_json FROM metadata_resolved WHERE image_id=i.id AND field='capture_timestamp'),(SELECT value_json FROM metadata_resolved WHERE image_id=i.id AND field='site_label') FROM images i "+condition+" ORDER BY i.id LIMIT ? OFFSET ?",[*args,self.limit,self.offset]).fetchall()
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for row,(image_id,path,stamp,site) in enumerate(rows):
            time_value=json.loads(stamp) if stamp else None
            values=[path or image_id,(time_value or {}).get('iso8601','Missing'),json.loads(site) if site else None]
            for column,value in enumerate(values):
                item=QTableWidgetItem(str(value or 'Missing'))
                item.setData(Qt.ItemDataRole.UserRole,image_id)
                self.table.setItem(row,column,item)
        self.table.resizeColumnsToContents()
        self.count.setText(f"{total:,} images · page {self.offset//self.limit+1}")
        if rows:
            self.table.selectRow(0)
        self.table.blockSignals(False)
        if rows:
            self.show_image(rows[0][0])
        else:
            self.scene.clear()
            self.caption.setText('No images match these filters.')
            for text in self.texts.values():
                text.clear()

    def selected(self):
        row=self.table.currentRow()
        if row>=0:
            self.show_image(self.table.item(row,0).data(Qt.ItemDataRole.UserRole))

    def show_image(self,image_id):
        if not self.database:
            return
        with closing(sqlite3.connect(self.database)) as connection:
            expected=connection.execute('SELECT sha256 FROM images WHERE id=?',(image_id,)).fetchone()[0]
            paths=connection.execute("SELECT absolute_path FROM image_paths WHERE image_id=? ORDER BY is_available DESC",(image_id,)).fetchall()
            resolved=connection.execute("SELECT field,value_json,source,conflict,provenance_json FROM metadata_resolved WHERE image_id=? ORDER BY field",(image_id,)).fetchall()
            exif=connection.execute("SELECT raw_json,error FROM exif_records WHERE image_id=? ORDER BY rowid DESC LIMIT 1",(image_id,)).fetchone()
            records={}
            for stage in ['ocr','detect']:
                records[stage]=connection.execute("SELECT r.payload_json,r.error FROM processing_records r JOIN processing_runs p ON p.id=r.run_id WHERE image_id=? AND p.stage=? ORDER BY r.rowid DESC LIMIT 1",(image_id,stage)).fetchone()
        def format_payload(value):
            if not value:
                return 'This stage has not run.'
            try:
                return json.dumps(json.loads(value[0]),indent=2,ensure_ascii=False) if value[0] else str(value[1])
            except ValueError:
                return value[0]
        readable=[]
        for field,value,source,conflict,provenance in resolved:
            parsed=json.loads(value)
            if isinstance(parsed,dict) and 'iso8601' in parsed:
                parsed=parsed['iso8601']+(' · timezone unspecified' if not parsed.get('timezone_known') else '')
            readable.append(field.replace('_',' ').title()+': '+str(parsed if parsed is not None else 'Missing')+'\nSource: '+str(source or 'No source')+(' · conflicting source readings retained' if conflict else ''))
        self.texts['Selected values'].setPlainText('\n\n'.join(readable))
        self.texts['EXIF'].setPlainText(format_payload(exif))
        self.texts['Overlay OCR'].setPlainText(format_payload(records['ocr']))
        self.texts['Detections'].setPlainText(format_payload(records['detect']))
        self.scene.clear()
        path=None
        for row in paths:
            try:
                candidate=Path(row[0])
                if candidate.is_file() and fingerprint(candidate,lambda:False)==expected:
                    path=candidate
                    break
            except OSError:
                continue
        if path is None:
            self.caption.setText('Source file is unavailable. Stored observations remain accessible.')
            return
        self.caption.setText(path.name)
        reader=QImageReader(str(path))
        size=reader.size()
        if size.isValid():
            reader.setScaledSize(size.scaled(1400,1000,Qt.AspectRatioMode.KeepAspectRatio))
        image=reader.read()
        if image.isNull():
            self.caption.setText(path.name+' · Preview unavailable')
            return
        self.scene.addPixmap(QPixmap.fromImage(image))
        detection=records['detect']
        if detection and detection[0] and not detection[1]:
            for box in json.loads(detection[0]).get('detections',[]):
                x,y,w,h=box['box']
                color={'animal':'#387d83','person':'#b67439','vehicle':'#486c9a'}[box['class']]
                self.scene.addRect(x*image.width(),y*image.height(),w*image.width(),h*image.height(),QPen(QColor(color),3))
                label=self.scene.addSimpleText(f"{box['class']} {box['confidence']:.2f}")
                label.setBrush(QColor(color))
                label.setPos(x*image.width(),max(0,y*image.height()-20))
        self.scene.setSceneRect(0,0,image.width(),image.height())
        self.view.fitInView(self.scene.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio)
