"""Serve the shared DuckDB over localhost HTTP; access it through an SSH tunnel.

Also runnable directly on the team's Python 3.10 server, with only duckdb installed.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from decimal import Decimal
import glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import threading
import time

import duckdb


def json_value(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value) if value.is_finite() else None
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key):json_value(item) for key,item in value.items()}
    return value


class QueryService:
    def __init__(self, database, *, sources=(), timeout=15, max_rows=10000):
        path=Path(database).resolve()
        if not path.is_file():
            raise ValueError(f'Database not found: {path}')
        self.timeout,self.max_rows=timeout,max_rows
        self.lock=threading.Lock()
        self.connection=duckdb.connect(str(path),read_only=True,config={
            'threads':2,'memory_limit':'2GB','allow_persistent_secrets':False,
            'autoinstall_known_extensions':False,
        })
        # Load bundled readers before restricting external access. Only the
        # explicitly supplied original Parquet files remain readable by queries.
        self.connection.execute('LOAD parquet')
        self.connection.execute('LOAD json')
        allowed=sorted({str(Path(p).resolve()) for pattern in sources for p in glob.glob(str(pattern))})
        self.connection.execute('SET allowed_paths = ?',[allowed])
        self.connection.execute('SET enable_external_access = false')
        self.connection.execute('SET lock_configuration = true')
        counts={table:self.connection.execute(f'SELECT count(*) FROM skillmarket_app.{table}').fetchone()[0]
                for table in ['postings','skills','posting_skills']}
        self.health={'status':'ok','database':path.name,'duckdb_version':duckdb.__version__,
                     'read_only':True,'counts':counts}

    def close(self):
        self.connection.close()

    def query(self,sql,parameters=None,max_rows=1000):
        if not isinstance(sql,str) or not sql.strip():
            raise ValueError('sql must be a nonempty string')
        if parameters is not None and not isinstance(parameters,list):
            raise ValueError('parameters must be a JSON array')
        if type(max_rows) is not int or not 1<=max_rows<=self.max_rows:
            raise ValueError(f'max_rows must be between 1 and {self.max_rows}')
        if not self.lock.acquire(timeout=2):
            raise TimeoutError('Database is busy; retry shortly')
        timer=None
        started=time.perf_counter()
        try:
            statements=self.connection.extract_statements(sql)
            if len(statements)!=1 or statements[0].type!=duckdb.StatementType.SELECT:
                raise ValueError('Exactly one SELECT query is allowed')
            timer=threading.Timer(self.timeout,self.connection.interrupt)
            timer.daemon=True
            timer.start()
            cursor=self.connection.execute(sql,parameters or [])
            columns=[{'name':c[0],'type':str(c[1])} for c in cursor.description]
            rows=cursor.fetchmany(max_rows+1)
            return {'columns':columns,'rows':json_value(rows[:max_rows]),
                    'row_count':min(len(rows),max_rows),'truncated':len(rows)>max_rows,
                    'elapsed_ms':round((time.perf_counter()-started)*1000,2)}
        finally:
            if timer:
                timer.cancel()
                timer.join()
            self.lock.release()

    def schema(self):
        return self.query("""SELECT table_schema,table_name,column_name,data_type,is_nullable
            FROM information_schema.columns WHERE table_schema='skillmarket_app'
            ORDER BY table_name,ordinal_position""",max_rows=1000)


class DatabaseHTTPServer(ThreadingHTTPServer):
    daemon_threads=True

    def __init__(self,address,service):
        self.service=service
        super().__init__(address,DatabaseHandler)


class DatabaseHandler(BaseHTTPRequestHandler):
    def respond(self,status,data):
        body=json.dumps(data,ensure_ascii=False,allow_nan=False,default=str).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            if self.path=='/health':
                self.respond(200,self.server.service.health)
            elif self.path=='/schema':
                self.respond(200,self.server.service.schema())
            else:
                self.respond(404,{'error':'Use GET /health, GET /schema or POST /query'})
        except (TimeoutError,duckdb.InterruptException) as exc:
            self.respond(503,{'error':str(exc)})
        except duckdb.Error as exc:
            self.respond(400,{'error':str(exc)})

    def do_POST(self):
        if self.path!='/query':
            self.respond(404,{'error':'Unknown endpoint'})
            return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=131072:
                self.respond(413,{'error':'Expected a JSON body of at most 128 KiB'})
                return
            body=json.loads(self.rfile.read(length))
            if not isinstance(body,dict) or set(body)-{'sql','parameters','max_rows'}:
                raise ValueError('Expected sql, optional parameters and optional max_rows')
            self.respond(200,self.server.service.query(body.get('sql'),body.get('parameters'),body.get('max_rows',1000)))
        except (ValueError,UnicodeError) as exc:
            self.respond(400,{'error':str(exc)})
        except duckdb.PermissionException as exc:
            self.respond(403,{'error':str(exc)})
        except (TimeoutError,duckdb.InterruptException) as exc:
            self.respond(503,{'error':str(exc)})
        except duckdb.Error as exc:
            self.respond(400,{'error':str(exc)})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--sources',nargs='*',default=[])
    args=parser.parse_args()
    service=QueryService(args.database,sources=args.sources)
    server=DatabaseHTTPServer(('127.0.0.1',args.port),service)
    print(json.dumps({'listen':f'http://127.0.0.1:{args.port}',**service.health}),flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()


if __name__=='__main__':
    main()
