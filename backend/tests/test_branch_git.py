"""Git-werk aan een branch-lab: een titel wordt een branch, en je pusht zelf.

Een branch-lab IS een werkkopie van een echte branch. Tot nu toe kon je er
alles mee behalve het enige waar het om gaat: je werk eruit krijgen.

Wat hier vastligt en stil kan sneuvelen:

- **Titel en branchnaam lopen niet uiteen.** Het scherm toont vooraf wat de
  branch gaat heten; maakt de backend er iets anders van, dan push je naar
  een branch die je niet bedoelde en merk je dat pas achteraf.
- **De beschrijving blijft intern.** Hij komt niet in de branchnaam en niet
  in een commit.
- **Een `.env` staat standaard UIT.** Eén keer per ongeluk meesturen is één
  keer te veel: na een push is het geheim openbaar, ook na een revert.
- **Bestandsnamen gaan niet door de shell.** Ze komen uit een formulier en
  mogen spaties, aanhalingstekens en nieuwe regels bevatten.
- **De index wordt eerst leeggemaakt.** Anders lift mee wat er toevallig al
  gestaged stond in een commit waarvan jij dacht dat je hem samenstelde.
"""
from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# ── van titel naar branchnaam ────────────────────────────────────────────────

@pytest.mark.parametrize("titel,verwacht", [
    ("Zoekfilter op het overzicht", "feature/zoekfilter-op-het-overzicht"),
    ("Fix: 2 bugs!!", "feature/fix-2-bugs"),
    ("Café-naam ééns", "feature/cafe-naam-eens"),
    ("   ", ""),
    ("!!!", ""),
])
def test_een_titel_wordt_een_bruikbare_branchnaam(titel, verwacht):
    from services.lab.branch_naam import uit_titel

    assert uit_titel(titel) == verwacht


@pytest.mark.parametrize("naam", [
    "feature//x", "a..b", "/x", "x/", "x.lock", "met spatie", "x:y", "x~1",
    "x?", "", "a" * 300,
])
def test_wat_git_niet_accepteert_wordt_geweigerd(naam):
    from services.lab.branch_naam import geldig

    assert not geldig(naam)


def test_een_afgeleide_naam_is_altijd_geldig():
    """Anders is het scherm een belofte die de push niet waarmaakt."""
    from services.lab.branch_naam import geldig, uit_titel

    for titel in ["Hoofdletters EN Spaties", "a" * 300, "x/y/z", "...", "1.2.3",
                  "Ünïcödé", "tab\tenter\n", "lock"]:
        naam = uit_titel(titel)
        assert naam == "" or geldig(naam), (titel, naam)


# ── wat er standaard aan en uit staat ────────────────────────────────────────

@pytest.mark.parametrize("pad", [
    ".env", "components/api/.env", "components/app/.env.local",
    "certs/server.key", "deploy/id_rsa", ".npmrc", ".labx-start.log",
])
def test_een_geheim_of_labx_bestand_staat_standaard_uit(pad):
    from services.lab.branch_git import _is_gevoelig

    assert _is_gevoelig(pad)


@pytest.mark.parametrize("pad", [
    "src/app.ts", "README.md", "components/api/src/routes/index.ts",
    "environment.ts", "keyboard.tsx",
])
def test_gewoon_werk_staat_standaard_aan(pad):
    from services.lab.branch_git import _is_gevoelig

    assert not _is_gevoelig(pad)


# ── de status uitlezen ───────────────────────────────────────────────────────

class _NepLab:
    id = "lab-1"
    branch = "feature/zoek-filter"
    feature_titel = "Zoek filter"
    feature_beschrijving = "Intern: nog twijfel over de sortering"
    image = "node:22"
    volume_name = "labx_vol_1"
    cluster_id = 1


def test_de_status_wordt_in_een_keer_uitgelezen():
    from services.lab.branch_git import BranchGit

    ruw = ("feature/zoek-filter\x1e"
           "a1b2c3d eerste opzet (2 uur geleden)\x1e"
           "3 1\x1e"
           "ja\x1e"
           " M src/app.ts\x00?? nieuw bestand.txt\x00 D weg.ts\x00?? .env\x00")
    uit = BranchGit.__new__(BranchGit)._lees_status(ruw, _NepLab())

    assert uit["branch"] == "feature/zoek-filter"
    assert uit["laatste_commit"].startswith("a1b2c3d")
    assert (uit["vooruit"], uit["achter"]) == (3, 1)
    assert uit["op_remote"] is True
    assert uit["schoon"] is False
    assert uit["titel"] == "Zoek filter"
    assert uit["beschrijving"] == "Intern: nog twijfel over de sortering"

    op_pad = {b["pad"]: b for b in uit["bestanden"]}
    assert set(op_pad) == {"src/app.ts", "nieuw bestand.txt", "weg.ts", ".env"}
    assert op_pad["src/app.ts"]["soort"] == "gewijzigd"
    assert op_pad["nieuw bestand.txt"]["soort"] == "nieuw"
    assert op_pad["weg.ts"]["soort"] == "verwijderd"
    assert op_pad["src/app.ts"]["standaard_aan"] is True
    assert op_pad[".env"]["standaard_aan"] is False


def test_een_schone_werkkopie_levert_geen_bestanden_op():
    from services.lab.branch_git import BranchGit

    ruw = "main\x1eabc eerste\x1e0 0\x1eja\x1e"
    uit = BranchGit.__new__(BranchGit)._lees_status(ruw, _NepLab())

    assert uit["bestanden"] == []
    assert uit["schoon"] is True


def test_een_bestandsnaam_met_een_nieuwe_regel_blijft_heel():
    """Daarom -z en niet regel-voor-regel: een newline mag in een pad."""
    from services.lab.branch_git import BranchGit

    ruw = "main\x1eabc\x1e0 0\x1enee\x1e?? raar\nbestand.txt\x00 M app.ts\x00"
    uit = BranchGit.__new__(BranchGit)._lees_status(ruw, _NepLab())

    assert [b["pad"] for b in uit["bestanden"]] == ["raar\nbestand.txt", "app.ts"]


# ── committen en pushen ──────────────────────────────────────────────────────

class _NepRuntime:
    def __init__(self, uitslag=None):
        self.aanroepen = []
        self._uitslag = uitslag or {"exit_code": 0, "output": "gecommit\n-- push naar x",
                                    "truncated": False}

    async def run_wegwerp(self, *, image, cmd, netwerk=None, env=None, volume=None,
                          volumes=None, timeout=0, **_):
        self.aanroepen.append({"image": image, "cmd": cmd, "env": env,
                               "volume": volume, "netwerk": netwerk})
        return self._uitslag


def _dienst(db, *, repo_url="https://github.com/org/repo.git"):
    from models.cluster import Cluster
    from models.lab import Lab

    nu = "2026-10-19T14:00:00+00:00"
    c = Cluster(naam="Hive", netwerk="labx-cluster-hive", repo_url=repo_url,
                created_at=nu, updated_at=nu)
    db.add(c)
    db.commit()
    lab = Lab(id="lab-1", name="Hive · Zoek filter", status="running", image="node:22",
              volume_name="labx_vol_1", cluster_id=c.id, branch="feature/zoek-filter",
              feature_titel="Zoek filter", feature_beschrijving="intern gedoe",
              created_at=nu, updated_at=nu)
    db.add(lab)
    db.commit()
    return c, lab


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


def _git(db, rt):
    from services.lab.branch_git import BranchGit

    svc = BranchGit(db)

    async def nep(lab, c, script, *, env=None, lezen=False):
        from services.lab.clusters import ClusterService

        toegang = ClusterService(db).toegang_voor(c)
        return await rt.run_wegwerp(
            image=lab.image, cmd=["sh", "-c", toegang.aanloop + script],
            env={"DEST": "repo", **(env or {})}, volume=lab.volume_name,
            timeout=600)

    svc._git = nep
    return svc


def test_de_gekozen_paden_gaan_als_bestand_de_container_in(db):
    """Niet door de shell: een pad uit een formulier mag alles bevatten."""
    rt = _NepRuntime()
    _, lab = _dienst(db)
    svc = _git(db, rt)

    asyncio.run(svc.push(lab.id, bericht="Zoekfilter toegevoegd",
                         bestanden=["src/app.ts", "raar bestand.txt"]))

    env = rt.aanroepen[0]["env"]
    paden = base64.b64decode(env["PADEN_B64"]).decode().split("\x00")
    assert paden == ["src/app.ts", "raar bestand.txt"]
    script = rt.aanroepen[0]["cmd"][2]
    assert "--pathspec-from-file=/tmp/labx-paden" in script
    assert "--pathspec-file-nul" in script
    # De paden zelf staan NIET in het script.
    assert "raar bestand.txt" not in script


def test_de_index_wordt_eerst_leeggemaakt(db):
    rt = _NepRuntime()
    _, lab = _dienst(db)
    asyncio.run(_git(db, rt).push(lab.id, bericht="x", bestanden=["a.ts"]))

    script = rt.aanroepen[0]["cmd"][2]
    assert script.index("git reset -q") < script.index("git add")


def test_alles_meesturen_kan_ook(db):
    rt = _NepRuntime()
    _, lab = _dienst(db)
    asyncio.run(_git(db, rt).push(lab.id, bericht="x", alles=True))

    env = rt.aanroepen[0]["env"]
    assert env["ALLES"] == "1"
    assert "PADEN_B64" not in env


def test_zonder_bericht_maar_met_bestanden_is_een_nette_fout(db):
    from fastapi import HTTPException

    rt = _NepRuntime()
    _, lab = _dienst(db)
    with pytest.raises(HTTPException) as fout:
        asyncio.run(_git(db, rt).push(lab.id, bericht="  ", bestanden=["a.ts"]))
    assert fout.value.status_code == 400
    assert not rt.aanroepen


def test_alleen_pushen_zonder_commit_mag_wel(db):
    """'Even pushen wat er al lag' is een geldige wens."""
    rt = _NepRuntime()
    _, lab = _dienst(db)
    asyncio.run(_git(db, rt).push(lab.id, bericht=""))

    env = rt.aanroepen[0]["env"]
    assert "COMMIT_BERICHT" not in env
    assert "PADEN_B64" not in env and "ALLES" not in env


def test_zonder_repo_url_is_er_niets_om_naar_te_pushen(db):
    from fastapi import HTTPException

    rt = _NepRuntime()
    _, lab = _dienst(db, repo_url=None)
    with pytest.raises(HTTPException) as fout:
        asyncio.run(_git(db, rt).push(lab.id, bericht="x", alles=True))
    assert fout.value.status_code == 400
    assert "Instellingen" in fout.value.detail


def test_een_ongeldige_branchnaam_pusht_niet(db):
    from fastapi import HTTPException

    rt = _NepRuntime()
    _, lab = _dienst(db)
    lab.branch = "met spatie"
    db.commit()

    with pytest.raises(HTTPException) as fout:
        asyncio.run(_git(db, rt).push(lab.id, bericht="x", alles=True))
    assert fout.value.status_code == 400
    assert not rt.aanroepen


def test_de_push_gaat_naar_de_branch_van_het_lab(db):
    rt = _NepRuntime()
    _, lab = _dienst(db)
    uit = asyncio.run(_git(db, rt).push(lab.id, bericht="x", alles=True))

    assert rt.aanroepen[0]["env"]["TAK"] == "feature/zoek-filter"
    assert "HEAD:refs/heads/$TAK" in rt.aanroepen[0]["cmd"][2]
    assert uit["ok"] is True


def test_een_mislukte_push_komt_terug_als_uitslag_en_niet_als_crash(db):
    """De uitvoer van git is het antwoord; een stacktrace helpt niemand."""
    rt = _NepRuntime({"exit_code": 1, "output": "! [rejected] non-fast-forward",
                      "truncated": False})
    _, lab = _dienst(db)
    uit = asyncio.run(_git(db, rt).push(lab.id, bericht="x", alles=True))

    assert uit["ok"] is False
    assert "non-fast-forward" in uit["uitvoer"]


def test_het_lab_moet_bij_een_cluster_horen(db):
    from fastapi import HTTPException
    from models.lab import Lab
    from services.lab.branch_git import BranchGit

    nu = "2026-10-19T14:00:00+00:00"
    los = Lab(id="los", name="Los lab", status="running", image="x",
              volume_name="v", created_at=nu, updated_at=nu)
    db.add(los)
    db.commit()

    with pytest.raises(HTTPException) as fout:
        asyncio.run(BranchGit(db).status("los"))
    assert fout.value.status_code == 404


# ── aanmaken met titel en beschrijving ───────────────────────────────────────

def test_een_branch_lab_krijgt_zijn_naam_uit_de_titel(db, monkeypatch):
    from services.lab.clusters import ClusterService

    _, _ = _dienst(db)
    svc = ClusterService(db)
    c = db.query(type(svc.get_or_404(1))).first()

    gezien = {}

    async def nep_init(_c, p, *, branch):
        gezien["branch"] = branch

    async def nep_kloon(_c, p, *, branch, basis):
        gezien["branch"] = branch

    async def nep_create(**kw):
        from models.lab import Lab

        rij = Lab(id="nieuw", name=kw["name"], status="running",
                  image=kw.get("image") or "x", volume_name="v",
                  created_at="x", updated_at="x")
        db.add(rij)
        db.commit()
        return {"id": "nieuw"}

    monkeypatch.setattr(svc, "_kloon_branch", nep_kloon)
    monkeypatch.setattr(svc, "_init_branch", nep_init)
    monkeypatch.setattr(svc, "controleer_toegang",
                        lambda _c: _klaar({"gecontroleerd": False}))
    import services.lab.lab_service as ls
    monkeypatch.setattr(ls.LabService, "create", lambda self, **kw: nep_create(**kw))
    monkeypatch.setattr(ls, "_na_inrichten_starten", lambda *a, **k: None)

    uit = asyncio.run(svc.maak_branch_lab(
        c.id, titel="Zoekfilter op het overzicht",
        beschrijving="Intern: twijfel over de sortering"))

    from models.lab import Lab
    lab = db.get(Lab, "nieuw")
    assert uit["branch"] == "feature/zoekfilter-op-het-overzicht"
    assert lab.branch == "feature/zoekfilter-op-het-overzicht"
    assert lab.feature_titel == "Zoekfilter op het overzicht"
    assert lab.feature_beschrijving == "Intern: twijfel over de sortering"
    # De beschrijving zit NIET in de naam van de branch.
    assert "twijfel" not in lab.branch


def _klaar(waarde):
    async def _f():
        return waarde
    return _f()


def test_een_titel_waar_geen_branchnaam_van_te_maken_is_wordt_geweigerd(db):
    from fastapi import HTTPException
    from services.lab.clusters import ClusterService

    _dienst(db)
    svc = ClusterService(db)
    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.maak_branch_lab(1, titel="!!!"))
    assert fout.value.status_code == 400
    assert "titel" in fout.value.detail.lower()
