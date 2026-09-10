"""
services/notify/notify_service.py

Meldingen versturen, en antwoorden terugbrengen naar de sessie waar ze bij
horen.

**Waarom dit niet zomaar "een mailtje sturen" is.** Een melding zonder weg
terug is een halve functie: je leest op je telefoon dat SWI-88 vastloopt op een
ontbrekende parameter, en vervolgens moet je achter een laptop kruipen om
"gebruik TST" te typen. De weg terug is dus het punt, en die vraagt om één
ding dat je vooraf moet regelen: onthouden waar een melding OVER ging. Dat is
`NotificationLog.context` — thread, ticket, run, planning — met de id die het
kanaal aan het bericht gaf (`external_id`) als sleutel. Een antwoord verwijst
naar dat bericht, en zo vindt "gebruik TST" zijn weg naar de juiste agent.

**Alles haalt, niets levert af.** Deze opstelling staat op een prive server
zonder domein en zonder open poort, dus een webhook kan hier niet aankomen.
Telegram (getUpdates) en mail (IMAP) worden daarom door de scheduler
opgehaald. Dat is hier geen omweg maar de robuustere kant: niets dat stukgaat
als het IP verandert, en niets dat van buiten bereikbaar hoeft te zijn.

**Een melding mag nooit de reden zijn dat er iets misgaat.** Versturen gebeurt
in een losse taak en elke fout blijft binnen dit bestand (en in
`channel.last_error`, zodat je hem in de UI ziet). Een agent-run die klaar is,
is klaar — ook als de mailserver plat ligt.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from component_logging import get_logger
from models.notification import EVENTS, NotificationChannel, NotificationLog

log = get_logger(__name__)

# Hoeveel van een samenvatting er in een melding past. Een melding is een
# aankondiging, geen verslag: wie meer wil weten opent LabX. Te lang maakt hem
# op een telefoon onleesbaar en bij mail vergroot het de kans dat een antwoord
# er middenin belandt.
MAX_SAMENVATTING = 1200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _geheim(channel: NotificationChannel) -> str:
    if not channel.secret_encrypted:
        return ""
    from utils.crypto import decrypt
    return decrypt(channel.secret_encrypted)


def _kort(tekst: Optional[str], grens: int = MAX_SAMENVATTING) -> str:
    body = (tekst or "").strip()
    if len(body) <= grens:
        return body
    return body[:grens] + f"\n\n[…ingekort, {len(body) - grens} tekens meer in LabX]"


def kanalen_voor(db: Session, event: str) -> List[NotificationChannel]:
    """Welke kanalen willen dit horen. Een kanaal zonder gekozen
    gebeurtenissen krijgt alles — dat is wat je bedoelt als je net een kanaal
    hebt aangemaakt en nog niets hebt aangevinkt."""
    uit = []
    for kanaal in db.query(NotificationChannel).filter(
            NotificationChannel.enabled == True).all():  # noqa: E712
        gekozen = [str(x) for x in (kanaal.events or [])]
        if not gekozen or event in gekozen:
            uit.append(kanaal)
    return uit


# ── versturen ───────────────────────────────────────────────────────────────

async def stuur(event: str, titel: str, tekst: str,
                context: Optional[Dict[str, Any]] = None,
                *, alleen_kanaal: Optional[int] = None) -> int:
    """Eén gebeurtenis naar alle kanalen die hem willen. Opent zijn eigen
    databasesessie: de aanroeper is meestal een afloop-hook waarvan de sessie
    zo meteen dicht gaat.

    `alleen_kanaal` is voor de testknop: dan gaat het bericht naar dít kanaal,
    ongeacht waar het op geabonneerd is. Het alternatief — de abonnementen even
    omzetten en daarna terugzetten — laat ze verkeerd staan zodra het verzoek
    halverwege afbreekt.
    """
    from db.database import SessionLocal

    db = SessionLocal()
    verstuurd = 0
    try:
        if alleen_kanaal is not None:
            doelen = [k for k in [db.get(NotificationChannel, alleen_kanaal)] if k is not None]
        else:
            doelen = kanalen_voor(db, event)
        for kanaal in doelen:
            regel = NotificationLog(
                channel_id=kanaal.id, event=event, title=titel[:512],
                body=_kort(tekst), status="sent", context=dict(context or {}),
                created_at=_now_iso())
            db.add(regel)
            db.commit()
            db.refresh(regel)
            try:
                regel.external_id = await _verstuur_via(kanaal, regel)
                kanaal.last_sent_at = _now_iso()
                kanaal.last_error = None
                verstuurd += 1
            except Exception as exc:  # noqa: BLE001 — nooit de aanroeper laten vallen
                regel.status = "failed"
                regel.error = str(exc)[:1000]
                kanaal.last_error = str(exc)[:1000]
                log.warningx("Melding versturen mislukt", kanaal=kanaal.name,
                             event=event, error=str(exc)[:300])
            kanaal.updated_at = _now_iso()
            db.commit()
    finally:
        db.close()
    return verstuurd


async def _verstuur_via(kanaal: NotificationChannel, regel: NotificationLog) -> str:
    if kanaal.kind == "telegram":
        from services.notify import telegram
        return await telegram.stuur(
            token=_geheim(kanaal), chat_id=str((kanaal.config or {}).get("chat_id") or ""),
            titel=regel.title, tekst=_met_afsluiter(kanaal, regel))
    if kanaal.kind == "email":
        from services.notify import mail
        # De sleutel in het onderwerp is het vangnet voor wie een NIEUWE mail
        # stuurt in plaats van te antwoorden; dan is In-Reply-To leeg.
        return await mail.stuur(
            config=kanaal.config or {}, wachtwoord=_geheim(kanaal),
            onderwerp=regel.title, tekst=_met_afsluiter(kanaal, regel),
            sleutel=f"{regel.id:06x}")
    raise RuntimeError(f"Onbekend kanaal-soort '{kanaal.kind}'")


def _met_afsluiter(kanaal: NotificationChannel, regel: NotificationLog) -> str:
    """De uitnodiging om te antwoorden hoort in het bericht zelf te staan.
    Zonder die regel weet niemand dat het kan, en dan is de weg terug er wel
    maar wordt hij niet gebruikt."""
    tekst = regel.body
    if not kanaal.allow_reply:
        return tekst
    context = regel.context or {}
    waarheen = context.get("ticket_key") or "dit gesprek"
    return (f"{tekst}\n\n—\nAntwoord op dit bericht om de agent verder te laten "
            f"werken aan {waarheen}. Wat je terugstuurt komt binnen als volgende "
            f"beurt in dezelfde sessie.")


def meld(event: str, titel: str, tekst: str,
         context: Optional[Dict[str, Any]] = None) -> None:
    """Vuur-en-vergeet vanuit synchrone code (afloop-hooks, de scheduler).

    Bewust een losse taak: een afloop-hook draait ín de lus van de run, en
    daar een SMTP-verbinding openen zou de afronding van die run laten wachten
    op een mailserver. En als er geen lus draait (een script, een test), dan
    gebeurt er niets — een melding is nooit belangrijk genoeg om ergens een
    tweede event-lus voor te starten.
    """
    if event not in EVENTS:
        log.warningx("Onbekende meldgebeurtenis", event=event)
        return
    try:
        lus = asyncio.get_running_loop()
    except RuntimeError:
        log.infox("Geen event-lus; melding overgeslagen", event=event)
        return
    taak = lus.create_task(stuur(event, titel, tekst, context))
    # Een taak zonder verwijzing kan door de garbage collector worden opgeruimd
    # vóór hij gedraaid heeft. Vandaar deze set.
    _LOPEND.add(taak)
    taak.add_done_callback(_LOPEND.discard)


_LOPEND: set = set()


# ── antwoorden ──────────────────────────────────────────────────────────────

async def verwerk_inbox(db: Session) -> Dict[str, int]:
    """Alle kanalen langs voor binnengekomen antwoorden. Draait vanuit de
    scheduler."""
    telling = {"gelezen": 0, "verwerkt": 0}
    for kanaal in db.query(NotificationChannel).filter(
            NotificationChannel.enabled == True,  # noqa: E712
            NotificationChannel.allow_reply == True).all():  # noqa: E712
        try:
            if kanaal.kind == "telegram":
                gelezen, verwerkt = await _inbox_telegram(db, kanaal)
            elif kanaal.kind == "email":
                gelezen, verwerkt = await _inbox_mail(db, kanaal)
            else:
                continue
            telling["gelezen"] += gelezen
            telling["verwerkt"] += verwerkt
            kanaal.last_error = None
        except Exception as exc:  # noqa: BLE001
            kanaal.last_error = str(exc)[:1000]
            log.warningx("Antwoorden ophalen mislukt", kanaal=kanaal.name,
                         error=str(exc)[:300])
        kanaal.last_poll_at = _now_iso()
        kanaal.updated_at = _now_iso()
        db.commit()
    return telling


async def _inbox_telegram(db: Session, kanaal: NotificationChannel) -> tuple:
    from services.notify import telegram

    config = dict(kanaal.config or {})
    offset = config.get("offset")
    updates = await telegram.haal_updates(token=_geheim(kanaal),
                                          offset=int(offset) if offset else None,
                                          timeout=5)
    gelezen = verwerkt = 0
    hoogste = int(offset or 0)
    for update in updates:
        hoogste = max(hoogste, int(update.get("update_id") or 0) + 1)
        bericht = update.get("message") or {}
        tekst = str(bericht.get("text") or "").strip()
        chat_id = str(((bericht.get("chat") or {}).get("id")) or "")
        if not tekst:
            continue
        # Berichten uit een andere chat dan de ingestelde negeren: een bot kan
        # door iedereen aangeschreven worden die het adres kent, en dat is geen
        # reden om een agent aan het werk te zetten.
        if chat_id and str(config.get("chat_id") or "") and chat_id != str(config["chat_id"]):
            log.warningx("Telegram-bericht uit onbekende chat genegeerd",
                         kanaal=kanaal.name, chat=chat_id)
            continue
        gelezen += 1
        antwoord_op = str(((bericht.get("reply_to_message") or {}).get("message_id")) or "")
        regel = _zoek_melding(db, kanaal, external_id=antwoord_op)
        if await _verwerk_antwoord(db, kanaal, regel, tekst):
            verwerkt += 1
    if hoogste:
        config["offset"] = hoogste
        kanaal.config = config
    return gelezen, verwerkt


async def _inbox_mail(db: Session, kanaal: NotificationChannel) -> tuple:
    from services.notify import mail

    config = dict(kanaal.config or {})
    berichten = await mail.haal_antwoorden(config=config, wachtwoord=_geheim(kanaal),
                                           laatste_uid=int(config.get("last_uid") or 0))
    gelezen = verwerkt = 0
    hoogste = int(config.get("last_uid") or 0)
    for bericht in berichten:
        hoogste = max(hoogste, int(bericht["uid"]))
        tekst = (bericht.get("tekst") or "").strip()
        if not tekst:
            continue
        gelezen += 1
        # Eerst op In-Reply-To (de afspraak uit RFC 5322), dan op het merkteken
        # in het onderwerp voor wie een nieuwe mail stuurde.
        regel = _zoek_melding(db, kanaal, external_id=bericht.get("in_reply_to"))
        if regel is None:
            sleutel = mail.zoek_merkteken(bericht.get("onderwerp") or "")
            if sleutel:
                regel = db.get(NotificationLog, int(sleutel, 16))
                if regel is not None and regel.channel_id != kanaal.id:
                    regel = None
        if await _verwerk_antwoord(db, kanaal, regel, tekst):
            verwerkt += 1
    if hoogste:
        config["last_uid"] = hoogste
        kanaal.config = config
    return gelezen, verwerkt


def _zoek_melding(db: Session, kanaal: NotificationChannel,
                  *, external_id: Optional[str]) -> Optional[NotificationLog]:
    """De melding waarop geantwoord wordt.

    Zonder verwijzing valt hij terug op de LAATSTE melding van dit kanaal.
    Dat is met opzet: in de praktijk typ je op je telefoon gewoon "ja doe maar"
    zonder de antwoordknop te gebruiken, en dan bedoel je het bericht dat je
    net las. Een antwoord dat nergens heen kan is erger dan een antwoord dat
    naar het meest recente gesprek gaat — zeker omdat we terugmelden wáár het
    terechtkwam.
    """
    if external_id:
        regel = (db.query(NotificationLog)
                 .filter(NotificationLog.channel_id == kanaal.id,
                         NotificationLog.external_id == str(external_id).strip())
                 .first())
        if regel is not None:
            return regel
    return (db.query(NotificationLog)
            .filter(NotificationLog.channel_id == kanaal.id,
                    NotificationLog.status == "sent")
            .order_by(NotificationLog.id.desc()).first())


async def _verwerk_antwoord(db: Session, kanaal: NotificationChannel,
                            regel: Optional[NotificationLog], tekst: str) -> bool:
    """Een antwoord terug de sessie in.

    Het wordt de volgende beurt in het gesprek van die run — de agent pakt het
    op met alles wat hij al gezien heeft. Hoort er een ticket bij, dan komt het
    daar ook als opmerking te staan, zodat het bordoverzicht klopt met wat er
    besproken is.
    """
    from models.lab import Lab
    from models.message import Message
    from models.thread import Thread
    from services.agent import background_runs
    from uuid import uuid4

    if regel is None:
        await _terugmelden(kanaal, "Ik weet niet bij welke melding dit hoort — er is nog "
                                   "niets verstuurd via dit kanaal.")
        return False
    context = regel.context or {}
    thread_id = context.get("thread_id")
    thread = db.get(Thread, thread_id) if thread_id else None
    if thread is None:
        await _terugmelden(kanaal, "Dit ging over iets zonder gesprek, dus ik kan je "
                                   "antwoord nergens naartoe sturen. Open LabX om verder te gaan.")
        return False

    # Het antwoord altijd eerst vastleggen. Wat er daarna ook misgaat, het is
    # niet kwijt — dat is het verschil tussen "de agent had het druk" en "je
    # bericht is verdwenen".
    db.add(Message(id=str(uuid4()), thread_id=thread.id, role="user",
                   content=tekst, steps=[], created_at=_now_iso()))
    thread.updated_at = _now_iso()
    db.commit()

    ticket_id = context.get("ticket_id")
    if ticket_id:
        try:
            from services.boards.board_service import BoardService
            BoardService(db).add_comment(int(ticket_id), body=tekst, author="user",
                                         kind="comment", internal=True)
        except Exception as exc:  # noqa: BLE001
            log.warningx("Antwoord als ticket-opmerking vastleggen mislukt",
                         ticket_id=ticket_id, error=str(exc)[:200])

    lab = db.get(Lab, thread.lab_id) if thread.lab_id else None
    if lab is None or lab.status != "running":
        await _terugmelden(kanaal, f"Genoteerd bij {context.get('ticket_key') or 'het gesprek'}, "
                                   f"maar het lab draait niet — de agent kan nu niet verder.")
        return True
    if background_runs.active_foreground_run(db, thread.id) is not None:
        await _terugmelden(kanaal, "Genoteerd. Er loopt op dit moment een beurt in dit "
                                   "gesprek, dus de agent pakt het op zodra die klaar is.")
        return True

    history = [{"role": m.role, "content": m.content} for m in
               db.query(Message).filter(Message.thread_id == thread.id)
               .order_by(Message.created_at.asc()).all()[-20:]
               if m.role in ("user", "assistant")]
    run = background_runs.start(db, thread_id=thread.id, lab_id=lab.id,
                                history=history, prompt=tekst,
                                model=thread.model, effort=thread.effort)
    _koppel_melding(run, kanaal, regel)
    await _terugmelden(kanaal, f"Opgepakt — de agent werkt verder aan "
                               f"{context.get('ticket_key') or 'dit gesprek'}. "
                               f"Je hoort het als hij klaar is.")
    return True


def _koppel_melding(run, kanaal: NotificationChannel, regel: NotificationLog) -> None:
    """Laat de run die uit een antwoord ontstaat straks óók een melding sturen,
    naar dezelfde context. Zonder dit zou je één keer antwoorden en daarna
    niets meer horen — precies op het moment dat je meekijkt."""
    from services.agent import background_runs

    context = dict(regel.context or {})

    def _hook(db_hook, run_row) -> None:
        if run_row is None:
            return
        status = getattr(run_row, "status", "") or ""
        klaar = status == "completed"
        meld("run_klaar" if klaar else "run_mislukt",
             titel=f"{context.get('ticket_key') or 'Gesprek'}: agent {'klaar' if klaar else status}",
             tekst=(getattr(run_row, "answer", None) or getattr(run_row, "error", None)
                    or "Geen samenvatting."),
             context=context)

    background_runs.on_finish(run.id, _hook)


async def _terugmelden(kanaal: NotificationChannel, tekst: str) -> None:
    """Een korte bevestiging terug. Belangrijk genoeg om apart te doen: zonder
    dit weet je niet of je antwoord ergens is aangekomen, en dan stuur je het
    nog een keer."""
    try:
        if kanaal.kind == "telegram":
            from services.notify import telegram
            await telegram.stuur(token=_geheim(kanaal),
                                 chat_id=str((kanaal.config or {}).get("chat_id") or ""),
                                 titel="LabX", tekst=tekst)
        elif kanaal.kind == "email":
            from services.notify import mail
            await mail.stuur(config=kanaal.config or {}, wachtwoord=_geheim(kanaal),
                             onderwerp="LabX", tekst=tekst, sleutel="000000")
    except Exception as exc:  # noqa: BLE001
        log.warningx("Bevestiging terugsturen mislukt", kanaal=kanaal.name,
                     error=str(exc)[:200])


async def tick() -> None:
    """Scheduler-ingang: haal antwoorden op. Eigen sessie, want hij draait los
    van elk verzoek."""
    from db.database import SessionLocal

    db = SessionLocal()
    try:
        await verwerk_inbox(db)
    finally:
        db.close()
