"""Waar een agent volgens zijn eigen stappen mee bezig is, in één regel.

De vraag "hoe staat het met KRI-114" hoort te worden beantwoord met *"hij
controleert de libraryversie per omgeving"*, niet met zeventig toolaanroepen.
Die vertaalslag gebeurt hier.

Twee dingen zijn belangrijk:

1. **Gecachet per run.** Hier zit een modelaanroep onder. Vraag je twee keer
   binnen een minuut, dan komt er geen tweede aanroep — alleen als er nieuwe
   stappen bij zijn gekomen wordt er opnieuw samengevat.
2. **Zonder model werkt het ook.** Is er geen CLI beschikbaar of valt de
   aanroep om, dan komt er een mechanische samenvatting uit de laatste stappen.
   Lelijker, maar een monitoringvraag hoort nooit te stranden op een
   samenvatter.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from component_logging import get_logger

log = get_logger(__name__)

# run_id -> (aantal stappen waarop dit gebaseerd is, samenvatting)
_CACHE: Dict[str, Tuple[int, str]] = {}

# Zoveel laatste stappen gaan mee. Meer maakt de samenvatting niet beter, wel
# duurder: wat er NU gebeurt staat achteraan.
_VENSTER = 12


def _stap_regel(stap: Any) -> Optional[str]:
    """Eén stap als leesbare regel, of None als er niets in zit."""
    if not isinstance(stap, dict):
        return None
    soort = stap.get("type") or stap.get("kind")
    if soort == "tool" or stap.get("name"):
        naam = stap.get("name") or "tool"
        invoer = stap.get("input")
        kort = ""
        if isinstance(invoer, dict):
            for sleutel in ("command", "prompt", "query", "path", "file_path", "key"):
                if invoer.get(sleutel):
                    kort = str(invoer[sleutel])[:160]
                    break
        return f"{naam}: {kort}".strip(": ")
    tekst = stap.get("text") or stap.get("thinking")
    if tekst:
        return str(tekst)[:300]
    return None


def _mechanisch(regels: List[str]) -> str:
    """Terugval zonder model: de laatste betekenisvolle regel."""
    for regel in reversed(regels):
        if regel and not regel.startswith(("TodoWrite", "Read:")):
            return regel[:200]
    return regels[-1][:200] if regels else "nog geen stappen"


def _via_model(regels: List[str]) -> Optional[str]:
    """Eén zin laten maken door het goedkoopste model dat er is.

    Expres synchroon en kort getimed: dit hangt aan een gesproken vraag, dus
    een samenvatting die tien seconden kost is erger dan een ruwe regel.
    """
    try:
        import subprocess

        from services.settings_service import get_settings  # noqa: F401
        from config import settings as cfg

        cli = getattr(cfg, "LABX_CLI_PATH", None) or "claude"
        prompt = (
            "Hieronder de laatste stappen van een agent die aan een ticket werkt. "
            "Antwoord met ÉÉN korte Nederlandse zin over waar hij nu mee bezig is. "
            "Geen opsomming, geen toolnamen, geen paden, geen aanhalingstekens.\n\n"
            + "\n".join(regels[-_VENSTER:])
        )
        uit = subprocess.run(
            [cli, "-p", "--model", "haiku", "--max-turns", "1",
             "--disallowedTools", "Bash Read Write Edit WebSearch Task"],
            input=prompt.encode("utf-8"), capture_output=True, timeout=12)
        if uit.returncode != 0:
            return None
        zin = uit.stdout.decode("utf-8", "replace").strip()
        return zin.splitlines()[0][:200] if zin else None
    except Exception as exc:  # noqa: BLE001 — nooit een vraag laten stranden
        log.warningx("Samenvatten via model mislukt", error=str(exc)[:200])
        return None


def run_samenvatting(db: Session, run) -> str:
    """Waar deze run volgens zijn stappen mee bezig is."""
    stappen = list(getattr(run, "steps", None) or [])
    run_id = str(getattr(run, "id", "") or "")
    if not stappen:
        return "nog geen stappen"

    gecachet = _CACHE.get(run_id)
    if gecachet and gecachet[0] == len(stappen):
        return gecachet[1]

    regels = [r for r in (_stap_regel(s) for s in stappen[-_VENSTER:]) if r]
    if not regels:
        return "nog geen stappen"

    samenvatting = _via_model(regels) or _mechanisch(regels)
    _CACHE[run_id] = (len(stappen), samenvatting)
    # De cache mag niet oneindig groeien; een paar honderd runs is ruim.
    if len(_CACHE) > 300:
        for sleutel in list(_CACHE)[:100]:
            _CACHE.pop(sleutel, None)
    return samenvatting
