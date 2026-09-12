"""
services/lab/profielen.py

Beveiligingsprofielen: wat een lab over zijn eigen wereld weet.

**Waarom dit nodig bleek.** De guard beoordeelde elk lab hetzelfde, en dat gaat
mis zodra je weet waar een lab voor is. Een lab dat in Microsoft Fabric werkt,
produceert de hele dag technische uitvoer: kolomlijsten, pipeline-definities,
procestabellen, delta-logs. Presidio's namenherkenning maakte daar rommel van —
gemeten op een gewone `ps`-uitvoer markeerde het "PID" en "python3" als
plaatsnaam, 35 keer in één antwoord. En de opdrachtregels blokkeerden
`SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS`, terwijl dat juist het
gereedschap is waarmee je datakwaliteit onderzoekt zónder gegevens te zien.

Het probleem was niet dat de regels te streng stonden, maar dat ze geen idee
hadden waar ze naar keken. Een profiel geeft ze dat idee.

**Wat een profiel NIET is.** Geen ontsnapping. Ook in het Fabric-profiel blijven
de checksum-detectors aan (een BSN is een BSN), blijft de dataset-detector aan
(veertig klantrijen zijn veertig klantrijen), en blijft het lokale model
kijken. Wat een profiel verschuift is wat er als NORMAAL geldt — niet wat er
beschermd wordt.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Elk profiel:
#   presidio        — draait de namenherkenning? Uit voor werelden waarin
#                     technische tekst de norm is.
#   toelaten_extra  — patronen die in deze wereld als normaal beheerwerk gelden
#                     en dus niet op de opdracht geblokkeerd worden.
#   acties          — overschrijft de actie van een regel, op sleutel.
PROFIELEN: Dict[str, Dict[str, Any]] = {
    "generiek": {
        "label": "Generiek",
        "uitleg": ("Geen aannames over de omgeving. Alle regels gelden zoals ze "
                   "staan en de namenherkenning draait mee. Kies dit als je niet "
                   "weet wat een lab gaat doen."),
        "presidio": True,
        "toelaten_extra": [],
        "acties": {},
    },
    "fabric": {
        "label": "Microsoft Fabric / Azure data-platform",
        "uitleg": ("Het lab werkt in Fabric, Power BI of Azure. Daar is technische "
                   "uitvoer de norm: kolomlijsten, pipeline-definities, job-status, "
                   "delta-logs. De namenherkenning gaat uit — die maakt van "
                   "pipelinenamen en procestabellen alleen rommel — en beheerwerk "
                   "op die platformen geldt niet als een poging klantdata op te "
                   "halen. Wat blijft: BSN, IBAN, creditcard, telefoon, de "
                   "dataset-detector en het lokale model."),
        "presidio": False,
        "toelaten_extra": [
            # Delta Lake / OneLake metadata: het transactielogboek zegt HOEVEEL
            # rijen er zijn en welke bestanden erbij horen, niet wat erin staat.
            r"_delta_log|\bnumRecords\b|\bdeltaTable\b|DESCRIBE\s+(HISTORY|DETAIL)",
            # Fabric- en Synapse-metadata via SQL.
            r"\bsys\.(tables|columns|objects|schemas|indexes)\b",
            # De job- en run-API's: status en duur, geen inhoud.
            r"/jobs/instances|/items/[^/ ]+/(getDefinition|updateDefinition)",
        ],
        "acties": {},
    },
    "streng": {
        "label": "Streng (vertrouwelijke omgeving)",
        "uitleg": ("Alles wat kán wijzen op gegevens gaat dicht in plaats van "
                   "gemaskeerd. Kies dit voor een lab dat met echt "
                   "productiemateriaal werkt en waar liever te veel dan te weinig "
                   "wordt tegengehouden — reken op valse treffers."),
        "presidio": True,
        "toelaten_extra": [],
        "acties": {"bsn": "blokkeren", "iban": "blokkeren",
                   "creditcard": "blokkeren", "telefoon_nl": "blokkeren",
                   "email": "blokkeren"},
    },
}

STANDAARD = "generiek"


def profiel(sleutel: Optional[str]) -> Dict[str, Any]:
    return PROFIELEN.get(str(sleutel or STANDAARD), PROFIELEN[STANDAARD])


def lijst() -> List[Dict[str, Any]]:
    return [{"key": k, "label": v["label"], "uitleg": v["uitleg"],
             "presidio": v["presidio"]} for k, v in PROFIELEN.items()]
