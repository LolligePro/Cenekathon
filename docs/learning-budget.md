# Leerbudget: pakketten van skills vergelijken

## Demo in de bestaande chat

Klik op de nieuwe voorbeeldvraag of stel:

> Ik ken Excel en SQL. Ik heb 40 uur om te leren. Welk skillpakket levert mij de meeste extra vacaturematches op voor technology in België?

Daarna kun je vragen: **En met 20 uur?** Het startprofiel en de filters blijven behouden. **Welke courses hiervoor?** zoekt de cursussen voor het aanbevolen pakket. Het antwoord toont maximaal drie alternatieven, hun leeruren, extra vacaturematches, het combinatie-effect, echte cursuslinks en een staafgrafiek.

## Berekening

`skillmarket_staging/learning_budget.py` leest dezelfde centrale DuckDB via de gedeelde read-only queryprovider. Geen nieuwe tabellen of import nodig. `plan_learning_budget(skills, hours_budget, country='ALL', job_type='ALL', skill_hours=None)` is ook een agent-tool. `skills` zijn reeds gekende namen of vaste skill-IDs; een lege lijst betekent starten zonder opgegeven skills. `skill_hours` kan expliciete uren per skillnaam of ID bevatten.

De cohort bestaat uit succesvol geparste, gedateerde vacatures in 2025, met land- en jobgroepfilter. De baseline telt vacatures met minstens één gevonden skill waarvoor alle gevonden skills al in het profiel zitten. Vacatures zonder gevonden skills tellen niet als volledige match.

De planner selecteert maximaal twaalf vaak voorkomende ontbrekende skills met een cursuskoppeling. Hij vergelijkt alle losse skills en paren uit die selectie die binnen het budget passen. Alleen vacatures waarvan alle ontbrekende skills in het pakket zitten leveren extra matches op. Elke vacature heeft precies één verzameling ontbrekende skill-IDs en telt per pakket eenmaal. De aantallen van alternatieven worden niet opgeteld. Het combinatie-effect telt vacatures waarvoor beide nieuwe skills tegelijk nodig zijn.

Sortering: meeste extra volledige skillmatches, daarna minste uren en stabiele skill-ID-volgorde. Het is een beperkte vergelijking voor de hackathon, geen globale optimalisatie over alle skills. De beste drie keuzes krijgen maximaal twee gekoppelde cursussen per pakket via de bestaande cursusplanner.

Leeruren zijn standaard **20 uur per skill als zichtbare demo-aanname**, geen cursusduur uit de database. Expliciete `skill_hours` in de tool krijgen voorrang. Het budget moet positief zijn en maximaal 10.000 uur; onherkende of ambigue startskills geven een duidelijke fout. Als niets binnen het budget past wordt geen fictieve winst getoond.

## Controle

`python -m unittest discover -s tests -p 'test_learning_budget.py' -v`

De tests gebruiken echte DuckDB-tabellen en controleren een pakket met zes unieke nieuwe matches, drie daarvan alleen door de combinatie; de urenlimiet en expliciete skilluren; uitsluiting van andere landen/jaren en vacatures met drie ontbrekende skills; cursusdekking; de bestaande HTTP-chat, vervolgvragen en het agent-toolpad. Expliciete vragen met leeruren gebruiken geen modeltokens.
