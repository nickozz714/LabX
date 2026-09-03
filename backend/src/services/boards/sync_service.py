"""
services/boards/sync_service.py

Synchronisatie tussen een LabX-board en zijn bron (Azure DevOps / Jira).

Volgorde is bewust **push vóór pull**: eerst wat LabX lokaal veranderde naar de
bron sturen, dan de bron als waarheid terugtrekken. Andersom zou de pull de nog
niet gepushte lokale wijziging overschrijven en die stilletjes weggooien.

Conflictregel: de bron wint, behalve voor een ticket dat nog `dirty` is (de
push is mislukt) — dat blijft lokaal staan zodat de wijziging niet verdampt en
zichtbaar blijft als openstaand verschil.

Statusmapping zit in `provider_config["state_map"]`: {kolom-key: [externe
statussen]}. Ontbreekt een kolom in de map, dan blijft het ticket in de kolom
waar het staat (pull) resp. wordt de status niet meegestuurd (push) — nooit
raden.

Die mapping wordt niet aan de gebruiker overgelaten: `_auto_map` leidt hem vóór
elke sync af uit de bron zelf (`discover_columns`). Dat is nodig omdat de
KOLOMMEN van een bordbron andere namen dragen dan de STATUSSEN eronder — een
Jira-bord met kolom "In Progress" kan er de status "Actief" onder hebben. Wie de
kolomkoppen overtypt (het voor de hand liggende), mapt op namen die als status
niet bestaan; alles valt dan door de mapping heen en belandt in de eerste kolom.
`_auto_map` herstelt precies dat geval, vult ontbrekende statussen aan en maakt
zo nodig een kolom bij. Handmatige keuzes die wél naar een bestaande status
wijzen blijven staan; `provider_config["auto_map"] = False` zet het uit.
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

# Hoeveel tickets buiten de board-query er per sync alsnog bijgewerkt worden.
# Ruim genoeg voor een normaal board, en het houdt een bord met duizenden oude
# tickets ervan af elke sync de bron plat te bellen.
_RECONCILE_LIMIT = 500


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(name: str) -> str:
    """Kolomnaam -> kolom-key. "In Progress" wordt "in_progress", waardoor een
    bronkolom zijn LabX-tegenhanger vindt zonder dat iemand ze koppelt."""
    out = "".join(ch if ch.isalnum() else "_" for ch in (name or "").lower())
    return "_".join(part for part in out.split("_") if part)[:32]


def _unique_key(base: str, columns: List[Dict[str, Any]]) -> str:
    key = base or "kolom"
    n = 2
    while any(str(c.get("key")) == key for c in columns):
        key = f"{base}_{n}"
        n += 1
    return key


class BoardSyncService:
    def __init__(self, db: Session):
        self.db = db
        self.boards = BoardService(db)

    # ── mapping ─────────────────────────────────────────────────────────────

    @staticmethod
    def _state_map(board: Board) -> Dict[str, List[str]]:
        """{kolom-key: [externe statussen]}. Een kolom in de bron is meestal
        een GROEPJE statussen (een Jira-bordkolom "In uitvoering" kan "In
        Progress" en "In Review" bevatten), dus een kolom mag er meerdere
        hebben. Oudere configuraties schreven één string per kolom — die
        blijven werken, komma's worden gesplitst."""
        raw = (board.provider_config or {}).get("state_map") or {}
        out: Dict[str, List[str]] = {}
        for key, value in raw.items():
            if isinstance(value, (list, tuple)):
                states = [str(v).strip() for v in value]
            else:
                states = [part.strip() for part in str(value).split(",")]
            states = [s for s in states if s]
            if states:
                out[str(key)] = states
        return out

    def _column_for_state(self, board: Board, state: Optional[str]) -> Optional[str]:
        """Externe status -> bordkolom. Hoofdletterongevoelig, en als de
        externe status letterlijk een kolom-key is werkt dat ook zonder map."""
        if not state:
            return None
        needle = state.strip().lower()
        for column_key, externals in self._state_map(board).items():
            if any(e.strip().lower() == needle for e in externals):
                return column_key
        for col in (board.columns or []):
            if str(col.get("key", "")).lower() == needle or str(col.get("name", "")).lower() == needle:
                return col.get("key")
        return None

    def _state_for_column(self, board: Board, column_key: str) -> Optional[str]:
        """Push-richting: de EERSTE status van de kolom — dat is de status
        waar een ticket in terechtkomt als het naar deze kolom verhuist."""
        states = self._state_map(board).get(column_key) or []
        return states[0] if states else None

    # ── mapping automatisch afleiden uit de bron ────────────────────────────

    async def _auto_map(self, board: Board, adapter, stats: Dict[str, Any]) -> None:
        """Repareer en vul `state_map` aan met wat de bron werkelijk kent.

        Drie stappen, in deze volgorde:
        1. Waarden die geen bestaande status zijn, gaan eruit — dat is de
           kolomkop-die-als-status-is-ingevuld.
        2. Elke bronkolom waarvan de statussen nog nergens gemapt zijn, krijgt
           een LabX-kolom toegewezen: op sleutel, anders op naam, anders de
           kolom die vóór stap 1 naar díé bronkolom wees, anders een nieuwe.
        3. Wat er verandert komt in de stats, zodat de sync kan uitleggen
           waarom een ticket ineens ergens anders staat.
        """
        if (board.provider_config or {}).get("auto_map") is False:
            return
        try:
            source_columns = await adapter.discover_columns()
            declared = await adapter.discover_states()
        except Exception as exc:  # noqa: BLE001 — mapping mag de sync niet blokkeren
            log.warningx("Mapping afleiden mislukt", board=board.name, error=str(exc)[:200])
            return
        if not source_columns:
            return

        valid = {s.strip().lower() for s in declared}
        valid |= {s.strip().lower() for c in source_columns for s in c.states}
        changes: List[str] = []

        # 1. ongeldige waarden eruit, maar onthouden waar ze naar wezen
        kept: Dict[str, List[str]] = {}
        stale: Dict[str, List[str]] = {}
        for key, states in self._state_map(board).items():
            good = [s for s in states if s.strip().lower() in valid]
            bad = [s for s in states if s.strip().lower() not in valid]
            if good:
                kept[key] = good
            if bad:
                stale[key] = bad
                changes.append(f"'{key}' wees naar {', '.join(bad)} — bestaat niet als status")

        columns = [dict(c) for c in (board.columns or [])]
        claimed: set = set()

        def _mapped() -> set:
            return {x.strip().lower() for vals in kept.values() for x in vals}

        # 2. elke bronkolom een LabX-kolom geven
        for sc in source_columns:
            todo = [s for s in sc.states if s.strip().lower() not in _mapped()]
            if not todo:
                continue
            slug = _slug(sc.name)
            name = sc.name.strip().lower()
            target = (
                next((c["key"] for c in columns if str(c.get("key", "")).lower() == slug), None)
                or next((c["key"] for c in columns
                         if str(c.get("name", "")).strip().lower() == name), None)
                # De kolom die vóór stap 1 de KOLOMNAAM als status had staan:
                # dat was de bedoeling van degene die het invulde.
                or next((k for k, vals in stale.items()
                         if k not in claimed
                         and any(v.strip().lower() == name for v in vals)), None)
            )
            if target is None:
                target = _unique_key(slug, columns)
                columns.append({"key": target, "name": sc.name})
                changes.append(f"kolom '{sc.name}' aangemaakt")
            claimed.add(target)
            kept.setdefault(target, []).extend(todo)
            changes.append(f"{sc.name} → {target}: {', '.join(todo)}")

        if not changes:
            return
        board.columns = columns
        board.provider_config = {**(board.provider_config or {}), "state_map": kept}
        board.updated_at = _now_iso()
        self.db.commit()
        stats["mapping"] = changes
        log.infox("Statusmapping afgeleid uit de bron", board=board.name, changes=len(changes))

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
            "comments_pulled": 0, "skipped_dirty": 0, "reconciled": 0, "errors": [],
            # Wat _auto_map aan de mapping veranderde (leeg = niets te doen).
            "mapping": [],
            # Statussen uit de bron die op geen enkele kolom gemapt zijn. Zonder
            # dit belandt zo'n ticket stilletjes in de eerste kolom en lijkt het
            # of de sync de status negeert.
            "unmapped_states": [],
        }
        try:
            adapter = self._adapter(board)
        except Exception as exc:  # noqa: BLE001
            board.last_sync_error = str(exc)[:1000]
            board.updated_at = _now_iso()
            self.db.commit()
            raise

        try:
            # Vóór de push: die vertaalt kolommen naar statussen en heeft dus
            # dezelfde mapping nodig.
            await self._auto_map(board, adapter, stats)
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
        """Alleen echte opmerkingen (kind="comment") die lokaal zijn ontstaan
        én niet als intern gemarkeerd zijn.

        LabX-activiteitsregels blijven binnen LabX — een bron volplempen met
        "verplaatst van todo naar agent" is ruis in andermans systeem. Hetzelfde
        geldt voor alles wat de agent schrijft: dat is standaard intern, en het
        gaat pas mee zodra iemand het bewust promoveert."""
        rows = (self.db.query(TicketComment, Ticket)
                .join(Ticket, Ticket.id == TicketComment.ticket_id)
                .filter(Ticket.board_id == board.id,
                        TicketComment.kind == "comment",
                        TicketComment.internal == False,  # noqa: E712
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

    async def push_comment(self, comment_id: int) -> Dict[str, Any]:
        """Eén opmerking nu naar de bron sturen — de weg die "promoveren naar
        extern" neemt, zodat je meteen ziet of het gelukt is in plaats van te
        moeten hopen op de volgende sync."""
        comment = self.boards.get_comment(comment_id)
        ticket = self.boards.get_ticket(comment.ticket_id)
        board = self.boards.get_board(ticket.board_id)
        if comment.internal:
            raise ValueError("Deze opmerking staat nog op intern.")
        if comment.pushed or comment.external_id:
            return {"ok": True, "detail": "Stond al in de bron."}
        target = ticket.external_key if board.provider == "jira" else ticket.external_id
        if not target:
            return {"ok": False, "error": "Dit ticket bestaat nog niet in de bron."}
        adapter = self._adapter(board)
        external_id = await adapter.add_comment(
            external_id=target, body=f"[LabX · {comment.author}]\n\n{comment.body}")
        comment.pushed = True
        if external_id:
            comment.external_id = external_id
        self.db.commit()
        return {"ok": True, "detail": f"Geplaatst op {ticket.external_key or target}."}

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
            if item.state and not column and item.state not in stats["unmapped_states"]:
                stats["unmapped_states"].append(item.state)
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

        await self._reconcile_known(board, adapter, existing,
                                    {i.external_id for i in items}, stats)

    async def _reconcile_known(self, board: Board, adapter,
                               existing: Dict[str, Ticket], seen: set,
                               stats: Dict[str, Any]) -> None:
        """Tickets die de board-query NIET (meer) teruggeeft, alsnog bijwerken.

        Een JQL/WIQL is meestal een selectie ("mijn issues in de lopende
        sprint"). Valt een issue daarbuiten, dan blijft het lokale ticket
        hangen op de status van de laatste keer dat het wél in de query zat —
        en na een mappingfout is dat de eerste kolom, voorgoed. Deze stap haalt
        die tickets op hun eigen sleutel op, maakt er nooit nieuwe bij.
        """
        stale = [t for external_id, t in existing.items()
                 if external_id not in seen and not t.dirty]
        if not stale:
            return
        by_key: Dict[str, Ticket] = {}
        for t in stale[:_RECONCILE_LIMIT]:
            key = t.external_key or t.external_id
            if key:
                by_key[str(key)] = t
        if len(stale) > _RECONCILE_LIMIT:
            stats["errors"].append(
                f"{len(stale) - _RECONCILE_LIMIT} ticket(s) buiten de query niet bijgewerkt "
                f"(maximaal {_RECONCILE_LIMIT} per sync)")
        try:
            items = await adapter.fetch_items_by_keys(list(by_key))
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(f"tickets buiten de query bijwerken: {str(exc)[:200]}")
            return
        for item in items:
            ticket = (existing.get(item.external_id)
                      or by_key.get(str(item.external_key or "")))
            if ticket is None or ticket.dirty:
                continue
            column = self._column_for_state(board, item.state)
            if item.state and not column and item.state not in stats["unmapped_states"]:
                stats["unmapped_states"].append(item.state)
            if self._apply_external_fields(ticket, item, column):
                stats["reconciled"] += 1
            self._apply_external_identity(ticket, board, item)
            ticket.external_synced_at = _now_iso()
        self.db.commit()

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
