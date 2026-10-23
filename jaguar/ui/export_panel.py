import multiprocessing
import queue
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog,QLabel,QLineEdit,QPushButton,QVBoxLayout,QWidget


def export_worker(database,path,scope,messages):
    try:
        from jaguar.export.workbook import export_workbook
        result=export_workbook(database,path,scope)
        messages.put({'result':result},timeout=2)
    except Exception as error:
        messages.put({'error':str(error)},timeout=2)


class ExportPanel(QWidget):
    def __init__(self,scope_getter):
        super().__init__()
        self.database=None
        self.process=None
        self.messages=None
        self.result_received=False
        self.scope_getter=scope_getter
        layout=QVBoxLayout(self)
        note=QLabel('Excel export uses the current dashboard camera/date/run image scope. Source sheets retain processing history for those images; Runs describes project history. An unfiltered export also includes failed imports without image identities. Original photographs are not embedded.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.path=QLineEdit()
        self.path.setPlaceholderText('Output workbook.xlsx')
        layout.addWidget(self.path)
        browse=QPushButton('Choose new workbook location')
        browse.clicked.connect(self.browse)
        layout.addWidget(browse)
        self.start=QPushButton('Export Excel workbook')
        self.start.clicked.connect(self.begin)
        layout.addWidget(self.start)
        self.status=QLabel('Export is ready after a project opens.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        self.timer=QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.poll)

    def set_project(self,path):
        self.database=path

    def active(self):
        return self.process is not None

    def browse(self):
        path,_=QFileDialog.getSaveFileName(self,'New Excel workbook','','Excel workbook (*.xlsx)')
        if path:
            self.path.setText(path)

    def begin(self):
        if self.active() or not self.database:
            return
        if not self.path.text().strip():
            self.status.setText('An output workbook path is required.')
            return
        context=multiprocessing.get_context('spawn')
        self.result_received=False
        self.messages=context.Queue(maxsize=2)
        self.process=context.Process(target=export_worker,args=(str(self.database),self.path.text().strip(),self.scope_getter(),self.messages))
        try:
            self.process.start()
        except (OSError,RuntimeError) as error:
            self.process=None
            self.status.setText(str(error))
            return
        self.start.setEnabled(False)
        self.status.setText('Writing workbook from a consistent project snapshot…')
        self.timer.start()

    def poll(self):
        self.receive()
        if self.process is not None and not self.process.is_alive():
            exitcode=self.process.exitcode
            self.process.join(0)
            self.receive()
            self.process.close()
            self.process=None
            self.messages.close()
            self.start.setEnabled(True)
            self.timer.stop()
            if not self.result_received:
                self.status.setText('Export worker ended without a completion result. Existing output requires checking.' if exitcode==0 else 'Export worker failed before completion.')

    def receive(self):
        try:
            message=self.messages.get_nowait()
            self.result_received=True
            if 'error' in message:
                self.status.setText('Export failed: '+message['error'])
            else:
                value=message['result']
                self.status.setText(f"Exported {value['images']} images to {value['path']}")
        except queue.Empty:
            pass
