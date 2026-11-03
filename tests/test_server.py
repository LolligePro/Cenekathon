import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen

import duckdb

from skillmarket_staging.client import RemoteDatabase
from skillmarket_staging.server import DatabaseHTTPServer,QueryService


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.db=self.root/'skillmarket.duckdb'
        self.allowed=self.root/'allowed.csv'
        self.allowed.write_text('value\n42\n')
        self.secret=self.root/'private.csv'
        self.secret.write_text('secret\nnot-for-the-api\n')
        with duckdb.connect(str(self.db)) as c:
            c.execute('CREATE SCHEMA skillmarket_app')
            c.execute("CREATE TABLE skillmarket_app.postings AS SELECT 'p1' AS posting_id,DATE '2026-01-01' AS date_posted")
            c.execute("CREATE TABLE skillmarket_app.skills AS SELECT 'S001' AS skill_id,'python' AS name")
            c.execute("CREATE TABLE skillmarket_app.posting_skills AS SELECT 'p1' AS posting_id,'S001' AS skill_id")
        self.service=QueryService(self.db,sources=[str(self.allowed)])

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_parameterized_query_and_result_limit(self):
        result=self.service.query('SELECT i,? AS label FROM range(5) t(i)',['hello'],max_rows=2)
        self.assertEqual(result['rows'],[[0,'hello'],[1,'hello']])
        self.assertTrue(result['truncated'])
        self.assertEqual(result['row_count'],2)
        values=self.service.query("SELECT DATE '2026-01-01', 'NaN'::DOUBLE")
        self.assertEqual(values['rows'],[['2026-01-01',None]])
        json.dumps(values,allow_nan=False)

    def test_rejects_writes_multiple_statements_and_invalid_limits(self):
        for sql in ['DELETE FROM skillmarket_app.postings','SELECT 1; SELECT 2','SET threads=8',"COPY (SELECT 1) TO '/tmp/nope'"]:
            with self.subTest(sql=sql),self.assertRaisesRegex(ValueError,'one SELECT'):
                self.service.query(sql)
        for limit in [0,True,10001]:
            with self.subTest(limit=limit),self.assertRaises(ValueError):
                self.service.query('SELECT 1',max_rows=limit)
        self.assertEqual(self.service.query('SELECT count(*) FROM skillmarket_app.postings')['rows'],[[1]])

    def test_external_access_is_limited_to_explicit_sources(self):
        self.assertEqual(self.service.query('SELECT value FROM read_csv(?)',[str(self.allowed)])['rows'],[[42]])
        with self.assertRaises(duckdb.PermissionException):
            self.service.query('SELECT * FROM read_csv(?)',[str(self.secret)])
        with self.assertRaises(ValueError):
            self.service.query('SET enable_external_access=true')

    def test_query_timeout_and_recovery(self):
        self.service.timeout=0.02
        with self.assertRaises(duckdb.InterruptException):
            self.service.query('SELECT sum(sqrt(i::DOUBLE)) FROM range(10000000000) t(i)')
        self.assertEqual(self.service.query('SELECT 42')['rows'],[[42]])

    def test_http_client_health_schema_query_and_errors(self):
        server=DatabaseHTTPServer(('127.0.0.1',0),self.service)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            url=f'http://127.0.0.1:{server.server_port}'
            client=RemoteDatabase(url)
            self.assertEqual(client.health()['counts']['posting_skills'],1)
            self.assertTrue(client.health()['read_only'])
            self.assertTrue(client.schema()['rows'])
            self.assertEqual(client.query('SELECT name FROM skillmarket_app.skills WHERE skill_id=?',['S001'])['rows'],[['python']])
            self.assertEqual(client.dataframe('SELECT 42 AS n').iloc[0]['n'],42)
            with self.assertRaises(ValueError):
                client.query('DELETE FROM skillmarket_app.skills')
            request=Request(url+'/query',data=b'not-json',headers={'Content-Type':'application/json'})
            with self.assertRaises(HTTPError) as caught:
                urlopen(request)
            self.assertEqual(caught.exception.code,400)
            caught.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__=='__main__':
    unittest.main()
