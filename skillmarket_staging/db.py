"""File-based setup and bounded operational inspection."""

import json
import threading
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path

import duckdb

from .contracts import ContractError

# One writer process owns the DuckDB file; serialize API calls within that process.
WRITER_LOCK = threading.RLock()


@contextmanager
def transaction(conn):
    conn.execute("BEGIN TRANSACTION")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def connect(database, *, create=False):
    path = Path(database)
    if str(database) == ":memory:":
        raise ContractError("Use a persistent DuckDB file for the import audit log")
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.is_file():
        raise ContractError("Database not found; run init first")
    # Keep newly created files readable by the stable DuckDB on the team server.
    conn = duckdb.connect(str(path), config={"storage_compatibility_version": "v1.4.0"})
    conn.execute("SET TimeZone = 'UTC'")
    return conn


def initialize(database) -> dict:
    with WRITER_LOCK, connect(database, create=True) as conn:
        with transaction(conn):
            conn.execute("CREATE SCHEMA IF NOT EXISTS skillmarket_ingest")
            conn.execute("""CREATE TABLE IF NOT EXISTS skillmarket_ingest.schema_migrations (
                version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp)""")
            versions = [r[0] for r in conn.execute(
                "SELECT version FROM skillmarket_ingest.schema_migrations ORDER BY version").fetchall()]
            if versions not in ([], [1], [1, 2], [1, 2, 3], [1, 2, 3, 4], [1, 2, 3, 4, 5]):
                raise ContractError(f"Unsupported schema versions: {versions}")
            if not versions:
                migration = files("skillmarket_staging").joinpath("sql", "001_initial.sql").read_text()
                conn.execute(migration)
                conn.execute("INSERT INTO skillmarket_ingest.schema_migrations (version) VALUES (1)")
            if 2 not in versions:
                migration = files("skillmarket_staging").joinpath("sql", "002_company_names.sql").read_text()
                conn.execute(migration)
                conn.execute("INSERT INTO skillmarket_ingest.schema_migrations (version) VALUES (2)")
            if 3 not in versions:
                migration = files("skillmarket_staging").joinpath("sql", "003_application.sql").read_text()
                conn.execute(migration)
                conn.execute("INSERT INTO skillmarket_ingest.schema_migrations (version) VALUES (3)")
            if 4 not in versions:
                migration = files("skillmarket_staging").joinpath("sql", "004_large_skill_edges.sql").read_text()
                conn.execute(migration)
                conn.execute("INSERT INTO skillmarket_ingest.schema_migrations (version) VALUES (4)")
            if 5 not in versions:
                migration = files("skillmarket_staging").joinpath("sql", "005_courses.sql").read_text()
                conn.execute(migration)
                conn.execute("INSERT INTO skillmarket_ingest.schema_migrations (version) VALUES (5)")
    return {"schema_version": 5, "status": "ready", "database": str(Path(database).resolve())}


def _ready(conn):
    try:
        versions = conn.execute("SELECT version FROM skillmarket_ingest.schema_migrations ORDER BY version").fetchall()
    except duckdb.CatalogException as exc:
        raise ContractError("Staging schema not found; run init first") from exc
    if versions not in ([(1,)], [(1,), (2,)], [(1,), (2,), (3,)], [(1,), (2,), (3,), (4,)], [(1,), (2,), (3,), (4,), (5,)]):
        raise ContractError("Unsupported staging schema version")


def as_dicts(cursor) -> list[dict]:
    columns = [c[0] for c in cursor.description]
    json_columns = {c[0] for c in cursor.description if str(c[1]) == 'JSON'}
    result = [dict(zip(columns, row)) for row in cursor.fetchall()]
    for row in result:
        # JSON logical types are returned as strings by DuckDB's Python client.
        for key in json_columns:
            if row[key] is not None:
                row[key] = json.loads(row[key])
    return result


def inspect_database(database, kind: str, *, limit: int = 20, batch_id: str | None = None) -> list[dict]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ContractError("limit must be between 1 and 100")
    with WRITER_LOCK, connect(database) as conn:
        _ready(conn)
        if kind == "runs":
            return as_dicts(conn.execute("""SELECT * FROM skillmarket_ingest.import_runs
                ORDER BY started_at DESC, run_id LIMIT ?""", [limit]))
        if kind == "batches":
            return as_dicts(conn.execute("""SELECT * FROM skillmarket_ingest.batches
                ORDER BY staged_at DESC, batch_id LIMIT ?""", [limit]))
        if kind == "records" and batch_id:
            return as_dicts(conn.execute("""SELECT r.line_number, r.source_record_id, r.payload
                FROM skillmarket_staging.records r JOIN skillmarket_ingest.batches b USING (batch_key)
                WHERE b.batch_id = ? ORDER BY r.line_number LIMIT ?""", [batch_id, limit]))
        raise ContractError("Use runs, batches, or records with a batch_id")
