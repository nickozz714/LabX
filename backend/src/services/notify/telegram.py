"""
services/notify/telegram.py

Telegram als meldkanaal — heen én terug.

Waarom Telegram hier past terwijl WhatsApp en SMS dat niet doen: `getUpdates`
is een HAAL-methode. LabX vraagt zelf om nieuwe berichten in plaats van dat
Telegram ze komt afleveren. Er hoeft dus niets van buiten naar binnen te
kunnen, en dat is precies de beperking van deze opstelling — een prive server
zonder domein en zonder open poort.

Twee dingen om te weten als je dit instelt:

- **Het bot-token** krijg je van @BotFather in Telegram zelf.
- **Het chat-id** is niet je gebruikersnaam. Stuur de bot één keer een bericht
  ("hoi"), en `ontdek_chats()` haalt hem hier op. Dat moet ook: een bot mag
  niemand als eerste aanschrijven, dus zonder dat eerste bericht van jou kan
  LabX je niet bereiken. De UI doet dat met de knop "Chats ophalen".

Antwoorden koppelen we op `reply_to_message`: antwoord je in Telegram op een
melding, dan weet LabX om welke run het ging. Typ je gewoon iets zonder te
antwoorden, dan valt het terug op de laatste melding naar die chat — want dat
is in de praktijk wat je bedoelt, en een bericht dat nergens heen kan is
erger dan een bericht dat naar het meest recente gesprek gaat.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from component_logging import get_logger

log = get_logger(__name__)

_BASIS = "https://api.telegram.org/bot{token}/{methode}"
# Telegram kapt boven de 4096 tekens af met een foutmelding in plaats van te
# knippen. Ruim eronder blijven en zelf knippen leest beter.
MAX_TEKST = 3800


async def _aanroep(token: str, methode: str, payload: Dict[str, Any],
                   *, timeout: float = 30.0) -> Dict[str, Any]:
    url = _BASIS.format(token=token, methode=methode)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload)
    if resp.status_code == 401:
        raise RuntimeError("Telegram weigerde het bot-token (controleer wat BotFather je gaf)")
    data = resp.json() if resp.content else {}
    if not data.get("ok"):
        raise RuntimeError(f"Telegram: {data.get('description') or resp.text[:200]}")
    return data.get("result") or {}


def _kort(tekst: str) -> str:
    tekst = tekst or ""
    if len(tekst) <= MAX_TEKST:
        return tekst
    return tekst[:MAX_TEKST] + f"\n\n[…ingekort, {len(tekst) - MAX_TEKST} tekens meer in LabX]"


async def stuur(*, token: str, chat_id: str, titel: str, tekst: str) -> str:
    """Verstuur een melding. Geeft het message_id terug — dat is waar een
    antwoord straks naar verwijst."""
    body = f"*{_escape(titel)}*\n\n{_escape(_kort(tekst))}"
    result = await _aanroep(token, "sendMessage", {
        "chat_id": chat_id,
        "text": body,
        "parse_mode": "MarkdownV2",
        # Een melding met een link erin hoeft geen halve webpagina eronder.
        "link_preview_options": {"is_disabled": True},
    })
    return str(result.get("message_id") or "")


_SPECIAAL = r"_*[]()~`>#+-=|{}.!"


def _escape(tekst: str) -> str:
    """MarkdownV2 wil élk speciaal teken ontsnapt hebben, ook in gewone tekst.
    Doe je dat niet, dan weigert Telegram het hele bericht — en dan komt de
    melding niet aan om een reden die niets met de melding te maken heeft.
    Vandaar: alles ontsnappen en geen opmaak in de body proberen."""
    uit = []
    for teken in (tekst or ""):
        uit.append("\\" + teken if teken in _SPECIAAL else teken)
    return "".join(uit)


async def haal_updates(*, token: str, offset: Optional[int] = None,
                       timeout: float = 25.0) -> List[Dict[str, Any]]:
    """Nieuwe berichten ophalen.

    `offset` is Telegram's bevestiging: door de volgende update_id mee te
    sturen zeg je "die daarvoor heb ik verwerkt" en levert Telegram ze niet
    opnieuw. Zonder dat krijg je bij elke ronde dezelfde berichten terug en
    reageert LabX steeds opnieuw op hetzelfde antwoord.
    """
    payload: Dict[str, Any] = {"allowed_updates": ["message"], "limit": 50}
    if offset is not None:
        payload["offset"] = int(offset)
    result = await _aanroep(token, "getUpdates", payload, timeout=timeout + 5)
    return result if isinstance(result, list) else []


async def ontdek_chats(*, token: str) -> List[Dict[str, str]]:
    """Welke chats hebben de bot aangeschreven? Dit is de manier om aan een
    chat-id te komen: een bot kan niemand als eerste benaderen, dus jij stuurt
    'hoi' en hier komt hij tevoorschijn.

    LET OP: dit bevestigt de updates NIET (geen offset), zodat een bericht dat
    hier langskomt straks nog gewoon door de inbox verwerkt wordt.
    """
    gevonden: Dict[str, Dict[str, str]] = {}
    for update in await haal_updates(token=token, timeout=5):
        chat = ((update.get("message") or {}).get("chat") or {})
        cid = str(chat.get("id") or "")
        if not cid or cid in gevonden:
            continue
        naam = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
        gevonden[cid] = {"chat_id": cid,
                         "naam": naam or chat.get("title") or chat.get("username") or cid,
                         "type": str(chat.get("type") or "")}
    return list(gevonden.values())


async def controleer(*, token: str) -> Dict[str, Any]:
    """Klopt het token? Geeft de botnaam terug, zodat de UI kan laten zien
    met welke bot je verbonden bent in plaats van alleen 'ok'."""
    me = await _aanroep(token, "getMe", {}, timeout=15)
    return {"ok": True, "bot": me.get("username") or me.get("first_name") or "?"}
