"""De begeleide opdracht: een planning of workflow in meerdere beurten.

Twee eigenschappen mogen hier nooit verschuiven:

1. **De server bepaalt welke vraag openstaat.** `vul_aan` neemt alleen een
   waarde aan, geen veldnaam. Kon het model zelf kiezen bij welk veld een
   antwoord hoort, dan kon het velden overslaan of invullen waar nooit naar
   gevraagd is -- bij iets dat straks vanzelf gaat draaien merk je dat pas als
   het draait.
2. **Afronden is ÉÉN bevestiging over het geheel**, met het complete concept
   in de zin. Per veld bevestigen leidt ertoe dat je na de derde keer blind ja
   zegt, en dan is het geen bevestiging meer.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.voice import opdracht  # noqa: E402


# ── Nederlandse tijden naar cron ─────────────────────────────────────────────

@pytest.mark.parametrize("zin,verwacht", [
    ("elke werkdag om negen uur", "0 9 * * 1-5"),
    ("elke dag om half acht", "30 7 * * *"),
    ("elke maandag om 7 uur", "0 7 * * 1"),
    ("elk uur", "0 * * * *"),
    ("elke 15 minuten", "*/15 * * * *"),
    ("om 06:30", "30 6 * * *"),
])
def test_tijden_in_spreektaal_worden_cron(zin, verwacht):
    assert opdracht.naar_cron(zin) == verwacht


def test_half_negen_is_half_voor_negen():
    """De klassieke valkuil: "half negen" is 8:30, niet 9:30."""
    assert opdracht.naar_cron("elke dag om half negen") == "30 8 * * *"


def test_onbegrijpelijke_tijd_levert_niets_op():
    """Liever doorvragen dan iets aannemelijks verzinnen voor iets dat straks
    vanzelf gaat draaien."""
    assert opdracht.naar_cron("af en toe eens kijken") is None
    assert opdracht.naar_cron("") is None


def test_cron_gaat_terug_naar_spreektaal():
    """De bevestigingszin moet voorleesbaar zijn; "0 9 * * 1-5" is dat niet."""
    assert opdracht.cron_in_woorden("0 9 * * 1-5") == "elke werkdag om 09:00"
    assert opdracht.cron_in_woorden("*/15 * * * *") == "elke 15 minuten"


# ── De lus zelf ──────────────────────────────────────────────────────────────

@pytest.fixture()
def db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    import models.lab  # noqa: F401
    import models.voice  # noqa: F401

    monkeypatch.setenv("LABX_FERNET_KEY",
                       "dGVzdC1zbGV1dGVsLXZvb3ItZGUtdW5pdC10ZXN0cy0wMDA9")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        models.lab.Lab.__table__,
        models.voice.VoiceSession.__table__,
        models.voice.VoiceConcept.__table__,
    ])
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _sessie(db) -> str:
    from datetime import datetime, timezone
    from models.voice import VoiceSession

    rij = VoiceSession(id="s1", brein="pipeline", microfoon="ptt",
                       status="actief",
                       started_at=datetime.now(timezone.utc).isoformat())
    db.add(rij)
    db.commit()
    return rij.id


def test_een_planning_wordt_veld_voor_veld_opgebouwd(db):
    sid = _sessie(db)
    concept = opdracht.begin(db, sid, "planning")

    assert opdracht.vraag_nu(concept) == "Hoe moet de planning heten?"

    uit = opdracht.vul_aan(db, concept, "Ochtendcontrole")
    assert "lab" in uit["vraag"].lower()

    uit = opdracht.vul_aan(db, concept, "Fabric")
    assert "wanneer" in uit["vraag"].lower()

    # Een tijd die er niet uit te halen is, mag het concept niet vervuilen.
    uit = opdracht.vul_aan(db, concept, "af en toe")
    assert "wanneer" not in (concept.velden or {})
    assert "bijvoorbeeld" in uit["vraag"]

    uit = opdracht.vul_aan(db, concept, "elke werkdag om negen uur")
    assert concept.velden["wanneer"] == "0 9 * * 1-5"

    uit = opdracht.vul_aan(db, concept, "controleer of de pipelines gedraaid hebben")
    assert uit.get("klaar") is True
    assert opdracht.vraag_nu(concept) is None


def test_de_server_bepaalt_het_veld_niet_het_model(db):
    """vul_aan neemt geen veldnaam aan: er is geen manier om een veld over te
    slaan of er eentje in te vullen waar niet naar gevraagd is."""
    import inspect

    parameters = inspect.signature(opdracht.vul_aan).parameters
    assert "veld" not in parameters
    assert set(parameters) == {"db", "concept", "waarde"}


def test_een_workflow_blijft_doorvragen_tot_je_klaar_zegt(db):
    sid = _sessie(db)
    concept = opdracht.begin(db, sid, "workflow")

    opdracht.vul_aan(db, concept, "Nachtelijke controle")
    opdracht.vul_aan(db, concept, "Kijkt of alles nog draait")

    uit = opdracht.vul_aan(db, concept, "Lees de logs van vannacht")
    assert "volgende stap" in uit["vraag"].lower()

    uit = opdracht.vul_aan(db, concept, "Meld wat er misging")
    assert "volgende stap" in uit["vraag"].lower()

    uit = opdracht.vul_aan(db, concept, "klaar")
    assert uit.get("klaar") is True
    # Stappen zijn getypeerde knopen, geen losse zinnen: dat is wat een
    # vertakking of een lus mogelijk maakt.
    assert [s["prompt"] for s in concept.velden["stappen"]] == [
        "Lees de logs van vannacht", "Meld wat er misging"]
    assert all(s["type"] == "agent" for s in concept.velden["stappen"])


def test_klaar_zeggen_zonder_enkele_stap_kan_niet(db):
    sid = _sessie(db)
    concept = opdracht.begin(db, sid, "workflow")
    opdracht.vul_aan(db, concept, "Lege workflow")
    opdracht.vul_aan(db, concept, "geen")          # omschrijving overslaan

    uit = opdracht.vul_aan(db, concept, "klaar")
    assert "eerste stap" in uit["vraag"].lower()
    assert not (concept.velden or {}).get("stappen_klaar")


def test_een_tweede_opdracht_breekt_de_eerste_af(db):
    """Twee lopende concepten maken "vul aan" dubbelzinnig, en dan weet
    niemand meer waar een antwoord bij hoort."""
    sid = _sessie(db)
    eerste = opdracht.begin(db, sid, "planning")
    opdracht.begin(db, sid, "workflow")

    db.refresh(eerste)
    assert eerste.status == "afgebroken"
    assert opdracht.lopend(db, sid).soort == "workflow"


def test_stoppen_laat_niets_klaarstaan(db):
    sid = _sessie(db)
    concept = opdracht.begin(db, sid, "planning")
    opdracht.vul_aan(db, concept, "Wegwerpplanning")
    opdracht.stop(db, concept)

    assert opdracht.lopend(db, sid) is None


def test_afronden_sluit_een_openstaande_stappenlijst(db):
    """Dit liep echt dood in een gesprek.

    De gebruiker zei "klaar", de transcriptie maakte er "Klar" van, het model
    gaf het niet door, en afronden antwoordde "Nog niet compleet. En de
    volgende stap?". Daarna gaf het model het op en BEWEERDE dat de workflow
    was aangemaakt. Wie afrondt, bedoelt afronden.
    """
    sid = _sessie(db)
    concept = opdracht.begin(db, sid, "workflow")
    opdracht.vul_aan(db, concept, "Nachtcontrole")
    opdracht.vul_aan(db, concept, "Kijkt of alles draait")
    opdracht.vul_aan(db, concept, "Lees de logs")

    assert opdracht.vraag_nu(concept) is not None, "de lijst staat nog open"
    assert opdracht.sluit_open_lijst(db, concept) is True
    assert opdracht.vraag_nu(concept) is None, "nu is het compleet"


def test_afronden_sluit_niets_als_er_nog_geen_stap_is(db):
    """Een workflow zonder stappen is geen workflow."""
    sid = _sessie(db)
    concept = opdracht.begin(db, sid, "workflow")
    opdracht.vul_aan(db, concept, "Leeg")
    opdracht.vul_aan(db, concept, "geen")

    assert opdracht.sluit_open_lijst(db, concept) is False
    assert opdracht.vraag_nu(concept) is not None


@pytest.mark.parametrize("gezegd", ["klaar", "Klaar.", "Klar", "dat was het",
                                    "KLAAR!", "verder niets"])
def test_verschillende_manieren_om_klaar_te_zeggen(db, gezegd):
    """De transcriptie is niet perfect; "Klar" mag het gesprek niet ophangen."""
    sid = _sessie(db)
    concept = opdracht.begin(db, sid, "workflow")
    opdracht.vul_aan(db, concept, "Nachtcontrole")
    opdracht.vul_aan(db, concept, "geen")
    opdracht.vul_aan(db, concept, "Lees de logs")

    uit = opdracht.vul_aan(db, concept, gezegd)
    assert uit.get("klaar") is True, f"'{gezegd}' sloot de lijst niet af"
