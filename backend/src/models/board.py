# models/board.py
#
# Het agent board: een kanban-bord dat OPTIONEEL aan een lab hangt. De
# koppeling met een lab is wat het een *agent* board maakt — een ticket kan
# door de LabX-agent worden opgepakt en aangevuld, en die agent draait dan in
# het lab van het bord (zelfde sandbox, guard en allowlist als een chat).
#
# Externe koppeling (Azure DevOps / Jira) zit op het BORD, niet op het ticket:
# één bord = één externe query/project. Een ticket draagt alleen zijn eigen
# externe identiteit (external_id/key/url/rev) zodat de sync per item kan
# matchen zonder te raden.
from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

# Providers waarmee een bord kan synchroniseren. "local" = alleen LabX.
BOARD_PROVIDERS = ("local", "azure_devops", "jira")
# pull    = extern is leidend, LabX schrijft NOOIT terug
# two_way = lokale wijzigingen (velden, status, opmerkingen) gaan ook terug
#           naar de bron. Dit is de standaard: Nick heeft op 2026-08-24
#           expliciet vastgelegd dat LabX-boards naar DevOps én Jira mogen
#           schrijven. Wie een bord read-only wil, zet het op "pull".
SYNC_DIRECTIONS = ("pull", "two_way")

# Kolommen waarmee een nieuw bord start.
DEFAULT_COLUMNS = [
    {"key": "todo", "name": "Te doen"},
    {"key": "agent", "name": "Voor de agent"},
    {"key": "in_progress", "name": "Bezig"},
    {"key": "review", "name": "Review"},
    {"key": "done", "name": "Klaar", "is_done": True},
]

TICKET_AGENT_STATES = ("idle", "queued", "running", "done", "failed")


class Board(Base):
    __tablename__ = "boards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ticket-sleutelprefix, bv. "LAB" -> LAB-1, LAB-2, ...
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False, default="LAB")
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Het gekoppelde lab. Nullable: een bord mag bestaan zonder lab (dan kan
    # de agent er alleen niet in werken — elke agent-run eist een lab).
    lab_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("labs.id", ondelete="SET NULL"), nullable=True)

    # [{key, name, is_done?, wip_limit?}] — volgorde is de kolomvolgorde.
    columns: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Kolom waaruit een board-schedule werk oppakt, en waar de agent het
    # ticket naartoe zet als hij klaar is.
    agent_column: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_done_column: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Standaardinstructie die bovenop élke agent-run op dit bord komt
    # (bv. "werk in /workspace/repo, schrijf tests, push niet").
    agent_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- externe koppeling -------------------------------------------------
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    # Niet-geheime providerconfig (organisatie, project, JQL/WIQL, statusmap).
    provider_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # PAT / API-token, Fernet-versleuteld (utils/crypto.py). Nooit terug in een
    # API-response — de router geeft alleen `has_secret` prijs.
    provider_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "two_way" (default) of "pull" — zie SYNC_DIRECTIONS.
    sync_direction: Mapped[str] = mapped_column(String(16), nullable=False, default="two_way")
    # 0 = geen automatische sync; anders het interval in minuten (scheduler).
    auto_sync_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sync_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("idx_boards_lab", "lab_id"),)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    board_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False)
    # Menselijke sleutel binnen het bord (LAB-12). Uniek per bord.
    key: Mapped[str] = mapped_column(String(64), nullable=False)

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    # De SPECIFICATIE van het werk (Markdown) — wat er moet gebeuren, niet wat
    # er gebeurd is. Voortgang en bevindingen horen in TicketComment; anders
    # groeit de omschrijving vol met werklogboek en is niet meer te zien wat er
    # eigenlijk gevraagd werd.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Wanneer is dit ticket klaar? (Markdown, meestal een lijstje.) Apart veld
    # en niet onderaan de omschrijving geplakt: de agent toetst zijn werk
    # hieraan, en Azure DevOps heeft er een eigen veld voor.
    acceptance_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    # De kolom-key waar het ticket in staat.
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="todo")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    assignee: Mapped[str | None] = mapped_column(String(255), nullable=True)
    labels: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Sorteervolgorde binnen een kolom; floats zodat "tussenvoegen" geen
    # herindexering van de hele kolom vraagt.
    position: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # --- agent -------------------------------------------------------------
    agent_state: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    agent_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- externe identiteit ------------------------------------------------
    external_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Revisie/versie zoals de bron hem noemt (DevOps `rev`, Jira `updated`) —
    # waarmee de pull ziet of er extern iets veranderd is.
    external_rev: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_synced_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Lokale wijziging die nog niet is teruggeschreven (alleen relevant bij
    # sync_direction="two_way").
    dirty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index("idx_tickets_board", "board_id"),
        Index("idx_tickets_board_status", "board_id", "status"),
        Index("idx_tickets_external", "board_id", "external_id"),
    )


class TicketComment(Base):
    __tablename__ = "ticket_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    # "comment" = echte opmerking (mag naar buiten), "activity" = LabX-eigen
    # gebeurtenis (verplaatst, agent gestart) die NOOIT gepusht wordt.
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="comment")
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="user")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # INTERN = blijft in LabX en gaat nooit naar de bron. Alles wat de agent
    # schrijft is dat: een agent doet verslag van zijn werk, en dat verslag is
    # voor ons — niet voor de klant, de leverancier of wie er verder in Jira
    # meeleest. Een interne opmerking is te PROMOVEREN naar extern (dan gaat
    # hij alsnog mee met de eerstvolgende push); de omgekeerde weg bestaat niet
    # zodra hij verstuurd is, want teruggehaald krijg je hem niet.
    internal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # False = lokaal ontstaan en nog niet naar de bron gepusht.
    pushed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("idx_ticket_comments_ticket", "ticket_id"),)
