"""Zelf de branch bepalen in een branch-lab.

Een branch-lab kloont één keer en blijft daarna op die ene branch. Dat is
prima voor "bouw deze feature", maar niet voor waar het ook voor gebruikt
wordt: een lopende werkkopie waarin je andermans werk nakijkt. Dan wil je
wisselen, bijwerken, en soms alles weggooien wat je onderweg hebt
aangeraakt.

Wat hier vastligt, en waarom:

- **Wisselen met ongecommit werk wordt geweigerd.** `git checkout` sleept
  zulke wijzigingen mee naar de andere branch, en dan staat je halve werk
  ergens waar het niet hoort zonder dat het gemeld is.
- **Weggooien is pas weggooien als je het zegt**, en je ziet eerst wat er
  verdwijnt — inclusief nieuwe bestanden, want juist die vergeet je.
- **`Lab.branch` gaat mee.** De rest van LabX zoekt het lab van een ticket
  op via die branch; lopen ze uiteen, dan werkt de agent in een werkkopie die
  bij een ander ticket hoort.
- **Twee labs op dezelfde branch kan niet.** Die werken langs elkaar heen in
  dezelfde code.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    from models import cluster, lab, lab_host  # noqa: F401

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _opzet(db, *, branch="feature/a"):
    from models.cluster import Cluster
    from models.lab import Lab

    nu = "2026-10-20T11:00:00+00:00"
    c = Cluster(naam="Hive", netwerk="n", repo_url="https://github.com/org/repo.git",
                created_at=nu, updated_at=nu)
    db.add(c)
    db.commit()
    lab = Lab(id="l1", name="Hive · a", status="running", image="x", volume_name="v",
              cluster_id=c.id, branch=branch, created_at=nu, updated_at=nu)
    db.add(lab)
    db.commit()
    return c, lab


def _git(db, uitslag=None, status=None):
    from services.lab.branch_git import BranchGit

    svc = BranchGit(db)
    gezien = {}

    async def nep(lab, c, script, *, env=None, lezen=False):
        gezien["script"] = script
        gezien["env"] = env or {}
        return uitslag or {"exit_code": 0, "output": "nu op feature/b", "truncated": False}

    svc._git = nep
    if status is not None:
        async def nep_status(_lab_id):
            return status
        svc.status = nep_status
    return svc, gezien


def _schoon():
    return {"bestanden": [], "schoon": True, "branch": "feature/a"}


def _vuil(*paden):
    return {"bestanden": [{"pad": p} for p in paden], "schoon": False,
            "branch": "feature/a"}


# ── welke branches er zijn ───────────────────────────────────────────────────

def test_lokale_en_externe_branches_staan_in_een_lijst(db):
    from services.lab.branch_git import BranchGit

    _, lab = _opzet(db)
    ruw = ("feature/a\x1e"
           "feature/a\nmain\x1e"
           "origin/HEAD -> origin/main\norigin/main\norigin/feature/b")
    uit = BranchGit.__new__(BranchGit)._lees_branches(ruw, lab)

    namen = [b["naam"] for b in uit["branches"]]
    assert uit["huidig"] == "feature/a"
    assert namen == ["feature/a", "main", "feature/b"]
    # feature/b staat alleen op de remote — juist daar wil je naartoe kunnen.
    b = {x["naam"]: x for x in uit["branches"]}
    assert b["feature/b"]["lokaal"] is False and b["feature/b"]["op_remote"] is True
    assert b["feature/a"]["huidig"] is True
    # En de HEAD-verwijzing is geen branch.
    assert not any("->" in n for n in namen)


def test_de_verkorte_head_is_geen_branch(db):
    """`%(refname:short)` kort refs/remotes/origin/HEAD af tot kaal "origin".
    Dat zag er in de lijst uit als een branch met die naam, en dat is er
    geen — gemeten op een repo met 790 branches stond hij er netjes tussen."""
    from services.lab.branch_git import BranchGit

    _, lab = _opzet(db)
    ruw = "main\x1emain\x1eorigin\norigin/main\norigin/feature/b\nHEAD"
    uit = BranchGit.__new__(BranchGit)._lees_branches(ruw, lab)

    namen = [b["naam"] for b in uit["branches"]]
    assert "origin" not in namen
    assert "HEAD" not in namen
    assert namen == ["main", "feature/b"]


# ── wisselen ─────────────────────────────────────────────────────────────────

def test_wisselen_met_ongecommit_werk_wordt_geweigerd(db):
    from fastapi import HTTPException

    _, lab = _opzet(db)
    svc, gezien = _git(db, status=_vuil("src/app.ts", ".env"))

    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.checkout(lab.id, branch="feature/b"))

    assert fout.value.status_code == 409
    assert "src/app.ts" in fout.value.detail
    assert "weggooien" in fout.value.detail
    assert "script" not in gezien  # er is niets uitgevoerd
    db.refresh(lab)
    assert lab.branch == "feature/a"


def test_wisselen_met_weggooien_mag_wel(db):
    _, lab = _opzet(db)
    svc, gezien = _git(db, status=_vuil("src/app.ts"))

    uit = asyncio.run(svc.checkout(lab.id, branch="feature/b", weggooien=True))

    assert uit["ok"] is True
    assert gezien["env"]["WEGGOOIEN"] == "1"
    assert "reset --hard" in gezien["script"]
    db.refresh(lab)
    assert lab.branch == "feature/b"


def test_een_schone_werkkopie_wisselt_zonder_gedoe(db):
    _, lab = _opzet(db)
    svc, gezien = _git(db, status=_schoon())

    asyncio.run(svc.checkout(lab.id, branch="feature/b"))

    assert "WEGGOOIEN" not in gezien["env"]
    db.refresh(lab)
    assert lab.branch == "feature/b"


def test_de_branch_van_het_lab_gaat_mee(db):
    """Anders zoekt de rest van LabX het lab van een ticket op de oude branch
    en werkt de agent in de verkeerde werkkopie."""
    _, lab = _opzet(db)
    svc, _ = _git(db, status=_schoon())

    asyncio.run(svc.checkout(lab.id, branch="hotfix/iets"))

    db.refresh(lab)
    assert lab.branch == "hotfix/iets"


def test_een_mislukte_checkout_laat_de_branch_staan(db):
    """Half omgezet is erger dan niet omgezet: dan wijst LabX naar een branch
    waar de werkkopie niet op staat."""
    _, lab = _opzet(db)
    svc, _ = _git(db, uitslag={"exit_code": 1, "output": "error: pathspec niet gevonden"},
                  status=_schoon())

    uit = asyncio.run(svc.checkout(lab.id, branch="feature/b"))

    assert uit["ok"] is False
    db.refresh(lab)
    assert lab.branch == "feature/a"


def test_twee_labs_op_dezelfde_branch_kan_niet(db):
    from fastapi import HTTPException
    from models.lab import Lab

    c, lab = _opzet(db)
    nu = "2026-10-20T11:00:00+00:00"
    db.add(Lab(id="l2", name="Hive · b", status="running", image="x", volume_name="v",
               cluster_id=c.id, branch="feature/b", created_at=nu, updated_at=nu))
    db.commit()
    svc, _ = _git(db, status=_schoon())

    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.checkout(lab.id, branch="feature/b"))
    assert fout.value.status_code == 409
    assert "Hive · b" in fout.value.detail


def test_naar_de_branch_waar_je_al_op_staat_doet_niets(db):
    _, lab = _opzet(db)
    svc, gezien = _git(db)

    uit = asyncio.run(svc.checkout(lab.id, branch="feature/a"))
    assert uit["ok"] is True
    assert "script" not in gezien


def test_een_onmogelijke_branchnaam_wordt_geweigerd(db):
    from fastapi import HTTPException

    _, lab = _opzet(db)
    svc, _ = _git(db)
    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.checkout(lab.id, branch="met spatie"))
    assert fout.value.status_code == 400


# ── weggooien ────────────────────────────────────────────────────────────────

def test_weggooien_meldt_wat_er_weg_is(db):
    """Zodat het scherm kan laten zien wat je kwijtraakt, en achteraf wat je
    kwijt bent."""
    _, lab = _opzet(db)
    svc, gezien = _git(db, status=_vuil("src/app.ts", "nieuw.txt"))

    uit = asyncio.run(svc.weggooien(lab.id))

    assert uit["weggegooid"] == ["src/app.ts", "nieuw.txt"]
    assert "clean -fd" in gezien["script"]


def test_weggooien_pakt_ook_nieuwe_bestanden(db):
    """`git reset --hard` alleen laat untracked bestanden staan — juist die
    vergeet je, en juist die zijn niet terug te halen."""
    from services.lab.branch_git import _WEGGOOI_SCRIPT

    assert "reset --hard" in _WEGGOOI_SCRIPT
    assert "clean -fd" in _WEGGOOI_SCRIPT


# ── bijwerken ────────────────────────────────────────────────────────────────

def test_bijwerken_haalt_de_basis_op(db):
    """Een branch-lab kloont één keer en daarna nooit meer."""
    _, lab = _opzet(db)
    svc, gezien = _git(db)

    asyncio.run(svc.bijwerken(lab.id, basis="develop"))

    assert gezien["env"]["BASIS"] == "develop"
    assert "git fetch origin" in gezien["script"]
    assert "git merge" in gezien["script"]


def test_bijwerken_zonder_basis_neemt_je_eigen_branch(db):
    """Dit stond op `main`, en dat was de verkeerde kant op.

    Aan een feature-branch werkt meestal meer dan één mens. Gemeten op
    23-10-2026: een lab van drie dagen oud stond 2 commits vooruit en 65
    achter op zijn EIGEN branch, de push werd geweigerd, en bijwerken vanaf
    main gaf 41 conflicten plus een halve merge. Met de eigen branch waren
    het er nul -- dezelfde lijn, dezelfde geschiedenis.
    """
    _, lab = _opzet(db)
    svc, gezien = _git(db)

    asyncio.run(svc.bijwerken(lab.id))
    assert gezien["env"]["BASIS"] == lab.branch


def test_bijwerken_vanaf_main_kan_nog_steeds(db):
    """Soms wil je main er juist in. Dat moet een keuze zijn, niet wat er
    gebeurt als je niets kiest."""
    _, lab = _opzet(db)
    svc, gezien = _git(db)

    asyncio.run(svc.bijwerken(lab.id, basis="main"))
    assert gezien["env"]["BASIS"] == "main"


def test_een_lab_zonder_branch_valt_terug_op_main(db):
    """Dan is er geen eigen branch om vanaf bij te werken."""
    _, lab = _opzet(db)
    lab.branch = None
    svc, gezien = _git(db)

    asyncio.run(svc.bijwerken(lab.id))
    assert gezien["env"]["BASIS"] == "main"


def test_bijwerken_rebaset_niet():
    """Een rebase herschrijft commits die misschien al gepusht zijn. Dat is
    geen keuze die een knop voor je hoort te maken."""
    from services.lab.branch_git import _BIJWERK_SCRIPT

    assert "rebase" not in _BIJWERK_SCRIPT


# ── de applicatie los van het lab ────────────────────────────────────────────

def test_een_lab_kan_draaien_zonder_dat_de_applicatie_start(db):
    """Een lab wordt ook gebruikt om code te LEZEN. Dan heb je de container
    nodig en de devserver juist niet: die eet geheugen, houdt poorten bezet en
    bouwt opnieuw bij elk bestand dat je aanraakt."""
    from services.lab.clusters import ClusterService

    c, lab = _opzet(db)
    c.start_script = "npm run dev"
    db.commit()

    # Inrichten gebeurt wél -- zonder git en basisgereedschap kun je niets lezen.
    assert ClusterService._wat_starten(lab, c) == "alleen-inrichten"


def test_met_de_schakelaar_aan_start_de_applicatie_gewoon(db):
    from services.lab.clusters import ClusterService

    c, lab = _opzet(db)
    c.start_script = "npm run dev"
    lab.applicatie_starten = True
    db.commit()

    assert ClusterService._wat_starten(lab, c) == "applicatie"


def test_een_eigen_startscript_telt_ook_zonder_cluster_script(db):
    """Dat ontbrak: de voorwaarde keek alleen naar het cluster, dus een eigen
    script werd wel bewaard en nooit gestart."""
    from services.lab.clusters import ClusterService

    c, lab = _opzet(db)
    lab.setup_script = "bash .devcontainer/bootstrap.sh"
    lab.applicatie_starten = True
    db.commit()

    assert ClusterService._wat_starten(lab, c) == "applicatie"


def test_zonder_enig_startscript_valt_er_niets_te_starten(db):
    from services.lab.clusters import ClusterService

    c, lab = _opzet(db)
    lab.applicatie_starten = True
    db.commit()
    assert ClusterService._wat_starten(lab, c) == "niets"


def test_het_stopscript_laat_het_logbestand_staan():
    """Dat is het verslag van wat er draaide, en dat wil je juist kunnen
    teruglezen nádat je hem hebt stilgezet."""
    import inspect

    from services.lab.lab_service import LabService

    bron = inspect.getsource(LabService.stop_applicatie)
    assert "kill -TERM" in bron and "kill -KILL" in bron
    assert "START_PID" in bron
    # De log wordt NIET verwijderd (anders dan bij het starten).
    assert "START_LOG" not in bron.split("script = (")[1]


def test_een_nieuw_lab_start_zijn_applicatie_niet_vanzelf(db):
    """Een lab aanzetten en een applicatie starten zijn twee dingen, en het
    tweede is het dure. Gemeten waarom dit omging: drie branch-labs die
    tegelijk `yarn install` begonnen pasten niet in het geheugen, en twee
    werden afgeschoten terwijl niemand om die installatie had gevraagd."""
    _, lab = _opzet(db)
    assert bool(lab.applicatie_starten) is False


def test_een_lab_zonder_applicatie_wordt_wel_ingericht(db):
    """Zonder git en basisgereedschap kun je ook geen code lezen."""
    from services.lab.clusters import ClusterService

    c, lab = _opzet(db)
    c.start_script = "npm run dev"
    db.commit()

    assert ClusterService._wat_starten(lab, c) == "alleen-inrichten"


def test_de_proefbouw_zet_de_applicatie_wel_aan(db):
    """Het ene geval waarin starten vanzelf moet: de proefbouw bestaat om uit
    te zoeken of dat startscript werkt."""
    from services.lab.proefbouw import _zet_app_aan

    _, lab = _opzet(db)
    assert bool(lab.applicatie_starten) is False

    _zet_app_aan(db, lab.id)

    db.refresh(lab)
    assert bool(lab.applicatie_starten) is True
