"""
services/secrets/vault.py

Geheimen die overal te gebruiken zijn, en de ene regel die dit veilig maakt.

**De regel.** Een `{{secret:naam}}` wordt alleen ingevuld op weg naar BUITEN —
een tool-aanroep, een commando, een HTTP-verzoek. Nooit op weg naar het model.
In een prompt, een skill-instructie of een agent-opdracht blijft de
verwijzing staan zoals hij is; het model ziet dus de naam en nooit de waarde.
Dat is geen implementatiedetail maar de hele afspraak: het model mag geheimen
KIEZEN, niet KENNEN.

**Waarom dat werkt.** Het model schrijft `{{secret:teams-webhook}}` in het
argument van een tool. Dat is tekst die het zelf verzint, zonder te weten wat
erin zit. LabX vervangt hem vlak voor de aanroep, en maskeert de waarde weer in
alles wat terugkomt. Wat er in het audit-spoor, in de tool-invoer en in de
uitvoer belandt, is dus de NAAM.

**Onbekende namen zijn een fout, geen stilte.** Zou LabX een onbekende
verwijzing laten staan, dan vertrekt `{{secret:typefout}}` letterlijk naar een
externe API — die er niets van begrijpt, of hem opslaat. Het model krijgt in
plaats daarvan te horen welke geheimen er wél zijn.

**Reikwijdte.** Een geheim geldt overal, of alleen in bepaalde labs. Een
sleutel van de ene klant hoort niet te werken in het lab van een andere; dat is
dezelfde afweging als bij de netwerk- en resource-instellingen.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from component_logging import get_logger
from models.secret import Secret

log = get_logger(__name__)

# `{{secret:naam}}`, met of zonder spaties. Zelfde vorm als de lab-geheimen
# (services/lab/secrets.py) — het is voor wie het typt hetzelfde begrip.
VERWIJZING = re.compile(r"\{\{\s*secret\s*:\s*([A-Za-z0-9_-]{1,64})\s*\}\}")
_NAAM_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Korte waarden maskeren we niet: dan raak je willekeurige tekst in een
# uitvoer die er niets mee te maken heeft.
MIN_MASKEERLENGTE = 8


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def verwijzingen_in(waarde: Any) -> List[str]:
    """Welke geheimen noemt deze waarde? Kijkt ook in lijsten en objecten, want
    een tool-argument is zelden een platte string."""
    gevonden: List[str] = []

    def _loop(x: Any) -> None:
        if isinstance(x, str):
            for naam in VERWIJZING.findall(x):
                if naam not in gevonden:
                    gevonden.append(naam)
        elif isinstance(x, dict):
            for v in x.values():
                _loop(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                _loop(v)

    _loop(waarde)
    return gevonden


class VaultService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── beheer ──────────────────────────────────────────────────────────────

    def lijst(self) -> List[Secret]:
        return self.db.query(Secret).order_by(Secret.name).all()

    def haal(self, naam: str) -> Optional[Secret]:
        return self.db.query(Secret).filter(Secret.name == str(naam or "").strip()).first()

    def zet(self, *, naam: str, waarde: Optional[str] = None,
            omschrijving: Optional[str] = None,
            lab_ids: Optional[List[str]] = None) -> Secret:
        """Aanmaken of bijwerken. Een lege waarde bij het bijwerken betekent
        "laat staan": het scherm krijgt hem toch nooit terug, dus een leeg veld
        is de normale toestand als je alleen de omschrijving aanpast."""
        from utils.crypto import encrypt

        naam = str(naam or "").strip()
        if not _NAAM_OK.match(naam):
            raise ValueError("Een naam mag alleen letters, cijfers, - en _ bevatten "
                             "(hij komt in {{secret:...}} te staan).")
        rij = self.haal(naam)
        now = _now_iso()
        if rij is None:
            if not (waarde or "").strip():
                raise ValueError("Een nieuw geheim heeft een waarde nodig.")
            rij = Secret(name=naam, value_encrypted=encrypt(waarde),
                         created_at=now, updated_at=now)
            self.db.add(rij)
        elif (waarde or "").strip():
            rij.value_encrypted = encrypt(waarde)
        if omschrijving is not None:
            rij.description = omschrijving or None
        if lab_ids is not None:
            rij.lab_ids = json.dumps([str(x) for x in lab_ids]) if lab_ids else None
        rij.updated_at = now
        self.db.commit()
        self.db.refresh(rij)
        return rij

    def verwijder(self, naam: str) -> bool:
        rij = self.haal(naam)
        if rij is None:
            return False
        self.db.delete(rij)
        self.db.commit()
        return True

    @staticmethod
    def labs_van(rij: Secret) -> List[str]:
        try:
            return list(json.loads(rij.lab_ids)) if rij.lab_ids else []
        except (TypeError, ValueError):
            return []

    def geldt_in(self, rij: Secret, lab_id: Optional[str]) -> bool:
        """Leeg = overal. Anders alleen in de genoemde labs — en dan óók niet
        buiten een lab om, want dan is er niets om aan te toetsen."""
        labs = self.labs_van(rij)
        if not labs:
            return True
        return bool(lab_id) and str(lab_id) in labs

    def beschikbaar(self, lab_id: Optional[str] = None) -> List[Secret]:
        return [r for r in self.lijst() if self.geldt_in(r, lab_id)]

    def to_dict(self, rij: Secret) -> Dict[str, Any]:
        """Nooit de waarde. Wel de naam, waar hij geldt en hoe vaak hij is
        gebruikt — genoeg om te beheren, te weinig om te lekken."""
        return {"name": rij.name, "description": rij.description,
                "placeholder": "{{secret:%s}}" % rij.name,
                "lab_ids": self.labs_van(rij),
                "created_at": rij.created_at, "updated_at": rij.updated_at,
                "last_used_at": rij.last_used_at, "use_count": rij.use_count}

    # ── invullen (alleen naar buiten) ───────────────────────────────────────

    def waarde_van(self, rij: Secret) -> Optional[str]:
        from utils.crypto import decrypt
        try:
            return decrypt(rij.value_encrypted)
        except Exception as exc:  # noqa: BLE001
            log.warningx("Geheim kon niet ontsleuteld worden", naam=rij.name,
                         error=str(exc)[:200])
            return None

    def vul_in(self, waarde: Any, *, lab_id: Optional[str] = None
               ) -> Tuple[Any, List[str], List[str]]:
        """Vervang elke `{{secret:naam}}` door zijn waarde.

        Geeft terug: (de ingevulde waarde, welke geheimen zijn gebruikt, welke
        namen niet bestonden). Die laatste lijst is belangrijk — de aanroeper
        hoort daarop te STOPPEN in plaats van een letterlijke `{{secret:...}}`
        naar een externe dienst te sturen.
        """
        namen = verwijzingen_in(waarde)
        if not namen:
            return waarde, [], []
        beschikbaar = {r.name: r for r in self.beschikbaar(lab_id)}
        gebruikt: List[str] = []
        onbekend: List[str] = []
        waarden: Dict[str, str] = {}
        for naam in namen:
            rij = beschikbaar.get(naam)
            if rij is None:
                onbekend.append(naam)
                continue
            echt = self.waarde_van(rij)
            if echt is None:
                onbekend.append(naam)
                continue
            waarden[naam] = echt
            gebruikt.append(naam)

        def _vervang(x: Any) -> Any:
            if isinstance(x, str):
                return VERWIJZING.sub(
                    lambda m: waarden.get(m.group(1), m.group(0)), x)
            if isinstance(x, dict):
                return {k: _vervang(v) for k, v in x.items()}
            if isinstance(x, list):
                return [_vervang(v) for v in x]
            return x

        ingevuld = _vervang(waarde)
        if gebruikt:
            nu = _now_iso()
            for naam in gebruikt:
                rij = beschikbaar[naam]
                rij.last_used_at = nu
                rij.use_count = int(rij.use_count or 0) + 1
            self.db.commit()
            log.infox("Geheimen ingevuld", aantal=len(gebruikt), namen=",".join(gebruikt),
                      lab_id=lab_id)
        return ingevuld, gebruikt, onbekend

    def maskeer(self, tekst: Optional[str], *, lab_id: Optional[str] = None) -> str:
        """Waarden die tóch in een uitvoer belanden onleesbaar maken.

        Het invullen zorgt dat een waarde niet in de INVOER staat; dit vangt de
        andere kant — een API die je sleutel terugspiegelt in een foutmelding,
        een tool die zijn eigen aanroep echoot."""
        uit = tekst or ""
        if not uit:
            return uit
        for rij in self.beschikbaar(lab_id):
            waarde = self.waarde_van(rij)
            if waarde and len(waarde) >= MIN_MASKEERLENGTE and waarde in uit:
                uit = uit.replace(waarde, f"{{{{secret:{rij.name}}}}}")
        return uit
