# Twee sessies in één werker

## Waar dit over gaat

Een lab is een sandbox-pc. Een werker is één container van dat lab, en tot nu
toe draaide daar één agent-run tegelijk in: de planner gaf elk ticket een eigen
werker, en was er geen vrije, dan wachtte je.

Soms wil je dat niet. Zes tickets die vooral Fabric-API's aanroepen kunnen
prima met z'n tweeën in één container — net zoals je op je eigen pc twee keer
Claude Code kunt starten. Of dat handig is, weet jij; LabX weet dat niet en
gaat het ook niet raden.

Daarom drie dingen:

- **Sessies per werker** — een getal bij het lab (standaard 1). Zet je het op
  2, dan passen er twee sessies in dezelfde container: een chat naast een
  ticket, twee tickets, een workflow naast een chat. Werk gaat altijd eerst
  naar een lege werker; delen is het vangnet, niet de eerste keuze. Bij elke
  werker staat `n/m`, zodat je ziet wat er draait in plaats van het te moeten
  geloven.


- **Bundels** — in een planning sleep je tickets op elkaar. Tickets in dezelfde
  bundel draaien tegelijk in één werker; de bundels zelf gaan op volgorde. Zes
  tickets per twee = drie bundels achter elkaar.
- **Claimbare resources** — voor het spul waar er maar één van is (de browser,
  een playground, een vaste poort) reserveert de agent met
  `lab__resource__claim`, en wacht de ander tot het vrij is.

## Wat een bundel wel en niet is

Wel: tickets die tegelijk in dezelfde container draaien, en die samen één plek
bezetten. Voor de rest van LabX is die werker gewoon bezet — andere planningen,
handmatig gestarte tickets en chats zien hem als in gebruik.

Niet: isolatie. De tickets delen `/workspace`, de processen, de poorten, de
browser en de az-sessie. Wie ze bij elkaar sleept, zegt daarmee dat ze elkaar
verdragen. Dat is een bewuste keuze: aparte werkmappen of een git-worktree per
sessie zouden schijnveiligheid geven (de agent kan overal bij) en werken niet
voor het soort werk waar dit voor bedoeld is — Fabric-tickets hebben vaak
helemaal geen repo.

Wat een bundel wél doet om ongelukken te voorkomen: elk ticket in een bundel
krijgt in zijn opdracht te lezen met wie het de container deelt, dat het geen
processen van een ander mag afschieten, en hoe het het deelbare spul
reserveert.

## Bundels maken

Open een planning op het bord. Sleep een ticket op een ander ticket en ze
vormen samen een bundel; sleep naar "nieuwe bundel" voor een aparte groep, en
met **losmaken** draait een ticket weer alleen. Een ticket dat al draait blijft
staan waar het is — dat zit al in een container, en zijn bundel verplaatsen zou
betekenen dat de administratie iets anders zegt dan de werkelijkheid.

Twee bundels mogen tegelijk lopen als het lab meer werkers heeft; hoeveel er
tegelijk lopen regelt **max_parallel** van de planning (dat telt nu bundels in
plaats van tickets — voor een planning zonder bundels is dat hetzelfde, want
dan is elk ticket zijn eigen bundel).

Een planning zonder bundels gedraagt zich precies zoals voorheen.

## Resources reserveren

De catalogus staat bij **Instellingen → Claimbare resources**; per lab vink je
aan welke gelden (bij het lab, tabblad Instellingen). Meegeleverd:
`chrome-browser`, `playground`, `az-sessie` (standaard aan) en `poort-8000`
(standaard uit).

Per resource leg je vast:

| Veld | Betekenis |
| --- | --- |
| Waar geldt hij | per **werker** (één per container) of per **lab** (over de werkers heen) |
| Als hij bezet is | **wachten** tot hij vrijkomt, of **weigeren** zodat de agent meteen iets anders kan doen |
| Vervalt na | het vangnet: een agent die crasht of vergeet vrij te geven mag een resource niet voorgoed op slot zetten |

De agent gebruikt drie tools:

- `lab__resource__claim(resource, reason, wait_seconds)` — reserveren, eventueel
  met wachten (hoogstens 300 seconden).
- `lab__resource__release(resource)` — vrijgeven; zonder argument alles.
- `lab__resource__status()` — wat er te reserveren valt en wie er op zit.

Een claim hangt aan de **sessie** en gaat automatisch los zodra die beurt klaar
is. Blijft er toch iets hangen (een afgebroken run), dan zie je dat bij het lab
onder "Nu in gebruik" en kun je het met **losbreken** weghalen.

Een naam die niet in de catalogus staat wordt niet stilzwijgend geaccepteerd:
de agent krijgt de lijst terug. Anders reserveert een typefout niets en denkt
hij dat het geregeld is — het slechtste van beide werelden.

## Dit staat naast `board__claim`

`board__claim` gaat over **logische** bronnen die de agent zelf benoemt: een
Fabric-pipeline, een tabel, een map. Die kent LabX niet en kan het ook niet
kennen. `lab__resource__claim` gaat over het **fysieke** spul in een container,
dat LabX wél kent en dat jij beheert. Ze vervangen elkaar niet.

## Wat we bewust NIET hebben gedaan

- **Geen eigen werkmap of git-worktree per sessie.** Hetzelfde risico als twee
  keer Claude Code op één pc, en voor Fabric-werk is er vaak geen repo.
- **Geen eigen az-context per sessie.** Een lab hangt aan één Azure-account;
  daar valt niets te scheiden.
- **Geen resource-verdeling van CPU of geheugen.** Twee zware runs in één
  container zitten elkaar in de weg. Wie dat niet wil, geeft ze aparte werkers
  (of een apart lab) — dat kan al.

Dat zijn geaccepteerde risico's, geen vergeten randjes.
