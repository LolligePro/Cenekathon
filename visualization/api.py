"""Functions the advisor layer calls. Each returns JSON-serializable data.

Every function returns a dict with:
    "summary": short text the LLM can quote,
    "data":    the underlying numbers,
    "chart":   a Vega-Lite spec the browser can render (None when not applicable).
"""

from visualization import analysis, charts
from visualization.data_source import get_source

DEFAULT_TOP_N = 5
MAX_SKILLS = 10
MAX_TOP_N = 25


def _check_n(n: int, limit: int) -> int:
    if not 1 <= n <= limit:
        raise ValueError(f"n must be between 1 and {limit}, got {n}")
    return n


def list_job_types() -> list[str]:
    return get_source().job_types()


def skill_trends(
    job_type: str,
    skills: list[str] | None = None,
    metric: str = "share",
    n: int = DEFAULT_TOP_N,
) -> dict:
    """Trend lines for the given skills, or for the top `n` skills when none are given.

    Explicit `skills` are capped at MAX_SKILLS so the chart stays readable.
    """
    source = get_source()
    if skills:
        if len(skills) > MAX_SKILLS:
            raise ValueError(f"Pass at most {MAX_SKILLS} skills, got {len(skills)}")
        selected = skills
    else:
        _check_n(n, MAX_SKILLS)
        selected = analysis.top_skills(source, job_type, n=n)["skill"].tolist()
    shares = analysis.skill_share_over_time(source, job_type, selected)
    chart = charts.skill_trend_chart(shares, job_type, metric)
    names = ", ".join(selected)
    return {
        "summary": f"Monthly {metric} for {names} in {job_type} postings.",
        "data": charts._records(shares),
        "chart": chart,
    }


def top_skills(
    job_type: str, n: int = 10, start: str | None = None, end: str | None = None
) -> dict:
    _check_n(n, MAX_TOP_N)
    top = analysis.top_skills(get_source(), job_type, n=n, start=start, end=end)
    return {
        "summary": f"Top {n} skills for {job_type} by average share of postings.",
        "data": charts._records(top),
        "chart": charts.top_skills_chart(top, job_type),
    }


def skill_trend(job_type: str, skill: str, window_months: int = 6) -> dict:
    result = analysis.trend(get_source(), job_type, skill, window_months)
    pct = (
        "n/a"
        if result["relative_change"] is None
        else f"{result['relative_change']:+.1%}"
    )
    return {
        "summary": (
            f"{skill} is {result['direction']} in {job_type} postings "
            f"({pct} over the last {window_months} months)."
        ),
        "data": result,
        "chart": None,
    }


def rising_and_falling_skills(
    job_type: str,
    window_months: int = 6,
    n: int = 5,
    min_share: float = 0.02,
) -> dict:
    """Biggest share movers in the latest window, ignoring skills below `min_share`."""
    _check_n(n, MAX_TOP_N)
    result = analysis.rising_and_falling(
        get_source(), job_type, window_months, n, min_share
    )
    return {
        "summary": f"Largest demand changes for {job_type} over the last {window_months} months.",
        "data": result,
        "chart": charts.change_chart(result, job_type, window_months),
    }
