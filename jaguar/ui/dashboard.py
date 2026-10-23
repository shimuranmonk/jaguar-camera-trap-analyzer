import sqlite3
from contextlib import closing
from PySide6.QtWidgets import (QComboBox,QFrame,QGridLayout,QHBoxLayout,QLabel,QLineEdit,QProgressBar,QPushButton,QScrollArea,QVBoxLayout,QWidget)
from jaguar.core.dashboard import dashboard_data


class Dashboard(QScrollArea):
    def __init__(self):
        super().__init__()
        self.database=None
        self.setWidgetResizable(True)
        body=QWidget()
        layout=QVBoxLayout(body)
        title=QLabel('Project dashboard')
        title.setObjectName('heading')
        layout.addWidget(title)
        filters=QHBoxLayout()
        self.camera=QComboBox()
        self.camera.addItem('All physical cameras',None)
        self.start,self.end=QLineEdit(),QLineEdit()
        self.start.setPlaceholderText('From YYYY-MM-DD')
        self.end.setPlaceholderText('Through YYYY-MM-DD')
        self.run=QComboBox()
        self.run.addItem('All images · latest results',None)
        for widget in [self.camera,self.start,self.end,self.run]:
            filters.addWidget(widget)
        apply=QPushButton('Apply / refresh')
        apply.clicked.connect(self.refresh)
        filters.addWidget(apply)
        layout.addLayout(filters)
        note=QLabel('Filters select images; cards show their latest processing results. Coverage and timeline use selected metadata.')
        note.setWordWrap(True)
        note.setObjectName('muted')
        layout.addWidget(note)
        self.cards=QWidget()
        self.content=QVBoxLayout(self.cards)
        self.content.setContentsMargins(0,0,0,0)
        self.content.setSpacing(14)
        layout.addWidget(self.cards)
        self.setWidget(body)
        self.refresh()

    def card(self,title):
        frame=QFrame()
        frame.setObjectName('card')
        layout=QVBoxLayout(frame)
        layout.setContentsMargins(18,14,18,14)
        label=QLabel(title)
        label.setObjectName('muted')
        layout.addWidget(label)
        return frame,layout

    def set_project(self,path):
        self.database=path
        self.camera.clear()
        self.camera.addItem('All physical cameras',None)
        self.camera.addItem('Unassigned physical camera','unassigned')
        self.run.clear()
        self.run.addItem('All images · latest results',None)
        with closing(sqlite3.connect(path)) as connection:
            for identity,label in connection.execute('SELECT id,label FROM cameras ORDER BY label'):
                self.camera.addItem(label,identity)
            for identity,stage,at in connection.execute('SELECT id,stage,started_at FROM processing_runs ORDER BY rowid DESC LIMIT 50'):
                self.run.addItem(stage+' · '+at[:19],identity)
        self.refresh()

    def refresh(self):
        while self.content.count():
            item=self.content.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if not self.database:
            self.content.addWidget(QLabel('Project statistics appear after a project opens.'))
            return
        try:
            data=dashboard_data(self.database,self.camera.currentData(),self.start.text().strip() or None,self.end.text().strip() or None,self.run.currentData())
        except (ValueError,sqlite3.Error) as error:
            self.content.addWidget(QLabel('Statistics unavailable: '+str(error)))
            return
        tile_widget=QWidget()
        tiles=QGridLayout(tile_widget)
        tiles.setContentsMargins(0,0,0,0)
        for index,(label,value) in enumerate([('Total images',data['total']),('Processing complete',data['processed']),('Additional duplicate paths',data['duplicates']),('Animal images',data['categories'].get('animal',0)),('Images requiring review',data['review_images']),('Failed processing stages',data['failures'])]):
            frame,layout=self.card(label)
            number=QLabel(f'{value:,}')
            number.setObjectName('value')
            layout.addWidget(number)
            tiles.addWidget(frame,index//3,index%3)
        self.content.addWidget(tile_widget)
        row_widget=QWidget()
        row=QHBoxLayout(row_widget)
        row.setContentsMargins(0,0,0,0)
        frame,layout=self.card('Selected metadata coverage')
        for label,field in [('Capture time','capture_timestamp'),('Manufacturer','make'),('Model','model'),('Camera/site label','site_label'),('Temperature','temperature_c')]:
            value=data['coverage'][field]
            layout.addWidget(QLabel(f'{label}: {value} / {data["total"]}'))
            bar=QProgressBar()
            bar.setRange(0,max(1,data['total']))
            bar.setValue(value)
            bar.setTextVisible(False)
            layout.addWidget(bar)
        row.addWidget(frame,2)
        frame,layout=self.card('Detection and review')
        for category in ['animal','person','vehicle']:
            layout.addWidget(QLabel(f'{category.title()} images: {data["categories"].get(category,0)}'))
        layout.addWidget(QLabel('No accepted detection: '+str(data['no_detection'])))
        for status in ['open','deferred','resolved']:
            layout.addWidget(QLabel(f'{status.title()} review cases: {data["reviews"].get(status,0)}'))
        label=QLabel('Category overlap is allowed. Zero accepted boxes does not confirm an empty image.')
        label.setWordWrap(True)
        label.setObjectName('muted')
        layout.addWidget(label)
        row.addWidget(frame,1)
        self.content.addWidget(row_widget)
        lower=QWidget()
        row=QHBoxLayout(lower)
        row.setContentsMargins(0,0,0,0)
        frame,layout=self.card('Capture dates · latest 15 dates')
        for day,count in data['timeline']:
            layout.addWidget(QLabel(f'{day}: {count} images'))
        layout.addWidget(QLabel(f'Missing capture time: {data["total"]-data["coverage"]["capture_timestamp"]}'))
        row.addWidget(frame)
        frame,layout=self.card('Manufacturer distribution')
        for make,count in data['cameras']:
            layout.addWidget(QLabel(f'{make}: {count} images'))
        layout.addStretch()
        row.addWidget(frame)
        self.content.addWidget(lower)
        self.content.addWidget(QLabel('Processing complete requires successful EXIF, OCR, detection, and reconciliation stages.'))
