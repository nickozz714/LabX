# Hostlab — LabX op deze machine, binnen één map

Een gewoon lab is een container. Dat werkt bijna altijd beter: afgeschermd,
wegwerpbaar, met een Azure-profiel dat je er expliciet in zet.

Behalve als een klant eist dat je aanmeldt vanaf een **apparaat dat zij kennen**
(`AADSTS530003: Your device is required to be managed`). Een container is geen
apparaat; die claim kan hij niet afgeven en hoort hij ook niet te kunnen
afgeven. Dan is dit de uitweg: de agent voert zijn commando's uit op jouw
machine, als jou, met de `az`-sessie die jij in een browser hebt opgezet.

Er wordt niets gekopieerd en niets voorgewend. Precies zoals je het zelf zou
typen.

## Starten

```bash
# Eén keer: een token maken en bewaren
export LABX_HOST_AGENT_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
echo "$LABX_HOST_AGENT_TOKEN"        # zet deze ook in LabX bij het lab

# Elke keer:
python3 host-agent/labx_host_agent.py
```

Zonder token start hij niet. Een onbeveiligde uitvoerdienst op je eigen machine
is precies het soort gemak waar je spijt van krijgt.

De agent luistert alleen op `127.0.0.1:8099`. LabX bereikt hem vanuit zijn
container via `host.docker.internal`, dat docker-compose al mapt.

## Een hostlab maken

In LabX: **Labs → Nieuw lab → "Op deze machine, in één projectmap"**. Je vult
twee dingen in:

| | |
|---|---|
| **Projectmap** | bijvoorbeeld `~/klanten/klant-a`. Moet in je thuismap liggen. |
| **Token** | hetzelfde als `LABX_HOST_AGENT_TOKEN` hierboven. |

Daarna in die map één keer aanmelden:

```bash
cd ~/klanten/klant-a
AZURE_CONFIG_DIR="$PWD/.azure" az login --tenant <tenant-id-van-de-klant>
```

## Wat de grenzen zijn

**De map.** Elk commando draait onder `sandbox-exec` met een profiel dat je hele
thuismap afsluit en alleen de projectmap opent. Dat is een grens in de kernel,
geen filter op commandotekst. Nagemeten in `backend/tests/test_hostlab_zandbak.py`:

- lezen en schrijven buiten de map: geweigerd
- een **symlink** vanuit de map naar buiten: geweigerd (de kernel volgt het
  doelpad, niet de link)
- een pad in een variabele of een subshell: geweigerd
- `~/.ssh` en `~/.azure` van de host: geweigerd

**De Azure-sessie.** Elk commando krijgt `AZURE_CONFIG_DIR` naar `<project>/.azure`.
Het lab ziet daardoor alleen de aanmeldingen die jij voor dát project hebt
gedaan — niet die van je andere klanten. Per klant een map, per map een login.

**Geen login-shell.** `sh -lc` wordt `sh -c`. Je profiel ligt in de afgesloten
thuismap, en je wil sowieso niet dat je eigen aliassen en PATH-aanpassingen
meedoen met wat een nachtrun uitvoert.

## Wat de grenzen NIET zijn

Dit moet je weten voordat je er onbeheerd op vertrouwt.

**Binnen die tenant kan de agent alles wat jij kunt.** De zandbak begrenst de
schijf, niet Azure. Draait er 's nachts iets dat een pipeline verwijdert, dan is
dat gebeurd. Wil je dat niet, dan is een service principal met minder rechten de
juiste weg — niet dit.

**De data-guard beschermt de weg naar het model, niet de weg naar buiten.**
Tooluitvoer gaat door de guard voordat het model het ziet. Een `curl` die zelf
iets wegstuurt, ziet de guard niet. Dat is in een lab met netwerktoegang niet
anders.

**Alleen macOS.** `sandbox-exec` bestaat nergens anders. Op Linux of Windows
start de agent niet, in plaats van zonder zandbak te draaien.

## Stoppen

Ctrl-C. Draaiende labs in LabX blijven staan maar kunnen niets meer uitvoeren —
het eerste commando zegt dan dat de agent niet bereikbaar is.
