# Development clusters

Een **cluster** is een groep labs die bij elkaar horen omdat ze dezelfde
applicatie draaien: één vaste laag die blijft staan, en losse branch-labs die
komen en gaan.

De aanleiding is concreet. IntelligentHive draait nu op een MacBook: front-end
en API lokaal, de Cosmos-emulator in Docker. Dat werkt, maar het is één
versie tegelijk — en zodra je twee branches naast elkaar wilt bekijken, of een
agent op een branch wilt laten werken terwijl jij op een andere kijkt, loopt
het vast op die ene machine.

---

## Wat een cluster is

```
CLUSTER  "IntelligentHive"            draait op: MacBook Pro (of de server)
│
├─ VASTE DIENSTEN                     blijven staan, gedeeld
│  └─ cosmos          :18000          de emulator
│
└─ BRANCH-LABS                        komen en gaan
   ├─ feature/zoek-filter   :18001    front-end + API van díé branch
   ├─ release/2026.09       :18002
   └─ feature/export        :18003
```

Alles in één cluster zit op **één docker-netwerk**. Een branch-lab bereikt de
emulator dus gewoon op `cosmos:8081`, zonder poorten of adressen — net zoals
de labs nu de backend bereiken.

Een cluster draait op **één machine**, en dat mag elke gekoppelde lab-host
zijn. Daar is niets nieuws voor nodig: een cluster erft `host_id` van de
bestaande machines, en alles wat daarvoor geldt (SSH, tunnels, wakker blijven)
geldt hier ook.

---

## De vaste diensten

Een vaste dienst is een container die bij het cluster hoort en niet bij een
branch: de Cosmos-emulator, straks misschien een message broker of een
mailvanger.

- Hij **blijft draaien** als branch-labs verdwijnen.
- Hij is **opnieuw op te bouwen vanaf een branch** — het is een gewone
  container met een image of een Dockerfile uit de repo, dus "bouw hem
  opnieuw vanaf `release/2026.09`" is dezelfde handeling als bij een lab.
- Hij staat op het clusternetwerk met een vaste naam (`cosmos`), zodat elk
  branch-lab hem zonder configuratie vindt.

**Data is gedeeld.** Alle branches praten tegen dezelfde emulator en dezelfde
gegevens. Dat is een bewuste keuze: het is simpel en je test tegen echte data.
De prijs staat er ook bij — een migratie in de ene branch raakt de andere. Als
dat gaat schuren is de volgende stap een database per branch, en dan hoeft
alleen de `COSMOS_DATABASE` per lab te verschillen.

---

## Branch-labs

Een branch-lab is een gewoon LabX-lab met twee dingen erbij: het weet welke
**branch** het draait, en het weet bij welk **cluster** het hoort.

Wat er gebeurt bij het aanmaken:

1. De repo wordt gekloond, **uitgecheckt op de basis die jij kiest** — `main`,
   maar net zo goed `release/2026.09`.
2. Een nieuwe branch wordt daarvan afgetakt, of een bestaande wordt
   uitgecheckt.
3. Het cluster-startscript draait: installeren en opstarten van front-end en
   API, precies zoals je het nu op de MacBook met de hand doet.
4. De poort van dat lab wordt gepubliceerd, zodat je de applicatie van díé
   branch in je browser kunt openen.

Het startscript hoort bij het **cluster** en niet bij het lab: je schrijft één
keer op hoe IntelligentHive start, en elk branch-lab gebruikt het. Verandert
dat, dan verandert het op één plek.

### Afbreken

Een branch-lab is wegwerpbaar — dat is het punt, en dat blijft het ook:

> **Verwijderen kan altijd.** Staat er niet-gecommit werk in, dan zegt LabX
> dat erbij — met wat er openstaat — maar het houdt je niet tegen.

Een waarschuwing en een blokkade zijn niet hetzelfde. Een blokkade zegt dat
LabX beter weet dan jij of dat werk ertoe doet, en dat weet hij niet: negen
van de tien keer is het een half experiment dat juist weg mag. Wat LabX wél
kan, is zorgen dat je het niet per ongeluk doet — dus laat hij zien wát er
openstaat op het moment dat je het weggooit.

Dat is iets anders dan bij het verplaatsen van een lab met lopend werk. Daar
wordt wel geweigerd, en om een andere reden: daar breek je een agent
halverwege af en haal je het volume onder zijn handen weg. Hier gooi je
bewust iets weg dat bedoeld was om weg te gooien.

---

## Bereikbaar van buiten

Elk branch-lab krijgt een **eigen poort** op de clustermachine:

```
http://macbook.local:18001   feature/zoek-filter
http://macbook.local:18002   release/2026.09
```

Draait het cluster op een andere machine dan LabX zelf, dan loopt dat via
dezelfde SSH-tunnel die labpoorten nu al gebruiken — er komt dus niets op je
LAN te staan wat er niet hoort.

Het scherm toont welke poort bij welke branch hoort; dat is de prijs van deze
variant. Nette namen (`zoek-filter.hive.lab`) kunnen later met een
reverse-proxy per cluster, en dat verandert niets aan de structuur eronder —
alleen de voorkant.

---

## Borden, tickets en branches

Een ticket krijgt een **branch**. Daarmee wordt de keten rond:

```
ticket SWI-204  →  branch feature/zoek-filter  →  branch-lab  →  :18001
```

- "Agent starten" op een ticket werkt in het branch-lab van dat ticket, en
  maakt dat lab aan als het er nog niet is.
- De chat van dat lab praat over díé branch: wat de agent wijzigt, wijzigt in
  die werkkopie, en wat jij op `:18001` ziet is het resultaat.
- Is het ticket klaar en de branch samengevoegd, dan kan het lab weg. Het
  cluster en de vaste diensten blijven.

Workflows werken op clusterniveau: "maak een branch-lab voor dit ticket",
"draai de tests in elk branch-lab", "ruim labs op van samengevoegde branches".

---

## Een vaste dienst bedienen

Een vaste dienst is geen lab. Er staat geen agent in, je kunt er niet in
chatten, en toch is hij juist het ding waar je wat MEE moet: Cosmos
leegmaken, opnieuw seeden, even kijken wat erin zit. Dat deed je tot nu toe
in een terminal naast LabX, met een commando dat je elke keer opzocht.

Onder **Diensten → Bedienen** staan knoppen bij elke dienst. Het model
bedenkt ze én schrijft het script eronder, want welke handelingen zinnig zijn
volgt uit het IMAGE — een Cosmos-emulator heeft andere knoppen dan een Redis,
en een vaste lijst in LabX zou voor allebei verkeerd zijn.

**Twee plekken om te draaien, allebei nodig.**

| | waar | waarvoor |
|---|---|---|
| `in` | `docker exec` in de dienst zelf | gereedschap dat in het image zit (`redis-cli`, `psql`) |
| `naast` | wegwerpcontainer op het clusternetwerk | alles wat een client nodig heeft die er NIET in zit |

De Cosmos-emulator heeft geen curl, laat staan een SDK, en leegmaken is juist
dáár wat je wilt. Zonder die tweede plek was de hele functie voor die dienst
onbruikbaar. Een `naast`-script krijgt `$DIENST_HOST`, `$DIENST_POORT` en
`$DIENST_POORTEN` mee, zodat de naam van de dienst er niet hard in staat.

**Het script staat in het volle zicht.** Een knop waarvan je niet kunt zien
wat hij doet, druk je niet in — zeker niet als een model hem heeft
geschreven. Stel je hem bij, dan laat een volgende modelronde hem met rust:
anders is je correctie één klik later weer weg.

Daarnaast een chat die niet adviseert maar doet. Een antwoord van de vorm
"voer dit even uit in je terminal" lost niets op — dan had je die terminal
net zo goed meteen kunnen openen. Komt het model met een script, dan draait
het, en de uitvoer staat eronder. Werkte het? Eén klik maakt er een knop van.

Wat er NIET naar het model gaat: de WAARDEN van de omgevingsvariabelen van de
dienst. Alleen de namen.

---

## Bestanden van buiten: bundels

Een vaste dienst begint leeg, en de bron waaruit hij gevuld hoort te worden
is niet altijd bereikbaar. De Cosmos van IntelligentHive zit achter een
accountfirewall; gemeten vanuit het lab:

```
403  Request originated from IP 86.80.134.69 through public internet.
     This is blocked by your Cosmos DB account firewall settings.
```

`.devcontainer/sync.sh` komt daar dus niet bij, en `bootstrap.sh` kent geen
tweede weg — die seedt met `sync.sh --if-empty` of helemaal niet. De export
staat dan gewoon op iemands schijf, en tot nu toe was er geen weg om die het
cluster in te krijgen: de bestandsbrowser hoort bij een LAB.

Onder **Diensten → Bestanden van buiten** upload je een `.zip` of `.tar.gz`
met één zin erbij over wat ermee moet ("zet deze dump in de Cosmos-emulator,
met de partition keys uit het manifest"). Het model schrijft het script; LabX
draait het in een wegwerpcontainer op het clusternetwerk, naast de dienst die
je kiest, met de bundel **alleen-lezen** op `/bundel`.

Vier dingen liggen vast:

- **De inhoud gaat nooit naar het model.** Het ziet de bestandsnamen en hun
  omvang, en krijgt de opdracht om een manifest in zijn SCRIPT te lezen in
  plaats van te raden. Een dump van een klant hoort in een container, niet in
  de context van een taalmodel.
- **Alleen-lezen aangekoppeld.** Een script dat een dump inleest hoort die
  dump niet te kunnen veranderen — anders is de tweede keer niet meer
  dezelfde als de eerste.
- **Uitpakken gebeurt in Python, per ingang.** `../../etc/passwd`, een
  absoluut pad en een symlink worden geweigerd, niet opgeschoond.
- **Een script dat werkte is ongewijzigd te herhalen**, zonder het model.
  Anders raadt het de volgende keer net iets anders.

De bundel staat in een eigen docker-volume van het cluster, niet in een map
op de server: het cluster kan op een andere machine draaien, en een volume is
het enige dat daar ook werkt.

---

## Wat er bewust NIET in zit

- **Geen tweede soort lab.** Een branch-lab is een gewoon lab met een branch
  en een cluster erbij. Alles wat labs al kunnen — werkers, data-guard,
  geheimen, verplaatsen naar een andere machine — geldt hier ook, en blijft
  op één plek geregeld.
- **Geen eigen netwerkmodel.** Eén docker-netwerk per cluster, verder niets.
- **Geen automatisch opruimen.** Een lab verdwijnt als jij dat zegt of als
  zijn TTL verloopt. Een branch die stil samengevoegd wordt, laat zijn lab
  staan tot je ernaar kijkt — liever dat dan werk weggooien waar niemand om
  vroeg.

---

## De verkenner

Alles hierboven gaat ervan uit dat iemand opschrijft wat een cluster is: dit
image, dit startscript, deze vaste dienst. Dat werkt voor het eerste cluster,
want dat bouwen we met de hand. Het werkt niet voor het tweede, en al helemaal
niet voor een repo die je vorige week niet kende.

Daarom komt er een stap vóór het cluster: **de verkenner leest de repo en
stelt een cluster voor.**

```
repo-URL + branch
   │
   ▼
VERKENNING            wegwerpcontainer, kloont ondiep, draait niets
   │
   ▼
PLAN                  vaste diensten · lab-image · startscript · omgeving
   │                  elk met bewijs en zekerheid
   ▼
JIJ KEURT GOED        in zijn geheel of per onderdeel
   │
   ▼
CLUSTER
```

De verkenning **draait niets uit de repo**. Ze kloont ondiep in een
wegwerpcontainer, leest bestanden, en gooit de container weg. Een repo die je
niet vertrouwt mag je veilig verkennen; pas bij het goedkeuren van het plan
start er iets.

### Bewijs, geen gevoel

De regel die dit eerlijk houdt: **geen vondst zonder vindplaats.** Elk
onderdeel van het plan noemt het bestand en de regel waar het op gebaseerd is,
en hoe zeker het is.

```
vaste dienst   ih-cosmos
  image        mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator:vnext-preview
  poorten      8081, 1234
  bewijs       components/api/.env.example:12  COSMOS_ENDPOINT=
               components/api/.env.example:13  COSMOS_KEY=
  zekerheid    midden — afgeleid uit omgevingssleutels, niet uit een compose
```

Zonder die regel is het plan een mening, en een mening kun je niet nakijken.

### De ladder

De verkenner zoekt in volgorde van hard naar zacht, en stopt niet bij de
eerste treffer — hij verzamelt alles en zegt erbij waar het vandaan komt.

| trede | bron | levert | zekerheid |
|---|---|---|---|
| 1 | `docker-compose*.yml`, `.devcontainer/` | vaste diensten, compleet | hoog |
| 2 | `Dockerfile`, `Makefile`, `Taskfile` | lab-image, bouwstappen | hoog |
| 3 | `.env.example`, `.env.*` | welke diensten de app verwacht | midden |
| 4 | `package.json`, `pyproject.toml`, `go.mod` | taal, pakketbeheerder, startcommando's | hoog |
| 5 | `README`, `docs/` | wat je met de hand zou doen | laag |

Een repo met een compose-bestand is in één klap klaar: daar staat het cluster
al in, iemand heeft het alleen nooit zo genoemd. IntelligentHive heeft dat
niet en landt op trede 3 en 4 — de Cosmos-emulator volgt uit de
omgevingssleutels, Node en yarn 4 uit `packageManager`, en het startscript uit
de scripts in de workspace-root.

### De dienstencatalogus

Wat een omgevingssleutel tot een container maakt, is een tabel in LabX — niet
iets dat de repo hoeft te weten. Dat is het stuk dat de verkenner algemeen
maakt:

```
COSMOS_ENDPOINT / COSMOS_KEY        → cosmos-emulator      8081, 1234
DATABASE_URL=postgres://            → postgres:16          5432
REDIS_URL                           → redis:7              6379
MONGODB_URI                         → mongo:7              27017
AMQP_URL / RABBITMQ_*               → rabbitmq:3-management 5672, 15672
AZURE_STORAGE_*devstoreaccount1     → azurite              10000-10002
```

De tabel is uit te breiden zonder dat er code bij hoeft; een onbekende sleutel
is geen fout, die komt gewoon in de tweede lijst terecht.

### Twee lijsten, niet één

Een verkenning levert altijd twee dingen op, en de tweede is de belangrijkste:

- **Wat ik kan nabouwen.** De vaste diensten. Een emulator, een database, een
  broker — dingen met een image.
- **Wat van buiten moet komen.** Sleutels naar echte diensten die geen
  emulator hebben. IntelligentHive heeft er vier: Application Insights,
  Langfuse, Locize en Cognitive Services.

Die tweede lijst is geen restpost. Het is precies het rijtje waar een
branch-lab op stukloopt als niemand het vooraf heeft opgeschreven — niet bij
het starten, maar veertig minuten later, halverwege een bouw. Het plan zegt
daarom welke geheimen het cluster nodig heeft, en het cluster is pas compleet
als die in de kluis staan. Geen token in het plan, alleen de naam.

### De rol van het model

Een agent leest de repo zoals een mens dat zou doen en stelt het cluster voor.
De detectoren lopen daarna langs elke vondst en kijken of er bewijs voor is.
Model leidend, detectoren als controle.

Dat is de flexibele keuze, en die is bewust gemaakt: een repo die buiten de
ladder valt levert bij detectoren-alleen een dun plan dat jij mag aanvullen,
en dat is precies het handwerk waar de verkenner vanaf moest. De prijs van
deze kant op is reproduceerbaarheid, en die prijs wordt op drie plekken
afgekocht.

**Het plan wordt bevroren.** Na goedkeuring is het plan de configuratie van
het cluster. Reproduceerbaarheid zit op het niveau van het plan, niet van de
verkenning: er wordt nooit stilletjes opnieuw verkend. Verkennen is een
handeling die jij doet, en het resultaat komt terug als een verschil met wat
er goedgekeurd is — niet als een vervanging.

**Onbevestigd is niet aangevinkt.** Een vondst die de detectoren niet kunnen
staven komt wél in het plan, met vindplaats en als lage zekerheid, maar staat
in het goedkeurscherm **uit**. Je kunt hem aanzetten; hij gaat niet vanzelf
mee. Zo blijft "model leidend" iets anders dan "model beslist", en blijft de
harde regel overeind: er draait geen container waar jij niet op hebt geklikt.

**De uitkomst wordt bewaard per commit.** Een verkenning hoort bij een repo op
een commit. Het plan opnieuw openen draait niets opnieuw; alleen een nieuwe
commit of een expliciet "verken opnieuw" doet dat. Dat scheelt geld en tijd,
en het maakt het verschil tussen twee verkenningen zichtbaar in plaats van
verwarrend.

De agent heeft verder een begrenzing in bestanden en tijd. Hij leest een repo,
hij doorzoekt hem niet eindeloos.

**Gemeten.** Op ND3X — een repo die het model niet kende — leverde dit een
beeld van 614 paden en 29 gelezen bestanden op, en daarop 30 seconden werk
voor ongeveer 35 cent. De uitkomst zette `web` en `api` niet in de vaste laag
(die worden uit de repo gebouwd) en `ollama` wel, met de regelnummers uit
`docker-compose.yml` erbij. Drie dingen die het niet uit de repo kon opmaken
zette het in `vragen` in plaats van ze te gokken. Dat is het gedrag waar deze
opzet op rekent, en het is de reden dat de uitkomst per commit bewaard wordt:
dit is geen aanroep die je per ongeluk vijf keer doet.

### Keuze, vaststelling, en lijstje

Niet elk onderdeel van een plan is hetzelfde soort ding, en dat zichtbaar
maken scheelt de helft van de verwarring in het goedkeurscherm.

- Een **vaststelling** heeft geen vinkje. Wat uit de repo gebouwd wordt,
  wordt gebouwd — daar valt niets aan te kiezen. Het staat er omdat je het
  moet weten, niet omdat je erover moet beslissen.
- Een **keuze** laat er precies één toe. Het lab-image is er zo een: een repo
  levert er zo vijf kandidaten voor op (een Dockerfile, een manifest, een
  compose-service, een voorstel van het model), en vijf aanvinken levert er
  alsnog één op — waarbij de volgorde van een lus bepaalt welke. Dat is geen
  keuze maar een loterij.
- Een **lijstje** is de rest: vaste diensten, geheimen, instellingen. Daar
  mag je aan- en uitvinken wat je wilt.

### Opnieuw verkennen

Het plan hoort bij het cluster en is goedgekeurd vanaf één basisbranch. Maar
een branch kan iets toevoegen — een dienst erbij in compose, een nieuwe
verplichte sleutel.

Bij het aanmaken van een branch-lab draait de verkenning daarom nog een keer,
op díé branch. Is er verschil, dan **start het lab gewoon** en staat het
verschil erbij: "deze branch verwacht ook `redis`." Informeren, niet
tegenhouden — om dezelfde reden als bij het afbreken van een lab.

---

## Meteen kunnen draaien

Een branch-lab aanmaken is nu: klonen, aftakken, startscript draaien. Dat
klopt, maar "gestart" is niet hetzelfde als "bruikbaar". Drie dingen
ontbreken.

**Een gedeelde bouwcache per cluster.** `yarn build:dependencies` loopt over
twaalf packages, en dat is de hele wachttijd. Eén volume per cluster, gekoppeld
aan de cache van de pakketbeheerder, en het tweede branch-lab betaalt die prijs
niet opnieuw. Nadrukkelijk de *cache*, niet `node_modules`: dat laatste delen
tussen branches is hoe je aan spookfouten komt.

**Een gereedsignaal.** Een lab is `bezig` tot zijn poort antwoordt, en pas dan
`draait`. Zonder dat betekent "het draait" alleen "ik heb het script gestart",
en dat is waar je tien minuten verliest met kijken naar een pagina die nog
niet bestaat.

**Een basisimage per cluster** — later, en alleen als de cache niet genoeg
blijkt. Eén keer bouwen vanaf de basisbranch met de afhankelijkheden warm,
branch-labs starten daarvandaan. Grote winst, maar het brengt een vraag mee
die de cache niet heeft: wanneer is dat image oud? Die vraag wil ik pas
beantwoorden als ik weet dat hij gesteld moet worden.

### Een branch opgeven

Twee ingangen, dezelfde machinerie:

- **"Maak een branch."** Nieuwe naam, basis naar keuze — `main`, maar net zo
  goed `release/2026.09`.
- **"Haal deze branch op."** Bestaande branch, wordt uitgecheckt.

Dat werkt al. Wat eraan toegevoegd wordt is het plan: het branch-lab erft het
image, het startscript, de omgeving en de poorten van het cluster, zodat
"aanmaken" en "kunnen draaien" dezelfde handeling zijn.

---

## Bouwvolgorde

Elke stap staat op zichzelf en is bruikbaar voordat de volgende er is.

1. **Cluster + vaste dienst.** ✓ Een cluster aanmaken, de emulator erin, op
   één netwerk, op een machine naar keuze. Nog geen branches.
2. **Branch-labs.** ✓ Een lab met een branch, afgetakt van een basis die jij
   kiest, met het cluster-startscript en een eigen poort.
3. **De verkenner.** Repo lezen, plan voorstellen met bewijs, goedkeuren,
   cluster vullen. Hierna is een tweede repo geen handwerk meer.
4. **Meteen kunnen draaien.** Gedeelde bouwcache en een gereedsignaal.
5. **Opruimen.** Verwijderen kan altijd; niet-gecommit werk wordt getoond.
6. **Tickets ↔ branches.** Het veld op het ticket, en "Agent starten" dat het
   juiste lab pakt of maakt.
7. **Workflows op clusterniveau.**
8. **Nette namen** via een reverse-proxy per cluster — pas als je weet welke
   namen je wilt.

Stap 3 en 4 zijn naar voren gehaald. Niet omdat opruimen onbelangrijk is, maar
omdat zonder die twee elk volgend cluster weer met de hand moet, en elk
branch-lab een wachtkamer zonder klok is.


---

## Bijlage: IntelligentHive als eerste cluster

Afgelezen van de MacBook Pro, zodat het cluster reproduceert wat daar nu met
de hand gebeurt. Dit is de maat waaraan stap 1 en 2 af te meten zijn.

### De repo

| | |
|---|---|
| Plek | `~/repos/Intelligenthive` |
| Remote | `git@github.com:beeminds-data/Intelligenthive.git` (**SSH**, privé) |
| Huidige branch | `feature/3130-losse-entiteiten-laden-in-gold` |
| Soort | yarn-workspaces monorepo, `packageManager: yarn@4.9.2` |

**Let op — dit raakt stap 2.** De remote is een SSH-URL. LabX kloont nu over
HTTPS met een token in een header; dat werkt hier niet. Een branch-lab heeft
dus óf een HTTPS-URL met een token, óf een deploy-key in het lab. Dat is een
keuze die gemaakt moet worden vóór het eerste branch-lab, niet erna.

### Hoe het start

```
yarn start:api     →  yarn workspace @beeminds/api start
yarn start:app     →  yarn workspace @beeminds/app dev
yarn start:agent   →  yarn workspace @beeminds/agent start
```

Daarvóór moet er gebouwd worden, en dat is geen detail:

```
yarn build:dependencies   # ~12 packages: interfaces, error-handling, auth,
                          # cdm, loader-fabric, models, loader-synapse,
                          # metamodel-purview, publisher-loganalytics,
                          # markdown, openai
yarn build:apps           # app + api
```

Dat is precies wat het **cluster-startscript** gaat bevatten: één keer
opschrijven, elk branch-lab draait het.

### De vaste dienst

```
ih-cosmos
  image:  mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator:vnext-preview
  poorten: 8081 (data), 1234 (explorer)
```

In een cluster draait deze container onder dezelfde naam op het
clusternetwerk. De API vindt hem dan via zijn eigen omgevingsvariabelen:

```
COSMOS_ENDPOINT=https://ih-cosmos:8081
COSMOS_KEY=<de vaste emulator-sleutel>
```

Geen poorten nodig tussen de labs onderling — alleen naar buiten, voor jou.

### Omgeving per onderdeel

- `components/api/.env.example` — o.a. `COSMOS_ENDPOINT`, `COSMOS_KEY`,
  `NODE_ENV`, `LOG_LEVEL`, `API_KEY`, en een reeks externe diensten
  (Application Insights, Langfuse, Locize, Cognitive Services).
- `components/app/.env.debug` — `VITE_TENANT_ID`,
  `DATABASE_CONNECTION_STRING`.

De front-end heeft **meerdere profielen**: `.env.debug`, `.env.realestate`,
`.env.insurance`, `.env.municipality`. Een branch-lab moet er dus één kiezen.
Voor ontwikkelwerk is `debug` de voor de hand liggende standaard; het hoort
een instelling van het cluster te zijn, niet iets dat in een script
vastgeroest zit.

### Wat dit betekent voor de bouw

1. Een branch-lab heeft een image met **Node en yarn 4** — niet het kale
   python-image dat labs nu standaard krijgen.
2. Het cluster heeft een **startscript** (bouwen + starten) en een
   **env-profiel** als instelling.
3. De **git-toegang** moet geregeld zijn vóór het eerste branch-lab.
4. `yarn build:dependencies` duurt; een branch-lab is niet in tien seconden
   klaar. Het inrichten loopt daarom op de achtergrond, zoals bij elk lab —
   maar de verwachting hoort in het scherm te staan.
