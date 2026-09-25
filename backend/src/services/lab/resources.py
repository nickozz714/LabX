"""
services/lab/resources.py

Reserveren van het spul waar er maar één van is in een lab: de browser, een
playground, een vaste poort.

**Waarom.** Sinds een planning meerdere tickets in dezelfde werker kan zetten,
zitten er twee agents tegelijk achter dezelfde sandbox-pc. Het meeste kan naast
elkaar; sommige dingen niet. Twee agents in dezelfde browser levert geen
foutmelding op maar een raadsel: je tabblad is weg, je login verdwenen, en in
het verslag staat niets waaruit dat blijkt.

**Wat dit wel en niet is.** Dit is een *afspraak*, geen slot op de deur. De
agent claimt, en LabX houdt bij wie wat heeft; niemand wordt technisch
tegengehouden om die browser toch te openen. Dat is bewust dezelfde keuze als
bij `board__claim`: een echte vergrendeling zou betekenen dat LabX weet wat een
commando gaat aanraken, en dat weet het niet.

**Waarom een claim verloopt.** Een agent die crasht, afgebroken wordt of
simpelweg vergeet vrij te geven, mag een resource niet voor eeuwig op slot
zetten. Elke claim heeft daarom een houdbaarheid; daarna telt hij niet meer mee
en zie je in de geschiedenis dát hij verlopen is.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from component_logging import get_logger
from models.claim_resource import (
    RESOURCE_GEDRAG, RESOURCE_SCOPES, ClaimResource, ResourceClaim,
)

log = get_logger(__name__)

# Hoe lang een `wacht=true` hoogstens blijft hangen. Langer dan dit is geen
# wachten meer maar vastlopen: de agent kan beter iets anders doen en later
# terugkomen.
MAX_WACHT_SECONDEN = 300
POLL_SECONDEN = 3

BUILTIN_RESOURCES: List[Dict[str, Any]] = [
    {
        "key": "chrome-browser",
        "label": "Browser (Chrome/Playwright)",
        "description": "De browser van het lab: één profiel, één venster. Twee agents die "
                       "tegelijk navigeren zien elkaars pagina's — en een login die de een "
                       "doet, gooit de ander eruit.",
        "scope": "werker", "gedrag": "wachten", "timeout_minutes": 30,
        "default_on": True,
    },
    {
        "key": "playground",
        "label": "Playground",
        "description": "De playground van dit lab. Er is er één van, en wie erin werkt wil "
                       "niet dat iemand anders er ondertussen doorheen loopt.",
        "scope": "werker", "gedrag": "wachten", "timeout_minutes": 30,
        "default_on": True,
    },
    {
        "key": "az-sessie",
        "label": "Azure-sessie (az / fab)",
        "description": "De container deelt één ingelogde az-sessie. Wie van abonnement of "
                       "tenant wisselt, doet dat ook voor de ander — midden in diens werk.",
        "scope": "werker", "gedrag": "wachten", "timeout_minutes": 15,
        "default_on": True,
    },
    {
        "key": "poort-8000",
        "label": "Poort 8000",
        "description": "De gebruikelijke poort voor een dev-server of preview. Twee servers "
                       "op dezelfde poort gaat niet; de tweede start gewoon niet.",
        "scope": "werker", "gedrag": "weigeren", "timeout_minutes": 60,
        "default_on": False,
    },
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def seed_builtin_resources(db: Session) -> int:
    """De meegeleverde resources in de tabel zetten als ze er nog niet zijn.

    Alleen TOEVOEGEN: wie een meegeleverde resource heeft aangepast (of
    uitgezet), houdt zijn eigen versie. Zelfde afspraak als bij de lab-extra's."""
    bestaand = {r.key for r in db.query(ClaimResource).all()}
    nieuw = 0
    now = _now_iso()
    for spec in BUILTIN_RESOURCES:
        if spec["key"] in bestaand:
            continue
        db.add(ClaimResource(
            key=spec["key"], label=spec["label"], description=spec.get("description"),
            scope=spec["scope"], gedrag=spec["gedrag"],
            timeout_minutes=int(spec["timeout_minutes"]),
            default_on=bool(spec.get("default_on")), is_enabled=True, builtin=True,
            created_at=now, updated_at=now))
        nieuw += 1
    if nieuw:
        db.commit()
        log.infox("Claimbare resources toegevoegd", aantal=nieuw)
    return nieuw


class ResourceService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── catalogus ───────────────────────────────────────────────────────────

    def catalogus(self, *, alleen_aan: bool = False) -> List[ClaimResource]:
        q = self.db.query(ClaimResource)
        if alleen_aan:
            q = q.filter(ClaimResource.is_enabled.is_(True))
        return q.order_by(ClaimResource.key).all()

    def resource(self, key: str) -> Optional[ClaimResource]:
        return (self.db.query(ClaimResource)
                .filter(ClaimResource.key == str(key or "").strip().lower()).first())

    def maak(self, **velden: Any) -> ClaimResource:
        key = str(velden.get("key") or "").strip().lower()
        if not key:
            raise ValueError("Een sleutel is verplicht")
        if self.resource(key) is not None:
            raise ValueError(f"'{key}' bestaat al")
        now = _now_iso()
        rij = ClaimResource(
            key=key, label=str(velden.get("label") or key)[:255],
            description=(velden.get("description") or None),
            scope=self._geldig(velden.get("scope"), RESOURCE_SCOPES, "werker"),
            gedrag=self._geldig(velden.get("gedrag"), RESOURCE_GEDRAG, "wachten"),
            timeout_minutes=max(1, min(int(velden.get("timeout_minutes") or 30), 24 * 60)),
            default_on=bool(velden.get("default_on")),
            is_enabled=bool(velden.get("is_enabled", True)),
            builtin=False, created_at=now, updated_at=now)
        self.db.add(rij)
        self.db.commit()
        self.db.refresh(rij)
        return rij

    def werk_bij(self, key: str, **velden: Any) -> ClaimResource:
        rij = self.resource(key)
        if rij is None:
            raise ValueError(f"'{key}' bestaat niet")
        if velden.get("label") is not None:
            rij.label = str(velden["label"])[:255]
        if "description" in velden:
            rij.description = velden["description"] or None
        if velden.get("scope") is not None:
            rij.scope = self._geldig(velden["scope"], RESOURCE_SCOPES, rij.scope)
        if velden.get("gedrag") is not None:
            rij.gedrag = self._geldig(velden["gedrag"], RESOURCE_GEDRAG, rij.gedrag)
        if velden.get("timeout_minutes") is not None:
            rij.timeout_minutes = max(1, min(int(velden["timeout_minutes"]), 24 * 60))
        if velden.get("default_on") is not None:
            rij.default_on = bool(velden["default_on"])
        if velden.get("is_enabled") is not None:
            rij.is_enabled = bool(velden["is_enabled"])
        rij.updated_at = _now_iso()
        self.db.commit()
        self.db.refresh(rij)
        return rij

    def verwijder(self, key: str) -> None:
        rij = self.resource(key)
        if rij is None:
            raise ValueError(f"'{key}' bestaat niet")
        self.db.delete(rij)
        self.db.commit()

    @staticmethod
    def _geldig(waarde: Any, toegestaan: tuple, standaard: str) -> str:
        tekst = str(waarde or "").strip().lower()
        return tekst if tekst in toegestaan else standaard

    @staticmethod
    def to_dict(rij: ClaimResource) -> Dict[str, Any]:
        return {"key": rij.key, "label": rij.label, "description": rij.description,
                "scope": rij.scope, "gedrag": rij.gedrag,
                "timeout_minutes": rij.timeout_minutes,
                "default_on": bool(rij.default_on), "is_enabled": bool(rij.is_enabled),
                "builtin": bool(rij.builtin),
                "created_at": rij.created_at, "updated_at": rij.updated_at}

    # ── welke gelden er in dit lab ──────────────────────────────────────────

    def voor_lab(self, lab) -> List[ClaimResource]:
        """De resources die in dit lab te claimen zijn.

        Niets ingesteld (NULL) betekent: de meegeleverde standaard. Dat is
        bewust geen lege lijst — een bestaand lab hoort de browser gewoon te
        kunnen reserveren zonder dat iemand eerst een vinkje zet."""
        aan = self.catalogus(alleen_aan=True)
        gekozen = getattr(lab, "claim_resources", None)
        if gekozen is None:
            return [r for r in aan if r.default_on]
        keys = {str(k) for k in gekozen}
        return [r for r in aan if r.key in keys]

    # ── claimen ─────────────────────────────────────────────────────────────

    def _sleutelfilter(self, q, resource: ClaimResource, lab_id: str,
                       worker_id: Optional[int]):
        q = q.filter(ResourceClaim.lab_id == str(lab_id),
                     ResourceClaim.resource_key == resource.key,
                     ResourceClaim.released_at.is_(None))
        if resource.scope == "werker":
            q = q.filter(ResourceClaim.worker_id.is_(None) if worker_id is None
                         else ResourceClaim.worker_id == int(worker_id))
        return q

    def _actieve_claim(self, resource: ClaimResource, lab_id: str,
                       worker_id: Optional[int]) -> Optional[ResourceClaim]:
        """De claim die er NU op zit — verlopen claims eerst opruimen.

        Het opruimen gebeurt hier en niet alleen in een opruimtaak, omdat de
        enige die er last van heeft precies degene is die nu staat te wachten."""
        rijen = self._sleutelfilter(self.db.query(ResourceClaim), resource,
                                    lab_id, worker_id).all()
        nu = _now_iso()
        levend = None
        for rij in rijen:
            if rij.expires_at and rij.expires_at <= nu:
                rij.released_at = nu
                rij.released_reason = "verlopen"
                continue
            levend = levend or rij
        self.db.commit()
        return levend

    def probeer(self, *, lab, worker_id: Optional[int], resource_key: str,
                houder: str, houder_soort: str = "sessie",
                houder_label: Optional[str] = None,
                reden: Optional[str] = None) -> Dict[str, Any]:
        """Eén poging. Geeft {ok: True} of wie hem vasthoudt."""
        key = str(resource_key or "").strip().lower()
        resource = self.resource(key)
        if resource is None or not resource.is_enabled:
            return {"ok": False, "onbekend": True,
                    "melding": (f"'{key}' is geen claimbare resource in dit lab. "
                                f"Beschikbaar: "
                                + (", ".join(r.key for r in self.voor_lab(lab)) or "geen"))}
        if resource.key not in {r.key for r in self.voor_lab(lab)}:
            return {"ok": False, "onbekend": True,
                    "melding": (f"'{key}' staat niet aan voor dit lab. Beschikbaar: "
                                + (", ".join(r.key for r in self.voor_lab(lab)) or "geen"))}

        werker = worker_id if resource.scope == "werker" else None
        bestaand = self._actieve_claim(resource, lab.id, werker)
        if bestaand is not None:
            if bestaand.houder == houder:
                # Alweer claimen is geen fout: de agent weet niet altijd meer
                # of hij hem al had, en dan is "ja, hij is van jou" het juiste
                # antwoord. Wel de houdbaarheid verversen.
                bestaand.expires_at = (_now() + timedelta(
                    minutes=resource.timeout_minutes)).isoformat()
                self.db.commit()
                return {"ok": True, "resource": resource.key, "al_van_jou": True,
                        "verloopt": bestaand.expires_at}
            return {"ok": False, "resource": resource.key,
                    "bezet_door": bestaand.houder_label or bestaand.houder,
                    "sinds": bestaand.created_at, "verloopt": bestaand.expires_at,
                    "reden": bestaand.reason,
                    "gedrag": resource.gedrag}

        rij = ResourceClaim(
            lab_id=str(lab.id), worker_id=werker, resource_key=resource.key,
            houder=str(houder)[:128], houder_soort=str(houder_soort)[:16],
            houder_label=(houder_label or None), reason=(reden or None),
            created_at=_now_iso(),
            expires_at=(_now() + timedelta(minutes=resource.timeout_minutes)).isoformat())
        self.db.add(rij)
        self.db.commit()
        log.infox("Resource geclaimd", lab_id=lab.id, werker=werker,
                  resource=resource.key, houder=houder_label or houder)
        return {"ok": True, "resource": resource.key, "verloopt": rij.expires_at}

    async def claim(self, *, lab, worker_id: Optional[int], resource_key: str,
                    houder: str, houder_soort: str = "sessie",
                    houder_label: Optional[str] = None, reden: Optional[str] = None,
                    wacht_seconden: int = 0) -> Dict[str, Any]:
        """Claimen, eventueel met wachten tot hij vrijkomt.

        Wachten doet de AANROEPER en niet de resource: de beurt van deze agent
        staat stil, de rest van LabX loopt door. Dat is de bedoeling — de agent
        die wacht, wacht op iets dat seconden tot minuten duurt, en wil daarna
        gewoon verder."""
        eerste = self.probeer(lab=lab, worker_id=worker_id, resource_key=resource_key,
                              houder=houder, houder_soort=houder_soort,
                              houder_label=houder_label, reden=reden)
        if eerste.get("ok") or eerste.get("onbekend"):
            return eerste
        resource = self.resource(resource_key)
        budget = max(0, min(int(wacht_seconden or 0), MAX_WACHT_SECONDEN))
        if budget <= 0 or (resource is not None and resource.gedrag == "weigeren"):
            return eerste
        eind = _now() + timedelta(seconds=budget)
        while _now() < eind:
            await asyncio.sleep(POLL_SECONDEN)
            poging = self.probeer(lab=lab, worker_id=worker_id, resource_key=resource_key,
                                  houder=houder, houder_soort=houder_soort,
                                  houder_label=houder_label, reden=reden)
            if poging.get("ok"):
                poging["gewacht_seconden"] = budget - int((eind - _now()).total_seconds())
                return poging
        laatste = self.probeer(lab=lab, worker_id=worker_id, resource_key=resource_key,
                               houder=houder, houder_soort=houder_soort,
                               houder_label=houder_label, reden=reden)
        laatste["gewacht_seconden"] = budget
        return laatste

    def release(self, *, lab_id: str, houder: str,
                resource_keys: Optional[List[str]] = None,
                reden: str = "vrijgegeven") -> int:
        """Vrijgeven wat deze houder vasthield. Zonder lijst: alles."""
        q = (self.db.query(ResourceClaim)
             .filter(ResourceClaim.lab_id == str(lab_id),
                     ResourceClaim.houder == str(houder),
                     ResourceClaim.released_at.is_(None)))
        if resource_keys:
            q = q.filter(ResourceClaim.resource_key.in_(
                [str(k).strip().lower() for k in resource_keys]))
        rijen = q.all()
        nu = _now_iso()
        for rij in rijen:
            rij.released_at = nu
            rij.released_reason = reden[:255]
        if rijen:
            self.db.commit()
            log.infox("Resources vrijgegeven", lab_id=lab_id, houder=houder,
                      aantal=len(rijen))
        return len(rijen)

    def actief(self, lab_id: str, *, worker_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Wat er nu vastgehouden wordt in dit lab (verlopen claims tellen niet)."""
        q = (self.db.query(ResourceClaim)
             .filter(ResourceClaim.lab_id == str(lab_id),
                     ResourceClaim.released_at.is_(None)))
        if worker_id is not None:
            q = q.filter(ResourceClaim.worker_id == int(worker_id))
        nu = _now_iso()
        uit = []
        for rij in q.order_by(ResourceClaim.created_at).all():
            if rij.expires_at and rij.expires_at <= nu:
                continue
            uit.append({"resource": rij.resource_key, "worker_id": rij.worker_id,
                        "houder": rij.houder, "houder_soort": rij.houder_soort,
                        "houder_label": rij.houder_label, "reden": rij.reason,
                        "sinds": rij.created_at, "verloopt": rij.expires_at})
        return uit

    def ruim_verlopen_op(self) -> int:
        """Achtergrondtaak: verlopen claims afsluiten.

        `_actieve_claim` doet dit ook, maar alleen voor een resource waar
        iemand naar vraagt. Zonder deze ronde blijft een verlopen claim in het
        overzicht staan alsof er iemand zit."""
        nu = _now_iso()
        rijen = (self.db.query(ResourceClaim)
                 .filter(ResourceClaim.released_at.is_(None),
                         ResourceClaim.expires_at.isnot(None),
                         ResourceClaim.expires_at <= nu).all())
        for rij in rijen:
            rij.released_at = nu
            rij.released_reason = "verlopen"
        if rijen:
            self.db.commit()
            log.infox("Verlopen resource-claims opgeruimd", aantal=len(rijen))
        return len(rijen)
