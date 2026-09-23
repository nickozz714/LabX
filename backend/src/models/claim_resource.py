# models/claim_resource.py
#
# Wat je in een lab kunt RESERVEREN, en wie het nu vasthoudt.
#
# Waarom dit bestaat. Een lab is een sandbox-pc, en sinds een planning meerdere
# tickets in dezelfde werker kan zetten, zitten er twee agents tegelijk achter
# die pc. Het meeste kan dat prima naast elkaar. Sommige dingen niet: er is één
# browser, één playground, één poort 8000. Wie die tegelijk pakt, krijgt geen
# foutmelding maar een raadsel — de ander sloot je tabblad, of je server start
# niet "om onduidelijke redenen".
#
# Twee tabellen, met opzet uit elkaar gehouden:
# - `claim_resources` is de CATALOGUS: wát er te claimen valt, en wat er moet
#   gebeuren als het bezet is. Die beheer je zelf, net als de lab-extra's; per
#   lab vink je aan welke ervan gelden.
# - `resource_claims` is wie er NU op zit.
#
# Dit staat naast `board__claim` (models/plan.py) en vervangt dat niet. Dat gaat
# over LOGISCHE bronnen die de agent zelf verzint — een Fabric-pipeline, een
# tabel, een map. Dit gaat over het FYSIEKE spul in een container, dat LabX
# kent en jij beheert.
from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

# werker: er is er één per container (de browser, een poort).
# lab:    er is er één voor het hele lab, over de werkers heen (een login die
#         op het gedeelde volume staat, een externe omgeving).
RESOURCE_SCOPES = ("werker", "lab")

# wachten:  de aanvrager blijft hangen tot hij vrijkomt (of zijn tijd om is).
# weigeren: hij krijgt meteen te horen wie hem heeft en gaat zelf iets anders doen.
RESOURCE_GEDRAG = ("wachten", "weigeren")


class ClaimResource(Base):
    """Eén regel uit de catalogus van claimbare resources."""

    __tablename__ = "claim_resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Waarmee de agent hem aanroept. Kleine letters, koppeltekens.
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="werker")
    gedrag: Mapped[str] = mapped_column(String(16), nullable=False, default="wachten")
    # Na zoveel minuten valt een claim vanzelf weg. Een agent die crasht of
    # vergeet vrij te geven mag een resource niet voor eeuwig op slot zetten —
    # dat is het soort blokkade waar niemand meer uitkomt.
    timeout_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    # Geldt deze standaard in een nieuw lab?
    default_on: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Uit = hij bestaat niet meer voor de agents, zonder dat je hem kwijtraakt.
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Meegeleverd of zelf gemaakt. Een meegeleverde mag je aanpassen; deze vlag
    # zegt alleen dat er een origineel bestaat om naar terug te keren.
    builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("idx_claim_resources_key", "key"),)


class ResourceClaim(Base):
    """Wie er nu op een resource zit.

    Een rij blijft staan nadat hij is vrijgegeven (`released_at`), zodat
    achteraf te zien is wie wanneer wat vasthield — bij een botsing is dat
    precies wat je wilt weten.
    """

    __tablename__ = "resource_claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lab_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # NULL bij een resource met scope "lab", en bij een werker-resource in een
    # lab dat nog met één container werkt (dan is het lab de werker).
    worker_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resource_key: Mapped[str] = mapped_column(String(64), nullable=False)

    # Wie hem heeft. `houder` is de technische sleutel waarop vrijgeven werkt
    # (de thread of de run), `houder_label` is wat een mens leest.
    houder: Mapped[str] = mapped_column(String(128), nullable=False)
    houder_soort: Mapped[str] = mapped_column(String(16), nullable=False, default="sessie")
    houder_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    # Na dit moment telt de claim niet meer mee, ook als niemand hem vrijgaf.
    expires_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    released_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    released_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index("idx_resource_claims_actief", "lab_id", "resource_key", "released_at"),
        Index("idx_resource_claims_houder", "houder"),
    )
