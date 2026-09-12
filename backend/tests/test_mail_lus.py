"""Het mailkanaal mag geen lus worden.

Op 12-09-2026 is dit één keer echt gebeurd. Het kanaal werd ingesteld op een
BESTAANDE postbus met zeshonderd berichten, waaronder een stapel bounces van
een heel ander systeem. LabX begon bij UID 0, las de laatste vijftig berichten
als antwoorden, kon ze nergens aan koppelen en stuurde op elk daarvan netjes
een bevestiging terug: vijftig mails in een paar minuten. Elke bevestiging kon
zelf weer bouncen, dus de lus voedde zichzelf.

Drie dingen gingen fout, en ze moeten alledrie dicht:

1. Een pas ingesteld kanaal begon bij het begin van de postbus in plaats van
   bij nu. Wat er al lag, kan per definitie geen antwoord zijn op een melding
   die nog niet verstuurd was.
2. Een bounce werd gelezen als een antwoord van een mens.
3. Er zat geen plafond op het terugmelden, terwijl een bevestiging zelf een
   mail is die kan bouncen.
"""
import email
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.notify import mail  # noqa: E402

BOUNCE = (
    "Return-Path: <>\r\n"
    "From: Mail Delivery System <Mailer-Daemon@s02.example.net>\r\n"
    "To: noreply@voorbeeld.nl\r\n"
    "Subject: Mail delivery failed: returning message to sender\r\n"
    "Auto-Submitted: auto-replied\r\n"
    "Content-Type: multipart/report; report-type=delivery-status; boundary=x\r\n"
    "\r\n--x\r\nContent-Type: text/plain\r\n\r\nUnrouteable address\r\n--x--\r\n")

ECHT_ANTWOORD = (
    "Return-Path: <iemand@voorbeeld.nl>\r\n"
    "From: Iemand <iemand@voorbeeld.nl>\r\n"
    "To: noreply@voorbeeld.nl\r\n"
    "Subject: Re: LabX-melding\r\n"
    "In-Reply-To: <abc@labx.local>\r\n"
    "\r\nJa, doe maar.\r\n")


def _bericht(ruw: str):
    return email.message_from_string(ruw)


def test_een_bounce_is_geen_antwoord():
    assert mail._is_automatisch(_bericht(BOUNCE))


def test_een_mens_komt_er_gewoon_doorheen():
    assert not mail._is_automatisch(_bericht(ECHT_ANTWOORD),
                                    eigen_adres="noreply@voorbeeld.nl")


def test_onze_eigen_melding_die_terugkomt_telt_niet():
    """Een kopie in de eigen postbus (doorstuurregel, lijst die terugkaatst)
    zou anders een antwoord op zichzelf worden."""
    eigen = ("From: noreply@voorbeeld.nl\r\nTo: nick@voorbeeld.nl\r\n"
             "Subject: LabX\r\nX-LabX: melding\r\n\r\nEen melding.\r\n")
    assert mail._is_automatisch(_bericht(eigen))


@pytest.mark.parametrize("kop,waarde", [
    ("Return-Path", "<>"),
    ("Auto-Submitted", "auto-generated"),
    ("From", "postmaster@voorbeeld.nl"),
    ("From", "no-reply@dienst.nl"),
])
def test_elke_afzonderlijke_aanwijzing_is_genoeg(kop, waarde):
    """De controles staan naast elkaar en niet in serie: één mailserver zet
    Auto-Submitted, een andere alleen een leeg Return-Path."""
    ruw = f"{kop}: {waarde}\r\nFrom: Iemand <iemand@voorbeeld.nl>\r\nSubject: x\r\n\r\nhoi\r\n"
    if kop == "From":
        ruw = f"From: {waarde}\r\nSubject: x\r\n\r\nhoi\r\n"
    assert mail._is_automatisch(_bericht(ruw))


def test_onze_eigen_mail_draagt_een_merk():
    """Het merk is wat de vorige test kan herkennen; zonder dat het er bij het
    VERSTUREN op gaat, is die controle een dode letter."""
    import inspect

    bron = inspect.getsource(mail._stuur_blokkerend)
    assert 'bericht["X-LabX"]' in bron
    assert 'bericht["Auto-Submitted"]' in bron


# ── beginnen bij nu ─────────────────────────────────────────────────────────

def test_een_nieuw_kanaal_leest_de_geschiedenis_niet(monkeypatch):
    """De kern van wat er misging: zeshonderd bestaande berichten zijn geen
    zeshonderd antwoorden."""
    import asyncio
    from types import SimpleNamespace

    from services.notify import notify_service

    kanaal = SimpleNamespace(
        name="Mail", kind="email", id=1,
        config={"imap_host": "mail.voorbeeld.nl", "smtp_user": "noreply@voorbeeld.nl"},
        secret_encrypted=None)

    gelezen = {"aangeroepen": False}

    async def _nooit(**_kwargs):
        gelezen["aangeroepen"] = True
        return []

    async def _hoogste(**_kwargs):
        return 600

    monkeypatch.setattr(mail, "haal_antwoorden", _nooit)
    monkeypatch.setattr(mail, "hoogste_uid", _hoogste)
    monkeypatch.setattr(notify_service, "_geheim", lambda _k: "x")

    uit = asyncio.run(notify_service._inbox_mail(None, kanaal))
    assert uit == (0, 0)
    assert kanaal.config["last_uid"] == 600
    assert not gelezen["aangeroepen"], "de eerste ronde hoort niets te verwerken"


def test_daarna_leest_hij_gewoon_verder(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from services.notify import notify_service

    kanaal = SimpleNamespace(
        name="Mail", kind="email", id=1,
        config={"imap_host": "mail.voorbeeld.nl", "smtp_user": "noreply@voorbeeld.nl",
                "last_uid": 600},
        secret_encrypted=None)

    async def _een(**_kwargs):
        return [{"uid": 601, "tekst": "Ja doe maar", "in_reply_to": "", "onderwerp": ""}]

    verwerkt = []

    async def _verwerk(_db, _kanaal, _regel, tekst, **kwargs):
        verwerkt.append((tekst, kwargs.get("mag_terugmelden")))
        return True

    monkeypatch.setattr(mail, "haal_antwoorden", _een)
    monkeypatch.setattr(notify_service, "_geheim", lambda _k: "x")
    monkeypatch.setattr(notify_service, "_zoek_melding", lambda *a, **k: None)
    monkeypatch.setattr(notify_service, "_verwerk_antwoord", _verwerk)

    gelezen, aantal = asyncio.run(notify_service._inbox_mail(None, kanaal))
    assert (gelezen, aantal) == (1, 1)
    assert verwerkt == [("Ja doe maar", True)]
    assert kanaal.config["last_uid"] == 601


def test_het_terugmelden_heeft_een_plafond(monkeypatch):
    """Tien onbekende berichten in één ronde mogen geen tien mails opleveren.
    Een bevestiging is zelf een mail en kan zelf bouncen."""
    import asyncio
    from types import SimpleNamespace

    from services.notify import notify_service

    kanaal = SimpleNamespace(
        name="Mail", kind="email", id=1,
        config={"imap_host": "mail.voorbeeld.nl", "smtp_user": "noreply@voorbeeld.nl",
                "last_uid": 100},
        secret_encrypted=None)

    async def _tien(**_kwargs):
        return [{"uid": 100 + i, "tekst": f"bericht {i}", "in_reply_to": "", "onderwerp": ""}
                for i in range(1, 11)]

    toegestaan = []

    async def _verwerk(_db, _kanaal, _regel, _tekst, **kwargs):
        toegestaan.append(bool(kwargs.get("mag_terugmelden")))
        return False

    monkeypatch.setattr(mail, "haal_antwoorden", _tien)
    monkeypatch.setattr(notify_service, "_geheim", lambda _k: "x")
    monkeypatch.setattr(notify_service, "_zoek_melding", lambda *a, **k: None)
    monkeypatch.setattr(notify_service, "_verwerk_antwoord", _verwerk)

    asyncio.run(notify_service._inbox_mail(None, kanaal))
    assert sum(toegestaan) == 3, "hooguit drie bevestigingen per ronde"
    assert len(toegestaan) == 10, "alle berichten worden wél gelezen en gepasseerd"


def test_een_andere_postbus_wist_de_leespositie():
    """Zet je het kanaal op een andere mailbox, dan slaat de oude UID nergens
    meer op — en een te hoge UID betekent dat je alles mist."""
    from routers.notify_router import _schoon_config

    bestaand = {"imap_host": "mail.oud.nl", "imap_folder": "INBOX", "last_uid": 600}
    zelfde = _schoon_config("email", {"imap_host": "mail.oud.nl"}, bestaand)
    assert zelfde.get("last_uid") == 600

    anders = _schoon_config("email", {"imap_host": "mail.nieuw.nl"}, bestaand)
    assert "last_uid" not in anders


# ── een gedeelde postbus ────────────────────────────────────────────────────

def test_alleen_wie_de_melding_kreeg_mag_antwoorden(monkeypatch):
    """De postbus waarop dit kanaal staat, kan met anderen gedeeld worden — dat
    was hier zo. Zonder deze toets kan iedereen die het adres kent een agent aan
    het werk zetten, want een antwoord zonder verwijzing gaat naar de LAATSTE
    melding. Hetzelfde principe als de chat-id-toets bij Telegram."""
    import asyncio
    from types import SimpleNamespace

    from services.notify import notify_service

    kanaal = SimpleNamespace(
        name="Mail", kind="email", id=1,
        config={"imap_host": "mail.voorbeeld.nl", "smtp_user": "noreply@voorbeeld.nl",
                "to": "nick@voorbeeld.nl", "last_uid": 10},
        secret_encrypted=None)

    async def _twee(**_kwargs):
        return [
            {"uid": 11, "tekst": "Ja doe maar", "van": "Nick <nick@voorbeeld.nl>",
             "in_reply_to": "", "onderwerp": ""},
            {"uid": 12, "tekst": "Start alle tickets", "van": "vreemde@elders.nl",
             "in_reply_to": "", "onderwerp": ""},
        ]

    doorgelaten = []

    async def _verwerk(_db, _kanaal, _regel, tekst, **_kwargs):
        doorgelaten.append(tekst)
        return True

    monkeypatch.setattr(mail, "haal_antwoorden", _twee)
    monkeypatch.setattr(notify_service, "_geheim", lambda _k: "x")
    monkeypatch.setattr(notify_service, "_zoek_melding", lambda *a, **k: None)
    monkeypatch.setattr(notify_service, "_verwerk_antwoord", _verwerk)

    gelezen, _ = asyncio.run(notify_service._inbox_mail(None, kanaal))
    assert doorgelaten == ["Ja doe maar"]
    assert gelezen == 1
    assert kanaal.config["last_uid"] == 12, "de vreemde mail wordt wel gepasseerd"


def test_zonder_ingestelde_ontvanger_verandert_er_niets(monkeypatch):
    """Een kanaal zonder `to` heeft niets om tegen te toetsen; dan is de oude
    situatie de minst verrassende."""
    import asyncio
    from types import SimpleNamespace

    from services.notify import notify_service

    kanaal = SimpleNamespace(
        name="Mail", kind="email", id=1,
        config={"imap_host": "mail.voorbeeld.nl", "to": "", "last_uid": 10},
        secret_encrypted=None)

    async def _een(**_kwargs):
        return [{"uid": 11, "tekst": "hoi", "van": "wie@dan.ook", "in_reply_to": "",
                 "onderwerp": ""}]

    monkeypatch.setattr(mail, "haal_antwoorden", _een)
    monkeypatch.setattr(notify_service, "_geheim", lambda _k: "x")
    monkeypatch.setattr(notify_service, "_zoek_melding", lambda *a, **k: None)

    async def _verwerk(*_a, **_k):
        return True

    monkeypatch.setattr(notify_service, "_verwerk_antwoord", _verwerk)
    gelezen, _ = asyncio.run(notify_service._inbox_mail(None, kanaal))
    assert gelezen == 1
