# DuckDB gebruiken

De werkende database heet `data/skillmarket.duckdb`. De applicatie leest tabellen in het schema **`skillmarket_app`**. Er is geen databaseserver of wachtwoord nodig.

## Aanmaken na een clone

Python 3.11+ is vereist. Voer dit uit vanuit de root van de repo:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m skillmarket_staging build
.venv/bin/python -m skillmarket_staging verify --require-complete
```

De geparste data staat al op Git in `parser/batches/`, met de gedeelde skilllijst in `parser/skills.jsonl`. De zes originele bestanden `vacancies_2021.parquet` t/m `vacancies_2026.parquet` moeten lokaal in `data/` of `~/Downloads/` staan. Ze bevatten de bedrijfsnamen, datums en locaties die niet in de parseroutput zitten. Een andere locatie kan met:

```bash
.venv/bin/python -m skillmarket_staging build --sources '/pad/naar/vacancies_*.parquet'
```

`build` voert migraties, bedrijfsnormalisatie, bronimport, strikte ontvangst van alle parserbatches, publicatie, controles en Parquet-exports uit. Voortgang verschijnt op stderr; het eindresultaat is JSON op stdout. Opnieuw uitvoeren behoudt de historie en telt vacatures of skills niet dubbel.

De grote DuckDB, bronbestanden en gegenereerde Parquet-exports worden bewust niet in Git opgeslagen. Elk teamlid kan dezelfde database met dit commando lokaal opbouwen.

## In de applicatie

```python
import duckdb

with duckdb.connect("data/skillmarket.duckdb", read_only=True) as db:
    jobs = db.execute("""
        SELECT posting_id, normalized_title, company_normalized, date_posted
        FROM skillmarket_app.postings
        WHERE parse_status = 'ok' AND job_type = ?
        LIMIT 10
    """, ["technology"]).fetchdf()
```

Gebruik vaste `skill_id`-waarden voor profielmatching. `normalized_title` bewaart de parseroutput; `job_type` is een brede functiegroep voor grafieken. `role_family` is optionele parserinput en is in de huidige batches niet ingevuld.

De bestaande visualisatie-API kiest de lokale database automatisch:

```python
from visualization import api

print(api.list_job_types())
chart = api.skill_trends("technology", skills=["python"])
advice = api.rising_and_falling_skills("technology")
```

Voor de volledige velddefinities, SQL voor trends en profieladvies, importbeheer en gebruiksafspraken: **[docs/database.md](docs/database.md)**. Het parsercontract staat in [parser-db-contract.md](parser-db-contract.md).

De volledige dataset is opgebouwd en getest: zie [het verificatierapport](docs/database-validation.md).
