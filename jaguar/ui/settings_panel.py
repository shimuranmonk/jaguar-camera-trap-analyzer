import sqlite3
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox,QDoubleSpinBox,QFileDialog,QFormLayout,QHBoxLayout,QLabel,QLineEdit,QMessageBox,QPushButton,QVBoxLayout,QWidget)
from jaguar.core.settings import read_settings,save_settings


class SettingsPanel(QWidget):
    saved=Signal()
    def __init__(self):
        super().__init__()
        self.database=None
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel('Local models and processing settings'))
        form=QFormLayout()
        self.fields={}
        for name,label in [('exiftool_path','ExifTool executable'),('ocr_models','OCR model folder'),('detector_models','MegaDetector model folder'),('species_models','SpeciesNet model folder'),('species_python','SpeciesNet Python executable')]:
            widget=QWidget()
            row=QHBoxLayout(widget)
            row.setContentsMargins(0,0,0,0)
            entry=QLineEdit()
            self.fields[name]=entry
            row.addWidget(entry)
            browse=QPushButton('Browse')
            browse.clicked.connect(lambda checked=False,name=name:self.browse(name))
            row.addWidget(browse)
            form.addRow(label,widget)
        for name,label in [('species_country','Species country (optional ISO alpha-3)'),('species_admin1','Species state (USA only, optional)')]:
            entry=QLineEdit()
            self.fields[name]=entry
            form.addRow(label,entry)
        for name,label,maximum in [('detection_threshold','Detection confidence threshold',1),('timestamp_tolerance_seconds','Timestamp conflict tolerance (seconds)',3600),('temperature_tolerance_c','Temperature conflict tolerance (C)',100)]:
            entry=QDoubleSpinBox()
            entry.setRange(0,maximum)
            entry.setDecimals(3)
            self.fields[name]=entry
            form.addRow(label,entry)
        self.device=QComboBox()
        self.device.addItems(['auto','cpu'])
        form.addRow('Detection device',self.device)
        layout.addLayout(form)
        note=QLabel('Inference starts at batch size one. Automatic detection uses CUDA only when a compatible runtime and device are available; failures retry CPU. OCR currently uses CPU. Settings do not download models or alter original images.')
        note.setWordWrap(True)
        layout.addWidget(note)
        save=QPushButton('Save project settings')
        save.clicked.connect(self.save)
        layout.addWidget(save)
        layout.addStretch()

    def set_project(self,path):
        self.database=path
        settings=read_settings(path)
        for name,entry in self.fields.items():
            if isinstance(entry,QLineEdit):
                entry.setText(settings[name])
            else:
                entry.setValue(settings[name])
        self.device.setCurrentText(settings['device'])

    def browse(self,name):
        value=QFileDialog.getOpenFileName(self,'Local executable','','Executable (*.exe)')[0] if name in {'exiftool_path','species_python'} else QFileDialog.getExistingDirectory(self,'Local model folder')
        if value:
            self.fields[name].setText(value)

    def save(self):
        if not self.database:
            return
        settings={name:entry.text() if isinstance(entry,QLineEdit) else entry.value() for name,entry in self.fields.items()}
        settings['device']=self.device.currentText()
        try:
            save_settings(self.database,settings)
            self.saved.emit()
        except (ValueError,sqlite3.Error) as error:
            QMessageBox.warning(self,'Settings could not be saved',str(error))
