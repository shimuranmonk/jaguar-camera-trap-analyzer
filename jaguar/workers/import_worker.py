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
