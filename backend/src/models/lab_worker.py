# models/lab_worker.py
#
# Een WERKER is één container van een lab. Een lab had er altijd precies één,
# en daarmee kon er ook maar één agent-run tegelijk in werken: twee runs in
# dezelfde container vechten om dezelfde bestanden, processen en `az`-sessie.
# Met meerdere werkers kunnen planningen naast elkaar lopen binnen hetzelfde
# lab.
#
# Wat werkers DELEN is het volume op /workspace. Dat is met opzet: een lab ís
# zijn werkmap — de repo, de scripts, de logs, de skills die erin gezet zijn.
# Werkers zijn extra handen in dezelfde werkplaats, geen aparte werkplaatsen.
# De keerzijde hoort erbij: twee runs die tegelijk hetzelfde bestand
# bewerken, doen dat ook echt tegelijk. Wie dat niet wil, geeft de planningen
# aparte labs.
#
# Werker 1 is de container die het lab altijd al had. `Lab.container_id` en
# `Lab.network_alias` blijven een SPIEGEL van die eerste werker, zodat alles
# wat met "het lab" praat (terminal, bestandsbrowser, browserproxy, az-sync)
# ongewijzigd blijft werken. De werkers-tabel is de waarheid; die twee velden
# lopen mee.
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

# creating | running | stopped | error — zelfde woorden als een lab, want het
# is dezelfde soort toestand.
WORKER_STATES = ("creating", "running", "stopped", "error")


class LabWorker(Base):
    __tablename__ = "lab_workers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lab_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("labs.id", ondelete="CASCADE"), nullable=False)
    # 1-gebaseerd. Werker 1 is de oorspronkelijke container van het lab.
    index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    container_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Container-naam én DNS-alias op het labx-labs-netwerk. Werker 1 houdt de
    # naam die het lab altijd had; de rest krijgt er "-w<n>" achter, zodat een
    # bestaande verwijzing naar een lab blijft kloppen.
    network_alias: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="creating")

    # Inrichten gebeurt per werker: elke container heeft zijn eigen pakketten
    # nodig (wat in /workspace staat is gedeeld, wat geïnstalleerd is niet).
    provision_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    provision_log: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index("idx_lab_workers_lab", "lab_id"),
        Index("idx_lab_workers_container", "container_id"),
    )
