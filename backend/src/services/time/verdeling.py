"""Tijd verdelen over projecten die tegelijk liepen.

Het probleem dat dit oplost: twintig uur aan project X en vijfentwintig aan
project Y is vijfenveertig uur, maar als vijftien daarvan tegelijk liepen is er
maar dertig uur klok voorbijgegaan. Wie de bruto-getallen op een factuur zet,
schrijft vijftien uur die niet bestaan. Wie ze zomaar halveert, benadeelt het
project dat wél alleen liep.

De aanpak is een veegregel over de tijdlijn. Alle begin- en eindpunten vormen
samen een rij elementaire schijfjes. Van elk schijfje weten we welke projecten
op dat moment actief waren; de duur van dat schijfje wordt gelijk verdeeld over
díé projecten. Loopt er één project, dan krijgt het alles. Lopen er drie, dan
krijgt elk een derde. Opgeteld over alle schijfjes komt de verdeling precies uit
op de werkelijk verstreken klok — nooit meer.

Dat "gelijk verdelen" is een keuze en geen waarheid: het zegt dat twee
projecten die tegelijk draaiden even zwaar op je aandacht drukten. Dat klopt
vaak genoeg om een eerlijk uitgangspunt te zijn, en de bedoeling is dat je het
daarna zelf bijstelt. Daarom geeft deze module de overlap apart terug in plaats
van hem stil weg te poetsen: je moet kunnen zien hoe groot de aanname is.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


def _stip(waarde: Optional[str]) -> Optional[datetime]:
    """Een ISO-tijdstempel naar datetime, met of zonder tijdzone."""
    if not waarde:
        return None
    try:
        d = datetime.fromisoformat(str(waarde).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


Interval = Tuple[datetime, datetime, str]   # (begin, eind, project)


def intervallen_van(regels: Iterable[dict]) -> List[Interval]:
    """De regels die een echte looptijd hebben, als intervallen.

    Een regel zonder begin- en eindtijd (met de hand ingevoerd, of door de
    agent gemeld zonder tijdstippen) kan per definitie niet overlappen: we
    weten niet wannéér hij viel. Die laten we hier weg en tellen we elders
    mee, zodat zo'n regel nooit stilzwijgend als overlap verdwijnt.
    """
    uit: List[Interval] = []
    for r in regels:
        begin, eind = _stip(r.get("started_at")), _stip(r.get("ended_at"))
        if begin is None or eind is None or eind <= begin:
            continue
        uit.append((begin, eind, str(r.get("project") or "(geen project)")))
    return uit


def verdeel(intervallen: Sequence[Interval]) -> Dict[str, Dict[str, float]]:
    """Per project: bruto minuten, verdeelde minuten en overlap.

    - **bruto**    — de eigen looptijd, dubbelingen binnen hetzelfde project
                     meegerekend als aparte regels (twee runs die elkaar
                     overlappen binnen één project tellen hier dus twee keer).
    - **verdeeld** — het eerlijke deel van de werkelijk verstreken klok.
    - **overlap**  — bruto min verdeeld: de tijd die je met een ander project
                     deelde en dus niet twee keer mag schrijven.
    """
    if not intervallen:
        return {}

    grenzen = sorted({t for begin, eind, _ in intervallen for t in (begin, eind)})
    bruto: Dict[str, float] = {}
    verdeeld: Dict[str, float] = {}

    for begin, eind, project in intervallen:
        bruto[project] = bruto.get(project, 0.0) + (eind - begin).total_seconds() / 60
        verdeeld.setdefault(project, 0.0)

    for links, rechts in zip(grenzen, grenzen[1:]):
        duur = (rechts - links).total_seconds() / 60
        if duur <= 0:
            continue
        # Welke PROJECTEN lopen in dit schijfje. Een set, want twee runs van
        # hetzelfde project tegelijk maken dat project niet zwaarder: er gaat
        # nog steeds maar één klok.
        actief = {p for b, e, p in intervallen if b < rechts and e > links}
        if not actief:
            continue
        deel = duur / len(actief)
        for p in actief:
            verdeeld[p] = verdeeld.get(p, 0.0) + deel

    return {
        p: {
            "bruto": round(bruto.get(p, 0.0), 1),
            "verdeeld": round(verdeeld.get(p, 0.0), 1),
            "overlap": round(bruto.get(p, 0.0) - verdeeld.get(p, 0.0), 1),
        }
        for p in sorted(bruto)
    }


def klok(intervallen: Sequence[Interval]) -> float:
    """De werkelijk verstreken tijd in minuten: de vereniging van alle
    intervallen. Dit is het plafond — de som van alle `verdeeld` hoort hier
    precies op uit te komen, en meer dan dit kun je nooit geschreven hebben."""
    if not intervallen:
        return 0.0
    blokken = sorted((b, e) for b, e, _ in intervallen)
    totaal = 0.0
    hu_b, hu_e = blokken[0]
    for b, e in blokken[1:]:
        if b <= hu_e:              # sluit aan of overlapt: oprekken
            hu_e = max(hu_e, e)
        else:
            totaal += (hu_e - hu_b).total_seconds() / 60
            hu_b, hu_e = b, e
    totaal += (hu_e - hu_b).total_seconds() / 60
    return round(totaal, 1)
