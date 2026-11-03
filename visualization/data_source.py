"""Data access for the visualization layer.

This is the ONLY module that knows where data comes from. Today it reads the
synthetic stub CSVs. When the database team is ready, implement DatabaseSource
against their schema and change `get_source()`; nothing else needs to change.

Expected contract (what every source must return):
    skill_demand: DataFrame[job_type, month (datetime64), skill, postings (int)]
        one row per (job_type, month, skill); postings = vacancies in that month
        that mention the skill.
    monthly_totals: DataFrame[job_type, month (datetime64), total_postings (int)]
        one row per (job_type, month).
"""

from pathlib import Path
import os
from typing import Protocol

import pandas as pd
import duckdb

STUB_DIR = Path(__file__).parent / "stub"


class DataSource(Protocol):
    def skill_demand(self) -> pd.DataFrame: ...
    def monthly_totals(self) -> pd.DataFrame: ...
    def job_types(self) -> list[str]: ...


class StubSource:
    """Reads the synthetic CSVs in visualization/stub/."""

    def __init__(self, stub_dir: Path = STUB_DIR):
        self._demand = self._load(stub_dir / "skill_demand.csv")
        self._totals = self._load(stub_dir / "monthly_totals.csv")

    @staticmethod
    def _load(path: Path) -> pd.DataFrame:
        df = pd.read_csv(path, parse_dates=["month"])
        return df

    def skill_demand(self) -> pd.DataFrame:
        return self._demand.copy()

    def monthly_totals(self) -> pd.DataFrame:
        return self._totals.copy()

    def job_types(self) -> list[str]:
        return sorted(self._demand["job_type"].unique().tolist())


class DatabaseSource:
    """Read the DB team's preaggregated tables, cached until the file changes."""

    def __init__(self, database=None):
        default = Path(__file__).resolve().parents[1] / "data" / "skillmarket.duckdb"
        self.database = Path(database or os.environ.get("SKILLMARKET_DB", default))
        self._stamp = None

    def _load(self):
        stat = self.database.stat()
        stamp = (stat.st_mtime_ns,stat.st_size)
        if stamp == self._stamp:
            return
        with duckdb.connect(str(self.database),read_only=True) as conn:
            self._demand = conn.execute("SELECT job_type,month,skill,postings FROM skillmarket_app.skill_demand ORDER BY job_type,month,skill").df()
            self._totals = conn.execute("SELECT job_type,month,total_postings FROM skillmarket_app.monthly_totals ORDER BY job_type,month").df()
        self._demand["month"] = pd.to_datetime(self._demand["month"])
        self._totals["month"] = pd.to_datetime(self._totals["month"])
        self._stamp = stamp

    def skill_demand(self):
        self._load()
        return self._demand.copy()

    def monthly_totals(self):
        self._load()
        return self._totals.copy()

    def job_types(self):
        self._load()
        return sorted(self._totals["job_type"].unique().tolist())


_source: DataSource | None = None


def get_source() -> DataSource:
    """Auto uses the real database once initialized; stub is an explicit fallback."""
    global _source
    if _source is None:
        mode = os.environ.get("SKILLMARKET_SOURCE", "auto")
        database = Path(os.environ.get("SKILLMARKET_DB",Path(__file__).resolve().parents[1]/"data"/"skillmarket.duckdb"))
        if mode not in {"auto","duckdb","stub"}:
            raise ValueError("SKILLMARKET_SOURCE must be auto, duckdb or stub")
        ready = False
        if mode == "auto" and database.exists():
            with duckdb.connect(str(database),read_only=True) as conn:
                ready = bool(conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='skillmarket_app' AND table_name='monthly_totals'").fetchone()[0])
        _source = DatabaseSource(database) if mode == "duckdb" or ready else StubSource()
    return _source
