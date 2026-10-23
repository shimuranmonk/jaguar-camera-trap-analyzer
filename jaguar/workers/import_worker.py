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
