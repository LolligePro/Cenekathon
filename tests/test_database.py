import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from skillmarket_staging.contracts import ContractError
from skillmarket_staging.database import database_status, export_database, import_labeled, import_parser, load_vacancies
from skillmarket_staging.db import initialize


class ApplicationDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.db=self.root/'skillmarket.duckdb'
        self.raw=self.root/"vacan'cies.parquet"
        initialize(self.db)
        with duckdb.connect() as c:
            c.execute("""COPY (SELECT * FROM (VALUES
                ('p1','Engineer','Original full description','ING Netherlands',DATE '2026-01-01','BE','Gent'),
                ('p2','Engineer','Description 2','ing',DATE '2026-01-20','BE','Gent'),
                ('p3','Engineer','Description 3','Yource Group',DATE '2026-03-01','BE','Gent'),
                ('p4','Engineer','Description 4','ING',NULL,'BE','Gent')
                ) t(vacancy_id,title,description,company,date_posted,country,city))
                TO ? (FORMAT PARQUET)""",[str(self.raw)])
        load_vacancies(self.db,[self.raw])

    def tearDown(self):
        self.temp.cleanup()

    def write_jsonl(self,path,rows):
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows))

    def batch(self,rows,batch_id='batch-one'):
        directory=self.root/batch_id
        directory.mkdir(exist_ok=True)
        (directory/'manifest.json').write_text(json.dumps({
            'contract_version':'1.0','batch_id':batch_id,'parser_version':'parser-v1',
            'skill_taxonomy_version':'skills-v1','record_count':len(rows)}))
        self.write_jsonl(directory/'skills.jsonl',[{'skill_id':'S001','name':'Python'},{'skill_id':'S002','name':'SQL'}])
        self.write_jsonl(directory/'parsed.jsonl',rows)
        return directory

    def valid_rows(self):
        return [dict(posting_id=p,parse_status='ok',normalized_title='Engineer',role_family='technology',
                     skill_ids=s,error=None) for p,s in [('p1',['S001']),('p2',[]),('p3',['S001','S002'])]]

    def query(self,sql):
        with duckdb.connect(str(self.db),read_only=True) as c:
            return c.execute(sql).fetchall()

    def test_original_metadata_and_full_description_view(self):
        status=database_status(self.db)
        self.assertEqual((status['source_postings'],status['pending'],status['missing_dates']),(4,4,1))
        self.assertEqual(self.query("SELECT description FROM skillmarket_app.raw_vacancies WHERE vacancy_id='p1'"),[('Original full description',)])
        self.assertEqual(self.query("SELECT company_normalized FROM skillmarket_app.postings WHERE posting_id='p1'"),[('ING',)])
        load_vacancies(self.db,[self.raw])
        self.assertEqual(database_status(self.db)['source_postings'],4)

    def test_jsonl_import_dense_months_and_zero_skill_denominator(self):
        directory=self.batch(self.valid_rows())
        result=import_parser(self.db,directory)
        self.assertEqual((result['parsed'],result['pending'],result['posting_skills']),(3,1,3))
        self.assertEqual([r[0] for r in self.query('SELECT total_postings FROM skillmarket_app.monthly_totals ORDER BY month')],[2,0,1])
        self.assertEqual([r[0] for r in self.query("SELECT postings FROM skillmarket_app.skill_demand WHERE skill='SQL' ORDER BY month")],[0,0,1])
        import_parser(self.db,directory)
        self.assertEqual(database_status(self.db)['posting_skills'],3)

    def test_correction_replaces_old_skill_edges(self):
        import_parser(self.db,self.batch(self.valid_rows()))
        row=self.valid_rows()[0] | {'skill_ids':['S002']}
        import_parser(self.db,self.batch([row],'correction'))
        self.assertEqual(self.query("SELECT skill_id FROM skillmarket_app.posting_skills WHERE posting_id='p1'"),[('S002',)])

    def test_unknown_source_ids_roll_back_publication(self):
        rows=[self.valid_rows()[0] | {'posting_id':'unknown'}]
        with self.assertRaisesRegex(ContractError,'not found'):
            import_parser(self.db,self.batch(rows))
        self.assertEqual(database_status(self.db)['parsed'],0)
        self.assertEqual(database_status(self.db)['skills'],0)

    def native_file(self,name='labeled_2026.parquet',unknown=False):
        path=self.root/name
        with duckdb.connect() as c:
            c.execute("""COPY (SELECT * FROM (VALUES
                (?, 'technology', ['python','sql','sql']),
                ('p2','technology',[]::VARCHAR[])) t(vacancy_id,job_type,skills))
                TO ? (FORMAT PARQUET)""",['bad-id' if unknown else 'p1',str(path)])
        return path

    def test_native_parser_adapter_and_idempotency(self):
        path=self.native_file()
        result=import_labeled(self.db,[path])
        self.assertEqual((result['parsed'],result['posting_skills']),(2,2))
        import_labeled(self.db,[path])
        self.assertEqual(database_status(self.db)['posting_skills'],2)
        self.assertEqual(self.query('SELECT name FROM skillmarket_app.skills ORDER BY name'),[('python',),('sql',)])

    def test_native_unknown_ids_roll_back(self):
        with self.assertRaises(ContractError):
            import_labeled(self.db,[self.native_file(unknown=True)])
        self.assertEqual(database_status(self.db)['skills'],0)

    def test_folder_skips_unfinished_parquet(self):
        self.native_file()
        (self.root/'labeled_2025.parquet').write_bytes(b'PAR1unfinished')
        result=import_parser(self.db,self.root)
        self.assertEqual(result['parsed'],2)
        self.assertEqual(len(result['skipped_unfinished_files']),1)

    def test_exports(self):
        import_parser(self.db,self.batch(self.valid_rows()))
        result=export_database(self.db,self.root/'export')
        self.assertEqual(len(result['exports']),5)
        with duckdb.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM read_parquet(?)',[str(self.root/'export'/'postings.parquet')]).fetchone()[0],4)

    def test_visualization_reads_database_and_serializes_gaps_as_null(self):
        from visualization.data_source import DatabaseSource
        from visualization import api

        import_parser(self.db,self.batch(self.valid_rows()))
        source=DatabaseSource(self.db)
        self.assertEqual(source.job_types(),['technology'])
        with patch.object(api,'get_source',return_value=source):
            result=api.skill_trends('technology',skills=['Python'])
        json.dumps(result,allow_nan=False)
        self.assertIsNone(result['data'][1]['share'])
        self.assertEqual(result['data'][0]['share'],0.5)

    def test_visualization_auto_selection_and_cache_refresh(self):
        import visualization.data_source as data_source
        import_parser(self.db,self.batch(self.valid_rows()))
        with patch.dict(os.environ,{'SKILLMARKET_DB':str(self.db),'SKILLMARKET_SOURCE':'auto'}),patch.object(data_source,'_source',None):
            source=data_source.get_source()
            self.assertIsInstance(source,data_source.DatabaseSource)
            self.assertEqual(int(source.monthly_totals()['total_postings'].sum()),3)
            row=self.valid_rows()[0] | {'posting_id':'p4'}
            import_parser(self.db,self.batch([row],'undated'))
            self.assertEqual(int(source.monthly_totals()['total_postings'].sum()),3)


if __name__=='__main__':
    unittest.main()
