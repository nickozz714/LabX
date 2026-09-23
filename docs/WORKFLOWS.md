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

## Sessies

Alle activiteiten van één run delen standaard **dezelfde sessie**: de agent
onthoudt wat hij in de vorige stap deed. Per activiteit kun je `verse_sessie`
aanzetten — een schone lei, precies wat je nodig hebt voor een beoordelaar die
niet door zijn eigen werk beïnvloed mag zijn. Daar hoort ook de **rol** bij:
een systeeminstructie voor die ene stap ("je bent reviewer, wees streng, wijzig
niets").

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

Te vinden bij de workflow onder **Verloop**. Een lopende run ververst zichzelf;
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

## Wat er nog komt

De canvas-editor (React Flow): activiteiten slepen, verbindingen tekenen,
eigenschappen rechts, en hetzelfde beeld als runweergave zodat je de workflow
ziet lopen. Tot die tijd bewerk je de stappen in het bestaande scherm; een
bestaande workflow blijft gewoon werken en wordt ingelezen als een rechte keten
van agent-activiteiten.
