import queue

from jaguar.ingest.service import import_folders


def run_import(database, roots, stop, messages):
    def publish(value):
        try:
            messages.put_nowait(value)
        except queue.Full:
            pass
    try:
        result = import_folders(database, roots, stop.is_set, publish)
        messages.put({"final": result}, timeout=2)
    except Exception:
        try:
            messages.put({"error": "Import could not start or finish. Source folders and project access require checking."}, timeout=2)
        except queue.Full:
            pass


def run_exif(database, executable, stop, messages):
    from jaguar.core.exif import extract_project
    def publish(value):
        try:
            messages.put_nowait(value)
        except queue.Full:
            pass
    try:
        result = extract_project(database, executable, stop.is_set, publish)
        messages.put({"final": result}, timeout=2)
    except Exception as error:
        try:
            from jaguar.adapters.exiftool import ExtractionError
            message = str(error) if isinstance(error, ExtractionError) else "EXIF extraction could not finish. Project and source access require checking."
            messages.put({"error": message}, timeout=2)
        except queue.Full:
            pass


def run_processing(database, stage, model_root, stop, messages):
    def publish(value):
        try:
            messages.put_nowait(value)
        except queue.Full:
            pass
    try:
        from jaguar.core.stages import run_stage
        import json, sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(database)) as connection:
            settings = json.loads(connection.execute("SELECT settings_json FROM projects").fetchone()[0])
        if stage == "ocr":
            from jaguar.adapters.paddle_ocr import PaddleEngine
            engine = PaddleEngine(model_root)
        elif stage == "detect":
            from jaguar.adapters.megadetector import Detector
            engine = Detector(model_root, threshold=settings.get("detection_threshold", .2))
        elif stage == "reconcile":
            from jaguar.core.reconciliation import Reconciler
            engine = Reconciler(settings.get("timestamp_tolerance_seconds", 0), settings.get("temperature_tolerance_c", 1))
        else:
            raise ValueError("Unsupported processing stage.")
        result = run_stage(database, stage, {"model_root": model_root, "settings": settings}, engine, stop.is_set, publish)
        messages.put({"final": result}, timeout=2)
    except Exception as error:
        try:
            messages.put({"error": str(error)[:2000]}, timeout=2)
        except queue.Full:
            pass
