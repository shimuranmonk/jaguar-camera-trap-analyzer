import json
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from openpyxl import load_workbook
from jaguar.export.workbook import export_workbook
from jaguar.core.reconciliation import correction,Reconciler
from jaguar.core.stages import run_stage
import test_workspace


class ExportTests(unittest.TestCase):
    def setUp(self):
        test_workspace.WorkspaceTests.setUp(self)
        self.output=Path(self.temp.name)/'export.xlsx'

    def test_workbook_grains_ids_numeric_types_and_formula_injection(self):
        correction(self.db,self.image_id,'make','=HYPERLINK("https://example.invalid")','Security test')
        run_stage(self.db,'reconcile',{},Reconciler())
        result=export_workbook(self.db,self.output)
        self.assertEqual(result['images'],1)
        book=load_workbook(self.output)
        self.addCleanup(book.close)
        self.assertTrue({'README','Images','Cameras','Detections','EXIF Metadata','OCR Metadata','Metadata Conflicts','Reviews','Errors','Runs','Export Info'}<=set(book.sheetnames))
        headers=[c.value for c in book['Images'][1]]
        row=dict(zip(headers,book['Images'][2]))
        self.assertEqual(row['image_id'].value,self.image_id)
        self.assertEqual(row['temperature_c'].data_type,'n')
        self.assertEqual(row['make'].data_type,'s')
        self.assertTrue(row['make'].value.startswith("'="))
        self.assertEqual(book['Detections'].max_row,3)
        self.assertEqual(book['Images'].freeze_panes,'A2')
        self.assertTrue(book['Images'].auto_filter.ref)

    def test_zero_rows_and_existing_file_protection(self):
        result=export_workbook(self.db,self.output,{'image_ids':[]})
        self.assertEqual(result['images'],0)
        book=load_workbook(self.output)
        self.assertEqual(book['Images'].max_row,1)
        book.close()
        before=self.output.read_bytes()
        with self.assertRaises(FileExistsError):
            export_workbook(self.db,self.output)
        self.assertEqual(self.output.read_bytes(),before)

    def test_analysis_preserves_source_values_and_long_tags_in_columns(self):
        import sqlite3
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute('UPDATE exif_records SET raw_json=?', (json.dumps({'ExifIFD:ISO':100,'Unknown:LongTag':'x'*70000,'IFD0:Software':'=unsafe'}),))
            connection.commit()
        export_workbook(self.db,self.output)
        book=load_workbook(self.output)
        self.addCleanup(book.close)
        sheet=book['Analysis Data']
        self.assertEqual(sheet.max_row,2)
        row=dict(zip((c.value for c in sheet[1]),sheet[2]))
        self.assertEqual(row['image_id'].value,self.image_id)
        self.assertEqual(row['exif_raw.ExifIFD:ISO'].value,100)
        self.assertEqual(row['exif_raw.ExifIFD:ISO'].data_type,'n')
        self.assertTrue(row['exif_raw.IFD0:Software'].value.startswith("'="))
        self.assertEqual(''.join(row[key].value for key in ['exif_raw.Unknown:LongTag','exif_raw.Unknown:LongTag__part_2','exif_raw.Unknown:LongTag__part_3']),'x'*70000)
        self.assertEqual(row['ocr_temperature_c'].data_type,'n')
        self.assertEqual(row['ocr_status'].value,'succeeded')
        self.assertIn('exif_capture_timestamp',row)
        self.assertIn('ocr_capture_timestamp',row)

    def test_long_raw_metadata_is_losslessly_chunked(self):
        import sqlite3
        raw=json.dumps([{'Unknown:LongTag':'x'*70000}])
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute('UPDATE exif_records SET raw_json=?',(raw,))
            connection.commit()
        export_workbook(self.db,self.output)
        book=load_workbook(self.output,read_only=True)
        self.addCleanup(book.close)
        rows=list(book['EXIF Metadata'].iter_rows(min_row=2,values_only=True))
        self.assertEqual(''.join(row[-1] for row in rows),raw)
        self.assertEqual(len(rows),3)

    def test_row_limit_rolls_over_without_dropping_data(self):
        with patch('jaguar.export.workbook.MAX_ROWS',3):
            result=export_workbook(self.db,self.output)
        book=load_workbook(self.output,read_only=True)
        self.addCleanup(book.close)
        sheets=[sheet for sheet in book if sheet.title.startswith('Observations')]
        self.assertGreater(len(sheets),1)
        self.assertEqual(sum(sum(1 for _ in sheet.iter_rows(min_row=2)) for sheet in sheets),result['counts']['Observations'])

    def test_invalid_scope_does_not_leave_output(self):
        with self.assertRaises(ValueError):
            export_workbook(self.db,self.output,{'start_date':'not-a-date'})
        self.assertFalse(self.output.exists())
