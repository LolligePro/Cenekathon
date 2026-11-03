"""Bounded-memory batch receipt with atomic staging and durable attempt logs."""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from pathlib import Path

import duckdb

from .contracts import ContractError, decode, text, validate_manifest, validate_record, validate_skill
from .db import WRITER_LOCK, _ready, as_dicts, connect, transaction


class ImportFailure(RuntimeError):
    def __init__(self, run_id, code, message):
        self.run_id, self.code = str(run_id), code
        super().__init__(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def _start(conn, operation, source):
    run_id = uuid.uuid4()
    conn.execute("""INSERT INTO skillmarket_ingest.import_runs
        (run_id, operation, source_name) VALUES (?, ?, ?)""", [run_id, operation, Path(source).name])
    # Autocommit: this attempt remains visible after the data transaction rolls back.
    return run_id


def _finish(conn, run_id, *, status, state, duplicate_of=None, error_code=None, error_message=None):
    conn.execute("""UPDATE skillmarket_ingest.import_runs SET
        status = ?, batch_id = ?, manifest = ?::JSON, manifest_sha256 = ?, records_sha256 = ?,
        expected_records = ?, received_records = ?, source_ids_verified = ?,
        duplicate_of = ?, error_code = ?, error_message = ?, finished_at = current_timestamp
        WHERE run_id = ?""", [status, state.get("batch_id"),
        _json(state["manifest"]) if "manifest" in state else None,
        state.get("manifest_sha256"), state.get("records_sha256"), state.get("expected_records"),
        state.get("received_records", 0), state.get("source_ids_verified", False),
        duplicate_of, error_code, error_message, run_id])


def _failure(conn, run_id, state, exc):
    if isinstance(exc, ContractError):
        code = "CONTRACT_ERROR"
    elif isinstance(exc, OSError):
        code = "FILE_ERROR"
    elif isinstance(exc, duckdb.Error):
        code = "DATABASE_ERROR"
    elif isinstance(exc, (KeyboardInterrupt, SystemExit)):
        code = "INTERRUPTED"
    else:
        code = "IMPORT_ERROR"
    message = str(exc)[:2000] or type(exc).__name__
    _finish(conn, run_id, status="failed", state=state, error_code=code, error_message=message)
    if not isinstance(exc, Exception):
        raise exc
    raise ImportFailure(run_id, code, message) from exc


def load_skills(database, path, *, taxonomy_version: str) -> dict:
    """Load the contract's immutable validation registry; no application model."""
    with WRITER_LOCK, connect(database) as conn:
        _ready(conn)
        run_id = _start(conn, "load_skills", path)
        state = {}
        try:
            text(taxonomy_version, "taxonomy_version")
            items, seen = [], set()
            digest = hashlib.sha256()
            with Path(path).open("rb") as stream:
                for n, line in enumerate(stream, 1):
                    digest.update(line)
                    state["received_records"] = n
                    row = decode(line, f"skill catalog line {n}")
                    validate_skill(row)
                    if row["skill_id"] in seen:
                        raise ContractError(f"Duplicate catalog skill_id: {row['skill_id']}")
                    seen.add(row["skill_id"])
                    items.append(row)
            state["records_sha256"] = digest.hexdigest()
            if not items:
                raise ContractError("The skill catalog is empty")
            # Key/row ordering is not part of a catalog's identity.
            content_hash = hashlib.sha256(_json(sorted(items, key=lambda r: r["skill_id"])).encode()).hexdigest()
            with transaction(conn):
                old = conn.execute("""SELECT content_sha256, source_run_id
                    FROM skillmarket_ingest.catalogs WHERE catalog_version = ?""", [taxonomy_version]).fetchone()
                if old:
                    if old[0] != content_hash:
                        raise ContractError("A catalog version is immutable; use a new taxonomy version")
                    _finish(conn, run_id, status="duplicate", state=state, duplicate_of=old[1])
                    status = "duplicate"
                else:
                    conn.execute("""INSERT INTO skillmarket_ingest.catalogs
                        (catalog_version, content_sha256, item_count, source_run_id) VALUES (?, ?, ?, ?)""",
                                 [taxonomy_version, content_hash, len(items), run_id])
                    conn.executemany("""INSERT INTO skillmarket_ingest.catalog_items
                        (catalog_version, item_id, payload) VALUES (?, ?, ?::JSON)""",
                                     [(taxonomy_version, row["skill_id"], _json(row)) for row in items])
                    _finish(conn, run_id, status="loaded", state=state)
                    status = "loaded"
            return {"run_id": str(run_id), "status": status, "skills": len(items),
                    "taxonomy_version": taxonomy_version}
        except BaseException as exc:
            _failure(conn, run_id, state, exc)


def _verify_ids(conn, incoming_table: str, expected_ids, tempdir):
    """Verify an optional UTF-8 file containing one original input ID per line."""
    path = Path(tempdir) / "expected.jsonl"
    with Path(expected_ids).open(encoding="utf-8") as source, path.open("w", encoding="utf-8") as out:
        for line in source:
            source_id = text(line.rstrip("\r\n"), "expected input ID")
            out.write(_json({"source_record_id": source_id}) + "\n")
    if path.stat().st_size == 0:
        raise ContractError("The expected input ID file is empty")
    conn.execute("""CREATE TEMP TABLE expected_ids AS SELECT * FROM read_json(?,
        format='newline_delimited', columns={source_record_id: 'VARCHAR'})""", [str(path)])
    if conn.execute("""SELECT source_record_id FROM expected_ids GROUP BY source_record_id
        HAVING count(*) > 1 LIMIT 1""").fetchone():
        raise ContractError("Duplicate IDs in the expected input ID file")
    # incoming_table is chosen internally, never interpolated from caller input.
    if incoming_table not in {"incoming", "received_ids"}:
        raise ValueError("Unknown internal ID table")
    mismatches = conn.execute(f"""SELECT coalesce(i.source_record_id, e.source_record_id)
        FROM {incoming_table} i FULL JOIN expected_ids e USING (source_record_id)
        WHERE i.source_record_id IS NULL OR e.source_record_id IS NULL LIMIT 5""").fetchall()
    if mismatches:
        raise ContractError(f"Parser output IDs differ from the expected input IDs: {mismatches}")


def receive_batch(database, batch_dir, *, expected_ids=None) -> dict:
    """Receive manifest.json + parsed.jsonl. Persist the original JSON objects.

    No title/skill/application tables are created. Corrected postings are retained
    as a new batch, not merged into a guessed final application model.
    """
    batch_dir = Path(batch_dir)
    with WRITER_LOCK, connect(database) as conn:
        _ready(conn)
        run_id = _start(conn, "receive_batch", batch_dir)
        state = {}
        try:
            manifest = decode((batch_dir / "manifest.json").read_bytes(), "manifest.json")
            validate_manifest(manifest)
            state.update(manifest=manifest, batch_id=manifest["batch_id"],
                         expected_records=manifest["record_count"],
                         manifest_sha256=hashlib.sha256(_json(manifest).encode()).hexdigest())
            catalog_version = manifest["skill_taxonomy_version"]
            if not conn.execute("SELECT 1 FROM skillmarket_ingest.catalogs WHERE catalog_version = ?",
                                [catalog_version]).fetchone():
                raise ContractError(f"Load the shared skill catalog first: {catalog_version}")
            known = {r[0] for r in conn.execute("""SELECT item_id FROM skillmarket_ingest.catalog_items
                WHERE catalog_version = ?""", [catalog_version]).fetchall()}
            ok_count, failed_count = 0, 0
            with tempfile.TemporaryDirectory(prefix="skillmarket-staging-") as tempdir:
                envelope = Path(tempdir) / "incoming.jsonl"
                digest = hashlib.sha256()
                # Validate streaming, then let DuckDB bulk-read a fixed envelope.
                # Memory is proportional to one record plus the small skill catalog.
                with (batch_dir / "parsed.jsonl").open("rb") as source, envelope.open("w", encoding="utf-8") as out:
                    for n, line in enumerate(source, 1):
                        digest.update(line)
                        state["received_records"] = n
                        row = decode(line, f"parsed.jsonl line {n}")
                        try:
                            validate_record(row, known)
                        except ContractError as exc:
                            raise ContractError(f"Line {n}: {exc}") from exc
                        ok_count += row["parse_status"] == "ok"
                        failed_count += row["parse_status"] == "failed"
                        out.write(_json({"line_number": n, "source_record_id": row["posting_id"],
                                         "payload": row}) + "\n")
                state["records_sha256"] = digest.hexdigest()
                if state.get("received_records", 0) != manifest["record_count"]:
                    raise ContractError("record_count does not match the number of output records")

                with transaction(conn):
                    old = as_dicts(conn.execute("SELECT * FROM skillmarket_ingest.batches WHERE batch_id = ?",
                                               [manifest["batch_id"]]))
                    if old:
                        old = old[0]
                        if (old["manifest_sha256"], old["records_sha256"]) != (
                            state["manifest_sha256"], state["records_sha256"]
                        ):
                            raise ContractError("batch_id already exists with different content; use a new batch_id")
                        state["source_ids_verified"] = old["source_ids_verified"]
                        if expected_ids is not None:
                            conn.execute("""CREATE TEMP TABLE received_ids AS
                                SELECT source_record_id FROM skillmarket_staging.records WHERE batch_key = ?""",
                                         [old["batch_key"]])
                            _verify_ids(conn, "received_ids", expected_ids, tempdir)
                            state["source_ids_verified"] = True
                        _finish(conn, run_id, status="duplicate", state=state, duplicate_of=old["source_run_id"])
                        return {"run_id": str(run_id), "status": "duplicate", "batch_id": manifest["batch_id"],
                                "record_count": old["record_count"], "source_ids_verified": state["source_ids_verified"]}

                    conn.execute("""CREATE TEMP TABLE incoming AS SELECT * FROM read_json(?,
                        format='newline_delimited',
                        columns={line_number: 'BIGINT', source_record_id: 'VARCHAR', payload: 'JSON'},
                        maximum_object_size=67108864)""", [str(envelope)])
                    duplicates = conn.execute("""SELECT source_record_id FROM incoming
                        GROUP BY source_record_id HAVING count(*) > 1 LIMIT 5""").fetchall()
                    if duplicates:
                        raise ContractError(f"Duplicate posting IDs within the batch: {duplicates}")
                    if expected_ids is not None:
                        _verify_ids(conn, "incoming", expected_ids, tempdir)
                        state["source_ids_verified"] = True
                    batch_key = uuid.uuid4()
                    conn.execute("""INSERT INTO skillmarket_ingest.batches
                        (batch_key, batch_id, source_run_id, contract_version, parser_version, catalog_version,
                         manifest, manifest_sha256, records_sha256, record_count, ok_records,
                         failed_records, source_ids_verified) VALUES (?, ?, ?, ?, ?, ?, ?::JSON, ?, ?, ?, ?, ?, ?)""",
                                 [batch_key, manifest["batch_id"], run_id, manifest["contract_version"],
                                  manifest["parser_version"], catalog_version, _json(manifest),
                                  state["manifest_sha256"], state["records_sha256"], manifest["record_count"],
                                  ok_count, failed_count, state.get("source_ids_verified", False)])
                    conn.execute("INSERT INTO skillmarket_staging.records SELECT ?, * FROM incoming", [batch_key])
                    _finish(conn, run_id, status="staged", state=state)
            return {"run_id": str(run_id), "status": "staged", "batch_id": manifest["batch_id"],
                    "record_count": manifest["record_count"], "ok_records": ok_count,
                    "failed_records": failed_count, "source_ids_verified": state.get("source_ids_verified", False)}
        except BaseException as exc:
            _failure(conn, run_id, state, exc)
