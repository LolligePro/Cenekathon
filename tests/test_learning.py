import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import duckdb

import test_talent as fixtures
from skillmarket_staging import learning
from skillmarket_staging.access import read_queries
from skillmarket_staging.server import QueryService, DatabaseHTTPServer
from visualization.data_source import DatabaseSource, StubSource
from visualization import api as viz


class LearningTests(unittest.TestCase):
    setUp=fixtures.TalentTests.setUp
    tearDown=fixtures.TalentTests.tearDown
    query=fixtures.TalentTests.query

    def test_names_aliases_ids_and_unknowns_are_distinct(self):
        with duckdb.connect(str(self.database)) as conn:
            conn.execute("UPDATE skillmarket_app.skills SET synonyms=? WHERE skill_id=?",['["py","coding"]','S018'])
            conn.execute("UPDATE skillmarket_app.skills SET synonyms=? WHERE skill_id=?",['["coding"]','S024'])
        self.assertEqual(learning.find_skills('ML')['skills'],[{'skill_id':'S091','name':'machine learning'}])
        self.assertEqual(learning.canonical_names(['s024','ML']),['sql','machine learning'])
        result=learning.recommend_courses(['py','ML','model validation','unknown skill','coding'])
        self.assertEqual(set(result['unresolved']),{'unknown skill'})
        self.assertEqual(set(result['ambiguous']),{'coding'})
        self.assertEqual({s['skill_id'] for s in result['skills_without_courses']},{'S018','S345'})
        self.assertEqual(result['courses'][0]['covered_skills'],[{'skill_id':'S091','name':'machine learning'}])

    def test_course_plan_adds_unique_coverage_and_respects_limit(self):
        result=learning.recommend_courses(['machine learning','data analysis'])
        self.assertEqual(len(result['courses']),1)
        self.assertEqual(result['courses'][0]['covered_count'],2)
        self.assertEqual(result['skills_without_courses'],[])
        limited=learning.recommend_courses(['machine learning','audit'],max_courses=1)
        self.assertEqual(limited['skills_without_courses'],[])
        self.assertEqual(len(limited['skills_not_covered_by_plan']),1)
        self.assertIn('Nog niet gedekt',learning.format_courses(limited))

    def test_visualization_reads_real_course_tables_and_remote_api(self):
        frame=DatabaseSource(self.database).courses()
        self.assertEqual(set(frame['posting_id']),{'course-ml','course-data','course-audit'})
        self.assertEqual(self.query('SELECT count(*) AS n FROM skillmarket_app.posting_skills',[])[0]['n'],0)
        service=QueryService(self.database)
        server=DatabaseHTTPServer(('127.0.0.1',0),service)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with patch.dict(os.environ,{'SKILLMARKET_API_URL':f'http://127.0.0.1:{server.server_port}'}):
                self.assertEqual(len(DatabaseSource(self.database).courses()),len(frame))
                self.assertTrue(learning.recommend_courses(['ML'])['courses'])
        finally:
            server.shutdown(); thread.join(); server.server_close(); service.close()

    def test_profile_matching_uses_country_year_and_actual_missing_skills(self):
        with duckdb.connect(str(self.database)) as conn:
            conn.execute("""INSERT INTO skillmarket_app.postings(posting_id,parse_status,date_posted,country,job_type)
                VALUES ('p1','ok',DATE '2025-05-01','BE','technology'),
                       ('p2','ok',DATE '2025-05-02','BE','technology'),
                       ('p3','ok',DATE '2025-05-03','GB','technology'),
                       ('old','ok',DATE '2024-05-03','BE','technology')""")
            conn.execute("""INSERT INTO skillmarket_app.posting_skills VALUES
                ('p1','S024'),('p1','S091'),('p2','S024'),('p3','S091'),('old','S024')""")
            conn.execute("INSERT INTO skillmarket_app.monthly_totals VALUES ('technology',DATE '2025-05-01',3)")
        result=learning.profile_advice(['SQL'],'BE','technology')
        self.assertEqual((result['observed_postings'],result['overlap_postings'],result['full_skill_matches']),(2,2,1))
        self.assertEqual(result['suggested_skills'][0]['skill_id'],'S091')
        self.assertEqual(result['suggested_skills'][0]['extra_full_skill_matches'],1)
        self.assertTrue(result['courses'])

    def test_source_override_is_scoped_and_career_gaps_distinguish_plan_limit(self):
        source=StubSource()
        original=viz.get_source
        with viz.use_source(source):
            self.assertIs(viz.get_source(),source)
            result=viz.career_path('Risk Manager','Data Analyst',max_courses=1)
        self.assertIs(viz.get_source,original)
        self.assertIsNone(viz._SOURCE.get())
        self.assertIn('skills_without_courses',result['data'])
        self.assertIn('skills_not_covered_by_plan',result['data'])

    def test_existing_chat_routes_courses_and_followups_without_inventing_matches(self):
        import ai_layer
        import server as chat_server
        from fastapi.testclient import TestClient
        with patch.object(ai_layer,'_get_client',side_effect=AssertionError('Explicit course queries use real tools')), \
             patch.object(ai_layer,'USAGE_LOG',self.root/'chat-usage.jsonl'), \
             patch.object(ai_layer,'_ANSWER_CACHE',{}),patch.object(chat_server,'sessions',{}),TestClient(chat_server.app) as client:
            ai_layer._ANSWER_CACHE['welke courses voor ml en sql?']={'answer':'Old stale answer without courses','messages':[]}
            first=client.post('/api/ask',json={'question':'Welke courses voor ML en SQL?'})
            self.assertEqual(client.get('/api/health').json()['counts']['courses'],3)
            self.assertEqual(first.status_code,200,first.text)
            self.assertIn('https://example.org/',first.json()['answer'])
            self.assertIn('Geen cursus gekoppeld',first.json()['answer'])
            second=client.post('/api/ask',json={'question':'En voor model validation?', 'session_id':first.json()['session_id']})
            self.assertEqual(second.status_code,200,second.text)
            self.assertNotIn('https://',second.json()['answer'])
            self.assertIn('model validation',second.json()['answer'])
            third=client.post('/api/ask',json={'question':'En voor onbestaande vaardigheid?', 'session_id':first.json()['session_id']})
            self.assertIn('Niet herkend',third.json()['answer'])
            self.assertNotIn('https://',third.json()['answer'])

    def test_agent_selected_learning_tools_are_formatted_without_a_second_model_round(self):
        import ai_layer
        tool=SimpleNamespace(type='tool_use',id='course-test',name='recommend_courses',input={'skills':['ML']})
        response=SimpleNamespace(content=[tool],stop_reason='tool_use',
            usage=SimpleNamespace(input_tokens=1,output_tokens=1,cache_read_input_tokens=0,cache_creation_input_tokens=0))
        fake=SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs:response))
        with patch.object(ai_layer,'_get_client',return_value=fake),patch.object(ai_layer,'USAGE_LOG',self.root/'usage.jsonl'):
            result=ai_layer.ask('Help mij met een passend leertraject.')
        self.assertIn('https://example.org/',result['answer'])
        self.assertEqual(result['usage']['output_tokens'],1)


if __name__=='__main__':
    unittest.main()
