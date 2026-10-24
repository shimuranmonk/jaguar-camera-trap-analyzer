import json
import sqlite3
import unittest
from contextlib import closing
from openpyxl import load_workbook
from pathlib import Path
from jaguar.core.species import label_fields,review_species,assign_species_location
from jaguar.core.stages import run_stage
from jaguar.export.workbook import export_workbook
from jaguar.adapters.speciesnet import SpeciesEngine,SpeciesFailure
import test_workspace

LABEL='id;mammalia;artiodactyla;bovidae;capra;hircus;goat'


class FakeSpecies:
    version='controlled-species'
    def extract_for_source(self,c,image,path):
        targets=[{'detection_id':i,'class':cl,'confidence':conf,'box':[x,y,w,h]} for i,cl,conf,x,y,w,h in c.execute('SELECT id,class,confidence,x,y,width,height FROM detections WHERE image_id=? ORDER BY rowid',(image,))]
        return {'targets':targets,'native':{'model_version':'controlled','unexpected_future_key':{'preserved':True},'detections':[
            {'raw_classifications':{'classes':[LABEL,'unknown'],'scores':[.8,.2]},'processed_classification':{'class':LABEL,'score':.8,'source':'classifier'}} for _ in targets]}}


class SpeciesTests(unittest.TestCase):
    def setUp(self):
        test_workspace.WorkspaceTests.setUp(self)

    def test_multiple_detections_full_raw_candidates_and_separate_reviews_export(self):
        result=run_stage(self.db,'species',{},FakeSpecies())
        self.assertEqual(result['succeeded'],1)
        with closing(sqlite3.connect(self.db)) as c:
            det=c.execute('SELECT id FROM detections LIMIT 1').fetchone()[0]
            record=c.execute('SELECT id FROM processing_records WHERE run_id=?',(result['run_id'],)).fetchone()[0]
        review_species(self.db,self.image_id,det,'goat','Capra hircus','Researcher','Verified image',detection_id=det,source_record_id=record)
        output=Path(self.temp.name)/'species.xlsx'
        export_workbook(self.db,output)
        book=load_workbook(output)
        self.addCleanup(book.close)
        self.assertEqual(book['Species Identifications'].max_row,3)
        self.assertEqual(book['Species Candidates'].max_row,5)
        self.assertEqual(book['Species Review History'].max_row,2)
        raw=list(book['Species Raw'].iter_rows(min_row=2,values_only=True))
        self.assertTrue(json.loads(''.join(r[5] for r in raw))['native']['unexpected_future_key']['preserved'])
        row=dict(zip((c.value for c in book['Analysis Data'][1]),(c.value for c in book['Analysis Data'][2])))
        self.assertEqual(row['species_confirmed_labels'],'goat')
        self.assertEqual(row['species_confirmed_count'],1)
        self.assertTrue(row['species_review_required'])

    def test_manual_without_detection_and_reversible_state(self):
        review_species(self.db,self.image_id,'manual-one','bird',None,'Reviewer','Manual source inspection')
        review_species(self.db,self.image_id,'manual-one',None,None,'Reviewer','Cleared prior confirmation',status='unreviewed')
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM species_reviews').fetchone()[0],2)
            self.assertEqual(c.execute("SELECT count(*) FROM audit_events WHERE action='species_review'").fetchone()[0],2)

    def test_reviews_require_actor_reason_and_matching_source(self):
        for actor,reason in [('', 'reason'),('reviewer','')]:
            with self.assertRaises(ValueError):
                review_species(self.db,self.image_id,'manual','goat',None,actor,reason)
        with self.assertRaises(ValueError):
            review_species(self.db,self.image_id,'other','goat',None,'Reviewer','reason',detection_id='missing')

    def test_crop_adapter_preserves_no_detection_as_review_not_blank(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('DELETE FROM detections')
            engine=SpeciesEngine.__new__(SpeciesEngine)
            engine.country=engine.admin1=''
            engine.version='test'
            engine.assets={}
            result=engine.extract_for_source(c,self.image_id,'unused')
        self.assertEqual(result['outcome'],'no_accepted_detection_requires_review')
        self.assertEqual(result['native']['detections'],[])

    def test_taxonomy_preserves_complete_label(self):
        fields=label_fields(LABEL)
        self.assertEqual(fields['scientific_name'],'capra hircus')
        self.assertEqual(fields['common_name'],'goat')
        self.assertEqual(fields['label'],LABEL)

    def test_country_assignments_are_audited_and_change_resume_identity(self):
        result=run_stage(self.db,'species',{},FakeSpecies(),stopped=lambda:True)
        assign_species_location(self.db,self.image_id,'ARE','Researcher','Dubai deployment')
        resumed=run_stage(self.db,'species',{},FakeSpecies())
        self.assertNotEqual(result['run_id'],resumed['run_id'])
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT country FROM species_locations').fetchone()[0],'ARE')
        assign_species_location(self.db,self.image_id,'SAU','Researcher','Corrected deployment assignment')

    def test_species_resume_keeps_confirmations_and_source_records(self):
        first=run_stage(self.db,'species',{},FakeSpecies(),stopped=lambda:True)
        second=run_stage(self.db,'species',{},FakeSpecies())
        self.assertEqual(first['run_id'],second['run_id'])
        review_species(self.db,self.image_id,'manual','bird',None,'Reviewer','Manual inspection')
        run_stage(self.db,'species',{},FakeSpecies())
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT label FROM species_reviews').fetchone()[0],'bird')

    def test_full_runtime_failure_details_are_retained(self):
        class FailingSpecies(FakeSpecies):
            def extract_for_source(self,c,image,path):
                raise SpeciesFailure({'error':'x'*3000,'unknown_native_field':[1,2,3]})
        result=run_stage(self.db,'species',{},FailingSpecies())
        self.assertEqual(result['failed'],1)
        with closing(sqlite3.connect(self.db)) as c:
            payload,error=c.execute('SELECT payload_json,error FROM processing_records WHERE run_id=?',(result['run_id'],)).fetchone()
        self.assertEqual(len(error),2000)
        self.assertEqual(len(json.loads(payload)['failure']['error']),3000)
        self.assertEqual(json.loads(payload)['failure']['unknown_native_field'],[1,2,3])
