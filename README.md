# LabX

Een losstaande "Lab" app — een POC die de ND3X Lab-functionaliteit (Docker-werkruimte
+ Claude Code CLI-agent + data-egress-guard) kopieert als zelfstandig product, zonder
ND3X-afhankelijkheid. Zie `docs/PLAN.md`-achtergrond in de sessie die dit gebouwd heeft
voor de volledige context; kort samengevat lost LabX drie pijnpunten uit ND3X op:

1. **Docker "niet beschikbaar"** — LabX draait zelf in een container maar mount
   `/var/run/docker.sock`, heeft de docker-CLI in het image, en start labs als
   **sibling-containers** naast zichzelf op dezelfde daemon (`GET /api/system/docker`
   geeft een concrete diagnose i.p.v. een blinde 503).
2. **Tool-keuze kost tokens** — de chat draait altijd als Claude Code CLI-agent met
   tool-search aan: alleen naam+beschrijving van elke toegestane tool staat in context,
   schema's laden on demand. Skills zijn how-to, geen poortwachter.
3. **Skill Wizard zonder tools** — de wizard laat je tools (gegroepeerd per MCP-server,
   host of in-lab) kiezen, toont per tool het input-schema als leidraad, en laat je per
   tool een instructie opgeven.

## Architectuur

```
LabX/
  backend/    FastAPI + SQLite, src/{models,schemas,routers,services}
  frontend/   Vite + React + TypeScript + Tailwind
  docker-compose.yml
```

- **Labs**: Docker-werkruimtes (sibling containers via de gemounte socket), met
  data-egress-guard (regels + optioneel lokaal LLM als tweede mening), bestandsbrowser,
  exec, interactieve terminal (xterm.js), publish-naar-git, az-login.
- **Chat**: vereist een gekoppeld, draaiend lab — zonder lab werkt de invoer niet. Draait
  de Claude Code CLI als volwaardige agent, uitgekleed tot alleen `mcp__labx`-tools.
- **MCP**: servers zijn **host** (extern, naast LabX) of **lab** (stdio-proces via
  `docker exec -i` in de lab-container) — beide tegelijk bruikbaar in één chat (eerst
  extern ophalen, dan in de sandbox verwerken).
- **Skills/Tools/Workflows**: skills koppelen tools met een schema-preview + per-tool
  instructie; workflows zijn Markdown-stappen met een visuele stap-editor (geen DAG).
- **Scheduling**: cron-expressies (croniter) draaien een prompt of workflow tegen een lab.
- **Azure-profielen**: meerdere Fernet-versleutelde identiteiten, syncbaar naar de LabX-
  host of naar een lab.

## Lokaal draaien

```bash
cd LabX
cp .env.example .env   # vul LABX_ADMIN_PASSWORD, LABX_JWT_SECRET, LABX_FERNET_KEY in
docker compose up --build
```

- API: http://localhost:8090 (docs op `/docs`)
- Web: http://localhost:8080
- Inloggen met `LABX_ADMIN_USERNAME` / `LABX_ADMIN_PASSWORD`.

Zonder een `CLAUDE_CODE_OAUTH_TOKEN` (of later via Instellingen ingesteld) kan de chat
geen Claude Code CLI starten — labs aanmaken/beheren werkt al wel.

Optioneel lokaal guard-model (Ollama) als sidecar meestarten:

```bash
docker compose --profile with-ollama up --build
```

## Draaien als desktop-app (Windows/macOS, via Tauri)

`desktop/` is een dunne Tauri-shell rond precies dezelfde `docker-compose.yml` hierboven —
géén andere backend/frontend, alleen een ander opstartpad: bij eerste start genereert de
shell zelf een `.env` (admin-wachtwoord, JWT/Fernet-secrets, intern token) in de
OS-app-datamap, draait `docker compose up -d --build`, wacht op `/api/system/health`, en
toont de bestaande web-UI dan in een native venster. Sluiten minimaliseert naar het
systeemvak (een lab kan nog bezig zijn) — écht afsluiten via het vak-menu draait ook
`docker compose down`. **Vereist Docker Desktop** — de eerste-keer-wizard in de app zelf
controleert dit en laat je ook meteen je `claude setup-token` plakken.

```bash
cd LabX/desktop
npm install
npm run tauri dev     # macOS/Linux/Windows dev-run tegen de lokale checkout
npm run tauri build   # installer (.app/.dmg, .msi/.exe, .deb/.AppImage) voor dit platform
```

Een Windows-installer bouwen/testen kan alleen op Windows zelf of via een
`windows-latest` CI-runner — dit is niet cross-compileerbaar vanaf macOS/Linux.

## Draaien op de eigen server

1. Kopieer de repo naar de server, of clone 'm daar.
2. `cp .env.example .env` en vul in (gebruik een sterk wachtwoord/secret op een
   internetbereikbare server).
3. `docker compose up -d --build`.
4. Zet een reverse proxy (nginx-proxy, Traefik, …) voor `:8080`/`:8090` als de server ook
   andere sites host — zie de `flux`-aanpak die Nectar/ND3X al gebruiken voor het patroon.

### Verharden: docker-socket-proxy

De directe `docker.sock`-mount geeft de api-container praktisch root op de host. Voor een
server met meerdere projecten is een socket-proxy (bv. `tecnativa/docker-socket-proxy`)
tussen de api-container en de socket de eenvoudigste verbetering: alleen de proxy mount de
echte socket, en `LABX_DOCKER_HOST=tcp://socket-proxy:2375` in `.env` is de enige wijziging
— de runtime-laag (`services/lab/docker_runtime.py`) leest `DOCKER_HOST` al uit settings.
Test `docker exec -it` vroeg tegen de proxy: dat is het commando dat een te beperkte proxy-
policy het eerst breekt (hijacked streams moeten worden doorgelaten).

## Testen

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
LABX_HOME=/tmp/labx LABX_ADMIN_PASSWORD=test LABX_JWT_SECRET=test-secret-min-32-chars \
LABX_FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
python3 -m uvicorn server:app --app-dir src --port 8090
```

```bash
cd frontend
npm install
npm run build   # tsc -b && vite build
npm run dev     # proxied naar localhost:8090, zie vite.config.ts
```

`scripts/smoke.sh` loopt het hoofdpad af tegen een draaiende `docker compose up`-stack:
inloggen, Docker-diagnose, lab aanmaken, exec, opruimen.

## Wat bewust dun blijft

Geen multi-tenant/rollen, geen publish-sidecar-hardening, geen uitgebreide testsuite,
geen provider-registry (één CLI-runtime), geen Docker-Hub-image-cache, geen mobiele
variant. Zie de sessie die dit gebouwd heeft voor het volledige fase-plan en de bewuste
vereenvoudigingen t.o.v. ND3X.
