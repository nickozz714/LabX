"""
models/cluster_env_file.py — env-bestanden die bij een cluster horen.

Waarom dit bestaat: de bestanden die een applicatie lokaal laten werken staan
vaak in `.gitignore` en nergens anders. Bij IntelligentHive zegt
`components/app/.env.example` letterlijk "ask these values from another
developer", en `components/app/.env.local` wordt door de `start-dev`-skill uit
een bestaande checkout gekopieerd. Een verse kloon kan er dus niet aan komen,
hoe goed de repo verder ook is.

Een cluster is de juiste plek om ze te bewaren: hij hoort al bij één repo,
heeft al een eigen kluis, en elk branch-lab eronder heeft dezelfde bestanden
nodig. Importeer ze één keer, en elk lab dat daarna ontstaat krijgt ze.

**De inhoud staat versleuteld en gaat één kant op.** Hij wordt geschreven naar
de werkkopie in een lab, en verder nergens heen: niet naar een scherm, niet
naar een log, niet naar de context van een model. Het scherm toont alleen het
pad en de NAMEN van de sleutels -- genoeg om te zien wat je hebt, te weinig om
iets te lekken.
"""
from __future__ import annotations

from sqlalchemy import Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class ClusterEnvFile(Base):
    __tablename__ = "cluster_env_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    #: Waar het bestand in de werkkopie hoort, relatief aan de repo-wortel.
    #: Bijvoorbeeld `components/api/.env` of `components/app/.env.local`.
    pad: Mapped[str] = mapped_column(String(400), nullable=False)

    inhoud_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    #: De namen van de sleutels in het bestand, als JSON-lijst. Alleen namen:
    #: dit is wat het scherm mag zien.
    sleutels: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Eén bestand per pad per cluster. Opnieuw importeren overschrijft, want
    #: twee versies van hetzelfde pad betekent dat één ervan stil genegeerd
    #: wordt -- en dan zoek je je scheel naar welke waarde er nu geldt.
    __table_args__ = (
        UniqueConstraint("cluster_id", "pad", name="uq_cluster_env_pad"),
    )
