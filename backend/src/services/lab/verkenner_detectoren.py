"""
services/lab/verkenner_detectoren.py

De detectoren van de verkenner: harde vondsten met een vindplaats.

Het model is leidend in de verkenning — het leest de repo zoals een mens dat
zou doen. Deze module is de controle eronder, en heeft twee taken die je niet
moet verwarren:

1. **Zelf vinden.** Alles wat uit opzoekwerk volgt vindt een detector sneller,
   goedkoper en betrouwbaarder dan een model: de diensten in een
   compose-bestand, de pakketbeheerder in `package.json`, de sleutels in een
   `.env.example`. Die vondsten gaan het plan in met hoge of middelzekerheid.

2. **Staven.** Wat het model voorstelt gaat langs `staaf()`. Is er bewijs voor
   in de repo, dan wordt de vondst bevestigd. Is dat er niet, dan blijft hij
   in het plan staan — met vindplaats, met lage zekerheid — maar hij staat in
   het goedkeurscherm UIT. Dat is het verschil tussen "model leidend" en
   "model beslist", en het is de reden dat er nooit een container draait waar
   niemand op geklikt heeft.

**Geen vondst zonder vindplaats.** Elke `Vondst` draagt het bestand en de
regel waar hij vandaan komt. Zonder die regel is een plan een mening, en een
mening kun je niet nakijken.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from component_logging import get_logger

from services.lab import verkenner_catalogus as cat

log = get_logger(__name__)

#: Wat een `RepoBeeld` is: pad (relatief, met /) -> inhoud als tekst. De
#: verkenningscontainer vult dit; de detectoren raken nooit een schijf aan en
#: zijn daarom los te testen zonder docker, repo of token.
RepoBeeld = Dict[str, str]

ZEKERHEDEN = ("hoog", "midden", "laag")


@dataclass
class Bewijs:
    bestand: str
    regel: Optional[int] = None
    fragment: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"bestand": self.bestand, "regel": self.regel, "fragment": self.fragment}


@dataclass
class Vondst:
    #: dienst | applicatie | extern | geheim | lab_image | startscript | omgeving
    #:
    #: `applicatie` is een VASTSTELLING en geen voorstel: dit wordt uit de
    #: repo gebouwd, dus dit is wat een branch-lab draait. Er valt niets aan
    #: te kiezen, en daarom staat er ook geen vinkje bij.
    soort: str
    sleutel: str
    waarde: Dict[str, Any] = field(default_factory=dict)
    bewijs: List[Bewijs] = field(default_factory=list)
    zekerheid: str = "midden"
    #: detector:<naam> of model
    bron: str = "detector"
    #: Door een detector gestaafd? Onbevestigd betekent: wel tonen, niet
    #: aanvinken.
    bevestigd: bool = True
    toelichting: str = ""
    #: Wat JIJ hierover gezegd hebt bij een eerdere ronde. Blijft staan bij
    #: het onderdeel, zodat je terugziet waarom het er zo bij staat.
    opmerking: str = ""
    #: Door jou uitgevinkt bij een eerdere ronde. De vondst blijft bestaan --
    #: het bewijs is nog net zo waar -- maar hij staat niet meer vooraf aan.
    #: Dit is het verschil tussen "ik heb het niet gevonden" en "jij wilde het
    #: niet".
    door_gebruiker_uit: bool = False
    #: Op grond van jouw OPDRACHT weggelaten. Een detector vindt wat er in de
    #: repo staat en weet niet wat je ermee wilt: zeg je "de agent hoort er
    #: niet bij", dan blijft hij `postgres` gewoon vinden. De vondst blijft
    #: staan -- het bewijs is waar -- maar hij gaat niet vanzelf mee.
    uitgesloten: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "soort": self.soort,
            "sleutel": self.sleutel,
            "waarde": self.waarde,
            "bewijs": [b.to_dict() for b in self.bewijs],
            "zekerheid": self.zekerheid,
            "bron": self.bron,
            "bevestigd": self.bevestigd,
            "toelichting": self.toelichting,
            "opmerking": self.opmerking,
            "door_gebruiker_uit": self.door_gebruiker_uit,
            "uitgesloten": self.uitgesloten,
        }


# ── gereedschap ──────────────────────────────────────────────────────────────

def _regel_van(tekst: str, patroon: str) -> Optional[int]:
    """Op welke regel staat dit patroon? 1-geïndexeerd, None als het er niet staat."""
    for nr, regel in enumerate(tekst.splitlines(), start=1):
        if re.search(patroon, regel):
            return nr
    return None


def _fragment(tekst: str, regel: Optional[int]) -> str:
    if not regel:
        return ""
    regels = tekst.splitlines()
    if 1 <= regel <= len(regels):
        return regels[regel - 1].strip()[:200]
    return ""


def _bewijs_op(beeld: RepoBeeld, bestand: str, patroon: str) -> Bewijs:
    tekst = beeld.get(bestand, "")
    nr = _regel_van(tekst, patroon)
    return Bewijs(bestand=bestand, regel=nr, fragment=_fragment(tekst, nr))


def _paden(beeld: RepoBeeld, patroon: str) -> List[str]:
    rx = re.compile(patroon)
    return sorted(p for p in beeld if rx.search(p))


# ── trede 1: compose en devcontainer ─────────────────────────────────────────

COMPOSE_PATROON = r"(^|/)(docker-)?compose(\.[\w-]+)?\.ya?ml$"


def detecteer_compose(beeld: RepoBeeld) -> List[Vondst]:
    """Diensten uit een compose-bestand. De sterkste bron die er is.

    **De regel die de app van zijn afhankelijkheden scheidt:** een service met
    `build:` wordt uit déze repo gebouwd en is dus de applicatie zelf — die
    hoort in een branch-lab, niet in de vaste laag. Een service met alleen
    `image:` is iets wat de applicatie nodig heeft en wat per branch hetzelfde
    blijft. Dat is precies de scheidslijn tussen een branch-lab en een vaste
    dienst, en het staat al in het bestand; niemand heeft het alleen ooit zo
    genoemd.
    """
    try:
        import yaml  # noqa: PLC0415 — alleen nodig als er een compose is
    except ImportError:  # pragma: no cover
        log.warning("PyYAML ontbreekt; compose-detectie overgeslagen")
        return []

    # Over de bestanden heen samenvoegen op NAAM. Een repo heeft vaak meer
    # dan een compose: een voor ontwikkelen, een voor uitrollen. Daarin staat
    # dezelfde dienst twee keer met een andere rol -- in LabX zelf heeft `api`
    # in de wortel een `build:` en in `deploy/` een `image:`. Los geteld
    # levert dat een applicatie die OOK een vaste dienst is, en dan draait
    # elke branch tegen de code van wie het cluster aanmaakte.
    #
    # `build:` wint, waar het ook staat: bouwt de repo deze dienst uit zijn
    # eigen bron, dan is het de applicatie en hoort hij in een branch-lab.
    ondiep_eerst = sorted(_paden(beeld, COMPOSE_PATROON), key=lambda p: (p.count("/"), p))
    gebouwd: Dict[str, Vondst] = {}
    diensten: Dict[str, Vondst] = {}

    for pad in ondiep_eerst:
        tekst = beeld[pad]
        try:
            doc = yaml.safe_load(tekst) or {}
        except Exception as exc:  # noqa: BLE001 — een kapotte compose is geen crash
            log.info("compose niet te lezen: %s (%s)", pad, exc)
            continue
        if not isinstance(doc, dict):
            continue
        spec_per_naam = doc.get("services")
        if not isinstance(spec_per_naam, dict):
            continue

        for naam, spec in spec_per_naam.items():
            if not isinstance(spec, dict):
                continue
            naam = str(naam)
            bewijs = _bewijs_op(beeld, pad, rf"^\s*{re.escape(naam)}\s*:")

            if spec.get("build") is not None:
                vondst = gebouwd.get(naam)
                if vondst is None:
                    gebouwd[naam] = Vondst(
                        soort="applicatie",
                        sleutel=naam,
                        waarde={"build": spec.get("build"), "image": spec.get("image")},
                        bewijs=[bewijs],
                        zekerheid="hoog",
                        bron="detector:compose",
                        toelichting=(
                            f"'{naam}' wordt uit deze repo gebouwd. Dit is dus wat een "
                            "branch-lab gaat draaien, en niet iets voor de vaste laag."
                        ),
                    )
                else:
                    vondst.bewijs.append(bewijs)
                continue

            vondst = diensten.get(naam)
            if vondst is None:
                diensten[naam] = Vondst(
                    soort="dienst",
                    sleutel=naam,
                    waarde={
                        "image": str(spec.get("image") or ""),
                        "poorten": _compose_poorten(spec),
                        "omgeving": _compose_omgeving(spec),
                    },
                    bewijs=[bewijs],
                    zekerheid="hoog",
                    bron="detector:compose",
                    toelichting=f"Vaste dienst uit {pad}.",
                )
            else:
                vondst.bewijs.append(bewijs)

    for naam in gebouwd:
        weg = diensten.pop(naam, None)
        if weg is not None:
            gebouwd[naam].bewijs.extend(weg.bewijs)
            gebouwd[naam].toelichting += (
                f" Staat elders ook als kant-en-klaar image ({weg.waarde.get('image')}); "
                "dat is de uitgerolde versie, niet wat een branch-lab moet draaien."
            )

    # Een dienst zonder image en zonder build zegt niets bruikbaars.
    return [*gebouwd.values(), *(v for v in diensten.values() if v.waarde.get("image"))]


def _compose_poorten(spec: Dict[str, Any]) -> List[int]:
    """De CONTAINERpoorten uit een compose-service.

    Welke poort dat op de machine wordt bepaalt LabX zelf (vanaf 18000) — de
    linkerkant van `8081:8081` nemen we juist niet over, want dat is hoe de
    emulator bij de eerste proef op een bezette 8081 botste.
    """
    uit: List[int] = []
    for poort in spec.get("ports") or []:
        tekst = str(poort).split("/")[0]
        deel = tekst.split(":")[-1]
        if "-" in deel:  # een reeks: alleen de ondergrens is zinnig
            deel = deel.split("-")[0]
        if deel.isdigit():
            uit.append(int(deel))
    for poort in spec.get("expose") or []:
        tekst = str(poort).split("/")[0]
        if tekst.isdigit() and int(tekst) not in uit:
            uit.append(int(tekst))
    return uit


def _compose_omgeving(spec: Dict[str, Any]) -> Dict[str, str]:
    omg = spec.get("environment")
    if isinstance(omg, dict):
        return {str(k): "" if v is None else str(v) for k, v in omg.items()}
    uit: Dict[str, str] = {}
    if isinstance(omg, list):
        for item in omg:
            sleutel, _, waarde = str(item).partition("=")
            uit[sleutel.strip()] = waarde.strip()
    return uit


# ── trede 3: omgevingsbestanden ──────────────────────────────────────────────

ENV_PATROON = r"(^|/)\.env(\.[\w.-]+)?$|(^|/)env\.(example|sample|template)$"
#: `.env` zonder achtervoegsel kan echte sleutels bevatten. Die nemen we niet
#: over; we gebruiken alleen de NAMEN, nooit de waarden.
ECHTE_ENV = re.compile(r"(^|/)\.env$|(^|/)\.env\.local$")


def lees_env(tekst: str) -> List[tuple[int, str, str]]:
    """(regelnummer, sleutel, waarde) uit een env-bestand."""
    uit: List[tuple[int, str, str]] = []
    for nr, regel in enumerate(tekst.splitlines(), start=1):
        kaal = regel.strip()
        if not kaal or kaal.startswith("#"):
            continue
        if kaal.lower().startswith("export "):
            kaal = kaal[7:].strip()
        sleutel, scheiding, waarde = kaal.partition("=")
        if not scheiding:
            continue
        sleutel = sleutel.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", sleutel):
            continue
        uit.append((nr, sleutel, waarde.strip().strip('"').strip("'")))
    return uit


def detecteer_omgevingsbestanden(beeld: RepoBeeld) -> List[Vondst]:
    """Welke diensten verwacht de applicatie, afgelezen aan haar sleutels?

    Dit is de trede waarop IntelligentHive landt: er is geen compose, maar
    `COSMOS_ENDPOINT` en `COSMOS_KEY` staan in `components/api/.env.example`
    en dat is bewijs genoeg om de emulator voor te stellen.

    Levert ook de andere lijst: sleutels naar diensten die LabX NIET kan
    nabouwen. Dat rijtje is geen restpost — het is precies waar een branch-lab
    veertig minuten later op omvalt als niemand het vooraf opschreef.
    """
    per_dienst: Dict[str, Vondst] = {}
    per_extern: Dict[str, Vondst] = {}
    geheimen: Dict[str, Vondst] = {}

    for pad in _paden(beeld, ENV_PATROON):
        echt = bool(ECHTE_ENV.search(pad))
        for nr, sleutel, waarde in lees_env(beeld[pad]):
            # Uit een ECHT .env-bestand gebruiken we alleen de naam. De waarde
            # kan een geldige sleutel zijn, en die hoort niet in een plan dat
            # straks op een scherm staat.
            zichtbaar = "" if echt else waarde
            bewijs = Bewijs(bestand=pad, regel=nr, fragment=f"{sleutel}=" if echt else _fragment(beeld[pad], nr))

            sjabloon = cat.zoek_dienst(sleutel, zichtbaar)
            if sjabloon:
                vondst = per_dienst.get(sjabloon.naam)
                if vondst is None:
                    vondst = Vondst(
                        soort="dienst",
                        sleutel=sjabloon.naam,
                        waarde={
                            "image": sjabloon.image,
                            "poorten": list(sjabloon.poorten),
                            "omgeving": dict(sjabloon.container_omgeving),
                            "lab_omgeving": cat.lab_omgeving_voor(sjabloon, sjabloon.naam),
                        },
                        zekerheid="midden",
                        bron="detector:omgeving",
                        toelichting=sjabloon.toelichting or
                        "Afgeleid uit omgevingssleutels, niet uit een compose-bestand.",
                    )
                    per_dienst[sjabloon.naam] = vondst
                vondst.bewijs.append(bewijs)
                continue

            extern = cat.zoek_externe_dienst(sleutel)
            if extern:
                vondst = per_extern.get(extern.naam)
                if vondst is None:
                    vondst = Vondst(
                        soort="extern",
                        sleutel=extern.naam,
                        waarde={"sleutels": []},
                        zekerheid="midden",
                        bron="detector:omgeving",
                        toelichting=extern.toelichting or "Hier bestaat geen emulator van; dit moet uit de kluis komen.",
                    )
                    per_extern[extern.naam] = vondst
                vondst.waarde["sleutels"].append(sleutel)
                vondst.bewijs.append(bewijs)
                continue

            if cat.is_geheim(sleutel) and sleutel not in geheimen:
                geheimen[sleutel] = Vondst(
                    soort="geheim",
                    sleutel=sleutel,
                    bewijs=[bewijs],
                    zekerheid="laag",
                    bron="detector:omgeving",
                    toelichting="Ziet eruit als een geheim, maar ik weet niet van welke dienst.",
                )

    return [*per_dienst.values(), *per_extern.values(), *geheimen.values()]


def detecteer_env_profielen(beeld: RepoBeeld) -> List[Vondst]:
    """Meerdere `.env.<profiel>`-bestanden = een keuze die het cluster moet maken.

    IntelligentHive heeft er vier (debug, realestate, insurance, municipality).
    Welke een branch-lab gebruikt hoort een instelling te zijn, niet iets dat
    in een startscript vastgeroest zit.
    """
    profielen: Dict[str, List[str]] = {}
    for pad in _paden(beeld, r"(^|/)\.env\.[\w-]+$"):
        naam = pad.rsplit("/", 1)[-1][len(".env."):]
        if naam in ("example", "sample", "template", "local"):
            continue
        profielen.setdefault(naam, []).append(pad)
    if len(profielen) < 2:
        return []
    return [Vondst(
        soort="omgeving",
        sleutel="env_profiel",
        waarde={"keuzes": sorted(profielen), "standaard": "debug" if "debug" in profielen else sorted(profielen)[0]},
        bewijs=[Bewijs(bestand=p[0]) for p in profielen.values()],
        zekerheid="hoog",
        bron="detector:omgeving",
        toelichting="Er zijn meerdere omgevingsprofielen; een branch-lab moet er één kiezen.",
    )]


# ── trede 4: manifesten ──────────────────────────────────────────────────────

def detecteer_manifesten(beeld: RepoBeeld) -> List[Vondst]:
    """Taal, pakketbeheerder en startcommando's.

    Hier komt het lab-image vandaan. Een repo met `packageManager: yarn@4.9.2`
    krijgt geen kaal python-image — dat is precies de reden dat dit veld
    bestaat.
    """
    vondsten: List[Vondst] = []
    pad = "package.json"
    if pad in beeld:
        try:
            pakket = json.loads(beeld[pad])
        except Exception:  # noqa: BLE001
            pakket = {}
        if isinstance(pakket, dict):
            vondsten.extend(_uit_package_json(beeld, pad, pakket))

    for pad in ("pyproject.toml", "requirements.txt", "setup.py"):
        if pad in beeld:
            vondsten.append(Vondst(
                soort="lab_image",
                sleutel="python",
                waarde={"image": "python:3.12-slim", "installeer": (
                    "pip install -r requirements.txt" if pad == "requirements.txt" else "pip install -e ."
                )},
                bewijs=[Bewijs(bestand=pad)],
                zekerheid="hoog",
                bron="detector:manifest",
                toelichting="Python-project.",
            ))
            break

    if "go.mod" in beeld:
        vondsten.append(Vondst(
            soort="lab_image", sleutel="go",
            waarde={"image": "golang:1.23"},
            bewijs=[_bewijs_op(beeld, "go.mod", r"^go\s")],
            zekerheid="hoog", bron="detector:manifest",
        ))
    return vondsten


def _uit_package_json(beeld: RepoBeeld, pad: str, pakket: Dict[str, Any]) -> List[Vondst]:
    tekst = beeld[pad]
    vondsten: List[Vondst] = []

    beheerder = str(pakket.get("packageManager") or "")
    node = str((pakket.get("engines") or {}).get("node") or "")
    node_major = re.search(r"(\d+)", node)
    image = f"node:{node_major.group(1)}-bookworm" if node_major else "node:22-bookworm"

    if beheerder.startswith("yarn@"):
        versie = beheerder.split("@", 1)[1]
        toelichting = (
            f"Node met yarn {versie} via corepack. Een kaal python-image volstaat hier niet."
        )
        installeer = "corepack enable && yarn install --immutable"
    elif beheerder.startswith("pnpm@"):
        toelichting = f"Node met pnpm via corepack ({beheerder})."
        installeer = "corepack enable && pnpm install --frozen-lockfile"
    elif "bun.lockb" in beeld:
        toelichting = "Node-project met bun."
        installeer = "bun install"
    else:
        toelichting = "Node-project met npm."
        installeer = "npm ci" if "package-lock.json" in beeld else "npm install"

    vondsten.append(Vondst(
        soort="lab_image", sleutel="node",
        waarde={"image": image, "pakketbeheerder": beheerder or "npm", "installeer": installeer},
        bewijs=[_bewijs_op(beeld, pad, r'"(packageManager|engines)"')],
        zekerheid="hoog", bron="detector:manifest", toelichting=toelichting,
    ))

    scripts = pakket.get("scripts")
    if isinstance(scripts, dict) and scripts:
        bouw = [n for n in scripts if re.match(r"^build(:|$)", n)]
        start = [n for n in scripts if re.match(r"^(start|dev|serve)(:|$)", n)]
        if bouw or start:
            vondsten.append(Vondst(
                soort="startscript", sleutel="scripts",
                waarde={
                    "bouwen": bouw,
                    "starten": start,
                    "werkruimtes": bool(pakket.get("workspaces")),
                },
                bewijs=[_bewijs_op(beeld, pad, r'"scripts"')],
                zekerheid="hoog", bron="detector:manifest",
                toelichting=(
                    "De bouw- en startcommando's die de repo zelf aanbiedt. De volgorde "
                    "waarin ze moeten (eerst afhankelijkheden, dan apps) staat hier niet in "
                    "— dat is waar het model naar kijkt."
                ),
            ))
    return vondsten


def detecteer_dockerfile(beeld: RepoBeeld) -> List[Vondst]:
    vondsten: List[Vondst] = []
    for pad in _paden(beeld, r"(^|/)Dockerfile(\.[\w-]+)?$")[:4]:
        tekst = beeld[pad]
        treffer = re.search(r"^\s*FROM\s+(\S+)", tekst, re.MULTILINE | re.IGNORECASE)
        if not treffer:
            continue
        vondsten.append(Vondst(
            soort="lab_image", sleutel=pad,
            waarde={"image": treffer.group(1), "dockerfile": pad},
            bewijs=[_bewijs_op(beeld, pad, r"^\s*FROM\s")],
            zekerheid="hoog", bron="detector:dockerfile",
            toelichting="Basis-image uit een Dockerfile in de repo.",
        ))
    return vondsten


def detecteer_poorten(beeld: RepoBeeld) -> List[Vondst]:
    """Waarop luistert de applicatie?

    LabX publiceerde standaard 3000 en 7071. Luistert de applicatie op 8000,
    dan gebeurt er niets -- geen foutmelding, geen aanwijzing, een lab dat
    draait en onbereikbaar is. Dat is geen vraag voor de gebruiker: het staat
    in de repo.

    `EXPOSE` is het sterkste bewijs dat er is. Het is een standaarddirectief,
    het staat er met een regelnummer, en wie het opschrijft bedoelt precies
    dit. Een compose-service die uit deze repo gebouwd wordt telt net zo goed.

    De WORTEL wint van een onderliggende map: een `frontend/Dockerfile` met
    `EXPOSE 80` is de losse webserver van de SPA, en in een branch-lab wordt
    die SPA meestal door de applicatie zelf geserveerd.
    """
    kandidaten: List[tuple[int, str, Bewijs]] = []

    for pad in sorted(_paden(beeld, r"(^|/)Dockerfile(\.[\w-]+)?$"),
                      key=lambda p: (p.count("/"), p)):
        tekst = beeld[pad]
        for nr, regel in enumerate(tekst.splitlines(), start=1):
            treffer = re.match(r"^\s*EXPOSE\s+(.+)$", regel, re.IGNORECASE)
            if not treffer:
                continue
            for stuk in treffer.group(1).split():
                nummer = stuk.split("/")[0]
                if nummer.isdigit():
                    kandidaten.append((int(nummer), pad,
                                       Bewijs(bestand=pad, regel=nr,
                                              fragment=regel.strip()[:200])))

    for vondst in detecteer_compose(beeld):
        if vondst.soort != "applicatie":
            continue
        for poort in vondst.waarde.get("poorten") or []:
            kandidaten.append((int(poort), vondst.bewijs[0].bestand, vondst.bewijs[0]))

    if not kandidaten:
        return []

    poorten: List[int] = []
    bewijzen: List[Bewijs] = []
    for nummer, _pad, bewijs in kandidaten:
        if nummer not in poorten:
            poorten.append(nummer)
            bewijzen.append(bewijs)

    return [Vondst(
        soort="lab_poorten", sleutel="poorten",
        waarde={"poorten": poorten[:6]},
        bewijs=bewijzen[:6],
        zekerheid="hoog", bron="detector:dockerfile",
        toelichting=("Hierop luistert de applicatie. LabX publiceert deze poorten "
                     "naar buiten; staat hier het verkeerde nummer, dan is het lab "
                     "onbereikbaar terwijl het wel draait."),
    )]


#: Hoe je ziet dat een repo de Azure CLI nodig heeft. Een aanroep van `az`
#: aan het begin van een regel of na een pipe, of een installatie ervan.
_AZ_GEBRUIK = re.compile(
    r"(^|[\n;|&]\s*)az\s+(login|account|keyvault|storage|ad|group|webapp|functionapp|rest)\b"
    r"|azure-cli|InstallAzureCLIDeb",
    re.MULTILINE)


def detecteer_azure_cli(beeld: RepoBeeld) -> List[Vondst]:
    """Heeft deze applicatie de Azure CLI nodig in het lab?

    Dit bestaat omdat een branch-lab hem standaard NIET krijgt: deze
    installatie zet hem in elk gewoon lab, en in een node-monorepo kost dat
    616 MB voor gereedschap dat nooit wordt aangeroepen.

    Maar soms wordt hij wél aangeroepen. Bij IntelligentHive draait het
    startscript `.devcontainer/ensure-access.sh`, en dat strandde op
    `az: command not found` -- in een lus die zichzelf eindeloop opnieuw
    probeerde, dus het lab leek te leven terwijl er niets gebeurde. Dat
    hoort LabX te zien in plaats van jou te laten raden.
    """
    bewijs: List[Bewijs] = []
    for pad, tekst in beeld.items():
        treffer = _AZ_GEBRUIK.search(tekst)
        if not treffer:
            continue
        # Per REGEL zoeken, en dus zonder de regelovergangen uit het patroon
        # hierboven -- die matchen nooit binnen één regel.
        nr = _regel_van(tekst, r"(^|[;|&]\s*|\s)az\s+\w|azure-cli|InstallAzureCLIDeb")
        bewijs.append(Bewijs(bestand=pad, regel=nr, fragment=_fragment(tekst, nr)))
        if len(bewijs) >= 4:
            break
    if not bewijs:
        return []
    return [Vondst(
        soort="omgeving", sleutel="azure_cli",
        waarde={"nodig": True},
        bewijs=bewijs,
        zekerheid="hoog", bron="detector:azure",
        toelichting=("Deze repo roept de Azure CLI aan. Een branch-lab krijgt die "
                     "standaard niet; vink dit aan, anders strandt het startscript op "
                     "'az: command not found'."),
    )]


#: Hoe je ziet dat een repo zijn geheimen ZELF ophaalt. Dan hoeft er niets in
#: de kluis: het project regelt het, en het enige wat LabX moet leveren is het
#: gereedschap (meestal de Azure CLI) en een geldige aanmelding.
_ZELF_OPHALEN = re.compile(
    r"az\s+keyvault\s+secret"
    r"|SecretClient\(|DefaultAzureCredential"
    r"|fetch[-_]secrets"
    r"|aws\s+secretsmanager|vault\s+kv\s+get|gcloud\s+secrets",
    re.IGNORECASE)


def detecteer_zelf_opgehaalde_geheimen(beeld: RepoBeeld) -> List[Vondst]:
    """Haalt deze repo zijn eigen geheimen op?

    Dan is "deze tien sleutels ontbreken in de kluis" een verkeerde
    conclusie. Lokaal start het project gewoon: een script trekt de waarden
    uit een vault en zet ze klaar. Wat LabX moet leveren is niet de WAARDE
    maar het GEREEDSCHAP -- de CLI en een aanmelding.

    Dat onderscheid maakt het verschil tussen "vul tien geheimen in voordat
    je kunt beginnen" en "zorg dat je ingelogd bent".
    """
    bewijs: List[Bewijs] = []
    for pad, tekst in beeld.items():
        if not _ZELF_OPHALEN.search(tekst):
            continue
        nr = _regel_van(tekst, _ZELF_OPHALEN.pattern)
        bewijs.append(Bewijs(bestand=pad, regel=nr, fragment=_fragment(tekst, nr)))
        if len(bewijs) >= 4:
            break
    if not bewijs:
        return []
    return [Vondst(
        soort="omgeving", sleutel="geheimen_zelf_ophalen",
        waarde={"zelf": True},
        bewijs=bewijs,
        zekerheid="hoog", bron="detector:geheimen",
        toelichting=("Deze repo haalt zijn instellingen zelf uit een vault. De sleutels "
                     "hoeven dan niet in de kluis van dit cluster; wat nodig is, is het "
                     "gereedschap en een geldige aanmelding."),
    )]


# ── alles bij elkaar ─────────────────────────────────────────────────────────

def detecteer_alles(beeld: RepoBeeld) -> List[Vondst]:
    """Elke detector over het repo-beeld, in volgorde van de ladder."""
    vondsten: List[Vondst] = []
    for detector in (
        detecteer_compose,
        detecteer_dockerfile,
        detecteer_poorten,
        detecteer_azure_cli,
        detecteer_zelf_opgehaalde_geheimen,
        detecteer_omgevingsbestanden,
        detecteer_env_profielen,
        detecteer_manifesten,
    ):
        try:
            vondsten.extend(detector(beeld))
        except Exception as exc:  # noqa: BLE001 — één kapotte detector sloopt de verkenning niet
            log.warning("detector %s viel om: %s", detector.__name__, exc)
    return vondsten


# ── staven: de controle op wat het model voorstelt ───────────────────────────

def staaf(vondst: Vondst, beeld: RepoBeeld) -> Vondst:
    """Is er bewijs in de repo voor wat het model voorstelt?

    Gevonden bewijs wordt AAN de vondst gehangen — ook als het model zelf al
    een vindplaats noemde, want die kan verzonnen zijn en deze niet. Zonder
    bewijs blijft de vondst staan, maar onbevestigd: zichtbaar in het plan,
    uitgevinkt in het goedkeurscherm.
    """
    gevonden: List[Bewijs] = []

    for b in vondst.bewijs:
        if b.bestand in beeld:
            tekst = beeld[b.bestand]
            nr = b.regel if (b.regel and 1 <= b.regel <= len(tekst.splitlines())) else None
            gevonden.append(Bewijs(bestand=b.bestand, regel=nr, fragment=_fragment(tekst, nr)))

    for term in _zoektermen(vondst):
        for pad, tekst in beeld.items():
            nr = _regel_van(tekst, re.escape(term))
            if nr:
                gevonden.append(Bewijs(bestand=pad, regel=nr, fragment=_fragment(tekst, nr)))
                break

    vondst.bewijs = _ontdubbel(gevonden) or vondst.bewijs
    vondst.bevestigd = bool(gevonden)
    if not vondst.bevestigd:
        vondst.zekerheid = "laag"
        if not vondst.toelichting:
            vondst.toelichting = "Voorgesteld door het model; ik kon het niet terugvinden in de repo."
    return vondst


def _zoektermen(vondst: Vondst) -> List[str]:
    """Waar moet het bewijs voor deze vondst uit bestaan?

    Voor een dienst is dat de omgevingssleutel of de image-naam: staat die
    nergens in de repo, dan heeft niemand om die dienst gevraagd.
    """
    termen: List[str] = []
    if vondst.soort in ("dienst", "extern"):
        sjabloon = next((d for d in cat.DIENSTEN if d.naam == vondst.sleutel), None)
        if sjabloon:
            termen.extend(sjabloon.lab_omgeving.keys())
        image = str(vondst.waarde.get("image") or "")
        if image:
            termen.append(image.split(":")[0].rsplit("/", 1)[-1])
        termen.extend(str(s) for s in (vondst.waarde.get("sleutels") or []))
    elif vondst.soort == "geheim":
        termen.append(vondst.sleutel)
    elif vondst.soort == "lab_image":
        image = str(vondst.waarde.get("image") or "")
        if image:
            termen.append(image.split(":")[0].rsplit("/", 1)[-1])
    elif vondst.soort == "startscript":
        termen.extend(str(s) for s in (vondst.waarde.get("bouwen") or []))
        termen.extend(str(s) for s in (vondst.waarde.get("starten") or []))
    return [t for t in termen if t and len(t) > 2][:8]


def _ontdubbel(bewijzen: Iterable[Bewijs]) -> List[Bewijs]:
    gezien: set = set()
    uit: List[Bewijs] = []
    for b in bewijzen:
        kern = (b.bestand, b.regel)
        if kern in gezien:
            continue
        gezien.add(kern)
        uit.append(b)
    return uit[:6]
