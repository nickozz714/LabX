"""Het pijplijn-brein: Claude beslist wat er moet gebeuren.

Hergebruikt de CLI-keten die er al staat (`services/agent/claude_cli_provider`),
maar met een andere pet op: een eigen systeemprompt, het goedkoopste model, en
géén lab. Dat laatste is belangrijk — zonder lab registreert de gateway geen
lab- of boardtools, dus het brein kan niets anders dan wat hier staat.

De toolaanroepen lopen niet via de MCP-gateway maar via een vraag-en-antwoord
lus in JSON. Dat is hier eenvoudiger en beter te volgen: het brein krijgt de
tools in zijn prompt, antwoordt met één JSON-object, en deze module voert dat
uit. Bij spraak gaat het om één actie per beurt, niet om een agent die
twintig stappen zet.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from component_logging import get_logger
from services.voice.schema import TOOLSCHEMA

log = get_logger(__name__)

# Zoveel beurten geschiedenis gaan mee. De rest wordt gesnoeid: zonder dat
# groeit de prompt elke beurt en betaal je het gesprek steeds opnieuw.
GESCHIEDENIS = 10

SYSTEEMPROMPT = """\
Je bent de spraakassistent van LabX. Je praat met één gebruiker die zijn handen
vol heeft — hij loopt rond, zit in de auto of kijkt naar iets anders.

Hoe je klaarkomt:
- Kort, in gewone spreektaal, in het Nederlands. Geen opsommingen, geen
  markdown, geen technische termen die je niet hoeft te noemen.
- Lees nooit ids, paden of lange codes voor. Noem een ticket bij zijn sleutel
  en zijn onderwerp, niet bij zijn interne nummer.
- Wissel je formuleringen af. Niet elke beurt "oké, ik kijk even".

Wat je doet:
- Je doet zelf geen werk. Je roept één van de beschikbare acties aan en vertelt
  wat eruit komt.
- Lost verwijzingen uit het gesprek zelf op: zegt de gebruiker "en start daar
  een agent op" nadat je het over een ticket had, dan vul je dat ticket in.
- Noemt de gebruiker een ticketsleutel (letters, streepje, nummer -- zoals
  PLAT-2), dan is die al uniek. Zoek meteen op. Vraag dan NOOIT van welke klant
  of welk bord het is; dat weet het systeem zelf.
- Stel alleen een vraag als je echt niet kunt kiezen, en dan één korte.
- Bij een leesactie: vat de feiten samen in één of twee zinnen. Heb je een
  letterlijke opmerking gekregen en vraagt de gebruiker ernaar door, citeer
  die dan in plaats van hem te parafraseren.
- Bij een schrijfactie krijg je GEEN resultaat terug maar een bevestigingszin.
  Lees die letterlijk voor en wacht. Bedenk zelf nooit een eigen formulering
  van wat er gaat gebeuren, en zeg nooit dat iets gedaan is voordat je het
  resultaat hebt gezien.
- Krijg je een vraag met keuzes terug, leg die dan kort voor.

Je antwoordt ALTIJD met één JSON-object en niets daarbuiten:
  {"actie": "<naam>", "args": {...}}   om een actie aan te roepen
  {"zeg": "<wat je uitspreekt>"}       om iets te zeggen
"""


def _tools_als_tekst() -> str:
    regels = []
    for t in TOOLSCHEMA:
        props = t["parameters"]["properties"]
        velden = ", ".join(
            f"{k}{'*' if k in t['parameters']['required'] else ''}" for k in props) or "geen"
        regels.append(f"- {t['name']}({velden}): {t['description']}")
    return "\n".join(regels)


def _prompt(db: Session, session_id: str, tekst: str,
            toolresultaat: Optional[Dict[str, Any]] = None) -> str:
    from models.voice import VoiceEvent

    rijen = (db.query(VoiceEvent)
             .filter(VoiceEvent.session_id == session_id,
                     VoiceEvent.soort.in_(("gebruiker", "assistent")))
             .order_by(VoiceEvent.id.desc()).limit(GESCHIEDENIS).all())
    historie = "\n".join(
        f"{'Gebruiker' if e.soort == 'gebruiker' else 'Jij'}: {e.tekst}"
        for e in reversed(rijen))

    delen = [SYSTEEMPROMPT, "", "Beschikbare acties:", _tools_als_tekst()]
    if historie:
        delen += ["", "Het gesprek tot nu toe:", historie]
    if toolresultaat is not None:
        delen += ["", "Resultaat van je vorige actie:",
                  json.dumps(toolresultaat, ensure_ascii=False)[:4000],
                  "", "Vertel dit nu in één of twee zinnen aan de gebruiker."]
    else:
        delen += ["", f"De gebruiker zegt: {tekst}"]
    return "\n".join(delen)


async def _vraag_claude(db: Session, prompt: str) -> str:
    """Eén vraag aan het goedkoopste model, zonder enig gereedschap.

    Bewust GEEN mcp_config_path: dit brein denkt alleen na. Zou het de
    labx-gateway krijgen, dan kon het rechtstreeks board- en labtools
    aanroepen — en daarmee om de bevestigingslus heen.
    """
    from services.agent.claude_cli_provider import ClaudeCliProvider
    from services.settings_service import get_settings

    s = get_settings(db)
    provider = ClaudeCliProvider(
        default_model=getattr(s, "voice_brein_model", None) or "haiku",
        oauth_token=getattr(s, "oauth_token", None),
        cli_path=s.cli_path or "claude",
        max_turns=1,
        timeout=45.0,
        enable_tool_search=False,
    )
    uit = await provider.chat(prompt)
    return uit.text


def _lees_json(ruw: str) -> Dict[str, Any]:
    """Het antwoord van het model als JSON, of als gewone zin.

    Een model dat per ongeluk proza teruggeeft hoort niet de hele beurt te
    laten mislukken — dan is die zin gewoon wat hij zegt.
    """
    tekst = (ruw or "").strip()
    if tekst.startswith("```"):
        tekst = tekst.strip("`")
        tekst = tekst.split("\n", 1)[-1] if "\n" in tekst else tekst
        tekst = tekst.rsplit("```", 1)[0]
    begin, eind = tekst.find("{"), tekst.rfind("}")
    if begin >= 0 and eind > begin:
        try:
            uit = json.loads(tekst[begin:eind + 1])
            if isinstance(uit, dict):
                return uit
        except json.JSONDecodeError:
            pass
    return {"zeg": tekst[:600]}


async def antwoord_op(db: Session, sessie, tekst: str) -> Dict[str, Any]:
    """Eén beurt: van wat de gebruiker zei naar wat de assistent zegt."""
    from services.voice.sessie import VoiceSessieService

    svc = VoiceSessieService(db)
    try:
        ruw = await _vraag_claude(db, _prompt(db, sessie.id, tekst))
    except Exception as exc:  # noqa: BLE001
        log.warningx("Spraakbrein onbereikbaar", error=str(exc)[:300])
        return {"antwoord": "Ik kan er even niet bij. Probeer het zo nog eens."}

    besluit = _lees_json(ruw)
    if besluit.get("zeg"):
        return {"antwoord": str(besluit["zeg"])[:600]}

    naam = str(besluit.get("actie") or "").strip()
    if not naam:
        return {"antwoord": "Dat heb ik niet goed begrepen."}

    uit = svc.roep_tool_aan(sessie.id, naam, besluit.get("args") or {})

    # Een schrijfactie: de ZIN VAN DE SERVER wordt voorgelezen, niet iets wat
    # het model ervan maakt. Daar zit de hele veiligheid in.
    if uit.get("wacht_op_bevestiging"):
        from services.settings_service import get_settings
        from services.voice.bevestiging import hoe_bevestigen
        s = get_settings(db)
        staart = hoe_bevestigen(s.voice_bevestiging, s.voice_woord)
        return {"antwoord": f"{uit['zin']} {staart}".strip(),
                "bevestiging": {"id": uit["id"], "zin": uit["zin"],
                                "vervalt_over_seconden": uit["vervalt_over_seconden"]}}

    if uit.get("vraag"):
        keuzes = uit.get("keuzes") or []
        zin = uit["vraag"]
        if keuzes:
            zin += " " + "; ".join(str(k) for k in keuzes[:5])
        return {"antwoord": zin}

    # Een leesactie: het model maakt er een zin van.
    try:
        ruw = await _vraag_claude(db, _prompt(db, sessie.id, tekst, toolresultaat=uit))
    except Exception:  # noqa: BLE001
        return {"antwoord": "Ik heb het opgehaald, maar kan het even niet samenvatten."}
    tweede = _lees_json(ruw)
    return {"antwoord": str(tweede.get("zeg") or tweede.get("actie") or "")[:600] or
            "Ik heb het opgehaald."}
