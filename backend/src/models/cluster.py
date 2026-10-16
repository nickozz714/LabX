# models/cluster.py
#
# Een development cluster: vaste diensten die blijven staan, en branch-labs
# die komen en gaan.
#
# Waarom dit een eigen ding is en geen slim lab: de vaste diensten horen bij
# GEEN branch. De Cosmos-emulator moet blijven draaien terwijl je drie
# branch-labs weggooit en vier nieuwe maakt. Dat is precies het verschil
# tussen een cluster en een lab, en de reden dat het hier apart staat.
#
# Wat het NIET is: een tweede soort lab. Een branch-lab blijft een gewoon
# LabX-lab met een branch en een cluster erbij, zodat werkers, data-guard,
# geheimen en verplaatsen op één plek geregeld blijven.
from __future__ import annotations

from sqlalchemy import Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

#: `nieuw`     — bestaat, nog niets gestart
#: `draait`    — de vaste diensten staan
#: `fout`      — een vaste dienst kwam niet op; staat erbij waarom
CLUSTER_STATUSSEN = ("nieuw", "draait", "fout")


class Cluster(Base):
    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    naam: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    #: Op welke machine het hele cluster draait. NULL = deze server. Eén
    #: cluster hoort bij één machine: de vaste diensten en de branch-labs
    #: moeten elkaar over een lokaal netwerk kunnen vinden, en dat kan niet
    #: als ze verspreid staan.
    host_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    #: Het docker-netwerk waarop alles van dit cluster zit. Afgeleid van de
    #: naam, maar opgeslagen zodat hernoemen het netwerk niet kwijtraakt.
    netwerk: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="nieuw")
    fout: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── wat een branch-lab nodig heeft (stap 2) ──────────────────────────────
    #
    # Deze staan er nu al omdat ze bij het CLUSTER horen en niet bij een lab:
    # je schrijft één keer op waar de repo staat en hoe de applicatie start,
    # en elk branch-lab gebruikt dat. Een tweede migratie ervoor zou geen
    # ander model opleveren.
    repo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: De naam van het geheim in de kluis waar het toegangstoken staat. NIET
    #: het token zelf: dat hoort in de kluis, met alles wat daar al voor
    #: geregeld is (versleuteling, maskeren in uitvoer, vervaltijd).
    repo_token_geheim: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: Het image waarin een branch-lab draait. IntelligentHive heeft Node en
    #: yarn 4 nodig; het standaard python-image volstaat daar niet.
    lab_image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Bouwen en starten, één keer opgeschreven voor het hele cluster.
    start_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Welk env-profiel de front-end gebruikt (debug, realestate, ...). Een
    #: instelling, want die keuze hoort niet in een script vastgeroest.
    env_profiel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Omgevingsvariabelen die élk branch-lab krijgt, bovenop zijn eigen.
    #: Hier staat bijvoorbeeld COSMOS_ENDPOINT, zodat elke branch dezelfde
    #: vaste dienst vindt zonder dat iemand dat per lab overtypt.
    omgeving: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    notitie: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)


#: `gestopt` — bestaat als rij, draait niet
#: `draait`  — container is op
#: `fout`    — gestart maar gevallen; `fout` zegt waarom
DIENST_STATUSSEN = ("gestopt", "draait", "fout")


class ClusterDienst(Base):
    """Een vaste container in een cluster — de Cosmos-emulator bijvoorbeeld.

    Hij hoort bij het cluster en niet bij een branch, en blijft dus staan als
    branch-labs verdwijnen. Opnieuw opbouwen vanaf een andere branch is
    gewoon: image wijzigen en herstarten.
    """

    __tablename__ = "cluster_diensten"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    #: Ook de DNS-naam op het clusternetwerk. Een branch-lab praat dus tegen
    #: `ih-cosmos:8081` en hoeft geen adres of poort te kennen.
    naam: Mapped[str] = mapped_column(String(120), nullable=False)
    image: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Poorten die naar BUITEN gepubliceerd worden, op de loopback van de
    #: clustermachine. Voor onderling verkeer is dit niet nodig -- dat gaat
    #: over het netwerk, op naam.
    #: Welke poorten IN de container naar buiten moeten. Welk nummer dat op
    #: de machine wordt, bepaalt LabX (zie `poort_map`) -- anders botst de
    #: emulator op 8081 met het eerste het beste dat daar al luistert, en dat
    #: is precies wat er bij de eerste proef gebeurde.
    poorten: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    #: {containerpoort: machinepoort}, toegekend bij het starten en bewaard,
    #: zodat het adres niet verandert zolang de dienst bestaat.
    poort_map: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    omgeving: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    container_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="gestopt")
    fout: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
