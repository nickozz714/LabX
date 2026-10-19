"""services/boards/grillmeister.py — iemand die je ticket op de rooster legt.

Een ticket dat je zelf schrijft, lijkt altijd duidelijk. Wat erin ontbreekt
zie je pas als iemand anders het leest -- of erger, pas als het half gebouwd
is en de vraag "en wat als er al iets gepubliceerd is?" alsnog gesteld wordt.

DRIE KEUZES DIE DE REST VERKLAREN:

- **Hij vraagt, hij schrijft niet ongevraagd.** De eerste beurt levert harde
  vragen op en geen nieuwe tekst. Een agent die meteen je ticket herschrijft
  haalt juist het denkwerk weg dat dit ticket nodig heeft.

- **Een voorstel is pas een voorstel als jij het vraagt.** Dan komt er een
  complete beschrijving en acceptatiecriteria terug, die jij met één klik
  overneemt of laat liggen. Er wordt nooit stilletjes naar het ticket
  geschreven.

- **De klantversie gaat langs de controle.** Vraag je om een tekst voor de
  klant, dan schrijft hij die ZONDER interne verwijzingen, en daarna kijkt
  `klantcommunicatie.controleer` of dat echt gelukt is. Een regel in een
  prompt is een verzoek; dit is een controle.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from services.boards import klantcommunicatie

log = get_logger(__name__)

#: Hoeveel beurten er meegaan. Verder terug voegt weinig toe en maakt elke
#: vraag duurder.
MAX_BEURTEN = 10
#: Wat er van een bord aan context meegaat: de andere tickets, zodat hij kan
#: zien dat iets al ergens anders belegd is. Alleen titels en sleutels.
MAX_BUREN = 40


class GrillMeister:
    def __init__(self, db: Session) -> None:
        self.db = db

    async def beurt(self, ticket_id: int, *, bericht: str = "",
                    modus: str = "grillen",
                    geschiedenis: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """Eén ronde. `modus` bepaalt wat eruit komt:

        - `grillen`     — vragen en gaten, geen nieuwe tekst.
        - `voorstel`    — een complete beschrijving plus acceptatiecriteria.
        - `klantversie` — dezelfde inhoud, maar leesbaar voor iemand van
                          buiten en zonder één verwijzing naar wat hier draait.
        """
        ticket, board = self._ticket(ticket_id)
        if modus not in ("grillen", "voorstel", "klantversie"):
            raise HTTPException(status_code=400, detail=f"Onbekende modus '{modus}'.")

        prompt = self._prompt(ticket, board, modus, bericht, geschiedenis or [])
        ruw = await self._vraag(prompt)
        uit = _lees_json(ruw)

        antwoord = {
            "modus": modus,
            "antwoord": str(uit.get("antwoord") or "").strip(),
            "vragen": [str(v) for v in (uit.get("vragen") or [])][:8],
            "gaten": [str(g) for g in (uit.get("gaten") or [])][:8],
            "voorstel": None,
            "klanttekst": "",
            "controle": None,
        }

        voorstel = uit.get("voorstel")
        if modus == "voorstel" and isinstance(voorstel, dict):
            antwoord["voorstel"] = {
                "titel": str(voorstel.get("titel") or "").strip()[:512],
                "description": str(voorstel.get("description") or "").strip(),
                "acceptance_criteria": str(voorstel.get("acceptance_criteria") or "").strip(),
            }

        if modus == "klantversie":
            tekst = str(uit.get("klanttekst") or "").strip()
            antwoord["klanttekst"] = tekst
            antwoord["controle"] = klantcommunicatie.controleer(tekst, self.db)
            if not antwoord["controle"]["schoon"]:
                # Niet stil doorlaten en niet zelf gaan poetsen: zeggen WAT er
                # mis is, zodat de volgende beurt het kan rechtzetten en jij
                # ziet dat de controle werkt.
                antwoord["antwoord"] = (
                    (antwoord["antwoord"] + "\n\n" if antwoord["antwoord"] else "")
                    + klantcommunicatie.uitleg(antwoord["controle"]))
                log.warningx("Klantversie bevatte interne verwijzingen",
                             ticket=ticket.key,
                             aantal=len(antwoord["controle"]["bevindingen"]))

        return antwoord

    def pas_toe(self, ticket_id: int, *, titel: str = "", description: str = "",
                acceptance_criteria: str = "") -> Dict[str, Any]:
        """Een voorstel overnemen. Alleen wat je meestuurt verandert."""
        from services.boards.board_service import BoardService

        ticket, _ = self._ticket(ticket_id)
        velden: Dict[str, Any] = {}
        if titel.strip():
            velden["title"] = titel.strip()[:512]
        if description.strip():
            velden["description"] = description.strip()
        if acceptance_criteria.strip():
            velden["acceptance_criteria"] = acceptance_criteria.strip()
        if not velden:
            raise HTTPException(status_code=400, detail="Er is niets om over te nemen.")
        svc = BoardService(self.db)
        bijgewerkt = svc.update_ticket(ticket.id, velden)
        log.infox("Voorstel van de grillmeister overgenomen", ticket=ticket.key,
                  velden=sorted(velden))
        return svc.ticket_to_dict(bijgewerkt)

    # ── context ──────────────────────────────────────────────────────────────

    def _ticket(self, ticket_id: int):
        from models.board import Board, Ticket

        t = self.db.get(Ticket, int(ticket_id))
        if t is None:
            raise HTTPException(status_code=404, detail="Ticket niet gevonden")
        b = self.db.get(Board, t.board_id)
        if b is None:
            raise HTTPException(status_code=404, detail="Bord niet gevonden")
        return t, b

    def _buren(self, board_id: int, behalve: int) -> str:
        from models.board import Ticket

        rijen = (self.db.query(Ticket.key, Ticket.title, Ticket.status)
                 .filter(Ticket.board_id == board_id, Ticket.id != behalve,
                         Ticket.archived_at.is_(None))
                 .limit(MAX_BUREN).all())
        if not rijen:
            return "(geen andere tickets)"
        return "\n".join(f"- {k} [{s}] {t}" for k, t, s in rijen)

    def _prompt(self, ticket, board, modus: str, bericht: str,
                geschiedenis: List[Dict[str, str]]) -> str:
        eerder = "\n".join(
            f"{'GRILLMEISTER' if (b.get('rol') or '') == 'model' else 'JIJ'}: "
            f"{str(b.get('tekst') or '')[:1500]}"
            for b in geschiedenis[-MAX_BEURTEN:]) or "(dit is de eerste ronde)"
        kop = {"grillen": _GRILLEN, "voorstel": _VOORSTEL, "klantversie": _KLANT}[modus]
        return kop.format(
            bord=board.name,
            borddoel=(board.description or "(geen omschrijving)")[:500],
            sleutel=ticket.key,
            titel=ticket.title,
            beschrijving=(ticket.description or "(leeg)")[:6000],
            acceptatie=(ticket.acceptance_criteria or "(leeg)")[:3000],
            status=ticket.status,
            prioriteit=ticket.priority,
            labels=", ".join(ticket.labels or []) or "geen",
            buren=self._buren(board.id, ticket.id),
            eerder=eerder,
            bericht=(bericht or "").strip()[:4000] or "(niets erbij gezegd)",
        )

    async def _vraag(self, prompt: str) -> str:
        from services.agent.claude_cli_provider import ClaudeCliProvider
        from services.lab.verkenner import MODEL_TIJD, VERKENNER_MODEL
        from services.settings_service import get_settings

        s = get_settings(self.db)
        provider = ClaudeCliProvider(
            default_model=VERKENNER_MODEL,
            oauth_token=getattr(s, "oauth_token", None),
            cli_path=s.cli_path or "claude",
            max_turns=1,
            timeout=MODEL_TIJD,
            # Geen gereedschap: dit model leest een ticket dat wij hebben
            # samengesteld en schrijft tekst terug. Het hoort niet zelf in de
            # repo of op het bord rond te kijken.
            enable_tool_search=False,
        )
        uit = await provider.chat(prompt)
        return uit.text


def _lees_json(ruw: str) -> Dict[str, Any]:
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
    log.infox("Grillmeister kreeg geen JSON terug", lengte=len(tekst))
    return {"antwoord": tekst[:2000]}


_TICKET = """HET BORD: {bord}
{borddoel}

ANDERE TICKETS OP DIT BORD (om te zien of iets al ergens anders belegd is):
{buren}

HET TICKET: {sleutel} — {titel}
status {status} · prioriteit {prioriteit} · labels: {labels}

BESCHRIJVING:
{beschrijving}

ACCEPTATIECRITERIA:
{acceptatie}

HET GESPREK TOT NU TOE:
{eerder}

WAT DE GEBRUIKER NU ZEGT:
{bericht}
"""

_GRILLEN = """Je bent de grillmeister van dit bord. Je legt één ticket op de rooster.

""" + _TICKET + """
Je taak: de vragen stellen die dit ticket NU nog niet beantwoordt, en die
later duur worden. Denk aan randgevallen, aan wat er gebeurt als iets al
bestaat of half af is, aan wie er iets mag, aan wat er met bestaande data
gebeurt, aan hoe je straks ziet dat het werkt.

Hoe je dat doet:
- Hooguit vijf vragen, de scherpste eerst. Liever vier die ertoe doen dan
  acht waarvan de helft vanzelf spreekt.
- Elke vraag staat op zichzelf en is te beantwoorden zonder de code te lezen.
- Noem ook wat er GOED in staat, in één zin. Een ticket dat alleen kritiek
  krijgt, wordt niet gelezen.
- Zie je dat iets al in een ander ticket op dit bord zit, zeg dat dan.
- Geen inleiding, geen afsluiting.

Antwoord met UITSLUITEND JSON:
{{"antwoord": "twee tot vier zinnen: wat dit ticket wil, en waar het rammelt",
  "vragen": ["de scherpste vraag", "..."],
  "gaten": ["wat er ontbreekt, in steekwoorden"]}}"""

_VOORSTEL = """Je bent de grillmeister van dit bord. Je hebt dit ticket gegrild en schrijft
nu de aangescherpte versie.

""" + _TICKET + """
Schrijf een complete beschrijving en acceptatiecriteria. Regels:
- Behoud wat de gebruiker bedoelde en zijn eigen woorden waar dat kan. Je
  scherpt aan, je herschrijft niet naar jouw smaak.
- Neem de antwoorden uit het gesprek hierboven erin op.
- Wat nog onbeslist is, komt als genummerde open punten onderaan de
  beschrijving. Verzin geen beslissing die niemand genomen heeft.
- Acceptatiecriteria zijn genummerd, controleerbaar en in de tegenwoordige
  tijd. Elk criterium is met het product in de hand te toetsen.
- Nederlands, geen opsmuk.

Antwoord met UITSLUITEND JSON:
{{"antwoord": "één zin over wat je veranderd hebt",
  "voorstel": {{"titel": "eventueel een scherpere titel, anders de bestaande",
               "description": "de volledige beschrijving in markdown",
               "acceptance_criteria": "1. ...\\n2. ..."}}}}"""

_KLANT = """Je bent de grillmeister van dit bord. Je schrijft nu de versie voor de KLANT.

""" + _TICKET + """
De lezer is iemand van BUITEN. Hij kent onze organisatie niet, onze
gereedschappen niet, onze borden niet, en hoeft dat ook niet te kennen.

HARDE REGELS -- hier mag niets doorheen glippen:
- NOEM GEEN interne ticketsleutels (zoals ABC-12), geen namen van onze
  systemen, producten of borden, geen labs, branches, servernamen, interne
  adressen of paden. Ook niet terloops, ook niet tussen haakjes.
- Verwijs niet naar "ons ticket", "onze sprint", "het bord" of iets wat
  alleen intern bestaat.
- Noem geen namen van collega's.
- Schrijf over het PRODUCT en wat de klant ervan merkt, niet over hoe wij het
  organiseren.

En verder:
- Begin met wat er verandert voor de klant.
- Hooguit een korte alinea plus eventueel een paar punten.
- Geen jargon dat alleen bij ons bestaat. Vaktermen uit zijn eigen domein
  mogen wel.
- Nederlands, zakelijk, zonder inleiding of afsluitende beleefdheden.

Antwoord met UITSLUITEND JSON:
{{"antwoord": "één zin voor intern gebruik: wat je hebt weggelaten en waarom",
  "klanttekst": "de tekst die de klant te zien krijgt"}}"""
