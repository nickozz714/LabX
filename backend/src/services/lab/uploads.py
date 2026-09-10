"""
services/lab/uploads.py

Bestanden die van buiten het lab naar binnen komen: uit de bestandsbrowser,
als bijlage bij een chatbericht, of bij de extra instructie van een agent-run.

**Waarom ze in het lab landen en niet in de prompt.** De agent werkt in het
lab; dat is de hele opzet. Een bijlage in de prompt plakken werkt alleen voor
kleine tekst, en dan nog kost het context die je aan het werk wilt besteden —
een spreadsheet, een PDF of een screenshot gaat er sowieso niet in. Een bestand
op /workspace kan de agent openen met het gereedschap dat erbij hoort, zo vaak
als nodig, en het blijft daar staan voor een volgende run. Wat er naar de
prompt gaat is dus niet de inhoud maar het PAD.

**Namen worden hier schoongemaakt, niet verderop.** Een bestandsnaam komt van
een browser en mag alles bevatten: spaties, aanhalingstekens, `../`, een
newline. Zulke namen zijn in een shell gevaarlijk en in een bestandsbrowser
onbruikbaar. Ze worden daarom teruggebracht tot letters, cijfers, punt,
liggend streepje en underscore — en het schrijven zelf geeft het pad als
argument mee in plaats van het in een commando te plakken (lab_service), zodat
zelfs een naam die hier onverhoopt doorheen komt niets kan uitvoeren.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Waar bijlagen terechtkomen. Onder /workspace, want dat is het gedeelde
# volume: alle werkers van een lab zien hetzelfde, en wat je vandaag uploadt
# staat er bij de volgende run nog.
#
# De AANROEPER kiest de submap en stuurt hem als `dir` mee — de bestandsbrowser
# gebruikt de map waar je staat, een gesprek `.../chat-<thread>`, een ticket
# `.../<TICKET-KEY>`. Die keuze hoort bij de plek waar de knop staat en niet
# hier; wat hier wél hoort is de bovengrens: `_safe_path` in lab_service laat
# niets buiten /workspace toe.
UPLOAD_ROOT = "/workspace/uploads"

# Grenzen. Ruim genoeg voor een export, een PDF of een screenshot; klein genoeg
# dat één verzoek de backend niet minutenlang bezet houdt.
MAX_BESTAND_BYTES = 25 * 1024 * 1024
MAX_TOTAAL_BYTES = 100 * 1024 * 1024

_TOEGESTAAN = re.compile(r"[^A-Za-z0-9._-]+")


def veilige_naam(naam: str, *, terugval: str = "bestand") -> str:
    """Een bestandsnaam uit een browser omzetten naar iets dat veilig op schijf
    kan staan. Mappen eruit (een browser stuurt soms een volledig pad mee),
    rare tekens naar `_`, en niets dat met een punt begint — een `.bashrc` of
    `.env` die je per ongeluk sleept, hoort niet stilletjes iets te overschrijven."""
    kaal = (naam or "").replace("\\", "/").split("/")[-1].strip()
    kaal = _TOEGESTAAN.sub("_", kaal).strip("._-")
    kaal = re.sub(r"_{2,}", "_", kaal)
    if not kaal:
        return terugval
    # Lange namen breken sommige bestandssystemen en maken de browser
    # onleesbaar; de extensie blijft behouden want die bepaalt wat je ermee kunt.
    if len(kaal) > 120:
        stam, punt, ext = kaal.rpartition(".")
        if punt and len(ext) <= 12:
            kaal = stam[:120 - len(ext) - 1] + "." + ext
        else:
            kaal = kaal[:120]
    return kaal


def unieke_naam(naam: str, bestaand: List[str]) -> str:
    """Botst de naam met iets dat er al staat, dan `-2`, `-3`, … ervoor de
    extensie. Overschrijven is hier het verkeerde antwoord: twee keer
    `export.csv` uploaden zijn meestal twee verschillende exports."""
    if naam not in bestaand:
        return naam
    stam, punt, ext = naam.rpartition(".")
    if not punt:
        stam, ext = naam, ""
    n = 2
    while True:
        kandidaat = f"{stam}-{n}{'.' + ext if ext else ''}"
        if kandidaat not in bestaand:
            return kandidaat
        n += 1


def beschrijf(bijlagen: Optional[List[Dict[str, Any]]]) -> str:
    """Het blok dat aan een bericht of instructie wordt geplakt.

    Bewust expliciet over wat de agent moet doen: alleen een pad noemen levert
    een agent op die de inhoud gokt uit de bestandsnaam. En bewust met de
    grootte erbij, zodat hij zelf kan besluiten of hij het bestand in één keer
    leest of eerst een stuk bekijkt.
    """
    regels = [b for b in (bijlagen or []) if (b or {}).get("path")]
    if not regels:
        return ""
    uit = ["", "### Bijgevoegde bestanden",
           "Deze staan in het lab waar je nu werkt. Open ze daar; ze zitten niet"
           " in dit bericht en de inhoud is hieronder niet meegestuurd."]
    for b in regels:
        maat = int(b.get("bytes") or 0)
        uit.append(f"- `{b['path']}` ({_leesbaar(maat)})")
    return "\n".join(uit)


def _leesbaar(bytes_: int) -> str:
    if bytes_ < 1024:
        return f"{bytes_} B"
    if bytes_ < 1024 * 1024:
        return f"{bytes_ / 1024:.0f} kB"
    return f"{bytes_ / (1024 * 1024):.1f} MB"
