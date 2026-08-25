"""
services/boards/board_service.py

CRUD + ordening voor boards en tickets. Alles wat een ticket verandert loopt
hier langs, om drie redenen die je niet in de router wilt herhalen:

1. **Sleuteluitgifte** — `Board.seq` is de teller achter LAB-1, LAB-2, ...
2. **Vuil-markering** — elke lokale wijziging aan een gesynchroniseerd ticket
   zet `dirty=True`, zodat de push weet wat er terug moet (en de pull weet dat
   hij niet zomaar mag overschrijven).
3. **Activiteit** — statuswissels en agent-acties landen als `activity`-regel
   op het ticket, zodat het bord ook zonder chat te lezen is.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models.board import (
    BOARD_PROVIDERS, DEFAULT_COLUMNS, SYNC_DIRECTIONS, Board, Ticket, TicketComment,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BoardService:
    def __init__(self, db: Session):
        self.db = db

    # ── boards ──────────────────────────────────────────────────────────────

    def list_boards(self) -> List[Board]:
        return self.db.query(Board).order_by(Board.name.asc()).all()

    def get_board(self, board_id: int) -> Board:
        b = self.db.get(Board, board_id)
        if not b:
            raise HTTPException(status_code=404, detail="Board niet gevonden")
        return b

    def board_for_lab(self, lab_id: str) -> Optional[Board]:
        """Het bord dat aan dit lab hangt (het eerste, als er meerdere zijn) —
        de ingang voor de agent-tools in de MCP-gateway."""
        if not lab_id:
            return None
        return (self.db.query(Board).filter(Board.lab_id == lab_id)
                .order_by(Board.id.asc()).first())

    def create_board(self, payload: Dict[str, Any]) -> Board:
        name = (payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is verplicht")
        provider = (payload.get("provider") or "local").strip()
        if provider not in BOARD_PROVIDERS:
            raise HTTPException(status_code=400, detail=f"Onbekende provider '{provider}'")
        columns = payload.get("columns") or [dict(c) for c in DEFAULT_COLUMNS]
        self._validate_columns(columns)
        prefix = (payload.get("key_prefix") or name[:3]).strip().upper()[:16] or "LAB"
        now = _now_iso()
        b = Board(
            name=name,
            description=payload.get("description"),
            key_prefix=prefix,
            seq=0,
            lab_id=payload.get("lab_id") or None,
            columns=columns,
            agent_column=payload.get("agent_column") or self._default_agent_column(columns),
            agent_done_column=payload.get("agent_done_column") or self._default_done_column(columns),
            agent_instruction=payload.get("agent_instruction"),
            provider=provider,
            provider_config=payload.get("provider_config") or {},
            sync_direction=self._validated_direction(payload.get("sync_direction")),
            auto_sync_minutes=int(payload.get("auto_sync_minutes") or 0),
            created_at=now, updated_at=now,
        )
        self._apply_secret(b, payload)
        self.db.add(b)
        self.db.commit()
        self.db.refresh(b)
        return b

    def update_board(self, board_id: int, payload: Dict[str, Any]) -> Board:
        b = self.get_board(board_id)
        if "name" in payload:
            # Apart van de lus hieronder: die vertaalt leeg naar None, en een
            # naamloos board is niet op te slaan (NOT NULL) én onvindbaar.
            name = (payload.get("name") or "").strip()
            if not name:
                raise HTTPException(status_code=400, detail="name mag niet leeg zijn")
            b.name = name
        for field in ("description", "lab_id", "agent_column", "agent_done_column",
                      "agent_instruction", "provider_config"):
            if field in payload:
                setattr(b, field, payload[field] or (None if field != "provider_config" else {}))
        if "key_prefix" in payload and payload["key_prefix"]:
            b.key_prefix = str(payload["key_prefix"]).strip().upper()[:16]
        if "columns" in payload and payload["columns"] is not None:
            self._validate_columns(payload["columns"])
            self._remap_orphan_tickets(b, payload["columns"])
            b.columns = payload["columns"]
        if "provider" in payload and payload["provider"]:
            if payload["provider"] not in BOARD_PROVIDERS:
                raise HTTPException(status_code=400, detail=f"Onbekende provider '{payload['provider']}'")
            b.provider = payload["provider"]
        if "sync_direction" in payload:
            b.sync_direction = self._validated_direction(payload.get("sync_direction"))
        if "auto_sync_minutes" in payload:
            b.auto_sync_minutes = int(payload.get("auto_sync_minutes") or 0)
        self._apply_secret(b, payload)
        b.updated_at = _now_iso()
        self.db.commit()
        self.db.refresh(b)
        return b

    def delete_board(self, board_id: int) -> None:
        b = self.get_board(board_id)
        # SQLite dwingt ON DELETE CASCADE niet af zonder PRAGMA foreign_keys,
        # dus de kinderen gaan hier expliciet mee.
        ticket_ids = [t.id for t in self.db.query(Ticket).filter(Ticket.board_id == board_id).all()]
        if ticket_ids:
            (self.db.query(TicketComment)
             .filter(TicketComment.ticket_id.in_(ticket_ids))
             .delete(synchronize_session=False))
            self.db.query(Ticket).filter(Ticket.board_id == board_id).delete(synchronize_session=False)
        self.db.delete(b)
        self.db.commit()

    def _apply_secret(self, board: Board, payload: Dict[str, Any]) -> None:
        """`provider_secret` in de payload is de PAT/API-token in klare tekst.
        Leeg string = wissen, afwezig = ongemoeid laten."""
        if "provider_secret" not in payload:
            return
        raw = payload.get("provider_secret")
        if raw is None or str(raw).strip() == "":
            board.provider_secret_encrypted = None
            return
        from utils.crypto import encrypt
        board.provider_secret_encrypted = encrypt(str(raw))

    @staticmethod
    def _validated_direction(value: Any) -> str:
        direction = (value or "two_way").strip()
        if direction not in SYNC_DIRECTIONS:
            raise HTTPException(status_code=400, detail=f"Onbekende sync_direction '{direction}'")
        return direction

    @staticmethod
    def _validate_columns(columns: Any) -> None:
        if not isinstance(columns, list) or not columns:
            raise HTTPException(status_code=400, detail="columns moet een niet-lege lijst zijn")
        keys = set()
        for col in columns:
            key = (col or {}).get("key")
            if not key or not str(key).strip():
                raise HTTPException(status_code=400, detail="Elke kolom heeft een 'key' nodig")
            if key in keys:
                raise HTTPException(status_code=400, detail=f"Dubbele kolom-key '{key}'")
            keys.add(key)

    def _remap_orphan_tickets(self, board: Board, new_columns: List[Dict[str, Any]]) -> None:
        """Een kolom verwijderen mag geen tickets onzichtbaar maken: alles wat
        in een verdwenen kolom stond schuift naar de eerste kolom."""
        new_keys = {c["key"] for c in new_columns}
        fallback = new_columns[0]["key"]
        orphans = (self.db.query(Ticket)
                   .filter(Ticket.board_id == board.id, Ticket.status.notin_(new_keys)).all())
        for t in orphans:
            t.status = fallback
            t.updated_at = _now_iso()

    @staticmethod
    def _default_agent_column(columns: List[Dict[str, Any]]) -> Optional[str]:
        for col in columns:
            if col.get("key") == "agent":
                return "agent"
        return columns[0].get("key") if columns else None

    @staticmethod
    def _default_done_column(columns: List[Dict[str, Any]]) -> Optional[str]:
        for col in columns:
            if col.get("is_done"):
                return col.get("key")
        return columns[-1].get("key") if columns else None

    # ── tickets ─────────────────────────────────────────────────────────────

    def list_tickets(self, board_id: int, *, status: Optional[str] = None,
                     assignee: Optional[str] = None, limit: int = 500) -> List[Ticket]:
        q = self.db.query(Ticket).filter(Ticket.board_id == board_id)
        if status:
            q = q.filter(Ticket.status == status)
        if assignee:
            q = q.filter(Ticket.assignee == assignee)
        return q.order_by(Ticket.position.asc(), Ticket.id.asc()).limit(limit).all()

    def get_ticket(self, ticket_id: int) -> Ticket:
        t = self.db.get(Ticket, ticket_id)
        if not t:
            raise HTTPException(status_code=404, detail="Ticket niet gevonden")
        return t

    def ticket_by_key(self, board_id: int, key: str) -> Optional[Ticket]:
        """Zoekt op de LabX-sleutel (LAB-12) en valt terug op de externe
        sleutel (BICC-6408) — de agent kent vaak alleen die laatste."""
        needle = (key or "").strip()
        if not needle:
            return None
        t = (self.db.query(Ticket)
             .filter(Ticket.board_id == board_id, Ticket.key == needle).first())
        if t:
            return t
        return (self.db.query(Ticket)
                .filter(Ticket.board_id == board_id, Ticket.external_key == needle).first())

    def create_ticket(self, board_id: int, payload: Dict[str, Any], *,
                      author: str = "user") -> Ticket:
        board = self.get_board(board_id)
        title = (payload.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="title is verplicht")
        status = payload.get("status") or (board.columns[0]["key"] if board.columns else "todo")
        self._assert_known_column(board, status)
        board.seq = int(board.seq or 0) + 1
        now = _now_iso()
        t = Ticket(
            board_id=board_id,
            key=f"{board.key_prefix}-{board.seq}",
            title=title,
            description=payload.get("description"),
            acceptance_criteria=payload.get("acceptance_criteria"),
            status=status,
            priority=payload.get("priority") or "normal",
            assignee=payload.get("assignee"),
            labels=payload.get("labels") or [],
            position=self.next_position(board_id, status),
            # Een lokaal ticket op een gekoppeld bord is per definitie nog niet
            # bij de bron bekend — de push maakt het daar aan.
            dirty=board.provider != "local",
            created_at=now, updated_at=now,
        )
        self.db.add(t)
        self.db.commit()
        self.db.refresh(t)
        self.add_comment(t.id, body=f"Ticket aangemaakt door {author}.", author=author,
                         kind="activity")
        return t

    def update_ticket(self, ticket_id: int, payload: Dict[str, Any], *,
                      author: str = "user") -> Ticket:
        t = self.get_ticket(ticket_id)
        board = self.get_board(t.board_id)
        changed_synced = False
        old_status = t.status

        for field in ("title", "description", "acceptance_criteria", "priority",
                      "assignee", "labels"):
            if field in payload:
                new_value = payload[field]
                if getattr(t, field) != new_value:
                    setattr(t, field, new_value)
                    changed_synced = True
        if "status" in payload and payload["status"] and payload["status"] != t.status:
            self._assert_known_column(board, payload["status"])
            t.status = payload["status"]
            t.position = self.next_position(t.board_id, t.status)
            changed_synced = True
        if "position" in payload and payload["position"] is not None:
            t.position = float(payload["position"])
        if "agent_state" in payload and payload["agent_state"]:
            t.agent_state = payload["agent_state"]

        if changed_synced and board.provider != "local":
            t.dirty = True
        t.updated_at = _now_iso()
        self.db.commit()
        self.db.refresh(t)

        if t.status != old_status:
            self.add_comment(t.id, body=f"Verplaatst van '{old_status}' naar '{t.status}' door {author}.",
                             author=author, kind="activity")
        return t

    def move_ticket(self, ticket_id: int, status: str, position: Optional[float] = None, *,
                    author: str = "user") -> Ticket:
        payload: Dict[str, Any] = {"status": status}
        t = self.update_ticket(ticket_id, payload, author=author)
        if position is not None:
            t.position = float(position)
            t.updated_at = _now_iso()
            self.db.commit()
            self.db.refresh(t)
        return t

    def delete_ticket(self, ticket_id: int) -> None:
        t = self.get_ticket(ticket_id)
        (self.db.query(TicketComment)
         .filter(TicketComment.ticket_id == ticket_id).delete(synchronize_session=False))
        self.db.delete(t)
        self.db.commit()

    def _assert_known_column(self, board: Board, status: str) -> None:
        keys = {c.get("key") for c in (board.columns or [])}
        if status not in keys:
            raise HTTPException(
                status_code=400,
                detail=f"Onbekende kolom '{status}' — kies uit: {', '.join(sorted(k for k in keys if k))}")

    def next_position(self, board_id: int, status: str) -> float:
        """Onderaan een kolom aanschuiven. Publiek: de sync gebruikt hem ook."""
        last = (self.db.query(Ticket)
                .filter(Ticket.board_id == board_id, Ticket.status == status)
                .order_by(Ticket.position.desc()).first())
        return (last.position + 100.0) if last else 100.0

    # ── comments / activiteit ───────────────────────────────────────────────

    def list_comments(self, ticket_id: int) -> List[TicketComment]:
        return (self.db.query(TicketComment)
                .filter(TicketComment.ticket_id == ticket_id)
                .order_by(TicketComment.created_at.asc(), TicketComment.id.asc()).all())

    def add_comment(self, ticket_id: int, *, body: str, author: str = "user",
                    kind: str = "comment", external_id: Optional[str] = None,
                    pushed: bool = False) -> TicketComment:
        c = TicketComment(ticket_id=ticket_id, kind=kind, author=author, body=body,
                          external_id=external_id, pushed=pushed, created_at=_now_iso())
        self.db.add(c)
        self.db.commit()
        self.db.refresh(c)
        return c

    # ── serialisatie ────────────────────────────────────────────────────────

    def board_to_dict(self, b: Board, *, with_counts: bool = False) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": b.id, "name": b.name, "description": b.description,
            "key_prefix": b.key_prefix, "lab_id": b.lab_id, "columns": b.columns or [],
            "agent_column": b.agent_column, "agent_done_column": b.agent_done_column,
            "agent_instruction": b.agent_instruction,
            "provider": b.provider, "provider_config": b.provider_config or {},
            # Het geheim zelf verlaat de backend nooit — alleen of het er is.
            "has_secret": bool(b.provider_secret_encrypted),
            "sync_direction": b.sync_direction, "auto_sync_minutes": b.auto_sync_minutes,
            "last_sync_at": b.last_sync_at, "last_sync_error": b.last_sync_error,
            "created_at": b.created_at, "updated_at": b.updated_at,
        }
        if b.lab_id:
            from models.lab import Lab
            lab = self.db.get(Lab, b.lab_id)
            out["lab_name"] = lab.name if lab else None
            out["lab_status"] = lab.status if lab else None
        if with_counts:
            counts: Dict[str, int] = {}
            for t in self.db.query(Ticket).filter(Ticket.board_id == b.id).all():
                counts[t.status] = counts.get(t.status, 0) + 1
            out["ticket_counts"] = counts
            out["ticket_total"] = sum(counts.values())
        return out

    @staticmethod
    def ticket_to_dict(t: Ticket) -> Dict[str, Any]:
        return {
            "id": t.id, "board_id": t.board_id, "key": t.key, "title": t.title,
            "description": t.description, "acceptance_criteria": t.acceptance_criteria,
            "status": t.status, "priority": t.priority,
            "assignee": t.assignee, "labels": t.labels or [], "position": t.position,
            "agent_state": t.agent_state, "agent_run_id": t.agent_run_id,
            "agent_thread_id": t.agent_thread_id, "agent_last_error": t.agent_last_error,
            "external_provider": t.external_provider, "external_id": t.external_id,
            "external_key": t.external_key, "external_url": t.external_url,
            "external_synced_at": t.external_synced_at, "dirty": t.dirty,
            "created_at": t.created_at, "updated_at": t.updated_at,
        }

    @staticmethod
    def comment_to_dict(c: TicketComment) -> Dict[str, Any]:
        return {
            "id": c.id, "ticket_id": c.ticket_id, "kind": c.kind, "author": c.author,
            "body": c.body, "external_id": c.external_id, "pushed": c.pushed,
            "created_at": c.created_at,
        }

    def tickets_to_dicts(self, tickets: Iterable[Ticket]) -> List[Dict[str, Any]]:
        return [self.ticket_to_dict(t) for t in tickets]
