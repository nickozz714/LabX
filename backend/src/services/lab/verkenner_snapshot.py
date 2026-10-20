"""
services/lab/verkenner_snapshot.py

Het repo-beeld: welke bestanden de verkenner te zien krijgt.

De verkenning kloont ondiep in een wegwerpcontainer en leest daar bestanden
uit. Wat ze NIET doet is iets uit die repo uitvoeren — geen install, geen
build, geen script. Een repo die je niet vertrouwt mag je daarom veilig
verkennen; pas bij het goedkeuren van het plan start er iets.

**Waarom dit begrensd is.** Een monorepo heeft honderdduizenden bestanden en
een model heeft daar niets aan. De begrenzing zit op drie plekken: mappen die
nooit interessant zijn vallen af, alleen bestanden die iets over de inrichting
zéggen worden gelezen, en er geldt een plafond per bestand en in totaal. Wat
erbuiten valt is nog steeds zichtbaar in de boom — het model ziet dus dát een
bestand bestaat en kan erom vragen.

**Waarom de redactie hier staat en niet in de backend.** Een gekloonde repo
hoort geen `.env` met werkende sleutels te bevatten, maar "hoort" is geen
garantie — op de eerste echte repo waar ik dit op losliet (LabX zelf) stonden
er vijf in. Wat hier uit de container komt gaat straks een modelvenster in.
Dus gaan de waarden eraf bij de BRON: de backend krijgt het geheim niet eens
te zien, en dan kan het ook niet in een log, een plan of een prompt belanden.

De scripts hieronder draaien IN de container, met niets dan de
standaardlibrary.
"""
from __future__ import annotations

#: Mappen die nooit iets over de inrichting zeggen en wel de helft van de
#: bestanden leveren.
OVERSLAAN = (
    "node_modules", "dist", "build", "out", "vendor", "target", "coverage",
    "__pycache__", "bin", "obj", "tmp", "logs",
)

#: Verborgen mappen vallen af, op deze na: daar staat wél iets over de
#: inrichting in. Een harde lijst van namen bleek te letterlijk — `.venv` stond
#: erin en `.venv-test` niet, en die ene map vulde in zijn eentje de hele boom
#: van 4000 bestanden. Bij een repo die je niet kent gaat dat stil mis, en dan
#: ziet het model de helft van het project niet.
BEHOUD_VERBORGEN = (".devcontainer", ".github", ".config", ".vscode")

#: Mappen waar een virtuele omgeving in pleegt te staan, ongeacht het
#: achtervoegsel dat iemand eraan plakte.
OMGEVING_VOORVOEGSELS = ("venv", "env-", ".env-")

#: Bestanden waarvan de INHOUD iets zegt over hoe dit project draait.
INTERESSANT = (
    "docker-compose*.y*ml", "compose*.y*ml", "Dockerfile*", "*.dockerfile",
    ".devcontainer/*", ".env*", "env.example", "env.sample",
    "package.json", "pnpm-workspace.yaml", "turbo.json", "nx.json", "lerna.json",
    "pyproject.toml", "requirements*.txt", "setup.py", "setup.cfg", "Pipfile",
    "go.mod", "Cargo.toml", "Gemfile", "pom.xml", "build.gradle*",
    "Makefile", "Taskfile*.y*ml", "justfile", "Procfile",
    ".nvmrc", ".tool-versions", ".python-version",
    "README*", "CONTRIBUTING*", "DEVELOPMENT*", "docs/*.md",
    "*.sln", "global.json",
)

#: Plafonds. Ruim genoeg voor een monorepo, krap genoeg voor een modelvenster.
MAX_BESTANDEN = 120
MAX_PER_BESTAND = 64 * 1024
MAX_TOTAAL = 1_200_000
MAX_BOOM = 4000
MAX_DIEPTE = 8

#: Waarden die in een omgevingsbestand mogen blijven staan, ook al ziet de
#: sleutel eruit als een geheim: publiek gedocumenteerde constanten van
#: emulators. De Cosmos-sleutel is in elke emulator ter wereld dezelfde, en
#: `devstoreaccount1` is hoe je Azurite herkent. Zonder deze uitzondering wordt
#: de detectie blind voor precies de diensten die we willen vinden.
PUBLIEKE_MARKERS = (
    "devstoreaccount1",
    "UseDevelopmentStorage",
    "C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM",
)

#: Gedeelde kop: de redactie. Staat in beide scripts, want ook een bestand dat
#: het model later NALEEST moet er langs.
_REDACTIE = r'''
import re as _re

PUBLIEKE_MARKERS = %(markers)r
_GEHEIM = _re.compile(r"(_KEY|_SECRET|_TOKEN|_PASSWORD|_PASS|_CREDENTIAL|_DSN|CONNECTION_STRING)", _re.I)
_ENV_BESTAND = _re.compile(r"(^|/)\.env(\.|$)|(^|/)env\.(example|sample|template)$")


def schoon_env(tekst):
    """Sleutelnamen behouden, waarden weggooien.

    De waarde blijft staan waar hij iets ONDERSCHEIDT en niets verraadt:
    `postgres://` in een DATABASE_URL bepaalt welke database het is. De
    inloggegevens in zo'n URL gaan er wel af.
    """
    uit = []
    for regel in tekst.splitlines():
        kaal = regel.strip()
        if not kaal or kaal.startswith("#") or "=" not in kaal:
            uit.append(regel)
            continue
        sleutel, _, waarde = regel.partition("=")
        naam = sleutel.strip()
        if naam.lower().startswith("export "):
            naam = naam[7:].strip()
        schoon = waarde.strip().strip('"').strip("'")
        if (_GEHEIM.search(naam)
                and not any(m in schoon for m in PUBLIEKE_MARKERS)
                # Een kort getal is geen geheim maar een instelling.
                # `ACCESS_TOKEN_MIN=15` en `REFRESH_TOKEN_DAYS=30` sneuvelden
                # op het woord TOKEN in hun naam, en dan ziet het model een
                # lege waarde waar een looptijd hoorde te staan.
                and not _re.fullmatch(r"\d{1,6}", schoon)):
            uit.append(sleutel + "=")
            continue
        schoon = _re.sub(r"://[^/@\s]+@", "://", schoon)[:80]
        uit.append(sleutel + "=" + schoon)
    return "\n".join(uit)


def misschien_schoon(pad, tekst):
    return schoon_env(tekst) if _ENV_BESTAND.search(pad) else tekst
''' % {"markers": PUBLIEKE_MARKERS}


SCRIPT = _REDACTIE + r'''
import fnmatch, json, os, sys

OVERSLAAN = set(%(overslaan)r)
BEHOUD_VERBORGEN = set(%(behoud)r)
OMGEVING_VOORVOEGSELS = %(voorvoegsels)r
INTERESSANT = %(interessant)r
MAX_BESTANDEN, MAX_PER_BESTAND = %(max_bestanden)d, %(max_per_bestand)d
MAX_TOTAAL, MAX_BOOM, MAX_DIEPTE = %(max_totaal)d, %(max_boom)d, %(max_diepte)d

wortel = sys.argv[1]
boom, kandidaten = [], []

for map_, mappen, bestanden in os.walk(wortel):
    rel = os.path.relpath(map_, wortel)
    rel = "" if rel == "." else rel.replace(os.sep, "/")
    if rel.count("/") + 1 > MAX_DIEPTE:
        mappen[:] = []
        continue
    mappen[:] = sorted(
        m for m in mappen
        if m not in OVERSLAAN
        and not (m.startswith(".") and m not in BEHOUD_VERBORGEN)
        and not m.startswith(OMGEVING_VOORVOEGSELS)
    )
    for naam in sorted(bestanden):
        pad = (rel + "/" + naam) if rel else naam
        if len(boom) < MAX_BOOM:
            boom.append(pad)
        if any(fnmatch.fnmatch(naam, p) or fnmatch.fnmatch(pad, p) for p in INTERESSANT):
            kandidaten.append(pad)

# Ondiepe bestanden eerst: een package.json in de wortel zegt meer over het
# project dan de vijftiende in een werkruimte.
kandidaten.sort(key=lambda p: (p.count("/"), len(p)))

inhoud, totaal, overgeslagen = {}, 0, []
for pad in kandidaten:
    if len(inhoud) >= MAX_BESTANDEN or totaal >= MAX_TOTAAL:
        overgeslagen.append(pad)
        continue
    try:
        with open(os.path.join(wortel, pad), "rb") as f:
            ruw = f.read(MAX_PER_BESTAND + 1)
    except OSError:
        continue
    if b"\0" in ruw[:4096]:
        continue
    afgekapt = len(ruw) > MAX_PER_BESTAND
    tekst = ruw[:MAX_PER_BESTAND].decode("utf-8", "replace")
    if afgekapt:
        tekst += "\n... (afgekapt)"
    tekst = misschien_schoon(pad, tekst)
    inhoud[pad] = tekst
    totaal += len(tekst)

json.dump({"boom": boom, "boom_afgekapt": len(boom) >= MAX_BOOM,
           "inhoud": inhoud, "overgeslagen": overgeslagen[:50],
           "bytes": totaal}, sys.stdout)
''' % {
    "overslaan": tuple(OVERSLAAN),
    "behoud": tuple(BEHOUD_VERBORGEN),
    "voorvoegsels": tuple(OMGEVING_VOORVOEGSELS),
    "interessant": INTERESSANT,
    "max_bestanden": MAX_BESTANDEN,
    "max_per_bestand": MAX_PER_BESTAND,
    "max_totaal": MAX_TOTAAL,
    "max_boom": MAX_BOOM,
    "max_diepte": MAX_DIEPTE,
}

#: Losse bestanden nalezen, als het model erom vraagt. Zelfde plafond per
#: bestand, zodat één `yarn.lock` het venster niet alsnog opvreet — en
#: dezelfde redactie, want "het model vroeg erom" is geen reden om een geheim
#: wél door te geven.
EXTRA_SCRIPT = _REDACTIE + r'''
import json, os, sys

wortel = sys.argv[1]
uit = {}
for pad in json.loads(sys.argv[2])[:20]:
    veilig = os.path.normpath(pad).lstrip("/")
    if veilig.startswith(".."):
        continue
    try:
        with open(os.path.join(wortel, veilig), "rb") as f:
            ruw = f.read(%(max_per_bestand)d)
    except OSError:
        continue
    if b"\0" not in ruw[:4096]:
        uit[veilig] = misschien_schoon(veilig, ruw.decode("utf-8", "replace"))
json.dump(uit, sys.stdout)
''' % {"max_per_bestand": MAX_PER_BESTAND}
