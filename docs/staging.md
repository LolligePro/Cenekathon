# DuckDB-basis voor team Database

Deze basis ontvangt de bestanden uit `parser-db-contract.md`, valideert de overdracht en bewaart de JSON-objecten in een staginglaag. De toepassingsdatabase en koppeling met de visualisatie staan beschreven in [database.md](database.md).

Er is geen databaseserver of wachtwoord nodig. De database is één lokaal DuckDB-bestand. Getest met DuckDB 2.0.0 en Python 3.14.

## 1. Installeren en initialiseren

Voer de opdrachten uit vanuit de Cenekathon-repo. Gebruik de bestaande virtuele omgeving of maak er een aan:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m skillmarket_staging init
```

Bij gebruik van uv kan de installatie ook met:

```bash
uv pip install --python .venv/bin/python -e .
```

De standaarddatabase is `data/skillmarket.duckdb`. Opnieuw `init` uitvoeren laat de bestaande gegevens intact.

Een ander bestand selecteren kan per opdracht:

```bash
.venv/bin/python -m skillmarket_staging --database data/ander-bestand.duckdb init
```

Of met de omgevingsvariabele `SKILLMARKET_DB`. Relatieve bestandspaden worden geïnterpreteerd ten opzichte van de huidige werkmap. Er wordt geen `.env` automatisch geladen.

## 2. Klaar voor de echte parseroutput

Registreer eerst de echte gedeelde skilllijst, met precies de versie uit het manifest:

```bash
.venv/bin/python -m skillmarket_staging load-skills data/incoming/skills.jsonl --taxonomy-version skills-v1
```

Ontvang daarna een map met `manifest.json` en `parsed.jsonl`:

```bash
.venv/bin/python -m skillmarket_staging receive data/incoming/batch-001
```

De registry is alleen een controle op bekende skill-ID’s. Dit is nog geen definitief relationeel skillmodel. Een geregistreerde skilllijstversie is onveranderlijk; gewijzigde inhoud vereist een nieuwe versie.

Bij ontvangst zonder bron-ID-bestand is `source_ids_verified` expliciet `false`: dan zijn het formaat, de aantallen, de interne uniciteit en de skill-ID’s gecontroleerd, maar nog niet de gelijkheid met de oorspronkelijke inputselectie.

Geef voor die laatste controle een UTF-8-bestand mee met één oorspronkelijk vacature-ID per regel:

```bash
.venv/bin/python -m skillmarket_staging receive data/incoming/batch-001 --expected-ids data/incoming/input-ids.txt
```

De volgorde van die ID’s mag verschillen van de parseroutput. ID’s worden niet getrimd, numeriek gemaakt of van hoofdlettergebruik veranderd. Het ID-bestand zelf mag geen lege of dubbele ID-regels bevatten.

## 3. Een volledige ontvangst proberen met fictieve data

Gebruik een aparte demonstratiedatabase, zodat voorbeelden buiten de echte staging blijven:

```bash
.venv/bin/python -m skillmarket_staging --database data/demo-staging.duckdb init
.venv/bin/python -m skillmarket_staging --database data/demo-staging.duckdb load-skills examples/staging/skills.jsonl --taxonomy-version skills-v1
.venv/bin/python -m skillmarket_staging --database data/demo-staging.duckdb receive examples/staging/batch --expected-ids examples/staging/input-ids.txt
.venv/bin/python -m skillmarket_staging --database data/demo-staging.duckdb batches
.venv/bin/python -m skillmarket_staging --database data/demo-staging.duckdb records --batch-id demo-batch-001
.venv/bin/python -m skillmarket_staging --database data/demo-staging.duckdb runs
```

Verwacht: drie records, waarvan twee met een geslaagde parserstatus en één met een geldige parserfout. Een tweede ontvangst van dezelfde batch levert `duplicate` op en voegt geen records toe.

## 4. Opslagindeling

| Tabel | Functie |
|---|---|
| `skillmarket_ingest.schema_migrations` | Toegepaste versies van de technische databasebasis. |
| `skillmarket_ingest.import_runs` | Iedere importpoging: status, start/eindtijd, aantallen, hashes en foutreden. |
| `skillmarket_ingest.catalogs` | Onveranderlijke versies van de gedeelde skilllijst. |
| `skillmarket_ingest.catalog_items` | Bekende IDs en de aangeleverde catalogusobjecten als JSON. |
| `skillmarket_ingest.batches` | Metadata van succesvol ontvangen batches, versies, hashes en resultaataantallen. |
| `skillmarket_staging.records` | Ontvangen parserobjecten, zonder definitieve businesskolommen. |

`skillmarket_staging.records` heeft slechts vier kolommen:

```text
batch_key          UUID       technische sleutel van de aanlevering
line_number        BIGINT     oorspronkelijke regelpositie
source_record_id   VARCHAR    ongewijzigd posting_id
payload            JSON       het volledige ontvangen parserobject
```

De JSON-inhoud blijft semantisch gelijk; opmaak en volgorde van objectsleutels kunnen veranderen. Bewaar de oorspronkelijke aanleverbestanden als exacte bytes nodig zijn. Het logboek bevat een SHA-256 van het oorspronkelijke JSONL-bestand.

De stagingrecords leggen zelf geen businesskolommen vast. De toepassingstabellen in het aparte schema `skillmarket_app` worden vanuit deze laag gevuld. De v1-contractvalidator staat los van de opslag. Bij extra afgesproken parservelden moet de contractvalidator worden aangepast; de JSON-kolom zelf kan ze al bewaren.

## 5. Wat de ontvanger controleert

- Exacte veldnamen en verplichte waarden volgens contract v1.0.
- Eén JSON-object per regel; UTF-8, zonder lege regels.
- Geen dubbele JSON-sleutels, ongeldige JSON of niet-standaard waarden zoals `NaN`.
- Correct onderscheid tussen `ok`, `failed` en een succesvolle lege skilllijst.
- Bekende skill-ID’s en geen dubbele skills in één resultaat.
- Manifest-aantal gelijk aan het aantal resultaatregels.
- Geen dubbele vacature-ID’s binnen de batch.
- Optioneel: precies dezelfde ID-set als de oorspronkelijke inputselectie.

Een geldige parserfout is een ontvangen record en krijgt dus een plek in staging. Een structurele contractfout blokkeert de hele batch.

## 6. Herlevering en foutafhandeling

Iedere poging krijgt een eigen `run_id`. Mogelijke logstatussen:

| Status | Betekenis |
|---|---|
| `started` | Een poging is begonnen; nog geen afgeronde uitkomst geregistreerd. |
| `loaded` | Een skilllijstversie is geregistreerd. |
| `staged` | Een volledige parserbatch is ontvangen. |
| `duplicate` | Deze ongewijzigde catalogus of batch was al ontvangen. |
| `failed` | De poging mislukte; zie `error_code` en `error_message`. |

Dezelfde `batch_id` met hetzelfde manifest en exact dezelfde JSONL-bytes is herhaalbaar. Verschillen in manifest-opmaak zijn toegestaan, omdat het manifest canoniek wordt gehasht. Gewijzigde JSONL-opmaak, regelvolgorde of inhoud vereist een nieuw batch-ID.

Batchmetadata, alle stagingrecords en de succesvolle loguitkomst worden in één transactie vastgelegd. Bij een fout verdwijnen eventuele gedeeltelijke inserts, terwijl de mislukte poging apart in het logboek wordt bewaard.

Een correctie met een nieuw batch-ID bewaart de eerdere aanlevering als historie. Er vindt nog geen automatische samenvoeging naar een definitief vacaturemodel plaats.

Een harde processtop kan een poging op `started` laten staan. De datatransactie wordt dan bij herstel teruggedraaid. Inspecteer zulke pogingen via `runs` en bied de batch opnieuw aan. Een fout vóór de database geopend kan worden, bijvoorbeeld een bestandslock, kan nog niet in die database worden gelogd.

Bij een latere herlevering met een bron-ID-bestand kan een eerdere ID-controle alsnog in de nieuwe poging worden geregistreerd. Batchmetadata beschrijft de eerste succesvolle ontvangst; het nieuwe logrecord bevat de aanvullende verificatie.

## 7. Inspectie en SQL

```bash
.venv/bin/python -m skillmarket_staging runs --limit 20
.venv/bin/python -m skillmarket_staging batches --limit 20
.venv/bin/python -m skillmarket_staging records --batch-id batch-001 --limit 10
```

De opdrachten retourneren JSON. Importfouten geven exitcode 1 en, zodra beschikbaar, het `run_id` waarmee je de fout kunt terugvinden.

Voorbeelden van rechtstreekse inspectie in DuckDB:

```sql
SELECT run_id, operation, status, batch_id, received_records, error_message
FROM skillmarket_ingest.import_runs
ORDER BY started_at DESC;

SELECT source_record_id,
       payload->>'$.normalized_title' AS normalized_title,
       payload->>'$.parse_status' AS parse_status
FROM skillmarket_staging.records
LIMIT 10;
```

## 8. Werkwijze met DuckDB

Eén proces beheert de database tijdens schrijven. De Python-API serialiseert haar eigen importaanroepen tussen threads. Start geen verschillende importprocessen tegelijk tegen hetzelfde bestand; daarvoor krijg je een DuckDB-bestandslockfout. Plan CLI-imports na elkaar of laat later één backend de importwachtrij beheren.

De bibliotheek sluit iedere verbinding na gebruik. Via de CLI kan je daardoor na een afgeronde import het bestand in een andere tool openen. Houd een externe read/write-verbinding niet onbedoeld open tijdens een CLI-import. Bewaar de actieve database op een lokale schijf, niet als gedeeld schrijfbestand in een synchronisatiemap.

DuckDB ondersteunt geen foreign keys tussen schema’s. De koppeling tussen batchmetadata en stagingrecords wordt hier door de gezamenlijke transactie bewaakt; wijzig deze technische tabellen via de ontvanger. Catalogusrelaties binnen hetzelfde schema hebben wel foreign keys.

De ontvanger leest parserrecords één voor één en laat DuckDB vervolgens een tijdelijk bestand in bulk inlezen. Er wordt geen volledige vacaturedataset in pandas geladen. Er is wel tijdelijke schijfruimte nodig voor één batch in JSONL-envelopvorm. Er is nog geen prestatiemeting op de volledige hackathondataset uitgevoerd.

## 9. Tests en vervolgwerk

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_staging.py' -v
```

De tests gebruiken eigen tijdelijke DuckDB-bestanden. Ze controleren onder andere rollback met behoud van het foutlogboek, herlevering, bron-ID-controles, ongeldige JSON, onbekende skill-ID’s, catalogusversies en parallelle aanroepen binnen één proces.

De verwerking van staging naar het appmodel is beschikbaar via `publish` en `import-parser`. Zie [database.md](database.md) voor het volledige pad van de originele bronvelden naar de maandtabellen.

Officiële documentatie: [JSON in DuckDB](https://duckdb.org/docs/2.0/data/json/overview.html), [transacties](https://duckdb.org/docs/2.0/sql/statements/transactions.html), [concurrency](https://duckdb.org/docs/2.0/connect/concurrency.html).

## Bedrijfsnamen

Voor snelle bedrijfsnaamnormalisatie is er een aparte module met een lookup in dezelfde DuckDB-database. Zie [companies.md](companies.md). Voer `init` opnieuw uit voor schema-versie 2.
