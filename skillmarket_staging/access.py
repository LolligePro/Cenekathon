"""One read-only query interface for local DuckDB and the shared HTTP service."""
from contextlib import contextmanager
import os
from pathlib import Path

import duckdb

from .client import RemoteDatabase


@contextmanager
def read_queries(database=None):
    path = Path(database or os.environ.get('SKILLMARKET_DB', Path(__file__).resolve().parents[1]/'data'/'skillmarket.duckdb'))
    url = os.environ.get('SKILLMARKET_API_URL')
    if url or not path.is_file():
        client = RemoteDatabase(url)
        def query(sql, parameters=(), limit=10000):
            result = client.query(sql, list(parameters), max_rows=limit)
            if result['truncated']:
                raise ValueError('Query exceeded the row limit; narrow the selection')
            columns = [c['name'] for c in result['columns']]
            return [dict(zip(columns, row)) for row in result['rows']]
        yield query
    else:
        with duckdb.connect(str(path), read_only=True) as connection:
            def query(sql, parameters=(), limit=10000):
                cursor = connection.execute(sql, list(parameters))
                columns = [c[0] for c in cursor.description]
                rows = cursor.fetchmany(limit+1)
                if len(rows)>limit:
                    raise ValueError('Query exceeded the row limit; narrow the selection')
                return [dict(zip(columns, row)) for row in rows]
            yield query
