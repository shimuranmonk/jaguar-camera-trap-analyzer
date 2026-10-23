import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from jaguar.core.models import Project

APPLICATION_ID = 0x4A414755
MIGRATIONS = ((1, (
    "CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL CHECK(length(trim(name)) > 0), created_at TEXT NOT NULL, settings_json TEXT NOT NULL)",
    "CREATE TABLE source_roots (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), folder_path TEXT NOT NULL, added_at TEXT NOT NULL, UNIQUE(project_id, folder_path))",
)), (2, (
    "CREATE TABLE images (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), sha256 TEXT NOT NULL CHECK(length(sha256)=64), width INTEGER NOT NULL, height INTEGER NOT NULL, media_type TEXT NOT NULL, first_seen_at TEXT NOT NULL, UNIQUE(project_id, sha256))",
    "CREATE TABLE image_paths (id TEXT PRIMARY KEY, image_id TEXT NOT NULL REFERENCES images(id), source_root_id TEXT NOT NULL REFERENCES source_roots(id), absolute_path TEXT NOT NULL, relative_path TEXT NOT NULL, file_size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, is_available INTEGER NOT NULL CHECK(is_available IN (0,1)), UNIQUE(image_id, absolute_path))",
    "CREATE INDEX image_paths_absolute ON image_paths(absolute_path)",
    "CREATE TABLE import_runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), roots_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('running','paused','completed','failed')), started_at TEXT NOT NULL, ended_at TEXT, discovery_complete INTEGER NOT NULL DEFAULT 0, scan_errors INTEGER NOT NULL DEFAULT 0, last_error TEXT)",
    "CREATE TABLE import_items (run_id TEXT NOT NULL REFERENCES import_runs(id), absolute_path TEXT NOT NULL, source_root_id TEXT NOT NULL REFERENCES source_roots(id), relative_path TEXT NOT NULL, file_size INTEGER, mtime_ns INTEGER, status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','failed')), attempt INTEGER NOT NULL DEFAULT 0, image_id TEXT REFERENCES images(id), error TEXT, seen INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(run_id,absolute_path))",
    "CREATE INDEX import_items_queue ON import_items(run_id,status)",
)), (3, (
    "CREATE TABLE exif_runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), status TEXT NOT NULL, tool_version TEXT NOT NULL, normalizer_version TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT)",
    "CREATE TABLE exif_records (id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES exif_runs(id), image_id TEXT NOT NULL REFERENCES images(id), source_path TEXT NOT NULL, status TEXT NOT NULL, raw_json TEXT, normalized_json TEXT, stderr TEXT, error TEXT, extracted_at TEXT NOT NULL, UNIQUE(run_id,image_id))",
    "CREATE INDEX exif_records_image ON exif_records(image_id,extracted_at)",
)), (4, (
    "CREATE TABLE profile_assignments (id TEXT PRIMARY KEY, image_id TEXT NOT NULL REFERENCES images(id), profile_id TEXT NOT NULL, profile_version INTEGER NOT NULL, actor TEXT NOT NULL, assigned_at TEXT NOT NULL)",
    "CREATE TABLE processing_runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), stage TEXT NOT NULL, config_json TEXT NOT NULL, config_hash TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT)",
    "CREATE TABLE processing_records (id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES processing_runs(id), image_id TEXT NOT NULL REFERENCES images(id), status TEXT NOT NULL, payload_json TEXT, error TEXT, processed_at TEXT NOT NULL, UNIQUE(run_id,image_id))",
    "CREATE INDEX processing_runs_stage ON processing_runs(stage,config_hash,status)",
    "CREATE INDEX processing_records_image ON processing_records(image_id,run_id)",
)), (5, (
    "CREATE TABLE detections (id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES processing_records(id), image_id TEXT NOT NULL REFERENCES images(id), run_id TEXT NOT NULL REFERENCES processing_runs(id), class TEXT NOT NULL CHECK(class IN ('animal','person','vehicle')), confidence REAL NOT NULL CHECK(confidence>=0 AND confidence<=1), x REAL NOT NULL, y REAL NOT NULL, width REAL NOT NULL, height REAL NOT NULL, model_version TEXT NOT NULL, threshold REAL NOT NULL)",
    "CREATE INDEX detections_image_class ON detections(image_id,class)",
)), (6, (
    "CREATE TABLE metadata_observations (id TEXT PRIMARY KEY, image_id TEXT NOT NULL REFERENCES images(id), field TEXT NOT NULL, source TEXT NOT NULL, value_json TEXT, raw_json TEXT, confidence REAL, source_record_id TEXT NOT NULL, observed_at TEXT NOT NULL)",
    "CREATE TABLE metadata_resolved (image_id TEXT NOT NULL REFERENCES images(id), field TEXT NOT NULL, value_json TEXT, source TEXT, provenance_json TEXT, conflict INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(image_id,field))",
    "CREATE TABLE audit_events (id TEXT PRIMARY KEY, image_id TEXT NOT NULL REFERENCES images(id), field TEXT NOT NULL, old_json TEXT, new_json TEXT, actor TEXT NOT NULL, reason TEXT NOT NULL, action TEXT NOT NULL, at TEXT NOT NULL)",
    "CREATE TABLE human_overrides (image_id TEXT NOT NULL REFERENCES images(id), field TEXT NOT NULL, value_json TEXT, event_id TEXT NOT NULL REFERENCES audit_events(id), active INTEGER NOT NULL, PRIMARY KEY(image_id,field))",
    "CREATE TABLE reviews (id TEXT PRIMARY KEY, image_id TEXT NOT NULL REFERENCES images(id), reason TEXT NOT NULL, fingerprint TEXT NOT NULL, detail_json TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT, UNIQUE(image_id,fingerprint))",
    "CREATE INDEX reviews_status ON reviews(status,image_id)",
    "CREATE TABLE cameras (id TEXT PRIMARY KEY, make TEXT, model TEXT, serial TEXT, label TEXT NOT NULL, created_at TEXT NOT NULL)",
    "CREATE TABLE deployments (id TEXT PRIMARY KEY, camera_id TEXT REFERENCES cameras(id), site_label TEXT NOT NULL, start_at TEXT, end_at TEXT, notes TEXT)",
    "CREATE TABLE image_assignments (id TEXT PRIMARY KEY, image_id TEXT NOT NULL REFERENCES images(id), camera_id TEXT REFERENCES cameras(id), deployment_id TEXT REFERENCES deployments(id), actor TEXT NOT NULL, assigned_at TEXT NOT NULL)",
)),)


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
        raise ValueError("The selected file is not a JAGUAR project.")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
        known = {version for version, _ in MIGRATIONS}
        if not applied <= known:
            raise ValueError("This project requires a newer JAGUAR version.")
        for version, statements in MIGRATIONS:
            if version in applied:
                continue
            for statement in statements:
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, datetime.now(timezone.utc).isoformat()))


def create_project(path: Path, name: str) -> Project:
    name = name.strip()
    if not name:
        raise ValueError("A project name is required.")
    path = Path(path)
    # Exclusive creation prevents accidental replacement of an existing project.
    with path.open("xb"):
        pass
    project = Project(str(uuid4()), name, datetime.now(timezone.utc).isoformat())
    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            migrate(connection)
            with connection:
                connection.execute("INSERT INTO projects VALUES (?, ?, ?, ?)", (project.id, project.name, project.created_at, json.dumps({})))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return project


def open_project(path: Path) -> Project:
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)) as connection:
        # Validate before applying migrations to an existing file.
        if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise ValueError("The selected file is not a JAGUAR project.")
        rows = connection.execute("SELECT id, name, created_at FROM projects").fetchall()
        if len(rows) != 1:
            raise ValueError("A project file must contain exactly one project.")
        migrate(connection)
        return Project(*rows[0])
