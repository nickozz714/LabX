"""
services/boards/sync/jira.py

Jira Cloud via REST API v3.

Config (`Board.provider_config`):
    base_url       "https://mijnbedrijf.atlassian.net"   (verplicht)
    email          het account bij de API-token          (verplicht)
    project_key    "BICC"                                (verplicht bij create)
    board_name     "BICC Sprint board"                   (optioneel — anders het
                                                          eerste agile board van
                                                          het project)
    jql            eigen JQL                             (optioneel — anders:
                                                          project = <key> ORDER
                                                          BY updated DESC)
    issue_type     "Task"                                (default, voor create)
    acceptance_field  "customfield_10035"                (optioneel)
    max_items      default 200

Acceptatiecriteria: Jira kent er geen standaardveld voor. Staat
`acceptance_field` ingevuld (de id van je eigen veld, te vinden via
/rest/api/3/field), dan synchroniseert LabX ze daarmee; anders blijven ze
LabX-only — bewust niet onder de description geplakt, want dan vervuilt de
opdracht in de bron precies zoals we op het board proberen te voorkomen.

Auth: basic auth met base64(email:API-token) — een Atlassian API-token, geen
wachtwoord.

Schrijven staat aan bij `sync_direction="two_way"` (de standaard voor een
LabX-board, zie models/board.py). Een bord dat de bron niet mag aanraken zet je
op `"pull"`.

Twee Jira-eigenaardigheden die dit bestand afhandelt:
- **ADF**: v3 wil `description` en opmerkingen als Atlassian Document Format
  (een JSON-document), niet als tekst. De vertaling van en naar Markdown zit in
  `sync/adf.py` — bewust een eigen module, want hij moet rondgang-vast zijn en
  dat vraagt om echte tests.
- **Assignee is een accountId, geen naam.** Jira Cloud accepteert
  `{"displayName": ...}` zonder te klagen en zet het veld vervolgens op
  niemand. Zo raakten in dit project vier tickets hun uitvoerder kwijt.
  `_account_id` zoekt de naam op en er wordt niets verstuurd als dat niet lukt
  — een leeg veld is erger dan een mislukte push, want die laatste zie je.
- **Status is geen veld**: je zet een issue niet op "Done" met een PUT, je
  voert een *transition* uit. `_transition_to` zoekt de transitie op naam op.
"""
from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

import httpx

from component_logging import get_logger
from services.boards.sync.adf import to_adf, to_markdown
from services.boards.sync.base import (ExternalBoardColumn, ExternalComment, ExternalItem,
                                       SyncAdapter)

log = get_logger(__name__)

_BASE_FIELDS = ["summary", "description", "status", "assignee", "labels", "priority", "updated"]
# Jira's standaardprioriteiten -> LabX. Onbekende namen blijven "normal".
_PRIORITY_FROM_JIRA = {
    "highest": "urgent", "high": "high", "medium": "normal",
    "low": "low", "lowest": "low",
}
_PRIORITY_TO_JIRA = {"urgent": "Highest", "high": "High", "normal": "Medium", "low": "Low"}


def _adf_to_text(node: Any) -> str:
    """ADF -> Markdown. Naam behouden omdat de rest van dit bestand hem zo
    noemt; de vertaling zelf zit in sync/adf.py."""
    return to_markdown(node)


def _text_to_adf(text: Optional[str]) -> Dict[str, Any]:
    return to_adf(text)


class JiraAdapter(SyncAdapter):
    provider = "jira"

    def __init__(self, config: Dict[str, Any], secret: Optional[str]):
        super().__init__(config, secret)
        self.base_url = self._require("base_url").rstrip("/")
        self.email = self._require("email")

    def _headers(self) -> Dict[str, str]:
        token = base64.b64encode(f"{self.email}:{self._require_secret()}".encode()).decode()
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @property
    def _acceptance_field(self) -> str:
        return str(self.config.get("acceptance_field") or "").strip()

    @property
    def _fields(self) -> List[str]:
        return _BASE_FIELDS + ([self._acceptance_field] if self._acceptance_field else [])

    def _jql(self) -> str:
        custom = str(self.config.get("jql") or "").strip()
        if custom:
            return custom
        return f"project = {self._require('project_key')} ORDER BY updated DESC"

    @staticmethod
    def _raise_for(resp: httpx.Response, what: str) -> None:
        if resp.status_code >= 400:
            detail = resp.text[:500]
            if resp.status_code in (401, 403):
                raise RuntimeError("Jira weigerde de credentials (controleer e-mailadres "
                                   "en API-token, en of het account het project mag zien)")
            raise RuntimeError(f"Jira: {what} mislukt ({resp.status_code}) — {detail}")

    async def _search(self, client: httpx.AsyncClient, max_items: int) -> List[Dict[str, Any]]:
        """Jira Cloud verving `/rest/api/3/search` door `/rest/api/3/search/jql`.
        Welke van de twee een instantie aanbiedt hangt af van hoe ver hij mee is,
        dus: nieuwe endpoint eerst, bij 404/410 terugvallen op de oude."""
        body = {"jql": self._jql(), "fields": self._fields, "maxResults": min(max_items, 100)}
        issues: List[Dict[str, Any]] = []

        resp = await client.post(f"{self.base_url}/rest/api/3/search/jql",
                                 headers=self._headers(), json=body)
        if resp.status_code in (404, 410):
            resp = await client.post(f"{self.base_url}/rest/api/3/search",
                                     headers=self._headers(), json=body)
        self._raise_for(resp, "JQL-zoekopdracht")
        data = resp.json()
        issues.extend(data.get("issues") or [])

        # Doorbladeren tot max_items: de nieuwe endpoint gebruikt nextPageToken,
        # de oude startAt.
        token = data.get("nextPageToken")
        start_at = len(issues)
        while len(issues) < max_items:
            page_body = dict(body)
            if token:
                page_body["nextPageToken"] = token
            elif data.get("total") is not None and start_at < int(data.get("total") or 0):
                page_body["startAt"] = start_at
            else:
                break
            page = await client.post(f"{self.base_url}/rest/api/3/search/jql"
                                     if token else f"{self.base_url}/rest/api/3/search",
                                     headers=self._headers(), json=page_body)
            if page.status_code >= 400:
                break
            data = page.json()
            batch = data.get("issues") or []
            if not batch:
                break
            issues.extend(batch)
            token = data.get("nextPageToken")
            start_at = len(issues)
        return issues[:max_items]

    async def fetch_items(self, *, with_comments: bool = True) -> List[ExternalItem]:
        max_items = int(self.config.get("max_items") or 200)
        async with httpx.AsyncClient(timeout=60.0) as client:
            issues = await self._search(client, max_items)
            items = [self._to_item(raw) for raw in issues]
            if with_comments:
                for item in items:
                    item.comments = await self._fetch_comments(client, item.external_key or item.external_id)
        return items

    async def fetch_items_by_keys(self, keys: List[str]) -> List[ExternalItem]:
        """`key in (...)` in blokken — Jira's JQL heeft een grens aan de lengte
        van zo'n opsomming, en een te grote query faalt in zijn geheel."""
        out: List[ExternalItem] = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            for start in range(0, len(keys), 50):
                block = [k for k in keys[start:start + 50] if k]
                if not block:
                    continue
                body = {"jql": "key in ({})".format(",".join(block)),
                        "fields": self._fields, "maxResults": len(block)}
                resp = await client.post(f"{self.base_url}/rest/api/3/search/jql",
                                         headers=self._headers(), json=body)
                if resp.status_code in (404, 410):
                    resp = await client.post(f"{self.base_url}/rest/api/3/search",
                                             headers=self._headers(), json=body)
                if resp.status_code >= 400:
                    # Een verwijderd issue laat de hele opsomming falen; dat mag
                    # de rest van de sync niet meeslepen.
                    log.warningx("Jira: issues op sleutel ophalen mislukt",
                                 status=resp.status_code, detail=resp.text[:200])
                    continue
                out.extend(self._to_item(raw) for raw in (resp.json().get("issues") or []))
        return out

    # ── ontdekken (statussen + bordkolommen) ────────────────────────────────

    async def discover_states(self) -> List[str]:
        """Alle statusnamen van het project, ook die waar nu geen issue in
        staat. `/project/{key}/statuses` geeft ze per issuetype; wij willen de
        vereniging."""
        names: List[str] = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for name in (await self._project_statuses(client)).values():
                if name not in names:
                    names.append(name)
        return sorted(names)

    async def _project_statuses(self, client: httpx.AsyncClient) -> Dict[str, str]:
        """{status-id: naam} voor dit project. De agile-API noemt kolomstatussen
        alleen bij id, dus die vertaling hebben we nodig."""
        out: Dict[str, str] = {}
        try:
            resp = await client.get(
                f"{self.base_url}/rest/api/3/project/{self._require('project_key')}/statuses",
                headers=self._headers())
            if resp.status_code >= 400:
                return out
            for issue_type in (resp.json() or []):
                for st in (issue_type.get("statuses") or []):
                    if st.get("id") and st.get("name"):
                        out[str(st["id"])] = str(st["name"])
        except Exception as exc:  # noqa: BLE001
            log.warningx("Jira-statussen ophalen mislukt", error=str(exc)[:200])
        return out

    async def discover_columns(self) -> List[ExternalBoardColumn]:
        """De kolommen van het Jira-bord van dit project, met de statussen die
        eronder hangen. Dit is wat een gebruiker in Jira ZIET — een kolom is
        daar een groepje statussen, niet één status."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            board_id = await self._agile_board_id(client)
            if not board_id:
                return []
            try:
                resp = await client.get(
                    f"{self.base_url}/rest/agile/1.0/board/{board_id}/configuration",
                    headers=self._headers())
                if resp.status_code >= 400:
                    return []
                config = resp.json() or {}
            except Exception as exc:  # noqa: BLE001
                log.warningx("Jira-bordconfiguratie ophalen mislukt", error=str(exc)[:200])
                return []

            by_id = await self._project_statuses(client)
            columns: List[ExternalBoardColumn] = []
            for col in ((config.get("columnConfig") or {}).get("columns") or []):
                states = [by_id.get(str(s.get("id")), "") for s in (col.get("statuses") or [])]
                columns.append(ExternalBoardColumn(name=str(col.get("name") or ""),
                                                   states=[s for s in states if s]))
            return [c for c in columns if c.name]

    async def _agile_board_id(self, client: httpx.AsyncClient) -> Optional[str]:
        """Het agile board bij dit project: op naam als `board_name` staat
        ingevuld, anders het eerste. Een project kan er meerdere hebben."""
        try:
            resp = await client.get(f"{self.base_url}/rest/agile/1.0/board",
                                    headers=self._headers(),
                                    params={"projectKeyOrId": self._require("project_key"),
                                            "maxResults": 50})
            if resp.status_code >= 400:
                return None
            boards = (resp.json() or {}).get("values") or []
        except Exception as exc:  # noqa: BLE001
            log.warningx("Jira-borden ophalen mislukt", error=str(exc)[:200])
            return None
        if not boards:
            return None
        wanted = str(self.config.get("board_name") or "").strip().lower()
        if wanted:
            for b in boards:
                if str(b.get("name") or "").strip().lower() == wanted:
                    return str(b.get("id"))
        return str(boards[0].get("id"))

    def _to_item(self, raw: Dict[str, Any]) -> ExternalItem:
        fields = raw.get("fields") or {}
        key = raw.get("key")
        status = ((fields.get("status") or {}).get("name"))
        assignee_obj = fields.get("assignee") or {}
        priority_name = str(((fields.get("priority") or {}).get("name") or "")).lower()
        return ExternalItem(
            external_id=str(raw.get("id") or key),
            external_key=key,
            external_url=f"{self.base_url}/browse/{key}" if key else None,
            title=str(fields.get("summary") or key or "Jira-issue"),
            description=_adf_to_text(fields.get("description")).strip(),
            acceptance_criteria=(_adf_to_text(fields.get(self._acceptance_field)).strip()
                                 if self._acceptance_field else None),
            state=status,
            priority=_PRIORITY_FROM_JIRA.get(priority_name),
            assignee=assignee_obj.get("displayName") or assignee_obj.get("emailAddress"),
            labels=list(fields.get("labels") or []),
            # `updated` is Jira's versie-stempel: verandert bij elke wijziging.
            rev=str(fields.get("updated") or ""),
        )

    async def _fetch_comments(self, client: httpx.AsyncClient, key: str) -> List[ExternalComment]:
        try:
            resp = await client.get(f"{self.base_url}/rest/api/3/issue/{key}/comment",
                                    headers=self._headers(), params={"maxResults": 50})
            if resp.status_code >= 400:
                return []
            out = []
            for c in (resp.json().get("comments") or []):
                out.append(ExternalComment(
                    external_id=str(c.get("id")),
                    author=((c.get("author") or {}).get("displayName") or "Jira"),
                    body=_adf_to_text(c.get("body")).strip(),
                    created_at=c.get("created"),
                ))
            return out
        except Exception as exc:  # noqa: BLE001
            log.warningx("Jira-opmerkingen ophalen mislukt", issue=key, error=str(exc)[:200])
            return []

    # ── schrijven ───────────────────────────────────────────────────────────

    async def _account_id(self, client: httpx.AsyncClient, naam: str,
                          issue_key: Optional[str] = None) -> Optional[str]:
        """Naam of e-mailadres -> Jira accountId.

        Nodig omdat `assignee` een gebruikersveld is: Jira wil een accountId.
        Stuur je een naam, dan neemt Jira de PUT aan en zet het veld op
        niemand — de stilste manier om een uitvoerder kwijt te raken.

        Eerst de lijst mensen die dit issue toegewezen mogen krijgen (dat is de
        lijst die Jira zelf in het toewijzingsveld toont), anders de algemene
        gebruikerszoekopdracht. Geen treffer of meerdere gelijke treffers ->
        None, en dan sturen we het veld niet mee.
        """
        zoek = (naam or "").strip()
        if not zoek:
            return None
        pogingen = []
        if issue_key:
            pogingen.append((f"{self.base_url}/rest/api/3/user/assignable/search",
                             {"issueKey": issue_key, "query": zoek, "maxResults": 50}))
        pogingen.append((f"{self.base_url}/rest/api/3/user/search",
                         {"query": zoek, "maxResults": 50}))
        for url, params in pogingen:
            try:
                resp = await client.get(url, headers=self._headers(), params=params)
            except Exception as exc:  # noqa: BLE001
                log.warningx("Jira-gebruiker zoeken mislukt", naam=zoek, error=str(exc)[:200])
                continue
            if resp.status_code >= 400:
                continue
            mensen = [u for u in (resp.json() or []) if u.get("accountId")]
            laag = zoek.lower()
            exact = [u for u in mensen
                     if str(u.get("displayName") or "").strip().lower() == laag
                     or str(u.get("emailAddress") or "").strip().lower() == laag]
            if len(exact) == 1:
                return str(exact[0]["accountId"])
            if len(mensen) == 1 and not exact:
                return str(mensen[0]["accountId"])
            if len(exact) > 1:
                # Twee mensen met dezelfde naam: raden is erger dan niets doen.
                log.warningx("Jira: naam is niet uniek, assignee niet meegestuurd",
                             naam=zoek, treffers=len(exact))
                return None
        return None

    async def _fields_payload(self, client: httpx.AsyncClient, *,
                              title: Optional[str], description: Optional[str],
                              priority: Optional[str], assignee: Optional[str],
                              labels: Optional[List[str]],
                              acceptance_criteria: Optional[str] = None,
                              issue_key: Optional[str] = None) -> Dict[str, Any]:
        """None betekent overal "niet aanraken". Dat is geen detail maar de
        hele afspraak met sync_service: die stuurt alleen de velden mee die in
        LabX daadwerkelijk zijn gewijzigd, zodat een sync nooit een veld
        overschrijft dat niemand heeft aangeraakt."""
        fields: Dict[str, Any] = {}
        if title is not None:
            fields["summary"] = title
        if description is not None:
            fields["description"] = _text_to_adf(description)
        if priority and priority in _PRIORITY_TO_JIRA:
            fields["priority"] = {"name": _PRIORITY_TO_JIRA[priority]}
        if labels is not None:
            # Jira-labels mogen geen spaties bevatten.
            fields["labels"] = [str(x).replace(" ", "-") for x in labels]
        if assignee:
            account = await self._account_id(client, assignee, issue_key)
            if account:
                fields["assignee"] = {"accountId": account}
            else:
                raise RuntimeError(
                    f"Jira kent geen gebruiker '{assignee}' in dit project — de "
                    f"toewijzing is NIET meegestuurd. Zonder deze stop zou Jira "
                    f"het veld leegmaken.")
        if acceptance_criteria is not None and self._acceptance_field:
            fields[self._acceptance_field] = _text_to_adf(acceptance_criteria)
        return fields

    async def create_item(self, *, title: str, description: Optional[str], state: Optional[str],
                          priority: Optional[str], assignee: Optional[str],
                          labels: List[str],
                          acceptance_criteria: Optional[str] = None) -> ExternalItem:
        async with httpx.AsyncClient(timeout=60.0) as client:
            fields = await self._fields_payload(
                client, title=title, description=description, priority=priority,
                assignee=None, labels=labels, acceptance_criteria=acceptance_criteria)
            fields["project"] = {"key": self._require("project_key")}
            fields["issuetype"] = {"name": str(self.config.get("issue_type") or "Task")}
            resp = await client.post(f"{self.base_url}/rest/api/3/issue",
                                     headers=self._headers(), json={"fields": fields})
            self._raise_for(resp, "issue aanmaken")
            key = (resp.json() or {}).get("key")
            if state:
                await self._transition_to(client, key, state)
            if assignee:
                # Pas na het aanmaken: de assignable-lijst hangt aan een
                # bestaand issue, en die geeft betere treffers dan de algemene
                # gebruikerszoekopdracht.
                toewijzing = await self._fields_payload(client, title=None, description=None,
                                                        priority=None, assignee=assignee,
                                                        labels=None, issue_key=key)
                await client.put(f"{self.base_url}/rest/api/3/issue/{key}",
                                 headers=self._headers(), json={"fields": toewijzing})
            return await self._reload(client, key)

    async def update_item(self, *, external_id: str, title: Optional[str],
                          description: Optional[str], state: Optional[str],
                          priority: Optional[str], assignee: Optional[str],
                          labels: Optional[List[str]],
                          acceptance_criteria: Optional[str] = None) -> ExternalItem:
        async with httpx.AsyncClient(timeout=60.0) as client:
            fields = await self._fields_payload(
                client, title=title, description=description, priority=priority,
                assignee=assignee, labels=labels,
                acceptance_criteria=acceptance_criteria, issue_key=external_id)
            if fields:
                resp = await client.put(f"{self.base_url}/rest/api/3/issue/{external_id}",
                                        headers=self._headers(), json={"fields": fields})
                self._raise_for(resp, f"issue {external_id} bijwerken")
            if state:
                await self._transition_to(client, external_id, state)
            return await self._reload(client, external_id)

    async def _transition_to(self, client: httpx.AsyncClient, key: str, state: str) -> None:
        # Al op de gevraagde status? Dan niets doen. Jira accepteert een
        # transitie naar dezelfde status en schrijft er een changelog-regel
        # voor ("In Progress -> In Progress"); dat is ruis in andermans
        # systeem en maakt de geschiedenis onleesbaar.
        huidig = await client.get(f"{self.base_url}/rest/api/3/issue/{key}",
                                  headers=self._headers(), params={"fields": "status"})
        if huidig.status_code < 400:
            nu = (((huidig.json().get("fields") or {}).get("status") or {}).get("name") or "")
            if nu.strip().lower() == state.strip().lower():
                return

        resp = await client.get(f"{self.base_url}/rest/api/3/issue/{key}/transitions",
                                headers=self._headers())
        self._raise_for(resp, f"transities van {key} ophalen")
        wanted = state.strip().lower()
        for tr in (resp.json().get("transitions") or []):
            names = {str(tr.get("name") or "").lower(),
                     str(((tr.get("to") or {}).get("name")) or "").lower()}
            if wanted in names:
                done = await client.post(f"{self.base_url}/rest/api/3/issue/{key}/transitions",
                                         headers=self._headers(),
                                         json={"transition": {"id": tr.get("id")}})
                self._raise_for(done, f"{key} naar '{state}' zetten")
                return
        raise RuntimeError(
            f"Jira kent voor {key} geen transitie naar '{state}' — controleer de "
            f"statusmapping van dit board tegen de workflow van het project")

    async def _reload(self, client: httpx.AsyncClient, key: str) -> ExternalItem:
        resp = await client.get(f"{self.base_url}/rest/api/3/issue/{key}",
                                headers=self._headers(),
                                params={"fields": ",".join(self._fields)})
        self._raise_for(resp, f"issue {key} herladen")
        return self._to_item(resp.json())

    async def add_comment(self, *, external_id: str, body: str) -> Optional[str]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{self.base_url}/rest/api/3/issue/{external_id}/comment",
                                     headers=self._headers(), json={"body": _text_to_adf(body)})
            self._raise_for(resp, f"opmerking plaatsen op {external_id}")
            return str((resp.json() or {}).get("id") or "")
