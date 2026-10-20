# models/cluster_bundel.py
#
# Een zak bestanden die bij een cluster hoort, met een opdracht erbij.
#
# Waarom dit bestaat: een vaste dienst begint leeg. De Cosmos-emulator van
# IntelligentHive hoort gevuld te worden uit de bron-database, maar die zit
# achter een accountfirewall -- en dan sta je stil, terwijl de data gewoon
# als export op je schijf staat. Dat bestand het lab in krijgen kon alleen
# via een lab (de bestandsbrowser), en een vaste dienst IS geen lab.
#
# Waarom een volume en geen map op de server: het cluster kan op een andere
# machine draaien. Een volume is het enige dat daar ook werkt, en het is het
# enige dat een wegwerpcontainer náást de dienst kan inzien.
#
# Wat er NIET gebeurt: de INHOUD gaat nooit naar het model. Het ziet de
# bestandsnamen en hun omvang, en schrijft een script dat de inhoud ter
# plekke leest. Een dump van een klant hoort in een container, niet in de
# context van een taalmodel.
from __future__ import annotations

from sqlalchemy import Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

#: Wat er uitgepakt kan worden. Een .zip omdat dat is wat een browser en een
#: collega je geven; tar omdat een export uit een Unix-wereld zo komt.
TOEGESTANE_SOORTEN = (".zip", ".tar", ".tar.gz", ".tgz")
#: Waar de bundel in een actiecontainer hangt. Vast, zodat een script dat het
#: model schrijft niet hoeft te raden waar hij moet kijken.
KOPPELPUNT = "/bundel"


class ClusterBundel(Base):
    __tablename__ = "cluster_bundels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    #: Hoe jij hem noemt. Standaard de bestandsnaam zonder extensie.
    naam: Mapped[str] = mapped_column(String(120), nullable=False)
    #: De naam van het geüploade archief, zoals het binnenkwam.
    bestandsnaam: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Het docker-volume waarin hij uitgepakt staat. Eén per bundel, zodat
    #: weggooien ook echt weggooit en niet half.
    volume: Mapped[str] = mapped_column(String(160), nullable=False)
    bytes_uitgepakt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    aantal_bestanden: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: De paden in de bundel met hun omvang: [{"pad": ..., "bytes": ...}].
    #: Afgekapt -- dit is er om te kunnen zien WAT erin zit en om het model
    #: een kaart te geven, niet om een bestandsbrowser te zijn.
    boom: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    #: Wat ermee moet gebeuren, in jouw woorden. "Zet deze dump in de
    #: Cosmos-emulator." Hoort bij de bundel en niet bij één handeling: je
    #: bedoelt hetzelfde als je hem een week later opnieuw toepast.
    instructie: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: De laatste keer dat hij ergens op toegepast is, met uitslag.
    laatst_toegepast: Mapped[str | None] = mapped_column(String(64), nullable=True)
    laatste_exitcode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    laatste_uitvoer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
