"""Print 10 random entries from each .parquet file in data/."""

import random
from pathlib import Path

import pyarrow.parquet as pq

DATA_DIR = Path(__file__).parent / "data"
SAMPLE_SIZE = 10
MAX_CHARS = 300  # truncate long fields such as description


def truncate(value):
    text = str(value)
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + "..."


def main():
    for path in sorted(DATA_DIR.glob("*.parquet")):
        table = pq.read_table(path)
        indices = random.sample(range(table.num_rows), min(SAMPLE_SIZE, table.num_rows))
        sample = table.take(indices).to_pylist()

        print(f"\n===== {path.name} ({table.num_rows:,} rows) =====")
        for i, row in enumerate(sample, 1):
            print(f"\n--- entry {i} ---")
            for column, value in row.items():
                print(f"{column}: {truncate(value)}")


if __name__ == "__main__":
    main()
