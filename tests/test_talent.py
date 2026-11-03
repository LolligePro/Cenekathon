import copy
from importlib.resources import files
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import duckdb

from skillmarket_staging.db import initialize
from skillmarket_staging.server import DatabaseHTTPServer, QueryService
from skillmarket_staging.talent import _match, find_internal_talent


class TalentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root/'skillmarket.duckdb'
        self.demo = json.loads(files('skillmarket_staging').joinpath('demo','internal_talent.json').read_text())
        catalog = {r['skill_id']:r['name'] for r in map(json.loads,
            (Path(__file__).resolve().parents[1]/'parser/skills.jsonl').read_text().splitlines())}
        ids = {s for p in self.demo['profiles'] for s in p['skill_ids']} | set(self.demo['roles'][0]['skill_ids'])
        initialize(self.database)
        with duckdb.connect(str(self.database)) as conn:
            conn.executemany('INSERT INTO skillmarket_app.skills(skill_id,name) VALUES (?,?)',
                             [(s,catalog[s]) for s in sorted(ids)])
            conn.execute('''INSERT INTO skillmarket_app.courses
                (course_id,title,url,site,listed_skills,parse_status,parser_version,taxonomy_version,batch_id,metadata,parser_payload)
                SELECT id,title,'https://example.org/'||id,'Test',[],'ok','test','skills-v1','test','{}','{}'
                FROM (VALUES ('course-ml','Machine learning introduction'),
                ('course-data','Data and machine learning'),('course-audit','Audit and risk')) t(id,title)''')
            conn.execute('''INSERT INTO skillmarket_app.course_skills VALUES
                ('course-ml','S091'),('course-data','S091'),('course-data','S007'),
                ('course-audit','S125'),('course-audit','S004')''')
        self.environment = patch.dict(os.environ, {'SKILLMARKET_DB':str(self.database)})
        self.environment.start()
        self.old_api = os.environ.pop('SKILLMARKET_API_URL',None)

    def tearDown(self):
        self.environment.stop()
        if self.old_api is not None:
            os.environ['SKILLMARKET_API_URL']=self.old_api
        self.temp.cleanup()

    def query(self, sql, params):
        with duckdb.connect(str(self.database), read_only=True) as conn:
            cursor=conn.execute(sql,params)
            return [dict(zip([c[0] for c in cursor.description],row)) for row in cursor.fetchall()]

    def test_ranking_uses_fixed_ids_and_real_course_gaps(self):
        result=find_internal_talent()
        self.assertTrue(result['synthetic'])
        self.assertEqual(result['profiles_evaluated'],5)
        self.assertEqual([p['profile_id'] for p in result['candidates']],['demo-a','demo-d','demo-b'])
        first=result['candidates'][0]
        self.assertEqual((first['matched_count'],first['required_count'],first['match_percentage']),(5,7,71.4))
        self.assertEqual({s['skill_id'] for s in first['missing_skills']},{'S091','S345'})
        self.assertEqual(first['skills_without_courses'],[{'skill_id':'S345','name':'model validation'}])
        self.assertTrue(first['courses'])
        for course in first['courses']:
            self.assertEqual(course['covered_count'],1)
            self.assertEqual(course['covered_skills'],[{'skill_id':'S091','name':'machine learning'}])

    def test_complete_profile_ties_and_profile_follow_up(self):
        demo=copy.deepcopy(self.demo)
        demo['profiles'][0]['skill_ids']=demo['roles'][0]['skill_ids']
        demo['profiles'][3]['skill_ids']=demo['roles'][0]['skill_ids']
        result=_match(self.query,demo=demo,n=2)
        self.assertEqual([p['profile_id'] for p in result['candidates']],['demo-a','demo-d'])
        self.assertEqual(result['candidates'][0]['match_percentage'],100)
        self.assertEqual(result['candidates'][0]['missing_skills'],[])
        self.assertEqual(result['candidates'][0]['courses'],[])
        follow=find_internal_talent(profile_id='demo-d')
        self.assertEqual(len(follow['candidates']),1)
        self.assertEqual(follow['candidates'][0]['profile_id'],'demo-d')

    def test_empty_course_catalog_and_invalid_inputs(self):
        with duckdb.connect(str(self.database)) as conn:
            conn.execute('DELETE FROM skillmarket_app.course_skills')
        first=find_internal_talent()['candidates'][0]
        self.assertEqual(first['courses'],[])
        self.assertEqual(first['skills_without_courses'],first['missing_skills'])
        for args in [{'n':0},{'n':True},{'n':6},{'target_role_id':'invented'},{'profile_id':'unknown'}]:
            with self.subTest(args=args),self.assertRaises(ValueError):
                find_internal_talent(**args)
        with duckdb.connect(str(self.database)) as conn:
            conn.execute("DELETE FROM skillmarket_app.skills WHERE skill_id='S345'")
        with self.assertRaisesRegex(ValueError,'missing from the database catalog'):
            find_internal_talent()

    def test_remote_database_uses_same_ranking(self):
        service=QueryService(self.database)
        server=DatabaseHTTPServer(('127.0.0.1',0),service)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with patch.dict(os.environ,{'SKILLMARKET_API_URL':f'http://127.0.0.1:{server.server_port}'}):
                result=find_internal_talent(n=1)
            self.assertEqual(result['candidates'][0]['match_percentage'],71.4)
            self.assertEqual(result['candidates'][0]['courses'][0]['covered_skills'][0]['skill_id'],'S091')
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            service.close()

    def test_existing_chat_executes_tool_and_retains_follow_up(self):
        import ai_layer
        import server as chat_server
        from fastapi.testclient import TestClient

        calls=[]

        def model_reply(**kwargs):
            calls.append(kwargs)
            args={'profile_id':'demo-d'} if len(calls)>1 else {}
            content=[SimpleNamespace(type='tool_use',id=f'talent-{len(calls)}',name='find_internal_talent',input=args)]
            stop='tool_use'
            return SimpleNamespace(content=content,stop_reason=stop,
                usage=SimpleNamespace(input_tokens=1,output_tokens=1,cache_read_input_tokens=0,cache_creation_input_tokens=0))

        fake=SimpleNamespace(messages=SimpleNamespace(create=model_reply))
        with patch.object(ai_layer,'_get_client',return_value=fake), \
             patch.object(ai_layer,'USAGE_LOG',self.root/'usage.jsonl'), \
             patch.object(ai_layer,'_ANSWER_CACHE',{}), \
             patch.object(chat_server,'sessions',{}),TestClient(chat_server.app) as client:
            response=client.post('/api/ask',json={'question':'Wie kan intern AI-auditor worden?'})
            self.assertEqual(response.status_code,200,response.text)
            data=response.json()
            self.assertIn('5/7',data['answer'])
            self.assertIn('https://example.org/',data['answer'])
            self.assertIn('Geen cursus gekoppeld in onze huidige catalogus voor: model validation.',data['answer'])
            self.assertIn('Fictieve HR-demo',data['answer'])
            self.assertEqual(len(calls),1)
            response=client.post('/api/ask',json={'question':'En Profiel D?', 'session_id':data['session_id']})
            self.assertEqual(response.status_code,200,response.text)
            self.assertIn('Profiel D',response.json()['answer'])
            self.assertGreater(len(calls[1]['messages']),1)
            self.assertEqual(len(calls),2)
            self.assertIn('find_internal_talent',{t['name'] for t in calls[0]['tools']})
            self.assertIn('AI-auditor',client.get('/').text)


if __name__=='__main__':
    unittest.main()
