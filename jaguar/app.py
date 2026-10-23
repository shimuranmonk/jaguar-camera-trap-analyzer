import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

from jaguar.core.config import configure_logging
from jaguar.ui.window import MainWindow


def main() -> int:
    configure_logging()
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    app.setApplicationName("JAGUAR")
    window = MainWindow()
    window.show()
    return app.exec()
