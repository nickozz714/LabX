"""Knoppen bij een vaste dienst, en de chat erbij.

Een vaste dienst is geen lab: er staat geen agent in. Toch is hij het ding
waar je wat MEE moet -- Cosmos leegmaken, opnieuw seeden, kijken wat erin
zit. Het model schrijft die knoppen, LabX voert ze uit.

Wat hier vastligt en stil kan sneuvelen:

- **De waarden van de omgeving gaan nooit naar het model.** Alleen de namen.
  Een emulator-sleutel is een geheim, ook als hij openbaar gedocumenteerd is.
- **Een knop die JIJ hebt bijgesteld overschrijft het model niet.** Anders is
  je correctie één verkenningsronde later weg.
- **`in` tegen een gestopte dienst is een nette 409**, geen docker-fout: er
  valt niets te exec'en in een container die er niet is.
- **De prompt-sjablonen renderen.** Ze bevatten JSON-voorbeelden met accolades
  en worden met `.format` gevuld; een gemiste placeholder valt pas om op het
  moment dat iemand op de knop drukt.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))



@pytest.fixture(autouse=True)
def _dienst_antwoordt(monkeypatch):
    """Deze tests gaan over knoppen, niet over opstarten.

    De echte gezondheidscontrole klopt van binnen het clusternetwerk aan bij
    de dienst; dat heeft hier geen netwerk om op te kloppen. Die controle
    heeft zijn eigen tests (test_dienst_gezondheid.py) -- hier zeggen we
    gewoon dat de dienst antwoordt, zodat deze tests blijven gaan waar ze
    over gaan.
    """
    async def klaar(_self, _id):
        return {"stand": "klaar", "klaar": True, "melding": "ok"}

    monkeypatch.setattr("services.lab.clusters.ClusterService.dienst_gezondheid",
                        klaar, raising=False)

@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    from models import (  # noqa: F401
        audit, azure_profile, background_run, board, claim_resource, cluster,
        cluster_dienst_actie, cluster_env_file, cluster_secret, guard_audit,
        guard_rule, lab, lab_extra, lab_host, lab_secret, lab_worker,
        mcp_server, message, model_runner, notification, plan, schedule,
        secret, skill, skill_tool, thread, time_entry, tool, voice, workflow,
    )

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _dienst(db, *, status: str = "draait"):
    from models.cluster import Cluster, ClusterDienst

    nu = "2026-10-19T10:00:00+00:00"
    c = Cluster(naam="Hive", netwerk="labx-cluster-hive", created_at=nu, updated_at=nu)
    db.add(c)
    db.commit()
    d = ClusterDienst(cluster_id=c.id, naam="ih-cosmos",
                      image="mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator:latest",
                      poorten=[8081, 10251], poort_map={"8081": 18000},
                      omgeving={"AZURE_COSMOS_EMULATOR_KEY": "zeer-geheim-abc123"},
                      status=status, container_id="deadbeef" if status == "draait" else None,
                      created_at=nu, updated_at=nu)
    db.add(d)
    db.commit()
    return c, d


# ── wat er naar het model gaat ───────────────────────────────────────────────

def test_het_beeld_bevat_de_sleutelnamen_maar_nooit_de_waarden(db):
    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    beeld = DienstActieService(db)._beeld(d)

    assert "AZURE_COSMOS_EMULATOR_KEY" in beeld
    assert "zeer-geheim-abc123" not in beeld


def test_het_beeld_noemt_image_netwerknaam_en_poorten(db):
    """Zonder deze drie kan het model geen bruikbaar script schrijven."""
    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    beeld = DienstActieService(db)._beeld(d)

    assert "azure-cosmos-emulator" in beeld
    assert "ih-cosmos" in beeld
    assert "labx-cluster-hive" in beeld
    assert "8081" in beeld


def test_de_prompts_renderen_zonder_ontbrekende_placeholder(db):
    """Een gemiste placeholder valt pas om als je op de knop drukt."""
    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    svc = DienstActieService(db)

    knoppen = svc._prompt_knoppen(d, "houd rekening met de seed")
    chat = svc._prompt_chat(d, "maak hem leeg", [{"rol": "mens", "tekst": "hoi"}])

    for tekst in (knoppen, chat):
        # Niets van de vorm {naam} mag blijven staan; de JSON-voorbeelden in
        # het sjabloon beginnen met {" en vallen hier dus buiten.
        assert not re.search(r"\{[a-z_]+\}", tekst), re.findall(r"\{[a-z_]+\}", tekst)
        assert "python:3.12-slim" in tekst
        assert "ih-cosmos" in tekst
    assert "houd rekening met de seed" in knoppen
    assert "maak hem leeg" in chat


def test_de_chatgeschiedenis_gaat_mee_maar_afgekapt(db):
    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    eerder = [{"rol": "mens", "tekst": f"vraag {i}"} for i in range(20)]
    prompt = DienstActieService(db)._prompt_chat(d, "en nu?", eerder)

    assert "vraag 19" in prompt
    assert "vraag 0" not in prompt  # alleen de laatste acht


# ── bewaren en bijstellen ────────────────────────────────────────────────────

def test_een_knop_zonder_script_wordt_geweigerd(db):
    from fastapi import HTTPException

    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    with pytest.raises(HTTPException) as fout:
        DienstActieService(db).zet(d.id, label="Leegmaken", script="   ")
    assert fout.value.status_code == 400


def test_een_onbekende_plek_wordt_geweigerd(db):
    from fastapi import HTTPException

    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    with pytest.raises(HTTPException) as fout:
        DienstActieService(db).zet(d.id, label="X", script="echo hoi", plek="overal")
    assert fout.value.status_code == 400


def test_meer_dan_het_maximum_aan_knoppen_kan_niet(db):
    from fastapi import HTTPException

    from services.lab.dienst_acties import MAX_ACTIES, DienstActieService

    _, d = _dienst(db)
    svc = DienstActieService(db)
    for i in range(MAX_ACTIES):
        svc.zet(d.id, label=f"knop {i}", script="echo hoi")
    with pytest.raises(HTTPException):
        svc.zet(d.id, label="nog een", script="echo hoi")


def test_het_model_overschrijft_een_knop_die_jij_hebt_bijgesteld_niet(db, monkeypatch):
    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    svc = DienstActieService(db)
    svc.zet(d.id, label="Leegmaken", script="echo MIJN VERSIE", bron="zelf")

    async def nep(_prompt):
        return ('{"toelichting": "x", "acties": [{"label": "Leegmaken", '
                '"script": "echo VAN HET MODEL", "plek": "in"}]}')

    monkeypatch.setattr(svc, "_vraag", nep)
    asyncio.run(svc.voorstellen(d.id))

    assert svc.lijst(d.id)[0].script == "echo MIJN VERSIE"


def test_het_model_werkt_zijn_eigen_knop_wel_bij(db, monkeypatch):
    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    svc = DienstActieService(db)
    svc.zet(d.id, label="Leegmaken", script="echo OUD", bron="model")

    async def nep(_prompt):
        return ('{"acties": [{"label": "Leegmaken", "script": "echo NIEUW", '
                '"plek": "naast", "image": "python:3.12-slim", "gevaarlijk": true}]}')

    monkeypatch.setattr(svc, "_vraag", nep)
    asyncio.run(svc.voorstellen(d.id))

    rijen = svc.lijst(d.id)
    assert len(rijen) == 1
    assert rijen[0].script == "echo NIEUW"
    assert rijen[0].plek == "naast"
    assert bool(rijen[0].gevaarlijk) is True


def test_proza_in_plaats_van_json_levert_geen_crash(db, monkeypatch):
    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    svc = DienstActieService(db)

    async def nep(_prompt):
        return "Ik weet het eigenlijk niet zo goed."

    monkeypatch.setattr(svc, "_vraag", nep)
    uit = asyncio.run(svc.voorstellen(d.id))

    assert uit["toegevoegd"] == 0
    assert uit["acties"] == []


# ── uitvoeren ────────────────────────────────────────────────────────────────

class _NepRuntime:
    def __init__(self):
        self.exec_aanroepen = []
        self.wegwerp_aanroepen = []

    async def exec(self, container_id, cmd, *, workdir="/workspace", timeout=0, **_):
        self.exec_aanroepen.append((container_id, cmd, workdir))
        return {"exit_code": 0, "output": "leeg", "truncated": False}

    async def run_wegwerp(self, *, image, cmd, netwerk=None, env=None, timeout=0, **_):
        self.wegwerp_aanroepen.append((image, cmd, netwerk, env))
        return {"exit_code": 0, "output": "3 databases weg", "truncated": False}


def test_een_actie_in_de_dienst_exect_in_de_container(db, monkeypatch):
    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    svc = DienstActieService(db)
    rt = _NepRuntime()
    monkeypatch.setattr(svc, "_runtime", lambda _c: rt)
    a = svc.zet(d.id, label="Kijken", script="ls /", plek="in")

    uit = asyncio.run(svc.draai(a.id))

    assert rt.exec_aanroepen == [("deadbeef", ["sh", "-c", "ls /"], "/")]
    assert uit["exit_code"] == 0
    assert svc.lijst(d.id)[0].laatste_uitvoer == "leeg"
    assert svc.lijst(d.id)[0].laatst_gedraaid


def test_een_actie_in_een_gestopte_dienst_is_een_nette_409(db, monkeypatch):
    from fastapi import HTTPException

    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db, status="gestopt")
    svc = DienstActieService(db)
    monkeypatch.setattr(svc, "_runtime", lambda _c: _NepRuntime())
    a = svc.zet(d.id, label="Kijken", script="ls /", plek="in")

    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.draai(a.id))
    assert fout.value.status_code == 409
    assert "start hem eerst" in fout.value.detail


def test_een_actie_naast_de_dienst_draait_op_het_clusternetwerk(db, monkeypatch):
    """Zonder het netwerk van het cluster vindt het script `ih-cosmos` niet."""
    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    svc = DienstActieService(db)
    rt = _NepRuntime()
    monkeypatch.setattr(svc, "_runtime", lambda _c: rt)
    a = svc.zet(d.id, label="Leegmaken", script="python leeg.py", plek="naast",
                image="python:3.12-slim", gevaarlijk=True)

    uit = asyncio.run(svc.draai(a.id))

    image, cmd, netwerk, env = rt.wegwerp_aanroepen[0]
    assert image == "python:3.12-slim"
    assert cmd == ["sh", "-c", "python leeg.py"]
    assert netwerk == "labx-cluster-hive"
    assert env["DIENST_HOST"] == "ih-cosmos"
    assert env["DIENST_POORT"] == "8081"
    assert uit["exit_code"] == 0


def test_een_actie_naast_de_dienst_mag_ook_als_hij_stilstaat(db, monkeypatch):
    """Juist dan: een leeggemaakt volume van een gestopte dienst is zinnig."""
    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db, status="gestopt")
    svc = DienstActieService(db)
    rt = _NepRuntime()
    monkeypatch.setattr(svc, "_runtime", lambda _c: rt)
    a = svc.zet(d.id, label="Leegmaken", script="echo x", plek="naast")

    asyncio.run(svc.draai(a.id))
    assert rt.wegwerp_aanroepen


def test_zonder_image_valt_een_naast_actie_terug_op_het_standaardimage(db, monkeypatch):
    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    svc = DienstActieService(db)
    rt = _NepRuntime()
    monkeypatch.setattr(svc, "_runtime", lambda _c: rt)
    a = svc.zet(d.id, label="X", script="echo x", plek="naast")

    asyncio.run(svc.draai(a.id))
    assert rt.wegwerp_aanroepen[0][0] == "python:3.12-slim"


def test_de_bewaarde_uitvoer_blijft_begrensd(db, monkeypatch):
    from services.lab.dienst_acties import MAX_BEWAARDE_UITVOER, DienstActieService

    _, d = _dienst(db)
    svc = DienstActieService(db)

    class _Veel(_NepRuntime):
        async def exec(self, *a, **k):
            return {"exit_code": 0, "output": "x" * 50_000, "truncated": True}

    monkeypatch.setattr(svc, "_runtime", lambda _c: _Veel())
    a = svc.zet(d.id, label="X", script="echo x", plek="in")
    asyncio.run(svc.draai(a.id))

    assert len(svc.lijst(d.id)[0].laatste_uitvoer) == MAX_BEWAARDE_UITVOER


# ── de chat ──────────────────────────────────────────────────────────────────

def test_de_chat_voert_uit_wat_het_model_schrijft(db, monkeypatch):
    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    svc = DienstActieService(db)
    rt = _NepRuntime()
    monkeypatch.setattr(svc, "_runtime", lambda _c: rt)

    async def nep(_prompt):
        return ('{"antwoord": "Ik maak hem leeg.", "script": "python leeg.py", '
                '"plek": "naast", "image": "python:3.12-slim", "label": "Leegmaken", '
                '"gevaarlijk": true}')

    monkeypatch.setattr(svc, "_vraag", nep)
    uit = asyncio.run(svc.chat(d.id, "maak cosmos leeg"))

    assert uit["uitvoer"] == "3 databases weg"
    assert uit["exit_code"] == 0
    assert uit["label"] == "Leegmaken"
    assert rt.wegwerp_aanroepen


def test_een_vraag_om_uitleg_voert_niets_uit(db, monkeypatch):
    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    svc = DienstActieService(db)
    rt = _NepRuntime()
    monkeypatch.setattr(svc, "_runtime", lambda _c: rt)

    async def nep(_prompt):
        return '{"antwoord": "Hij luistert op 8081.", "script": ""}'

    monkeypatch.setattr(svc, "_vraag", nep)
    uit = asyncio.run(svc.chat(d.id, "op welke poort luistert hij?"))

    assert uit["script"] == ""
    assert uit["exit_code"] is None
    assert not rt.exec_aanroepen and not rt.wegwerp_aanroepen


def test_een_lege_vraag_wordt_geweigerd(db):
    from fastapi import HTTPException

    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    with pytest.raises(HTTPException) as fout:
        asyncio.run(DienstActieService(db).chat(d.id, "   "))
    assert fout.value.status_code == 400


def test_een_chatantwoord_is_te_bewaren_als_knop(db, monkeypatch):
    """De weg van 'dat werkte' naar 'dat wil ik vaker' moet één klik zijn."""
    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    svc = DienstActieService(db)
    a = svc.zet(d.id, label="Leegmaken", script="python leeg.py", plek="naast",
                image="python:3.12-slim", gevaarlijk=True, bron="chat")

    assert svc.dict(a)["bron"] == "chat"
    assert svc.dict(a)["image"] == "python:3.12-slim"
