# LabX

**LabX is een veilige AI-werkplaats: je geeft een AI-agent een eigen, afgeschermde
Docker-sandbox ("lab") waarin hij écht werk doet — code draaien, data verwerken,
repositories bouwen, tools en API's gebruiken — terwijl jij bepaalt wat er in en uit
mag.**

Waarom LabX:

- **Echt werk, geen chat-theater** — de agent (Claude Code CLI) heeft een volledige
  shell, bestandssysteem en netwerk *binnen het lab*. Hij installeert wat hij mist,
  test wat hij bouwt en levert resultaat op (inclusief publiceren naar git).
- **Afgeschermd by design** — alles draait in een wegwerpbare container. De
  data-egress-guard controleert wat het lab verlaat (regelset + optioneel een lokaal
  guard-model via Ollama). Sluit een lab en het is weg.
- **Token-zuinig toolgebruik** — de agent ziet alleen naam + beschrijving van elke
  toegestane tool; schema's laden on demand. Skills zijn how-to-kennis, geen
  poortwachter.
- **Werk dat zichzelf oppakt** — hang een **agent board** aan een lab: tickets in
  kolommen, en de agent pakt ze op, doet het werk in het lab en vult het ticket zelf
  aan. Handmatig, of op een cron-schedule. Boards synchroniseren met **Azure DevOps**
  of **Jira**.
- **Uitbreidbaar** — koppel MCP-servers (extern op de host óf als proces ín het lab),
  bundel kennis in skills met per-tool instructies, automatiseer met workflows en
  cron-schedules.
- **Volledig zicht** — live stappen en redenatie van de agent, tokenteller +
  context-indicator, achtergrondtaken die doorlopen terwijl jij weg navigeert.

## Installeren

**Desktop-app (aanbevolen)** — download de installer van de
[Releases-pagina](https://github.com/nickozz714/LabX/releases): macOS `.dmg` of
Windows `.exe`. Vereist [Docker Desktop](https://www.docker.com/products/docker-desktop/)
en een Claude-abonnement (`claude setup-token`). De app haalt voorgebakken images van
GHCR (met lokale build als fallback), genereert zelf secrets en leidt je bij de eerste
start door de setup: accountnaam + wachtwoord kiezen, Docker-check, setup-token plakken.

**Docker Compose (server of lokaal):**

```bash
git clone https://github.com/nickozz714/LabX.git && cd LabX
cp .env.example .env   # vul LABX_ADMIN_PASSWORD, LABX_JWT_SECRET, LABX_FERNET_KEY in
docker compose up -d --build
```

- Web-UI: http://localhost:8080 — API: http://localhost:8090 (Swagger op `/docs`)
- Optioneel lokaal guard-model meestarten: `docker compose --profile with-ollama up -d`

Meer detail (server-hardening, reverse proxy, docker-socket-proxy, GHCR-images):
zie de **[wiki](https://github.com/nickozz714/LabX/wiki)**.

## Wat zit erin

| Onderdeel | Kort |
| --- | --- |
| **Labs** | Docker-sandboxes (sibling containers), bestandsbrowser, exec, interactieve terminal (xterm.js), egress-guard, publish-naar-git, az-login |
| **Chat** | Claude Code CLI als volwaardige agent, gekoppeld aan een draaiend lab; Markdown, live stappen, tokenteller, per-chat model/effort (dropdown + `/model`, `/effort`) |
| **Achtergrondtaken** | Handmatig of door het model zelf gestart; turns draaien server-side door, ook als je wegnavigeert — Taken-tab in het rechterpaneel |
| **MCP-servers** | Host- (extern) of lab-servers (stdio in de container), scope per sessie/lab/beide, Azure-profielkoppeling, bulk-acties |
| **Skills** | Wizard met tool-picker (gegroepeerd per MCP-server), per tool instructies + input-schema-preview; installeerbaar in een lab (incl. bestanden) |
| **Workflows & schedules** | Markdown-stappen met visuele editor; cron-schedules draaien een prompt, een workflow, of board-werk tegen een lab |
| **Agent boards** | Kanban gekoppeld aan een lab; de agent pakt tickets op, werkt in het lab en doet verslag op het ticket (`board__*`-tools). Opdracht, acceptatiecriteria en tijdlijn staan gescheiden. Two-way sync met Azure DevOps / Jira, of alleen lezen |
| **Azure-profielen** | Meerdere versleutelde identiteiten, syncbaar naar host of lab |
| **Hooks** | Meerdere automatische hooks per gebeurtenis, zichtbaar als ⚙️-stappen in de chat |

## Agent boards

Een board is een kanban-bord dat aan een lab hangt. Die koppeling is het punt: pakt de
agent een ticket op, dan werkt hij ín dat lab — zelfde sandbox, zelfde egress-guard,
zelfde allowlist als een chat. Tijdens de run heeft hij `board__*`-tools waarmee hij het
ticket leest, opmerkingen plaatst en het naar een andere kolom verplaatst; het resultaat
staat dus op het ticket en niet in een chatlog. De thread achter zo'n run is dan ook
verborgen in de Chat-pagina — board-werk leest je op het board.

**Een ticket heeft drie gescheiden vlakken**, en die scheiding wordt in de agent-prompt
en in de tool-beschrijvingen afgedwongen:

| Vlak | Wat erin hoort | Wie schrijft |
| --- | --- | --- |
| **Omschrijving** | de opdracht — wat er moet gebeuren (Markdown) | mens; de agent alleen om een onduidelijke opdracht aan te scherpen |
| **Acceptatiecriteria** | wanneer is het klaar (Markdown, toetsbaar lijstje) | mens, of de agent stelt ze op als ze ontbreken |
| **Tijdlijn** | bevindingen, voortgang, vragen — nieuwste bovenaan | mens en agent |

Zonder die scheiding schrijft een agent zijn verslag zowel in een opmerking als onder de
omschrijving, en is na twee runs niet meer te zien wat er oorspronkelijk gevraagd werd.
De agent toetst zijn werk expliciet aan de acceptatiecriteria en meldt per criterium of
eraan voldaan is.

Werk laten oppakken kan op drie manieren:

- **Per ticket** — "Agent starten" in het ticketpaneel, met optioneel een extra instructie.
- **Per bord** — "Pak werk op" neemt de bovenste tickets uit de agent-kolom.
- **Op tijd** — een schedule van het type *board-werk* doet hetzelfde volgens cron.

**Externe koppeling.** Een board kan zijn tickets uit **Azure DevOps Boards** (work items,
PAT met scope *Work Items read/write*) of **Jira Cloud** (issues, e-mail + API-token)
halen. De richting is per board instelbaar:

- `two_way` (standaard) — velden, status en opmerkingen gaan ook terug naar de bron,
  inclusief wat de agent op een ticket schrijft.
- `pull` — de bron is leidend en LabX schrijft niets terug.

De sync doet eerst de push en dan de pull, zodat een nog niet weggeschreven lokale
wijziging niet door de bron overschreven wordt. De statusmapping (bordkolom ↔ status in de
bron) stel je per board in; "verbinding testen" haalt de echte statusnamen op zodat je ze
niet hoeft te raden. LabX-activiteitsregels ("verplaatst van X naar Y") blijven binnen
LabX — alleen echte opmerkingen gaan naar buiten.

## Architectuur

```
LabX/
  backend/    FastAPI + SQLite — src/{models,schemas,routers,services}
              services/boards/  board- en ticketlogica, agent-runs, DevOps/Jira-sync
  frontend/   Vite + React + TypeScript + Tailwind
  desktop/    Tauri-shell (macOS/Windows) rond dezelfde compose-stack
  docker-compose.yml
```

De backend mount `/var/run/docker.sock` en start labs als **sibling-containers** op
dezelfde Docker-daemon, verbonden via een eigen bridge-netwerk. `GET /api/system/docker`
geeft altijd een concrete diagnose (CLI aanwezig? daemon bereikbaar? socket gemount?).

## Ontwikkelen

```bash
# backend
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
LABX_HOME=/tmp/labx LABX_ADMIN_PASSWORD=test LABX_JWT_SECRET=test-secret-min-32-chars \
LABX_FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
python3 -m uvicorn server:app --app-dir src --port 8090

# frontend
cd frontend && npm install
npm run dev     # proxied naar localhost:8090
npm run build   # tsc -b && vite build

# desktop
cd desktop && npm install
npm run tauri dev
npm run tauri build   # installer voor het huidige platform
```

`scripts/smoke.sh` loopt het hoofdpad af tegen een draaiende stack: inloggen,
Docker-diagnose, lab aanmaken, exec, opruimen. Windows-installers bouwt de CI
(`windows-latest`); cross-compilen vanaf macOS/Linux kan niet.

## Status

LabX is een jong project met een bewust dunne kern: geen multi-tenant/rollen, één
agent-runtime, geen mobiele variant. Zie de
[wiki](https://github.com/nickozz714/LabX/wiki) voor de volledige documentatie en de
[Releases](https://github.com/nickozz714/LabX/releases) voor installers.
