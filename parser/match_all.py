"""Parse every vacancy into parser-db contract v1.0 batches (manifest.json + parsed.jsonl per batch)."""

import argparse
import json
import shutil
import time
from collections import deque
from multiprocessing import Pool

import pyarrow.parquet as pq

from matching import (
    DATA_DIR,
    PARSER_DIR,
    PARSER_VERSION,
    SKILL_TAXONOMY_VERSION,
    clean_text,
    find_skills,
    load_taxonomy,
    normalise_title,
)

OUT_DIR = PARSER_DIR / "batches"
COLUMNS = ["vacancy_id", "title", "description"]
YEARS = [2021, 2022, 2023, 2024, 2025, 2026]
BATCH_SIZE = 20000
CONTRACT_VERSION = "1.0"
RUN_SUFFIX = "run-001"

_state = {}


def _init_worker():
    aliases, max_tokens, first_tokens = load_taxonomy()
    _state.update(aliases=aliases, max_tokens=max_tokens, first_tokens=first_tokens, known=set(aliases.values()))


def _failed(posting_id, code, message):
    return {
        "posting_id": posting_id,
        "parse_status": "failed",
        "normalized_title": None,
        "skill_ids": [],
        "error": {"code": code, "message": message},
    }


def _parse_posting(posting_id, title, description):
    title_text = clean_text(title).strip()
    description_text = clean_text(description).strip()
    if not title_text and not description_text:
        return _failed(posting_id, "EMPTY_INPUT", "Vacature bevat geen bruikbare titel- of beschrijvingstekst.")
    try:
        normalized = normalise_title(title)
        if not normalized:
            return _failed(posting_id, "INVALID_OUTPUT", "Geen bruikbare genormaliseerde titel.")
        skill_ids = find_skills(
            f"{title_text} {description_text}", _state["aliases"], _state["max_tokens"], _state["first_tokens"]
        )
    except Exception:
        return _failed(posting_id, "INTERNAL_ERROR", "Interne verwerkingsfout.")
    return {
        "posting_id": posting_id,
        "parse_status": "ok",
        "normalized_title": normalized,
        "skill_ids": skill_ids,
        "error": None,
    }


def _validate(results, input_ids, known):
    if len(set(input_ids)) != len(input_ids):
        raise ValueError("duplicate posting_id in batch input")
    if [r["posting_id"] for r in results] != input_ids:
        raise ValueError("output posting_ids do not match batch input")
    for r in results:
        pid = r["posting_id"]
        if len(set(r["skill_ids"])) != len(r["skill_ids"]) or not set(r["skill_ids"]) <= known:
            raise ValueError(f"invalid skill_ids for {pid}")
        if r["parse_status"] == "ok" and (not r["normalized_title"] or r["error"] is not None):
            raise ValueError(f"invalid ok record {pid}")
        if r["parse_status"] == "failed" and (r["normalized_title"] is not None or r["skill_ids"] or r["error"] is None):
            raise ValueError(f"invalid failed record {pid}")


def _write_batch(job):
    year, index, rows = job
    batch_name = f"batch-{year}-{index:03d}"
    final_dir = OUT_DIR / batch_name
    tmp_dir = OUT_DIR / f".tmp-{batch_name}"
    if (final_dir / "manifest.json").exists():
        return json.loads((final_dir / "manifest.json").read_text())["record_count"]
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True)

    try:
        input_ids = [str(v) for v in rows["vacancy_id"]]
        results = [_parse_posting(pid, t, d) for pid, t, d in zip(input_ids, rows["title"], rows["description"])]
        _validate(results, input_ids, _state["known"])

        with open(tmp_dir / "parsed.jsonl", "w", encoding="utf-8") as f:
            for record in results:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        manifest = {
            "contract_version": CONTRACT_VERSION,
            "batch_id": f"{year}-part-{index:03d}-{RUN_SUFFIX}",
            "parser_version": PARSER_VERSION,
            "skill_taxonomy_version": SKILL_TAXONOMY_VERSION,
            "record_count": len(results),
        }
        with open(tmp_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        shutil.rmtree(final_dir, ignore_errors=True)
        tmp_dir.rename(final_dir)
        return len(results)
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def _iter_batches(year, batch_size):
    path = DATA_DIR / f"vacancies_{year}.parquet"
    for batch in pq.ParquetFile(path).iter_batches(batch_size=batch_size, columns=COLUMNS):
        yield batch.to_pydict()


def _jobs(years):
    for year in years:
        for index, rows in enumerate(_iter_batches(year, BATCH_SIZE), 1):
            yield year, index, rows


def run(years, workers):
    OUT_DIR.mkdir(exist_ok=True)
    started = time.time()
    total = failed = 0
    in_flight = deque()
    with Pool(workers, initializer=_init_worker) as pool:
        for job in _jobs(years):
            in_flight.append(pool.apply_async(_write_batch, (job,)))
            if len(in_flight) >= workers * 2:
                total, failed = _collect(in_flight.popleft(), total, failed)
                print(f"{total:,} postings, {failed} batches failed, {total / (time.time() - started):.0f}/s", flush=True)
        while in_flight:
            total, failed = _collect(in_flight.popleft(), total, failed)
    print(f"all done: {total:,} postings, {failed} batches failed, {(time.time() - started) / 60:.1f} min")


def _collect(result, total, failed):
    try:
        return total + result.get(), failed
    except ValueError as exc:
        print(f"batch rejected: {exc}", flush=True)
        return total, failed + 1


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", default=YEARS)
    parser.add_argument("--workers", type=int, default=11)
    return parser.parse_args()
    parser.add_argument("--years", type=int, nargs="+", default=YEARS)
    parser.add_argument("--workers", type=int, default=11)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(args.years, args.workers)
