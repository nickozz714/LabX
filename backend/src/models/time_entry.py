# models/time_entry.py — tijdregels: hoe lang er aan een ticket is gewerkt.
#
# Eén tabel voor drie heel verschillende soorten getallen, met `kind` als het
# onderscheid. Dat is met opzet: ze horen in één overzicht naast elkaar, maar
# ze mogen NOOIT stilzwijgend bij elkaar opgeteld worden.
#
# - "gemeten"  — de wandklok van een agent-run (start tot eind). Hard, maar het
#                is machinetijd: een run die veertig minuten op een pipeline
#                wacht, meet veertig minuten waarin niemand iets deed. Dit is
#                dus GEEN urenstaat, het is context bij wat er speelde.
# - "geschat"  — een schatting van de tijd die de GEBRUIKER zelf kwijt was:
#                afgeleid uit zijn eigen berichten en opmerkingen. Dit is het
#                getal waar een uurregel uit komt, en precies daarom staat er
#                altijd bij dat het een schatting is.
# - "gemeld"   — de agent heeft zelf gezegd hoe lang iets duurde en waar het
#                bij hoorde.
# - "handmatig" — door de gebruiker ingevoerd of gecorrigeerd. Wint altijd.
from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

SOORTEN = ("gemeten", "geschat", "gemeld", "handmatig")


class TimeEntry(Base):
    __tablename__ = "time_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    board_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False)
    # Een regel hoort bij een ticket, maar hoeft dat niet: overleg of
    # administratie die nergens op het bord landt is ook tijd.
    ticket_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)
    # Overgenomen van het ticket op het moment van schrijven, niet live
    # opgezocht: verandert het project van een ticket later, dan blijft een
    # al geschreven uurregel staan waar hij hoorde. Anders verspringt je
    # verleden elke keer dat je iets opruimt.
    project: Mapped[str | None] = mapped_column(String(128), nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)

    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="handmatig")
    minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Waar dit vandaan komt, zodat een getal navraagbaar is: het run-id bij
    # "gemeten", de thread bij "geschat".
    source_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    started_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ended_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # De dag waarop deze tijd telt (YYYY-MM-DD). Apart van started_at, want
    # een urenstaat gaat per dag en niet per tijdstip, en een correctie mag de
    # dag verzetten zonder het oorspronkelijke tijdstip te vervalsen.
    day: Mapped[str] = mapped_column(String(10), nullable=False)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Afgetekend: deze regel heb je gezien en goedgekeurd om te schrijven.
    # Een schatting die je nooit bekeken hebt, hoort niet op een factuur.
    approved: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index("idx_time_board_day", "board_id", "day"),
        Index("idx_time_ticket", "ticket_id"),
        # Een gemeten regel hoort precies één keer per run te bestaan; de
        # verzamelaar draait herhaaldelijk en moet idempotent zijn.
        Index("idx_time_run", "source_run_id"),
    )
