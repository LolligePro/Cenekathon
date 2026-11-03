# Parser method and status

Reference for agents working on `parser/`. Covers how skills and job titles are extracted, what was produced, and what is known to be weak.

## Goal

Per vacancy in `data/vacancies_2021.parquet` to `vacancies_2026.parquet` (1,542,169 postings): a normalised job title and a list of fixed skill IDs, delivered in the format of `parser-db-contract.md` (contract v1.0, Dutch, at the workspace root). Team Database owns storage and validation.

## Pipeline

1. **LLM sample (`extract_skills.py`)**
   - Model: `qwen2.5:7b-instruct` served by Ollama at `http://localhost:11434`.
   - Binary is at `~/.local/ollama/bin/ollama`, not in the repo. Start with `LD_LIBRARY_PATH=$HOME/.local/ollama/lib/ollama OLLAMA_NUM_PARALLEL=8 ~/.local/ollama/bin/ollama serve`. It does not restart on reboot.
   - Input: 500 random postings per year (seed 42), 3,000 total. HTML stripped, description truncated to 4,000 characters.
   - Output: `parser/skills.sqlite`, tables `vacancies` (category, country, date) and `vacancy_skills`.
   - Speed: about 3 postings/s with 8 workers. Full dataset with the LLM would take about 128 hours, so it is only used for the sample.
2. **Taxonomy (`build_taxonomy.py --min-df 5`)**
   - Skills kept if they appear in at least 5 sample postings.
   - Aliases: "X skill(s)" maps to "X" when "X" is also kept.
   - Writes `parser/taxonomy.json` (aliases used by the matcher) and `parser/skills.jsonl` (contract format, `skill_taxonomy_version` `skills-v1`, 1,202 skills, IDs `S001`–`S1202` ordered by sample frequency).
   - Prints validation against the LLM labels.
3. **Matcher (`matching.py`)**
   - Tokenises with `TOKEN_RE`, splits text into sentences, and matches multi-word aliases up to `max_tokens` long.
   - Skips sentences that contain company-info markers ("about us", "we are a", …).
   - Skips a skill if a negator ("no", "not", "without", …) appears within the 3 tokens before it.
   - `normalise_title` lowercases, removes gender tags (m/f/d/x), bracketed text and seniority words, then keeps the rest.
   - `job_type` keyword rules still exist in the file but are not written to the output (not in the contract).
4. **Batch writer (`match_all.py`)**
   - Reads each year in 20,000-row batches, parses them in parallel (11 workers), validates, and writes `parser/batches/batch-YEAR-NNN/`.
   - Each batch writes `parsed.jsonl` and `manifest.json` into a `.tmp-` folder first, then renames it. The manifest is written last, so a folder with a manifest is complete.
   - Existing batches are skipped on re-run, so an interrupted run resumes.
   - Full run: `python3 match_all.py --workers 11` (about 2.5 minutes). Run it in the background with a log, since a foreground run can be cut off.

## Output (contract v1.0)

- `manifest.json`: `contract_version` "1.0", `batch_id` "YEAR-part-NNN-run-001", `parser_version` "rules-v1", `skill_taxonomy_version` "skills-v1", `record_count`.
- `parsed.jsonl`: one object per input posting with `posting_id` (the original `vacancy_id`, unchanged), `parse_status`, `normalized_title`, `skill_ids`, `error`.
- Failures use `INVALID_OUTPUT` (empty normalised title) or `INTERNAL_ERROR`, with `EMPTY_INPUT` for postings with no text.

Current run:

- 79 batches, 1,542,169 records, all `posting_id`s unique, `record_count` matches every batch.
- 1,541,614 `ok`, 555 `failed` (all `INVALID_OUTPUT`).
- 15,205 `ok` records have an empty `skill_ids` list. These are valid successes.
- Average 23.7 skills per `ok` record.

## Known weaknesses

- **Skill quality is poor.** On the 3,000 sample postings, against LLM labels: precision 16.96%, recall 47.72%. The average of 23.7 skills per posting is too high, and generic soft skills ("communication", "problem solving") dominate.
- **Job title normalisation is unvalidated.** Job-type agreement with the LLM category was 54.7% on the sample. Titles are lowercased tokens, not canonical job names.
- The LLM labels are themselves unvalidated, so they are a weak ground truth.
- Contract accuracy checks (structure, IDs, counts) pass. Content accuracy has not been reviewed.
- No `role_family` field (optional, not agreed).

## Next steps

1. Improve precision: restrict to skill-like phrases, drop soft skills or move them to a separate list, and tune `--min-df`.
2. Review a random sample of normalised titles and fix normalisation rules.
3. Produce the contract's first test batch (about 10 postings with an ok, an empty-skill ok, and a failed record) and agree the field list with Team Database.
4. Settle `role_family` and any extra fields before adding them.

## Caveats

- Data use: the dataset README restricts use to CenEkathon and forbids sharing the data or the download links outside the team. The links expire on 3 November 2026 at 23:59 Brussels time.
- Generated data (`parser/batches/`, `parser/skills.sqlite`, `parser/results/`, `parser/match_all.log`) is local output. Check `.gitignore` before committing it.
- `parser/results/labeled_2021.parquet` is from an earlier version of the batch writer and is not contract output.
- Do not change the meaning of an existing `skill_taxonomy_version` or `parser_version`. Create `skills-v2` or `rules-v2` instead.
