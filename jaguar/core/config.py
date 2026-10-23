import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging() -> Path:
    data_root=Path(os.environ['JAGUAR_DATA_DIR']) if os.environ.get('JAGUAR_DATA_DIR') else Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "JAGUAR"
    folder=data_root/'logs'
    folder.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("jaguar")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(folder / "application.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return folder
