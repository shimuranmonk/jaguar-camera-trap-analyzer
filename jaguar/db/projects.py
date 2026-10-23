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
