"""De bevestigingslus van de spraaklaag, end-to-end tegen een echte database.

Dit is het stuk dat namens de gebruiker in klantsystemen schrijft, dus de
eigenschappen die hier worden vastgelegd zijn eigenschappen die niet mogen
verschuiven:

1. Een schrijfactie gebeurt NOOIT bij de aanroep, alleen na bevestiging.
2. De bevestigingszin komt uit de opgeloste parameters, niet uit wat het model
   zei — en bevat de sleutel die daadwerkelijk uitgevoerd wordt.
3. Een verkeerd of uitblijvend antwoord voert niets uit.
4. Twee openstaande bevestigingen tegelijk kunnen niet: dan is "ja" dubbelzinnig.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture()
def db(monkeypatch):
    """Een lege SQLite met alleen de tabellen die deze tests raken.

    Niet via init_db(): die draait ook de migraties van een BESTAANDE
    installatie, en die veronderstellen tabellen die hier nog niet bestaan.
    Hetzelfde patroon als guard_db in conftest.py — bouw wat je nodig hebt.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    import models.board  # noqa: F401  (registreert de tabellen)
    import models.lab  # noqa: F401
    import models.background_run  # noqa: F401
    import models.thread  # noqa: F401
    import models.message  # noqa: F401  (background_runs verwijst ernaar)
    import models.voice  # noqa: F401

    monkeypatch.setenv("LABX_FERNET_KEY",
                       "dGVzdC1zbGV1dGVsLXZvb3ItZGUtdW5pdC10ZXN0cy0wMDA9")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        models.board.Board.__table__,
        models.board.Ticket.__table__,
        models.board.TicketComment.__table__,
        models.lab.Lab.__table__,
        models.background_run.BackgroundRun.__table__,
        models.thread.Thread.__table__,
        models.message.Message.__table__,
        models.voice.VoiceSession.__table__,
        models.voice.VoiceEvent.__table__,
        models.voice.VoicePendingAction.__table__,
    ])
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _bord_met_ticket(db):
    from datetime import datetime, timezone
    from models.board import Board, Ticket
    nu = datetime.now(timezone.utc).isoformat()
    b = Board(name="Swinkels", key_prefix="SWI", seq=1,
              columns=[{"key": "todo", "name": "Te doen"},
                       {"key": "review", "name": "Review"}],
              provider="local", sync_direction="two_way",
              created_at=nu, updated_at=nu)
    db.add(b)
    db.commit()
    db.refresh(b)
    t = Ticket(board_id=b.id, key="SWI-141", title="Holland Malt — silver reload",
               status="todo", priority="normal", position=100.0,
               agent_state="idle", created_at=nu, updated_at=nu)
    db.add(t)
    db.commit()
    db.refresh(t)
    return b, t


def test_een_schrijfactie_gebeurt_niet_bij_de_aanroep(db):
    """De kern: aanroepen zet klaar, het voert niets uit."""
    from services.voice.sessie import VoiceSessieService
    from models.board import TicketComment

    _, t = _bord_met_ticket(db)
    svc = VoiceSessieService(db)
    s = svc.start(brein="pipeline", microfoon="ptt")

    uit = svc.roep_tool_aan(s.id, "plaats_opmerking",
                            {"ticket": "SWI-141", "tekst": "kijk hier even naar"})

    assert uit["wacht_op_bevestiging"] is True
    assert uit["vervalt_over_seconden"] == 20
    # En er staat nog niets op het ticket.
    assert db.query(TicketComment).filter(TicketComment.ticket_id == t.id).count() == 0


def test_de_zin_bevat_de_sleutel_die_echt_wordt_uitgevoerd(db):
    """Je moet horen wát er gebeurt, niet dat er íets gebeurt."""
    from services.voice.sessie import VoiceSessieService

    _, t = _bord_met_ticket(db)
    svc = VoiceSessieService(db)
    s = svc.start(brein="pipeline", microfoon="ptt")

    # Gesproken verwijzing zonder sleutel: die moet hij zelf oplossen.
    uit = svc.roep_tool_aan(s.id, "start_agent",
                            {"ticket": "het Holland Malt ticket", "klant": "Swinkels",
                             "instructie": "de zilveren pipelines doorladen"})
    assert "SWI-141" in uit["zin"]
    assert "zilveren pipelines" in uit["zin"]

    wacht = svc.openstaand(s.id)
    assert wacht.parameters["ticket_id"] == t.id


def test_een_verkeerd_antwoord_voert_niets_uit(db):
    from services.voice.sessie import VoiceSessieService
    from models.board import TicketComment

    _, t = _bord_met_ticket(db)
    svc = VoiceSessieService(db)
    s = svc.start(brein="pipeline", microfoon="ptt")
    svc.roep_tool_aan(s.id, "plaats_opmerking",
                      {"ticket": "SWI-141", "tekst": "iets"})

    uit = asyncio.run(
        svc.beantwoord(s.id, antwoord="ja", woord="Henk", stand="spraak"))

    assert uit["status"] == "geweigerd"
    assert db.query(TicketComment).filter(TicketComment.ticket_id == t.id).count() == 0


def test_het_juiste_woord_voert_wel_uit(db):
    from services.voice.sessie import VoiceSessieService
    from models.board import TicketComment

    _, t = _bord_met_ticket(db)
    svc = VoiceSessieService(db)
    s = svc.start(brein="pipeline", microfoon="ptt")
    svc.roep_tool_aan(s.id, "plaats_opmerking",
                      {"ticket": "SWI-141", "tekst": "kijk hier even naar"})

    uit = asyncio.run(
        svc.beantwoord(s.id, antwoord="Henk", woord="Henk", stand="spraak"))

    assert uit["status"] == "uitgevoerd"
    rijen = db.query(TicketComment).filter(TicketComment.ticket_id == t.id).all()
    assert len(rijen) == 1 and "kijk hier even naar" in rijen[0].body


def test_de_stand_bepaalt_welk_kanaal_mag(db):
    """In 'klik' doet het woord niets, in 'spraak' doet de knop niets."""
    from services.voice.sessie import VoiceSessieService

    _bord_met_ticket(db)
    svc = VoiceSessieService(db)
    s = svc.start(brein="pipeline", microfoon="open")

    svc.roep_tool_aan(s.id, "plaats_opmerking", {"ticket": "SWI-141", "tekst": "a"})
    uit = asyncio.run(
        svc.beantwoord(s.id, antwoord="Henk", woord="Henk", stand="klik"))
    assert uit["status"] == "geweigerd_kanaal"
    # De bevestiging staat er nog: een geweigerd kanaal is geen 'nee'.
    assert svc.openstaand(s.id) is not None

    uit = asyncio.run(
        svc.beantwoord(s.id, via_klik=True, akkoord=True, stand="spraak"))
    assert uit["status"] == "geweigerd_kanaal"


def test_maar_een_bevestiging_tegelijk(db):
    """Twee openstaande acties maken 'ja' dubbelzinnig."""
    from services.voice.sessie import VoiceSessieService
    from models.voice import VoicePendingAction

    _bord_met_ticket(db)
    svc = VoiceSessieService(db)
    s = svc.start(brein="pipeline", microfoon="ptt")

    svc.roep_tool_aan(s.id, "plaats_opmerking", {"ticket": "SWI-141", "tekst": "een"})
    svc.roep_tool_aan(s.id, "verplaats_ticket", {"ticket": "SWI-141", "kolom": "review"})

    wachtend = (db.query(VoicePendingAction)
                .filter(VoicePendingAction.session_id == s.id,
                        VoicePendingAction.status == "wacht").all())
    assert len(wachtend) == 1
    assert wachtend[0].tool == "verplaats_ticket"


def test_een_dubbelzinnige_verwijzing_zet_niets_klaar(db):
    """Bij twijfel doorvragen, niet alvast iets klaarzetten."""
    from datetime import datetime, timezone
    from models.board import Ticket
    from services.voice.sessie import VoiceSessieService

    b, _ = _bord_met_ticket(db)
    nu = datetime.now(timezone.utc).isoformat()
    db.add(Ticket(board_id=b.id, key="SWI-150", title="Holland Malt gold model",
                  status="todo", priority="normal", position=200.0,
                  agent_state="idle", created_at=nu, updated_at=nu))
    db.commit()

    svc = VoiceSessieService(db)
    s = svc.start(brein="pipeline", microfoon="ptt")
    uit = svc.roep_tool_aan(s.id, "start_agent", {"ticket": "Holland Malt"})

    assert "wacht_op_bevestiging" not in uit
    assert uit.get("keuzes")
    assert svc.openstaand(s.id) is None


def test_de_sessie_stoppen_laat_geen_actie_achter(db):
    """Een actie die een gesprek overleeft is precies wat je niet wilt."""
    from services.voice.sessie import VoiceSessieService

    _bord_met_ticket(db)
    svc = VoiceSessieService(db)
    s = svc.start(brein="pipeline", microfoon="ptt")
    svc.roep_tool_aan(s.id, "plaats_opmerking", {"ticket": "SWI-141", "tekst": "a"})
    assert svc.openstaand(s.id) is not None

    svc.stop(s.id)
    assert svc.openstaand(s.id) is None


def test_lezen_hoeft_geen_bevestiging(db):
    from services.voice.sessie import VoiceSessieService

    _bord_met_ticket(db)
    svc = VoiceSessieService(db)
    s = svc.start(brein="pipeline", microfoon="ptt")

    uit = svc.roep_tool_aan(s.id, "ticket_status", {"ticket": "SWI-141"})
    assert "wacht_op_bevestiging" not in uit
    assert uit["feiten"]["sleutel"] == "SWI-141"


def test_een_onbekende_tool_wordt_niet_stil_genegeerd(db):
    from services.voice.sessie import VoiceSessieService

    svc = VoiceSessieService(db)
    s = svc.start(brein="pipeline", microfoon="ptt")
    uit = svc.roep_tool_aan(s.id, "verwijder_alles", {})
    assert "fout" in uit


def test_de_verboden_acties_bestaan_niet_als_tool():
    """De vijf acties die nooit via spraak mogen, zijn geen afwezige CONTROLE
    maar een afwezige FUNCTIE. Deze test bewaakt dat ze niet terugsluipen."""
    from services.voice.acties import LEESACTIES
    from services.voice.schrijfacties import SCHRIJFACTIES, UITVOERDERS

    alles = set(LEESACTIES) | set(SCHRIJFACTIES) | set(UITVOERDERS)
    verboden = ("verwijder", "delete", "sync", "synchroniseer",
                "secret", "geheim", "kluis", "instelling", "guard")
    for naam in alles:
        assert not any(v in naam.lower() for v in verboden), naam
