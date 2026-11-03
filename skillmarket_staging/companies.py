"""Fast hackathon company normalization: rules + a small brand/group alias list.

This is intentionally a brand/group normalization, not legal entity resolution.
Only DISTINCT company strings are passed through Python; DuckDB joins the result.
"""

from __future__ import annotations

import glob
import hashlib
import html
import json
import re
import tempfile
import time
import unicodedata
from pathlib import Path

from .contracts import ContractError
from .db import WRITER_LOCK, _ready, connect, transaction

NORMALIZER_VERSION = "company-rules-v1"

# Matching is anchored at the beginning and uses word boundaries. Recruiters and
# job boards have their own labels; client-bank mentions are never inspected.
_ALIASES = [(re.compile(pattern), name) for pattern, name in [
    (r"^(?:bgl\s+)?bnp\s+paribas\b", "BNP Paribas"),
    (r"^ing\b", "ING"),
    (r"^abn\s+amro\b", "ABN AMRO"),
    (r"^barclays\b", "Barclays"),
    (r"^j\s*p\s*morgan\s*chase\b|^jpmorganchase\b|^j\s*p\s*morgan\b", "JPMorgan Chase"),
    (r"^willis\s+towers\s+watson\b|^wtw\b", "WTW"),
    (r"^natwest\b|^rbs\b|^royal\s+bank\s+of\s+scotland\b", "NatWest Group"),
    (r"^lloyds\b", "Lloyds Banking Group"),
    (r"^(?:banco\s+)?santander\b", "Santander"),
    (r"^hsbc\b", "HSBC"),
    (r"^rabobank\b", "Rabobank"),
    (r"^aviva\b", "Aviva"),
    (r"^axa\b", "AXA"),
    (r"^aon\b", "Aon"),
    (r"^citi\b|^citigroup\b|^citibank\b", "Citi"),
    (r"^fis\b", "FIS"),
    (r"^bny\b|^bank\s+of\s+new\s+york\s+mellon\b", "BNY"),
    (r"^marsh\s+(?:and\s+)?mclennan\b", "Marsh McLennan"),
    (r"^goldman\s+sachs\b", "Goldman Sachs"),
    (r"^deutsche\s+bank\b", "Deutsche Bank"),
    (r"^zurich\b", "Zurich"),
    (r"^nn\b|^nationale\s+nederlanden\b", "NN Group"),
    (r"^legal\s+and\s+general\b", "Legal & General"),
    (r"^yource\b", "Yource"),
    (r"^randstad\b", "Randstad"),
    (r"^adecco\b|^the\s+adecco\s+group\b", "Adecco"),
    (r"^hays\b", "Hays"),
    (r"^robert\s+half\b", "Robert Half"),
    (r"^michael\s+page\b", "Michael Page"),
    (r"^talent\s+and\s+pro\b|^talentpro\b", "Talent&Pro"),
    (r"^jobs\s+via\s+efinancialcareers\b|^efinancialcareers\b", "eFinancialCareers"),
]]
_SUFFIX = re.compile(r"(?:\s+(?:limited|ltd|plc|llp|inc|incorporated|corp|corporation|bv|nv|sa|sarl|gmbh|ag|b v|n v|s a|s a r l|n a))+$")
_CAREERS = re.compile(r"\s+(?:(?:external\s+)?careers?\s+site|careers|banen|recruitment)$")
_UNKNOWN = {"", "unknown", "confidential", "not disclosed", "not provided", "n a", "none", "null"}


def normalize_company_name(value: str | None) -> str | None:
    return normalize_company(value)["company_normalized"]


def normalize_company(value: str | None) -> dict:
    if value is not None and not isinstance(value, str):
        raise ContractError("company must be a string or null")
    key = unicodedata.normalize("NFKC", html.unescape(value or "")).casefold()
    key = re.sub(r"\s+", " ", key).strip()
    key = re.sub(r"[^\w\s]", " ", key.replace("&", " and "))
    key = re.sub(r"\s+", " ", key).strip()
    if key in _UNKNOWN:
        canonical, method = None, "unknown"
    else:
        key = re.sub(r"^logo\s+", "", key)
        previous = None
        while previous != key:
            previous = key
            key = _SUFFIX.sub("", _CAREERS.sub("", key)).strip()
        canonical, method = key.title(), "rules"
        for pattern, label in _ALIASES:
            if pattern.search(key):
                canonical, method = label, "group_alias"
                break
    company_id = "co_" + hashlib.sha256(canonical.casefold().encode()).hexdigest()[:16] if canonical else None
    return {"company_raw": value, "company_normalized": canonical, "company_id": company_id,
            "normalization_method": method, "normalizer_version": NORMALIZER_VERSION}


def normalize_companies(database, sources, *, output="data/company_map.parquet", write_postings=None) -> dict:
    started = time.perf_counter()
    inputs = sorted({str(Path(p).resolve()) for source in sources for p in glob.glob(str(source))})
    if not inputs or any(not Path(p).is_file() or Path(p).suffix.lower() != ".parquet" for p in inputs):
        raise ContractError("Provide existing Parquet source files (glob patterns are supported)")
    outputs = [Path(p).resolve() for p in [output, write_postings] if p is not None]
    if len(set(outputs)) != len(outputs) or any(str(p) in inputs or p == Path(database).resolve() for p in outputs):
        raise ContractError("Output paths must differ from each other, the database and all source files")
    with WRITER_LOCK, connect(database) as conn:
        _ready(conn)
        if not conn.execute("""SELECT count(*) FROM information_schema.tables
            WHERE table_schema='skillmarket_staging' AND table_name='company_names'""").fetchone()[0]:
            raise ContractError("Run init to apply the company-normalization migration")
        conn.execute("""CREATE TEMP TABLE company_counts AS
            SELECT company AS company_raw, count(*) AS occurrences
            FROM read_parquet(?, union_by_name=true) GROUP BY company""", [inputs])
        names = conn.execute("SELECT company_raw FROM company_counts").fetchall()
        mappings = [normalize_company(r[0]) for r in names]
        with tempfile.TemporaryDirectory(prefix="company-map-") as temp:
            path = Path(temp) / "mappings.jsonl"
            with path.open("w", encoding="utf-8") as stream:
                for row in mappings:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            with transaction(conn):
                conn.execute("""INSERT INTO skillmarket_staging.company_names
                    SELECT * FROM read_json(?, format='newline_delimited', columns={
                        company_raw:'VARCHAR', company_normalized:'VARCHAR', company_id:'VARCHAR',
                        normalization_method:'VARCHAR', normalizer_version:'VARCHAR'})
                    WHERE company_raw IS NOT NULL
                    ON CONFLICT (company_raw) DO UPDATE SET
                        company_normalized=excluded.company_normalized, company_id=excluded.company_id,
                        normalization_method=excluded.normalization_method,
                        normalizer_version=excluded.normalizer_version""", [str(path)])
        for p in outputs:
            p.parent.mkdir(parents=True, exist_ok=True)
        conn.execute("""COPY (
            SELECT c.company_raw, m.company_normalized, m.company_id,
                coalesce(m.normalization_method,'unknown') AS normalization_method,
                coalesce(m.normalizer_version,$normalizer) AS normalizer_version, c.occurrences
            FROM company_counts c LEFT JOIN skillmarket_staging.company_names m USING (company_raw)
            ORDER BY c.occurrences DESC, c.company_raw
        ) TO $destination (FORMAT PARQUET, COMPRESSION ZSTD)""", {"normalizer":NORMALIZER_VERSION,"destination":str(Path(output).resolve())})
        if write_postings:
            columns = {r[0] for r in conn.execute("DESCRIBE SELECT * FROM read_parquet(?, union_by_name=true)", [inputs]).fetchall()}
            id_column = "vacancy_id" if "vacancy_id" in columns else "posting_id" if "posting_id" in columns else None
            if id_column is None:
                raise ContractError("Per-posting output requires vacancy_id or posting_id")
            # id_column is selected from two fixed literals, not arbitrary SQL input.
            conn.execute(f"""COPY (
                SELECT p.{id_column} AS posting_id, p.company AS company_raw,
                    m.company_normalized, m.company_id,
                    coalesce(m.normalization_method,'unknown') AS normalization_method,
                    coalesce(m.normalizer_version,$normalizer) AS normalizer_version
                FROM read_parquet($sources, union_by_name=true) p
                LEFT JOIN skillmarket_staging.company_names m ON m.company_raw=p.company
            ) TO $destination (FORMAT PARQUET, COMPRESSION ZSTD)""",
                         {"normalizer":NORMALIZER_VERSION,"sources":inputs,"destination":str(Path(write_postings).resolve())})
        row_count = conn.execute("SELECT sum(occurrences) FROM company_counts").fetchone()[0] or 0
    return {"source_files": len(inputs), "postings": row_count, "raw_names": len(mappings),
            "normalized_companies": len({r["company_id"] for r in mappings if r["company_id"]}),
            "normalizer_version": NORMALIZER_VERSION, "seconds": round(time.perf_counter()-started, 3),
            "output_map": str(Path(output).resolve()),
            "output_postings": str(Path(write_postings).resolve()) if write_postings else None}
