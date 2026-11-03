# Skill Market: what the finance job market asks for, and how to get there

## The idea in one sentence
A jobseeker or student uses Skill Market to decide **which skills to learn or what to study**, based on what finance companies actually ask for in 1.5M job descriptions.

## Components

### 1. Parser → database (the spine)
- Strip boilerplate from descriptions (legal text, application instructions).
- Extract **job title / role family** and **required skills** per posting, mapped to a fixed skill list (ESCO subset + AI skills).
- Store as: `posting_id | company | date | role_family | skill_id`.
- Cheap by design: keyword baseline → embeddings (JobBERT-v3 / e5 on the GPU) for the bulk. Claude only labels ~200 postings to measure quality.

### 2. Graph tool ("skill stock market")
- "Price" of a skill = share of postings that ask for it, per month or quarter (shares, not counts, so coverage jumps don't look like booms).
- Index (base period = 100) and momentum (change vs a year ago).
- Filters: skill, role family, period, country.

### 3. Profile advisor
- The user enters their skills → matched to the skill list.
- Output: which of your skills are rising or falling in demand, how many vacancies you match, and which **one or two skills would unlock the most extra vacancies**.

### 4. Study advisor (extra)
- A student enters a target role → required skills for that role from the database.
- Study programmes are parsed into the same skill list → "these programmes cover most of the skills this role needs."



## Who pays
Universities (curriculum planning) and public employment services like VDAB (reskilling advice).

## Not doing
- Live scraping (we use the provided 1.5M postings)
- User accounts, login, mobile app
- Salary-based advice (salary missing in many postings)

## Architecture
- Boilerplate stripping + company cleanup: rules / SQL (DuckDB)
- Skill detection: keyword baseline → sentence embeddings (JobBERT-v3 / e5, A100) matched to skill list
- Quality check: Claude labels ~200 postings → precision of our detector
- Aggregation: DuckDB SQL over Parquet
- Advice text: Claude, reading only aggregated numbers (never raw postings)

## Contracts
All tables are Parquet files in `data/`. Period format: `YYYY-Qn` (quarters). Placeholder values are examples.

### data/skills.parquet (the ticker list)
| skill_id | name | category | synonyms |
|---|---|---|---|
| S001 | python | programming | ["python", "python3", "pandas"] |
| S042 | machine learning | ai | ["machine learning", "ML", "ML-modellen"] |

### data/postings.parquet (one row per posting, written by parser)
| posting_id | company | date | period | country | role_family |
|---|---|---|---|---|---|
| P-0001 | ing | 2026-03-14 | 2026-Q1 | BE | risk |

### data/posting_skills.parquet (one row per posting × skill, written by parser)
| posting_id | skill_id | method |
|---|---|---|
| P-0001 | S042 | keyword |

`method` = keyword | embedding, so we can compare both.

### data/skill_prices.parquet (written by aggregation, read by graph tool + advisor)
| skill_id | period | country | role_family | share | index | momentum_yoy |
|---|---|---|---|---|---|---|
| S042 | 2026-Q1 | ALL | risk | 0.12 | 240 | 0.40 |

- share = postings with this skill / all postings in the same period, country and role_family
- index = share relative to 2021-Q1 (= 100)
- momentum_yoy = share / share of the same quarter last year − 1
- country and role_family use `ALL` for the totals

### advisor input
```json
{"skills": ["python", "excel", "risk management"], "country": "BE"}
```

### advisor output (read by the frontend)
```json
{
  "matched_vacancies": 312,
  "rising_skills": [{"skill_id": "S042", "name": "machine learning", "momentum_yoy": 0.40}],
  "falling_skills": [{"skill_id": "S107", "name": "manual reconciliation", "momentum_yoy": -0.25}],
  "unlock": [{"skill_id": "S001", "name": "python", "extra_vacancies": 180}],
  "advice": "Learn Python: it unlocks 180 more vacancies, and demand grew 40% this year."
}
```

## Owners
- A: skill list + parser + posting_skills
- B: skill_prices aggregation + graph tool
- C: profile advisor
- D: study advisor (only if time allows) + pitch + slides