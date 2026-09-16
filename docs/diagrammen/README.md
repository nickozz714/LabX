# Platen

Zes diagrammen die LabX uitleggen, in de volgorde waarin je het aan een zaal vertelt.
Allemaal **1600 × 900** (16:9 — precies een slide) en **SVG**, dus scherp op elk formaat.

| # | Bestand | Waarvoor |
| --- | --- | --- |
| 1 | `1-wat-is-labx.svg` | De kern: een agent met een eigen, afgeschermde werkplaats |
| 2 | `2-de-agent-lus.svg` | Wat er gebeurt tussen opdracht en verslag |
| 3 | `3-wat-het-model-weet.svg` | De vier bronnen, wat er dichtstaat, en de Nectar-cyclus |
| 4 | `4-van-ticket-tot-resultaat.svg` | Boards, planningen en werkers; sync met Jira en Azure DevOps |
| 5 | `5-de-data-guard.svg` | Invoer- én uitvoercontrole, wat je zelf instelt, en de audit |
| 6 | `6-labx-en-nectar.svg` | De combinatie: LabX doet het werk, Nectar onthoudt het |
| 7 | `7-waar-het-draait.svg` | Welke containers, welke poorten, welke volumes — en waar de CLI draait |

Plaat 3 is de plaat voor de vraag die altijd komt: *wat weet dat model eigenlijk, en waar
haalt het dat vandaan?* Hij is niet naar smaak getekend maar afgelezen uit
`backend/src/services/agent/claude_cli_provider.py` — daar staat welke eigen tools van de CLI
dichtgezet worden (`Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `WebFetch`, …), dat
`--strict-mcp-config` voorkomt dat er een MCP-server van de beheerder meelift, en welke
omgevingsvariabelen worden gestript. Verandert die lijst, dan hoort de plaat mee te
veranderen.

## Beeldtaal

Dezelfde kleur betekent op elke plaat hetzelfde, zodat ze door elkaar te gebruiken zijn.
De kleuren komen uit de producten zelf: het blauw uit `frontend/src/index.css` van LabX,
het honinggeel uit de mind-interface van Nectar.

| Kleur | Betekenis |
| --- | --- |
| `#3B82F6` blauw | LabX en alles wat eraan hangt |
| `#E08C1E` honing | Nectar, het gedeelde geheugen |
| `#0B1220` donker | de grens: guard, poorten, beslissingen |
| streepjeslijn | de sandbox — binnen is het lab, buiten niet |
| `#0E9F6E` / `#D97706` / `#DC2626` | doorgelaten · gemaskeerd of wachtend · geblokkeerd |
| `#8B5CF6` violet | wat terugkomt naar jou |

## Naar PowerPoint

```bash
python3 maak-pptx.py --titel "LabX" \
    --ondertitel "Een veilige AI-werkplaats — zeven platen, van de kern tot waar het draait" \
    --accent 3B82F6 --uit LabX-platen.pptx
```

Levert een 16:9-deck op met een titeldia, één dia per plaat en **sprekersnotities**
(waarvoor die plaat is, en waar je bij het vertellen op moet letten). In elke dia zit de
SVG én een bitmap-voorvertoning: PowerPoint 2019+ toont de vector — scherp op elk formaat en
met rechtermuis → *Converteren naar vorm* tot losse vormen te maken — en oudere versies
vallen terug op de bitmap. Zo gaat het deck overal open.

Het renderen gebeurt met Chrome in headless-modus en niet met ImageMagick: die laatste
rendert SVG-tekst met een eigen, beperkte engine en maakt er een potje van zodra er een
lettertype-fallback aan te pas komt.

De `.pptx` staat bewust **niet** in git — het is een gegenereerd bestand dat bij elke
regeneratie in zijn geheel verandert, en de platen ernaast zijn de bron. Het bouwen kost een
paar seconden.

## In een presentatie

- **PowerPoint / Keynote**: Invoegen → Afbeeldingen → het `.svg`-bestand.
- **Losse onderdelen aanpassen**: in PowerPoint rechtermuis → *Converteren naar vorm*;
  daarna is elk kader en elk woord los te bewerken.
- **Google Slides** lust geen SVG — maak er eerst een PNG van.

## Lettertype

De platen vragen om Archivo en vallen terug op Segoe UI / Helvetica Neue / Arial. Ze zijn
bewust niet afhankelijk van een webfont: een plaat die in een presentatie belandt moet het
ook doen op een machine die dat lettertype niet heeft.

## Bijhouden

Wat er op de platen staat is afgelezen uit de code, niet bedacht: welke tools dichtstaan
(`claude_cli_provider.py`), waar de guard zit (`tool_execution_service.py`), welke acties en
doelen een regel kan hebben (`models/guard_rule.py`) en welke poorten open staan. Verandert
daar iets, dan hoort de bijbehorende plaat mee te veranderen — het zijn tekstbestanden, dus
een aanpassing is een gewone diff.
