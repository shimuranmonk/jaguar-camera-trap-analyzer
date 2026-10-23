import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from PySide6.QtGui import QImage
from jaguar.db.projects import create_project
from jaguar.ingest.service import import_folders
from jaguar.core.exif import extract_project
from jaguar.core.stages import run_stage
from jaguar.core.reconciliation import Reconciler
from jaguar.core.cameras import add_camera,add_deployment,assign_camera
from jaguar.core.dashboard import dashboard_data
from jaguar.core.settings import read_settings,save_settings
from test_exif import FakeTool
from test_ocr import FakeOcr


class FakeDetector:
    version='controlled-detector'
    def extract(self,path):
        return {'model_version':self.version,'threshold':.2,'detections':[{'class':'animal','confidence':.9,'box':[.1,.1,.2,.2]},{'class':'person','confidence':.8,'box':[.4,.4,.2,.2]}],'outcome':'accepted_detection'}


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.sources=root/'images'
        self.sources.mkdir()
        image=QImage(12,12,QImage.Format.Format_RGB32)
        image.fill(0)
        image.save(str(self.sources/'sample.png'),'PNG')
        self.db=root/'project.sqlite'
        create_project(self.db,'Workspace')
        import_folders(self.db,[self.sources])
        extract_project(self.db,'unused',adapter=FakeTool())
        run_stage(self.db,'ocr',{},FakeOcr())
        run_stage(self.db,'detect',{},FakeDetector())
        run_stage(self.db,'reconcile',{},Reconciler())
        with closing(sqlite3.connect(self.db)) as connection:
            self.image_id=connection.execute('SELECT id FROM images').fetchone()[0]

    def test_dashboard_counts_overlap_without_double_counting_images(self):
        data=dashboard_data(self.db)
        self.assertEqual(data['total'],1)
        self.assertEqual(data['processed'],1)
        self.assertEqual(data['categories'],{'animal':1,'person':1})
        self.assertEqual(data['review_images'],1)
        self.assertEqual(data['coverage']['model'],0)

    def test_date_camera_and_run_filters_use_stored_data(self):
        self.assertEqual(dashboard_data(self.db,start='2021-01-01')['total'],0)
        self.assertEqual(dashboard_data(self.db,start='2020-01-01',end='2020-12-31')['total'],1)
        camera=add_camera(self.db,'Device 1','TEST','Model','SERIAL')
        deployment=add_deployment(self.db,camera,'Site A')
        assign_camera(self.db,self.image_id,camera,deployment)
        self.assertEqual(dashboard_data(self.db,camera=camera)['total'],1)
        self.assertEqual(dashboard_data(self.db,camera='unassigned')['total'],0)
        self.assertEqual(dashboard_data(self.db,run='missing-run')['total'],0)

    def test_deployment_cannot_silently_change_physical_camera(self):
        first=add_camera(self.db,'First')
        second=add_camera(self.db,'Second')
        deployment=add_deployment(self.db,first,'Site A')
        with self.assertRaises(ValueError):
            assign_camera(self.db,self.image_id,second,deployment)

    def test_settings_are_persistent_and_validate_ranges(self):
        settings=read_settings(self.db)
        settings['timestamp_tolerance_seconds']=3
        save_settings(self.db,settings)
        self.assertEqual(read_settings(self.db)['timestamp_tolerance_seconds'],3)
        settings['detection_threshold']=2
        with self.assertRaises(ValueError):
            save_settings(self.db,settings)
