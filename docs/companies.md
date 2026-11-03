# Snelle bedrijfsnaamnormalisatie

Voor de hackathon gebruikt deze module tekstregels en een kleine vaste aliaslijst. Bekende banken worden op merk/groepsniveau samengevoegd: bijvoorbeeld ING Netherlands en ING Luxembourg naar ING. Dit is een benadering voor het concept, geen juridische entiteitsherkenning. De oorspronkelijke naam blijft altijd bewaard.

## Uitvoeren

Vanuit de repo, na installatie van de bestaande stagingmodule:

```bash
.venv/bin/python -m skillmarket_staging init
.venv/bin/python -m skillmarket_staging normalize-companies '/Users/daanderock/Downloads/vacancies_*.parquet'
```

Dit schrijft de lookup naar `skillmarket_staging.company_names` in DuckDB en exporteert dezelfde selectie met aantallen naar `data/company_map.parquet`.

Ook een compacte koppeling per vacature wegschrijven:

```bash
.venv/bin/python -m skillmarket_staging normalize-companies '/Users/daanderock/Downloads/vacancies_*.parquet' --write-postings data/posting_companies.parquet
```

Elke outputregel bevat `posting_id`, `company_raw`, `company_normalized`, `company_id`, `normalization_method` en `normalizer_version`. Deze koppeling kan via `posting_id` worden samengevoegd met de bestaande jobtitel/skill-parseroutput. Het parsercontract verandert niet.

Dezelfde opdracht mag opnieuw uitgevoerd worden. Nieuwe namen worden toegevoegd, bestaande namen opnieuw berekend; er ontstaan geen dubbele lookupregels. Bronbestanden worden niet gewijzigd.

## Waarom dit snel is

- DuckDB leest alleen de benodigde Parquet-kolommen.
- Python normaliseert alleen de unieke bedrijfsnamen, niet elke vacature opnieuw.
- De koppeling met alle vacatures gebeurt met een DuckDB-join.
- Er zijn geen LLM-calls, externe API-calls, embeddings of onderlinge fuzzy vergelijkingen.

## Regels

HTML decoderen, Unicode/spaties/hoofdletters normaliseren, interpunctie vereenvoudigen, juridische achtervoegsels en bekende scraperteksten opruimen. Daarna volgt een kleine aliaslijst voor belangrijke banken, groepen, recruiters en platformen.

Recruiters zoals Yource en Randstad behouden hun eigen naam. De module leest geen jobtitel of beschrijving en schrijft hen dus niet toe aan een daarin genoemde opdrachtgever. Een onbekende naam krijgt de opgeschoonde tekst; lege/expliciet onbekende waarden worden null.

`company_id` is een deterministische hash van de genormaliseerde naam. Het is een technische groepssleutel. Pas de aliaslijst in `skillmarket_staging/companies.py` aan voor extra demo-organisaties en verhoog `NORMALIZER_VERSION` bij gewijzigde regels. Herbereken de lookup daarna voor de volledige bronset. Exportbestanden zijn per uitvoering; de DuckDB-lookup kan daarnaast eerder verwerkte namen bevatten.

## Python-gebruik

```python
from skillmarket_staging.companies import normalize_company_name, normalize_company

normalize_company_name("ING Bank N.V.")  # "ING"
normalize_company("logo ABN AMRO")       # inclusief bronwaarde, ID, methode en versie
```
