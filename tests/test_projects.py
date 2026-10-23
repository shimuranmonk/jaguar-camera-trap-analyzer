import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jaguar.db.projects import create_project, open_project, migrate


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "project.sqlite"

    def test_round_trip_unicode_and_repeat_open(self):
        project = create_project(self.path, "  森林 camera survey  ")
        self.assertEqual(project.name, "森林 camera survey")
        self.assertEqual(open_project(self.path), project)
        self.assertEqual(open_project(self.path), project)

    def test_existing_file_is_not_overwritten(self):
        self.path.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            create_project(self.path, "Survey")
        self.assertEqual(self.path.read_bytes(), b"original")

    def test_missing_file_is_not_created_on_open(self):
        with self.assertRaises(sqlite3.Error):
            open_project(self.path)
        self.assertFalse(self.path.exists())

    def test_invalid_name_does_not_create_file(self):
        with self.assertRaises(ValueError):
            create_project(self.path, " ")
        self.assertFalse(self.path.exists())

    def test_unrelated_database_is_unchanged(self):
        connection = sqlite3.connect(self.path)
        connection.execute("CREATE TABLE other (value TEXT)")
        connection.close()
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            open_project(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_foreign_keys_and_migration_idempotence(self):
        create_project(self.path, "Survey")
        with closing(sqlite3.connect(self.path)) as connection:
            migrate(connection)
            migrate(connection)
            self.assertEqual(connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO source_roots VALUES ('root', 'missing', 'folder', 'today')")

    def test_failed_migration_rolls_back(self):
        create_project(self.path, "Survey")
        with closing(sqlite3.connect(self.path)) as connection:
            with patch("jaguar.db.projects.MIGRATIONS", ((1, ()), (2, ("CREATE TABLE temporary_table (id TEXT)", "INVALID SQL")))):
                with self.assertRaises(sqlite3.Error):
                    migrate(connection)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='temporary_table'").fetchone())
            self.assertEqual(connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0], 1)

    def test_newer_schema_is_rejected(self):
        create_project(self.path, "Survey")
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("INSERT INTO schema_migrations VALUES (999, 'future')")
            connection.commit()
        with self.assertRaises(ValueError):
            open_project(self.path)
