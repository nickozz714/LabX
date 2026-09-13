"""
services/mcp/catalog.py

A small, curated "standard library" of well-known MCP servers to install
with one click — the fix for "ik wil al een standaard library... aangesloten
voor MCP Servers en Tools". This is a built-in list shipped with LabX, NOT a
live external marketplace: wiring up a trustworthy third-party registry API
is a bigger, separate trust decision (which registry, whose auth, what
happens when it's unreachable) that deserves its own call, not a silent
side-effect of this pass.

Every entry's `package`/`base_url` is copied verbatim from the server's own
official README (github.com/modelcontextprotocol/servers or
github.com/microsoft/mcp — checked live, not from training-data memory,
since a wrong package name here would silently produce a broken "Installing"
button). `kind` decides how `install_from_catalog` builds the MCPServer row:
- "stdio": `npx -y <package> <suggested_args>` (Node — already in the image)
  or `uvx <package> <suggested_args>` (needs the `uv` package, added to the
  backend Dockerfile alongside Node/Claude Code).
- "http": `base_url` directly, no local process.

`needs_arg`/`needs_auth` are UI hints, not enforcement: some servers need a
value only the user has (an Azure DevOps org name, a GitHub PAT) — those
install with a placeholder/no-auth and the user finishes setup via "Auth
instellen" or (for stdio args) the connection-edit action.
"""
from __future__ import annotations

from typing import Any, Dict, List

CATALOG: List[Dict[str, Any]] = [
    # ── Official MCP reference servers (modelcontextprotocol/servers) ───────
    {
        "key": "filesystem",
        "name": "Filesystem",
        "description": "Lees/schrijf bestanden binnen een toegestane map. In een lab: geeft de agent "
                       "een tweede, MCP-genormeerd pad naast lab__shell_exec voor bestandswerk.",
        "kind": "stdio", "runner": "npx",
        "package": "@modelcontextprotocol/server-filesystem",
        "suggested_location": "lab",
        "suggested_args": "/workspace",
        "provenance": "data",
    },
    {
        "key": "memory",
        "name": "Memory (knowledge graph)",
        "description": "Een simpel, lokaal kennisgraaf-geheugen (entiteiten + relaties) dat de agent "
                       "tussen turns kan bijhouden.",
        "kind": "stdio", "runner": "npx",
        "package": "@modelcontextprotocol/server-memory",
        "suggested_location": "host",
        "suggested_args": "",
        "provenance": "control",
    },
    {
        "key": "sequential-thinking",
        "name": "Sequential Thinking",
        "description": "Structureert meerstaps-redeneren van de agent in expliciete, herzienbare "
                       "denkstappen — handig bij complexe taken.",
        "kind": "stdio", "runner": "npx",
        "package": "@modelcontextprotocol/server-sequential-thinking",
        "suggested_location": "host",
        "suggested_args": "",
        "provenance": "control",
    },
    {
        "key": "everything",
        "name": "Everything (referentie/test-server)",
        "description": "Officiële MCP-referentieserver met voorbeelden van elk protocol-onderdeel "
                       "(tools, resources, prompts) — handig om de MCP-koppeling zelf te testen.",
        "kind": "stdio", "runner": "npx",
        "package": "@modelcontextprotocol/server-everything",
        "suggested_location": "host",
        "suggested_args": "",
        "provenance": "control",
    },

    # ── Official Microsoft MCP servers (github.com/microsoft/mcp) ───────────
    {
        "key": "ms-playwright",
        "name": "Playwright (Microsoft)",
        "description": "Officiële Microsoft-browserautomatisering: laat de agent webpagina's bedienen "
                       "via accessibility-snapshots (geen screenshots nodig).",
        "kind": "stdio", "runner": "npx",
        "package": "@playwright/mcp@latest",
        "suggested_location": "host",
        "suggested_args": "",
        "provenance": "control",
    },
    {
        "key": "ms-playwright-lab",
        "name": "Playwright (in het lab)",
        "description": "Dezelfde Playwright-server, maar als proces IN de labcontainer: de browser "
                       "draait dan in de sandbox en achter de egress-guard, niet op de host. "
                       "Vink daarvoor bij het lab de extra's 'Node.js' en 'Playwright (Node) + "
                       "Chromium' aan — of eenvoudiger: vink het pakket 'Playwright MCP-server' "
                       "aan, dan registreert LabX deze server zelf.",
        "kind": "stdio", "runner": "npx",
        "package": "@playwright/mcp@latest",
        # Expliciet commando i.p.v. `npx -y @playwright/mcp@latest`: de extra
        # zet het pakket globaal in het lab neer, en `npx ... @latest` zou bij
        # ELKE aanroep opnieuw het netwerk op om te kijken of er iets nieuwers
        # is — merkbaar traag voor een tool die de agent tientallen keren
        # aanroept.
        "stdio_command": "playwright-mcp --headless --browser chromium",
        "suggested_location": "lab",
        "suggested_args": "",
        "provenance": "data",
    },
    {
        "key": "ms-azure",
        "name": "Azure MCP Server (Microsoft)",
        "description": "Alle Azure-tools (40+ diensten) in één server. Gebruikt de LOKALE `az`-sessie "
                       "van de container voor auth (geen los token hier instellen) — sync eerst een "
                       "Azure-profiel naar de host via de Azure-profielen-pagina, anders faalt elke call.",
        "kind": "stdio", "runner": "npx",
        "package": "@azure/mcp@latest",
        "suggested_args": "server start",
        "suggested_location": "host",
        "provenance": "control",
    },
    {
        "key": "ms-fabric",
        "name": "Microsoft Fabric MCP (Microsoft, preview)",
        "description": "Toegang tot Fabric's publieke API's, item-definities en best practices voor "
                       "AI-ondersteunde ontwikkeling. Public preview. Gebruikt lokale Azure-auth net als "
                       "de Azure MCP Server hierboven.",
        "kind": "stdio", "runner": "npx",
        "package": "@microsoft/fabric-mcp@latest",
        "suggested_args": "server start --mode all",
        "suggested_location": "host",
        "provenance": "control",
    },
    {
        "key": "ms-fabric-rti",
        "name": "Fabric Real-Time Intelligence (Microsoft)",
        "description": "Bevraag en analyseer Fabric RTI (Real-Time Intelligence / Eventhouse-KQL) data "
                       "vanuit de agent.",
        "kind": "stdio", "runner": "uvx",
        "package": "microsoft-fabric-rti-mcp",
        "suggested_args": "",
        "suggested_location": "host",
        "provenance": "control",
    },
    {
        "key": "ms-devbox",
        "name": "Microsoft Dev Box (Microsoft)",
        "description": "Beheer Dev Boxes, omgevingen en pools via natuurlijke taal.",
        "kind": "stdio", "runner": "npx",
        "package": "@microsoft/devbox-mcp@latest",
        "suggested_args": "",
        "suggested_location": "host",
        "provenance": "control",
    },
    {
        "key": "ms-azure-devops",
        "name": "Azure DevOps (Microsoft)",
        "description": "Werk vanuit de agent met Azure DevOps (werkitems, repos, pipelines). Vereist je "
                       "organisatienaam als argument — installeer en pas daarna het commando aan via "
                       "'Verbinding bewerken' (voeg je org-naam toe achter het package).",
        "kind": "stdio", "runner": "npx",
        "package": "@azure-devops/mcp",
        "suggested_args": "<jouw-ado-organisatie>",
        "suggested_location": "host",
        "provenance": "control",
    },
    {
        "key": "markitdown",
        "name": "Markitdown (Microsoft)",
        "description": "Zet documenten (PDF/Office/HTML/…) om naar Markdown voor de agent.",
        "kind": "stdio", "runner": "uvx",
        "package": "markitdown-mcp",
        "suggested_args": "",
        "suggested_location": "host",
        "provenance": "control",
    },
    {
        "key": "ms-learn",
        "name": "Microsoft Learn (Microsoft)",
        "description": "Live toegang tot de officiële Microsoft-documentatie. Publieke dienst, geen "
                       "authenticatie nodig.",
        "kind": "http",
        "base_url": "https://learn.microsoft.com/api/mcp",
        "suggested_location": "host",
        "provenance": "control",
    },
    {
        "key": "work-iq",
        "name": "Microsoft Work IQ (Teams, Outlook, agenda, bestanden)",
        "description": (
            "Microsofts eigen intelligentielaag over Microsoft 365, als remote MCP-server. "
            "Tien generieke tools werken op resource-paden: `fetch /me/messages` leest je mail, "
            "`do_action /me/sendMail` verstuurt, `fetch /me/chats/{id}/messages` leest een "
            "Teams-gesprek, `create_entity /me/events` zet een afspraak in de agenda. "
            "Alles gebeurt NAMENS JOU: de agent ziet precies wat jij ziet, niet meer — Work IQ "
            "kent geen application-permissies. Vereist daarom een eigen Entra-app-registratie "
            "met een device-code-inlog (profieltype 'Entra-app'), geen az-CLI-profiel: "
            "Microsoft weigert de Azure CLI voor deze API. Let op: dit haalt échte mail- en "
            "chatinhoud naar het model toe."),
        "kind": "http",
        "base_url": "https://workiq.svc.cloud.microsoft/mcp",
        "suggested_location": "host",
        # Een token voor Azure Resource Manager wordt hier geweigerd: de
        # audience klopt niet. Work IQ heeft zijn eigen application ID URI.
        "token_scope": "api://workiq.svc.cloud.microsoft/.default",
        "needs_auth": True,
        # `control` zou hier verkeerd zijn: dit levert geen metadata maar de
        # inhoud van gesprekken en mail.
        "provenance": "data",
        "setup": {
            "titel": "Wat er eenmalig in Entra en het M365-beheercentrum moet gebeuren",
            "stappen": [
                "Microsoft Entra → App-registraties → Nieuwe registratie. Naam naar keuze, "
                "accounttype 'Alleen accounts in deze organisatiemap'. Een redirect-URI is niet "
                "nodig.",
                "Bij Verificatie: zet 'Openbare clientstromen toestaan' op JA. Zonder dat werkt "
                "de device-code-login niet — dat is de enige instelling die mensen hier vergeten.",
                "Bij API-machtigingen → Machtiging toevoegen → API's die mijn organisatie "
                "gebruikt → zoek 'Work IQ' → Gedelegeerde machtigingen → vink "
                "WorkIQAgent.Ask aan (en de overige Work IQ-machtigingen die je wilt gebruiken).",
                "Klik 'Beheerderstoestemming verlenen'. Dit is verplicht: WorkIQAgent.Ask "
                "vereist admin consent. Ben je zelf geen beheerder, dan is dit het moment om het "
                "aan te vragen — de rest kun je zonder beheerder doen.",
                "Zet Work IQ aan voor de tenant in het Microsoft 365-beheercentrum, en zet daar "
                "SCHRIJFACTIES aan als de agent ook mail mag versturen of afspraken mag maken. "
                "Work IQ staat standaard op alleen-lezen.",
                "Maak een spending policy voor Work IQ. Het rekent af in Copilot Credits, los van "
                "je Microsoft 365 Copilot-licenties — zonder policy kan het gebruik ongemerkt "
                "oplopen.",
                "Kopieer uit Entra de Toepassings-id (client) en de Map-id (tenant) en maak er in "
                "LabX een Azure-profiel van het type 'Entra-app' mee. Klik daarna op 'Inloggen' "
                "en voer de code in.",
            ],
        },
    },
    {
        "key": "github",
        "name": "GitHub",
        "description": "Repositories, issues en pull requests via de officiële GitHub Copilot MCP-"
                       "endpoint. Vereist een GitHub Personal Access Token — stel dit na installatie in "
                       "via 'Auth instellen' (Bearer-token).",
        "kind": "http",
        "base_url": "https://api.githubcopilot.com/mcp/",
        "suggested_location": "host",
        "provenance": "control",
    },
]


def find(key: str) -> Dict[str, Any] | None:
    return next((c for c in CATALOG if c["key"] == key), None)
