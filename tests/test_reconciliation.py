import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from PySide6.QtGui import QImage
from jaguar.core.exif import extract_project
from jaguar.core.reconciliation import Reconciler, correction, set_review_status
from jaguar.core.stages import run_stage
from jaguar.db.projects import create_project
from jaguar.ingest.service import import_folders
from test_exif import FakeTool
from test_ocr import FakeOcr


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        sources=root/'images'
        sources.mkdir()
        image=QImage(12,12,QImage.Format.Format_RGB32)
        image.fill(0)
        image.save(str(sources/'image.png'),'PNG')
        self.db=root/'project.sqlite'
        create_project(self.db,'Review')
        import_folders(self.db,[sources])
        extract_project(self.db,'unused',adapter=FakeTool())
        run_stage(self.db,'ocr',{},FakeOcr())
        with closing(sqlite3.connect(self.db)) as connection:
            self.image_id=connection.execute('SELECT id FROM images').fetchone()[0]

    def query(self,sql,args=()):
        with closing(sqlite3.connect(self.db)) as connection:
            return connection.execute(sql,args).fetchall()

    def test_conflicts_preserve_observations_and_prefer_documented_exif(self):
        result=run_stage(self.db,'reconcile',{},Reconciler())
        self.assertEqual(result['succeeded'],1)
        value,source,conflict=self.query("SELECT value_json,source,conflict FROM metadata_resolved WHERE field='capture_timestamp'")[0]
        self.assertEqual(json.loads(value)['iso8601'],'2020-02-03T04:05:06')
        self.assertTrue(source.startswith('exif:'))
        self.assertEqual(conflict,1)
        count=self.query('SELECT count(*) FROM metadata_observations')[0][0]
        run_stage(self.db,'reconcile',{},Reconciler())
        self.assertEqual(self.query('SELECT count(*) FROM metadata_observations')[0][0],count)

    def test_correction_revert_and_audit_do_not_destroy_source(self):
        run_stage(self.db,'reconcile',{},Reconciler())
        before=self.query('SELECT raw_json FROM exif_records')
        correction(self.db,self.image_id,'capture_timestamp','2021-01-02T03:04:05','Verified field log')
        run_stage(self.db,'reconcile',{},Reconciler())
        self.assertEqual(self.query("SELECT source FROM metadata_resolved WHERE field='capture_timestamp'"),[('human',)])
        correction(self.db,self.image_id,'capture_timestamp',None,'Correction withdrawn',revert=True)
        run_stage(self.db,'reconcile',{},Reconciler())
        self.assertTrue(self.query("SELECT source FROM metadata_resolved WHERE field='capture_timestamp'")[0][0].startswith('exif:'))
        self.assertEqual(self.query('SELECT raw_json FROM exif_records'),before)
        self.assertEqual(self.query('SELECT count(*) FROM audit_events'),[(2,)])
        self.assertTrue(self.query("SELECT count(*) FROM reviews WHERE reason='metadata_conflict:capture_timestamp' AND status='open'")[0][0])

    def test_bad_values_and_reason_are_rejected(self):
        for field,value,reason in [('latitude',100,'bad'),('width',-1,'bad'),('temperature_c','nan','bad'),('make','value','')]:
            with self.assertRaises(ValueError):
                correction(self.db,self.image_id,field,value,reason)

    def test_review_status_is_audited(self):
        run_stage(self.db,'reconcile',{},Reconciler())
        review=self.query('SELECT id FROM reviews LIMIT 1')[0][0]
        set_review_status(self.db,review,'deferred')
        self.assertEqual(self.query('SELECT status FROM reviews WHERE id=?',(review,)),[('deferred',)])
        self.assertEqual(self.query("SELECT action FROM audit_events"),[('review',)])
