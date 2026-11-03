# Gedeelde database op de teamserver

De database en read-only HTTP-service draaien op de teammachine. Downloaden of lokaal opbouwen is niet nodig om query's uit te voeren. De bestaande AI-server en Ollama blijven afzonderlijk draaien.

## Verbinden vanaf je laptop

Gebruik het netwerk van de locatie of eduroam, zoals aangegeven door de organisatoren. Open een terminal en laat deze SSH-tunnel actief:

```bash
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8765:127.0.0.1:8765 team7@34.136.171.143
```

Voer het teampassword in wanneer SSH erom vraagt. Het wachtwoord staat uitsluitend in de interne teamcommunicatie, niet in deze repo. Na het inloggen blijft de terminal stil; dat is normaal voor een tunnel.

De database-API is nu bereikbaar op **`http://127.0.0.1:8765`**. Dit adres op jouw laptop wordt via SSH doorgestuurd naar de centrale database. Bij een lokaal bezette poort kun je links `18765` gebruiken en de client-URL daarop aanpassen.

Controle:

```bash
curl http://127.0.0.1:8765/health
```

De reactie bevat `status: ok`, `read_only: true` en de aantallen vacatures, skills en koppelingen.

## Query's vanuit Python

Haal de laatste code op en installeer de dependencies met Python 3.11+:

```bash
git pull origin main
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Gebruik daarna:

```python
from skillmarket_staging.client import RemoteDatabase

# Houd de SSH-tunnel in de andere terminal open.
db = RemoteDatabase("http://127.0.0.1:8765")
print(db.health())

jobs = db.dataframe("""
    SELECT posting_id, normalized_title, company_normalized, date_posted
    FROM skillmarket_app.postings
    WHERE parse_status = 'ok' AND job_type = ?
    LIMIT 10
""", ["technology"])
print(jobs)
```

`dataframe()` geeft een pandas-DataFrame terug. `query()` geeft het ruwe JSON-resultaat met `columns`, `rows`, `row_count`, `truncated` en `elapsed_ms`. `schema()` geeft alle velden van het applicatieschema. De URL kan ook via `SKILLMARKET_API_URL` ingesteld worden.

Trendquery voor het team:

```python
trends = db.dataframe("""
    SELECT d.month, d.skill, d.postings, t.total_postings,
           d.postings::DOUBLE / nullif(t.total_postings, 0) AS share
    FROM skillmarket_app.skill_demand d
    JOIN skillmarket_app.monthly_totals t USING (job_type, month)
    WHERE d.job_type = ? AND d.skill = ?
    ORDER BY d.month
""", ["technology", "python"])
```

Dezelfde SQL voor profieladvies uit [database.md](database.md) werkt via `db.query()` of `db.dataframe()`.

## HTTP-contract

| Methode en pad | Resultaat |
|---|---|
| `GET /health` | Bereikbaarheid, DuckDB-versie en aantallen. |
| `GET /schema` | Schema, tabel, veldnaam, type en nullability. |
| `POST /query` | Voert één SELECT uit met optionele gebonden parameters. |

Voorbeeldbody:

```json
{"sql":"SELECT count(*) FROM skillmarket_app.postings WHERE job_type = ?", "parameters":["technology"], "max_rows":1000}
```

Standaard maximaal 1.000 resultaatrijen, optioneel tot 10.000. Controleer `truncated`, of `df.attrs["truncated"]` bij een DataFrame. Gebruik aggregaties en filters op de server in plaats van alle detailrijen over te halen. Een query krijgt maximaal 15 seconden. HTTP 503 betekent een onderbroken query of een bezette service; probeer eventueel opnieuw met een kleinere query. Schrijfacties, meerdere statements en niet-toegestane bestandstoegang worden geweigerd.

De HTTP-service luistert uitsluitend op `127.0.0.1` van de server. SSH regelt teamtoegang en versleuteling. Gebruik deze service voor het team, niet als publiek SQL-endpoint. De bestaande lokale `visualization.api` blijft standaard een lokale DB lezen; een laptop gebruikt de centrale DB via deze client. Code die al op de server draait kan het bestand rechtstreeks openen.

## Rechtstreeks op de server

Log in met:

```bash
ssh team7@34.136.171.143
```

Centrale paden:

| Onderdeel | Pad |
|---|---|
| Database | `/home/team7/skillmarket-db/skillmarket.duckdb` |
| Python-omgeving | `/home/team7/skillmarket-db/.venv` |
| Servicecode | `/home/team7/skillmarket-db/skillmarket_staging/server.py` |
| Startscript | `/home/team7/skillmarket-db/deploy/run-db-server.sh` |
| Log | `/home/team7/skillmarket-db/server.log` |
| Verificatieresultaat | `/home/team7/skillmarket-db/verification.json` |
| Originele vacatures | `/home/team7/Cenekathon/data/vacancies_*.parquet` |

Voor een backend op dezelfde server:

```python
import duckdb

with duckdb.connect("/home/team7/skillmarket-db/skillmarket.duckdb", read_only=True) as db:
    print(db.execute("SELECT count(*) FROM skillmarket_app.postings").fetchone())
```

Gebruik DuckDB 1.5.6 of een compatibele nieuwere versie. De aparte DB-service werkt op de aanwezige Python 3.10; direct starten van het servicebestand vereist alleen `duckdb`. De complete repo-installatie voor bouwen en testen vereist Python 3.11+. Voor de recente visualisatiecode kan `SKILLMARKET_DB=/home/team7/skillmarket-db/skillmarket.duckdb` worden ingesteld.

De volledige beschrijvingen zijn beschikbaar via `skillmarket_app.raw_vacancies`. De view is op deze machine aangepast aan de zes aanwezige originele Parquet-bestanden. Alleen deze bestanden zijn vanuit de HTTP-queryservice als externe bron toegestaan.

## Beheer tijdens de hackathon

De service draait in een eigen tmux-sessie **`skillmarket-db`**, los van de SSH-tunnel. De bestaande `ollama`-sessie wordt niet aangepast.

```bash
tmux list-sessions
tail -n 30 /home/team7/skillmarket-db/server.log
```

Na een serverherstart of gestopte service start je haar op de server opnieuw met:

```bash
tmux new-session -d -s skillmarket-db -c /home/team7/skillmarket-db \
  'exec bash deploy/run-db-server.sh >> server.log 2>&1'
```

Dit startcommando is bedoeld als de sessie nog niet bestaat. Stop uitsluitend deze DB-service indien nodig met `tmux kill-session -t skillmarket-db`. Stop de service en sluit andere DB-lezers voordat je het databasebestand vervangt of een import uitvoert; voer daarna de validatie uit en start de service opnieuw.

Volgens de organisatoren wordt de machine om **20:30** uitgeschakeld. Vanaf dat moment zijn de server en database-API niet bereikbaar. Deze tijdelijke hackathonopstelling is geen permanent gehoste database.

## Opslagcompatibiliteit

Nieuwe databases worden met opslagcompatibiliteit `v1.4.0` aangemaakt. De server gebruikt de beschikbare stabiele DuckDB 1.5.6; dezelfde code is ook met DuckDB 2.0 getest. Een bestaand bestand in het standaardformaat van 2.0 moet expliciet worden omgezet voordat 1.5 het kan lezen; zie [de DuckDB-instructies voor opslagconversie](https://duckdb.org/docs/stable/internals/storage). De centrale en lokale database zijn bij deze deployment al omgezet.

## Deployment gecontroleerd

Op 3 november 2026 zijn de geüploade bytes met SHA-256 vergeleken, het opslagformaat is omgezet voor DuckDB 1.5.6 en de beschrijvingsview is naar de serverbronnen omgezet. Op de server zijn alle relationele en maandcontroles geslaagd: **1.542.169 vacatures, 1.202 skills, 36.538.876 koppelingen, 0 pending en 555 expliciete parserfouten**.

Vanaf de Mac zijn `/health`, `/schema`, 69 maandpunten voor Python, een volledige beschrijving en de weigering van schrijfacties en willekeurige bestandstoegang door de SSH-tunnel getest. De volledige suite van 49 tests slaagt ook op DuckDB 1.5.6.
