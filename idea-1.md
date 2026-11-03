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

## Rating against the jury's scoring (honest estimate)

| Criterion | Max | Estimate | Why |
|---|---|---|---|
| Solution value | 6 | 2–3 | The case asks to help a **company** see where it stands; this is B2C. Jobseekers rarely pay. The graph tool alone is a dashboard. The advisor does give real advice, which helps. |
| Cost & scalability | 6 | 4–5 | Strong if parsing runs on the GPU and Claude only labels a sample and writes final advice. Shares + incremental updates scale to 100×. |
| Creativity | 4 | 2–3 | "Skill stock market" framing and linking vacancies to study programmes are original. Skill demand from postings itself is TechWolf's core business. |
| Pitch | 4 | 2–4 | Depends on a working demo. Three products is hard to show and explain in 5 minutes. |
| **Total** | **20** | **~10–14** | |

## Main risks
1. **Scope:** graph tool + profile advisor + study advisor + study-programme parser is four products. With ~4.5 hours left, one working path beats four half-features.
2. **Study programme data is not in the provided data lake.** Scraping and parsing study guides (UGent, KU Leuven, …) costs time and adds a second messy dataset. Cut this first if time is short.
3. **Finance-only data:** advice to a jobseeker only covers finance companies in BE/NL/LU/UK. Say so in the pitch.
4. **Who pays?** Expect the question. Have a buyer ready (see below).
5. **Quality proof:** the jury will ask "how do you know the skills are correct?" Hand-check ~100 postings and report precision.

## How to raise the score without changing the build
- **Name a paying customer:** universities (curriculum planning: "which skills is the market asking for that our programmes don't teach?") or public employment services like VDAB (reskilling advice). Both are organisations, which fits the "help an organisation see where it stands" brief better.
- **Optional B2B twist:** the same profile advisor run over a bank's own employees (synthetic internal data) = "which of your people need which skills, retrain vs hire." Same code, much stronger value score.
- **Lead with the AI angle:** show AI skills as the fastest-rising "stocks" in finance roles, since that's the case's theme.

## Suggested demo path (one thing that works)
1. Graph: "AI skills in finance roles, 2021 → 2026" (the headline insight).
2. Profile advisor: a sample profile → "your top rising skill, your falling skill, learn X to unlock N more vacancies."
3. Study advisor only if it works by 18:00; otherwise mention it as next step.

## Not doing
Live scraping of vacancies, user accounts, mobile app, salary-based advice (salary is missing in many postings — check the sample before promising it).
