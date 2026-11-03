# Database: opbouw, velden en gebruik

De toepassing gebruikt DuckDB. De database staat standaard in `data/skillmarket.duckdb`. Gebruik het schema `skillmarket_app` voor applicatiequeries; `skillmarket_staging` bewaart ontvangen JSON en bedrijfsnormalisatie en `skillmarket_ingest` bewaart ontvangsthistorie. SQL-migraties staan in `skillmarket_staging/sql/` (versies 1–5).

## Opbouwen en controleren

Voer commando's uit vanuit de root van de repo. Python 3.11+:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m skillmarket_staging build
.venv/bin/python -m skillmarket_staging status
.venv/bin/python -m skillmarket_staging verify --require-complete
```

De actuele parseroutput is op Git aanwezig:

- `parser/skills.jsonl`: catalogus met vaste skill-ID's;
- `parser/batches/*/manifest.json`: batchidentiteit, versies en aantal records;
- `parser/batches/*/parsed.jsonl`: titel, skill-ID's en parserstatus per vacature.

De originele zes bestanden `vacancies_2021.parquet` t/m `vacancies_2026.parquet` zijn ook nodig. `build` zoekt eerst in `data/`, anders in `~/Downloads/`. Kies bij een andere locatie expliciet:

```bash
.venv/bin/python -m skillmarket_staging build --sources '/pad/naar/vacancies_*.parquet' --parser-directory parser
```

Gebruik een volledige bronset. De zeven oorspronkelijke velden zijn `vacancy_id`, `title`, `description`, `company`, `date_posted`, `country`, `city`. Een vacature-ID wordt als ongewijzigde string opgeslagen, ook als deze op een groot negatief getal lijkt.

`build` past migraties toe, normaliseert bedrijfsnamen, laadt metadata, valideert en ontvangt batches, publiceert het laatste resultaat per vacature, berekent maandtabellen, controleert de database en exporteert vijf applicatietabellen. Het commando eindigt met een foutcode bij ongeldige input, onbekende IDs, ontbrekende parserresultaten of inconsistente tellingen. Staging blijft bewaard wanneer publicatie faalt, zodat herstel mogelijk is. Een volledige build bestaat uit meerdere transacties; elke batch en de publicatiestap zijn afzonderlijk atomair.

Alleen de code, documentatie en reeds aangeleverde JSONL-parserdata staan op Git. `.duckdb`, `.wal`, originele Parquet-bronnen en gegenereerde exports blijven lokaal via `.gitignore`. Een teamlid moet de database dus eenmaal bouwen.

## Applicatietabellen en velden

### `skillmarket_app.postings`

Een rij per oorspronkelijke vacature. Alle velden zijn nullable tenzij anders vermeld.

| Veld | DuckDB-type | Betekenis |
|---|---|---|
| `posting_id` | VARCHAR, primary key | Originele `vacancy_id` als string; koppelsleutel naar parser en skills. |
| `title` | VARCHAR | Originele functietitel. |
| `company_raw` | VARCHAR | Originele bedrijfsnaam. |
| `company_normalized` | VARCHAR | Genormaliseerde merk-/groepsnaam. |
| `company_id` | VARCHAR | Stabiele `co_…`-identifier van die genormaliseerde naam. |
| `date_posted` | DATE | Originele publicatiedatum; kan ontbreken. |
| `country` | VARCHAR | Originele landwaarde. |
| `city` | VARCHAR | Originele stadswaarde. |
| `normalized_title` | VARCHAR | Ongewijzigde genormaliseerde titel uit de parser. |
| `role_family` | VARCHAR | Optionele functiefamilie van de parser; huidige output levert deze niet. |
| `job_type` | VARCHAR | Brede functiegroep: `role_family` indien geleverd, anders lokale trefwoordclassificatie van de genormaliseerde titel. |
| `parse_status` | VARCHAR, NOT NULL | `pending`, `ok` of `failed`. |
| `parser_version` | VARCHAR | Parserrelease uit het batchmanifest. |
| `taxonomy_version` | VARCHAR | Versie van de gedeelde skillcatalogus. |

`pending` betekent dat nog geen resultaat is gepubliceerd. `ok` kan ook nul gevonden skills hebben. `failed` is een expliciete parserfout en heeft geen actieve skillkoppelingen. De foutcode en foutmelding staan in het ontvangen JSON-record (zie verderop).

De huidige classifier levert tien groepen, in prioriteitsvolgorde: `risk and compliance`, `legal`, `customer service and support`, `sales and marketing`, `technology`, `finance and accounting`, `product and project management`, `human resources`, `operations`, `other`. Trefwoorden worden met woordgrenzen gematcht op unieke titels. De precieze regels staan in `skillmarket_staging/job_families.py`. Dit is een snelle hackathonindeling; de veel specifiekere titel blijft beschikbaar.

Bedrijfsnamen worden met tekstregels en een kleine aliastabel naar merk-/groepsnamen omgezet. De oorspronkelijke naam blijft behouden. Dit is geen juridisch bedrijfsregister; zie [companies.md](companies.md) voor de regels.

### `skillmarket_app.skills`

| Veld | Type | Betekenis |
|---|---|---|
| `skill_id` | VARCHAR, primary key | Vaste gedeelde ID uit `parser/skills.jsonl`. |
| `name` | VARCHAR, NOT NULL | Canonieke naam; gebruik deze exacte spelling in de visualisatie-API. |
| `category` | VARCHAR | Optionele categorie; huidige catalogus bevat null. |
| `synonyms` | JSON, NOT NULL | Array van alternatieve namen; standaard `[]`. |

Gebruik `skill_id` als identiteit, geen zelfberekende ID of getal uit de naam.

### `skillmarket_app.posting_skills`

| Veld | Type | Betekenis |
|---|---|---|
| `posting_id` | VARCHAR, NOT NULL | Verwijst logisch naar `postings.posting_id`. |
| `skill_id` | VARCHAR, NOT NULL | Verwijst logisch naar `skills.skill_id`. |

Elke combinatie komt eenmaal voor. Op tientallen miljoenen rijen vermijden we een grote primary-keyindex: de importer controleert posting-ID's en maakt skilllijsten uniek voordat hij schrijft. Schrijf daarom via de importer. Een herimport vervangt de volledige skillset van de betrokken vacatures. Relaties worden transactioneel door de toepassing bewaakt; niet met cross-schema foreign keys.

### Vooraf berekende maandtabellen

| Tabel | Velden en types | Sleutel |
|---|---|---|
| `skillmarket_app.monthly_totals` | `job_type VARCHAR`, `month DATE`, `total_postings BIGINT`; alle NOT NULL | (`job_type`, `month`) |
| `skillmarket_app.skill_demand` | `job_type VARCHAR`, `month DATE`, `skill VARCHAR`, `postings BIGINT`; alle NOT NULL | (`job_type`, `month`, `skill`) |

`month` is de eerste dag van de maand. `skill` is hier de canonieke **naam**, voor compatibiliteit met de bestaande visualisatie. De detailrelaties gebruiken skill-ID's.

- Alleen `ok`-vacatures met datum tellen mee.
- Vacatures met een succesvolle lege skilllijst tellen mee in `total_postings`.
- Elke skill telt per vacature eenmaal.
- Alle kalendermaanden tussen eerste en laatste waarneming per functiegroep zijn aanwezig, ook met nul vacatures.
- Een waargenomen skill krijgt voor die groep ook nulrijen in de overige maanden.
- Share is `postings / total_postings`; bij nul vacatures is deze onbekend (`NULL`/JSON `null`).

### `skillmarket_app.raw_vacancies`

Een view over de originele Parquet-bestanden, met de zeven originele kolommen. `description` staat bewust alleen in de bronnen: de grote teksten worden niet opnieuw in DuckDB gekopieerd. Alle metadata en parserresultaten staan wel fysiek in de database. Na het verplaatsen van bronbestanden werkt deze view weer na `load-vacancies` met de nieuwe paden. De maandtabellen en detailqueries zonder beschrijvingen hebben die bronbestanden na de build niet nodig.

### `skillmarket_app.parser_imports`

Succesvolle publicaties: `import_id UUID` (primary key), `format VARCHAR`, `parser_version VARCHAR`, `files JSON` (bronpaden voor Parquet), `row_count BIGINT`, `imported_at TIMESTAMPTZ`. Bij JSONL zijn `parser_version='from-batch-manifests'` en `files=[]`; de concrete versies staan per posting en in `skillmarket_ingest.batches`.

## Cursussen en dezelfde skillcatalogus

`skillmarket_app.courses`, `course_skills` en `course_imports` vormen een afzonderlijke aanvulling. Course-IDs tellen nooit mee als vacatures. Zie [courses.md](courses.md) voor alle velden, aanbevelingsqueries en import.

## SQL voor het team

Open verbindingen kort en gebruik parameters voor input van de chat/agent:

```python
import duckdb

with duckdb.connect("data/skillmarket.duckdb", read_only=True) as db:
    rows = db.execute("""
        SELECT posting_id, normalized_title, company_normalized, date_posted
        FROM skillmarket_app.postings
        WHERE parse_status = 'ok' AND job_type = ?
        ORDER BY date_posted DESC NULLS LAST
        LIMIT 20
    """, ["technology"]).fetchdf()
```

### Skilltrends

```sql
SELECT d.month, d.skill, d.postings, t.total_postings,
       d.postings::DOUBLE / nullif(t.total_postings, 0) AS share
FROM skillmarket_app.skill_demand d
JOIN skillmarket_app.monthly_totals t USING (job_type, month)
WHERE d.job_type = 'technology' AND d.skill = 'python'
ORDER BY d.month;
```

Gebruik share om perioden met verschillende aantallen vacatures te vergelijken. De bron loopt van januari 2021 tot september 2026. De verzameling was onvolledig van januari tot april 2026: een daling daar kan door ontbrekende data komen. Een trend vergelijkt beschikbare historie en voorspelt geen toekomstige vraag. Zie ook [de bronbeschrijving](../data/README.md).

### Skills bij een vacature

```sql
SELECT s.skill_id, s.name
FROM skillmarket_app.posting_skills ps
JOIN skillmarket_app.skills s USING (skill_id)
WHERE ps.posting_id = ?
ORDER BY s.name;
```

### Profieladvies: welke ene skill opent extra matches?

Onderstaande voorbeeldquery zoekt vacatures waarbij een profiel met `S001` en `S002` precies één aangetroffen skill mist. Die laatste skill leren zou, volgens de parserlijsten, extra volledige matches geven. Vervang de lijst en filters door de gebruikersinput, bij voorkeur als gebonden parameters. Er worden geen persoonlijke profielen in deze database opgeslagen.

```sql
WITH profile AS (
    SELECT unnest(['S001', 'S002']) AS skill_id
), gaps AS (
    SELECT ps.posting_id,
           list(ps.skill_id) FILTER (WHERE pr.skill_id IS NULL) AS missing
    FROM skillmarket_app.posting_skills ps
    JOIN skillmarket_app.postings p USING (posting_id)
    LEFT JOIN profile pr USING (skill_id)
    WHERE p.parse_status = 'ok'
      AND p.job_type = 'technology'
      AND p.date_posted >= DATE '2025-01-01'
    GROUP BY ps.posting_id
)
SELECT s.skill_id, s.name, count(*) AS extra_matching_postings
FROM gaps g
CROSS JOIN unnest(g.missing) AS u(skill_id)
JOIN skillmarket_app.skills s USING (skill_id)
WHERE len(g.missing) = 1
GROUP BY s.skill_id, s.name
ORDER BY extra_matching_postings DESC, s.name
LIMIT 10;
```

Deze telling behandelt de gevonden skills als vereisten. Ze is bedoeld als hackathondemonstratie van matching en bevat geen aannames over ervaring, diploma of echte selectiecriteria.

## Visualisatie-API

```python
from visualization import api

print(api.list_job_types())
trends = api.skill_trends("technology", skills=["python"])
top = api.top_skills("technology", n=10)
movers = api.rising_and_falling_skills("technology", window_months=6)
one = api.skill_trend("technology", "python", window_months=6)
```

`DatabaseSource` leest de vooraf berekende tabellen en vernieuwt zijn cache wanneer het DB-bestand wijzigt. De vergelijking van alle stijgers/dalers wordt gezamenlijk berekend, zodat niet voor iedere skill opnieuw de volledige dataset wordt gelezen. Alle bovenstaande resultaten zijn JSON-serialiseerbaar.

Omgevingsvariabelen (optioneel):

```bash
export SKILLMARKET_DB='/absoluut/pad/naar/data/skillmarket.duckdb'
export SKILLMARKET_SOURCE=duckdb
```

Standaard is de bron `auto`: een bestaande applicatiedatabase wordt gebruikt, ook als ze nog leeg is. `duckdb` vereist de database expliciet. `stub` kiest de fictieve demo. De API gebruikt skillnamen, profielmatching gebruikt skill-ID's.

## Nieuwe data ontvangen en fouten bekijken

```bash
.venv/bin/python -m skillmarket_staging init
.venv/bin/python -m skillmarket_staging load-vacancies '/pad/naar/vacancies_*.parquet'
.venv/bin/python -m skillmarket_staging import-parser parser
.venv/bin/python -m skillmarket_staging verify --require-complete
.venv/bin/python -m skillmarket_staging export-database
```

Een JSONL-hoofdmap wordt recursief doorzocht op `manifest.json` naast `parsed.jsonl`. `skills.jsonl` mag naast een batch, in de opgegeven hoofdmap of één niveau erboven staan. Reeds geregistreerde catalogi kunnen ook hergebruikt worden.

Dezelfde batch opnieuw aanbieden wordt als `duplicate` gelogd. Gewijzigde inhoud vereist een **nieuwe `batch_id`**; een cataloguswijziging een **nieuwe taxonomieversie**. Bij meerdere resultaten voor hetzelfde posting-ID wint het laatst ontvangen nieuwe batchresultaat. Bestaande staginghistorie blijft behouden. Ontvangst en publicatie zijn gescheiden; na handmatige `receive` is `publish` nodig.

```bash
.venv/bin/python -m skillmarket_staging runs --limit 20
.venv/bin/python -m skillmarket_staging batches --limit 20
.venv/bin/python -m skillmarket_staging records --batch-id 2021-part-001-run-001 --limit 5
```

Parserfouten bekijken:

```sql
SELECT r.source_record_id AS posting_id,
       json_extract_string(r.payload, '$.error.code') AS code,
       json_extract_string(r.payload, '$.error.message') AS message
FROM skillmarket_staging.records r
WHERE json_extract_string(r.payload, '$.parse_status') = 'failed'
LIMIT 20;
```

### Ontvangst- en stagingtabellen

| Tabel | Beschikbare velden |
|---|---|
| `skillmarket_staging.records` | `batch_key UUID`, `line_number BIGINT`, `source_record_id VARCHAR`, `payload JSON`. Sleutel batch + regel; uniek batch + posting-ID. |
| `skillmarket_staging.company_names` | `company_raw`, `company_normalized`, `company_id`, `normalization_method`, `normalizer_version` (VARCHAR). Sleutel `company_raw`. |
| `skillmarket_ingest.import_runs` | `run_id UUID`, `operation`, `status`, `batch_id`, `source_name`, `manifest JSON`, `manifest_sha256`, `records_sha256`, `expected_records BIGINT`, `received_records BIGINT`, `source_ids_verified BOOLEAN`, `duplicate_of UUID`, `error_code`, `error_message`, `started_at TIMESTAMPTZ`, `finished_at TIMESTAMPTZ`. Overige velden VARCHAR. Ook mislukte pogingen blijven staan. |
| `skillmarket_ingest.batches` | `batch_key UUID`, `batch_id`, `source_run_id UUID`, `contract_version`, `parser_version`, `catalog_version`, `manifest JSON`, `manifest_sha256`, `records_sha256`, `record_count BIGINT`, `ok_records BIGINT`, `failed_records BIGINT`, `source_ids_verified BOOLEAN`, `staged_at TIMESTAMPTZ`. Overige velden VARCHAR. |
| `skillmarket_ingest.catalogs` | `catalog_version VARCHAR`, `content_sha256 VARCHAR`, `item_count BIGINT`, `source_run_id UUID`, `loaded_at TIMESTAMPTZ`. |
| `skillmarket_ingest.catalog_items` | `catalog_version VARCHAR`, `item_id VARCHAR`, `payload JSON`. Sleutel versie + skill-ID. |
| `skillmarket_ingest.schema_migrations` | `version INTEGER`, `applied_at TIMESTAMPTZ`. |

`source_ids_verified` gaat specifiek over de optionele controle met een apart bestand van verwachte batch-ID's. Het kan false zijn terwijl de applicatiepublicatie de koppeling aan alle geladen originele vacatures heeft gecontroleerd. `verify --require-complete` controleert bovendien dat geen geladen vacature pending blijft.

Een oudere `labeled_*.parquet`-adapter blijft beschikbaar voor compatibiliteit. Hij gebruikt technische `name_…`-IDs en is niet de huidige Git-aanleverroute. Combineer verschillende taxonomiesystemen niet ongemerkt in een dataset.

## Exports en procesafspraken

`export-database` schrijft naar `data/`, of een map via `--directory`:

| Bestand | Kolommen |
|---|---|
| `postings.parquet` | `posting_id`, `company` (genormaliseerd), `company_raw`, `company_id`, `date`, `period` (`YYYY-Qn`), `country`, `city`, `title`, `normalized_title`, `role_family`, `job_type`, `parse_status`. |
| `skills.parquet` | `skill_id`, `name`, `category`, `synonyms` (stringlijst). |
| `posting_skills.parquet` | `posting_id`, `skill_id`. |
| `monthly_totals.parquet` | `job_type`, `month`, `total_postings`. |
| `skill_demand.parquet` | `job_type`, `month`, `skill`, `postings`. |

Gebruik één schrijvend proces voor builds/imports. Sluit externe DuckDB-verbindingen voordat je importeert; DuckDB heeft geen aparte server die gelijktijdige schrijvers coördineert. Na de build kunnen meerdere processen de gesloten database read-only openen. Maak backups door het gesloten DB-bestand te kopiëren.

De ondersteunde importweg bewaakt unieke relaties en geldige IDs. Handmatige wijzigingen aan applicatietabellen kunnen de maandtabellen ongeldig maken. Gebruik de importcommando's en voer daarna `verify` uit. Standaard verifieert dit relaties en maandtotalen; `--require-complete` vereist ook een niet-lege dataset zonder pending resultaten. Geldige `failed`-resultaten zijn geen ontbrekende aanleveringen.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

De tests gebruiken tijdelijke databases met fictieve data. Ze controleren contractvalidatie, duurzame foutlogging, duplicaten, correcties, bedrijfsnormalisatie, JSONL- en Parquet-import, lege skilllijsten, functiegroepering, datumgaten, een volledige build, integriteitscontroles, exports en de aansluiting op de visualisatie-API. De definitieve echte-datarun wordt daarnaast met `verify --require-complete` en de visualisatie-API gecontroleerd.
