"""Functions the advisor layer calls. Each returns JSON-serializable data.

Every function returns a dict with:
    "summary": short text the LLM can quote,
    "data":    the underlying numbers,
    "chart":   a Vega-Lite spec the browser can render (None when not applicable).
"""

from visualization import analysis, charts
from visualization.data_source import get_source as _default_source
from contextlib import contextmanager
from contextvars import ContextVar

_SOURCE = ContextVar("visualization_source", default=None)


def get_source():
    source = _SOURCE.get()
    return source if source is not None else _default_source()


@contextmanager
def use_source(source):
    token = _SOURCE.set(source)
    try:
        yield
    finally:
        _SOURCE.reset(token)

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


def job_type_skills(job_type: str, n: int = 15) -> dict:
    """Skill profile of one job type: the n most demanded skills and their share.

    Use this when the user asks which skills a job type needs. `job_type` must be
    one of `list_job_types()`.
    """
    _check_n(n, MAX_TOP_N)
    if job_type not in list_job_types():
        raise ValueError(f"Unknown job type {job_type!r}; see list_job_types()")
    source = get_source()
    top = analysis.top_skills(source, job_type, n=n)
    totals = source.monthly_totals()
    total = int(totals.loc[totals["job_type"] == job_type, "total_postings"].sum())
    return {
        "summary": (
            f"Top {len(top)} skills for {job_type}, out of {total:,} postings "
            "over the full period."
        ),
        "data": top.to_dict("records"),
        "chart": charts.top_skills_chart(top, job_type),
    }


def significant_risers(
    job_type: str,
    window_months: int = 6,
    n: int = 10,
    min_share: float = 0.02,
) -> dict:
    """Skills whose recent share rose beyond their normal month-to-month noise.

    Prefer this over `rising_and_falling_skills` when the user wants the skills that
    are genuinely gaining ground: generic skills mentioned steadily every month (for
    example "team player") are filtered out because they don't move beyond noise.
    """
    _check_n(n, MAX_TOP_N)
    risers = analysis.significant_risers(
        get_source(), job_type, window_months, n, min_share
    )
    return {
        "summary": (
            f"Skills for {job_type} rising beyond normal noise over the last "
            f"{window_months} months."
        ),
        "data": risers.to_dict("records"),
        "chart": charts.risers_chart(risers, job_type, window_months),
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


def career_path(from_job: str, to_job: str, n_skills: int = 8, max_courses: int = 5) -> dict:
    """Advice for moving from one job type to another: skills to learn and courses to take.

    Both jobs must be in `list_job_types()`. `data` has `skills_to_learn` (the gap) and
    `courses` (a short list covering the most gap, in recommended order, with links).
    `chart` shows the skill gap; `courses_chart` shows the courses (None when none match).
    """
    _check_n(n_skills, MAX_TOP_N)
    _check_n(max_courses, MAX_SKILLS)
    known = list_job_types()
    for job in (from_job, to_job):
        if job not in known:
            raise ValueError(f"Unknown job type {job!r}; see list_job_types()")
    if from_job == to_job:
        raise ValueError("from_job and to_job must differ")
    return _career_path(get_source(), from_job, to_job, n_skills, max_courses)


def _career_path(source, from_job, to_job, n_skills=8, max_courses=5):
    gap = analysis.skill_gap(source, from_job, to_job, n=n_skills)
    courses = source.courses()
    plan = analysis.recommend_courses(gap, courses, max_courses)
    available = set(courses["skill"])
    unavailable = sorted(set(gap["skill"]) - available)
    unselected = sorted(set(gap["skill"]) - {s for sk in plan["skills"] for s in sk})
    if gap.empty:
        summary = f"{to_job} asks for no skills more often than {from_job}."
    elif plan.empty:
        summary = f"To move from {from_job} to {to_job}, learn {', '.join(gap['skill'])}. No matching courses found."
    else:
        left = sorted(set(gap["skill"]) - {s for sk in plan["skills"] for s in sk})
        summary = (
            f"To move from {from_job} to {to_job}, close {len(gap)} skill gaps; "
            f"{len(plan)} course(s) cover {len(gap) - len(left)} of them, starting with {plan['title'][0]}."
            + (f" Not covered by the selected plan: {', '.join(left)}." if left else "")
        )
    return {
        "summary": summary,
        "data": {"skills_to_learn": gap.to_dict("records"), "courses": plan.to_dict("records"),
                 "skills_without_courses": unavailable, "skills_not_covered_by_plan": unselected},
        "chart": charts.skill_gap_chart(gap, plan, from_job, to_job) if not gap.empty else None,
        "courses_chart": charts.course_plan_chart(plan, from_job, to_job) if not plan.empty else None,
    }
