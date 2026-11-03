# Verborgen intern talent: hackathondemo

Vraag in de bestaande chat:

> Wie kan binnen onze fictieve bank doorgroeien naar AI-auditor, en welke courses helpen?

De chat heeft hiervoor een voorbeeldknop. De agent roept `find_internal_talent` aan en geeft een korte ranking, skilloverlap, ontbrekende skills en echte cursuslinks uit de gekoppelde catalogus. Een vervolgvraag kan zijn: **En welke skills en cursussen ontbreken voor Profiel D?**

## Wat is fictief en wat komt uit de database?

- **Fictief:** Demo Bank, vijf anonieme interne profielen en hun huidige functies/skills. Er worden geen echte personen of personeelsgegevens gebruikt.
- **Handmatig gekozen:** het doelprofiel AI-auditor met zeven skills. Dit is een illustratief intern functieprofiel, geen beroepsstandaard of uit vacaturedata afgeleide eis.
- **Bestaande data:** skill-ID's en namen uit de gedeelde `skills-v1`-catalogus; cursustitels, URL's, platformen en skillkoppelingen uit de serverdatabase.
- In een echte organisatie beheert HR / Learning & Development de interne profielen en functievereisten.

De fixture staat in `skillmarket_staging/demo/internal_talent.json` en wordt met het Python-package meegeleverd. Wijzigen van deze kleine JSON is voldoende om de profielen of gewenste skills aan te passen. Gebruik altijd bestaande skill-ID's; onbekende IDs geven een fout.

## Doelrol en verwachte ranking

| ID | Skill in het doelprofiel |
|---|---|
| S004 | risk management |
| S006 | regulatory compliance |
| S007 | data analysis |
| S090 | data governance |
| S091 | machine learning |
| S125 | audit |
| S345 | model validation |

| Profiel | Huidige fictieve functie | Overlap |
|---|---|---|
| A (`demo-a`) | Compliance analyst | 5/7 = 71,4% |
| D (`demo-d`) | Model risk specialist | 4/7 = 57,1% |
| B (`demo-b`) | Internal auditor | 3/7 = 42,9% |
| C (`demo-c`) | Data analyst | 2/7 = 28,6% |
| E (`demo-e`) | Operations associate | 1/7 = 14,3% |

Profiel A mist machine learning en model validation. De huidige cursusdata bevat machine-learningkoppelingen, maar geen koppelingen voor model validation. De tool toont dat laatste expliciet en verzint geen opleiding. Het is een gat in onze catalogus; het bewijst niet dat zulke opleidingen nergens bestaan.

## Berekening en cursusselectie

De implementatie staat in `skillmarket_staging/talent.py`:

```text
matched = profielskills ∩ doelrolskills
missing = doelrolskills − profielskills
match_percentage = round(100 × len(matched) / len(doelrolskills), 1)
```

Alle skills tellen even zwaar. Rangschik op meeste overlap, bij gelijke stand op `profile_id`. Standaard worden de beste drie profielen teruggegeven; `n` kan 1 tot 5 zijn. Extra skills buiten de doelrol tellen niet mee.

Per kandidaat zoekt bulk-SQL maximaal drie cursussen uit `skillmarket_app.courses` en `course_skills`. Alleen succesvolle cursusrecords tellen mee. Sorteer op het aantal **verschillende ontbrekende** skills dat een cursus afdekt, daarna op titel en ID. Er wordt op vaste IDs gematcht, niet op losse titelwoorden. Niveau, prijs, duur, beschikbaarheid en de werkelijke cursuskwaliteit worden niet beoordeeld.

Een aparte kleine query controleert welke ontbrekende skills ergens in de hele cursuscatalogus voorkomen. `skills_without_courses` betekent dus werkelijk geen gevonden koppeling, ook als een relevante cursus buiten de top drie zou vallen. `skills_not_covered_by_recommendations` toont alle gaps die de getoonde drie cursussen niet afdekken. Bij volledige skilloverlap zijn gaps en cursusadviezen leeg.

## Toolcontract

```python
from skillmarket_staging.talent import find_internal_talent

result = find_internal_talent()  # target_role_id="ai-auditor", n=3
detail = find_internal_talent(profile_id="demo-d")
```

Het resultaat bevat `synthetic`, `company`, `data_owner`, `target_role` met `required_skills`, `profiles_evaluated`, `candidates` en `limitations`.

Elke kandidaat bevat `profile_id`, `label`, `current_role`, `matched_count`, `required_count`, `match_percentage`, `matched_skills`, `missing_skills`, `courses`, `skills_without_courses` en `skills_not_covered_by_recommendations`. Skills zijn objecten met `skill_id` en `name`. Een cursus bevat `course_id`, `title`, `url`, `site`, `covered_count` en `covered_skills`; deze laatste velden gaan alleen over de ontbrekende skills van deze kandidaat.

`ai_layer.py` registreert deze functie als tool. De bestaande model/tool-loop, `/api/ask` en sessiegeschiedenis worden hergebruikt. De agent kiest de tool. Na een succesvolle toolaanroep bouwt een vaste Nederlandse formatter het antwoord op uit de berekende velden: fictieve data, ranking, ontbrekende skills, cursuslinks en gaten in de catalogus. Dit voorkomt dat een model zelf extra cursusdekking verzint en bespaart een tweede modelronde. De toolresultaten en het geformatteerde antwoord blijven in de sessiegeschiedenis voor vervolgvragen. Een expliciete vervolgvraag over `Profiel A` t/m `Profiel E` wordt binnen deze talentcontext direct opnieuw doorgerekend en geformatteerd, zonder modelaanroep. Zo worden cursusgaten van verschillende kandidaten niet verwisseld. De chat maakt HTTP(S)-cursuslinks klikbaar met DOM-nodes; modeltekst wordt niet als HTML geïnterpreteerd.

## Starten vanuit de repo

Installeer met Python 3.11+:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[chat,test]'
```

Gebruik een lokale database met de cursusimport uit [courses.md](courses.md), of verbind de talenttool met de bestaande [server-API via SSH](database-server.md):

```bash
export SKILLMARKET_API_URL=http://127.0.0.1:8765
.venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

De bestaande LLM-providerconfiguratie blijft nodig voor chatantwoorden. Voor de gewone Anthropic-client is dat `ANTHROPIC_API_KEY` en eventueel `SKILLMARKET_MODEL`; gebruik de modelconfiguratie die voor jullie prototype werkt. De matchfunctie zelf gebruikt geen LLM en kan rechtstreeks vanuit Python worden aangeroepen.

Voor **deze tool** krijgt expliciet `SKILLMARKET_API_URL` voorrang. Anders opent hij `SKILLMARKET_DB`, standaard `data/skillmarket.duckdb`, read-only. Als dat bestand ontbreekt, gebruikt hij de localhost-API op poort 8765. Bestaande markt- en visualisatietools houden hun eigen bestaande databroninstellingen; deze optie verandert die niet.

Er is geen nieuwe databasemigratie nodig. De fixture blijft in JSON; de tool voert uitsluitend SELECT-query's uit op skills en cursussen. Er wordt geen medewerkerstabel of persistent profiel aangemaakt.

## Testen en grenzen van de demo

```bash
.venv/bin/python -m unittest discover -s tests -p test_talent.py -v
.venv/bin/python -m unittest discover -s tests -v
```

De gerichte tests controleren vaste-ID-ranking, gelijke scores, een volledig passend profiel, cursusdekking en ontbrekend aanbod, ongeldige invoer, de remote DB-route en het bestaande chatendpoint met vervolgvragen. De chattest gebruikt een gecontroleerde modelrespons en voert de echte matching en SQL uit, zonder API-kosten.

De overlapscore is geen sollicitatiekans of oordeel over geschiktheid. Ervaring, beheersingsniveau, voorkeuren, diploma's en opleidingsresultaten worden niet gemodelleerd. Het advies is een startpunt voor een gesprek met HR en een leertraject.
