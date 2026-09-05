# models/lab_extra.py
#
# Een "extra" is een stuk inrichting dat BOVENOP het basis-image in een lab
# komt: Playwright + Chromium, Node, uv, compilers. Dat stond eerst hardcoded
# in lab_service._provision_base_tools, waardoor elk nieuw pakket een
# code-change en een release was ("Playwright werkt niet" → wachten op een
# nieuwe image). Als tabel is het een instelling: je beheert de catalogus in
# de UI en vinkt per lab aan wat erin moet.
#
# Een extra is een PAAR van twee shell-commando's:
#   check_cmd       — exit 0 betekent "staat er al", installatie overslaan.
#                     Dit is wat inrichten idempotent maakt: het draait ook bij
#                     elke start van een lab, en dan is het bijna gratis.
#   install_script  — wat er moet gebeuren als de check faalt.
# Beide draaien als root in de container, via `sh -c`, met netwerk (een lab
# zonder netwerk wordt overgeslagen — er valt dan niets binnen te halen).
from __future__ import annotations

from sqlalchemy import Boolean, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class LabExtra(Base):
    __tablename__ = "lab_extras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    check_cmd: Mapped[str | None] = mapped_column(Text, nullable=True)
    install_script: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Keys van andere extra's die er eerst moeten zijn (Playwright voor Node
    # heeft Node nodig). Worden automatisch meegenomen, ook als het lab ze
    # niet zelf aangevinkt heeft.
    requires: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    # Een browser binnenhalen duurt minuten; de standaard exec-timeout van 120s
    # zou daar middenin afkappen.
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False, default=900)

    # Brengt dit pakket een MCP-SERVER mee die in het lab draait? Dan
    # {"slug", "name", "command", "description"?, "replaces"?: [slugs]}. Na een
    # geslaagde installatie registreert LabX die server zelf, zet hem op de
    # allowlist van dit lab en haalt zijn tools op. Zonder dat blijft er een
    # handmatige stap over die niemand kan raden: het pakket staat er, de agent
    # ziet niets, en de host-variant van dezelfde server pakt de aanroep op —
    # in een container zonder browser.
    mcp_server: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Standaard aangevinkt in het "Nieuw lab"-scherm.
    default_on: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # True = meegeleverd met LabX (zie services/lab/extras_catalog.py). Je mag
    # zo'n rij gewoon aanpassen; `builtin` onthoudt alleen dat er een origineel
    # is om naar terug te zetten.
    builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Vingerafdruk van het MEEGELEVERDE origineel op het moment dat deze rij is
    # gezet. Daarmee kan een nieuwe LabX-versie een verbeterd pakket alsnog
    # doorvoeren zonder eigen aanpassingen te overschrijven: komt de huidige
    # inhoud nog overeen met deze afdruk, dan heeft niemand eraan gezeten en mag
    # het origineel bijgewerkt worden; wijkt hij af, dan blijft de rij met rust.
    builtin_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
