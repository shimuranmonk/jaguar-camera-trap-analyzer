import json
import sqlite3
from contextlib import closing
from uuid import uuid4
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox,QGridLayout,QHBoxLayout,QInputDialog,QLabel,QLineEdit,QMessageBox,QPushButton,QVBoxLayout,QWidget)
from jaguar.ui.images_panel import ImagesPanel
from jaguar.core.species import label_fields,review_species,assign_species_location


class SpeciesPanel(QWidget):
    def __init__(self,stage):
        super().__init__()
        self.stage=stage
        self.database=None
        self.suggestion=None
        self.image_id=None
        self.record_id=None
        self.predictions={}
        layout=QVBoxLayout(self)
        note=QLabel('Species suggestions require human review. Each detection can retain a separate identification; image-level manual annotations also support missed animals.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.browser=ImagesPanel()
        layout.addWidget(self.browser,1)
        row=QHBoxLayout()
        self.target=QComboBox()
        self.target.currentIndexChanged.connect(self.display)
        row.addWidget(self.target)
        self.actor=QLineEdit('local operator')
        self.actor.setPlaceholderText('Reviewer name')
        row.addWidget(self.actor)
        self.country=QComboBox()
        for label,code in [('Project default',''),('Saudi Arabia','SAU'),('Dubai / United Arab Emirates','ARE')]:
            self.country.addItem(label,code)
        row.addWidget(self.country)
        location=QPushButton('Assign country')
        location.clicked.connect(self.assign_country)
        row.addWidget(location)
        layout.addLayout(row)
        buttons=QGridLayout()
        for index,(title,callback) in enumerate([('Refresh identifications',self.refresh),('Process selected image',self.process_selected),('Confirm suggestion',self.confirm),('Manual identification',self.manual),('Leave unidentified',lambda:self.save(None,None,'unidentified')),('Clear confirmation',lambda:self.save(None,None,'unreviewed'))]):
            button=QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button,index//3,index%3)
        layout.addLayout(buttons)
        self.summary=QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.browser.table.itemSelectionChanged.connect(self.load_selected)

    def set_project(self,path):
        self.database=path
        self.browser.set_project(path)
        self.load_selected()

    def refresh(self):
        self.browser.refresh()
        self.load_selected()

    def load_selected(self):
        row=self.browser.table.currentRow()
        self.image_id=self.browser.table.item(row,0).data(Qt.ItemDataRole.UserRole) if row>=0 else None
        self.target.blockSignals(True)
        self.target.clear()
        self.predictions={}
        self.record_id=None
        if self.image_id and self.database:
            with closing(sqlite3.connect(self.database)) as c:
                record=c.execute("SELECT r.id,r.payload_json,r.error FROM processing_records r JOIN processing_runs p ON p.id=r.run_id WHERE image_id=? AND p.stage='species' ORDER BY r.rowid DESC LIMIT 1",(self.image_id,)).fetchone()
                payload=json.loads(record[1]) if record and record[1] else {}
                self.record_id=record[0] if record else None
                for target,result in zip(payload.get('targets',[]),payload.get('native',{}).get('detections',[])):
                    self.predictions[target['detection_id']]=result
                det=c.execute("SELECT r.id FROM processing_records r JOIN processing_runs p ON p.id=r.run_id WHERE image_id=? AND p.stage='detect' ORDER BY r.rowid DESC LIMIT 1",(self.image_id,)).fetchone()
                if det:
                    for identity,class_ in c.execute('SELECT id,class FROM detections WHERE record_id=? ORDER BY rowid',(det[0],)):
                        self.target.addItem(class_+' — '+identity[:8],identity)
                location=c.execute('SELECT country FROM species_locations WHERE image_id=?',(self.image_id,)).fetchone()
                code=location[0] if location else ''
                if self.country.findData(code)<0:
                    self.country.addItem(code,code)
                self.country.setCurrentIndex(self.country.findData(code))
                self.target.addItem('Image-level manual annotation',None)
                for key, in c.execute("SELECT DISTINCT target_key FROM species_reviews WHERE image_id=? AND detection_id IS NULL ORDER BY target_key",(self.image_id,)):
                    self.target.addItem('Manual annotation '+key[:8],key)
        self.target.blockSignals(False)
        self.display()

    def display(self):
        key=self.target.currentData()
        result=self.predictions.get(key,{})
        suggested=result.get('processed_classification',{})
        self.suggestion=suggested.get('class')
        candidates=result.get('raw_classifications',{})
        text=['Suggestion: '+str(label_fields(self.suggestion).get('common_name') or 'None'),
              'Score: '+str(suggested.get('score','Unavailable'))+' | Source: '+str(suggested.get('source','Unavailable'))]
        text.append('Alternatives: '+', '.join(str(label_fields(label)['common_name'])+' ('+format(score,'.3f')+')' for label,score in list(zip(candidates.get('classes',[]),candidates.get('scores',[])))[:5]))
        if self.database and self.image_id and key:
            with closing(sqlite3.connect(self.database)) as c:
                review=c.execute('SELECT label,scientific_name,status,actor FROM species_reviews WHERE image_id=? AND target_key=? ORDER BY rowid DESC LIMIT 1',(self.image_id,key)).fetchone()
            text.append('Current review: '+str(review or 'Unreviewed'))
        self.summary.setText('\n'.join(text))

    def process_selected(self):
        if self.image_id:
            self.stage.set_scope([self.image_id])
            self.stage.begin()
            self.summary.setText('Species processing started. Refresh identifications becomes available after processing finishes.')

    def assign_country(self):
        if not self.image_id:
            return
        reason,ok=QInputDialog.getText(self,'Image country assignment','Assignment reason')
        if not ok:
            return
        try:
            assign_species_location(self.database,self.image_id,self.country.currentData(),self.actor.text(),reason)
            self.load_selected()
        except (ValueError,sqlite3.Error,OSError) as error:
            QMessageBox.warning(self,'Country could not be assigned',str(error))

    def confirm(self):
        if self.suggestion:
            fields=label_fields(self.suggestion)
            self.save(fields['common_name'],fields['scientific_name'],'confirmed')

    def manual(self):
        label,ok=QInputDialog.getText(self,'Manual identification','Common name or broader label')
        if not ok or not label.strip():
            return
        scientific,ok=QInputDialog.getText(self,'Manual identification','Scientific name (optional)')
        if ok:
            self.save(label,scientific or None,'confirmed')

    def save(self,label,scientific,status):
        if not self.image_id:
            return
        reason,ok=QInputDialog.getText(self,'Identification review','Review reason')
        if not ok:
            return
        key=self.target.currentData()
        manual=key is None or key not in self.predictions
        try:
            # Detection targets remain linked even when no classifier run exists.
            with closing(sqlite3.connect(self.database)) as c:
                detection=key if key and c.execute('SELECT 1 FROM detections WHERE id=?',(key,)).fetchone() else None
            saved_key=key or str(uuid4())
            review_species(self.database,self.image_id,saved_key,label,scientific,self.actor.text(),reason,status,
                detection_id=detection,source_record_id=self.record_id if not manual else None)
            self.load_selected()
            self.target.setCurrentIndex(self.target.findData(saved_key))
        except (ValueError,sqlite3.Error,OSError) as error:
            QMessageBox.warning(self,'Identification could not be saved',str(error))
