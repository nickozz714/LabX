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

## Bouwvolgorde

Elke stap staat op zichzelf en is bruikbaar voordat de volgende er is.

1. **Cluster + vaste dienst.** Een cluster aanmaken, de emulator erin, op één
   netwerk, op een machine naar keuze. Nog geen branches.
2. **Branch-labs.** Een lab met een branch, afgetakt van een basis die jij
   kiest, met het cluster-startscript en een eigen poort.
3. **Opruimen.** Verwijderen kan altijd; niet-gecommit werk wordt getoond.
4. **Tickets ↔ branches.** Het veld op het ticket, en "Agent starten" dat het
   juiste lab pakt of maakt.
5. **Workflows op clusterniveau.**
6. **Nette namen** via een reverse-proxy per cluster — pas als je weet welke
   namen je wilt.
