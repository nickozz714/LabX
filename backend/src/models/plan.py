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
# Meerdere planningen mogen naast elkaar bestaan en tegelijk lopen, en sinds
# een lab meerdere werkers heeft mogen ook de TICKETS BINNEN één planning naast
# elkaar draaien — één per vrije werker. Dat was de hele reden voor werkers:
# vijf entiteiten die niets met elkaar te maken hebben, hoeven niet op elkaar
# te wachten.
#
# Wat dat mogelijk maakt zonder dat ze elkaar slopen, is `plan_claims`: een
# agent meldt waar hij aan zit ("fabric:acc:PL_RUN_SILVER") en LabX houdt een
# ticket tegen dat hetzelfde wil. Werkers delen namelijk /workspace, en twee
# agents in dezelfde pipeline of hetzelfde bestand is geen theoretisch risico.
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
    # Gepauzeerd tot dit moment (ISO). Sinds tickets naast elkaar kunnen lopen
    # is dit alleen nog voor een planning die ALS GEHEEL stilstaat (handmatig,
    # of een blokkade); een agent die op een pipeline wacht parkeert tegenwoordig
    # zijn eigen ITEM (TicketPlanItem.resume_at) en laat de planning doorlopen.
    resume_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Hoeveel tickets van deze planning tegelijk mogen draaien. NULL = zoveel
    # als er werkers vrij zijn; dan bepaalt het maximum van het lab de breedte
    # en hoeft er per planning niets ingesteld te worden.
    max_parallel: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # "gedeeld" (standaard) of "apart".
    #
    # Werkers delen /workspace — dat IS een lab: één repo, één az-sessie, één
    # browserprofiel. Voor werk dat vooral API's aanroept (Fabric, Azure) is
    # parallel draaien daar prima. Zitten de tickets in BESTANDEN, dan is het
    # dat niet, en krijgt elk ticket een eigen map onder
    # /workspace/.plan-<id>/<TICKET> om in te werken.
    #
    # Let op wat dat wel en niet is: scheiding, geen isolatie. De agent kan nog
    # steeds overal bij; hij krijgt een werkmap toegewezen en de instructie er
    # te blijven. Wie echte isolatie wil, geeft de planningen aparte labs.
    workspace_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="gedeeld")

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
    # Dit ticket ligt stil tot dit moment (ISO) omdat de agent op iets langs
    # loopends wacht. Het ITEM wacht, niet de planning: de werker komt vrij en
    # het volgende ticket begint meteen. Dat is het verschil met vroeger, toen
    # wachten op een pipeline de hele rij stillegde.
    resume_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # WAAROM dit ticket stilligt, in de woorden van de agent ("wacht op
    # pipeline X", "gebruikslimiet tot 13:40"). Zonder dit staat er in de UI
    # alleen "waiting" bij vier tickets tegelijk, en is niet te zien of er iets
    # loopt, iets stukzit, of er gewoon niets gebeurt.
    wait_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_plan_items_plan", "plan_id"),
        Index("idx_plan_items_ticket", "ticket_id"),
    )


class PlanClaim(Base):
    """Waar een draaiend ticket aan zit.

    Het probleem dat dit oplost: zodra tickets naast elkaar draaien, kan LabX
    niet weten of dat veilig is. Afhankelijkheden vooraf invullen werkt bij drie
    tickets en niet bij twintig, en een LLM die het vooraf inschat, gokt.

    De agent weet het wel — hij staat op het punt die pipeline aan te passen.
    Dus meldt hij het: `board__claim(["fabric:acc:PL_RUN_SILVER"])`. Zolang hij
    hem vasthoudt, start LabX geen ticket dat dezelfde bron eerder claimde, en
    krijgt een agent die hem alsnog opvraagt te horen wie hem heeft.

    Een claim overleeft `board__wait_until` met opzet: wie een uur op een
    pipeline wacht, wil juist niet dat er ondertussen iemand anders in zit. Hij
    gaat pas los als het ticket klaar is, mislukt of wordt overgeslagen.
    """
    __tablename__ = "plan_claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ticket_id: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Vrije tekst, genormaliseerd naar kleine letters. Een afspraak, geen
    # opsomming: "fabric:acc:PL_RUN_SILVER", "repo:/workspace/silver/product".
    # Wat het betekent bepaalt de agent; wat het DOET is botsingen zichtbaar
    # maken, en daarvoor is alleen gelijkheid nodig.
    resource: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    released_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_plan_claims_resource", "resource"),
        Index("idx_plan_claims_item", "plan_item_id"),
        Index("idx_plan_claims_ticket", "ticket_id"),
    )
