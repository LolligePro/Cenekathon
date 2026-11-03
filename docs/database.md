# Database en overdracht van de parser

De branch gebruikt DuckDB en sluit aan op de maandtabellen uit `visualization/README.md`.
Alleen code, instructies en fictieve tests worden gecommit. De echte database, exports en brondata blijven lokaal en worden door `.gitignore` uitgesloten.

## Nu al uitvoeren

Vanuit de repo:

```bash
.venv/bin/python -m skillmarket_staging init
.venv/bin/python -m skillmarket_staging load-vacancies '/Users/daanderock/Downloads/vacancies_*.parquet'
.venv/bin/python -m skillmarket_staging status
```

`init` past de migraties 1–3 toe en behoudt bestaande stagingrecords. `load-vacancies` verwerkt alle oorspronkelijke jaarbestanden en koppelt de bedrijfsnamennormalisatie. Opnieuw uitvoeren telt dezelfde vacature-ID's niet dubbel.

## Zodra de map van de parser in Downloads staat

```bash
.venv/bin/python -m skillmarket_staging import-parser '/Users/daanderock/Downloads/parser-output'
.venv/bin/python -m skillmarket_staging status
.venv/bin/python -m skillmarket_staging export-database
```

Vervang `parser-output` door de echte naam van de ontvangen map. Er is geen nieuwe code nodig voor onderstaande formaten.

### Formaat A: het afgesproken JSONL-contract

Een map of submappen met `manifest.json` en `parsed.jsonl`. De gedeelde `skills.jsonl` mag bij de batch of in de opgegeven hoofdmap staan. Als de catalogus eerder met `load-skills` geregistreerd is, hoeft het bestand niet opnieuw meegeleverd te worden.

De ontvanger gebruikt de bestaande strikte validatie en het importlogboek. Daarna publiceert hij per vacature het laatst ontvangen resultaat. Nieuwe resultaten vervangen de volledige oude skillset. Een geldige `failed`-regel wist de eerdere skills en blijft als parserfout zichtbaar.

Als batches al via `receive` gestaged zijn, is alleen dit nodig:

```bash
.venv/bin/python -m skillmarket_staging publish
```

`normalized_title` blijft apart bewaard. De visualisatiekolom `job_type` gebruikt `role_family` wanneer aanwezig, anders de genormaliseerde titel. Skill-ID's uit het contract blijven ongewijzigd.

### Formaat B: de huidige parserbranch

`parser/match_all.py` produceert `labeled_2021.parquet` tot en met `labeled_2026.parquet` met:

```text
vacancy_id | year | country | date_posted | job_type | skills: list<string>
```

De adapter gebruikt `vacancy_id` voor de koppeling en de oorspronkelijke datum, het land en de bedrijfsnaam uit de bronbestanden. `job_type` is de functiegroep. De canonieke skillnamen krijgen een vaste technische ID: `name_` plus de MD5 van de volledige naam. Dit is een identifier, geen beveiligingshash. Er wordt geen nieuwe skill-extractie uitgevoerd en er worden geen modellen aangeroepen.

Alle complete jaarbestanden in de ontvangen map en submappen worden geïmporteerd. Parquet-bestanden zonder leesbare footer worden overgeslagen en gerapporteerd als onafgewerkt. Een bestand dat tijdens het inlezen verandert, blokkeert die import. Bied de map opnieuw aan na afloop van de parserrun; bestaande vacature/skill-relaties worden vervangen, niet opgeteld.

Gebruik per parserrun één aanleverformaat. De formatschakelaar is een compatibiliteitsadapter; het maakt verschillende skill-ID-systemen niet automatisch equivalent.

## Tabellen en originele velden

| Object | Inhoud |
|---|---|
| `skillmarket_app.raw_vacancies` | View met alle zeven originele kolommen: `vacancy_id`, `title`, `description`, `company`, `date_posted`, `country`, `city`. |
| `skillmarket_app.postings` | Compacte metadata, oorspronkelijke en genormaliseerde bedrijfsnaam, jobtype, genormaliseerde titel, parserstatus en versies. |
| `skillmarket_app.skills` | Skill-ID, naam, categorie en synoniemen. |
| `skillmarket_app.posting_skills` | Unieke koppelingen tussen vacature-ID en skill-ID. |
| `skillmarket_app.monthly_totals` | `job_type`, `month`, `total_postings`. |
| `skillmarket_app.skill_demand` | `job_type`, `month`, `skill`, `postings`. |
| `skillmarket_app.parser_imports` | Succesvolle publicaties, formaat, bronbestanden, parserversie en aantal resultaten. |

De volledige beschrijvingen blijven in de Parquet-bronnen en worden via `raw_vacancies` beschikbaar gemaakt. Daardoor worden gigabytes tekst niet nogmaals gekopieerd voor de tellingen. De metadata die de queries nodig hebben, staat fysiek in DuckDB. De bronbestanden moeten voor de beschrijvingen op hun geregistreerde locatie blijven staan. Verplaats je ze, voer dan `load-vacancies` opnieuw uit met de nieuwe paden.

`pending` betekent: bronvacature geladen, parserresultaat nog niet ontvangen. `ok` omvat ook succesvolle resultaten zonder gevonden skills. `failed` betekent een expliciete parserfout. `status` laat de aantallen zien; een geladen bronbestand wordt dus nooit als volledig geparst voorgesteld.

## Maandtotalen en grafieken

- Alleen `ok`-vacatures met een geldige datum tellen mee in de maandtabellen.
- Een succesvolle lege skilllijst telt wel mee in het vacaturetotaal.
- Dezelfde skill wordt per vacature eenmaal geteld.
- Ontbrekende kalendermaanden tussen de eerste en laatste waarneming worden toegevoegd met nul vacatures.
- Skills worden ook voor maanden zonder detecties als nul opgenomen.
- Een maand met nul vacatures geeft in de grafiek een ontbrekende share (`null`), zodat geen denkbeeldige vraag wordt getoond.
- De onvolledige dataverzameling van januari–april 2026 blijft een bronbeperking; normalisatie herstelt die dekking niet.

De visualisatielaag heeft een `DatabaseSource` die precies de bestaande DataFrame-contracten levert. Ze leest de vooraf berekende tabellen, niet alle vacatureteksten. Resultaten worden gecachet totdat het databasebestand wijzigt.

Omgevingsvariabelen:

```bash
export SKILLMARKET_DB='/Users/daanderock/Documents/Cenekathon/data/skillmarket.duckdb'
export SKILLMARKET_SOURCE=duckdb
```

`auto` is standaard: zodra de toepassingsdatabase is aangemaakt, wordt die gebruikt, ook wanneer de parser nog nul resultaten geleverd heeft. De lijst jobtypes is dan leeg. Voor de oorspronkelijke fictieve visualisatiedemo kun je expliciet `SKILLMARKET_SOURCE=stub` kiezen.

## Exports

`export-database` schrijft naar `data/`: `postings.parquet`, `skills.parquet`, `posting_skills.parquet`, `monthly_totals.parquet` en `skill_demand.parquet`. `postings` bevat ook nog niet geparste vacatures met hun status. De twee maandexports bevatten uitsluitend geparste waarnemingen. De postingexport bevat daarnaast het kwartaal in `YYYY-Qn` voor andere teamonderdelen.

Ontvangst en verwerking gebeuren lokaal via één schrijvend proces. Sluit langdurige externe databaseverbindingen voordat je importeert. Start vervolgens de grafieken opnieuw of roep de API opnieuw aan; de DB-bron controleert het bestand op wijzigingen.

## Controleren

```bash
.venv/bin/python -m unittest discover -s tests -v
```

De tests gebruiken eigen tijdelijke databases en fictieve vacatures. Ze controleren zowel JSONL als gelabelde Parquet, bronkoppeling, herimport, correcties, lege skilllijsten, datumgaten, exports en het gebruik van de echte DB door de visualisatie-API.
