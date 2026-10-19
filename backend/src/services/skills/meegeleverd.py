"""services/skills/meegeleverd.py — skills die LabX zelf meebrengt.

Eén keer aangemaakt bij het opstarten, daarna nooit meer aangeraakt. Dat
laatste is het belangrijke deel: zet je hem uit, pas je de tekst aan of gooi
je hem weg, dan blijft dat zo. Een meegeleverde skill die zichzelf elke
herstart terugzet, is geen hulp maar een discussie.

Waarom ze in de DATABASE landen en niet in de code blijven: een skill is
tekst die jij hoort te kunnen bijstellen. In het Skills-scherm staat hij
tussen je eigen skills, met hetzelfde schakelaartje ernaast.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from component_logging import get_logger

log = get_logger(__name__)


#: De tien regels van `i-have-adhd` (ayghri, MIT) -- naverteld in LabX' eigen
#: woorden en taal, niet overgenomen. De bron staat erbij omdat het idee niet
#: van ons is: https://github.com/ayghri/i-have-adhd
#:
#: Waarom dit een SKILL is en geen vaste systeemprompt: het is een
#: SCHRIJFSTIJL, en of je die wilt verschilt per dag en per taak. Een skill
#: heeft een schakelaar; een systeemprompt heeft die niet.
TO_THE_POINT = """\
Schrijf je antwoorden zo dat iemand die moeite heeft met lange lappen tekst
er meteen mee uit de voeten kan. Tien regels:

1. Begin met wat er moet gebeuren, niet met context. De eerste regel is iets
   dat de lezer kan DOEN.
2. Meer dan één stap? Nummer ze. Zo min mogelijk stappen, en elke stap af te
   maken.
3. Eindig met één concrete volgende actie, klein genoeg voor twee minuten.
4. Geen zijpaden. Maak eerst het hoofdprobleem af; noem de rest daarna als
   losse vraag ("Zal ik ook naar X kijken?").
5. Zeg elke beurt waar je staat: "stap 3 van 5 klaar".
6. Schattingen in echte eenheden: "ongeveer 2 minuten", niet "zo meteen".
7. Maak zichtbaar wat er AF is, in concrete termen -- niet weggestopt in een
   samenvatting.
8. Fouten zakelijk: wat ging er mis, en wat is de oplossing. Geen excuses.
9. Lijstjes van hooguit vijf dingen, op volgorde van belang. Is er meer, zeg
   dat er meer is.
10. Geen inleiding, geen terugblik, geen afsluitende beleefdheid.

Uitzonderingen -- hier mag het langer:
- Vraagt de lezer om uitleg, geef die dan. Nog steeds zonder inleiding en
  zonder afsluiting.
- Iets dat je niet terugdraait (verwijderen, force push, een database leegmaken)
  vraagt altijd eerst om bevestiging, hoe kort het antwoord verder ook is.
- Lukt dezelfde oplossing drie keer niet, stop dan met proberen en stel één
  diagnostische vraag.
- Is de opdracht echt dubbelzinnig, stel dan één verduidelijkende vraag in
  plaats van te gokken.
- Regels van de omgeving zelf (bevestigingen, de data-guard, wat je niet mag)
  gaan hier altijd vóór.

Vóór je iets verstuurt: staat de actie bovenaan, kan alles eruit wat geen
informatie draagt, en weet de lezer wat hij nu moet doen?

Naar het idee van `i-have-adhd` van ayghri (MIT-licentie),
https://github.com/ayghri/i-have-adhd -- nagevolgd, niet overgenomen."""


#: Wat LabX meebrengt. Een skill die hier bij komt, verschijnt bij de
#: eerstvolgende start -- en alleen als er nog geen skill met die naam is.
MEEGELEVERD: List[Dict[str, Any]] = [
    {
        "name": "to-the-point",
        "display_name": "Kort en to the point",
        "description": ("Antwoorden die beginnen met wat je moet doen: geen inleiding, "
                        "geen terugblik, stappen genummerd, lijstjes kort."),
        "instructions": TO_THE_POINT,
        # `sessie` en niet `beide`: dit gaat over hoe de agent SCHRIJFT, en dat
        # geldt in elk gesprek. Hem per lab moeten aanvinken zou betekenen dat
        # je in het ene lab korte antwoorden krijgt en in het andere niet,
        # zonder dat daar een reden voor is.
        "usage_scope": "sessie",
        "is_enabled": True,
        "priority": 10,
    },
]


def zorg_voor_meegeleverde_skills(db: Session) -> int:
    """Maak ontbrekende meegeleverde skills aan. Geeft terug hoeveel er bij kwamen.

    Bestaat er al een skill met die naam, dan blijft die met rust -- ook als
    hij uitstaat, ook als de tekst is aangepast. Dit draait bij elke start.
    """
    from models.skill import Skill

    nu = datetime.now(timezone.utc).isoformat()
    bij = 0
    for sjabloon in MEEGELEVERD:
        bestaat = db.query(Skill).filter(Skill.name == sjabloon["name"]).first()
        if bestaat is not None:
            continue
        db.add(Skill(created_at=nu, updated_at=nu, is_system=False, **sjabloon))
        bij += 1
        log.infox("Meegeleverde skill toegevoegd", skill=sjabloon["name"])
    if bij:
        db.commit()
    return bij
