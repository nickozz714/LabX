"""
services/boards/sync_service.py

Synchronisatie tussen een LabX-board en zijn bron (Azure DevOps / Jira).

Volgorde is bewust **push vóór pull**: eerst wat LabX lokaal veranderde naar de
bron sturen, dan de bron als waarheid terugtrekken. Andersom zou de pull de nog
niet gepushte lokale wijziging overschrijven en die stilletjes weggooien.

Conflictregel: de bron wint, behalve voor een ticket dat nog `dirty` is (de
push is mislukt) — dat blijft lokaal staan zodat de wijziging niet verdampt en
zichtbaar blijft als openstaand verschil.

Statusmapping zit in `provider_config["state_map"]`: {kolom-key: externe
status}. Ontbreekt een kolom in de map, dan blijft het ticket in de kolom waar
het staat (pull) resp. wordt de status niet meegestuurd (push) — nooit raden.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from component_logging import get_logger
from models.board import Board, Ticket, TicketComment
from services.boards.board_service import BoardService
from services.boards.sync.base import ExternalItem, build_adapter

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BoardSyncService:
    def __init__(self, db: Session):
        self.db = db
        self.boards = BoardService(db)

    # ── mapping ─────────────────────────────────────────────────────────────

    @staticmethod
    def _state_map(board: Board) -> Dict[str, str]:
        raw = (board.provider_config or {}).get("state_map") or {}
        return {str(k): str(v) for k, v in raw.items() if v}

    def _column_for_state(self, board: Board, state: Optional[str]) -> Optional[str]:
        """Externe status -> bordkolom. Hoofdletterongevoelig, en als de
        externe status letterlijk een kolom-key is werkt dat ook zonder map."""
        if not state:
            return None
        needle = state.strip().lower()
        for column_key, external in self._state_map(board).items():
            if str(external).strip().lower() == needle:
                return column_key
        for col in (board.columns or []):
            if str(col.get("key", "")).lower() == needle or str(col.get("name", "")).lower() == needle:
                return col.get("key")
        return None

    def _state_for_column(self, board: Board, column_key: str) -> Optional[str]:
        return self._state_map(board).get(column_key)

    def _adapter(self, board: Board):
        if board.provider == "local":
            raise ValueError("Dit board is lokaal — er is geen bron om mee te synchroniseren")
        secret = None
        if board.provider_secret_encrypted:
            from utils.crypto import decrypt
            secret = decrypt(board.provider_secret_encrypted)
        return build_adapter(board.provider, board.provider_config or {}, secret)

    # ── publieke ingang ─────────────────────────────────────────────────────

    async def sync_board(self, board_id: int) -> Dict[str, Any]:
        board = self.boards.get_board(board_id)
        stats: Dict[str, Any] = {
            "board_id": board.id, "provider": board.provider,
            "direction": board.sync_direction,
            "pushed": 0, "created_external": 0, "comments_pushed": 0,
            "pulled": 0, "created_local": 0, "updated_local": 0,
            "comments_pulled": 0, "skipped_dirty": 0, "errors": [],
        }
        try:
            adapter = self._adapter(board)
        except Exception as exc:  # noqa: BLE001
            board.last_sync_error = str(exc)[:1000]
            board.updated_at = _now_iso()
            self.db.commit()
            raise

        try:
            if board.sync_direction == "two_way":
                await self._push(board, adapter, stats)
            await self._pull(board, adapter, stats)
            board.last_sync_error = None
        except Exception as exc:  # noqa: BLE001 — de fout hoort op het board, leesbaar
            board.last_sync_error = str(exc)[:1000]
            board.last_sync_at = _now_iso()
            board.updated_at = _now_iso()
            self.db.commit()
            log.warningx("Board-sync mislukt", board=board.name, error=str(exc)[:300])
            raise
        board.last_sync_at = _now_iso()
        board.updated_at = _now_iso()
        self.db.commit()
        log.infox("Board gesynchroniseerd", board=board.name, **{
            k: v for k, v in stats.items() if isinstance(v, int)})
        return stats

    # ── push ────────────────────────────────────────────────────────────────

    async def _push(self, board: Board, adapter, stats: Dict[str, Any]) -> None:
        dirty = (self.db.query(Ticket)
                 .filter(Ticket.board_id == board.id, Ticket.dirty == True)  # noqa: E712
                 .all())
        for ticket in dirty:
            state = self._state_for_column(board, ticket.status)
            try:
                if ticket.external_id:
                    item = await adapter.update_item(
                        external_id=ticket.external_id, title=ticket.title,
                        description=ticket.description, state=state,
                        priority=ticket.priority, assignee=ticket.assignee,
                        labels=list(ticket.labels or []),
                        acceptance_criteria=ticket.acceptance_criteria)
                    stats["pushed"] += 1
                else:
                    item = await adapter.create_item(
                        title=ticket.title, description=ticket.description, state=state,
                        priority=ticket.priority, assignee=ticket.assignee,
                        labels=list(ticket.labels or []),
                        acceptance_criteria=ticket.acceptance_criteria)
                    stats["created_external"] += 1
                self._apply_external_identity(ticket, board, item)
                ticket.dirty = False
                ticket.updated_at = _now_iso()
                self.db.commit()
            except Exception as exc:  # noqa: BLE001 — één ticket mag de rest niet blokkeren
                stats["errors"].append(f"{ticket.key}: {str(exc)[:300]}")
                log.warningx("Ticket pushen mislukt", ticket=ticket.key, error=str(exc)[:300])

        await self._push_comments(board, adapter, stats)

    async def _push_comments(self, board: Board, adapter, stats: Dict[str, Any]) -> None:
        """Alleen echte opmerkingen (kind="comment") die lokaal zijn ontstaan.
        LabX-activiteitsregels blijven binnen LabX — een bron volplempen met
        "verplaatst van todo naar agent" is ruis in andermans systeem."""
        rows = (self.db.query(TicketComment, Ticket)
                .join(Ticket, Ticket.id == TicketComment.ticket_id)
                .filter(Ticket.board_id == board.id,
                        TicketComment.kind == "comment",
                        TicketComment.pushed == False,  # noqa: E712
                        TicketComment.external_id.is_(None))
                .all())
        for comment, ticket in rows:
            target = ticket.external_key if board.provider == "jira" else ticket.external_id
            if not target:
                continue
            try:
                external_id = await adapter.add_comment(
                    external_id=target,
                    body=f"[LabX · {comment.author}]\n\n{comment.body}")
                comment.pushed = True
                if external_id:
                    comment.external_id = external_id
                self.db.commit()
                stats["comments_pushed"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"opmerking op {ticket.key}: {str(exc)[:200]}")

    # ── pull ────────────────────────────────────────────────────────────────

    async def _pull(self, board: Board, adapter, stats: Dict[str, Any]) -> None:
        items: List[ExternalItem] = await adapter.fetch_items(with_comments=True)
        stats["pulled"] = len(items)
        existing = {t.external_id: t for t in
                    self.db.query(Ticket).filter(Ticket.board_id == board.id,
                                                 Ticket.external_id.isnot(None)).all()}
        fallback_column = (board.columns or [{}])[0].get("key") or "todo"

        for item in items:
            ticket = existing.get(item.external_id)
            column = self._column_for_state(board, item.state)
            if ticket is None:
                ticket = self._create_from_external(board, item, column or fallback_column)
                stats["created_local"] += 1
            else:
                if ticket.dirty:
                    # De push van dit ticket is niet gelukt; de lokale
                    # wijziging is nog het enige exemplaar. Niet overschrijven.
                    stats["skipped_dirty"] += 1
                    continue
                if self._apply_external_fields(ticket, item, column):
                    stats["updated_local"] += 1
            self._apply_external_identity(ticket, board, item)
            ticket.external_synced_at = _now_iso()
            self.db.commit()
            stats["comments_pulled"] += self._merge_comments(ticket, item)

    def _create_from_external(self, board: Board, item: ExternalItem, column: str) -> Ticket:
        board.seq = int(board.seq or 0) + 1
        now = _now_iso()
        ticket = Ticket(
            board_id=board.id,
            key=f"{board.key_prefix}-{board.seq}",
            title=item.title[:512],
            description=item.description,
            acceptance_criteria=item.acceptance_criteria or None,
            status=column,
            priority=item.priority or "normal",
            assignee=item.assignee,
            labels=list(item.labels or []),
            position=self.boards.next_position(board.id, column),
            dirty=False,
            created_at=now, updated_at=now,
        )
        self.db.add(ticket)
        self.db.commit()
        self.db.refresh(ticket)
        return ticket

    @staticmethod
    def _apply_external_fields(ticket: Ticket, item: ExternalItem,
                               column: Optional[str]) -> bool:
        changed = False
        # acceptance_criteria alleen als de bron het veld kent (None = niet
        # ondersteund/niet opgevraagd) — anders zou een Jira zonder ingesteld
        # custom field de lokale criteria bij elke pull wissen.
        for attr, value in (("title", item.title[:512]),
                            ("description", item.description),
                            ("acceptance_criteria", item.acceptance_criteria),
                            ("assignee", item.assignee)):
            if value is not None and getattr(ticket, attr) != value:
                setattr(ticket, attr, value)
                changed = True
        if item.priority and ticket.priority != item.priority:
            ticket.priority = item.priority
            changed = True
        if item.labels is not None and list(ticket.labels or []) != list(item.labels):
            ticket.labels = list(item.labels)
            changed = True
        if column and ticket.status != column:
            ticket.status = column
            changed = True
        if changed:
            ticket.updated_at = _now_iso()
        return changed

    @staticmethod
    def _apply_external_identity(ticket: Ticket, board: Board, item: ExternalItem) -> None:
        ticket.external_provider = board.provider
        ticket.external_id = item.external_id
        ticket.external_key = item.external_key
        ticket.external_url = item.external_url
        ticket.external_rev = item.rev

    def _merge_comments(self, ticket: Ticket, item: ExternalItem) -> int:
        if not item.comments:
            return 0
        known = {c.external_id for c in
                 self.db.query(TicketComment)
                 .filter(TicketComment.ticket_id == ticket.id,
                         TicketComment.external_id.isnot(None)).all()}
        added = 0
        for c in item.comments:
            if c.external_id in known:
                continue
            # Een opmerking die LabX zelf heeft gepusht komt terug in de pull;
            # de prefix herkent hem zodat hij niet dubbel in de tijdlijn staat.
            if c.body.startswith("[LabX · "):
                continue
            self.db.add(TicketComment(
                ticket_id=ticket.id, kind="comment", author=c.author,
                body=c.body, external_id=c.external_id, pushed=True,
                created_at=c.created_at or _now_iso()))
            added += 1
        if added:
            self.db.commit()
        return added

    # ── automatische sync (scheduler) ───────────────────────────────────────

    async def tick(self) -> None:
        """Draait elke minuut vanuit de DynamicScheduler: elk board met
        auto_sync_minutes > 0 dat lang genoeg geleden is gesynchroniseerd."""
        now = datetime.now(timezone.utc)
        due: List[int] = []
        for board in self.db.query(Board).filter(Board.auto_sync_minutes > 0).all():
            if board.provider == "local":
                continue
            if not board.last_sync_at:
                due.append(board.id)
                continue
            try:
                last = datetime.fromisoformat(board.last_sync_at)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
            except ValueError:
                due.append(board.id)
                continue
            if (now - last).total_seconds() >= board.auto_sync_minutes * 60:
                due.append(board.id)

        for board_id in due:
            try:
                await self.sync_board(board_id)
            except Exception:  # noqa: BLE001 — al gelogd en op het board gezet
                continue
