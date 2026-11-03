"""Build the fixed skill taxonomy from the LLM sample and validate the rule matcher against it."""

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict

import pyarrow.parquet as pq

from matching import (
    DATA_DIR,
    PARSER_DIR,
    SKILL_TAXONOMY_VERSION,
    SKILLS_JSONL_PATH,
    TAXONOMY_PATH,
    TOKEN_RE,
    clean_text,
    find_skills,
    job_type,
    load_taxonomy,
)

DB_PATH = PARSER_DIR / "skills.sqlite"
YEARS = [2021, 2022, 2023, 2024, 2025, 2026]


def key_of(skill):
    return " ".join(TOKEN_RE.findall(skill.lower()))


def build_taxonomy(conn, min_df):
    total = conn.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0]
    per_vacancy = defaultdict(set)
    for vacancy_id, skill in conn.execute("SELECT vacancy_id, skill FROM vacancy_skills"):
        key = key_of(skill)
        if key:
            per_vacancy[vacancy_id].add(key)

    counts = Counter(key for keys in per_vacancy.values() for key in keys)
    kept = {key for key, n in counts.items() if n >= min_df}

    aliases = {key: key for key in kept}
    for key in kept:
        base = re.sub(r" skills?$", "", key)
        if base != key and base in kept:
            aliases[key] = base

    canonical = sorted(set(aliases.values()), key=lambda name: (-counts[name], name))
    skill_ids = {name: f"S{i:03d}" for i, name in enumerate(canonical, 1)}
    synonyms = defaultdict(list)
    for key, name in aliases.items():
        if key != name:
            synonyms[name].append(key)
    skills = [
        {
            "skill_id": skill_ids[name],
            "name": name,
            "category": None,
            "synonyms": sorted(synonyms[name]),
            "sample_df": counts[name],
        }
        for name in canonical
    ]
    return {
        "skill_taxonomy_version": SKILL_TAXONOMY_VERSION,
        "sample_postings": total,
        "min_df": min_df,
        "max_tokens": max(len(key.split()) for key in aliases),
        "skills": skills,
        "aliases": {key: skill_ids[name] for key, name in aliases.items()},
    }


def validate(conn, aliases, max_tokens, first_tokens):
    llm_skills = defaultdict(set)
    for vacancy_id, skill in conn.execute("SELECT vacancy_id, skill FROM vacancy_skills"):
        key = key_of(skill)
        if key in aliases:
            llm_skills[str(vacancy_id)].add(aliases[key])
    llm_category = dict(conn.execute("SELECT vacancy_id, job_category FROM vacancies"))
    sample_ids = {str(v) for v in llm_category}

    tp = fp = fn = agree = evaluated = 0
    for year in YEARS:
        table = pq.read_table(DATA_DIR / f"vacancies_{year}.parquet", columns=["vacancy_id", "title", "description"])
        for row in table.to_pylist():
            vacancy_id = str(row["vacancy_id"])
            if vacancy_id not in sample_ids:
                continue
            evaluated += 1
            text = clean_text(row["title"]) + " " + clean_text(row["description"])
            matched = set(find_skills(text, aliases, max_tokens, first_tokens))
            llm = llm_skills[vacancy_id]
            tp += len(matched & llm)
            fp += len(matched - llm)
            fn += len(llm - matched)
            if job_type(row["title"]) == job_type(llm_category[vacancy_id] or ""):
                agree += 1

    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    print(f"validated on {evaluated:,} sample postings")
    print(f"skill precision vs LLM labels: {precision:.2%}")
    print(f"skill recall vs LLM labels:    {recall:.2%}")
    print(f"job type agreement (title rules vs LLM category): {agree / evaluated:.2%}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-df", type=int, default=5, help="minimum sample postings a skill must appear in")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    taxonomy = build_taxonomy(conn, args.min_df)
    TAXONOMY_PATH.write_text(json.dumps(taxonomy, indent=2))
    with open(SKILLS_JSONL_PATH, "w", encoding="utf-8") as f:
        for skill in taxonomy["skills"]:
            record = {k: skill[k] for k in ("skill_id", "name", "category", "synonyms")}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"wrote {len(taxonomy['skills'])} skills to {TAXONOMY_PATH.name} and {SKILLS_JSONL_PATH.name}")

    aliases, max_tokens, first_tokens = load_taxonomy()
    validate(conn, aliases, max_tokens, first_tokens)
    conn.close()


if __name__ == "__main__":
    main()
