"""Skill Market AI layer: answers questions with an LLM, using only aggregated tables.

The model never sees raw postings. It can only call the tools below, and each tool
runs a fixed, parameterised, read-only DuckDB query over the team's pre-aggregated
tables in the skillmarket_app schema (skill_demand, monthly_totals, skills).
"""

import copy
import json
import math
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import anthropic
import pandas as pd

from skillmarket_staging.talent import find_internal_talent, format_internal_talent

from skillmarket_staging import learning, learning_budget
from skillmarket_staging.access import read_queries

from visualization import analysis, api as viz, charts as viz_charts
from visualization.data_source import get_source

DB_PATH = Path(os.environ.get("SKILLMARKET_DB", str(Path(__file__).resolve().parent / "data" / "skillmarket.duckdb")))
MODEL = os.environ.get("SKILLMARKET_MODEL", "claude-haiku-5-5-20251001")
USAGE_LOG = Path("usage_log.jsonl")
MAX_TOKENS = int(os.environ.get("SKILLMARKET_MAX_TOKENS", "4000"))  # Qwen3 thinking uses many tokens
MAX_TOOL_ROUNDS = 6
MAX_ROWS = 200
MAX_SKILLS_PER_QUERY = 5
MOVER_MIN_SHARE = 0.02  # ignore tiny bases; relative changes on them are noisy
# Jan-Apr 2026 collection was incomplete (see docs/database.md), so the default
# window ends before it. Pass end_month explicitly to look at later months.
DEFAULT_END_MONTH = "2025-12"
JOB_TYPES = [
    "customer service and support", "finance and accounting", "human resources",
    "legal", "operations", "other", "product and project management",
    "risk and compliance", "sales and marketing", "technology",
]

SYSTEM_PROMPT = """You are the Skill Market assistant. You help HR teams, jobseekers and students in finance decide which skills to learn, based on job postings in Belgium, the Netherlands, Luxembourg and the UK.

Rules:
- For learning-hour budgets or comparing skill bundles, call plan_learning_budget with the known skills and hours_budget. Reuse the preceding profile for follow-ups. It compares up to two new skills and returns real, deduplicated extra full skill matches, courses and explicit demo learning-hour assumptions. Money-only budgets require a learning-hour budget first.
- Only state numbers that came from tool results. Never invent or estimate numbers.
- For internal talent, employee reskilling, or who could become an AI auditor, call find_internal_talent directly. This is a synthetic HR demo with the fixed ai-auditor role. Use profile_id for follow-up questions about one candidate; do not call profile_advice for this use case.
- For internal talent answers, explicitly say the profiles and target role are fictional. Show the best candidate's matched_count/required_count and match_percentage as skill overlap, never suitability or hiring probability. Explain missing skills, include actual recommended course titles and URLs, and explicitly mention skills_without_courses. Do not claim a course covers a skill absent from its covered_skills. Suggest HR review and training as the next action.
- For course questions ALWAYS use recommend_courses with learning targets, profile_advice with already-known skills, or career_path for a role transition. Never answer course availability from memory. Reuse skills from prior tool results for follow-ups such as "courses hiervoor". Report unrecognized skills separately from known skills without courses. Only recommend URLs returned by these tools.
- Resolve skill names with find_skills first for other market questions. Names, synonyms, ML/AI and skill IDs are resolved by the backend.
- Use top_skills for the most demanded skills in a job type, skill_trend for one skill's direction, and rising_and_falling_skills for the biggest movers.
- Visualization tools draw their own charts. Call make_chart only for an additional comparison not drawn by a tool.
- Show shares as percentages (0.12 -> 12%). Share is the fraction of a job type's postings in that month that mention the skill.
- For market questions, mention the limits in your answer: postings are not hires; the data covers finance only; data collection was incomplete from January to April 2026, so drops in those months reflect collection, not hiring.
- Keep market answers to at most 5 sentences, plus one concrete piece of advice that includes a number. For internal talent, use a short ranking and explain the best candidate with course links and uncovered skills. End with a concrete next action.
"""


def _tool_error(message):
    return {"error": message}


class _Data:
    """Open each query read-only so database replacement cannot leave stale handles."""

    def __init__(self, db_path):
        self.database = db_path

    def query(self, sql, params, limit=MAX_ROWS):
        with read_queries(self.database) as query:
            rows = query(sql, params, limit=limit)
        return [{key: (value.isoformat() if hasattr(value, "isoformat") else value)
                 for key, value in row.items()} for row in rows]


_DATA = None


def _data():
    global _DATA
    if _DATA is None:
        _DATA = _Data(DB_PATH)
    return _DATA


# ---------- tools ----------

def _check_job_type(job_type):
    if job_type in (None, "", "ALL"):
        return None
    if job_type not in JOB_TYPES:
        raise ValueError(f"job_type must be ALL or one of {JOB_TYPES}")
    return job_type


def _check_month(value, default):
    if value in (None, "", "latest"):
        return default
    if not re.fullmatch(r"\d{4}-\d{2}", str(value)):
        raise ValueError("month must look like YYYY-MM, e.g. 2025-12")
    return f"{value}-01"


def find_skills(text):
    return learning.find_skills(text)


def get_skill_share(skills, job_type="ALL", start_month="2021-01", end_month=None):
    """Monthly share (postings / total postings) for up to 5 skill names."""
    if not isinstance(skills, list) or not 1 <= len(skills) <= MAX_SKILLS_PER_QUERY:
        return _tool_error(f"skills must be a list of 1 to {MAX_SKILLS_PER_QUERY} skill names")
    skills = learning.canonical_names(skills)
    job_type = _check_job_type(job_type)
    start = _check_month(start_month, "2021-01-01")
    end = _check_month(end_month, f"{DEFAULT_END_MONTH}-01")
    placeholders = ", ".join("?" for _ in skills)
    rows = _data().query(
        f"""
        WITH tot AS (
            SELECT month, SUM(total_postings) AS total
            FROM skillmarket_app.monthly_totals
            WHERE (? IS NULL OR job_type = ?)
            GROUP BY month
        ), dem AS (
            SELECT skill, month, SUM(postings) AS postings
            FROM skillmarket_app.skill_demand
            WHERE skill IN ({placeholders}) AND (? IS NULL OR job_type = ?)
            GROUP BY skill, month
        )
        SELECT d.skill, strftime(d.month, '%Y-%m') AS month,
               d.postings, t.total AS total_postings,
               ROUND(d.postings::DOUBLE / t.total, 4) AS share
        FROM dem d JOIN tot t USING (month)
        WHERE d.month BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
        ORDER BY d.skill, d.month
        """,
        [job_type, job_type, *skills, job_type, job_type, start, end],
        limit=MAX_ROWS,
    )
    return {"rows": rows, "job_type": job_type or "ALL", "start_month": start[:7], "end_month": end[:7]}


def top_movers(direction, job_type="ALL", window_months=6, n=10, end_month=None):
    """Skills whose average share changed most: last window vs the window before it."""
    if direction not in ("up", "down"):
        return _tool_error("direction must be 'up' or 'down'")
    job_type = _check_job_type(job_type)
    if not isinstance(window_months, int) or not 1 <= window_months <= 24:
        return _tool_error("window_months must be an integer from 1 to 24")
    if not isinstance(n, int) or not 1 <= n <= 20:
        return _tool_error("n must be an integer from 1 to 20")
    end = _check_month(end_month, f"{DEFAULT_END_MONTH}-01")
    order = "DESC" if direction == "up" else "ASC"
    rows = _data().query(
        f"""
        WITH tot AS (
            SELECT month, SUM(total_postings) AS total
            FROM skillmarket_app.monthly_totals
            WHERE (? IS NULL OR job_type = ?)
            GROUP BY month
        ), dem AS (
            SELECT skill, month, SUM(postings) AS postings
            FROM skillmarket_app.skill_demand
            WHERE (? IS NULL OR job_type = ?)
            GROUP BY skill, month
        ), mon AS (
            SELECT d.skill, d.month, d.postings::DOUBLE / t.total AS share
            FROM dem d JOIN tot t USING (month)
            WHERE d.month <= CAST(? AS DATE)
        ), win AS (
            SELECT skill,
                   AVG(share) FILTER (WHERE month > CAST(? AS DATE) - INTERVAL (? * 1) MONTH) AS share_recent,
                   AVG(share) FILTER (WHERE month > CAST(? AS DATE) - INTERVAL (? * 2) MONTH
                                        AND month <= CAST(? AS DATE) - INTERVAL (? * 1) MONTH) AS share_before
            FROM mon GROUP BY skill
        )
        SELECT skill,
               ROUND(share_recent, 4) AS share_recent,
               ROUND(share_before, 4) AS share_before,
               ROUND((share_recent - share_before) / share_before, 3) AS relative_change
        FROM win
        WHERE share_recent >= ? AND share_before > 0
        ORDER BY relative_change {order}
        LIMIT {n}
        """,
        [job_type, job_type, job_type, job_type, end,
         end, window_months, end, window_months, end, window_months,
         MOVER_MIN_SHARE],
    )
    return {"movers": rows, "job_type": job_type or "ALL", "window_months": window_months, "end_month": end[:7]}


def profile_advice(skills, country="ALL", job_type="ALL"):
    return learning.profile_advice(skills, country, job_type)


def recommend_courses(skills, max_courses=5):
    return learning.recommend_courses(skills, max_courses)








class _CappedSource:
    """Wraps the data source so analysis functions ignore months after end_month."""

    def __init__(self, source, end_month):
        self._source = source
        self._end = pd.Timestamp(end_month)

    def skill_demand(self):
        df = self._source.skill_demand()
        return df[df["month"] <= self._end]

    def monthly_totals(self):
        df = self._source.monthly_totals()
        return df[df["month"] <= self._end]

    def job_types(self):
        return self._source.job_types()

    def courses(self):
        return self._source.courses()


def _viz_source(end_month):
    return _CappedSource(get_source(), _check_month(end_month, f"{DEFAULT_END_MONTH}-01"))


def _require_job_type(job_type):
    if job_type not in JOB_TYPES:
        raise ValueError(f"job_type must be one of {JOB_TYPES}")
    return job_type


def _native(value):
    return value.item() if hasattr(value, "item") else str(value)


def _plain(obj):
    """Converts pandas and numpy values into JSON-safe Python values."""
    def clean(value):
        if isinstance(value, float) and not math.isfinite(value): return None
        if isinstance(value, dict): return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list): return [clean(item) for item in value]
        return value
    return clean(json.loads(json.dumps(obj, default=_native)))


VIZ_FUNCTIONS = {
    "skill_trends": viz.skill_trends,
    "top_skills": viz.top_skills,
    "skill_trend": viz.skill_trend,
    "rising_and_falling_skills": viz.rising_and_falling_skills,
    "significant_risers": viz.significant_risers,
    "career_path": viz.career_path,
}


def _run_viz_tool(name, args, charts):
    args = dict(args)
    end_month = args.pop("end_month", None)
    if args.get("skills"):
        args["skills"] = learning.canonical_names(args["skills"])
    if args.get("skill"):
        args["skill"] = learning.canonical_names([args["skill"]])[0]
    with viz.use_source(_viz_source(end_month)):
        result = VIZ_FUNCTIONS[name](**args)
    for key in ("chart", "courses_chart"):
        if result.get(key):
            charts.append(result[key])
    data = result["data"]
    if name == "skill_trends":
        # The full time series is in the chart; the model needs only a small summary.
        frame = pd.DataFrame(data)
        data = [{"skill": skill, "first_share": rows.iloc[0]["share"],
                 "last_share": rows.iloc[-1]["share"]}
                for skill, rows in frame.dropna(subset=["share"]).groupby("skill")]
    return _plain({"summary": result["summary"], "data": data})


def validate_chart_spec(spec):
    """Checks a chart spec against the contract in idea.md. Returns an error string or None."""
    if not isinstance(spec, dict):
        return "spec must be an object"
    if spec.get("type") not in ("line", "bar"):
        return "type must be 'line' or 'bar'"
    if not isinstance(spec.get("title"), str) or not isinstance(spec.get("y_label"), str):
        return "title and y_label must be strings"
    series = spec.get("series")
    if not isinstance(series, list) or not 1 <= len(series) <= 6:
        return "series must be a list of 1 to 6 items"
    for s in series:
        if not isinstance(s, dict) or not isinstance(s.get("name"), str):
            return "each series needs a string name"
        points = s.get("points")
        if not isinstance(points, list) or not 1 <= len(points) <= MAX_ROWS:
            return f"each series needs 1 to {MAX_ROWS} points"
        for p in points:
            if not (isinstance(p, list) and len(p) == 2 and isinstance(p[0], str)
                    and isinstance(p[1], (int, float)) and not isinstance(p[1], bool)):
                return "each point must be [x_label string, y_value number]"
    return None


def make_chart(spec, charts):
    error = validate_chart_spec(spec)
    if error:
        return _tool_error(error)
    charts.append(spec)
    return {"ok": True, "charts_so_far": len(charts)}


# ---------- tool definitions for the API ----------

TOOLS = [
    {'name': 'plan_learning_budget',
     'description': 'Compare learning packages of one or two skills within an hour budget. Pass skills already known, '
                    'or an empty list for a beginner. Counts actual extra fully matched vacancies once per package; '
                    'returns a combination bonus, course links and demo learning hours. Defaults to 20 hours per new skill; '
                    'skill_hours can override hours per skill. Reuse known skills and country/job_type from prior results.',
     'input_schema': {'type': 'object', 'properties': {
         'skills': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 20},
         'hours_budget': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 10000},
         'country': {'type': 'string', 'enum': ['ALL', 'BE', 'NL', 'LU', 'GB']},
         'job_type': {'type': 'string', 'enum': ['ALL', *JOB_TYPES]},
         'skill_hours': {'type': 'object', 'additionalProperties': {'type': 'number', 'exclusiveMinimum': 0}}},
         'required': ['skills', 'hours_budget'], 'additionalProperties': False}},
    {'name': 'significant_risers',
     'description': 'Draw skills rising beyond usual month-to-month noise in a job group. A '
                    'descriptive ranking, not statistical proof.',
     'input_schema': {'type': 'object',
                      'properties': {'job_type': {'type': 'string'},
                                     'window_months': {'type': 'integer', 'minimum': 1, 'maximum': 12},
                                     'n': {'type': 'integer', 'minimum': 1, 'maximum': 25}},
                      'required': ['job_type']}},
    {'name': 'recommend_courses',
     'description': 'Find actual catalog courses for skills the user WANTS TO LEARN. Accept names, synonyms, '
                    'ML/AI or fixed skill IDs. Returns links and exact skill coverage; reports unknown skills '
                    'and skills without courses. Use profile_advice for skills the user already knows.',
     'input_schema': {'type': 'object',
                      'properties': {'skills': {'type': 'array',
                                                'items': {'type': 'string'},
                                                'minItems': 1,
                                                'maxItems': 20},
                                     'max_courses': {'type': 'integer', 'minimum': 1, 'maximum': 10}},
                      'required': ['skills'],
                      'additionalProperties': False}},
    {'name': 'career_path',
     'description': 'Compare two supported job groups, compute skill gaps, and recommend actual courses across '
                    'all platforms, with gap and course charts. Maps broad job groups, not precise occupations.',
     'input_schema': {'type': 'object',
                      'properties': {'from_job': {'type': 'string',
                                                  'enum': ['customer service and support',
                                                           'finance and accounting',
                                                           'human resources',
                                                           'legal',
                                                           'operations',
                                                           'other',
                                                           'product and project management',
                                                           'risk and compliance',
                                                           'sales and marketing',
                                                           'technology']},
                                     'to_job': {'type': 'string',
                                                'enum': ['customer service and support',
                                                         'finance and accounting',
                                                         'human resources',
                                                         'legal',
                                                         'operations',
                                                         'other',
                                                         'product and project management',
                                                         'risk and compliance',
                                                         'sales and marketing',
                                                         'technology']},
                                     'n_skills': {'type': 'integer', 'minimum': 1, 'maximum': 25},
                                     'max_courses': {'type': 'integer', 'minimum': 1, 'maximum': 10}},
                      'required': ['from_job', 'to_job']}},
    {'name': 'skill_trends',
     'description': 'Draw skill demand over time for a job group and optional skill names or IDs.',
     'input_schema': {'type': 'object',
                      'properties': {'job_type': {'type': 'string',
                                                  'enum': ['customer service and support',
                                                           'finance and accounting',
                                                           'human resources',
                                                           'legal',
                                                           'operations',
                                                           'other',
                                                           'product and project management',
                                                           'risk and compliance',
                                                           'sales and marketing',
                                                           'technology']},
                                     'skills': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 10},
                                     'metric': {'type': 'string', 'enum': ['share', 'postings']},
                                     'n': {'type': 'integer', 'minimum': 1, 'maximum': 10}},
                      'required': ['job_type']}},
    {
        "name": "find_skills",
        "description": "Find skills whose name matches the text. Returns up to 5 canonical skill names. Call this first to resolve skill names.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Part of a skill name, e.g. 'python' or 'excel'"}},
            "required": ["text"],
        },
    },
    {
        "name": "get_skill_share",
        "description": "Monthly share (fraction of a job type's postings that mention the skill) for up to 5 skills, with the raw posting counts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skills": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5, "description": "Canonical skill names from find_skills"},
                "job_type": {"type": "string", "enum": ["ALL"] + JOB_TYPES, "description": "Default ALL"},
                "start_month": {"type": "string", "description": "YYYY-MM, default 2021-01"},
                "end_month": {"type": "string", "description": f"YYYY-MM, default {DEFAULT_END_MONTH}"},
            },
            "required": ["skills"],
        },
    },
    {
        "name": "top_movers",
        "description": "Skills whose average share changed most: the last window_months compared with the window_months before. Use direction 'up' for rising and 'down' for falling skills. Skills below 2% recent share are excluded.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"]},
                "job_type": {"type": "string", "enum": ["ALL"] + JOB_TYPES, "description": "Default ALL"},
                "window_months": {"type": "integer", "minimum": 1, "maximum": 24, "description": "Default 6"},
                "n": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Default 10"},
                "end_month": {"type": "string", "description": f"YYYY-MM, default {DEFAULT_END_MONTH}"},
            },
            "required": ["direction"],
        },
    },
    {
        "name": "profile_advice",
        "description": "Use existing user skills to compute 2025 vacancy skill overlap, missing skills, and actual linked courses. Input skills are already known, not learning targets. Country and job_type default to ALL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skills": {"type": "array", "items": {"type": "string"}, "description": "Skill names as the user wrote them"},
                "country": {"type": "string", "enum": ["ALL", "BE", "NL", "LU", "GB"]},
                "job_type": {"type": "string", "enum": ["ALL"] + JOB_TYPES},
            },
            "required": ["skills"],
        },
    },
    {
        "name": "top_skills",
        "description": "Most demanded skills in one job type, ranked by average monthly share of postings. Use for 'which skills matter for X'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_type": {"type": "string", "enum": JOB_TYPES},
                "n": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Default 10"},
                "end_month": {"type": "string", "description": f"YYYY-MM, default {DEFAULT_END_MONTH}"},
            },
            "required": ["job_type"],
        },
    },
    {
        "name": "skill_trend",
        "description": "Whether one skill's share of postings is rising, falling or flat in one job type, comparing the last window_months with the window before. Use the canonical skill name from find_skills.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_type": {"type": "string", "enum": JOB_TYPES},
                "skill": {"type": "string"},
                "window_months": {"type": "integer", "minimum": 1, "maximum": 12, "description": "Default 6"},
                "end_month": {"type": "string", "description": f"YYYY-MM, default {DEFAULT_END_MONTH}"},
            },
            "required": ["job_type", "skill"],
        },
    },
    {
        "name": "rising_and_falling_skills",
        "description": "Biggest share movers in one job type over the latest window_months, split into rising and falling lists. Skills below 2% share are ignored.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_type": {"type": "string", "enum": JOB_TYPES},
                "window_months": {"type": "integer", "minimum": 1, "maximum": 12, "description": "Default 6"},
                "n": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Skills per list, default 5"},
                "end_month": {"type": "string", "description": f"YYYY-MM, default {DEFAULT_END_MONTH}"},
            },
            "required": ["job_type"],
        },
    },
    {
        "name": "find_internal_talent",
        "description": "Find hidden internal talent for the AI-auditor role using five explicitly fictional employee profiles. Returns a skill-overlap ranking, missing skills, real catalog course titles/URLs, and gaps without courses. Use for HR reskilling and internal mobility; profile_id selects one candidate for follow-up questions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_role_id": {
                    "type": "string",
                    "enum": [
                        "ai-auditor"
                    ],
                    "description": "Fixed hand-curated demo role; default ai-auditor."
                },
                "n": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "description": "Number of candidates, default 3."
                },
                "profile_id": {
                    "type": "string",
                    "enum": [
                        "demo-a",
                        "demo-b",
                        "demo-c",
                        "demo-d",
                        "demo-e"
                    ],
                    "description": "Optional candidate from a previous result, to see their gaps and courses."
                }
            },
            "additionalProperties": False
        }
    },
    {
        "name": "make_chart",
        "description": "Add a line or bar chart to the answer. Use for trends (line) or comparisons (bar). The chart is shown to the user next to your answer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spec": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["line", "bar"]},
                        "title": {"type": "string"},
                        "y_label": {"type": "string"},
                        "series": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 6,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "points": {
                                        "type": "array",
                                        "minItems": 1,
                                        "maxItems": MAX_ROWS,
                                        "items": {"type": "array", "minItems": 2, "maxItems": 2},
                                    },
                                },
                                "required": ["name", "points"],
                            },
                        },
                    },
                    "required": ["type", "title", "y_label", "series"],
                }
            },
            "required": ["spec"],
        },
    },
]

TOOL_FUNCTIONS = {
    "recommend_courses": recommend_courses,
    "find_internal_talent": find_internal_talent,
    "find_skills": find_skills,
    "get_skill_share": get_skill_share,
    "top_movers": top_movers,
    "profile_advice": profile_advice,
}


def _cached_tools():
    tools = copy.deepcopy(TOOLS)
    tools[-1]["cache_control"] = {"type": "ephemeral"}
    return tools


def _run_tool(name, args, charts):
    try:
        if name == "plan_learning_budget":
            output = learning_budget.plan_learning_budget(**args)
            chart = learning_budget.budget_chart(output)
            if chart:
                charts.append(chart)
            return output
        if name == "make_chart":
            return make_chart(args.get("spec"), charts)
        if name in VIZ_FUNCTIONS:
            return _run_viz_tool(name, args, charts)
        fn = TOOL_FUNCTIONS.get(name)
        if fn is None:
            return _tool_error(f"unknown tool {name}")
        return fn(**args)
    except Exception as exc:
        return _tool_error(f"{type(exc).__name__}: {exc}")


def _to_api_content(block):
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return None  # thinking blocks from local models are not replayed


def _log_usage(question, usage, cached):
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "question": question,
        "cached": cached,
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "cache_read_tokens": usage["cache_read_tokens"],
        "cache_write_tokens": usage["cache_write_tokens"],
    }
    with USAGE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


_ANSWER_CACHE = {}
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _has_learning_tools(messages):
    names = {"recommend_courses", "profile_advice", "career_path", "find_internal_talent", "plan_learning_budget"}
    return any(block.get("type") == "tool_use" and block.get("name") in names
               for message in messages if isinstance(message.get("content"), list)
               for block in message["content"] if isinstance(block, dict))


def ask(question, history=None):
    """Answer one question. Returns {"answer", "charts", "usage", "messages"}."""
    cache_key = question.strip().lower()
    learning_question = re.search(learning.COURSE_INTENT, question, re.I) or re.search(r"\b(learn|leren|profiel|profile|leerbudget|opleidingsbudget|budget|uren|uur|hours?)\b", question, re.I)
    if (not history and cache_key in _ANSWER_CACHE and not learning_question
            and not _has_learning_tools(_ANSWER_CACHE[cache_key].get("messages", []))):
        cached = copy.deepcopy(_ANSWER_CACHE[cache_key])
        zero = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
        cached["usage"] = zero
        _log_usage(question, zero, cached=True)
        return cached

    messages = list(history or []) + [{"role": "user", "content": question}]
    charts = []
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    # Explicit profile follow-ups in this demo need fresh data, not a model guess
    # based on a previous candidate's courses.
    profile = re.search(r"\b(?:profiel|profile)\s+([a-e])\b", question, re.IGNORECASE)
    talent_context = any(
        block.get("type") == "tool_use" and block.get("name") == "find_internal_talent"
        for message in (history or []) if isinstance(message.get("content"), list)
        for block in message["content"] if isinstance(block, dict)
    )
    role_request = re.search(r"\bai[ -]?auditor\b", question, re.IGNORECASE)
    budget_args = learning_budget.budget_request(question, history)
    if not budget_args and (role_request or (profile and talent_context)):
        args = {"profile_id": "demo-" + profile.group(1).lower()} if profile else {}
        output = _run_tool("find_internal_talent", args, charts)
        if "error" not in output:
            tool_id = "talent_" + uuid.uuid4().hex
            answer = format_internal_talent(output)
            messages.extend([
                {"role": "assistant", "content": [{"type": "tool_use", "id": tool_id, "name": "find_internal_talent", "input": args}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": json.dumps(output)}]},
                {"role": "assistant", "content": [{"type": "text", "text": answer}]},
            ])
            _log_usage(question, usage, cached=False)
            return {"answer": answer, "charts": charts, "usage": usage, "messages": messages}
    profile_args = None if budget_args else learning.profile_request(question)
    course_args = None if budget_args or profile_args else learning.course_request(question, history)
    learning_tool = "plan_learning_budget" if budget_args else "profile_advice" if profile_args else "recommend_courses"
    if budget_args or profile_args or course_args:
        course_args = budget_args or profile_args or course_args
        output = _run_tool(learning_tool, course_args, charts)
        if "error" not in output:
            tool_id = "courses_" + uuid.uuid4().hex
            answer = learning_budget.format_budget(output) if budget_args else learning.format_courses(output)
            messages.extend([
                {"role": "assistant", "content": [{"type": "tool_use", "id": tool_id, "name": learning_tool, "input": course_args}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": json.dumps(output)}]},
                {"role": "assistant", "content": [{"type": "text", "text": answer}]},
            ])
            _log_usage(question, usage, cached=False)
            return {"answer": answer, "charts": charts, "usage": usage, "messages": messages}
        if budget_args:
            answer = "Het leerbudget kon niet worden verwerkt: " + output["error"]
            messages.append({"role": "assistant", "content": [{"type": "text", "text": answer}]})
            _log_usage(question, usage, cached=False)
            return {"answer": answer, "charts": charts, "usage": usage, "messages": messages}
    client = _get_client()
    system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
    tools = _cached_tools()
    answer = ""

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=tools,
            messages=messages,
        )
        usage["input_tokens"] += response.usage.input_tokens
        usage["output_tokens"] += response.usage.output_tokens
        usage["cache_read_tokens"] += response.usage.cache_read_input_tokens or 0
        usage["cache_write_tokens"] += response.usage.cache_creation_input_tokens or 0

        assistant_content = [c for c in (_to_api_content(b) for b in response.content) if c is not None]
        messages.append({"role": "assistant", "content": assistant_content})
        if response.stop_reason != "tool_use":
            answer = "".join(b.text for b in response.content if b.type == "text").strip()
            if re.search(learning.COURSE_INTENT, question, re.I):
                fallback = learning.course_request(question, messages)
                if fallback:
                    output = _run_tool("recommend_courses", fallback, charts)
                    answer = learning.format_courses(output) if "error" not in output else "Cursusadvies is tijdelijk niet beschikbaar; controleer de cursusimport en databaseverbinding."
                else:
                    answer = "Voor welke vaardigheden zoek je een cursus? Geef skillnamen of vaste skill-ID’s, dan zoek ik de gekoppelde opleidingen in onze catalogus."
                messages[-1] = {"role": "assistant", "content": [{"type": "text", "text": answer}]}
            break

        results = []
        talent_result = None
        course_result = None
        for block in response.content:
            if block.type == "tool_use":
                output = _run_tool(block.name, block.input, charts)
                if block.name == "find_internal_talent" and isinstance(output, dict) and "error" not in output:
                    talent_result = output
                if block.name in {"recommend_courses", "profile_advice", "career_path", "plan_learning_budget"} and isinstance(output, dict) and "error" not in output:
                    course_result = (block.name, output)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(output, default=str),
                    "is_error": isinstance(output, dict) and "error" in output,
                })
        messages.append({"role": "user", "content": results})
        if course_result is not None:
            name, output = course_result
            if name == "career_path":
                data = output["data"]
                output = {"skills": [{"name": r["skill"]} for r in data["skills_to_learn"]],
                          "courses": [{"title": c["title"], "url": c["url"], "site": "catalogus",
                                       "covered_skills": [{"name": v} for v in c["skills"]]} for c in data["courses"]],
                          "skills_without_courses": [{"name": v} for v in data["skills_without_courses"]]}
            answer = learning_budget.format_budget(output) if name == "plan_learning_budget" else learning.format_courses(output)
            messages.append({"role": "assistant", "content": [{"type": "text", "text": answer}]})
            break
        if talent_result is not None:
            # Use verified numbers and course mappings directly; save a model round.
            answer = format_internal_talent(talent_result)
            messages.append({"role": "assistant", "content": [{"type": "text", "text": answer}]})
            break
    else:
        answer = "I could not finish this question within the tool limit. Please ask a narrower question."

    result = {"answer": answer, "charts": charts, "usage": usage, "messages": messages}
    _log_usage(question, usage, cached=False)
    if not history and answer and not learning_question and not _has_learning_tools(messages):
        _ANSWER_CACHE[cache_key] = copy.deepcopy({k: v for k, v in result.items() if k != "usage"})
    return result


def main():
    if len(sys.argv) < 2:
        print('usage: python ai_layer.py "your question"', file=sys.stderr)
        sys.exit(2)
    result = ask(" ".join(sys.argv[1:]))
    print("ANSWER:\n" + result["answer"])
    print("\nCHARTS:\n" + json.dumps(result["charts"], indent=2, ensure_ascii=False))
    print("\nUSAGE:\n" + json.dumps(result["usage"], indent=2))


if __name__ == "__main__":
    main()
