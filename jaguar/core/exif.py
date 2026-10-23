import json
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from jaguar.adapters.exiftool import ExifTool, ExtractionError, normalize
from jaguar.db.projects import open_project
from jaguar.ingest.service import fingerprint, import_lock, now


def extract_project(database, executable, stopped=lambda: False, progress=lambda value: None, adapter=None, resume=True):
    database = Path(database).resolve()
    with import_lock(database):
        project = open_project(database)
        tool = adapter or ExifTool(executable)
        version = tool.version()
        run_id = str(uuid4())
        with closing(sqlite3.connect(database, timeout=10)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            existing = connection.execute("SELECT id FROM exif_runs WHERE status IN ('paused','running') AND tool_version=? AND normalizer_version='1' ORDER BY rowid DESC LIMIT 1", (version,)).fetchone() if resume else None
            if existing:
                run_id = existing[0]
            with connection:
                connection.execute("UPDATE exif_runs SET status='interrupted',ended_at=? WHERE status='running' AND id<>?", (now(), run_id))
                if existing:
                    connection.execute("UPDATE exif_runs SET status='running',ended_at=NULL WHERE id=?", (run_id,))
                else:
                    connection.execute("INSERT INTO exif_runs(id,project_id,status,tool_version,normalizer_version,started_at) VALUES (?,?,'running',?,'1',?)", (run_id, project.id, version, now()))
            total = connection.execute("SELECT count(*) FROM images WHERE project_id=?", (project.id,)).fetchone()[0]
            prior = dict(connection.execute("SELECT status,count(*) FROM exif_records WHERE run_id=? GROUP BY status", (run_id,)))
            counts = {"run_id": run_id, "total": total, "succeeded": prior.get("succeeded", 0), "failed": prior.get("failed", 0), "status": "running"}
            last_id = ""
            while True:
                if stopped():
                    counts["status"] = "paused"
                    break
                image = connection.execute("SELECT id,sha256 FROM images WHERE project_id=? AND id>? AND NOT EXISTS (SELECT 1 FROM exif_records WHERE run_id=? AND image_id=images.id) ORDER BY id LIMIT 1", (project.id, last_id, run_id)).fetchone()
                if image is None:
                    counts["status"] = "completed"
                    break
                image_id, expected_hash = image
                last_id = image_id
                paths = connection.execute("SELECT absolute_path FROM image_paths WHERE image_id=? AND is_available=1 ORDER BY absolute_path", (image_id,))
                path = None
                result, normalized, error = None, None, None
                try:
                    for (candidate,) in paths:
                        candidate = Path(candidate)
                        try:
                            if candidate.is_file() and fingerprint(candidate, stopped) == expected_hash:
                                path = candidate
                                break
                        except OSError:
                            continue
                    if path is None:
                        raise ExtractionError("Source files are unavailable or changed; import requires refreshing.")
                    before = path.stat()
                    result = tool.extract(path)
                    if fingerprint(path, stopped) != expected_hash or (before.st_size, before.st_mtime_ns) != (path.stat().st_size, path.stat().st_mtime_ns):
                        raise ExtractionError("Source changed during extraction; results were rejected.")
                    error = result["error"]
                    if not error:
                        normalized = normalize(result["record"])
                except InterruptedError:
                    counts["status"] = "paused"
                    break
                except (OSError, ValueError, UnicodeError) as failure:
                    if isinstance(failure, ExtractionError) and failure.raw_json is not None:
                        result = {"raw_json": failure.raw_json, "stderr": failure.stderr}
                    error = str(failure) if isinstance(failure, ExtractionError) else "Metadata extraction failed; source access or metadata requires review."
                status = "failed" if error else "succeeded"
                with connection:
                    connection.execute("INSERT INTO exif_records VALUES (?,?,?,?,?,?,?,?,?,?)", (str(uuid4()), run_id, image_id, str(path) if path else "", status,
                        result["raw_json"] if result else None, json.dumps(normalized, ensure_ascii=False) if normalized else None,
                        result["stderr"] if result else None, error, now()))
                counts[status] += 1
                progress(dict(counts))
            with connection:
                connection.execute("UPDATE exif_runs SET status=?,ended_at=? WHERE id=?", (counts["status"], now(), run_id))
            progress(dict(counts))
            return counts
