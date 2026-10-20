"""Bestanden van buiten naar een cluster brengen, met een opdracht erbij.

Een vaste dienst begint leeg, en de bron waaruit hij gevuld hoort te worden
is niet altijd bereikbaar — een firewall, een omgeving waar je niet bij kunt.
De export staat dan gewoon op je schijf.

Wat hier vastligt en stil kan sneuvelen:

- **Een archief komt van buiten.** `../../etc/passwd`, een absoluut pad, een
  symlink: alle drie horen geweigerd te worden, en weigeren komt vóór
  opschonen. Andersom is de fout die hier eerder in zat — `lstrip("./")`
  maakte van `/etc/passwd` een keurig `etc/passwd`.
- **De inhoud gaat nooit naar het model.** Alleen paden en omvang.
- **De bundel hangt alleen-lezen.** Een script dat een dump inleest hoort die
  dump niet te kunnen veranderen.
- **Zonder opdracht gebeurt er niets.** "Draai maar iets tegen deze dienst"
  is geen opdracht.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import tarfile
import zipfile
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
        cluster_bundel, cluster_dienst_actie, cluster_env_file, cluster_secret,
        guard_audit, guard_rule, lab, lab_extra, lab_host, lab_secret,
        lab_worker, mcp_server, message, model_runner, notification, plan,
        schedule, secret, skill, skill_tool, thread, time_entry, tool, voice,
        workflow,
    )

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


class _NepRuntime:
    def __init__(self):
        self.gevuld = []
        self.gedraaid = []
        self.verwijderd = []

    async def vul_volume(self, volume, bron_map, *, timeout=0):
        # Vastleggen WAT er het volume in zou gaan, zodat een test kan zien
        # dat een geweigerd pad er ook echt niet in beland is.
        bestanden = []
        for wortel, _mappen, namen in os.walk(bron_map):
            for n in namen:
                bestanden.append(os.path.relpath(os.path.join(wortel, n), bron_map))
        self.gevuld.append((volume, sorted(bestanden)))

    async def run_wegwerp(self, *, image, cmd, netwerk=None, env=None,
                          volumes=None, timeout=0, **_):
        self.gedraaid.append({"image": image, "cmd": cmd, "netwerk": netwerk,
                              "env": env, "volumes": volumes})
        return {"exit_code": 0, "output": "13603 documenten", "truncated": False}

    async def remove_volume(self, naam):
        self.verwijderd.append(naam)


def _cluster_en_dienst(db):
    from models.cluster import Cluster, ClusterDienst

    nu = "2026-10-19T12:00:00+00:00"
    c = Cluster(naam="Hive", netwerk="labx-cluster-hive", created_at=nu, updated_at=nu)
    db.add(c)
    db.commit()
    d = ClusterDienst(cluster_id=c.id, naam="ih-cosmos", image="cosmos:latest",
                      poorten=[8081], poort_map={"8081": 18000}, omgeving={},
                      status="draait", container_id="abc", created_at=nu, updated_at=nu)
    db.add(d)
    db.commit()
    return c, d


def _zip(tmp_path, leden: dict) -> str:
    pad = str(tmp_path / "bundel.zip")
    with zipfile.ZipFile(pad, "w") as z:
        for naam, inhoud in leden.items():
            z.writestr(naam, inhoud)
    return pad


def _svc(db, rt):
    from services.lab.bundels import BundelService

    svc = BundelService(db)
    svc._runtime = lambda _c: rt
    return svc


# ── paden van buiten ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("pad", [
    "../../etc/passwd", "/etc/passwd", "~/.ssh/id_rsa", "a/../../../x",
    "..\\\\..\\\\windows\\\\system32", "",
])
def test_een_pad_dat_buiten_de_bundel_wijst_wordt_geweigerd(pad):
    from services.lab.bundels import veilig_pad

    assert veilig_pad(pad) is None


@pytest.mark.parametrize("pad,verwacht", [
    ("./controlcenter/data/models.ndjson", "controlcenter/data/models.ndjson"),
    ("manifest.json", "manifest.json"),
    ("a//b/./c.txt", "a/b/c.txt"),
])
def test_een_gewoon_pad_blijft_gewoon(pad, verwacht):
    from services.lab.bundels import veilig_pad

    assert veilig_pad(pad) == verwacht


def test_een_archief_met_een_ontsnappend_pad_levert_dat_bestand_niet_op(db, tmp_path):
    """Weigeren, niet opschonen: /etc/passwd mag geen etc/passwd worden."""
    rt = _NepRuntime()
    pad = _zip(tmp_path, {"manifest.json": "{}", "../../etc/passwd": "root:x:0:0"})
    c, _ = _cluster_en_dienst(db)

    b = asyncio.run(_svc(db, rt).zet(c.id, bestandsnaam="bundel.zip", pad=pad))

    paden = [x["pad"] for x in b.boom]
    assert paden == ["manifest.json"]
    assert rt.gevuld[0][1] == ["manifest.json"]


def test_een_symlink_in_een_tar_komt_er_niet_in(db, tmp_path):
    from services.lab.bundels import BundelService  # noqa: F401

    pad = str(tmp_path / "bundel.tar.gz")
    with tarfile.open(pad, "w:gz") as t:
        data = b"{}"
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("sleutel")
        link.type = tarfile.SYMTYPE
        link.linkname = "/root/.ssh/id_rsa"
        t.addfile(link)

    rt = _NepRuntime()
    c, _ = _cluster_en_dienst(db)
    b = asyncio.run(_svc(db, rt).zet(c.id, bestandsnaam="bundel.tar.gz", pad=pad))

    assert [x["pad"] for x in b.boom] == ["manifest.json"]


def test_een_archief_zonder_bruikbare_inhoud_wordt_geweigerd(db, tmp_path):
    from fastapi import HTTPException

    rt = _NepRuntime()
    pad = _zip(tmp_path, {"../buiten.txt": "x"})
    c, _ = _cluster_en_dienst(db)

    with pytest.raises(HTTPException) as fout:
        asyncio.run(_svc(db, rt).zet(c.id, bestandsnaam="bundel.zip", pad=pad))
    assert fout.value.status_code == 400


def test_een_bestandssoort_die_labx_niet_uitpakt_wordt_geweigerd(db, tmp_path):
    from fastapi import HTTPException

    pad = str(tmp_path / "dump.ndjson")
    Path(pad).write_text("{}")
    c, _ = _cluster_en_dienst(db)

    with pytest.raises(HTTPException) as fout:
        asyncio.run(_svc(db, _NepRuntime()).zet(c.id, bestandsnaam="dump.ndjson", pad=pad))
    assert ".zip" in fout.value.detail


def test_te_veel_uitgepakte_bytes_wordt_geweigerd(db, tmp_path, monkeypatch):
    """Een klein archief met een enorme inhoud zet anders de schijf vol."""
    from fastapi import HTTPException

    import services.lab.bundels as mod

    monkeypatch.setattr(mod, "MAX_UITGEPAKT_BYTES", 100)
    pad = _zip(tmp_path, {"groot.txt": "x" * 500})
    c, _ = _cluster_en_dienst(db)

    with pytest.raises(HTTPException):
        asyncio.run(_svc(db, _NepRuntime()).zet(c.id, bestandsnaam="bundel.zip", pad=pad))


def test_een_mislukte_vulling_laat_geen_spookbundel_achter(db, tmp_path):
    """Een rij zonder volume is erger dan geen rij: hij belooft iets dat er
    niet is, en elke poging hem te gebruiken faalt onbegrijpelijk."""
    from services.lab.bundels import BundelService
    from models.cluster_bundel import ClusterBundel

    class _Stuk(_NepRuntime):
        async def vul_volume(self, *a, **k):
            raise RuntimeError("daemon weg")

    c, _ = _cluster_en_dienst(db)
    pad = _zip(tmp_path, {"manifest.json": "{}"})
    svc = _svc(db, _Stuk())

    with pytest.raises(RuntimeError):
        asyncio.run(svc.zet(c.id, bestandsnaam="bundel.zip", pad=pad))
    assert db.query(ClusterBundel).count() == 0


# ── wat het model te zien krijgt ─────────────────────────────────────────────

def test_het_model_ziet_de_paden_maar_nooit_de_inhoud(db, tmp_path):
    from services.lab.bundels import BundelService

    rt = _NepRuntime()
    pad = _zip(tmp_path, {
        "manifest.json": '{"source": "cosmosmxfzpsgk7ltc2.documents.azure.com"}',
        "controlcenter/data/models.ndjson": '{"id": "geheim-document-42"}',
    })
    c, d = _cluster_en_dienst(db)
    svc = _svc(db, rt)
    b = asyncio.run(svc.zet(c.id, bestandsnaam="bundel.zip", pad=pad))

    prompt = BundelService(db)._prompt(b, d, "zet deze dump in de emulator")

    assert "controlcenter/data/models.ndjson" in prompt
    assert "geheim-document-42" not in prompt
    assert "cosmosmxfzpsgk7ltc2" not in prompt
    assert "LEES DIE DAN IN JE SCRIPT" in prompt
    assert "zet deze dump in de emulator" in prompt
    assert "ih-cosmos" in prompt


def test_de_prompt_rendert_zonder_ontbrekende_placeholder(db, tmp_path):
    import re

    from services.lab.bundels import BundelService

    pad = _zip(tmp_path, {"manifest.json": "{}"})
    c, d = _cluster_en_dienst(db)
    b = asyncio.run(_svc(db, _NepRuntime()).zet(c.id, bestandsnaam="bundel.zip", pad=pad))

    prompt = BundelService(db)._prompt(b, d, "doe iets")
    assert not re.search(r"\{[a-z_]+\}", prompt), re.findall(r"\{[a-z_]+\}", prompt)


# ── toepassen ────────────────────────────────────────────────────────────────

def _bundel(db, tmp_path, rt, instructie=""):
    c, d = _cluster_en_dienst(db)
    pad = _zip(tmp_path, {"manifest.json": "{}"})
    svc = _svc(db, rt)
    b = asyncio.run(svc.zet(c.id, bestandsnaam="bundel.zip", pad=pad,
                            instructie=instructie))
    return svc, b, d


def test_de_bundel_hangt_alleen_lezen_op_een_vaste_plek(db, tmp_path):
    from models.cluster_bundel import KOPPELPUNT

    rt = _NepRuntime()
    svc, b, d = _bundel(db, tmp_path, rt)

    asyncio.run(svc.toepassen(b.id, dienst_id=d.id, script="echo hoi"))

    run = rt.gedraaid[0]
    assert run["volumes"] == {b.volume: KOPPELPUNT}
    assert run["netwerk"] == "labx-cluster-hive"
    assert run["env"]["DIENST_HOST"] == "ih-cosmos"
    assert run["env"]["BUNDEL"] == KOPPELPUNT


def test_een_meegegeven_script_gaat_ongewijzigd_en_zonder_model(db, tmp_path, monkeypatch):
    """Wat één keer goed ging wil je ongewijzigd kunnen herhalen."""
    from services.lab.dienst_acties import DienstActieService

    def nooit(*_a, **_k):
        raise AssertionError("het model hoort hier niet gevraagd te worden")

    monkeypatch.setattr(DienstActieService, "_vraag", nooit)
    rt = _NepRuntime()
    svc, b, d = _bundel(db, tmp_path, rt)

    uit = asyncio.run(svc.toepassen(b.id, dienst_id=d.id, script="python laad.py",
                                    image="python:3.12-slim"))

    assert rt.gedraaid[0]["cmd"] == ["sh", "-c", "python laad.py"]
    assert uit["exit_code"] == 0
    assert uit["uitvoer"] == "13603 documenten"


def test_zonder_opdracht_en_zonder_script_gebeurt_er_niets(db, tmp_path):
    from fastapi import HTTPException

    rt = _NepRuntime()
    svc, b, d = _bundel(db, tmp_path, rt)

    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.toepassen(b.id, dienst_id=d.id))
    assert fout.value.status_code == 400
    assert not rt.gedraaid


def test_de_opdracht_van_de_bundel_telt_als_er_geen_losse_meekomt(db, tmp_path, monkeypatch):
    from services.lab.dienst_acties import DienstActieService

    gezien = {}

    async def nep(_self, prompt):
        gezien["prompt"] = prompt
        return '{"script": "echo ja", "image": "python:3.12-slim"}'

    monkeypatch.setattr(DienstActieService, "_vraag", nep)
    rt = _NepRuntime()
    svc, b, d = _bundel(db, tmp_path, rt, instructie="zet deze dump in de emulator")

    asyncio.run(svc.toepassen(b.id, dienst_id=d.id))

    assert "zet deze dump in de emulator" in gezien["prompt"]
    assert rt.gedraaid[0]["cmd"] == ["sh", "-c", "echo ja"]


def test_een_dienst_van_een_ander_cluster_kan_niet(db, tmp_path):
    from fastapi import HTTPException

    from models.cluster import Cluster, ClusterDienst

    rt = _NepRuntime()
    svc, b, _ = _bundel(db, tmp_path, rt)
    nu = "2026-10-19T12:00:00+00:00"
    ander = Cluster(naam="Ander", netwerk="labx-cluster-ander", created_at=nu, updated_at=nu)
    db.add(ander)
    db.commit()
    vreemd = ClusterDienst(cluster_id=ander.id, naam="x", image="x:1", poorten=[],
                           poort_map={}, omgeving={}, status="draait",
                           created_at=nu, updated_at=nu)
    db.add(vreemd)
    db.commit()

    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.toepassen(b.id, dienst_id=vreemd.id, script="echo x"))
    assert fout.value.status_code == 404


def test_weggooien_ruimt_ook_het_volume_op(db, tmp_path):
    from models.cluster_bundel import ClusterBundel

    rt = _NepRuntime()
    svc, b, _ = _bundel(db, tmp_path, rt)
    volume = b.volume

    asyncio.run(svc.verwijder(b.id))

    assert rt.verwijderd == [volume]
    assert db.query(ClusterBundel).count() == 0


def test_de_uitslag_blijft_bij_de_bundel_staan(db, tmp_path):
    """Een week later wil je kunnen zien dat het vorige keer goed ging."""
    rt = _NepRuntime()
    svc, b, d = _bundel(db, tmp_path, rt)

    asyncio.run(svc.toepassen(b.id, dienst_id=d.id, script="echo hoi"))

    uit = svc.dict(svc._bundel(b.id))
    assert uit["laatste_exitcode"] == 0
    assert "13603 documenten" in uit["laatste_uitvoer"]
    assert uit["laatst_toegepast"]
