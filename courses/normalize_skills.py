"""Map the skills of data/Online_Courses.csv onto parser/taxonomy.json skill IDs.

Writes a parser-db contract v1.0 batch (courses/batches/batch-courses-001/manifest.json
and parsed.jsonl, plus input-ids.txt), courses/courses.jsonl (course metadata keyed by
posting_id) and courses/unmapped_skills.csv (listed skills without taxonomy match,
by frequency, to spot taxonomy gaps).
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))

from matching import (  # noqa: E402
    PARSER_VERSION,
    TAXONOMY_PATH,
    TOKEN_RE,
    clean_text,
    find_skills,
    load_taxonomy,
    normalise_title,
)

PAREN_RE = re.compile(r"\(([^)]*)\)")
SLUG_RE = re.compile(r"\W+")
BATCH_ID = "courses-part-001-run-001"
CONTRACT_VERSION = "1.0"
TEXT_FIELDS = ("Title", "Short Intro", "What you learn")


def tokens(text):
    return " ".join(TOKEN_RE.findall(text.lower()))


def match_phrase(phrase, aliases, max_tokens, first_tokens):
    """Skill IDs for one listed skill, e.g. 'Cascading Style Sheets (CSS)'."""
    outer = PAREN_RE.sub(" ", phrase)
    inner = " ".join(PAREN_RE.findall(phrase))
    for candidate in (tokens(phrase), tokens(outer), tokens(inner)):
        if candidate in aliases:
            return {aliases[candidate]}
    # Fall back to taxonomy terms contained in the phrase ("java programming" -> java).
    return set(find_skills(outer, aliases, max_tokens, first_tokens))


def normalise_row(row, aliases, max_tokens, first_tokens, unmapped):
    ids, source = set(), None
    listed = [p.strip() for p in (row.get("Skills") or "").split(",") if p.strip()]
    for phrase in listed:
        found = match_phrase(phrase, aliases, max_tokens, first_tokens)
        if found:
            ids |= found
        else:
            unmapped[tokens(phrase)] += 1
    if ids:
        source = "listed"
    elif not listed:
        text = clean_text(" ".join(row.get(f) or "" for f in TEXT_FIELDS))
        ids = set(find_skills(text, aliases, max_tokens, first_tokens))
        source = "text" if ids else None
    return sorted(ids), source, listed


def parsed_record(posting_id, title, ids):
    normalized = normalise_title(title)
    if not normalized:
        code, message = ("EMPTY_INPUT", "Cursus bevat geen bruikbare titel.")
        if title.strip():
            code, message = "INVALID_OUTPUT", "Geen bruikbare genormaliseerde titel."
        return {
            "posting_id": posting_id,
            "parse_status": "failed",
            "normalized_title": None,
            "skill_ids": [],
            "error": {"code": code, "message": message},
        }
    return {
        "posting_id": posting_id,
        "parse_status": "ok",
        "normalized_title": normalized,
        "skill_ids": ids,
        "error": None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data" / "Online_Courses.csv")
    parser.add_argument("--taxonomy", type=Path, default=TAXONOMY_PATH)
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()

    aliases, max_tokens, first_tokens = load_taxonomy(args.taxonomy)
    taxonomy = json.loads(args.taxonomy.read_text())
    unmapped, stats = Counter(), Counter()
    batch_dir = args.out_dir / "batches" / "batch-courses-001"
    batch_dir.mkdir(parents=True, exist_ok=True)

    with (
        args.input.open(encoding="utf-8", newline="") as src,
        (args.out_dir / "courses.jsonl").open("w", encoding="utf-8") as meta,
        (batch_dir / "parsed.jsonl").open("w", encoding="utf-8") as parsed,
        (batch_dir / "input-ids.txt").open("w", encoding="utf-8") as input_ids,
    ):
        for line_no, row in enumerate(csv.DictReader(src)):
            ids, source, listed = normalise_row(row, aliases, max_tokens, first_tokens, unmapped)
            posting_id = f"{SLUG_RE.sub('-', row['Site'].lower())}-{line_no:05d}"
            record = parsed_record(posting_id, row["Title"], ids)
            parsed.write(json.dumps(record, ensure_ascii=False) + "\n")
            input_ids.write(posting_id + "\n")
            meta.write(
                json.dumps(
                    {
                        "posting_id": posting_id,
                        "title": row["Title"].strip(),
                        "url": row["URL"].strip(),
                        "site": row["Site"].strip(),
                        "short_intro": clean_text(row["Short Intro"]).strip() or None,
                        "listed_skills": listed,
                        "skill_source": source,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            stats["courses"] += 1
            stats[f"source_{source}"] += 1
            stats["with_skill_ids"] += bool(record["skill_ids"])
            stats["failed"] += record["parse_status"] == "failed"

    manifest = {
        "contract_version": CONTRACT_VERSION,
        "batch_id": BATCH_ID,
        "parser_version": f"{PARSER_VERSION}-courses",
        "skill_taxonomy_version": taxonomy["skill_taxonomy_version"],
        "record_count": stats["courses"],
    }
    (batch_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    with (args.out_dir / "unmapped_skills.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["skill", "courses"])
        writer.writerows(unmapped.most_common())
    print(dict(stats), f"unmapped distinct: {len(unmapped)}")


if __name__ == "__main__":
    main()
