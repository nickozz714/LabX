# models/cluster_dienst_actie.py
#
# Een knop bij een vaste dienst, met het script dat eronder zit.
#
# Waarom dit bestaat: een vaste dienst is geen lab. Je kunt er niet in
# chatten, er staat geen agent in, en toch wil je er dingen MEE doen --
# "maak Cosmos leeg", "laat de containers zien", "zet de seed opnieuw".
# Dat zijn steeds dezelfde handelingen, en ze staan nu nergens: je typt ze
# over uit een terminal die je een week geleden openhad.
#
# Waarom het model ze schrijft en niet wij: welke handelingen zinnig zijn
# hangt af van WAT er draait. Een Cosmos-emulator heeft andere knoppen dan
# een Redis of een Mailpit, en een vaste lijst in LabX zou voor allebei
# verkeerd zijn. Het model kent het image en bedenkt de knoppen erbij; jij
# ziet het script voordat je erop drukt, en kunt het aanpassen.
from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

#: `in`    — `docker exec` IN de dienst zelf. Goed voor gereedschap dat in het
#:           image zit (`redis-cli`, `psql`).
#: `naast` — een wegwerpcontainer op het clusternetwerk, die de dienst op naam
#:           bereikt. Nodig zodra je een client nodig hebt die in de dienst
#:           niet zit -- de Cosmos-emulator heeft geen curl, laat staan een
#:           SDK, en toch is "leegmaken" juist dáár wat je wilt.
ACTIE_PLEKKEN = ("in", "naast")

#: Het image voor een `naast`-actie als het model er geen kiest. Klein, en
#: python + pip is genoeg om tegen vrijwel elke dienst te praten.
STANDAARD_HULPIMAGE = "python:3.12-slim"


class ClusterDienstActie(Base):
    __tablename__ = "cluster_dienst_acties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dienst_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    #: Eén zin: wat er gebeurt als je erop drukt. Staat onder de knop, want
    #: een knop met alleen "Leegmaken" erop zegt niet WAT er leeg gaat.
    uitleg: Mapped[str | None] = mapped_column(String(400), nullable=True)
    #: `in` of `naast` (zie ACTIE_PLEKKEN).
    plek: Mapped[str] = mapped_column(String(8), nullable=False, default="in")
    #: Het image voor een `naast`-actie. Leeg = STANDAARD_HULPIMAGE.
    image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Het script, uitgevoerd met `sh -c`. Staat in het volle zicht op het
    #: scherm: een knop waarvan je niet kunt zien wat hij doet, druk je niet
    #: in -- zeker niet als een model hem geschreven heeft.
    script: Mapped[str] = mapped_column(Text, nullable=False)
    #: Iets dat je niet terugdraait (leegmaken, verwijderen). Krijgt een
    #: bevestiging en een andere kleur.
    gevaarlijk: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    #: `model` — voorgesteld bij een verkenningsronde van de dienst.
    #: `zelf`  — jij hebt hem getypt of bijgesteld.
    #: `chat`  — uit een chatopdracht bewaard.
    bron: Mapped[str] = mapped_column(String(8), nullable=False, default="model")
    #: De laatste keer dat hij draaide, met uitslag. Zodat je bij een knop die
    #: je zelden gebruikt kunt zien dat hij vorige keer wél werkte.
    laatst_gedraaid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    laatste_exitcode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    laatste_uitvoer: Mapped[str | None] = mapped_column(Text, nullable=True)
    volgorde: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
