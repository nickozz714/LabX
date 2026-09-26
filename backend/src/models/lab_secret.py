# models/lab_secret.py
#
# Geheimen van een lab: tokens, sleutels, wachtwoorden.
#
# Het probleem dat dit oplost. Een agent die met Fabric of Azure werkt, haalt
# eerst een access-token op en gebruikt dat daarna in tien commando's. Die
# commando's zijn TEKST: ze staan in het audit-spoor, ze gaan als tool-invoer
# terug naar het model, en ze komen in de uitvoer terecht zodra een script iets
# echoot. Zo reisde er een geldig OAuth-token mee door de hele keten — niet
# omdat iemand slordig was, maar omdat een token in een shell-commando nu
# eenmaal tekst is.
#
# Met een geheim in deze tabel schrijft de agent `{{secret:fabric}}` in plaats
# van de waarde. LabX vervangt die verwijzing pas IN de container, en zelfs
# daar niet door de waarde: het commando krijgt `"$LABX_SECRET_FABRIC"` en de
# waarde komt uit een bestand dat alleen root in die container kan lezen. De
# waarde staat dus nergens op een commandoregel — niet in het audit-spoor, niet
# in `ps` op de host, niet in `ps` in de container, en niet in de prompt.
#
# Twee soorten:
# - `waarde`   — je plakt hem er zelf in (een API-sleutel die niet verloopt).
# - `commando` — LabX draait een commando IN het lab om hem te maken
#                (`az account get-access-token … -o tsv`). Dat is de goede
#                vorm voor alles wat verloopt: het geheim wordt vers gehaald
#                wanneer het te oud is, zonder dat iemand iets bijwerkt.
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

SECRET_SOORTEN = ("waarde", "commando")


class LabSecret(Base):
    __tablename__ = "lab_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lab_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("labs.id", ondelete="CASCADE"), nullable=False)
    # De naam waarmee de agent hem aanroept: {{secret:fabric}}. Alleen letters,
    # cijfers, - en _ , want hij wordt ook een omgevingsvariabele.
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="waarde")
    # Fernet-versleuteld (utils/crypto). Komt NOOIT terug in een API-antwoord;
    # de router geeft alleen prijs dát er een waarde is.
    value_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Bij kind="commando": wat er in het lab gedraaid wordt om de waarde te
    # maken. Dit is geen geheim en mag gewoon zichtbaar zijn.
    produce_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Hoe lang een via `commando` gemaakte waarde bruikbaar blijft. Azure-
    # tokens leven een uur; 50 minuten laat ruimte voor een langlopend script
    # dat hem aan het begin ophaalt.
    ttl_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    refreshed_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_used_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Dit geheim komt van een Azure-profiel en wordt daar ook weer ververst.
    # Zonder deze verwijzing zou LabX na een uur een verlopen token blijven
    # aanbieden: een geheim van het soort `waarde` ververst zichzelf niet, en
    # een token van een managed identity leeft een uur.
    azure_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index("idx_lab_secrets_lab", "lab_id"),
        Index("idx_lab_secrets_naam", "lab_id", "name", unique=True),
    )
