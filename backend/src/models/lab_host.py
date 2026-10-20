# models/lab_host.py
#
# Een computer in je netwerk die labs kan draaien.
#
# Waarom dit bestaat: één server die alles doet, krijgt het op zijn staart.
# Een pc die toch aan staat kan er labs bij nemen, en dan verdubbel je je
# capaciteit zonder iets te kopen. Later is "een VM in Azure" precies
# hetzelfde ding: een adres met een Docker-daemon erachter.
#
# DE TAAKVERDELING, en waarom hij zo is:
#
# - **Containers aansturen gaat over SSH.** `DOCKER_HOST=ssh://nick@werkpc`
#   en de docker-CLI doet de rest. Geen open poort, geen eigen protocol, geen
#   tweede ding dat versleuteling en authenticatie goed moet doen -- dat is
#   allemaal SSH, en dat is al dertig jaar goed.
#
# - **De agent op die pc doet drie dingen die SSH NIET kan**: zich aanmelden
#   (Docker broadcast niets, dus zonder dit typ je IP-adressen over), de
#   machine wakker houden zolang er werk draait, en melden hoe het ermee
#   staat. Hij voert géén commando's uit. Dat is de kern van het ontwerp: wie
#   de agent overneemt, kan niets starten -- de gevaarlijke kant zit achter
#   een SSH-sleutel die jij beheert.
#
# - **De pc meldt zich bij LabX, niet andersom.** De backend draait in een
#   container en ziet het LAN niet: multicast komt de bridge niet over (op
#   onze eigen api-container staat IGMP alleen op `lo`). Zoeken vanaf LabX
#   zou dus sowieso niet werken; aanmelden vanaf de pc wel.
from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

#: `nieuw`      — heeft zich gemeld, nog niet gekoppeld (je moet de code bevestigen)
#: `gekoppeld`  — mag labs draaien
#: `geweigerd`  — bewust afgewezen; meldt zich wel, krijgt niets
HOST_STATUSSEN = ("nieuw", "gekoppeld", "geweigerd")


class LabHost(Base):
    __tablename__ = "lab_hosts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: Hoe de machine zichzelf noemt. Niet uniek afgedwongen: twee pc's met
    #: dezelfde naam is een probleem van de gebruiker, en een aanmelding
    #: weigeren omdat de naam botst is onbehulpzaam.
    naam: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    #: Waarmee de agent zich identificeert tussen twee aanmeldingen door. Blijft
    #: gelijk als het IP verandert -- een laptop op wifi krijgt een ander adres
    #: en moet niet als nieuwe machine binnenkomen.
    machine_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="nieuw")

    #: Waar de docker-CLI heen praat: `ssh://gebruiker@host`. Dit is het enige
    #: dat LabX nodig heeft om er labs te draaien.
    docker_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Het adres waarop de agent zelf te bereiken is (voor wakker houden en
    #: statusvragen). Komt uit de aanmelding en kan dus wijzigen.
    agent_url: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: Wat de machine te bieden heeft, zoals de agent het laatst meldde.
    cpus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    geheugen_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vrij_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schijf_vrij_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    load: Mapped[str | None] = mapped_column(String(32), nullable=True)
    docker_versie: Mapped[str | None] = mapped_column(String(64), nullable=True)
    besturingssysteem: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Hoeveel labs hier maximaal mogen. Leeg = geen eigen plafond; dan telt
    #: alleen wat de machine aankan.
    max_labs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Houdt de agent de machine wakker zolang hier labs draaien? Een pc die
    #: na tien minuten in slaap valt is geen lab-host, en dat wil je wéten en
    #: niet merken aan een nachtrun die halverwege stopt.
    blijft_wakker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: Hoeveel modelcontainers (Ollama) hier mogen draaien voor de
    #: data-guard. 0 = geen; deze machine doet alleen labs.
    #:
    #: Waarom dit per machine instelbaar is en geen automatisme: een model
    #: vreet geheugen en CPU, en jij weet wat een machine er nog bij kan
    #: hebben. LabX kan dat raden, maar raden op iemands werkcomputer is
    #: precies wat je niet wilt.
    ollama_runners: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: De koppelcode die de agent op het scherm van die pc toont. Verdwijnt
    #: zodra er gekoppeld is -- een code die blijft staan is een wachtwoord
    #: dat niemand meer vervangt.
    koppelcode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Waarmee de agent op die machine een verzoek van DEZE LabX herkent.
    #: Hij krijgt hem pas als een mens de koppelcode heeft bevestigd, en
    #: daarom is dit geen tweede sleutel naast de koppelcode maar het gevolg
    #: ervan: wat zich meldt mag zichzelf nog steeds niet toelaten.
    agent_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    laatst_gezien: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notitie: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("idx_lab_hosts_machine", "machine_id"),)
