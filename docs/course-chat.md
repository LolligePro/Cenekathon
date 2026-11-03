# Cursussen en skills in de chat: werking en democontrole

## Herstelde oorzaken

De cursussen staan in `skillmarket_app.courses` en hun relaties in `course_skills`. De oude visualisatielaag zocht `coursera-*` in `posting_skills`, waar uitsluitend vacatures thuishoren. Daardoor gaf de chat een lege cursuslijst ondanks 16.852 aanwezige koppelingen.

De gewone chat had bovendien geen algemene cursus-tool en `profile_advice` verwees naar een ontbrekende `advisor.py`. Vrije skillnamen en afkortingen werden alleen met substringzoekwerk verwerkt. Een eerder gegenereerd antwoord kon daarna in de antwoordcache blijven staan.

De oplossing bevat de teammate-fix uit `courses-integrated` / `courses-fix`, zijn Vega-Lite-grafiekrouter en de riser-grafieken. Daarbovenop zijn de gedeelde skillresolver, cursusplanner, werkend profieladvies, regressietests en de gecontroleerde antwoordweergave geïntegreerd. De visualisatiedatabron wordt per aanvraag met een `ContextVar` gekozen; de chat vervangt geen moduleglobale databron meer.

## Eén datacontract voor de toepassing

- `skillmarket_staging/access.py`: verse read-only queryverbinding per aanvraag, lokaal via `SKILLMARKET_DB` of expliciet via `SKILLMARKET_API_URL`. Geen blijvende verbinding naar een oud, vervangen DB-bestand.
- `skillmarket_staging/learning.py`: skillresolutie, cursusplanning, profieladvies en cursusantwoordtekst.
- `visualization/data_source.py`: cursusmetadata en skills uit de echte cursustabellen, voor alle platformen. De naam `posting_id` bestaat alleen nog in het interne legacy-DataFramecontract; de waarde is een course-ID.
- `skillmarket_staging/talent.py`: de fictieve AI-auditordemo gebruikt dezelfde cursusplanner en queryprovider.

Matching gebruikt bestaande skill-ID's. Namen worden hoofdletterongevoelig verwerkt; vaste IDs, catalogussynoniemen en een kleine expliciete aliastabel worden ondersteund, bijvoorbeeld `ML` → `machine learning` en `AI` → `artificial intelligence`. Er is geen onzichtbare fuzzy match. Onbekende en ambigue invoer wordt apart gemeld. Vaste IDs worden nooit herschreven; verschillende catalogus-IDs blijven afzonderlijk bestaan.

## Chat-tools en antwoorden

`recommend_courses(skills, max_courses=5)` ontvangt vaardigheden die iemand **wil leren**. De planner kiest per stap de cursus die de meeste nog niet afgedekte vaardigheden toevoegt. Bij gelijke dekking krijgen expliciete skilllijsten voorrang op uit tekst afgeleide matches, daarna titelovereenkomst en stabiele sortering. Titel, URL, platform en dekking komen rechtstreeks uit de database. Dezelfde skill telt eenmaal; een cursus die niets toevoegt wordt niet herhaald.

`profile_advice(skills, country='ALL', job_type='ALL')` ontvangt vaardigheden die iemand **al kent**. Het gebruikt succesvolle, gedateerde vacatures uit 2025, het laatste volledige jaar vóór de bekende dataverzamelingsgaten. Het telt vacatures met minstens één profielskill en volledige overlap met alle gevonden parser-skills. Ontbrekende skills worden gerangschikt op extra volledige skillmatches en vraag in de overlappende vacatures, waarna cursussen worden gekoppeld. Dit is geen kans op aanwerving en beoordeelt geen diploma's of ervaring.

`career_path` behoudt de gewogen skillgap- en cursusgrafieken van de visualisatielaag. `skills_without_courses` betekent geen koppeling in de hele catalogus; `skills_not_covered_by_plan` betekent niet afgedekt door de beperkte getoonde selectie. Die twee situaties worden niet meer verward.

Expliciete cursusvragen met herkenbare skills en cursusvervolgvragen hergebruiken deze tools rechtstreeks. Andere formuleringen lopen via de agent, die dezelfde tools kan aanroepen. Na een leeradvies-tool wordt het antwoord uit de toolvelden opgebouwd; de taalmodeltekst kan cursuslinks of extra dekking niet veranderen. Zonder onderbouwde cursusdata vraagt de chat om een skillnaam in plaats van beschikbaarheid te verzinnen. Leeradviezen worden niet uit de algemene antwoordcache hergebruikt.

## Demovragen

1. **Welke courses helpen om Python en SQL te leren?** Controle: vaste IDs `S018`, `S024`; een cursus kan beide afdekken.
2. **En voor ML?** Controle: `S091`, machine learning, echte cursuslink.
3. **En voor model validation?** Controle: bekende skill `S345`, geen cursus gekoppeld.
4. **En voor een onbestaande vaardigheid?** Controle: niet herkend; geen verzonnen cursus.
5. **Ik ken Excel en SQL. Welke skills en courses helpen mij voor technology in België?** Controle: profieladvies met BE-filter, jaar 2025 en echte cursuskoppelingen.
6. **Welke courses helpen om van finance and accounting naar risk and compliance te gaan?** Controle: skillgaps en cursusgrafieken uit de database.
7. **Wie kan binnen onze fictieve bank AI-auditor worden?** Daarna: **En Profiel D?** Controle: de bestaande fictieve talentdemo blijft werken.

De catalogus omvat 8.092 cursussen en 16.852 koppelingen voor 607 verschillende skills. Niet elk van de 1.202 skills heeft dus een cursus. Dat is een expliciet resultaat, geen fout. De koppelingen zijn parseroutput; niveau, prijs, actuele beschikbaarheid en leerresultaten zijn niet gecontroleerd.

## Starten en juiste versie controleren

Vanuit de repo, met de gebruikelijke LLM-providerconfiguratie voor algemene vragen:

```bash
pip install -e '.[chat,test]'
export SKILLMARKET_DB=/home/team7/skillmarket-db/skillmarket.duckdb
export SKILLMARKET_PYTHON=/home/team7/skillmarket-venv/bin/python3
bash deploy/run-chat-server.sh
```

Het startscript vereist de echte DuckDB-bron en luistert standaard alleen op `127.0.0.1:8000`. Een expliciete `SKILLMARKET_API_URL` krijgt voor de queryprovider voorrang. De bestaande marktvisualisaties gebruiken hun lokale aggregaten; op de teamserver wijst `SKILLMARKET_DB` daarom steeds naar de centrale DB.

`GET /api/health` controleert de tabellen en retourneert de codeversie plus aantallen vacatures, skills, cursussen en koppelingen. HTTP 503 betekent een ontbrekende database of cursusimport. Gepubliceerde appkopieën krijgen een `RELEASE`-bestand met de commit-ID, zodat een oude serverversie meteen herkenbaar is. De bestaande DB-service op poort 8765 blijft afzonderlijk draaien.

## Verificatie

```bash
python -m unittest discover -s tests -v
```

De regressietests gebruiken echte, afzonderlijke cursustabellen en voegen nooit cursusrecords aan vacatures toe. Ze controleren meerdere platformen, aliases/IDs/synoniemen, onbekende en ambigue skills, lege dekking, beperkte cursusplannen, land- en jaarfilters, de remote DB-route, chatvervolgvragen, een verouderde antwoordcache en de bestaande talentdemo.

## Uitgevoerde democontrole

De gecombineerde versie heeft 70 geslaagde tests. Op de echte serverdatabase zijn negen scenario's via de HTTP-chatroute gecontroleerd: Python + SQL, ML, een bekende skill zonder cursus, onbekende invoer, persoonlijk profieladvies, carrièrepad met twee grafieken, AI-auditor, de Profiel D-vervolgvraag en een bestaande marktgrafiek. De directe cursus-, profiel- en talentvragen gebruikten nul modeltokens en duurden in deze eenmalige meting ongeveer 0,05–0,28 seconden.

De actuele appversie staat onder `/home/team7/skillmarket-app/releases/<commit>/`. `/home/team7/skillmarket-db/active-chat-release.json` vermeldt de actieve versie, map, PID en log. De aparte ontwikkelcheckout van teamgenoten wordt niet overschreven. Controleer voor de demo `/api/health`; die moet dezelfde commit en de verwachte cursustellingen tonen.

## Leerbudgetdemo

De chat vergelijkt ook skillpakketten binnen een leerurenbudget, met unieke extra vacaturematches, combinatie-effect en gekoppelde cursussen. Zie [leerbudget: demo, berekening en tests](learning-budget.md).
