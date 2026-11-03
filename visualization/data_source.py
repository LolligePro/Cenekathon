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
from typing import Protocol

import pandas as pd

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


_source: DataSource | None = None


def get_source() -> DataSource:
    """Return the active data source. Swap StubSource for the DB-backed source here."""
    global _source
    if _source is None:
        _source = StubSource()
    return _source
