"""
services/notify/mail.py

Mail als meldkanaal — heen via SMTP, terug via IMAP.

Terugpraten via mail werkt hier om dezelfde reden als Telegram: LabX HAALT de
antwoorden op. Er hoeft niets van buiten naar binnen, dus geen tunnel en geen
open poort.

Het koppelen van een antwoord aan de juiste sessie gebeurt met de
**Message-ID** die we zelf zetten. Een mailprogramma dat antwoordt, zet die in
`In-Reply-To` en `References` — dat is een afspraak uit RFC 5322 en elk
mailprogramma houdt zich eraan. Daarom genereren we hem zelf in plaats van de
server er een te laten verzinnen: alleen dan kunnen we hem later terugvinden.

Als vangnet staat er ook een merkteken in het onderwerp (`[LabX #ab12cd]`).
Sommige mensen sturen een nieuwe mail in plaats van te antwoorden, en dan is
`In-Reply-To` leeg terwijl het onderwerp meestal wél overleeft.

smtplib en imaplib zijn blokkerend en komen uit de standaardbibliotheek. Ze
draaien daarom in een thread (`asyncio.to_thread`); een extra afhankelijkheid
voor iets wat Python al kan, is het niet waard.
"""
from __future__ import annotations

import asyncio
import email
import imaplib
import re
import smtplib
import ssl
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import make_msgid, parsedate_to_datetime
from typing import Any, Dict, List, Optional

from component_logging import get_logger

log = get_logger(__name__)

_MERK = re.compile(r"\[LabX #([0-9a-f]{6,})\]")


def merkteken(sleutel: str) -> str:
    return f"[LabX #{sleutel}]"


def zoek_merkteken(onderwerp: str) -> Optional[str]:
    m = _MERK.search(onderwerp or "")
    return m.group(1) if m else None


# ── versturen ───────────────────────────────────────────────────────────────

def _stuur_blokkerend(*, host: str, port: int, gebruiker: str, wachtwoord: str,
                      afzender: str, ontvangers: List[str], onderwerp: str,
                      tekst: str, message_id: str, tls: str) -> None:
    bericht = EmailMessage()
    bericht["From"] = afzender
    bericht["To"] = ", ".join(ontvangers)
    bericht["Subject"] = onderwerp
    bericht["Message-ID"] = message_id
    # Antwoorden op deze mail komen terug in dezelfde mailbox; dat is de
    # bedoeling en niet iets wat de gebruiker hoeft in te stellen.
    bericht["Reply-To"] = afzender
    # Ons eigen merk op de mail. Komt hij ooit in onze eigen postbus terecht —
    # een kopie, een doorstuurregel, een lijst die terugkaatst — dan herkennen
    # we hem en laten we hem liggen in plaats van er een "antwoord" van te maken.
    bericht["X-LabX"] = "melding"
    bericht["Auto-Submitted"] = "auto-generated"
    bericht.set_content(tekst)

    context = ssl.create_default_context()
    if tls == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=30, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
    try:
        if tls == "starttls":
            server.starttls(context=context)
        if gebruiker:
            server.login(gebruiker, wachtwoord)
        server.send_message(bericht)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001 — afsluiten mag de verzending niet alsnog laten falen
            pass


async def stuur(*, config: Dict[str, Any], wachtwoord: str, onderwerp: str,
                tekst: str, sleutel: str) -> str:
    """Verstuur een melding. Geeft de Message-ID terug — daarmee vinden we
    het antwoord straks terug."""
    host = str(config.get("smtp_host") or "").strip()
    if not host:
        raise RuntimeError("Geen SMTP-server ingesteld op dit kanaal")
    ontvangers = [x.strip() for x in str(config.get("to") or "").replace(";", ",").split(",")
                  if x.strip()]
    if not ontvangers:
        raise RuntimeError("Geen ontvanger ingesteld op dit kanaal")
    afzender = str(config.get("from") or config.get("smtp_user") or ontvangers[0]).strip()
    message_id = make_msgid(domain="labx.local")

    await asyncio.to_thread(
        _stuur_blokkerend,
        host=host, port=int(config.get("smtp_port") or 587),
        gebruiker=str(config.get("smtp_user") or "").strip(), wachtwoord=wachtwoord or "",
        afzender=afzender, ontvangers=ontvangers,
        onderwerp=f"{onderwerp} {merkteken(sleutel)}",
        tekst=tekst, message_id=message_id,
        tls=str(config.get("smtp_tls") or "starttls").lower())
    return message_id


# ── ophalen ─────────────────────────────────────────────────────────────────

def _tekst_van(bericht: email.message.Message) -> str:
    """De leesbare tekst uit een mail. Alleen text/plain: de HTML-variant van
    hetzelfde antwoord levert tags op waar de agent niets aan heeft, en de
    citatie eronder wordt toch afgeknipt."""
    if bericht.is_multipart():
        for deel in bericht.walk():
            if deel.get_content_type() == "text/plain" and "attachment" not in str(
                    deel.get("Content-Disposition") or ""):
                try:
                    return deel.get_payload(decode=True).decode(
                        deel.get_content_charset() or "utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    continue
        return ""
    try:
        return bericht.get_payload(decode=True).decode(
            bericht.get_content_charset() or "utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return str(bericht.get_payload() or "")


_CITAAT = re.compile(
    r"^(>|Op .* schreef |On .* wrote:|-{2,} ?Oorspronkelijk bericht|"
    r"-{2,} ?Original Message|Van: |From: |Verzonden: |Sent: )", re.M)


def knip_citaat(tekst: str) -> str:
    """Alleen het NIEUWE deel van een antwoord.

    Een mailprogramma plakt de hele voorgaande conversatie eronder. Die
    ongefilterd als prompt doorgeven betekent dat de agent zijn eigen melding
    terugleest als instructie — en bij lange draden verdrinkt het echte
    antwoord in citaat.
    """
    regels = (tekst or "").replace("\r\n", "\n").split("\n")
    uit: List[str] = []
    for regel in regels:
        if _CITAAT.match(regel.strip()):
            break
        uit.append(regel)
    schoon = "\n".join(uit).strip()
    # Handtekening eraf: alles na een regel die precies "--" is.
    schoon = re.split(r"^-- ?$", schoon, maxsplit=1, flags=re.M)[0].strip()
    return schoon or (tekst or "").strip()


def _kop(waarde: Any) -> str:
    if not waarde:
        return ""
    try:
        return str(make_header(decode_header(str(waarde))))
    except Exception:  # noqa: BLE001
        return str(waarde)


# Afzenders die per definitie een machine zijn. Een antwoord van dit adres
# bestaat niet; wat er binnenkomt is een rapport over wat wij zelf stuurden.
_MACHINE_AFZENDER = re.compile(
    r"mailer-daemon|postmaster|no-?reply|do-?not-?reply|bounce|automat", re.IGNORECASE)


def _is_automatisch(bericht: Any, *, eigen_adres: str = "") -> bool:
    """Komt dit van een mens, of van een mailserver?

    Dit onderscheid moest er komen na 12-09-2026. Het mailkanaal werd
    ingesteld op een BESTAANDE postbus met zeshonderd berichten erin, waaronder
    een stapel bounces van een heel ander systeem. LabX las de laatste vijftig
    als antwoorden, kon ze nergens aan koppelen, en stuurde op elk daarvan een
    bevestiging terug: vijftig mails in een paar minuten. Elk van die
    bevestigingen kon zelf weer bouncen — een lus die zichzelf voedt.

    De controles staan op volgorde van hardheid. Een leeg Return-Path (`<>`) is
    de afspraak uit RFC 3834 waarmee een mailserver zegt "beantwoord dit niet";
    `Auto-Submitted` zegt hetzelfde met zoveel woorden; een `multipart/report`
    is per definitie een rapport. Pas daarna kijken we naar de afzender, want
    dat is de enige heuristiek in de rij.
    """
    retour = (bericht.get("Return-Path") or "").strip()
    if retour in ("<>", "<MAILER-DAEMON>"):
        return True
    auto = (bericht.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return True
    if (bericht.get_content_type() or "").lower() == "multipart/report":
        return True
    if (bericht.get("X-LabX") or "").strip():
        return True          # onze eigen melding, teruggekomen
    afzender = (bericht.get("From") or "")
    if eigen_adres and eigen_adres.lower() in afzender.lower():
        return True          # wijzelf
    if _MACHINE_AFZENDER.search(afzender):
        return True
    return False


def _haal_blokkerend(*, host: str, port: int, gebruiker: str, wachtwoord: str,
                     map_naam: str, ssl_aan: bool, laatste_uid: int) -> List[Dict[str, Any]]:
    if ssl_aan:
        verbinding = imaplib.IMAP4_SSL(host, port, timeout=30)
    else:
        verbinding = imaplib.IMAP4(host, port, timeout=30)
    try:
        verbinding.login(gebruiker, wachtwoord)
        verbinding.select(map_naam)
        # Op UID zoeken en niet op volgnummer: volgnummers schuiven op zodra er
        # een mail verwijderd wordt, en dan slaan we berichten over of lezen we
        # ze dubbel.
        status, data = verbinding.uid("search", None, f"UID {laatste_uid + 1}:*")
        if status != "OK":
            return []
        uids = [u for u in (data[0] or b"").split() if u]
        uit: List[Dict[str, Any]] = []
        for uid in uids[-50:]:
            nummer = int(uid)
            if nummer <= laatste_uid:
                # IMAP geeft bij "N:*" altijd minstens één bericht terug, ook
                # als er niets nieuws is. Zonder deze toets verwerken we de
                # laatste mail bij elke ronde opnieuw.
                continue
            status, ruw = verbinding.uid("fetch", uid, "(RFC822)")
            if status != "OK" or not ruw or not ruw[0]:
                continue
            bericht = email.message_from_bytes(ruw[0][1])
            if _is_automatisch(bericht, eigen_adres=gebruiker):
                # Wel de UID bijwerken (dat gebeurt bij de aanroeper op grond
                # van `hoogste`), maar niet verwerken: een bounce is geen
                # antwoord van een mens.
                continue
            uit.append({
                "uid": nummer,
                "van": _kop(bericht.get("From")),
                "onderwerp": _kop(bericht.get("Subject")),
                "in_reply_to": (bericht.get("In-Reply-To") or "").strip(),
                "references": (bericht.get("References") or "").strip(),
                "tekst": knip_citaat(_tekst_van(bericht)),
                "datum": _datum(bericht.get("Date")),
            })
        return uit
    finally:
        try:
            verbinding.logout()
        except Exception:  # noqa: BLE001
            pass


def _datum(waarde: Any) -> Optional[str]:
    try:
        return parsedate_to_datetime(str(waarde)).isoformat()
    except Exception:  # noqa: BLE001
        return None


async def haal_antwoorden(*, config: Dict[str, Any], wachtwoord: str,
                          laatste_uid: int) -> List[Dict[str, Any]]:
    host = str(config.get("imap_host") or "").strip()
    if not host:
        return []
    return await asyncio.to_thread(
        _haal_blokkerend,
        host=host, port=int(config.get("imap_port") or 993),
        gebruiker=str(config.get("imap_user") or config.get("smtp_user") or "").strip(),
        wachtwoord=wachtwoord or "",
        map_naam=str(config.get("imap_folder") or "INBOX"),
        ssl_aan=bool(config.get("imap_ssl", True)),
        laatste_uid=int(laatste_uid or 0))


async def hoogste_uid(*, config: Dict[str, Any], wachtwoord: str) -> int:
    """Waar staat de postbus NU? Het ijkpunt voor een pas ingesteld kanaal.

    Alles wat er al ligt, ligt er buiten ons om: een postbus die al bestond kan
    geen antwoorden bevatten op meldingen die nog niet verstuurd zijn. Beginnen
    bij nul betekent die hele geschiedenis als antwoorden lezen, en dat kostte
    op 12-09-2026 vijftig mails.
    """
    host = str(config.get("imap_host") or "").strip()
    if not host:
        return 0

    def _laatste() -> int:
        verbinding = (imaplib.IMAP4_SSL(host, int(config.get("imap_port") or 993), timeout=30)
                      if config.get("imap_ssl", True)
                      else imaplib.IMAP4(host, int(config.get("imap_port") or 143), timeout=30))
        try:
            verbinding.login(str(config.get("imap_user") or config.get("smtp_user") or ""),
                             wachtwoord or "")
            verbinding.select(str(config.get("imap_folder") or "INBOX"))
            status, data = verbinding.uid("search", None, "ALL")
            if status != "OK":
                return 0
            uids = [int(u) for u in (data[0] or b"").split() if u]
            return max(uids) if uids else 0
        finally:
            try:
                verbinding.logout()
            except Exception:  # noqa: BLE001
                pass

    try:
        return await asyncio.to_thread(_laatste)
    except Exception as exc:  # noqa: BLE001
        log.warningx("Kon het ijkpunt van de postbus niet bepalen", error=str(exc)[:200])
        return 0


async def controleer(*, config: Dict[str, Any], wachtwoord: str) -> Dict[str, Any]:
    """Kunnen we versturen en (als het ingesteld is) ophalen? Beide apart
    melden, want een verkeerd IMAP-wachtwoord mag niet lijken op een kapotte
    SMTP."""
    uit: Dict[str, Any] = {"smtp": None, "imap": None}

    def _smtp() -> str:
        host = str(config.get("smtp_host") or "").strip()
        tls = str(config.get("smtp_tls") or "starttls").lower()
        context = ssl.create_default_context()
        server = (smtplib.SMTP_SSL(host, int(config.get("smtp_port") or 465), timeout=20,
                                   context=context)
                  if tls == "ssl" else
                  smtplib.SMTP(host, int(config.get("smtp_port") or 587), timeout=20))
        try:
            if tls == "starttls":
                server.starttls(context=context)
            if config.get("smtp_user"):
                server.login(str(config["smtp_user"]), wachtwoord or "")
            return "ok"
        finally:
            try:
                server.quit()
            except Exception:  # noqa: BLE001
                pass

    try:
        uit["smtp"] = await asyncio.to_thread(_smtp)
    except Exception as exc:  # noqa: BLE001
        uit["smtp"] = f"fout: {str(exc)[:200]}"

    if str(config.get("imap_host") or "").strip():
        def _imap() -> str:
            verbinding = (imaplib.IMAP4_SSL(str(config["imap_host"]),
                                            int(config.get("imap_port") or 993), timeout=20)
                          if config.get("imap_ssl", True) else
                          imaplib.IMAP4(str(config["imap_host"]),
                                        int(config.get("imap_port") or 143), timeout=20))
            try:
                verbinding.login(str(config.get("imap_user") or config.get("smtp_user") or ""),
                                 wachtwoord or "")
                status, _ = verbinding.select(str(config.get("imap_folder") or "INBOX"))
                return "ok" if status == "OK" else f"map niet gevonden ({status})"
            finally:
                try:
                    verbinding.logout()
                except Exception:  # noqa: BLE001
                    pass
        try:
            uit["imap"] = await asyncio.to_thread(_imap)
        except Exception as exc:  # noqa: BLE001
            uit["imap"] = f"fout: {str(exc)[:200]}"
    uit["ok"] = uit["smtp"] == "ok" and uit["imap"] in (None, "ok")
    return uit
