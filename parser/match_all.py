"""Label every vacancy with a job type and matched skills, writing one Parquet file per year."""

import argparse
import time
from multiprocessing import Pool
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from matching import DATA_DIR, PARSER_DIR, clean_text, find_skills, job_type, load_taxonomy

OUT_DIR = PARSER_DIR / "results"
COLUMNS = ["vacancy_id", "title", "description", "date_posted", "country"]
YEARS = [2021, 2022, 2023, 2024, 2025, 2026]
BATCH_SIZE = 20000
SCHEMA = pa.schema([
    ("vacancy_id", pa.string()),
    ("year", pa.int32()),
    ("country", pa.string()),
    ("date_posted", pa.string()),
    ("job_type", pa.string()),
    ("skills", pa.list_(pa.string())),
])

_state = {}


def _init_worker():
    _state["aliases"], _state["max_tokens"], _state["first_tokens"] = load_taxonomy()


def _label(job):
    year, rows = job
    aliases, max_tokens, first_tokens = _state["aliases"], _state["max_tokens"], _state["first_tokens"]
    out = {name: [] for name in SCHEMA.names}
    for vacancy_id, title, description, date_posted, country in zip(
        rows["vacancy_id"], rows["title"], rows["description"], rows["date_posted"], rows["country"]
    ):
        text = clean_text(title) + " " + clean_text(description)
        out["vacancy_id"].append(str(vacancy_id))
        out["year"].append(year)
        out["country"].append(country)
        out["date_posted"].append(str(date_posted) if date_posted is not None else None)
        out["job_type"].append(job_type(title))
        out["skills"].append(find_skills(text, aliases, max_tokens, first_tokens))
    return pa.Table.from_pydict(out, schema=SCHEMA)


def iter_batches(path):
    for batch in pq.ParquetFile(path).iter_batches(batch_size=BATCH_SIZE, columns=COLUMNS):
        yield batch.to_pydict()


def run(years, workers):
    OUT_DIR.mkdir(exist_ok=True)
    started = time.time()
    total = 0
    with Pool(workers, initializer=_init_worker) as pool:
        for year in years:
            path = DATA_DIR / f"vacancies_{year}.parquet"
            writer = pq.ParquetWriter(OUT_DIR / f"labeled_{year}.parquet", SCHEMA)
            pending = []
            for rows in iter_batches(path):
                pending.append((year, rows))
                if len(pending) == workers * 4:
                    total += _flush(pool, writer, pending)
                    pending = []
                    print(f"{year}: {total:,} rows, {total / (time.time() - started):.0f}/s", flush=True)
            if pending:
                total += _flush(pool, writer, pending)
            writer.close()
            print(f"{year}: done", flush=True)
    print(f"all done: {total:,} rows in {(time.time() - started) / 60:.1f} min")


def _flush(pool, writer, pending):
    count = 0
    for table in pool.map(_label, pending):
        writer.write_table(table)
        count += table.num_rows
    return count


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", default=YEARS)
    parser.add_argument("--workers", type=int, default=11)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(args.years, args.workers)
