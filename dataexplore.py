from pathlib import Path
import pandas as pd

df = pd.read_parquet(
    Path.home() / "Downloads" / "vacancies_2021.parquet"
)

print(df.head().to_string(index=False, max_cols=None, max_colwidth=None))
print(df.shape)