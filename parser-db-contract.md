# Parser → Database: aanlevercontract v1.0

**Project:** Cenekathon / Skill Market  
**Bestemd voor:** team Parser en team Database  
**Status:** voorstel voor gezamenlijke bevestiging; dit document beschrijft de verwachte aanlevering en is geen implementatie.  
**Datum:** 3 november 2026

## 1. Doel en verantwoordelijkheid

De parser zet per vacature de oorspronkelijke jobtitel en beschrijving om naar:

- een genormaliseerde jobtitel;
- een lijst van vaste skill-ID’s uit de gedeelde skilllijst;
- een expliciete verwerkingsstatus.

Team Database ontvangt deze resultaten, koppelt ze aan de oorspronkelijke vacaturegegevens en verzorgt opslag, validatie, herimport en verdere verwerking.

Dit contract legt de minimaal vereiste overdracht vast. Andere inhoudelijke velden kunnen later worden afgesproken zonder dat de voorbereidende database-infrastructuur daarop hoeft te wachten.

## 2. Wat moet team Parser leveren?

Lever **per batch** twee UTF-8-bestanden aan:

```text
batch-2021-001/
├── manifest.json
└── parsed.jsonl
```

Daarnaast moet team Database voor elke gebruikte skilllijstversie beschikken over de bijbehorende **skills.jsonl**. Als die lijst al gedeeld is, volstaat een verwijzing naar de exact afgesproken versie. De lijst hoeft niet bij iedere batch opnieuw te worden meegestuurd.

De vaste overdracht voor dit voorstel is JSONL: één JSON-object per regel. Als de parser al Parquet produceert, spreken beide teams eerst het equivalente schema af. Gebruik niet stilzwijgend een ander bestandsformaat.

Alle voorbeelden hieronder zijn fictief en dienen uitsluitend om het contract te verduidelijken.

## 3. Input en koppeling met de brondata

De parser moet per inputvacature toegang hebben tot deze gegevens:

```json
{
  "posting_id": "DEMO-001",
  "title": "Senior Credit Risk Analyst",
  "description": "De oorspronkelijke vacaturebeschrijving..."
}
```

**`posting_id` is exact de waarde van `vacancy_id` uit de oorspronkelijke dataset.** Alleen de veldnaam verandert. De waarde blijft ongewijzigd, inclusief hoofdletters, leestekens en eventuele voorloopnullen. Genereer geen nieuw vacature-ID.

Team Database gebruikt dit ID om het resultaat aan bedrijf, datum, land en andere oorspronkelijke metadata te koppelen. De parser hoeft deze gegevens niet af te leiden of opnieuw mee te sturen.

Eén batch bestaat uit een afgesproken verzameling inputvacatures. Team Database moet kunnen vaststellen welke input-ID’s bij die batch horen, bijvoorbeeld via het gedeelde inputbestand of een afgesproken selectie.

## 4. Batchmanifest: manifest.json

```json
{
  "contract_version": "1.0",
  "batch_id": "2021-part-001-run-001",
  "parser_version": "parser-v1",
  "skill_taxonomy_version": "skills-v1",
  "record_count": 3
}
```

| Veld | Type | Verplicht | Betekenis |
|---|---|---|---|
| `contract_version` | string | Ja | Voor dit contract exact `"1.0"`. |
| `batch_id` | string | Ja | Unieke, niet-lege identificatie van deze aanlevering. |
| `parser_version` | string | Ja | Identificatie van de gebruikte parserconfiguratie: code, model en/of prompt. |
| `skill_taxonomy_version` | string | Ja | Exacte versie van de gedeelde skilllijst. |
| `record_count` | integer | Ja | Aantal resultaatregels in `parsed.jsonl`, inclusief mislukte verwerkingen. Moet groter dan nul zijn. |

Gebruik binnen één batch één parserversie en één skilllijstversie. Een versie moet reproduceerbaar naar dezelfde configuratie verwijzen; verander de betekenis van een bestaande versie niet.

## 5. Resultaten: parsed.jsonl

Iedere regel is een zelfstandig JSON-object. Het bestand is **geen JSON-array**. De volgorde van de regels hoeft niet gelijk te zijn aan de inputvolgorde: koppeling gebeurt via `posting_id`.

Het volgende voorbeeldbestand hoort bij het manifest met `record_count: 3`:

```jsonl
{"posting_id":"DEMO-001","parse_status":"ok","normalized_title":"Credit Risk Analyst","skill_ids":["S001","S042"],"error":null}
{"posting_id":"DEMO-002","parse_status":"ok","normalized_title":"Cashier","skill_ids":[],"error":null}
{"posting_id":"DEMO-003","parse_status":"failed","normalized_title":null,"skill_ids":[],"error":{"code":"MODEL_TIMEOUT","message":"Verwerking overschreed de tijdslimiet."}}
```

### Verplichte velden per resultaat

| Veld | Type | Regels |
|---|---|---|
| `posting_id` | string | Niet leeg; exact het oorspronkelijke vacature-ID. |
| `parse_status` | string | Uitsluitend `"ok"` of `"failed"`. |
| `normalized_title` | string of null | Bij `ok`: niet-leeg. Bij `failed`: altijd `null`. |
| `skill_ids` | array van strings | Altijd aanwezig, zonder dubbele ID’s. Bij `failed`: altijd `[]`. |
| `error` | object of null | Bij `ok`: altijd `null`. Bij `failed`: foutobject volgens onderstaande regels. |

Een ontbrekend verplicht veld is ongeldig. Gebruik echte JSON-waarden: `null` is geen `"null"`, en een lijst is geen string zoals `"['S001']"`.

### Optioneel veld

| Veld | Type | Regels |
|---|---|---|
| `role_family` | string of null | Alleen toevoegen als de parser een afgesproken bredere functiefamilie levert, bijvoorbeeld `"risk"`. Bij `failed`: weglaten of `null`. De waarde `"ALL"` is gereserveerd voor database-aggregaties. |

`role_family` is niet nodig voor de eerste aansluiting. Laat het veld weg als de betekenis of indeling nog niet is afgesproken. Een interne functiesleutel kan team Database zelf aanmaken op basis van de genormaliseerde titel.

Andere velden worden vooraf afgestemd en in het contract opgenomen. Stuur geen onaangekondigde extra velden die mogelijk een andere betekenis hebben dan team Database verwacht.

## 6. Betekenis van titels en skills

### Genormaliseerde titel

- Gebruik voor dezelfde genormaliseerde functie dezelfde schrijfwijze binnen een parserversie.
- Lever een functiebenaming, geen willekeurig gegenereerd ID of alleen een brede functiefamilie.
- De precieze regels voor senioriteit en het samenvoegen van verwante functies behoren tot de afgesproken normalisatielogica van team Parser.
- Als geen bruikbare titel kan worden geproduceerd, lever `failed` met een passende foutcode aan.

### Skill-ID’s

- Iedere ID bestaat in de skilllijst die het manifest aanwijst.
- ID’s zijn hoofdlettergevoelig en worden exact overgenomen.
- De volgorde van de skills is niet betekenisvol.
- Een skill die meerdere keren in dezelfde vacature voorkomt, wordt eenmaal opgenomen.
- De lijst bevat gedetecteerde, voor de functie gevraagde skills. Loutere vermeldingen in bedrijfsinformatie of expliciete ontkenningen worden niet als positieve skillvereiste behandeld.
- Dit contract maakt nog geen onderscheid tussen verplichte en gewenste skills, vaardigheidsniveau of aantal jaren ervaring. Ken die betekenis niet impliciet toe aan de lijst.
- Een geslaagde verwerking zonder gevonden skills krijgt `parse_status: "ok"` en `skill_ids: []`.

**Geen skills gevonden is iets anders dan een mislukte verwerking.** Beide gevallen moeten herkenbaar blijven.

## 7. Foutresultaten

Bij `parse_status: "failed"` bevat `error` precies deze velden:

```json
{
  "code": "MODEL_TIMEOUT",
  "message": "Verwerking overschreed de tijdslimiet."
}
```

| Code | Gebruik |
|---|---|
| `EMPTY_INPUT` | De input bevat onvoldoende bruikbare titel- en beschrijvingsinformatie. |
| `MODEL_TIMEOUT` | Een modelaanroep of verwerking overschreed de tijdslimiet. |
| `INVALID_OUTPUT` | Er kon geen resultaat worden verkregen dat aan het contract voldoet. |
| `INTERNAL_ERROR` | Een andere interne verwerkingsfout. |

`message` is een korte, niet-lege toelichting. Stuur geen volledige vacaturetekst, credentials of modelprompts als foutmelding mee.

Als de titel wel kon worden bepaald maar de skillverwerking mislukte, geldt het volledige resultaat in v1 als `failed`. Een gedeeltelijk resultaat mag niet als succesvolle vacature worden opgeslagen.

## 8. Gedeelde skilllijst

Team Database heeft voor iedere nieuwe `skill_taxonomy_version` een volledige lijst nodig met minimaal `skill_id` en `name`:

```jsonl
{"skill_id":"S001","name":"Python","category":"programming","synonyms":["python3"]}
{"skill_id":"S042","name":"Machine learning","category":"ai","synonyms":["ML"]}
```

| Veld | Type | Verplicht |
|---|---|---|
| `skill_id` | unieke, niet-lege string | Ja |
| `name` | niet-lege string | Ja |
| `category` | string of null | Nee |
| `synonyms` | lijst van strings | Nee; standaard `[]` |

Gebruik alleen echte synoniemen als alias. Verwante skills hoeven geen synoniemen te zijn: bijvoorbeeld Python en pandas mogen verschillende skill-ID’s hebben.

Een eenmaal gedeelde skilllijstversie blijft onveranderd. Lever een nieuwe versie aan wanneer de inhoud verandert. Stem versiewijzigingen af met team Database, zodat analyses geen verschillende definities stilzwijgend mengen.

## 9. Volledigheid, herlevering en wijzigingen

1. **Eén input-ID geeft één outputregel.** Laat mislukte vacatures niet weg.
2. De input-ID’s en output-ID’s van een batch zijn exact gelijk.
3. Binnen één batch komt een `posting_id` maximaal eenmaal voor.
4. Een volledige parserrun mag in meerdere batches worden geleverd; een inputvacature hoort daarbij eenmaal voor te komen. Een bewuste correctie is een nieuwe aanlevering.
5. Hetzelfde `batch_id` met dezelfde inhoud mag opnieuw worden aangeboden. Dit mag geen dubbeltelling veroorzaken.
6. Gewijzigde inhoud krijgt een nieuw `batch_id`.
7. Bij correctie of herverwerking blijft `posting_id` gelijk. De nieuwe `skill_ids`-lijst vervangt de volledige vorige skillset; het is geen lijst met alleen toegevoegde skills.
8. Publiceer het bestand en manifest pas als de batch volledig is geschreven. Een onderbroken of nog groeiend bestand is geen complete aanlevering.

Team Database bewaart het ontvangen batchresultaat voor herkomst en herverwerking. Bij het publiceren naar definitieve tabellen worden wijzigingen gezamenlijk toegepast. Foutresultaten worden als fout geregistreerd en nooit stilzwijgend als succesvolle vacatures zonder skills behandeld.

## 10. Acceptatie door team Database

Voor een batch wordt gepubliceerd, controleert team Database:

- of het manifest en alle resultaatregels geldig zijn;
- of aantallen en vacature-ID’s overeenkomen met de batchinput;
- of alle verplichte velden en statusafhankelijke waarden kloppen;
- of skill-ID’s bekend zijn in de opgegeven skilllijstversie;
- of er geen dubbele vacature-ID’s of dubbele skills binnen een resultaat zijn;
- of het batch-ID en de versies consistent zijn met eerdere aanleveringen.

Een structurele contractfout blokkeert publicatie van de batch en wordt met reden teruggekoppeld. Geldige `failed`-resultaten zijn geen structurele contractfouten: ze worden geregistreerd zodat verwerking later opnieuw kan worden geprobeerd.

Deze controles toetsen de overdracht en consistentie. De inhoudelijke nauwkeurigheid van de titelnormalisatie en skilldetectie vraagt daarnaast een aparte kwaliteitscontrole door het team.

## 11. Checklist voor de eerste overdracht

- [ ] Beide teams bevestigen dit contract en de veldnamen.
- [ ] De skilllijst en de versie ervan zijn gedeeld.
- [ ] Er is duidelijkheid over welke bronvacatures bij de testbatch horen.
- [ ] Team Parser levert eerst een kleine voorbeeldbatch van circa 10 vacatures.
- [ ] De voorbeeldbatch bevat normale resultaten, een succesvolle lege skilllijst en minstens één foutresultaat.
- [ ] `manifest.json` en `parsed.jsonl` zijn volledig en onderling consistent.
- [ ] Team Database bevestigt dat koppeling en validatie van de voorbeeldbatch werken.
- [ ] Daarna volgen de volledige batches volgens hetzelfde contract.
