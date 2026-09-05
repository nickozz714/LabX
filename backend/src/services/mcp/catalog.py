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
                       "Chromium' aan (Instellingen > Lab-extra's) — zonder browser start de server "
                       "niet.",
        "kind": "stdio", "runner": "npx",
        "package": "@playwright/mcp@latest",
        # Expliciet commando i.p.v. `npx -y @playwright/mcp@latest`: de extra
        # zet het pakket globaal in het lab neer, en `npx ... @latest` zou bij
        # ELKE aanroep opnieuw het netwerk op om te kijken of er iets nieuwers
        # is — merkbaar traag voor een tool die de agent tientallen keren
        # aanroept.
        "stdio_command": "mcp-server-playwright --headless --browser chromium",
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
