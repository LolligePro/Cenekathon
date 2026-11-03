import json
from pathlib import Path
import tempfile
import unittest

import duckdb

from skillmarket_staging.courses import import_courses
from skillmarket_staging.db import initialize
from skillmarket_staging.ingest import ImportFailure, load_skills


class CourseImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / 'skillmarket.duckdb'
        self.batch = self.root / 'batches' / 'batch-courses-001'
        self.batch.mkdir(parents=True)
        initialize(self.db)
        self.write('skills.jsonl', [{'skill_id':'S001','name':'python'}, {'skill_id':'S002','name':'sql'}])
        load_skills(self.db, self.root/'skills.jsonl', taxonomy_version='skills-v1')
        with duckdb.connect(str(self.db)) as conn:
            conn.execute("INSERT INTO skillmarket_app.skills (skill_id,name) VALUES ('S001','python'),('S002','sql')")
            conn.execute("INSERT INTO skillmarket_app.postings (posting_id,title) VALUES ('p1','Untouched job')")
        self.metadata = [dict(posting_id=i,title='Course '+i,url='https://example.org/'+i,site='Test',
                              short_intro=None,listed_skills=[],skill_source='listed') for i in ['c1','c2']]
        self.rows = [dict(posting_id=i,parse_status='ok',normalized_title='course',skill_ids=s,error=None)
                     for i,s in [('c1',['S001','S002']),('c2',[])]]
        self.manifest = dict(contract_version='1.0',batch_id='courses-1',parser_version='rules-v1-courses',
                             skill_taxonomy_version='skills-v1',record_count=2)
        self.save()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, rows):
        (self.root/name).write_text(''.join(json.dumps(row)+'\n' for row in rows))

    def save(self):
        self.write('courses.jsonl', self.metadata)
        self.write('batches/batch-courses-001/parsed.jsonl', self.rows)
        (self.batch/'manifest.json').write_text(json.dumps(self.manifest))
        (self.batch/'input-ids.txt').write_text('c1\nc2\n')

    def query(self, sql):
        with duckdb.connect(str(self.db), read_only=True) as conn:
            return conn.execute(sql).fetchall()

    def test_import_joins_catalog_keeps_empty_courses_and_preserves_jobs(self):
        result = import_courses(self.db, self.root)
        self.assertEqual((result['courses'],result['course_skills'],result['without_skills']), (2,2,1))
        self.assertEqual(self.query('''SELECT c.course_id,s.name FROM skillmarket_app.courses c
            JOIN skillmarket_app.course_skills cs USING(course_id)
            JOIN skillmarket_app.skills s USING(skill_id) ORDER BY s.name'''), [('c1','python'),('c1','sql')])
        self.assertEqual(self.query('SELECT title FROM skillmarket_app.postings'), [('Untouched job',)])
        self.assertEqual(self.query('SELECT count(*) FROM skillmarket_staging.records'), [(0,)])
        self.assertEqual(self.query("SELECT json_extract_string(metadata,'$.posting_id') FROM skillmarket_app.courses ORDER BY course_id"), [('c1',),('c2',)])

    def test_duplicate_and_changed_batch(self):
        import_courses(self.db, self.root)
        self.assertEqual(import_courses(self.db, self.root)['status'], 'duplicate')
        self.metadata[0]['title']='Changed title'
        self.save()
        with self.assertRaisesRegex(ImportFailure, 'immutable'):
            import_courses(self.db, self.root)
        self.assertEqual(self.query("SELECT title FROM skillmarket_app.courses WHERE course_id='c1'"), [('Course c1',)])
        self.assertEqual(self.query('SELECT status FROM skillmarket_ingest.import_runs ORDER BY started_at DESC LIMIT 1'), [('failed',)])

    def test_correction_replaces_edges_and_failed_result_clears_skills(self):
        import_courses(self.db, self.root)
        self.manifest['batch_id']='courses-2'
        self.rows[0].update(parse_status='failed',normalized_title=None,skill_ids=[],
                            error={'code':'EMPTY_INPUT','message':'No usable title'})
        self.save()
        import_courses(self.db, self.root)
        self.assertEqual(self.query('SELECT count(*) FROM skillmarket_app.course_skills'), [(0,)])
        self.assertEqual(self.query("SELECT json_extract_string(parser_payload,'$.error.code') FROM skillmarket_app.courses WHERE course_id='c1'"), [('EMPTY_INPUT',)])

    def test_invalid_ids_and_unknown_skills_never_publish(self):
        for mode in ['missing_metadata','duplicate_metadata','duplicate_parser','unknown_skill','expected_ids']:
            with self.subTest(mode=mode):
                self.save()
                if mode=='missing_metadata':
                    self.write('courses.jsonl',self.metadata[:1])
                elif mode=='duplicate_metadata':
                    self.write('courses.jsonl',self.metadata+[self.metadata[0]])
                elif mode=='duplicate_parser':
                    self.write('batches/batch-courses-001/parsed.jsonl',[self.rows[0]]*2)
                elif mode=='unknown_skill':
                    self.write('batches/batch-courses-001/parsed.jsonl',[self.rows[0] | {'skill_ids':['UNKNOWN']}, self.rows[1]])
                else:
                    (self.batch/'input-ids.txt').write_text('c1\nwrong\n')
                with self.assertRaises(ImportFailure):
                    import_courses(self.db,self.root)
                self.assertEqual(self.query('SELECT count(*) FROM skillmarket_app.courses'), [(0,)])

    def test_existing_v4_database_migrates_without_rebuilding_jobs(self):
        with duckdb.connect(str(self.db)) as conn:
            for table in ['courses','course_skills','course_imports']:
                conn.execute(f'DROP TABLE skillmarket_app.{table}')
            conn.execute('DELETE FROM skillmarket_ingest.schema_migrations WHERE version=5')
        import_courses(self.db,self.root)
        self.assertEqual(self.query('SELECT max(version) FROM skillmarket_ingest.schema_migrations'), [(5,)])
        self.assertEqual(self.query('SELECT count(*) FROM skillmarket_app.postings'), [(1,)])


if __name__ == '__main__':
    unittest.main()
