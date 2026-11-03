# Verificatie van de volledige database

Uitgevoerd op 3 november 2026 met de zes originele vacaturebestanden en alle 79 JSONL-batches uit `parser/batches/`. Parser: `rules-v1`; skillcatalogus: `skills-v1`. DuckDB 2.0.0, Python 3.14.3.

## Resultaat

| Onderdeel | Aantal |
|---|---:|
| Vacatures | 1.542.169 |
| Succesvol geparst | 1.541.614 |
| Expliciete parserfouten | 555 |
| Nog te ontvangen (`pending`) | 0 |
| Skills | 1.202 |
| Vacature/skill-koppelingen | 36.538.876 |
| Brede functiegroepen | 10 |
| Maandtotalen | 690 |
| Maand/skill-rijen | 793.155 |
| Vacatures zonder publicatiedatum | 271 |

De gedateerde vacatures lopen van 2021-01-01 tot 2026-09-26. De database en vijf Parquet-exports zijn lokaal aangemaakt. De database is ongeveer 659 MiB; het bestand wordt niet in Git opgeslagen.

## Uitgevoerde controles

- Alle 44 unittests slagen: contract, staging, foutlogging, normalisatie, publicatie, herimport, correcties, volledige build, exports en visualisaties.
- `verify --require-complete` slaagt: geen onbekende skill-ID's, verweesde koppelingen, skills op mislukte/onverwerkte vacatures of ontbrekende functiegroepen.
- De maandtotalen stemmen overeen met alle succesvol geparste, gedateerde vacatures. De som van de skilltellingen stemt overeen met de bijbehorende detailkoppelingen; geen skilltelling overschrijdt het maandtotaal.
- Alle 79 batches en 1.542.169 ontvangen JSON-records staan in staging.
- Alle vier SQL-voorbeelden uit `database.md` zijn op de echte data uitgevoerd, inclusief profieladvies; ze leveren resultaten.
- De visualisatie-API levert tien functiegroepen, 69 maandpunten voor Python in `technology`, topvaardigheden en vijf stijgers/vijf dalers. Alle resultaten zijn strikt JSON-serialiseerbaar, zonder NaN.
- Een trendtelling uit de API is onafhankelijk vergeleken met een telling via `postings` + `posting_skills` + `skills`; aantal en share komen overeen.
- Alle vijf Parquet-exports zijn heropend en hebben hetzelfde aantal rijen als de bijbehorende DB-tabellen.
- De view met volledige oorspronkelijke beschrijvingen is uitgelezen.
- Een echte batch is opnieuw ontvangen: status `duplicate`, geen extra records of skillkoppelingen.

## Gemeten uitvoering

Een volledige build met laden, strikte ontvangst, publicatie, controles en exports duurde lokaal ongeveer **48 seconden**. De eerste visualisatie-initialisatie duurde circa 0,23 seconde; opvolgende API-aanroepen circa 0,007–0,022 seconde. De SQL-query voor profieladvies duurde circa 0,17 seconde. Dit zijn losse lokale metingen met de aanwezige bronbestanden en cache, geen prestatienormen voor andere machines.

## Opnieuw controleren

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m skillmarket_staging verify --require-complete
```

Zie [DATABASE.md](../DATABASE.md) voor opnieuw opbouwen en [database.md](database.md) voor de velddefinities en gebruiksafspraken. De 555 parserfouten en de bekende ontbrekende brondata blijven expliciet zichtbaar; ze worden niet als succesvolle parsing voorgesteld.
