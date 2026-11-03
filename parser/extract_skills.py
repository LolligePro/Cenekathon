"""Extract job category and skills from vacancy postings with a self-hosted LLM (Ollama).

Results are stored in SQLite and the run is resumable: postings already in the
database are skipped on the next run.
"""

import argparse
import html
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pyarrow.parquet as pq

PARSER_DIR = Path(__file__).resolve().parent
DATA_DIR = PARSER_DIR.parent / "data"
DEFAULT_DB = PARSER_DIR / "skills.sqlite"
COLUMNS = ["vacancy_id", "title", "description", "date_posted", "country"]
ALL_YEARS = [2021, 2022, 2023, 2024, 2025, 2026]
MAX_DESCRIPTION_CHARS = 4000
MAX_SKILLS = 15
MAX_ATTEMPTS = 3

SYSTEM_PROMPT = (
    "You extract structured data from job postings at financial companies (banks, insurers, payment firms). "
    "Postings may be in any language; always write categories and skills in English. "
    "job_category: a short lowercase job family, e.g. 'software engineering', 'risk and compliance', 'sales', "
    "'customer service', 'finance and accounting'. "
    "skills: concrete skills, tools and technologies the role requires, e.g. 'python', 'sql', 'credit risk modelling', "
    "'stakeholder management'. Skip company names, locations, salary and benefits. "
    f"Return at most {MAX_SKILLS} skills as short lowercase noun phrases without duplicates. "
    "Respond only with the JSON object."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "job_category": {"type": "string"},
        "skills": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["job_category", "skills"],
}

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vacancies (
    vacancy_id   TEXT PRIMARY KEY,
    year         INTEGER NOT NULL,
    country      TEXT,
    date_posted  TEXT,
    job_category TEXT,
    model        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vacancy_skills (
    vacancy_id TEXT NOT NULL REFERENCES vacancies(vacancy_id),
    skill      TEXT NOT NULL,
    PRIMARY KEY (vacancy_id, skill)
);
"""


def clean_text(value, limit=MAX_DESCRIPTION_CHARS):
    if value is None:
        return ""
    text = html.unescape(TAG_RE.sub(" ", str(value)))
    return WS_RE.sub(" ", text).strip()[:limit]


def build_user_message(posting):
    return (
        f"Title: {clean_text(posting['title'], 300)}\n"
        f"Country: {posting['country'] or 'unknown'}\n\n"
        f"Posting:\n{clean_text(posting['description'])}"
    )


def check_server(endpoint, model):
    with urllib.request.urlopen(f"{endpoint}/api/tags", timeout=10) as resp:
        models = {m["name"] for m in json.load(resp)["models"]}
    if model not in models:
        sys.exit(f"Model '{model}' not found on {endpoint}. Available: {sorted(models)}")


def call_model(endpoint, model, user_message, timeout):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "format": RESPONSE_SCHEMA,
        "stream": False,
        "options": {"temperature": 0, "num_ctx": 4096},
    }
    request = urllib.request.Request(
        f"{endpoint}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        body = json.load(resp)
    return json.loads(body["message"]["content"])


def normalise(result):
    category = " ".join(str(result.get("job_category", "")).lower().split())
    skills, seen = [], set()
    for raw in result.get("skills", []):
        skill = " ".join(str(raw).lower().split())
        if skill and skill not in seen:
            seen.add(skill)
            skills.append(skill)
    return category, skills[:MAX_SKILLS]


def extract(args, posting):
    """Return (category, skills) or None if all attempts failed."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = call_model(args.endpoint, args.model, build_user_message(posting), args.timeout)
            return normalise(result)
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, TypeError, AttributeError, ValueError) as exc:
            if attempt == MAX_ATTEMPTS:
                print(f"\nskipped {posting['vacancy_id']}: {exc}", file=sys.stderr)
                return None
            time.sleep(2 ** attempt)
    return None


def load_postings(years, per_year, seed):
    postings = []
    for year in years:
        path = DATA_DIR / f"vacancies_{year}.parquet"
        df = pq.read_table(path, columns=COLUMNS).to_pandas()
        if per_year is not None and len(df) > per_year:
            df = df.sample(n=per_year, random_state=seed)
        df["year"] = year
        postings.extend(df.to_dict("records"))
        print(f"loaded {len(df):,} postings from {path.name}")
    return postings


def save(conn, posting, category, skills, model):
    vacancy_id = str(posting["vacancy_id"])
    conn.execute(
        "INSERT OR REPLACE INTO vacancies VALUES (?, ?, ?, ?, ?, ?)",
        (
            vacancy_id,
            int(posting["year"]),
            posting["country"],
            str(posting["date_posted"]) if posting["date_posted"] is not None else None,
            category,
            model,
        ),
    )
    conn.execute("DELETE FROM vacancy_skills WHERE vacancy_id = ?", (vacancy_id,))
    conn.executemany(
        "INSERT INTO vacancy_skills VALUES (?, ?)",
        [(vacancy_id, skill) for skill in skills],
    )


def run(args):
    check_server(args.endpoint, args.model)
    conn = sqlite3.connect(args.db)
    conn.executescript(SCHEMA_SQL)

    postings = load_postings(args.years, args.per_year, args.seed)
    done = {row[0] for row in conn.execute("SELECT vacancy_id FROM vacancies")}
    todo = [p for p in postings if str(p["vacancy_id"]) not in done]
    print(f"{len(todo):,} to process ({len(done):,} already in {args.db.name})")

    failed = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(extract, args, p): p for p in todo}
        for i, future in enumerate(as_completed(futures), 1):
            posting = futures[future]
            result = future.result()
            if result is None:
                failed += 1
            else:
                save(conn, posting, *result, args.model)
            if i % 100 == 0 or i == len(todo):
                conn.commit()
                rate = i / (time.time() - started)
                print(f"\r{i:,}/{len(todo):,} ({rate:.2f}/s, {failed} failed)", end="", flush=True)
    conn.commit()
    conn.close()
    print(f"\ndone: {len(todo) - failed:,} stored, {failed:,} failed")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", default=ALL_YEARS)
    parser.add_argument("--per-year", type=int, default=500, help="random sample size per year")
    parser.add_argument("--all", action="store_true", help="process every posting (overrides --per-year)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--model", default="qwen2.5:7b-instruct")
    parser.add_argument("--endpoint", default="http://localhost:11434")
    parser.add_argument("--workers", type=int, default=4, help="parallel requests (match OLLAMA_NUM_PARALLEL)")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    if args.all:
        args.per_year = None
    return args


if __name__ == "__main__":
    run(parse_args())
