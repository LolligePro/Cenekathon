"""Skill Market AI layer: answers questions with Claude, using only aggregated tables.

The model never sees raw postings. It can only call the tools below, and each tool
runs a fixed, parameterised DuckDB query over data/skills.parquet and
data/skill_prices.parquet.
"""

import copy
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import anthropic

DATA_DIR = Path(os.environ.get("SKILLMARKET_DATA", "data"))
MODEL = os.environ.get("SKILLMARKET_MODEL", "claude-haiku-5-5-20251001")
USAGE_LOG = Path("usage_log.jsonl")
MAX_TOKENS = int(os.environ.get("SKILLMARKET_MAX_TOKENS", "4000"))  # Qwen3 thinking uses many tokens
MAX_TOOL_ROUNDS = 6
MAX_ROWS = 60
COUNTRIES = ["ALL", "BE", "NL", "LU", "GB"]
ROLE_FAMILIES_NOTE = "ALL or a role family such as risk, compliance, analytics"

SYSTEM_PROMPT = """You are the Skill Market assistant. You help jobseekers and students in finance decide which skills to learn, based on job postings in Belgium, the Netherlands, Luxembourg and the UK.

Rules:
- Only state numbers that came from tool results. Never invent or estimate numbers.
- Resolve skill names with find_skills first, then use the returned skill_id values in other tools.
- Call make_chart for trends or comparisons over time.
- Show shares as percentages (0.12 -> 12%).
- Country codes are ALL, BE, NL, LU or GB. Use GB, not UK.
- Mention the limits in your answer: postings are not hires; the data covers finance only; data collection was incomplete from January to April 2026, so drops in those months reflect collection, not hiring.
- Keep the answer to at most 5 sentences, plus one concrete piece of advice that includes a number. Always end with that advice.
"""


def _tool_error(message):
    return {"error": message}


class _Data:
    """DuckDB connection with views over the two aggregated Parquet files only."""

    def __init__(self, data_dir):
        skills_path = (Path(data_dir) / "skills.parquet").as_posix()
        prices_path = (Path(data_dir) / "skill_prices.parquet").as_posix()
        self.con = duckdb.connect(":memory:")
        self.con.execute(f"CREATE VIEW skills AS SELECT * FROM read_parquet('{skills_path}')")
        self.con.execute(f"CREATE VIEW skill_prices AS SELECT * FROM read_parquet('{prices_path}')")
        self.latest_period = self.con.execute("SELECT MAX(period) FROM skill_prices").fetchone()[0]

    def query(self, sql, params):
        cur = self.con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchmany(MAX_ROWS)]


_DATA = None


def _data():
    global _DATA
    if _DATA is None:
        _DATA = _Data(DATA_DIR)
    return _DATA


# ---------- tools ----------

def find_skills(text):
    """Match name or synonyms, max 5 results."""
    if not isinstance(text, str) or not text.strip():
        return _tool_error("text must be a non-empty string")
    pattern = f"%{text.strip().lower()}%"
    rows = _data().query(
        """
        SELECT skill_id, name, category
        FROM skills
        WHERE lower(name) LIKE ?
           OR EXISTS (SELECT 1 FROM unnest(synonyms) AS t(v) WHERE lower(v) LIKE ?)
        ORDER BY name
        LIMIT 5
        """,
        [pattern, pattern],
    )
    return {"skills": rows}


def _check_country(country):
    if country not in COUNTRIES:
        raise ValueError(f"country must be one of {COUNTRIES}")


def _period(value, default):
    if value in (None, "", "latest"):
        return default
    if not re.fullmatch(r"\d{4}-Q[1-4]", str(value)):
        raise ValueError("period must look like YYYY-Qn, e.g. 2026-Q1")
    return value


def get_skill_prices(skill_ids, role_family="ALL", country="ALL", from_period="2021-Q1", to_period=None):
    """Time series of share, index and momentum_yoy for up to 5 skills."""
    if not isinstance(skill_ids, list) or not 1 <= len(skill_ids) <= 5:
        return _tool_error("skill_ids must be a list of 1 to 5 skill ids")
    _check_country(country)
    from_period = _period(from_period, "2021-Q1")
    to_period = _period(to_period, _data().latest_period)
    placeholders = ", ".join("?" for _ in skill_ids)
    rows = _data().query(
        f"""
        SELECT skill_id, period, share, index, momentum_yoy
        FROM skill_prices
        WHERE skill_id IN ({placeholders})
          AND role_family = ?
          AND country = ?
          AND period BETWEEN ? AND ?
        ORDER BY skill_id, period
        LIMIT {MAX_ROWS}
        """,
        [*skill_ids, role_family, country, from_period, to_period],
    )
    return {"prices": rows, "to_period": to_period}


def top_movers(direction, period=None, role_family="ALL", country="ALL", category=None, n=10):
    """Skills sorted by momentum_yoy, rising (up) or falling (down)."""
    if direction not in ("up", "down"):
        return _tool_error("direction must be 'up' or 'down'")
    _check_country(country)
    if not isinstance(n, int) or not 1 <= n <= 20:
        return _tool_error("n must be an integer from 1 to 20")
    period = _period(period, _data().latest_period)
    order = "DESC" if direction == "up" else "ASC"
    rows = _data().query(
        f"""
        SELECT p.skill_id, s.name, s.category, p.share, p.momentum_yoy
        FROM skill_prices p
        JOIN skills s USING (skill_id)
        WHERE p.period = ?
          AND p.role_family = ?
          AND p.country = ?
          AND (? IS NULL OR s.category = ?)
        ORDER BY p.momentum_yoy {order}
        LIMIT {n}
        """,
        [period, role_family, country, category, category],
    )
    return {"movers": rows, "period": period}


def profile_advice(skills, country):
    """Calls the profile advisor (advisor.py, owned by person C)."""
    try:
        from advisor import advise
    except ImportError:
        return _tool_error("profile advisor is not available yet (advisor.py missing)")
    try:
        _check_country(country)
        return advise({"skills": skills, "country": country})
    except Exception as exc:  # the advisor is external code; report its failures to the model
        return _tool_error(f"profile advisor failed: {exc}")


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
    {
        "name": "find_skills",
        "description": "Find skills whose name or synonyms match the text. Returns up to 5 skill_ids. Call this first to resolve skill names.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Skill name or synonym, e.g. 'ML' or 'python'"}},
            "required": ["text"],
        },
    },
    {
        "name": "get_skill_prices",
        "description": "Time series of share (fraction of postings), index (2021-Q1 = 100) and momentum_yoy for up to 5 skills.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5},
                "role_family": {"type": "string", "description": f"Default ALL. {ROLE_FAMILIES_NOTE}."},
                "country": {"type": "string", "enum": COUNTRIES, "description": "Default ALL"},
                "from_period": {"type": "string", "description": "YYYY-Qn, default 2021-Q1"},
                "to_period": {"type": "string", "description": "YYYY-Qn, default latest period"},
            },
            "required": ["skill_ids"],
        },
    },
    {
        "name": "top_movers",
        "description": "Skills ranked by momentum_yoy (change in share vs a year ago). Use direction 'up' for rising and 'down' for falling skills.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"]},
                "period": {"type": "string", "description": "YYYY-Qn, default latest period"},
                "role_family": {"type": "string", "description": f"Default ALL. {ROLE_FAMILIES_NOTE}."},
                "country": {"type": "string", "enum": COUNTRIES, "description": "Default ALL"},
                "category": {"type": "string", "description": "Optional skill category filter, e.g. programming"},
                "n": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Default 10"},
            },
            "required": ["direction"],
        },
    },
    {
        "name": "profile_advice",
        "description": "Advice for a jobseeker with these skills: rising and falling skills, matching vacancies, and the skill that unlocks the most vacancies.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skills": {"type": "array", "items": {"type": "string"}, "description": "Skill names as the user wrote them"},
                "country": {"type": "string", "enum": COUNTRIES},
            },
            "required": ["skills", "country"],
        },
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
    "find_skills": find_skills,
    "get_skill_prices": get_skill_prices,
    "top_movers": top_movers,
    "profile_advice": profile_advice,
}


def _cached_tools():
    tools = copy.deepcopy(TOOLS)
    tools[-1]["cache_control"] = {"type": "ephemeral"}
    return tools


def _run_tool(name, args, charts):
    if name == "make_chart":
        return make_chart(args.get("spec"), charts)
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return _tool_error(f"unknown tool {name}")
    try:
        return fn(**args)
    except Exception as exc:  # bad arguments or DuckDB errors go back to the model
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


def ask(question, history=None):
    """Answer one question. Returns {"answer", "charts", "usage", "messages"}."""
    cache_key = question.strip().lower()
    if not history and cache_key in _ANSWER_CACHE:
        cached = copy.deepcopy(_ANSWER_CACHE[cache_key])
        zero = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
        cached["usage"] = zero
        _log_usage(question, zero, cached=True)
        return cached

    client = _get_client()
    messages = list(history or []) + [{"role": "user", "content": question}]
    charts = []
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
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
            break

        results = []
        for block in response.content:
            if block.type == "tool_use":
                output = _run_tool(block.name, block.input, charts)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(output, default=str),
                    "is_error": isinstance(output, dict) and "error" in output,
                })
        messages.append({"role": "user", "content": results})
    else:
        answer = "I could not finish this question within the tool limit. Please ask a narrower question."

    result = {"answer": answer, "charts": charts, "usage": usage, "messages": messages}
    _log_usage(question, usage, cached=False)
    if not history and answer:
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
