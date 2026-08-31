"""
services/boards/sync/base.py

Het contract tussen de sync-orkestratie (sync_service.py) en een concrete
bron (Azure DevOps, Jira). Elke adapter vertaalt zijn eigen API naar één
genormaliseerde vorm — `ExternalItem` — zodat sync_service niets van DevOps'
json-patch of Jira's ADF hoeft te weten.

Bewust géén universele veldmapping-laag: LabX synchroniseert de handvol
velden die een kanban-ticket écht draagt (titel, omschrijving, acceptatie-
criteria, status, prioriteit, toegewezene, labels) plus opmerkingen. Alles daarbuiten blijft in
het bronsysteem, waar het thuishoort.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ExternalComment:
    external_id: str
    author: str
    body: str
    created_at: Optional[str] = None


@dataclass
class ExternalBoardColumn:
    """Een kolom zoals de BRON hem kent (een Jira-bordkolom, een DevOps-
    boardkolom): een naam met de statussen die eronder vallen. Een kolom is
    dus bijna nooit één status — precies waarom de statusmapping meerdere
    statussen per LabX-kolom moet aankunnen."""
    name: str
    states: List[str] = field(default_factory=list)


@dataclass
class ExternalItem:
    external_id: str
    title: str
    external_key: Optional[str] = None
    external_url: Optional[str] = None
    description: Optional[str] = None
    # Los van de omschrijving: Azure DevOps heeft er een eigen veld voor en een
    # ticket wordt eraan afgemeten. None = de bron kent het niet (dan blijft de
    # lokale waarde staan).
    acceptance_criteria: Optional[str] = None
    # De status zoals de BRON hem noemt ("Active", "In Progress") — de mapping
    # naar een bordkolom gebeurt in sync_service.
    state: Optional[str] = None
    priority: Optional[str] = None
    assignee: Optional[str] = None
    labels: List[str] = field(default_factory=list)
    rev: Optional[str] = None
    comments: List[ExternalComment] = field(default_factory=list)


class SyncAdapter:
    """Basisklasse. Een adapter is stateless op de config na en doet zelf geen
    databasewerk — hij praat alleen met de bron."""

    provider = "base"

    def __init__(self, config: Dict[str, Any], secret: Optional[str]):
        self.config = config or {}
        self.secret = secret or ""

    # ── lezen ───────────────────────────────────────────────────────────────

    async def fetch_items(self, *, with_comments: bool = True) -> List[ExternalItem]:
        raise NotImplementedError

    async def discover_states(self) -> List[str]:
        """Alle statussen die de bron kent — niet alleen die van de opgehaalde
        items. Zonder dit ziet de gebruiker een status pas in de mapping als er
        toevallig een item in staat. Lege lijst = adapter weet het niet."""
        return []

    async def discover_columns(self) -> List[ExternalBoardColumn]:
        """De kolommen van het bord in de bron, met de statussen eronder.
        Lege lijst = de bron kent geen bordkolommen (of we mogen ze niet zien)."""
        return []

    # ── schrijven (alleen aangeroepen bij sync_direction="two_way") ──────────

    async def create_item(self, *, title: str, description: Optional[str], state: Optional[str],
                          priority: Optional[str], assignee: Optional[str],
                          labels: List[str],
                          acceptance_criteria: Optional[str] = None) -> ExternalItem:
        raise NotImplementedError

    async def update_item(self, *, external_id: str, title: Optional[str],
                          description: Optional[str], state: Optional[str],
                          priority: Optional[str], assignee: Optional[str],
                          labels: Optional[List[str]],
                          acceptance_criteria: Optional[str] = None) -> ExternalItem:
        raise NotImplementedError

    async def add_comment(self, *, external_id: str, body: str) -> Optional[str]:
        raise NotImplementedError

    # ── hulpjes ─────────────────────────────────────────────────────────────

    def _require(self, key: str) -> str:
        value = str(self.config.get(key) or "").strip()
        if not value:
            raise ValueError(f"{self.provider}: '{key}' ontbreekt in de boardconfiguratie")
        return value

    def _require_secret(self) -> str:
        if not self.secret:
            raise ValueError(f"{self.provider}: geen token ingesteld op dit board")
        return self.secret


def build_adapter(provider: str, config: Dict[str, Any], secret: Optional[str]) -> SyncAdapter:
    if provider == "azure_devops":
        from services.boards.sync.azure_devops import AzureDevOpsAdapter
        return AzureDevOpsAdapter(config, secret)
    if provider == "jira":
        from services.boards.sync.jira import JiraAdapter
        return JiraAdapter(config, secret)
    raise ValueError(f"Provider '{provider}' kent geen synchronisatie")
