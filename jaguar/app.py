import sys
import argparse
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont,QFontDatabase
from PySide6.QtCore import QTimer

from jaguar.core.config import configure_logging
from jaguar.ui.window import MainWindow


def main() -> int:
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--smoke-test',action='store_true')
    parser.add_argument('--project')
    arguments,qt_args=parser.parse_known_args(sys.argv[1:])
    configure_logging()
    app = QApplication([sys.argv[0],*qt_args])
    import os
    font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/segoeui.ttf'
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Segoe UI", 10))
    app.setApplicationName("JAGUAR")
    window = MainWindow()
    temporary=None
    if arguments.project:
        window.load_project(Path(arguments.project))
    elif arguments.smoke_test:
        from jaguar.db.projects import create_project
        temporary=tempfile.TemporaryDirectory()
        path=Path(temporary.name)/'smoke.sqlite'
        create_project(path,'Packaged launch check')
        window.load_project(path)
    window.show()
    if arguments.smoke_test:
        QTimer.singleShot(300,app.quit)
    result=app.exec()
    if temporary:
        temporary.cleanup()
    return result
