"""Real DuckDB integration tests; each test owns a temporary database file."""

import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import duckdb

from skillmarket_staging.cli import main
from skillmarket_staging.contracts import ContractError, decode, validate_manifest, validate_record
from skillmarket_staging.db import initialize, inspect_database
from skillmarket_staging.ingest import ImportFailure, load_skills, receive_batch


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "staging.duckdb"
        initialize(self.database)
        self.skills = self.root / "skills.jsonl"
        self.write_jsonl(self.skills, [{"skill_id": "S001", "name": "Python"},
                                       {"skill_id": "S042", "name": "Machine learning"}])
        load_skills(self.database, self.skills, taxonomy_version="skills-v1")
        self.batch = self.root / "batch"
        self.batch.mkdir()
        self.manifest = {"contract_version": "1.0", "batch_id": "batch-001", "parser_version": "parser-v1",
                         "skill_taxonomy_version": "skills-v1", "record_count": 3}
        self.rows = [
            {"posting_id": "001", "parse_status": "ok", "normalized_title": "Risk Analyst",
             "skill_ids": ["S001", "S042"], "error": None},
            {"posting_id": "002", "parse_status": "ok", "normalized_title": "Cashier",
             "skill_ids": [], "error": None},
            {"posting_id": "003", "parse_status": "failed", "normalized_title": None,
             "skill_ids": [], "error": {"code": "MODEL_TIMEOUT", "message": "Timeout"}},
        ]
        self.save_batch()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write_jsonl(path, rows):
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    def save_batch(self):
        (self.batch / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        self.write_jsonl(self.batch / "parsed.jsonl", self.rows)

    def query(self, statement, args=None):
        with duckdb.connect(str(self.database)) as conn:
            return conn.execute(statement, args or []).fetchall()

    def assert_no_received_batches(self):
        self.assertEqual(self.query("SELECT count(*) FROM skillmarket_ingest.batches")[0][0], 0)
        self.assertEqual(self.query("SELECT count(*) FROM skillmarket_staging.records")[0][0], 0)

    def test_receive_preserves_payload_and_counts_parser_failures(self):
        result = receive_batch(self.database, self.batch)
        self.assertEqual((result["status"], result["ok_records"], result["failed_records"]), ("staged", 2, 1))
        out = inspect_database(self.database, "records", batch_id="batch-001")
        self.assertEqual([r["source_record_id"] for r in out], ["001", "002", "003"])
        self.assertEqual([r["payload"] for r in out], self.rows)
        self.assertFalse(inspect_database(self.database, "batches")[0]["source_ids_verified"])
        runs = inspect_database(self.database, "runs")
        self.assertEqual(runs[0]["status"], "staged")
        self.assertEqual(runs[0]["manifest"], self.manifest)
        self.assertIsNotNone(runs[0]["finished_at"])

    def test_repeated_delivery_is_logged_without_duplicate_records(self):
        first = receive_batch(self.database, self.batch)
        second = receive_batch(self.database, self.batch)
        self.assertEqual(second["status"], "duplicate")
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(self.query("SELECT count(*) FROM skillmarket_staging.records")[0][0], 3)
        self.assertEqual(str(inspect_database(self.database, "runs")[0]["duplicate_of"]), first["run_id"])

    def test_changed_content_cannot_reuse_batch_id(self):
        receive_batch(self.database, self.batch)
        self.rows[0]["skill_ids"] = []
        self.save_batch()
        with self.assertRaisesRegex(ImportFailure, "different content"):
            receive_batch(self.database, self.batch)
        self.assertEqual(inspect_database(self.database, "runs")[0]["status"], "failed")
        self.assertEqual(inspect_database(self.database, "records", batch_id="batch-001")[0]
                         ["payload"]["skill_ids"], ["S001", "S042"])

    def test_corrections_keep_staging_history(self):
        receive_batch(self.database, self.batch)
        self.manifest["batch_id"] = "batch-002"
        self.rows[0]["skill_ids"] = []
        self.save_batch()
        receive_batch(self.database, self.batch)
        self.assertEqual(self.query("SELECT count(*) FROM skillmarket_ingest.batches")[0][0], 2)
        self.assertEqual(self.query("SELECT count(*) FROM skillmarket_staging.records")[0][0], 6)

    def test_count_mismatch_has_durable_failure_log(self):
        self.manifest["record_count"] = 4
        self.save_batch()
        with self.assertRaisesRegex(ImportFailure, "record_count") as failure:
            receive_batch(self.database, self.batch)
        self.assert_no_received_batches()
        run = inspect_database(self.database, "runs")[0]
        self.assertEqual(str(run["run_id"]), failure.exception.run_id)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["received_records"], 3)

    def test_duplicate_posting_ids_roll_back_the_batch(self):
        self.rows[1]["posting_id"] = "001"
        self.save_batch()
        with self.assertRaisesRegex(ImportFailure, "Duplicate posting IDs"):
            receive_batch(self.database, self.batch)
        self.assert_no_received_batches()
        self.assertEqual(inspect_database(self.database, "runs")[0]["error_code"], "CONTRACT_ERROR")

    def test_late_failure_rolls_back_records_but_keeps_attempt_log(self):
        from skillmarket_staging import ingest

        original_finish = ingest._finish

        def fail_on_success(*args, **kwargs):
            if kwargs["status"] == "staged":
                raise RuntimeError("Simulated failure after records were inserted")
            return original_finish(*args, **kwargs)

        with patch.object(ingest, "_finish", side_effect=fail_on_success):
            with self.assertRaisesRegex(ImportFailure, "after records were inserted"):
                receive_batch(self.database, self.batch)
        self.assert_no_received_batches()
        self.assertEqual(inspect_database(self.database, "runs")[0]["status"], "failed")

    def test_unknown_skill_ids_block_receipt(self):
        self.rows[0]["skill_ids"] = ["not-registered"]
        self.save_batch()
        with self.assertRaisesRegex(ImportFailure, "Unknown skill IDs"):
            receive_batch(self.database, self.batch)
        self.assert_no_received_batches()

    def test_missing_catalog_is_explicit(self):
        self.manifest["skill_taxonomy_version"] = "unknown-version"
        self.save_batch()
        with self.assertRaisesRegex(ImportFailure, "Load the shared skill catalog first"):
            receive_batch(self.database, self.batch)
        self.assert_no_received_batches()

    def test_malformed_json_and_duplicate_json_keys_are_rejected(self):
        for content in ["{invalid}\n", '{"posting_id":"001","posting_id":"002"}\n',
                        '{"posting_id":NaN}\n', "[]\n", "\n"]:
            (self.batch / "parsed.jsonl").write_text(content)
            with self.assertRaises(ImportFailure):
                receive_batch(self.database, self.batch)
            self.assert_no_received_batches()

    def test_missing_file_attempt_is_logged(self):
        with self.assertRaises(ImportFailure):
            receive_batch(self.database, self.root / "missing")
        self.assertEqual(inspect_database(self.database, "runs")[0]["error_code"], "FILE_ERROR")

    def test_manifest_failure_is_logged(self):
        (self.batch / "manifest.json").write_text('{"record_count":true}')
        with self.assertRaises(ImportFailure):
            receive_batch(self.database, self.batch)
        self.assertEqual(inspect_database(self.database, "runs")[0]["status"], "failed")

    def test_source_id_verification_and_duplicate_delivery(self):
        ids = self.root / "ids.txt"
        ids.write_text("003\n001\n002\n")
        self.assertTrue(receive_batch(self.database, self.batch, expected_ids=ids)["source_ids_verified"])
        self.assertTrue(receive_batch(self.database, self.batch, expected_ids=ids)["source_ids_verified"])
        ids.write_text("001\n002\nwrong\n")
        with self.assertRaisesRegex(ImportFailure, "differ from"):
            receive_batch(self.database, self.batch, expected_ids=ids)
        self.assertEqual(self.query("SELECT count(*) FROM skillmarket_staging.records")[0][0], 3)

    def test_source_id_mismatch_and_duplicates_reject_new_batch(self):
        ids = self.root / "ids.txt"
        for content in ["001\n002\nwrong\n", "001\n001\n002\n003\n", ""]:
            ids.write_text(content)
            with self.assertRaises(ImportFailure):
                receive_batch(self.database, self.batch, expected_ids=ids)
            self.assert_no_received_batches()

    def test_catalog_versions_are_immutable(self):
        self.assertEqual(load_skills(self.database, self.skills, taxonomy_version="skills-v1")["status"], "duplicate")
        self.write_jsonl(self.skills, [{"skill_id": "S001", "name": "Changed"}])
        with self.assertRaisesRegex(ImportFailure, "immutable"):
            load_skills(self.database, self.skills, taxonomy_version="skills-v1")
        self.assertEqual(self.query("SELECT count(*) FROM skillmarket_ingest.catalog_items")[0][0], 2)

    def test_catalog_duplicate_ids_roll_back(self):
        self.write_jsonl(self.skills, [{"skill_id": "X", "name": "X"}] * 2)
        with self.assertRaisesRegex(ImportFailure, "Duplicate catalog"):
            load_skills(self.database, self.skills, taxonomy_version="skills-v2")
        self.assertEqual(self.query("SELECT count(*) FROM skillmarket_ingest.catalogs")[0][0], 1)

    def test_initialization_is_idempotent_and_no_business_columns_exist(self):
        receive_batch(self.database, self.batch)
        initialize(self.database)
        cols = self.query("""SELECT column_name FROM information_schema.columns
            WHERE table_schema='skillmarket_staging' AND table_name='records' ORDER BY ordinal_position""")
        self.assertEqual([c[0] for c in cols], ["batch_key", "line_number", "source_record_id", "payload"])
        self.assertEqual(self.query("SELECT count(*) FROM skillmarket_staging.records")[0][0], 3)

    def test_threaded_duplicate_receipts_are_serialized(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: receive_batch(self.database, self.batch), range(2)))
        self.assertEqual(sorted(r["status"] for r in results), ["duplicate", "staged"])
        self.assertEqual(self.query("SELECT count(*) FROM skillmarket_staging.records")[0][0], 3)

    def test_cli_success_and_failure_exit_codes(self):
        with redirect_stdout(io.StringIO()) as out:
            code = main(["--database", str(self.database), "receive", str(self.batch)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["status"], "staged")
        self.rows[0]["skill_ids"] = ["wrong"]
        self.save_batch()
        with redirect_stderr(io.StringIO()) as out:
            code = main(["--database", str(self.database), "receive", str(self.batch)])
        self.assertEqual(code, 1)
        self.assertIn("run_id", json.loads(out.getvalue()))

    def test_payload_is_queryable_with_duckdb_json_functions(self):
        receive_batch(self.database, self.batch)
        counts = self.query("""SELECT payload->>'$.parse_status', count(*)
            FROM skillmarket_staging.records GROUP BY 1 ORDER BY 1""")
        self.assertEqual(counts, [("failed", 1), ("ok", 2)])


class ContractTests(unittest.TestCase):
    def test_boolean_is_not_a_record_count(self):
        with self.assertRaises(ContractError):
            validate_manifest({"contract_version": "1.0", "batch_id": "b", "parser_version": "v1",
                               "skill_taxonomy_version": "v1", "record_count": True})

    def test_duplicate_skills_and_invalid_success_failure_states(self):
        row = {"posting_id": "p", "parse_status": "ok", "normalized_title": "Role", "skill_ids": [], "error": None}
        validate_record(row, {"S001"})
        for changes in [{"skill_ids": ["S001", "S001"]}, {"normalized_title": None},
                        {"parse_status": "failed"}, {"error": {}}, {"surprise_field": "x"}]:
            with self.assertRaises(ContractError):
                validate_record(row | changes, {"S001"})

    def test_optional_role_family_and_opaque_ids(self):
        row = {"posting_id": "0001-BE", "parse_status": "ok", "normalized_title": "Role",
               "skill_ids": [], "error": None, "role_family": None}
        validate_record(row, set())
        validate_record(row | {"role_family": "risk"}, set())
        self.assertEqual(row["posting_id"], "0001-BE")
        with self.assertRaises(ContractError):
            validate_record(row | {"role_family": "ALL"}, set())

    def test_json_duplicate_keys_are_rejected_recursively(self):
        with self.assertRaises(ContractError):
            decode('{"error":{"code":"a","code":"b"}}', "test")


if __name__ == "__main__":
    unittest.main()
