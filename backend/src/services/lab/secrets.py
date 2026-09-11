"""
services/lab/secrets.py

Geheimen van een lab, en hoe ze in een commando terechtkomen zonder dat de
waarde ergens als tekst opduikt.

**Waarom dit bestaat.** Een agent die met Fabric of Azure werkt, haalt eerst een
access-token op en gebruikt dat daarna in tien commando's. Die commando's zijn
TEKST: ze staan in het audit-spoor, ze gaan als tool-invoer terug naar het
model, en ze komen in de uitvoer terecht zodra een script iets echoot. Zo
reisde er een geldig OAuth-token door de hele keten — niet door slordigheid,
maar omdat een token in een shell-commando nu eenmaal tekst is.

**Hoe de vervanging werkt, en waarom juist zo.** De agent schrijft
`{{secret:fabric}}`. De voor de hand liggende oplossing is die tekst te
vervangen door de waarde vlak voor het uitvoeren — maar dan staat de waarde
alsnog op de commandoregel van `docker exec`, en die is te zien in `ps` op de
HOST. Daarom gebeurt het in twee stappen:

1. De waarden gaan naar een bestand IN de container (`/run/labx-secrets.env`,
   mode 600, alleen root leest het).
2. Het commando krijgt `"$LABX_SECRET_FABRIC"` te zien, met een regel ervoor
   die dat bestand inleest.

De waarde staat daarmee op geen enkele commandoregel: niet in het audit-spoor,
niet in `ps` op de host, niet in `ps` in de container, en niet in de prompt.

**Verlopende geheimen.** Een Azure-token leeft een uur. Een geheim van het
soort `commando` wordt daarom vers gemaakt zodra het te oud is, door dát
commando in het lab te draaien. De agent merkt daar niets van en hoeft nergens
aan te denken — wat precies de bedoeling is, want "de agent moet eraan denken"
is hoe tokens in de eerste plaats gingen rondslingeren.
"""
from __future__ import annotations

import re
import shlex
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from component_logging import get_logger
from models.lab_secret import LabSecret

log = get_logger(__name__)

# Waar de waarden in de container staan. Onder /run: dat is een tmpfs in de
# meeste images, dus de waarden overleven een herstart van de container niet —
# precies wat je wilt voor iets dat toch ververst wordt.
SECRETS_BESTAND = "/run/labx-secrets.env"

# `{{secret:naam}}`, met of zonder spaties eromheen.
VERWIJZING = re.compile(r"\{\{\s*secret\s*:\s*([A-Za-z0-9_-]{1,64})\s*\}\}")

_NAAM_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def envnaam(naam: str) -> str:
    """`fabric-token` -> `LABX_SECRET_FABRIC_TOKEN`. Hoofdletters en
    underscores, want een omgevingsvariabele kan niet anders."""
    return "LABX_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", naam).upper()


def geldige_naam(naam: str) -> bool:
    return bool(_NAAM_OK.match(naam or ""))


def verwijzingen_in(command: Optional[str]) -> List[str]:
    """Welke geheimen noemt dit commando? Zonder dubbelen, in volgorde."""
    uit: List[str] = []
    for naam in VERWIJZING.findall(command or ""):
        if naam not in uit:
            uit.append(naam)
    return uit


class SecretService:
    def __init__(self, db: Session):
        self.db = db

    # ── beheer ──────────────────────────────────────────────────────────────

    def lijst(self, lab_id: str) -> List[LabSecret]:
        return (self.db.query(LabSecret)
                .filter(LabSecret.lab_id == lab_id)
                .order_by(LabSecret.name).all())

    def haal(self, lab_id: str, naam: str) -> Optional[LabSecret]:
        return (self.db.query(LabSecret)
                .filter(LabSecret.lab_id == lab_id, LabSecret.name == naam).first())

    def zet(self, lab_id: str, *, naam: str, waarde: Optional[str] = None,
            commando: Optional[str] = None, omschrijving: Optional[str] = None,
            ttl_minuten: Optional[int] = None) -> LabSecret:
        from utils.crypto import encrypt

        if not geldige_naam(naam):
            raise ValueError("Een naam mag alleen letters, cijfers, - en _ bevatten "
                             "(hij wordt ook een omgevingsvariabele).")
        if not (waarde or commando):
            raise ValueError("Geef een waarde of een commando dat de waarde maakt.")
        rij = self.haal(lab_id, naam)
        now = _now_iso()
        if rij is None:
            rij = LabSecret(lab_id=lab_id, name=naam, created_at=now, updated_at=now)
            self.db.add(rij)
        if omschrijving is not None:
            rij.description = omschrijving or None
        if commando:
            rij.kind = "commando"
            rij.produce_command = commando
            # Een nieuw commando maakt de oude waarde verdacht: weggooien is
            # hier goedkoper dan hem per ongeluk blijven gebruiken.
            rij.value_encrypted = None
            rij.refreshed_at = None
        elif waarde:
            rij.kind = "waarde"
            rij.value_encrypted = encrypt(waarde)
            rij.produce_command = None
            rij.refreshed_at = now
        if ttl_minuten is not None:
            rij.ttl_minutes = max(1, min(int(ttl_minuten), 24 * 60))
        rij.last_error = None
        rij.updated_at = now
        self.db.commit()
        self.db.refresh(rij)
        return rij

    def verwijder(self, lab_id: str, naam: str) -> bool:
        rij = self.haal(lab_id, naam)
        if rij is None:
            return False
        self.db.delete(rij)
        self.db.commit()
        return True

    @staticmethod
    def to_dict(rij: LabSecret) -> Dict[str, Any]:
        """Nooit de waarde. Wel of er een is, en hoe vers."""
        return {
            "name": rij.name, "description": rij.description, "kind": rij.kind,
            "has_value": bool(rij.value_encrypted),
            "produce_command": rij.produce_command,
            "ttl_minutes": rij.ttl_minutes,
            "refreshed_at": rij.refreshed_at, "last_used_at": rij.last_used_at,
            "last_error": rij.last_error,
            "placeholder": "{{secret:%s}}" % rij.name,
        }

    # ── waarde ophalen (en zo nodig vernieuwen) ─────────────────────────────

    def _verlopen(self, rij: LabSecret) -> bool:
        if rij.kind != "commando":
            return False
        if not rij.value_encrypted or not rij.refreshed_at:
            return True
        try:
            ouderdom = _now() - datetime.fromisoformat(rij.refreshed_at)
        except ValueError:
            return True
        return ouderdom > timedelta(minutes=max(1, int(rij.ttl_minutes or 50)))

    async def waarde(self, rij: LabSecret, *, runtime, container_id: str) -> Optional[str]:
        """De bruikbare waarde, desnoods vers gemaakt.

        Vernieuwen gebeurt IN het lab: het commando dat een token maakt heeft
        de `az`-sessie van dat lab nodig, en die staat nergens anders.
        """
        from utils.crypto import decrypt

        if self._verlopen(rij) and rij.produce_command:
            await self._vernieuw(rij, runtime=runtime, container_id=container_id)
        if not rij.value_encrypted:
            return None
        try:
            return decrypt(rij.value_encrypted)
        except Exception as exc:  # noqa: BLE001
            log.warningx("Geheim kon niet ontsleuteld worden", naam=rij.name,
                         error=str(exc)[:200])
            return None

    async def _vernieuw(self, rij: LabSecret, *, runtime, container_id: str) -> None:
        from utils.crypto import encrypt

        res = await runtime.exec(
            container_id, ["sh", "-lc", rij.produce_command], timeout=120)
        uit = (res.get("output") or "").strip()
        if res.get("exit_code") != 0 or not uit:
            rij.last_error = (f"exit {res.get('exit_code')}: "
                              f"{(res.get('output') or 'geen uitvoer')[:300]}")
            rij.updated_at = _now_iso()
            self.db.commit()
            log.warningx("Geheim vernieuwen mislukt", naam=rij.name, error=rij.last_error[:200])
            return
        # Alleen de LAATSTE regel: `az ... -o tsv` schrijft soms een
        # waarschuwing naar stdout vóór de waarde, en die zou anders meegaan.
        rij.value_encrypted = encrypt(uit.splitlines()[-1].strip())
        rij.refreshed_at = _now_iso()
        rij.last_error = None
        rij.updated_at = rij.refreshed_at
        self.db.commit()
        log.infox("Geheim vernieuwd", naam=rij.name)

    # ── de vervanging ───────────────────────────────────────────────────────

    async def bereid_voor(self, lab_id: str, command: str, *,
                          runtime, container_id: str) -> Tuple[str, List[str], List[str]]:
        """Zet een commando met `{{secret:…}}` om in iets uitvoerbaars.

        Geeft terug: (commando, gebruikte namen, onbekende namen). De waarden
        zelf komen NIET in het commando — die gaan naar een bestand in de
        container, en het commando leest dat bestand in.
        """
        namen = verwijzingen_in(command)
        if not namen:
            return command, [], []

        gevonden: Dict[str, str] = {}
        onbekend: List[str] = []
        for naam in namen:
            rij = self.haal(lab_id, naam)
            if rij is None:
                onbekend.append(naam)
                continue
            waarde = await self.waarde(rij, runtime=runtime, container_id=container_id)
            if waarde is None:
                onbekend.append(naam)
                continue
            gevonden[naam] = waarde
            rij.last_used_at = _now_iso()
        if gevonden:
            self.db.commit()
            await self._schrijf_bestand(gevonden, runtime=runtime, container_id=container_id)

        # Verwijzingen vervangen door de VARIABELE, niet door de waarde.
        def _vervang(m: "re.Match[str]") -> str:
            naam = m.group(1)
            if naam in gevonden:
                # `${VAR}` en niet `"$VAR"`: de verwijzing staat meestal al
                # BINNEN aanhalingstekens (`-H "Authorization: Bearer {{…}}"`),
                # en er dan nog een paar omheen zetten levert
                # `Bearer "$VAR""` op. Dat gaat in bash net goed en leest
                # verschrikkelijk. `${VAR}` klopt zowel binnen dubbele
                # aanhalingstekens als kaal; een token bevat geen spaties, dus
                # woordsplitsing speelt niet.
                return "${%s}" % envnaam(naam)
            return m.group(0)

        nieuw = VERWIJZING.sub(_vervang, command)
        if gevonden:
            nieuw = (f'set -a; . {SECRETS_BESTAND} 2>/dev/null; set +a\n' + nieuw)
        return nieuw, list(gevonden), onbekend

    async def _schrijf_bestand(self, waarden: Dict[str, str], *,
                               runtime, container_id: str) -> None:
        """De waarden in één keer naar /run/labx-secrets.env.

        Via stdin en niet als argument: een argument staat op de commandoregel
        van `docker exec` en is dan zichtbaar in `ps` op de host — precies het
        lek dat we dichtmaken.
        """
        regels = []
        for naam, waarde in waarden.items():
            regels.append(f"{envnaam(naam)}={shlex.quote(waarde)}")
        inhoud = "\n".join(regels) + "\n"
        await runtime.exec(
            container_id,
            ["sh", "-c", f"umask 077 && cat > {SECRETS_BESTAND}"],
            stdin=inhoud.encode("utf-8"), timeout=20)

    # ── laatste vangnet ─────────────────────────────────────────────────────

    def maskeer_in(self, lab_id: str, tekst: Optional[str]) -> str:
        """Geheimen die tóch in de uitvoer belanden onleesbaar maken.

        Nodig omdat een script een token kan echoën, in een foutmelding kan
        laten vallen of in een header kan terugkrijgen. De vervanging hierboven
        voorkomt dat een waarde in het commando staat; dit vangt de andere kant.
        """
        from utils.crypto import decrypt

        uit = tekst or ""
        if not uit:
            return uit
        for rij in self.lijst(lab_id):
            if not rij.value_encrypted:
                continue
            try:
                waarde = decrypt(rij.value_encrypted)
            except Exception:  # noqa: BLE001
                continue
            # Korte waarden niet maskeren: dan raak je willekeurige tekst.
            if waarde and len(waarde) >= 12:
                uit = uit.replace(waarde, f"[geheim {rij.name} verborgen]")
        return uit
