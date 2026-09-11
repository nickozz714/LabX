# models/guard_rule.py
#
# De regels van de data-guard, beheerbaar door de gebruiker.
#
# Waarom dit uit de code komt. De guard was een vaste lijst reguliere
# expressies verspreid over twee bestanden. Elke aanpassing was een
# code-wijziging, en niemand kon zien waaróm iets werd tegengehouden — laat
# staan er iets aan doen. Tegelijk is dit precies het soort ding dat per
# omgeving verschilt: wat bij de ene klant klantdata is, is bij de andere een
# testbestand.
#
# TWEE soorten regels, en het verschil is wezenlijk:
#
# - `opdracht` — kijkt naar het COMMANDO, vóór uitvoeren. Dit is de enige plek
#   waar je `SELECT MAX(bedrag)` kunt tegenhouden: dat levert één getal op dat
#   in geen enkele uitvoercontrole opvalt en tóch een echte magnitude is. Hier
#   hoort blokkeren thuis.
# - `uitvoer` — kijkt naar wat er TERUGKOMT, vóór het naar het model gaat.
#   Hier hoort MASKEREN thuis: de hele uitvoer weggooien omdat er één BSN in
#   staat, kost je ook de exitcode, het pad en de foutmelding die je nodig had.
#
# Een regel is ingebouwd (een detector met een checksum, zoals BSN of IBAN) of
# een eigen patroon. Ingebouwde regels worden bijgewerkt zolang je ze niet zelf
# hebt aangepast — dezelfde afspraak als bij lab-extra's.
from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

DOELEN = ("opdracht", "uitvoer")

# blokkeren    — tegenhouden; bij een opdracht: niet uitvoeren.
# maskeren     — alleen de treffers onleesbaar maken, de rest gaat gewoon door.
#                Bij een opdracht bestaat dit niet (je kunt een commando niet
#                half uitvoeren); daar valt het terug op waarschuwen.
# waarschuwen  — doorlaten, maar vastleggen en tonen.
# toelaten     — expliciet goedkeuren; wint van andere regels. Hiermee zet je
#                een ingebouwde regel uit voor jouw geval zonder hem te wissen.
ACTIES = ("blokkeren", "maskeren", "waarschuwen", "toelaten")


class GuardRule(Base):
    __tablename__ = "guard_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Sleutel voor ingebouwde regels; leeg bij een eigen regel.
    key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target: Mapped[str] = mapped_column(String(16), nullable=False, default="uitvoer")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "ingebouwd" (een detector in code, met checksum-controle) of "regex".
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="regex")
    pattern: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Waar de treffer onder valt. Vrije tekst; komt terug in de audit en in de
    # melding aan de agent, dus hou hem begrijpelijk ("burgerservicenummer").
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="klantgegevens")
    action: Mapped[str] = mapped_column(String(16), nullable=False, default="maskeren")

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    # Vingerafdruk van de ingebouwde versie, zodat een update alleen regels
    # bijwerkt die de gebruiker niet zelf heeft aangepast.
    builtin_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index("idx_guard_rules_doel", "target", "sort_order"),
        Index("idx_guard_rules_key", "key", unique=True),
    )
