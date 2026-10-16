# models/model_runner.py
#
# Eén draaiende modelcontainer (Ollama) in de pool.
#
# Waarom dit bestaat: de data-guard vraagt een klein lokaal model om een
# tweede mening, en dat model draaide op één plek -- de server. Die server
# doet ook alle labs, dus een drukke guard-ronde ging ten koste van het werk
# zelf. Machines in je netwerk staan er intussen bij; die kunnen dit prima
# doen.
#
# EEN RUNNER IS WEGWERPBAAR. Hij bevat geen staat die ertoe doet: het model
# staat in een volume op zijn eigen machine, en een verzoek dat misgaat gaat
# gewoon naar een andere runner. Daarom staat hier geen "herstel"-logica maar
# alleen wat nodig is om hem terug te vinden en op te ruimen.
from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

#: `startend`  — container is aangemaakt, het model wordt nog geladen
#: `klaar`     — antwoordt op /api/tags en heeft het model
#: `fout`      — gestart maar onbruikbaar; blijft staan zodat je ziet waarom
#: `gestopt`   — bewust weggehaald
RUNNER_STATUSSEN = ("startend", "klaar", "fout", "gestopt")


class ModelRunner(Base):
    __tablename__ = "model_runners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: Op welke lab-host hij draait. NULL = op deze server.
    host_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    naam: Mapped[str] = mapped_column(String(255), nullable=False)
    container_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: De poort die op de loopback van DIE machine gepubliceerd is. Daar legt
    #: LabX een SSH-tunnel naartoe -- net als bij een labpoort, en om dezelfde
    #: reden: er hoort niets van dit alles op je LAN te staan.
    verre_poort: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="startend")
    fout: Mapped[str | None] = mapped_column(String(500), nullable=True)
    laatst_gebruikt: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
