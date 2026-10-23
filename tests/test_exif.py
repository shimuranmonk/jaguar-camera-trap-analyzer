import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PySide6.QtGui import QImage

from jaguar.adapters.exiftool import ExifTool, ExtractionError, normalize
from jaguar.core.exif import extract_project
from jaguar.db.projects import create_project
from jaguar.ingest.service import import_folders


class FakeTool:
    def version(self):
        return "13.59"

    def extract(self, path):
        record = {"IFD0:Make": "TEST", "ExifIFD:DateTimeOriginal": "2020:02:03 04:05:06", "Unknown:Tag": "retained"}
        return {"raw_json": json.dumps([record]), "record": record, "stderr": "", "error": None}


class NormalizationTests(unittest.TestCase):
    def test_missing_values_stay_null(self):
        result = normalize({"System:FileModifyDate": "2026:01:01 00:00:00"})
        self.assertTrue(all(value is None for value in result["fields"].values()))

    def test_original_timestamp_and_timezone_ambiguity(self):
        result = normalize({"ExifIFD:DateTimeOriginal": "2019:03:31 15:29:20"})
        self.assertFalse(result["fields"]["capture_timestamp"]["value"]["timezone_known"])
        self.assertIn("timezone", " ".join(result["warnings"]))

    def test_explicit_offset_and_create_date_fallback(self):
        result = normalize({"ExifIFD:DateTimeOriginal": "invalid", "ExifIFD:CreateDate": "2019:03:31 15:29:20", "ExifIFD:OffsetTimeDigitized": "+04:00"})
        self.assertEqual(result["fields"]["capture_timestamp"]["value"]["iso8601"], "2019-03-31T15:29:20+04:00")
        self.assertEqual(len([o for o in result["observations"] if o["field"] == "capture_timestamp"]), 2)

    def test_temperature_units_and_raw_value(self):
        result = normalize({"MakerNotes:Temperature": "68 F"})
        self.assertEqual(result["fields"]["temperature_c"]["value"], 20)
        self.assertEqual(result["observations"][0]["raw_value"], "68 F")
        self.assertIsNone(normalize({"MakerNotes:Temperature": 68})["fields"]["temperature_c"])

    def test_gps_references_and_invalid_values(self):
        result = normalize({"GPS:GPSLatitude": 24, "GPS:GPSLatitudeRef": "S", "GPS:GPSLongitude": 56, "GPS:GPSLongitudeRef": "E"})
        self.assertEqual(result["fields"]["latitude"]["value"], -24)
        self.assertEqual(result["fields"]["longitude"]["value"], 56)
        self.assertIsNone(normalize({"GPS:GPSLatitude": 999, "GPS:GPSLatitudeRef": "N"})["fields"]["latitude"])

    def test_camera_identity_is_not_inferred_from_make_model(self):
        result = normalize({"IFD0:Make": "BUSHNELL", "IFD0:Model": "", "SourceFile": "DM_HMCA004.jpg"})
        self.assertIsNone(result["fields"]["model"])
        self.assertIsNone(result["fields"]["physical_camera_id"])


class AdapterTests(unittest.TestCase):
    def test_read_only_command_and_raw_retention(self):
        raw = '[{"Unknown:Tag":"preserved"}]\n'
        with patch("jaguar.adapters.exiftool.subprocess.run", return_value=subprocess.CompletedProcess([], 0, raw.encode(), b"warning")) as run:
            result = ExifTool("tool.exe").extract("image name.jpg")
        args = run.call_args.args[0]
        self.assertEqual(args[1:3], ["-config", ""])
        self.assertIn("--", args)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(result["raw_json"], raw)
        self.assertEqual(result["stderr"], "warning")

    def test_timeout_and_invalid_json_are_actionable(self):
        with patch("jaguar.adapters.exiftool.subprocess.run", side_effect=subprocess.TimeoutExpired("tool", 1)):
            with self.assertRaisesRegex(ExtractionError, "timed out"):
                ExifTool("tool").extract("a.jpg")
        with patch("jaguar.adapters.exiftool.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"not json", b"")):
            with self.assertRaises(ExtractionError) as caught:
                ExifTool("tool").extract("a.jpg")
        self.assertEqual(caught.exception.raw_json, "not json")

    def test_tool_error_record_is_retained(self):
        raw = b'[{"ExifTool:Error":"corrupt"}]'
        with patch("jaguar.adapters.exiftool.subprocess.run", return_value=subprocess.CompletedProcess([], 1, raw, b"")):
            result = ExifTool("tool").extract("a.jpg")
        self.assertIsNotNone(result["error"])
        self.assertEqual(result["raw_json"], raw.decode())


class ExifServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / "images"
        self.source.mkdir()
        self.path = self.source / "a.png"
        image = QImage(12, 8, QImage.Format.Format_RGB32)
        image.fill(0)
        self.assertTrue(image.save(str(self.path), "PNG"))
        self.database = root / "project.sqlite"
        create_project(self.database, "Test")
        import_folders(self.database, [self.source])

    def query(self, sql):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute(sql).fetchall()

    def test_raw_normalized_and_immutable_run_history(self):
        first = extract_project(self.database, "unused", adapter=FakeTool())
        second = extract_project(self.database, "unused", adapter=FakeTool())
        self.assertEqual(first["succeeded"], 1)
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(len(self.query("SELECT * FROM exif_records")), 2)
        raw, normalized = self.query("SELECT raw_json,normalized_json FROM exif_records LIMIT 1")[0]
        self.assertEqual(json.loads(raw)[0]["Unknown:Tag"], "retained")
        self.assertEqual(json.loads(normalized)["fields"]["make"]["source_tag"], "IFD0:Make")

    def test_changed_source_is_not_attached_to_old_hash(self):
        self.path.write_bytes(self.path.read_bytes() + b"changed")
        result = extract_project(self.database, "unused", adapter=FakeTool())
        self.assertEqual(result["failed"], 1)
        self.assertIn("changed", self.query("SELECT error FROM exif_records")[0][0])

    def test_invalid_json_failure_keeps_tool_output(self):
        tool = FakeTool()
        tool.extract = lambda path: (_ for _ in ()).throw(ExtractionError("bad json", "raw output", "warning"))
        result = extract_project(self.database, "unused", adapter=tool)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(self.query("SELECT raw_json,stderr FROM exif_records"), [("raw output", "warning")])

    def test_pause_keeps_completed_records(self):
        result = extract_project(self.database, "unused", stopped=lambda: True, adapter=FakeTool())
        self.assertEqual(result["status"], "paused")
        self.assertEqual(self.query("SELECT count(*) FROM exif_records"), [(0,)])

    def test_resume_preserves_run_and_committed_records(self):
        paused = [False]
        def progress(value):
            if value["succeeded"] == 1:
                paused[0] = True
        first = extract_project(self.database, "unused", stopped=lambda: paused[0], progress=progress, adapter=FakeTool())
        self.assertEqual(first["status"], "paused")
        resumed = extract_project(self.database, "unused", adapter=FakeTool())
        self.assertEqual(first["run_id"], resumed["run_id"])
        self.assertEqual(resumed["succeeded"], 1)
        self.assertEqual(self.query("SELECT count(*) FROM exif_records"), [(1,)])
