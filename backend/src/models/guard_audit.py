# models/guard_audit.py
#
# Het audit-spoor van de data-guard: wat er uit de container kwam, en wat het
# model daarvan te zien kreeg.
#
# Waarom dit er moest komen. Het oude spoor bewaarde alleen een classificatie,
# een reden en het AANTAL bytes. Bij een blokkade kon je daarmee niet nagaan of
# er terecht iets is tegengehouden — je zag "value_revealing: leest ruwe
# bestandsinhoud" bij een commando dat 13 bytes teruggaf, en verder niets. Je
# kon dus niet vaststellen dat de guard ernaast zat, en al helemaal niet dat hij
# iets liet passeren dat er niet doorheen had gemogen. Een audit die de vraag
# "is dit goed gegaan?" niet kan beantwoorden, is geen audit.
#
# Nu staan er twee teksten in: de ORIGINELE uitvoer, en wat er na maskeren of
# blokkeren daadwerkelijk naar het model is gegaan. Naast elkaar leesbaar.
#
# **Dat is gevoelig, en daar is naar gehandeld.** Dit spoor bevat per definitie
# precies de gegevens die de guard tegenhield. Beide teksten staan daarom
# Fernet-versleuteld (utils/crypto, dezelfde sleutel als tokens en
# board-secrets), ze gaan nooit terug naar een model, ze zijn alleen op te
# vragen door een ingelogde gebruiker, en ze worden na een instelbare termijn
# opgeruimd. Het alternatief — niets bewaren — maakt de guard oncontroleerbaar,
# en een guard die niemand kan controleren is een geloofsartikel.
from __future__ import annotations

from sqlalchemy import Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

# doorgelaten — onveranderd naar het model
# gemaskeerd  — treffers onleesbaar gemaakt, de rest is doorgegaan
# geblokkeerd — de uitvoer is vervangen door een uitleg
# geweigerd   — het COMMANDO is niet uitgevoerd
UITKOMSTEN = ("doorgelaten", "gemaskeerd", "geblokkeerd", "geweigerd")


class GuardAudit(Base):
    __tablename__ = "guard_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(String(64), nullable=False)

    lab_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lab_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    worker_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Het commando zoals het is uitgevoerd. Geheimen staan hier al als
    # `${LABX_SECRET_…}` in — zie services/lab/secrets.py.
    command: Mapped[str | None] = mapped_column(Text, nullable=True)

    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="doorgelaten")
    # Wat er is gevonden: [{regel, categorie, actie, aantal}]. Dit is de laag
    # waarop je later kunt zien dát een regel te vaak of te weinig aanslaat.
    findings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Het oordeel van het lokale model, als dat gedraaid heeft.
    llm_verdict: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Wat de agent verklaarde op te halen (metadata / telling / code /
    # klantdata), en of de uitvoer daarbij paste. Een verklaring verruimt wat
    # er op de opdracht mag; deze twee kolommen zijn wat die verruiming
    # controleerbaar maakt. Zonder ze zou "ik haalde alleen metadata op" een
    # bewering blijven die niemand kan nakijken.
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    intent_mismatch: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Beide Fernet-versleuteld. Zie de kop van dit bestand.
    original_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    bytes_original: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_delivered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("idx_guard_audit_ts", "ts"),
        Index("idx_guard_audit_lab", "lab_id", "ts"),
        Index("idx_guard_audit_outcome", "outcome"),
    )
