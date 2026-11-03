# CenEkathon 2026: vacancy data

About 1.5 million (1,542,169) real job postings from banks, insurers, payment firms and other financial
companies in Belgium, the Netherlands, Luxembourg and the UK, plus postings that recruitment
agencies and job boards published for them. Posted between January 2021 and September 2026.
The postings were collected from public job sites by TechWolf.

## Files

One Parquet file per year of posting:

`vacancies_2021.parquet`, `vacancies_2022.parquet`, `vacancies_2023.parquet`,
`vacancies_2024.parquet`, `vacancies_2025.parquet`, `vacancies_2026.parquet`

Download them with the links you received (for example `curl -L -o vacancies_2024.parquet "<link>"`),
then read them with pandas, polars or DuckDB:

```python
import pandas as pd
df = pd.read_parquet("vacancies_2024.parquet")
```

## Columns

| Column | What it is |
| :- | :- |
| `vacancy_id` | unique id of the posting in this dataset |
| `title` | job title as posted |
| `description` | full posting text as posted (HTML and formatting may remain) |
| `company` | the name of the organisation that published the posting, as written on the job site |
| `date_posted` | date the posting went online (empty for 271 postings from 2021) |
| `country` | BE, NL, LU or GB |
| `city` | city or locality of the job, as given by the job site |

Postings are in several languages, mostly English (about three quarters are UK postings). There are no
labels, categories or skill tags: everything beyond the raw posting is up to you.

## Good to know

- Data collection was incomplete from January to April 2026. A drop in those months says
  something about the collection, not about hiring.
- The links stop working on Tuesday 3 November 2026 at 23:59 (Brussels time).
- Use the data only for the CenEkathon and do not share it or the links outside your team.