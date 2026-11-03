# Cursussen en skills gebruiken

De centrale serverdatabase bevat **8.092 cursussen** en **16.852 unieke course-skill-koppelingen** uit de aangeleverde Git-data. Er zijn 6.152 cursussen met minstens een skill en 1.940 zonder herkende skills. Alle koppelingen gebruiken de bestaande `skills-v1`-IDs; er worden geen nieuwe skills bedacht.

## Opvragen vanuit de applicatie

Gebruik dezelfde [SSH-tunnel en RemoteDatabase-client](database-server.md) als voor vacatures:

```python
from skillmarket_staging.client import RemoteDatabase

db = RemoteDatabase()
courses = db.dataframe("""
    SELECT c.course_id, c.title, c.url, c.site,
           count(DISTINCT cs.skill_id) AS covered_skills,
           list(DISTINCT s.name ORDER BY s.name) AS skill_names
    FROM skillmarket_app.courses c
    JOIN skillmarket_app.course_skills cs USING (course_id)
    JOIN skillmarket_app.skills s USING (skill_id)
    WHERE cs.skill_id IN (?, ?)
    GROUP BY c.course_id, c.title, c.url, c.site
    ORDER BY covered_skills DESC, c.title, c.course_id
    LIMIT 10
""", ["S024", "S091"])  # Vervang door de ontbrekende skill-IDs van het profiel.
print(courses)
```

`covered_skills` telt hier alleen de gevraagde skills. Het is een telling van parserkoppelingen, geen garantie over leerresultaten. Haal alle skills van een cursus op met:

```sql
SELECT s.skill_id, s.name
FROM skillmarket_app.course_skills cs
JOIN skillmarket_app.skills s USING (skill_id)
WHERE cs.course_id = ?
ORDER BY s.name;
```

Op de server kan dezelfde SQL rechtstreeks met DuckDB `read_only=True` worden uitgevoerd op `/home/team7/skillmarket-db/skillmarket.duckdb`. Er is geen extra API-endpoint nodig. `GET /schema` toont de nieuwe tabellen; de bestaande `/health`-tellingen blijven over vacatures gaan.

## Tabellen en velden

`skillmarket_app.courses` bevat een rij per cursus:

| Veld | Type | Betekenis |
|---|---|---|
| `course_id` | VARCHAR, primary key | Ongewijzigde `posting_id` uit de course-JSONL, bijvoorbeeld `coursera-00000`. |
| `title`, `url`, `site` | VARCHAR, NOT NULL | Originele titel, cursuslink en platform. |
| `short_intro` | VARCHAR, nullable | Korte beschrijving. |
| `listed_skills` | VARCHAR[], NOT NULL | Originele skillteksten; gebruik deze niet als koppelsleutel. |
| `skill_source` | VARCHAR, nullable | `listed`: uit expliciete skilllijst; `text`: uit titel/tekst; null: geen koppeling gevonden. |
| `normalized_title` | VARCHAR, nullable | Genormaliseerde titel uit de parser. |
| `parse_status` | VARCHAR, NOT NULL | `ok` of `failed`; een succesvolle cursus mag nul skills hebben. |
| `parser_version`, `taxonomy_version`, `batch_id` | VARCHAR, NOT NULL | Herkomst van het gepubliceerde parserresultaat. |
| `metadata`, `parser_payload` | JSON, NOT NULL | Originele course-metadata en parserrecord, inclusief eventuele foutmelding. |

`skillmarket_app.course_skills` bevat `course_id VARCHAR` en `skill_id VARCHAR`, beide NOT NULL, met een samengestelde primary key. Join `skill_id` naar `skillmarket_app.skills.skill_id`, dezelfde catalogus als bij vacatures.

`skillmarket_app.course_imports` bewaart per onveranderlijke `batch_id` de `run_id UUID`, `manifest JSON`, drie SHA256-hashes (`manifest_sha256`, `metadata_sha256`, `records_sha256`), `course_count BIGINT`, `skill_link_count BIGINT` en `imported_at TIMESTAMPTZ`.

Alle importpogingen, ook fouten en duplicaten, staan bovendien in `skillmarket_ingest.import_runs` met `operation='import_courses'`.

## Importeren of bijwerken

Vanuit de repo, nadat de vacaturedatabase en gedeelde skillcatalogus zijn opgebouwd:

```bash
.venv/bin/python -m skillmarket_staging import-courses courses
```

Een expliciete database kan met `--database` voor het subcommando:

```bash
.venv/bin/python -m skillmarket_staging --database /pad/skillmarket.duckdb import-courses courses
```

De invoer is `courses/courses.jsonl` plus precies een map onder `courses/batches/` met `manifest.json` en `parsed.jsonl`. Indien aanwezig wordt `input-ids.txt` ook gecontroleerd. Bij meerdere batches kies je er een met `--batch-directory`; de metadata moet precies bij die batch horen.

De importer past schema-migratie 5 toe, controleert het bestaande parsercontract, metadata-IDs, unieke IDs, aantallen en alle skill-IDs, en schrijft daarna met bulk-SQL in een transactie. Hij herbouwt geen vacatures of maandtabellen. Course-records komen niet in de vacaturestaging, zodat ze latere vacaturepublicatie niet verstoren. De gedeelde catalogus moet al in zowel het importregister als de applicatietabel bestaan.

Dezelfde batch opnieuw importeren levert `duplicate` en telt niets dubbel. Veranderde metadata of parseroutput vereist een nieuwe `batch_id`. Een nieuwe batch vervangt voor de aangeleverde course-IDs de metadata en volledige skillset; andere cursussen blijven staan. Een `failed`-resultaat verwijdert eerdere skillkoppelingen voor die cursus. Foutieve invoer verandert geen gepubliceerde cursusdata.

`build` en de vijf bestaande Parquet-exports blijven op vacatures gericht; gebruik `import-courses` voor deze aparte aanvulling. Stop lezende processen voordat je hetzelfde bestand rechtstreeks beschrijft. Op de teamserver is deze eerste import vooraf getest op een kopie en daarna met een korte serviceherstart geactiveerd; de vorige database blijft als backup staan.

## Uitgevoerde controles

Op de teamserver: import van 8.092 cursussen en 16.852 koppelingen in 3,587 seconden; identieke herimport in 0,426 seconden als `duplicate`. Het uploadpakket was 1,2 MB; de grote vacaturedatabase is niet opnieuw over het netwerk verstuurd. De serviceherstart en controle duurden 0,162 seconden. Dit zijn eenmalige metingen op de hackathonmachine.

Alle 1.542.169 vacatures, 36.538.876 vacature-skillrelaties, 690 maandtotalen en 793.155 trendrijen zijn behouden. De bestaande volledige integriteitscontrole slaagt; er zijn geen course-koppelingen naar onbekende skills of ontbrekende cursussen. Aanbevelingsqueries zijn via de centrale HTTP-service en de laptopclient getest. De 54 unittest-cases, inclusief vijf nieuwe course-importtests, slagen op DuckDB 1.5.6 en 2.0.0.
