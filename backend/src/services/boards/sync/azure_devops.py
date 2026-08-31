"""
services/boards/sync/azure_devops.py

Azure DevOps Boards (work items) via de REST API, api-version 7.1.

Config (`Board.provider_config`):
    organization       "mijn-org"                (verplicht)
    project            "MijnProject"             (verplicht)
    wiql               eigen WIQL-query          (optioneel — anders: alle
                                                  work items van het project,
                                                  nieuwste eerst)
    area_path          "MijnProject\\Team"        (optioneel, filtert de default-query)
    work_item_type     "Task" / "User Story"     (default "Task", voor create)
    max_items          default 200

Acceptatiecriteria gaan naar `Microsoft.VSTS.Common.AcceptanceCriteria`. Dat
veld bestaat niet op elk work item type (een Task heeft het standaard niet, een
User Story/PBI wel) — schrijven faalt dan met een leesbare DevOps-fout die de
sync per ticket opvangt.

Auth: een Personal Access Token met scope *Work Items (read, write)* —
DevOps' basic-auth vorm is base64(":" + PAT).

Bewust géén Azure-AD/OBO-pad hier: een board synchroniseert op de achtergrond
(ook via de scheduler, zonder gebruiker in de lus), en een PAT is de enige
credential die dat zonder interactieve refresh overleeft.
"""
from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

import httpx

from component_logging import get_logger
from services.boards.sync.base import ExternalComment, ExternalItem, SyncAdapter

log = get_logger(__name__)

_API = "7.1"
_COMMENTS_API = "7.1-preview.4"
_FIELDS = [
    "System.Id", "System.Title", "System.Description", "System.State",
    "System.AssignedTo", "System.Tags", "System.WorkItemType", "System.Rev",
    "Microsoft.VSTS.Common.Priority",
    "Microsoft.VSTS.Common.AcceptanceCriteria",
]
# DevOps' Priority is 1..4 (1 = hoogst); LabX gebruikt woorden.
_PRIORITY_FROM_DEVOPS = {1: "urgent", 2: "high", 3: "normal", 4: "low"}
_PRIORITY_TO_DEVOPS = {"urgent": 1, "high": 2, "normal": 3, "low": 4}


def _html_to_text(html: Optional[str]) -> str:
    """System.Description is HTML. Geen parser-dependency toevoegen voor wat
    in de praktijk <div>/<br>/<p> is: tags strippen en entities terugzetten
    geeft leesbare tekst, en de bron blijft leidend voor de opmaak."""
    if not html:
        return ""
    import html as html_mod
    import re
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</\s*(p|div|li|tr|h[1-6])\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*li\s*[^>]*>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html_mod.unescape(text).strip()


def _text_to_html(text: Optional[str]) -> str:
    import html as html_mod
    if not text:
        return ""
    return "<div>" + html_mod.escape(text).replace("\n", "<br>") + "</div>"


class AzureDevOpsAdapter(SyncAdapter):
    provider = "azure_devops"

    def __init__(self, config: Dict[str, Any], secret: Optional[str]):
        super().__init__(config, secret)
        self.organization = self._require("organization")
        self.project = self._require("project")
        self.base = f"https://dev.azure.com/{self.organization}/{self.project}/_apis"

    def _headers(self, *, patch: bool = False) -> Dict[str, str]:
        token = base64.b64encode(f":{self._require_secret()}".encode()).decode()
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json-patch+json" if patch else "application/json",
            "Accept": "application/json",
        }

    def _wiql(self) -> str:
        custom = str(self.config.get("wiql") or "").strip()
        if custom:
            return custom
        clauses = ["[System.TeamProject] = @project"]
        area = str(self.config.get("area_path") or "").strip()
        if area:
            clauses.append(f"[System.AreaPath] UNDER '{area}'")
        return ("SELECT [System.Id] FROM WorkItems WHERE "
                + " AND ".join(clauses)
                + " ORDER BY [System.ChangedDate] DESC")

    @staticmethod
    def _raise_for(resp: httpx.Response, what: str) -> None:
        if resp.status_code >= 400:
            detail = resp.text[:500]
            # DevOps antwoordt op een verkeerde/verlopen PAT met een HTML
            # loginpagina en 203 of 401 — die tekst is voor een gebruiker
            # onleesbaar, dus vertaal hem naar de echte oorzaak.
            if resp.status_code in (401, 203) or "Azure DevOps Services | Sign In" in detail:
                raise RuntimeError("Azure DevOps weigerde de PAT (ongeldig, verlopen of "
                                   "te weinig scopes — nodig: Work Items read/write)")
            raise RuntimeError(f"Azure DevOps: {what} mislukt ({resp.status_code}) — {detail}")

    async def fetch_items(self, *, with_comments: bool = True) -> List[ExternalItem]:
        max_items = int(self.config.get("max_items") or 200)
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{self.base}/wit/wiql?api-version={_API}",
                                     headers=self._headers(),
                                     json={"query": self._wiql()})
            self._raise_for(resp, "WIQL-query")
            ids = [str(w["id"]) for w in (resp.json().get("workItems") or [])][:max_items]
            if not ids:
                return []

            items: List[ExternalItem] = []
            # workitemsbatch accepteert maximaal 200 ids per aanroep.
            for chunk_start in range(0, len(ids), 200):
                chunk = ids[chunk_start:chunk_start + 200]
                batch = await client.post(
                    f"https://dev.azure.com/{self.organization}/_apis/wit/workitemsbatch?api-version={_API}",
                    headers=self._headers(),
                    json={"ids": [int(i) for i in chunk], "fields": _FIELDS})
                self._raise_for(batch, "work items ophalen")
                for raw in (batch.json().get("value") or []):
                    items.append(self._to_item(raw))

            if with_comments:
                for item in items:
                    item.comments = await self._fetch_comments(client, item.external_id)
        return items

    async def fetch_items_by_keys(self, keys: List[str]) -> List[ExternalItem]:
        """Work items op id, buiten de WIQL van het board om."""
        ids = [int(k) for k in keys if str(k).isdigit()]
        out: List[ExternalItem] = []
        if not ids:
            return out
        async with httpx.AsyncClient(timeout=60.0) as client:
            for start in range(0, len(ids), 200):
                batch = await client.post(
                    f"https://dev.azure.com/{self.organization}/_apis/wit/workitemsbatch?api-version={_API}",
                    headers=self._headers(),
                    json={"ids": ids[start:start + 200], "fields": _FIELDS})
                if batch.status_code >= 400:
                    log.warningx("DevOps: work items op id ophalen mislukt",
                                 status=batch.status_code)
                    continue
                out.extend(self._to_item(raw) for raw in (batch.json().get("value") or []))
        return out

    async def discover_states(self) -> List[str]:
        """De statussen van het work item type van dit board — ook die waar nu
        geen work item in staat."""
        wit = str(self.config.get("work_item_type") or "Task")
        url = (f"https://dev.azure.com/{self.organization}/{self.project}"
               f"/_apis/wit/workitemtypes/{wit}/states")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self._headers(),
                                        params={"api-version": _API})
                if resp.status_code >= 400:
                    return []
                return [str(v.get("name")) for v in (resp.json().get("value") or [])
                        if v.get("name")]
        except Exception as exc:  # noqa: BLE001
            log.warningx("DevOps-statussen ophalen mislukt", error=str(exc)[:200])
            return []

    def _to_item(self, raw: Dict[str, Any]) -> ExternalItem:
        fields = raw.get("fields") or {}
        wid = str(raw.get("id"))
        assigned = fields.get("System.AssignedTo")
        if isinstance(assigned, dict):
            assignee = assigned.get("displayName") or assigned.get("uniqueName")
        else:
            assignee = assigned
        tags = [t.strip() for t in str(fields.get("System.Tags") or "").split(";") if t.strip()]
        priority = _PRIORITY_FROM_DEVOPS.get(fields.get("Microsoft.VSTS.Common.Priority"))
        return ExternalItem(
            external_id=wid,
            external_key=f"#{wid}",
            external_url=(f"https://dev.azure.com/{self.organization}/{self.project}"
                          f"/_workitems/edit/{wid}"),
            title=str(fields.get("System.Title") or f"Work item {wid}"),
            description=_html_to_text(fields.get("System.Description")),
            state=fields.get("System.State"),
            # DevOps laat een leeg/onbestaand veld wég uit de response. Het
            # verschil telt: ontbreekt het, dan is er niets om te synchroniseren
            # (None) — zou hier "" staan, dan wiste elke pull de lokale criteria.
            acceptance_criteria=(
                _html_to_text(fields["Microsoft.VSTS.Common.AcceptanceCriteria"])
                if "Microsoft.VSTS.Common.AcceptanceCriteria" in fields else None),
            priority=priority,
            assignee=assignee,
            labels=tags,
            rev=str(fields.get("System.Rev") or raw.get("rev") or ""),
        )

    async def _fetch_comments(self, client: httpx.AsyncClient, wid: str) -> List[ExternalComment]:
        try:
            resp = await client.get(
                f"{self.base}/wit/workItems/{wid}/comments?api-version={_COMMENTS_API}",
                headers=self._headers())
            if resp.status_code >= 400:
                return []
            out = []
            for c in (resp.json().get("comments") or []):
                created_by = c.get("createdBy") or {}
                out.append(ExternalComment(
                    external_id=str(c.get("id")),
                    author=created_by.get("displayName") or "Azure DevOps",
                    body=_html_to_text(c.get("text")),
                    created_at=c.get("createdDate"),
                ))
            return out
        except Exception as exc:  # noqa: BLE001 — opmerkingen mogen een sync niet breken
            log.warningx("DevOps-opmerkingen ophalen mislukt", work_item=wid, error=str(exc)[:200])
            return []

    # ── schrijven ───────────────────────────────────────────────────────────

    def _patch_ops(self, *, title: Optional[str], description: Optional[str],
                   state: Optional[str], priority: Optional[str], assignee: Optional[str],
                   labels: Optional[List[str]],
                   acceptance_criteria: Optional[str] = None) -> List[Dict[str, Any]]:
        ops: List[Dict[str, Any]] = []

        def add(path: str, value: Any) -> None:
            ops.append({"op": "add", "path": path, "value": value})

        if title is not None:
            add("/fields/System.Title", title)
        if description is not None:
            add("/fields/System.Description", _text_to_html(description))
        if acceptance_criteria is not None:
            # Niet elk work item type heeft dit veld (Task bv. niet standaard);
            # DevOps antwoordt dan met een leesbare fout die de sync per ticket
            # opvangt — beter dan de criteria stilletjes onder de omschrijving
            # plakken en de opdracht vervuilen.
            add("/fields/Microsoft.VSTS.Common.AcceptanceCriteria",
                _text_to_html(acceptance_criteria))
        if state:
            add("/fields/System.State", state)
        if priority and priority in _PRIORITY_TO_DEVOPS:
            add("/fields/Microsoft.VSTS.Common.Priority", _PRIORITY_TO_DEVOPS[priority])
        if assignee:
            add("/fields/System.AssignedTo", assignee)
        if labels is not None:
            add("/fields/System.Tags", "; ".join(str(x) for x in labels))
        return ops

    async def create_item(self, *, title: str, description: Optional[str], state: Optional[str],
                          priority: Optional[str], assignee: Optional[str],
                          labels: List[str],
                          acceptance_criteria: Optional[str] = None) -> ExternalItem:
        wit = str(self.config.get("work_item_type") or "Task")
        ops = self._patch_ops(title=title, description=description, state=state,
                              priority=priority, assignee=assignee, labels=labels,
                              acceptance_criteria=acceptance_criteria)
        area = str(self.config.get("area_path") or "").strip()
        if area:
            ops.append({"op": "add", "path": "/fields/System.AreaPath", "value": area})
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base}/wit/workitems/${wit}?api-version={_API}",
                headers=self._headers(patch=True), json=ops)
            self._raise_for(resp, f"work item aanmaken ({wit})")
            return self._to_item(resp.json())

    async def update_item(self, *, external_id: str, title: Optional[str],
                          description: Optional[str], state: Optional[str],
                          priority: Optional[str], assignee: Optional[str],
                          labels: Optional[List[str]],
                          acceptance_criteria: Optional[str] = None) -> ExternalItem:
        ops = self._patch_ops(title=title, description=description, state=state,
                              priority=priority, assignee=assignee, labels=labels,
                              acceptance_criteria=acceptance_criteria)
        if not ops:
            raise RuntimeError("Niets te updaten")
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.patch(
                f"{self.base}/wit/workitems/{external_id}?api-version={_API}",
                headers=self._headers(patch=True), json=ops)
            self._raise_for(resp, f"work item {external_id} bijwerken")
            return self._to_item(resp.json())

    async def add_comment(self, *, external_id: str, body: str) -> Optional[str]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base}/wit/workItems/{external_id}/comments?api-version={_COMMENTS_API}",
                headers=self._headers(), json={"text": _text_to_html(body)})
            self._raise_for(resp, f"opmerking plaatsen op {external_id}")
            return str((resp.json() or {}).get("id") or "")
