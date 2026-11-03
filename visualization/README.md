# Visualization layer

This package turns skill-demand data into charts and short summaries for the advisor.
The advisor calls a small set of functions in `api.py`. Each returns a plain dict that
is JSON-serializable, so it can be passed to an LLM or a frontend as-is.

It is pure Python (pandas + Vega-Lite spec dicts). It does no rendering itself: the
charts are Vega-Lite v5 specs that any frontend with `vega-embed` can draw.

## Quick start

```python
from visualization import api

api.list_job_types()                              # ["Compliance Officer", "Data Analyst", ...]
api.skill_trends("Software Engineer")             # top 5 skills, share over time
api.skill_trends("Software Engineer", n=3, metric="postings")
api.skill_trends("Data Analyst", skills=["Python", "SQL"])
api.top_skills("Risk Manager", n=10)
api.skill_trend("Data Analyst", "Python", window_months=6)   # verdict only, no chart
api.rising_and_falling_skills("Software Engineer", window_months=6, n=5)
```

To regenerate the gallery at `preview.html` (requires internet for the CDN scripts):

```
python -m visualization.preview
```

## Layout

| File | Responsibility |
|------|----------------|
| `data_source.py` | The only module that knows where data comes from. Holds `StubSource` and `get_source()`. |
| `analysis.py` | Pure pandas computations: shares, top skills, trends, rising/falling. No I/O, no charts. |
| `charts.py` | Builds Vega-Lite v5 spec dicts. Holds the palette and shared `THEME`. |
| `api.py` | The advisor-facing functions. Validates inputs, calls analysis and charts, builds summaries. |
| `preview.py` | Builds the HTML gallery (`preview.html`) from the default charts. |
| `preview.html` | Generated output. Do not edit by hand; rerun `preview.py`. |
| `stub/` | Synthetic CSVs used until the database is ready. |

Dependency direction is one-way: `api` → `analysis`/`charts` → `data_source`.
Keep it that way. Analysis must not import charts, and nothing except `data_source`
should read files.

## The advisor API (`api.py`)

Every function returns:

```python
{
    "summary": str,        # one line the LLM can quote
    "data": ...,           # the underlying numbers (list of records or a dict)
    "chart": dict | None,  # Vega-Lite spec, or None when no chart applies
}
```

| Function | Purpose | Key arguments (defaults) | Limits |
|----------|---------|--------------------------|--------|
| `list_job_types()` | Job types available in the source | none | returns a list, not a dict |
| `skill_trends(job_type, skills=None, metric="share", n=5)` | Line chart of skills over time | `metric`: `"share"` or `"postings"` | `n` in 1..10; explicit `skills` at most 10 |
| `top_skills(job_type, n=10, start=None, end=None)` | Ranking by average share | `start`/`end`: ISO date strings | `n` in 1..25 |
| `skill_trend(job_type, skill, window_months=6)` | Direction and size of change for one skill | | no chart |
| `rising_and_falling_skills(job_type, window_months=6, n=5, min_share=0.02)` | Biggest share movers | `min_share`: ignore skills below this share | `n` in 1..25; `data` = `{"rising": [...], "falling": [...]}` |

Out-of-range `n` or too many explicit skills raise `ValueError` with a message. Callers
should catch it and pass the message back to the user rather than retrying blindly.

Note that `skill_trends` with no `skills` picks the top `n` by average share over the
whole history, so the same skills come back regardless of the time window.

## Definitions

These terms are used consistently across the code and the summaries:

- **share** = `postings / total_postings` for a job type and month. The fraction of that
  month's vacancies that mention the skill.
- **average share** = mean of monthly shares over the requested period (used for ranking).
- **trend window** = the latest `window_months` months compared with the
  `window_months` before them.
- **relative change** = `(after - before) / before`, where `before` and `after` are the mean
  share in each window. It is `None` when `before` is 0.
- **direction**: `rising` or `falling` when `|relative change| >= 0.5%` (`FLAT_BAND` in
  `analysis.py`), otherwise `flat`. `flat` is also used when the change is `None`.
- **min_share**: the minimum recent share (`share_after`) a skill needs to appear in
  rising/falling results. It exists because small bases give large, noisy percentages.

## Data contract

Every `DataSource` must return these shapes (see `data_source.py`):

- `skill_demand()`: `DataFrame[job_type, month (datetime64), skill, postings (int)]`,
  one row per (job_type, month, skill).
- `monthly_totals()`: `DataFrame[job_type, month (datetime64), total_postings (int)]`,
  one row per (job_type, month).
- `job_types()`: sorted list of job type names.

Analysis assumes months are contiguous and complete for each job type. Missing months
will distort the window averages. Check this when the database source is added.

## Changing the data source

1. Implement a class with `skill_demand`, `monthly_totals`, and `job_types` that meets the contract.
2. Change `get_source()` in `data_source.py` to return it.

Nothing else should change. If you find yourself editing `analysis.py` or `api.py` to
handle a new source, the source is returning the wrong shape.

## Adding a chart or function

1. Put the computation in `analysis.py` as a pure function taking a `DataSource`.
   Return a DataFrame or a dict.
2. Put the spec builder in `charts.py`. It takes the computed data and returns a dict.
   Reuse `THEME`, `PALETTE`, and the colour constants rather than hardcoding colours.
3. Expose it in `api.py` with validated arguments and a `summary` string. Use `_check_n`
   for range checks, and add a new upper bound constant if needed.
4. Optionally add it to `build_gallery()` in `preview.py`, and rerun `python -m visualization.preview`.

## Chart conventions

- Specs are plain dicts with `$schema` set to Vega-Lite v5. Data is embedded in
  `data.values`. Dates are serialized as `YYYY-MM-DD` strings by `_records`.
- Trend lines label each skill at its last point rather than using a legend.
- Bar charts sort by value. The change chart is anchored at zero.
- Number formats: `.0%`/`.1%` for shares, `+.1%` for changes, `,.0f` for counts.
- Use the `THEME` dict for fonts and axis styling so charts stay consistent.

## Known limitations

- **No tests.** Behaviour is checked by calling the functions and rendering the gallery. If
  you add tests, put them in a `tests/` folder and do not change the function signatures
  without updating the advisor.
- **Gallery notes are hardcoded.** The captions in `preview.py` say "Top 5" and "2%". Update
  them if the defaults change.
- **Vega sort warnings on the combined page.** When all four gallery charts are on one page,
  the browser logs two "conflicting sort properties" warnings. Each chart alone renders with
  no warnings, so this comes from the shared `skill` domain across charts.
- **`change_chart` has an unused `flat` colour** in its colour scale. The advisor never
  passes flat skills to it.
- **Preview needs internet.** Vega, Vega-Lite, and vega-embed are loaded from a CDN.
- **Stub data only.** The skill set, job types, and date range are synthetic
  (4 job types, 10 skills, 2024-01 to 2026-08).
