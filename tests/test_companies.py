import tempfile
import unittest
from pathlib import Path

import duckdb

from skillmarket_staging.companies import normalize_company, normalize_company_name, normalize_companies
from skillmarket_staging.contracts import ContractError
from skillmarket_staging.db import initialize


class CompanyTests(unittest.TestCase):
    def test_basic_cleanup_and_legal_forms(self):
        self.assertEqual(normalize_company_name("  Example\u00a0  Company B.V. "), "Example Company")
        self.assertEqual(normalize_company_name("EXAMPLE COMPANY bv"), "Example Company")
        self.assertEqual(normalize_company_name("Talent&amp;Pro"), "Talent&Pro")
        self.assertEqual(normalize_company_name("logo ABN AMRO"), "ABN AMRO")
        self.assertEqual(normalize_company_name("WTW External Careers Site"), "WTW")

    def test_brand_aliases(self):
        self.assertEqual({normalize_company_name(n) for n in ["ING", "ing", "ING Netherlands Banen", "ING Luxembourg S.A."]}, {"ING"})
        self.assertEqual(normalize_company_name("JPMorganChase"), "JPMorgan Chase")
        self.assertEqual(normalize_company_name("JP Morgan Chase & Co."), "JPMorgan Chase")
        self.assertEqual(normalize_company_name("Willis Towers Watson"), "WTW")

    def test_recruiters_are_kept_and_short_substrings_do_not_match(self):
        self.assertEqual(normalize_company_name("Yource Group"), "Yource")
        self.assertEqual(normalize_company_name("Randstad Nederland"), "Randstad")
        self.assertEqual(normalize_company_name("eFinancialCareers"), "eFinancialCareers")
        self.assertNotEqual(normalize_company_name("Baring"), "ING")
        self.assertNotEqual(normalize_company_name("Fisher Investments"), "FIS")

    def test_raw_names_and_stable_ids(self):
        value = "  Barclays PLC  "
        out = normalize_company(value)
        self.assertEqual(out["company_raw"], value)
        self.assertEqual(out["company_id"], normalize_company("BARCLAYS")["company_id"])
        self.assertEqual(out["normalization_method"], "group_alias")
        self.assertIsNone(normalize_company_name(None))
        self.assertIsNone(normalize_company_name("Confidential"))

    def test_normalization_is_idempotent(self):
        for name in ["ABN AMRO Bank N.V.", "Lloyds Banking Group", "WTW External Careers Site", "Example B.V.", "Unknown"]:
            once = normalize_company_name(name)
            self.assertEqual(normalize_company_name(once), once)

    def test_parquet_pipeline_and_repeated_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database, source = root / "data.duckdb", root / "input.parquet"
            output, postings = root / "map.parquet", root / "posting-map.parquet"
            initialize(database)
            with duckdb.connect() as conn:
                conn.execute("COPY (SELECT * FROM (VALUES ('a','ING Netherlands'),('b','ing'),('c','Yource Group'),('d',NULL)) t(vacancy_id,company)) TO ? (FORMAT PARQUET)", [str(source)])
            result = normalize_companies(database, [source], output=output, write_postings=postings)
            self.assertEqual(result["postings"], 4)
            self.assertEqual(result["raw_names"], 4)
            self.assertEqual(result["normalized_companies"], 2)
            with duckdb.connect() as conn:
                rows = conn.execute("SELECT posting_id,company_raw,company_normalized FROM read_parquet(?) ORDER BY posting_id", [str(postings)]).fetchall()
            self.assertEqual(rows, [("a", "ING Netherlands", "ING"), ("b", "ing", "ING"), ("c", "Yource Group", "Yource"), ("d", None, None)])
            normalize_companies(database, [source], output=output)
            with duckdb.connect(str(database)) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM skillmarket_staging.company_names").fetchone()[0], 3)
            with self.assertRaises(ContractError):
                normalize_companies(database, [source], output=source)


if __name__ == "__main__":
    unittest.main()
