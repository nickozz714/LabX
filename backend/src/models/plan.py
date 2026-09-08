# models/plan.py
#
# Een PLANNING is een geordende set tickets die de agent achter elkaar
# afwerkt — een run met een begin, een verloop en een afloop, zoals een
# pipeline-run. Twee dingen die je daarvoor nodig hebt en die een losse
# "pak N tickets uit kolom X" niet geeft:
#
# 1. **Volgorde die van jou is.** Je stelt zelf samen wát er in gaat en in
#    welke volgorde. "Pak de hele kolom op" is niet iets aparts maar dezelfde
#    planning, automatisch gevuld met de kolom in bordvolgorde.
# 2. **Een verloop dat je kunt volgen en sturen.** Per ticket zie je of het
#    wacht, draait, klaar is of is overgeslagen — en je kunt pauzeren,
#    hervatten of afbreken zonder de rest kwijt te raken.
#
# Meerdere planningen mogen naast elkaar bestaan en tegelijk lopen. Binnen ÉÉN
# lab draait er voorlopig één tegelijk: alle tickets van een bord werken in
# dezelfde container, en twee agents die daar tegelijk in graaien vechten om
# dezelfde bestanden en processen. Zodra een lab meerdere werkers kan hebben,
# is dat de plek waar die grens opgerekt wordt (zie plan_service).
from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

# draft     — samengesteld, nog niet losgelaten
# scheduled — wacht op zijn tijdstip
# running   — er is een ticket bezig, of het volgende staat klaar
# paused    — door jou stilgezet, of vanzelf bij een geblokkeerd ticket
# done      — alles afgewerkt (ook als er onderweg iets mislukte)
# cancelled — afgebroken; wat al klaar was blijft klaar
PLAN_STATES = ("draft", "scheduled", "running", "paused", "done", "cancelled")

# waiting  — nog niet aan de beurt
# blocked  — wacht op een ticket dat nog niet klaar is (zet de planning stil)
# running  — de agent werkt eraan
# done     — de run is goed afgelopen
# failed   — de run is stukgelopen
# skipped  — overgeslagen (handmatig, of het ticket bestond niet meer)
PLAN_ITEM_STATES = ("waiting", "blocked", "running", "done", "failed", "skipped")


class TicketPlan(Base):
    __tablename__ = "ticket_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    board_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")

    # Leeg = meteen beginnen. Anders een tijdstip (ISO): de scheduler pakt hem
    # op zodra dat gepasseerd is.
    start_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Waar deze planning vandaan komt: "handmatig" (eigen selectie) of
    # "kolom" (de hele agent-kolom). Puur ter herkenning in het overzicht.
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="handmatig")
    # Instructie die bovenop élke run in deze planning komt, naast die van het
    # bord — bv. "alleen TST, niet ACC".
    instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Waarom hij stilstaat (geblokkeerd ticket, handmatig gepauzeerd, fout).
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_plans_board", "board_id"),
        Index("idx_plans_state", "state"),
    )


class TicketPlanItem(Base):
    __tablename__ = "ticket_plan_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ticket_plans.id", ondelete="CASCADE"), nullable=False)
    ticket_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    # Volgorde binnen de planning. Float, zodat herschikken geen hernummering
    # van de hele lijst vraagt — zelfde truc als Ticket.position.
    position: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="waiting")

    # De achtergrondrun die dit ticket heeft uitgevoerd, zodat het overzicht
    # naar het echte verloop kan wijzen in plaats van naar een samenvatting.
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # In welke werker (container) van het lab dit ticket draait/draaide. Dit
    # is ook wat "bezet" betekent: zolang hier een lopend item aan hangt, is
    # die werker niet vrij voor een andere planning.
    worker_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_plan_items_plan", "plan_id"),
        Index("idx_plan_items_ticket", "ticket_id"),
    )
