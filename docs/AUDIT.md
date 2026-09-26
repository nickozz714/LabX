# Wat er in een lab gebeurd is

## Twee sporen, twee vragen

LabX houdt twee dingen bij, en ze beantwoorden verschillende vragen.

| | Waar | Waarover |
| --- | --- | --- |
| **Audit** | tab *Audit* | Wat heeft dit lab gedaan: welk model, wat ging erin, wat kwam eruit, welke acties zijn ondernomen. |
| **Data-guard** | tab *Data-guard* | Wat is er tegengehouden of gemaskeerd op weg naar het model, en waarom. |

Het guard-spoor is een scherp mes voor één vraag. De andere vraag — "wat deden
we vandaag bij deze klant" — gaat over beurten en niet over bytes, en die stel
je per lab.

## Wat een beurt is

Eén keer dat de agent aan het werk ging. Die ontstaan op twee manieren, en ze
staan in één lijst omdat je bij het terugkijken niet eerst wilt kiezen:

- **chat** — een bericht in de chat.
- **taak** — een achtergrondtaak of een ticket dat een agent oppakte.
- **workflow** — één activiteit uit een workflow, met zijn ronde erbij als hij
  in een lus zat.

Per beurt staat er: het moment, het lab, het model, de status, de duur, de
tokens en de kosten. Klap hem open en je ziet de opdracht die het model kreeg,
het antwoord dat eruit kwam, en de acties ertussenin.

## Wat een actie is

Een tool-aanroep. `lab__shell_exec` is er een, een MCP-tool van Zoho is er een,
`WebSearch` is er een. Dat is het niveau waarop je wil kunnen zien wat er
gebeurde — niet de losse tekens van een antwoord, en niet zo grof als "er
draaide iets".

Ze worden geteld en niet uitgeschreven: een beurt die twintig keer een
shell-commando doet, levert anders twintig regels op waar je niets aan ziet.

## De staafjes

Per **dag**, **week** of **maand**: hoeveel beurten, hoeveel acties, en wat er
misging (het rode deel van een staaf). Daaronder de meest gebruikte acties over
diezelfde periode.

Een periode zonder werk blijft als leeg vakje staan. Hem weglaten zou de
grafiek over het tempo laten liegen — twee drukke dagen naast elkaar zien er
anders hetzelfde uit als twee drukke dagen met een stille week ertussen.

## Over de kosten

De CLI meldt per beurt het totaal van de hele **sessie**, niet wat die beurt
kostte. Activiteiten van één workflow delen standaard één sessie, dus dat getal
loopt op: 13,35 — 13,56 — 13,81. Opgeslagen zoals het binnenkomt en daarna
opgeteld, telt hetzelfde geld vijf keer mee, en dat valt niet op omdat elk
bedrag op zichzelf plausibel is. Er wordt daarom het verschil met de vorige
melding bewaard; een activiteit met een verse sessie begint bij nul en telt
gewoon zijn eigen bedrag.

Beurten van vóór deze correctie staan nog met het sessietotaal in de
geschiedenis. Die zijn niet met terugwerkende kracht te herstellen — het
verschil is niet meer af te leiden zodra de melding zelf weg is.

## Waar het vandaan komt

Niets hiervan is apart bijgehouden: het komt uit `background_runs` (de beurten
van de agent, met de tool-aanroepen en het verbruik in `steps`) en uit
`workflow_run_steps` (de activiteiten van de motor, die per stap al een invoer,
een uitvoer en een prijs boeken). Er is dus geen tweede administratie die uit
de pas kan gaan lopen met de eerste.
