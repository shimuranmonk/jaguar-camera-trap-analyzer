"""Streaming, checkpointed imports. Source files are opened read-only."""
import hashlib
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PySide6.QtGui import QImageReader

from jaguar.db.projects import open_project

SUPPORTED = {".jpg", ".jpeg", ".jfif", ".png"}


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def import_lock(database):
    # OS locking releases automatically after forced process termination.
    with open(str(database) + ".import.lock", "a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise ValueError("Another import is already using this project.") from error
        else:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise ValueError("Another import is already using this project.") from error
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def normalize_roots(roots):
    result = sorted({str(Path(root).resolve()) for root in roots})
    if not result:
        raise ValueError("At least one source folder is required.")
    for root in result:
        if not Path(root).is_dir():
            raise ValueError("A source folder is missing or unavailable.")
    return result


def fingerprint(path, stopped):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            if stopped():
                raise InterruptedError("Import paused.")
            digest.update(chunk)
    return digest.hexdigest()


def inspect_image(path):
    reader = QImageReader(str(path))
    reader.setDecideFormatFromContent(True)
    kind = bytes(reader.format()).decode("ascii").lower()
    image = reader.read()
    if image.isNull():
        raise ValueError("Unsupported or corrupt image.")
    if kind not in {"png", "jpeg", "jpg"}:
        raise ValueError("Image content is not JPEG or PNG.")
    return image.width(), image.height(), "image/png" if kind == "png" else "image/jpeg"


def summary(connection, run_id):
    counts = dict(connection.execute("SELECT status,count(*) FROM import_items WHERE run_id=? GROUP BY status", (run_id,)))
    logical = connection.execute("SELECT count(DISTINCT image_id) FROM import_items WHERE run_id=? AND status='succeeded'", (run_id,)).fetchone()[0]
    run = connection.execute("SELECT status,scan_errors,last_error FROM import_runs WHERE id=?", (run_id,)).fetchone()
    return {"run_id": run_id, "status": run[0], "discovered": sum(counts.values()),
            "imported": counts.get("succeeded", 0), "failed": counts.get("failed", 0),
            "pending": counts.get("pending", 0) + counts.get("running", 0),
            "unique_images": logical, "duplicates": counts.get("succeeded", 0) - logical,
            "scan_errors": run[1], "last_error": run[2]}


def latest_summary(database):
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=rw", uri=True)) as connection:
        row = connection.execute("SELECT id FROM import_runs ORDER BY rowid DESC LIMIT 1").fetchone()
        return summary(connection, row[0]) if row else None


def latest_errors(database):
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=rw", uri=True)) as connection:
        return connection.execute("""SELECT absolute_path,error FROM import_items WHERE run_id=
            (SELECT id FROM import_runs ORDER BY rowid DESC LIMIT 1) AND status='failed' LIMIT 10""").fetchall()


def import_folders(database, roots, stopped=lambda: False, progress=lambda value: None, resume=True):
    database = Path(database).resolve()
    roots = normalize_roots(roots)
    with import_lock(database):
        project = open_project(database)
        with closing(sqlite3.connect(database, timeout=10)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            roots_json = json.dumps(roots)
            row = connection.execute("SELECT id FROM import_runs WHERE roots_json=? AND status IN ('running','paused') ORDER BY rowid DESC LIMIT 1", (roots_json,)).fetchone() if resume else None
            run_id = row[0] if row else str(uuid4())
            with connection:
                if row:
                    connection.execute("UPDATE import_runs SET status='running', ended_at=NULL, discovery_complete=0, scan_errors=0, last_error=NULL WHERE id=?", (run_id,))
                    connection.execute("UPDATE import_items SET status='pending' WHERE run_id=? AND status IN ('running','failed')", (run_id,))
                    connection.execute("UPDATE import_items SET seen=0 WHERE run_id=?", (run_id,))
                else:
                    connection.execute("INSERT INTO import_runs(id,project_id,roots_json,status,started_at) VALUES (?,?,?,'running',?)", (run_id, project.id, roots_json, now()))
            def publish():
                progress(summary(connection, run_id))

            def scan_error(error):
                with connection:
                    connection.execute("UPDATE import_runs SET scan_errors=scan_errors+1,last_error=? WHERE id=?", ("A source directory could not be scanned.", run_id))

            try:
                for root in roots:
                    with connection:
                        connection.execute("INSERT OR IGNORE INTO source_roots VALUES (?,?,?,?)", (str(uuid4()), project.id, root, now()))
                    root_id = connection.execute("SELECT id FROM source_roots WHERE project_id=? AND folder_path=?", (project.id, root)).fetchone()[0]
                    for folder, directories, filenames in os.walk(root, followlinks=False, onerror=scan_error):
                        directories[:] = [name for name in directories if not (Path(folder) / name).is_symlink() and not (Path(folder) / name).is_junction()]
                        for filename in filenames:
                            if stopped():
                                raise InterruptedError()
                            path = Path(folder) / filename
                            if path.suffix.lower() not in SUPPORTED or path.is_symlink():
                                continue
                            absolute = str(path)
                            try:
                                stat = path.stat()
                                size, mtime = stat.st_size, stat.st_mtime_ns
                            except OSError:
                                size, mtime = None, None
                            # Completed checkpoints are reused only when size and mtime match.
                            with connection:
                                connection.execute("""INSERT INTO import_items(run_id,absolute_path,source_root_id,relative_path,file_size,mtime_ns,status)
                                    VALUES (?,?,?,?,?,?,'pending') ON CONFLICT(run_id,absolute_path) DO UPDATE SET
                                    status=CASE WHEN import_items.status='succeeded' AND import_items.file_size=excluded.file_size AND import_items.mtime_ns=excluded.mtime_ns THEN 'succeeded' ELSE 'pending' END,
                                    file_size=excluded.file_size,mtime_ns=excluded.mtime_ns,seen=1""",
                                    (run_id, absolute, root_id, str(path.relative_to(root)), size, mtime))
                        publish()
                with connection:
                    connection.execute("UPDATE import_runs SET discovery_complete=1 WHERE id=?", (run_id,))
                    connection.execute("UPDATE import_items SET status='failed',error='File was not found during the resumed scan.' WHERE run_id=? AND seen=0", (run_id,))
                while True:
                    if stopped():
                        raise InterruptedError()
                    item = connection.execute("SELECT absolute_path,source_root_id,relative_path FROM import_items WHERE run_id=? AND status='pending' LIMIT 1", (run_id,)).fetchone()
                    if not item:
                        break
                    absolute, root_id, relative = item
                    path = Path(absolute)
                    with connection:
                        connection.execute("UPDATE import_items SET status='running',attempt=attempt+1,error=NULL WHERE run_id=? AND absolute_path=?", (run_id, absolute))
                    try:
                        if path.is_symlink():
                            raise ValueError("Symbolic link skipped.")
                        before = path.stat()
                        sha = fingerprint(path, stopped)
                        width, height, media = inspect_image(path)
                        after = path.stat()
                        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                            raise ValueError("File changed during import; retry required.")
                        with connection:
                            connection.execute("INSERT OR IGNORE INTO images VALUES (?,?,?,?,?,?,?)", (str(uuid4()), project.id, sha, width, height, media, now()))
                            image_id = connection.execute("SELECT id FROM images WHERE project_id=? AND sha256=?", (project.id, sha)).fetchone()[0]
                            connection.execute("UPDATE image_paths SET is_available=0 WHERE absolute_path=? AND image_id<>?", (absolute, image_id))
                            connection.execute("""INSERT INTO image_paths VALUES (?,?,?,?,?,?,?,1) ON CONFLICT(image_id,absolute_path) DO UPDATE SET file_size=excluded.file_size,mtime_ns=excluded.mtime_ns,is_available=1""", (str(uuid4()), image_id, root_id, absolute, relative, after.st_size, after.st_mtime_ns))
                            connection.execute("UPDATE import_items SET status='succeeded',image_id=?,file_size=?,mtime_ns=?,error=NULL WHERE run_id=? AND absolute_path=?", (image_id, after.st_size, after.st_mtime_ns, run_id, absolute))
                    except InterruptedError:
                        raise
                    except (OSError, ValueError) as error:
                        message = str(error) if isinstance(error, ValueError) else "File is missing or unreadable."
                        with connection:
                            connection.execute("UPDATE import_items SET status='failed',error=? WHERE run_id=? AND absolute_path=?", (message, run_id, absolute))
                            connection.execute("UPDATE image_paths SET is_available=0 WHERE absolute_path=?", (absolute,))
                    publish()
                with connection:
                    # Missing paths are marked unavailable only after a clean directory scan.
                    if connection.execute("SELECT scan_errors FROM import_runs WHERE id=?", (run_id,)).fetchone()[0] == 0:
                        connection.execute("""UPDATE image_paths SET is_available=0 WHERE source_root_id IN
                            (SELECT id FROM source_roots WHERE folder_path IN (SELECT value FROM json_each(?)))
                            AND absolute_path NOT IN (SELECT absolute_path FROM import_items WHERE run_id=? AND seen=1)""", (roots_json, run_id))
                    connection.execute("UPDATE import_runs SET status='completed',ended_at=? WHERE id=?", (now(), run_id))
            except InterruptedError:
                with connection:
                    connection.execute("UPDATE import_items SET status='pending' WHERE run_id=? AND status='running'", (run_id,))
                    connection.execute("UPDATE import_runs SET status='paused',ended_at=? WHERE id=?", (now(), run_id))
            except Exception:
                with connection:
                    connection.execute("UPDATE import_runs SET status='failed',ended_at=?,last_error='Import could not finish.' WHERE id=?", (now(), run_id))
                raise
            publish()
            return summary(connection, run_id)
