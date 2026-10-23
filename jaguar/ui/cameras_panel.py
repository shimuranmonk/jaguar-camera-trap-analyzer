import sqlite3
from contextlib import closing
from PySide6.QtWidgets import (QComboBox,QFormLayout,QHBoxLayout,QInputDialog,QLabel,QLineEdit,QMessageBox,QPushButton,QTableWidget,QTableWidgetItem,QVBoxLayout,QWidget)
from jaguar.core.cameras import add_camera,add_deployment,assign_camera
from jaguar.core.stages import assign_profile
from jaguar.ocr.profiles import PROFILES


class CamerasPanel(QWidget):
    def __init__(self,images_panel,ocr_panel):
        super().__init__()
        self.database=None
        self.images_panel,self.ocr_panel=images_panel,ocr_panel
        layout=QVBoxLayout(self)
        text=QLabel('Physical cameras and deployments are separate identities. Assignments apply to the image selected in Images.')
        text.setWordWrap(True)
        layout.addWidget(text)
        form=QFormLayout()
        self.label,self.make,self.model,self.serial=[QLineEdit() for _ in range(4)]
        for title,widget in [('Physical camera label',self.label),('Manufacturer',self.make),('Model',self.model),('Serial number',self.serial)]:
            form.addRow(title,widget)
        layout.addLayout(form)
        add=QPushButton('Register physical camera')
        add.clicked.connect(self.create_camera)
        layout.addWidget(add)
        self.camera,self.deployment=QComboBox(),QComboBox()
        self.camera.currentIndexChanged.connect(self.load_deployments)
        row=QHBoxLayout()
        row.addWidget(self.camera)
        row.addWidget(self.deployment)
        layout.addLayout(row)
        actions=QHBoxLayout()
        for title,action in [('Add deployment',self.create_deployment),('Assign selected image',self.assign),('Assign OCR profile',self.profile),('Refresh',self.refresh)]:
            button=QPushButton(title)
            button.clicked.connect(action)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.table=QTableWidget(0,4)
        self.table.setHorizontalHeaderLabels(['Physical camera','Make','Model','Serial'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

    def set_project(self,path):
        self.database=path
        self.refresh()

    def refresh(self):
        if not self.database:
            return
        self.camera.clear()
        self.camera.addItem('Unassigned physical camera',None)
        with closing(sqlite3.connect(self.database)) as connection:
            rows=connection.execute('SELECT id,label,make,model,serial FROM cameras ORDER BY label').fetchall()
        self.table.setRowCount(len(rows))
        for index,(identity,label,make,model,serial) in enumerate(rows):
            self.camera.addItem(label,identity)
            for column,value in enumerate([label,make,model,serial]):
                self.table.setItem(index,column,QTableWidgetItem(value or 'Unknown'))
        self.load_deployments()

    def load_deployments(self):
        self.deployment.clear()
        self.deployment.addItem('Unassigned deployment',None)
        if self.database:
            with closing(sqlite3.connect(self.database)) as connection:
                for identity,label in connection.execute('SELECT id,site_label FROM deployments WHERE camera_id IS ? ORDER BY site_label',(self.camera.currentData(),)):
                    self.deployment.addItem(label,identity)

    def create_camera(self):
        if not self.database:
            return
        try:
            add_camera(self.database,self.label.text(),self.make.text(),self.model.text(),self.serial.text())
            self.refresh()
        except (ValueError,sqlite3.Error) as error:
            QMessageBox.warning(self,'Camera could not be saved',str(error))

    def create_deployment(self):
        if not self.database:
            return
        label,ok=QInputDialog.getText(self,'Deployment','Site label')
        if ok:
            try:
                add_deployment(self.database,self.camera.currentData(),label)
                self.load_deployments()
            except (ValueError,sqlite3.Error) as error:
                QMessageBox.warning(self,'Deployment could not be saved',str(error))

    def image_id(self):
        row=self.images_panel.table.currentRow()
        from PySide6.QtCore import Qt
        return self.images_panel.table.item(row,0).data(Qt.ItemDataRole.UserRole) if row>=0 else None

    def assign(self):
        image_id=self.image_id()
        if image_id and self.database:
            try:
                assign_camera(self.database,image_id,self.camera.currentData(),self.deployment.currentData())
                QMessageBox.information(self,'Assignment saved','The physical camera/deployment assignment has been recorded.')
            except (ValueError,sqlite3.Error) as error:
                QMessageBox.warning(self,'Assignment could not be saved',str(error))

    def profile(self):
        image_id=self.image_id()
        if image_id and self.database:
            profile_id,ok=QInputDialog.getItem(self,'OCR profile','Profile for the selected image',list(PROFILES),0,False)
            if ok:
                try:
                    assign_profile(self.database,image_id,profile_id)
                    self.ocr_panel.set_scope([image_id])
                    QMessageBox.information(self,'Profile assigned','The OCR tab now targets the selected image for reprocessing.')
                except (ValueError,sqlite3.Error) as error:
                    QMessageBox.warning(self,'Profile could not be assigned',str(error))
