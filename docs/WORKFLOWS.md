# Workflows

## Wat het was, en waarom dat niet volstond

Een workflow was een markdown-bestand met stappen. Bij het uitvoeren gingen
**alle** stappen in één prompt naar de agent, in één beurt van één sessie, en
daarna was het aan het model.

Dat verklaart in één keer wat er ontbrak: tijdens het draaien bestonden er geen
stappen. Dus viel er niets te vertakken, niets te herhalen, en niets te loggen
— een run bewaarde de eindtekst en verder niets, terwijl de redenatie ergens in
een chattranscript stond.

## Wat het nu is

Een workflow is een **graaf van activiteiten** die LabX zelf uitvoert: één
activiteit tegelijk, met een eigen invoer, uitvoer en status.

| Activiteit | Doet |
| --- | --- |
| **agent** | een opdracht aan de agent in het lab; optioneel met een rol en een JSON-schema |
| **shell** | een commando in de container — deterministisch, goedkoop, langs de guard |
| **als** | splitst op een voorwaarde; verbindingen `ja` en `nee` |
| **wacht** | een pauze, bijvoorbeeld tussen twee pogingen |
| **lus** (`voorelk`) | een omhulsel: alles wat erin ligt draait één keer per element van een lijst |
| **bubbel** (`parallel`) | een omhulsel: alles wat erin ligt draait tegelijk |

Verbindingen hebben een soort: **succes**, **fout** of **altijd** (na een
gewone activiteit) en **ja** / **nee** (na een `als`). `altijd` telt bij elke
afloop mee — dat is de verbinding voor "ruim op" en "meld het", die juist moet
lopen als er iets misging. Een activiteit zonder uitgaande verbinding is het
einde van die tak.

### Herhalen

Herhalen zit op de activiteit zelf, niet in een aparte lus:

- **`herhaal_over`** — een verwijzing naar een lijst. De activiteit draait één
  keer per element; `item` en `iteratie` zijn beschikbaar in de tekst en in
  voorwaarden. Een lege lijst slaat de activiteit over — dat is geen fout, een
  lege lijst is vaak juist het goede nieuws.
- **`herhaal_tot`** — een voorwaarde; hij draait opnieuw tot die klopt, met een
  harde bovengrens (`herhaal_max`). Want een model dat "nog niet klaar" blijft
  zeggen moet ergens stoppen.

Een lus over meerdere activiteiten hoort in een sub-workflow; die komt later.

### Verwijzen naar eerdere activiteiten

In de tekst van een activiteit met `{{ ... }}`, en in voorwaarden als losse
verwijzing:

```
stap.<sleutel>.uitvoer        de tekst die die activiteit opleverde
stap.<sleutel>.json.<pad>     een veld uit gestructureerde uitvoer
stap.<sleutel>.exit_code      de afloop van een shell-activiteit
stap.<sleutel>.status         "ok" of "fout"
invoer.<veld>                 waarmee de run gestart is
item / iteratie               binnen een herhaling
```

De sleutel is de naam van de activiteit in kleine letters met underscores
("Zijn er rijen?" → `zijn_er_rijen`).

Een voorwaarde is een **object** — links een verwijzing, een operator, rechts
een waarde — en geen vrij in te tikken expressie. Dat is precies wat een
schermpje met drie velden kan tonen, en er valt niets mee uit te voeren.
Operators: `==`, `!=`, `>`, `>=`, `<`, `<=`, `bevat`, `bevat_niet`, `is_leeg`,
`is_niet_leeg`. Een voorwaarde die nergens op slaat is `false` — de `nee`-tak,
want doorgaan alsof alles goed is, is precies wat je niet wilt als je niet weet
wat er staat.

Zet **geen aanhalingstekens** om de rechterwaarde: het veld is een waarde, geen
code. `'simpel'` wordt gelezen als `simpel` — de editor waarschuwt erbij, en de
motor negeert ze, zodat je vergelijking niet stil op de letterlijke tekst
mislukt.

Wat een `als` besloot, staat achteraf in het runverslag: niet alleen de tak,
maar ook de waarde waarop hij besloot, de waarde waarmee hij vergeleek, en — als
de linkerverwijzing niets opleverde — welke velden er wél in zaten. Een
voorwaarde op `item.complexiteit` terwijl het veld `complexity` heet, is
daardoor geen stille `nee` meer.

### Per element: de lus

Sleep activiteiten in een **lus** en alles wat erin ligt draait één keer per
element van een lijst. Binnen de lus zijn `{{ item }}` (met zijn velden, zoals
`{{ item.title }}`) en `{{ iteratie }}` beschikbaar — óók in een `als` die
erin ligt. Dat is precies het verschil met `herhaal_over` op één activiteit:
daar past maar één stap in, in een lus past een hele kleine workflow.

Elke activiteit in de lus wordt gelogd met de ronde en het element waarin hij
draaide, zodat het verslag van dertien rondes leesbaar blijft. De lus zelf
staat op *bezig* tot de laatste ronde klaar is, en sluit af met hoeveel rondes
er gedraaid zijn. Draait hij nul rondes, dan staat erbij waarom: geen lijst
gekozen, lege lijst, of een verwijzing die niets opleverde.

Het typische geval: een analysestap levert `incidentGroups` op, de lus loopt
daarlangs, en een `als` op `item.complexity` stuurt elke groep de goede kant
op.

Verder: er zit een bovengrens op het aantal elementen (een lijst die onverwacht
duizend lang is, is duizend agent-beurten), elke ronde begint met een schone
kopie van wat er buiten de lus bekend is (zodat ronde 3 niet in ronde 4 lekt),
en bij een mislukte ronde kies je tussen stoppen en doorgaan.

### Tegelijk draaien: de bubbel

Sleep activiteiten in een **bubbel** en ze draaien gelijktijdig. Wat je moet
weten:

- Elke tak krijgt **onvermijdelijk een eigen sessie**. Eén CLI-sessie kan geen
  twee beurten tegelijk hebben; een tak die de gedeelde sessie zou hervatten,
  husselt de context van beide door elkaar.
- Heeft het lab meer werkers, dan worden de takken over die containers
  verdeeld. Zijn er minder werkers dan takken, dan draaien ze naast elkaar in
  dezelfde container — twee keer Claude op één pc, met dezelfde afweging als
  een bundel in een planning.
- Takken kunnen elkaars uitvoer niet gebruiken (ze lopen tegelijk), maar ná de
  bubbel kan dat wel: `stap.<sleutel>.uitvoer` van elke tak staat klaar.
- `hoeveel tegelijk` begrenst het aantal gelijktijdige takken; bij een mislukte
  tak kies je tussen "de bubbel mislukt" (volg de fout-verbinding) en
  "doorgaan".
- Een activiteit in een bubbel wordt **alleen door die bubbel** gestart — hij
  doet niet ook nog mee in de gewone wandeling door de graaf.

## Sessies

Alle activiteiten van één run delen standaard **dezelfde sessie**: de agent
onthoudt wat hij in de vorige stap deed. Per activiteit kun je `verse_sessie`
aanzetten — een schone lei, precies wat je nodig hebt voor een beoordelaar die
niet door zijn eigen werk beïnvloed mag zijn. Daar hoort ook de **rol** bij:
een systeeminstructie voor die ene stap ("je bent reviewer, wees streng, wijzig
niets").

## Een lab dat slaapt

Een lab gaat vanzelf uit als er een tijd niet in gewerkt is — dat hoort zo, en
het is precies waarom een workflow er last van had: die draait 's nachts, als
het lab al uren stil is.

Daarom hoef je geen draaiend lab te kiezen. Kies er een die slaapt, en de run
**zet hem als eerste stap aan** — dat staat ook als eerste regel in het
verslag, anders lijkt de eerste activiteit onverklaarbaar lang te duren. Het
inrichten dat daarna loopt (pakketten terugzetten) wordt niet afgewacht: dat is
idempotent, en de meeste activiteiten hebben er niets van nodig.

Hetzelfde geldt voor een **schedule**: die faalde met "Lab draait niet" — op
het moment waarvoor hij bestond, en zonder dat iemand keek. Nu wordt het lab
gestart. Lukt dát niet, dan faalt de run met wat er precies misging.

## Schedulen

Dat kon al: **Scheduling** → type "workflow" + lab + cron. Wat er veranderde,
is dat een geplande run nu dezelfde motor gebruikt en in **dezelfde
geschiedenis** terechtkomt als een handmatige run. Daarvóór landde hij in de
schedule-runs en zag je óf dat de cron had gedraaid, óf wat de workflow had
gedaan — nooit allebei.

## Monitoren

Per run staat er nu een volledig verslag: per activiteit de **invoer** die het
model kreeg, de **tool-aanroepen** ertussenin, de **uitvoer**, het
gestructureerde resultaat, de duur, het tokenverbruik en de kosten, plus welke
**tak** er genomen is. Bij herhalingen staat elke ronde er apart in.

Te vinden bij de workflow onder **Monitoring**. Een lopende run ververst zichzelf;
afbreken kan tussen twee activiteiten (de lopende maakt hij af — een agent-beurt
halverwege afkappen laat het lab in een toestand achter waarvan niemand meer
weet welke).

De sessie van een run is een gewone thread, dus je kunt er achteraf in
terugkijken. Hij staat niet in je chatlijst: een workflow die elk uur draait,
zou die anders vullen.

## Vangnetten

- Een run stopt na 200 activiteiten. Eén verkeerd getekende verbinding mag geen
  lab een nacht bezet houden.
- Een mislukte activiteit stopt de run, tenzij er een `fout`- of
  `altijd`-verbinding is of de activiteit `mag_falen` heeft.
- Een mislukte run meldt zichzelf via de gewone meldingen — een workflow die
  's nachts draait mag niet stil omvallen.

## Wat er met je oude workflows gebeurt

Een workflow van vóór de graaf wordt bij het opstarten **één keer echt
omgezet**: zijn stappen worden opgeslagen als agent-activiteiten in een rechte
keten, met een eigen plek op het doek. Daarvoor werd die keten wel afgeleid bij
elk lezen en uitvoeren, maar nergens bewaard — dus verzon het doek telkens
opnieuw waar de activiteiten stonden, en bleef een workflow die je nooit opende
half in de oude wereld hangen.

Heeft een workflow al een graaf (zelf getekend of eerder omgezet), dan wordt er
niets aangeraakt. Dat is belangrijker dan het klinkt: deze omzetting draait bij
élke start van de backend, dus zou hij overschrijven, dan was je eigen tekening
na elke herstart weg.

Markdown blijft daarnaast gewoon bestaan als im- en export.

## Verwijzingen kies je, je typt ze niet

Een voorwaarde en een lus verwijzen naar eerdere uitvoer. Die verwijzing uit je
hoofd typen is vragen om fouten: een typefout levert geen foutmelding op maar
een voorwaarde die altijd onwaar is, en dat merk je pas als de verkeerde tak
loopt.

LabX kent de JSON-schema's van de activiteiten, dus de velden staan in een
keuzelijst — inclusief de uitleg die je bij een veld hebt gezet. Binnen een lus
staan `item` en zijn velden bovenaan. Zelf typen kan nog steeds (niet alle
uitvoer heeft een schema); staat je verwijzing niet in de lijst, dan zegt het
scherm dat erbij.

Het **uitvoerschema** van een agent-stap teken je ook: velden met een naam, een
soort en een uitleg, met geneste objecten en lijsten. Die uitleg is geen
franje — het model leest hem, én hij komt terug in de keuzelijst hierboven. Een
schema dat niet in velden te vatten is (anyOf, patronen, $ref) blijft gewoon
als JSON bewerkbaar.

## De editor

Een workflow openen doet nu een eigen pagina open (`/workflows/<id>`), geen
pop-up meer. Links de activiteiten die je kunt toevoegen, in het midden het
doek, rechts de eigenschappen van wat je aanklikt.

- **Verbinden** doe je door van een punt onder een activiteit te slepen: het
  groene punt is "bij succes", het rode "bij fout". Een `als` heeft ja en nee.
  De soort van de verbinding komt dus uit waar je begint te slepen — je hoeft
  er achteraf niets aan in te stellen.
- **Parallel maken** doe je door een activiteit in een bubbel te slepen. Eruit
  slepen zet hem terug in de hoofdstroom.
- **Terwijl een run loopt kleurt het doek mee**: geel is bezig, groen klaar,
  rood mislukt. Onderin staat de monitoring met per activiteit de invoer, de
  tool-aanroepen, de uitvoer en de kosten.

## Wat er nog komt

Een sub-workflow-activiteit, zodat een lus over meerdere activiteiten kan (nu
zit herhalen op één activiteit). En het doek als losse runweergave, met de
geschiedenis van een specifieke run in beeld.
