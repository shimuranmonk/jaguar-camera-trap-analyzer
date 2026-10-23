import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from jaguar.db.projects import open_project
from jaguar.ingest.service import import_lock, fingerprint, now
from jaguar.ocr.profiles import PROFILES, choose_profile
from jaguar.ocr.parsers import parse_overlay


def assign_profile(database, image_id, profile_id, actor="local operator"):
    profile = PROFILES[profile_id]
    with import_lock(database), closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with connection:
            connection.execute("INSERT INTO profile_assignments VALUES (?,?,?,?,?,?)", (str(uuid4()), image_id, profile.id, profile.version, actor, now()))


def ocr_payload(connection, image_id, path, engine):
    row = connection.execute("SELECT normalized_json FROM exif_records WHERE image_id=? AND status='succeeded' ORDER BY rowid DESC LIMIT 1", (image_id,)).fetchone()
    make = None
    if row:
        make = (json.loads(row[0])["fields"].get("make") or {}).get("value")
    assignment = connection.execute("SELECT profile_id FROM profile_assignments WHERE image_id=? ORDER BY rowid DESC LIMIT 1", (image_id,)).fetchone()
    profile, reason = choose_profile(make, assignment[0] if assignment else None)
    tokens = engine.extract(path, profile)
    attempts = [{"profile": profile.snapshot(), "tokens": tokens}]
    if not tokens and profile.id != "generic-edges":
        profile, reason = choose_profile()
        tokens = engine.extract(path, profile)
        attempts.append({"profile": profile.snapshot(), "tokens": tokens})
    return {"engine_version": engine.version, "profile": profile.snapshot(), "selection_reason": reason,
            "tokens": tokens, "attempts": attempts, "parsed": parse_overlay(tokens, profile)}


def run_stage(database, stage, config, engine, stopped=lambda: False, progress=lambda value: None):
    database = Path(database).resolve()
    with import_lock(database):
        project = open_project(database)
        with closing(sqlite3.connect(database, timeout=10)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            config = dict(config, engine_version=engine.version)
            if stage == "ocr":
                config["profiles"] = [p.snapshot() for p in PROFILES.values()]
                config["assignments"] = connection.execute("SELECT image_id,profile_id,profile_version FROM profile_assignments ORDER BY rowid").fetchall()
            encoded = json.dumps(config, sort_keys=True)
            config_hash = hashlib.sha256(encoded.encode()).hexdigest()
            old = connection.execute("SELECT id FROM processing_runs WHERE stage=? AND config_hash=? AND status IN ('running','paused') ORDER BY rowid DESC LIMIT 1", (stage, config_hash)).fetchone()
            run_id = old[0] if old else str(uuid4())
            with connection:
                if old:
                    connection.execute("UPDATE processing_runs SET status='running',ended_at=NULL WHERE id=?", (run_id,))
                else:
                    connection.execute("INSERT INTO processing_runs VALUES (?,?,?,?,?,'running',?,NULL)", (run_id, project.id, stage, encoded, config_hash, now()))
            ids = config.get("image_ids")
            selected = list(dict.fromkeys(ids)) if ids is not None else None
            scope_sql = " AND id IN (SELECT value FROM json_each(?))" if selected is not None else ""
            scope_args = (json.dumps(selected),) if selected is not None else ()
            total = connection.execute("SELECT count(*) FROM images WHERE project_id=?" + scope_sql, (project.id, *scope_args)).fetchone()[0]
            counts = dict(connection.execute("SELECT status,count(*) FROM processing_records WHERE run_id=? GROUP BY status", (run_id,)))
            report = {"run_id": run_id, "stage": stage, "total": total, "succeeded": counts.get("succeeded", 0), "failed": counts.get("failed", 0), "status": "running"}
            while True:
                if stopped():
                    report["status"] = "paused"
                    break
                image = connection.execute("SELECT id,sha256 FROM images WHERE project_id=?" + scope_sql + " AND NOT EXISTS(SELECT 1 FROM processing_records WHERE run_id=? AND image_id=images.id) ORDER BY id LIMIT 1", (project.id, *scope_args, run_id)).fetchone()
                if image is None:
                    report["status"] = "completed"
                    break
                image_id, expected = image
                payload, error, path = None, None, None
                try:
                    for (candidate,) in connection.execute("SELECT absolute_path FROM image_paths WHERE image_id=? AND is_available=1", (image_id,)):
                        candidate = Path(candidate)
                        if candidate.is_file() and fingerprint(candidate, stopped) == expected:
                            path = candidate
                            break
                    if path is None:
                        raise ValueError("Source is unavailable or changed; a new import is required.")
                    payload = ocr_payload(connection, image_id, path, engine) if stage == "ocr" else engine.extract(path)
                    if fingerprint(path, stopped) != expected:
                        raise ValueError("Source changed during processing; output rejected.")
                except InterruptedError:
                    report["status"] = "paused"
                    break
                except Exception as failure:
                    error = str(failure)[:2000]
                status = "failed" if error else "succeeded"
                with connection:
                    connection.execute("INSERT INTO processing_records VALUES (?,?,?,?,?,?,?)", (str(uuid4()), run_id, image_id, status, json.dumps(payload, ensure_ascii=False) if payload else None, error, now()))
                report[status] += 1
                progress(dict(report))
            with connection:
                connection.execute("UPDATE processing_runs SET status=?,ended_at=? WHERE id=?", (report["status"], now(), run_id))
            progress(dict(report))
            return report
