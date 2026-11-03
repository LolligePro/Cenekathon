"""Pure analysis functions over skill demand. No plotting or I/O here."""

import pandas as pd

from visualization.data_source import DataSource

# Relative changes smaller than this (0.5%) count as flat, not rising or falling.
FLAT_BAND = 0.005


def _job_slice(source: DataSource, job_type: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    demand = source.skill_demand()
    totals = source.monthly_totals()
    demand = demand[demand["job_type"] == job_type]
    totals = totals[totals["job_type"] == job_type]
    if demand.empty:
        raise ValueError(f"No data for job type {job_type!r}")
    return demand, totals


def skill_share_over_time(
    source: DataSource, job_type: str, skills: list[str] | None = None
) -> pd.DataFrame:
    """Columns: month, skill, postings, total_postings, share (postings / total_postings)."""
    demand, totals = _job_slice(source, job_type)
    if skills is not None:
        demand = demand[demand["skill"].isin(skills)]
    merged = demand.merge(totals[["month", "total_postings"]], on="month", how="left")
    merged["share"] = merged["postings"] / merged["total_postings"]
    return merged.sort_values(["skill", "month"]).reset_index(drop=True)


def top_skills(
    source: DataSource, job_type: str, n: int = 10, start=None, end=None
) -> pd.DataFrame:
    """Top-n skills by average share over the period. Columns: skill, avg_share, postings."""
    shares = skill_share_over_time(source, job_type)
    shares = _filter_period(shares, start, end)
    grouped = shares.groupby("skill").agg(
        avg_share=("share", "mean"), postings=("postings", "sum")
    )
    return grouped.sort_values("avg_share", ascending=False).head(n).reset_index()


def trend(
    source: DataSource, job_type: str, skill: str, window_months: int = 6
) -> dict:
    """Compare the latest `window_months` with the same-length window before it.

    Returns a dict with start/end share averages and relative change, so the advisor
    can phrase the result. `relative_change` is None when the earlier share is 0.
    """
    shares = skill_share_over_time(source, job_type, skills=[skill]).sort_values(
        "month"
    )
    if shares.empty:
        raise ValueError(f"No data for skill {skill!r} in {job_type!r}")
    recent = shares.tail(window_months)
    earlier = shares.iloc[-2 * window_months : -window_months]
    if earlier.empty:
        raise ValueError("Not enough history for the requested window")
    before = float(earlier["share"].mean())
    after = float(recent["share"].mean())
    if pd.isna(before) or pd.isna(after):
        raise ValueError("No observed vacancies in one of the trend windows")
    rel = None if before == 0 else (after - before) / before
    if rel is None or abs(rel) < FLAT_BAND:
        direction = "flat"
    else:
        direction = "rising" if rel > 0 else "falling"
    return {
        "job_type": job_type,
        "skill": skill,
        "window_months": window_months,
        "share_before": before,
        "share_after": after,
        "relative_change": rel,
        "direction": direction,
    }


def rising_and_falling(
    source: DataSource,
    job_type: str,
    window_months: int = 6,
    n: int = 5,
    min_share: float = 0.0,
) -> dict:
    """Skills with the largest relative share change over the latest window.

    Skills whose recent share is below `min_share` are ignored: a small base makes
    relative changes swing wildly and drowns out the meaningful movers.
    """
    rows = []
    for skill in source.skill_demand().query("job_type == @job_type")["skill"].unique():
        try:
            rows.append(trend(source, job_type, skill, window_months))
        except ValueError:
            continue
    df = pd.DataFrame(rows)
    if df.empty:
        return {"rising": [], "falling": []}
    df = df.dropna(subset=["relative_change"])
    df = df[df["share_after"] >= min_share]
    rising = df[df["direction"] == "rising"].sort_values("relative_change", ascending=False)
    falling = df[df["direction"] == "falling"].sort_values("relative_change")
    return {
        "rising": rising.head(n).to_dict("records"),
        "falling": falling.head(n).to_dict("records"),
    }


def _filter_period(df: pd.DataFrame, start, end) -> pd.DataFrame:
    if start is not None:
        df = df[df["month"] >= pd.Timestamp(start)]
    if end is not None:
        df = df[df["month"] <= pd.Timestamp(end)]
    return df
