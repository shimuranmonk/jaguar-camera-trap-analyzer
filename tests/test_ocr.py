import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from PySide6.QtGui import QImage

from jaguar.db.projects import create_project
from jaguar.ingest.service import import_folders
from jaguar.core.stages import run_stage, assign_profile
from jaguar.ocr.profiles import choose_profile, PROFILES
from jaguar.ocr.parsers import parse_overlay


def token(text, confidence=.99):
    return {"text": text, "confidence": confidence, "region": "band", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}


class ParserTests(unittest.TestCase):
    def test_bushnell_retains_both_temperature_values(self):
        parsed = parse_overlay([token("DM_HMCA004 102°F38°C 10-04-2018 15:20:10")], PROFILES["bushnell-band"])
        self.assertEqual(parsed["values"]["capture_timestamp"], "2018-10-04T15:20:10")
        self.assertEqual([(t["value"], t["unit"]) for t in parsed["values"]["temperatures"]], [(102, "F"), (38, "C")])
        self.assertEqual(parsed["values"]["camera_site_label"], "DM_HMCA004")
        self.assertFalse(parsed["review_required"])

    def test_moultrie_example_uses_12_hour_clock(self):
        parsed = parse_overlay([token("MOULTRIE 64 F NPCA 1 15 OCT 2017 04:11 pm")], PROFILES["moultrie-band"])
        self.assertEqual(parsed["values"]["capture_timestamp"], "2017-10-15T16:11:00")
        self.assertEqual(parsed["values"]["camera_site_label"], "NPCA 1")

    def test_missing_invalid_and_low_confidence_are_reviewable(self):
        self.assertTrue(parse_overlay([], PROFILES["generic-edges"])["review_required"])
        parsed = parse_overlay([token("13-99-2019 25:99:01", .5)], PROFILES["bushnell-band"])
        self.assertIsNone(parsed["values"]["capture_timestamp"])
        self.assertTrue(parsed["review_required"])

    def test_profile_does_not_invent_brand_from_weak_text(self):
        parsed = parse_overlay([token("Bushnell", .5)], PROFILES["bushnell-band"])
        self.assertIsNone(parsed["values"]["brand"])

    def test_unknown_brand_uses_generic_and_manual_selection_is_explicit(self):
        self.assertEqual(choose_profile("unknown")[0].id, "generic-edges")
        self.assertEqual(choose_profile("unknown", "bushnell-band")[1], "manual")


class FakeOcr:
    version = "fake-ocr-v1"
    def extract(self, path, profile):
        return [token("DM_HMCA004 77 F25 C 03-29-2019 17:46:58")]


class StageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "source"
        self.root.mkdir()
        image = QImage(12, 12, QImage.Format.Format_RGB32)
        image.fill(0)
        image.save(str(self.root / "image.png"), "PNG")
        self.db = self.base / "project.sqlite"
        create_project(self.db, "OCR")
        import_folders(self.db, [self.root])

    def test_durable_tokens_parsed_fields_and_model_profile_provenance(self):
        result = run_stage(self.db, "ocr", {}, FakeOcr())
        self.assertEqual(result["succeeded"], 1)
        with closing(sqlite3.connect(self.db)) as connection:
            payload = json.loads(connection.execute("SELECT payload_json FROM processing_records").fetchone()[0])
        self.assertEqual(payload["tokens"][0]["confidence"], .99)
        self.assertEqual(payload["selection_reason"], "generic_fallback")
        self.assertEqual(payload["parsed"]["values"]["date"], "2019-03-29")

    def test_assignment_and_targeted_scope_preserve_prior_results(self):
        first = run_stage(self.db, "ocr", {}, FakeOcr())
        with closing(sqlite3.connect(self.db)) as connection:
            image_id = connection.execute("SELECT id FROM images").fetchone()[0]
        assign_profile(self.db, image_id, "bushnell-band")
        second = run_stage(self.db, "ocr", {"image_ids": [image_id]}, FakeOcr())
        self.assertNotEqual(first["run_id"], second["run_id"])
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM processing_records").fetchone()[0], 2)
            payload = json.loads(connection.execute("SELECT payload_json FROM processing_records ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        self.assertEqual(payload["selection_reason"], "manual")

    def test_pause_and_restart_reuse_committed_results(self):
        paused = [False]
        first = run_stage(self.db, "ocr", {}, FakeOcr(), lambda: paused[0], lambda value: paused.__setitem__(0, True))
        self.assertEqual(first["status"], "paused")
        second = run_stage(self.db, "ocr", {}, FakeOcr())
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(second["succeeded"], 1)
