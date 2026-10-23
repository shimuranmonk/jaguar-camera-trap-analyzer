import hashlib
import multiprocessing
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PySide6.QtGui import QImage, QColor

from jaguar.db.projects import MIGRATIONS, create_project, open_project
from jaguar.ingest.service import import_folders, import_lock, latest_summary


def crashable_import(database, root, channel):
    def committed(value):
        if value["imported"] == 1:
            channel.send(True)
            # Parent terminates this process after a committed checkpoint.
            channel.recv()
    import_folders(database, [root], progress=committed)


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "相机 survey"
        self.root.mkdir()
        self.database = self.base / "project.sqlite"
        create_project(self.database, "Survey")

    def image(self, name, color="red"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        image = QImage(16, 12, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        self.assertTrue(image.save(str(path), "PNG" if path.suffix.lower() == ".png" else "JPEG"))
        return path

    def query(self, sql):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute(sql).fetchall()

    def test_mixed_nested_formats_duplicates_and_source_integrity(self):
        first = self.image("nested/camera.JPG")
        shutil.copyfile(first, self.root / "copy.jfif")
        self.image("another.jpeg", "blue")
        self.image("nested/deep/image.PNG", "green")
        (self.root / "ignore.txt").write_text("ignored")
        paths = list(self.root.rglob("*"))
        before = {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in paths if p.is_file()}
        result = import_folders(self.database, [self.root])
        self.assertEqual((result["imported"], result["unique_images"], result["duplicates"]), (4, 3, 1))
        self.assertEqual(len(self.query("SELECT * FROM image_paths")), 4)
        for name, evidence in before.items():
            path = Path(name)
            self.assertEqual((hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns), evidence)

    def test_repeat_import_is_idempotent_and_keeps_runs(self):
        self.image("a.png")
        first = import_folders(self.database, [self.root])
        second = import_folders(self.database, [self.root])
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(len(self.query("SELECT * FROM images")), 1)
        self.assertEqual(len(self.query("SELECT * FROM image_paths")), 1)
        self.assertEqual(len(self.query("SELECT * FROM import_runs")), 2)

    def test_corrupt_file_is_reported_and_batch_continues(self):
        (self.root / "bad.jpg").write_bytes(b"not an image")
        self.image("valid.png")
        result = import_folders(self.database, [self.root])
        self.assertEqual((result["failed"], result["imported"], result["pending"]), (1, 1, 0))
        self.assertIn("corrupt", self.query("SELECT error FROM import_items WHERE status='failed'")[0][0])

    def test_pause_resume_does_not_repeat_committed_hash(self):
        for number in range(3):
            self.image(f"{number}.png", ["red", "green", "blue"][number])
        stopped = [False]
        def progress(value):
            if value["imported"] == 1:
                stopped[0] = True
        first = import_folders(self.database, [self.root], lambda: stopped[0], progress)
        self.assertEqual(first["status"], "paused")
        second = import_folders(self.database, [self.root])
        self.assertEqual(second["run_id"], first["run_id"])
        self.assertEqual(second["imported"], 3)
        self.assertEqual(self.query("SELECT sum(attempt) FROM import_items")[0][0], 3)

    def test_force_termination_releases_lock_and_resumes(self):
        for number in range(3):
            self.image(f"{number}.png")
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=crashable_import, args=(str(self.database), str(self.root), child))
        process.start()
        try:
            self.assertTrue(parent.poll(20), "Worker did not reach its first checkpoint")
            self.assertTrue(parent.recv())
        finally:
            process.terminate()
            process.join(10)
            parent.close()
            child.close()
        self.assertFalse(process.is_alive())
        process.close()
        previous = latest_summary(self.database)
        result = import_folders(self.database, [self.root])
        self.assertEqual(result["run_id"], previous["run_id"])
        self.assertEqual((result["status"], result["imported"]), ("completed", 3))
        self.assertEqual(len(self.query("SELECT * FROM image_paths")), 3)

    def test_changed_path_keeps_prior_image_identity(self):
        path = self.image("a.png")
        import_folders(self.database, [self.root])
        self.image("a.png", "blue")
        import_folders(self.database, [self.root])
        self.assertEqual(len(self.query("SELECT * FROM images")), 2)
        self.assertEqual(self.query("SELECT sum(is_available) FROM image_paths")[0][0], 1)
        self.assertTrue(path.exists())

    def test_missing_path_is_marked_unavailable(self):
        path = self.image("a.png")
        import_folders(self.database, [self.root])
        path.unlink()
        import_folders(self.database, [self.root])
        self.assertEqual(self.query("SELECT is_available FROM image_paths"), [(0,)])

    def test_overlapping_roots_do_not_duplicate_paths(self):
        self.image("nested/a.png")
        result = import_folders(self.database, [self.root, self.root / "nested"])
        self.assertEqual(result["discovered"], 1)
        self.assertEqual(len(self.query("SELECT * FROM image_paths")), 1)

    def test_unavailable_root_does_not_create_run(self):
        with self.assertRaises(ValueError):
            import_folders(self.database, [self.root / "missing"])
        self.assertEqual(self.query("SELECT count(*) FROM import_runs"), [(0,)])

    def test_second_writer_is_rejected(self):
        with import_lock(self.database):
            with self.assertRaisesRegex(ValueError, "Another import"):
                import_folders(self.database, [self.root])

    def test_gate1_project_upgrades_without_changing_identity(self):
        legacy = self.base / "legacy.sqlite"
        with patch("jaguar.db.projects.MIGRATIONS", MIGRATIONS[:1]):
            project = create_project(legacy, "Legacy")
        self.assertEqual(open_project(legacy), project)
        self.assertEqual(import_folders(legacy, [self.root])["status"], "completed")

    def test_change_during_hash_is_rejected(self):
        path = self.image("a.png")
        from jaguar.ingest.service import fingerprint
        def changing(candidate, stopped):
            result = fingerprint(candidate, stopped)
            candidate.write_bytes(candidate.read_bytes() + b"changed")
            return result
        with patch("jaguar.ingest.service.fingerprint", changing):
            result = import_folders(self.database, [self.root])
        self.assertEqual(result["failed"], 1)
        self.assertEqual(self.query("SELECT count(*) FROM images"), [(0,)])

    def test_pause_during_hash_retries_running_task(self):
        self.image("a.png")
        with patch("jaguar.ingest.service.fingerprint", side_effect=InterruptedError):
            result = import_folders(self.database, [self.root])
        self.assertEqual((result["status"], result["pending"]), ("paused", 1))
        result = import_folders(self.database, [self.root])
        self.assertEqual(result["imported"], 1)

    def test_scan_error_does_not_mark_known_paths_missing(self):
        self.image("a.png")
        import_folders(self.database, [self.root])
        def unavailable(root, followlinks, onerror):
            onerror(PermissionError("unavailable"))
            return iter(())
        with patch("jaguar.ingest.service.os.walk", unavailable):
            result = import_folders(self.database, [self.root])
        self.assertEqual(result["scan_errors"], 1)
        self.assertEqual(self.query("SELECT is_available FROM image_paths"), [(1,)])

    def test_removed_file_during_pause_is_not_counted_as_success(self):
        for number in range(2):
            self.image(f"{number}.png")
        stopped = [False]
        def progress(value):
            if value["imported"] == 1:
                stopped[0] = True
        import_folders(self.database, [self.root], lambda: stopped[0], progress)
        imported = self.query("SELECT absolute_path FROM import_items WHERE status='succeeded'")[0][0]
        Path(imported).unlink()
        result = import_folders(self.database, [self.root])
        self.assertEqual((result["imported"], result["failed"]), (1, 1))
        self.assertEqual(self.query("SELECT is_available FROM image_paths WHERE absolute_path='" + imported.replace("'", "''") + "'"), [(0,)])
