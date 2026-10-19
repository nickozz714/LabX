"""Wat er nooit naar een klant mag.

De lezer is extern. Hij kent dit netwerk niet en hoort het niet te leren
kennen uit een mail. Geen interne ticketsleutels, geen namen van wat hier
draait, geen labs, branches of servernamen.

Waarom dit een CONTROLE is en niet alleen een regel in een prompt: een regel
is een verzoek, en een model houdt zich er meestal aan. Eén ticketsleutel in
een mail naar een klant is er één te veel, en je ziet het pas als het
verstuurd is.

Wat hier vastligt:

- De vaste namen worden gevonden, ook midden in een zin en ongeacht
  hoofdletters.
- De namen uit DEZE installatie worden gevonden: borden, clusters, diensten,
  labs en branches veranderen, en een vaste lijst zou de nieuwe naam missen.
- Een gewone zin over het product blijft schoon. Een controle die overal
  aanslaat wordt weggeklikt.
- De controle laat nooit het werk stilvallen: valt het ophalen van namen om,
  dan blijft de vaste laag staan.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    from models import board, cluster, lab  # noqa: F401

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    nu = "2026-10-19T15:00:00+00:00"
    s.add(board.Board(name="IntelligentHive", key_prefix="IH", columns=[],
                      created_at=nu, updated_at=nu))
    s.add(cluster.Cluster(naam="IntelligentHive", netwerk="labx-cluster-ih",
                          created_at=nu, updated_at=nu))
    s.commit()
    c = s.query(cluster.Cluster).one()
    s.add(cluster.ClusterDienst(cluster_id=c.id, naam="ih-cosmos", image="x",
                                poorten=[], poort_map={}, omgeving={},
                                created_at=nu, updated_at=nu))
    s.add(lab.Lab(id="l1", name="IntelligentHive · proefbouw", status="running",
                  image="x", volume_name="v", cluster_id=c.id,
                  branch="feature/zoek-filter", created_at=nu, updated_at=nu))
    s.commit()
    try:
        yield s
    finally:
        s.close()


# ── vaste namen ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tekst", [
    "We hebben dit in Nectar vastgelegd.",
    "Zie de notitie in hivemind.",
    "Dit draait in LabX.",
    "Afgestemd met Beeminds.",
    "nectar bevat de achtergrond",
])
def test_een_interne_productnaam_wordt_gevonden(tekst):
    from services.boards import klantcommunicatie

    uit = klantcommunicatie.controleer(tekst)
    assert not uit["schoon"], tekst


@pytest.mark.parametrize("tekst", [
    "De server draait op 192.168.2.15:8642.",
    "Open http://localhost:18004 om het te bekijken.",
    "Staat in /workspace/Intelligenthive/components/api.",
    "Het zit op feature/zoek-filter.",
    "Kijk op build.internal voor de logs.",
])
def test_een_adres_pad_of_branch_wordt_gevonden(tekst):
    from services.boards import klantcommunicatie

    uit = klantcommunicatie.controleer(tekst)
    assert not uit["schoon"], tekst


# ── namen uit deze installatie ───────────────────────────────────────────────

def test_een_ticketsleutel_van_dit_bord_wordt_gevonden(db):
    from services.boards import klantcommunicatie

    uit = klantcommunicatie.controleer("Dit is opgelost, zie IH-4 voor details.", db)
    assert not uit["schoon"]
    soorten = {b["soort"] for b in uit["bevindingen"]}
    assert "ticketsleutel" in soorten


def test_namen_van_cluster_dienst_lab_en_branch_worden_gevonden(db):
    from services.boards import klantcommunicatie

    tekst = ("We hebben het getest op ih-cosmos in het lab "
             "IntelligentHive · proefbouw.")
    uit = klantcommunicatie.controleer(tekst, db)
    gevonden = {b["gevonden"].lower() for b in uit["bevindingen"]}
    assert "ih-cosmos" in gevonden


def test_de_bevinding_zegt_waar_het_stond(db):
    from services.boards import klantcommunicatie

    uit = klantcommunicatie.controleer("Alles klaar. Details staan in IH-4.", db)
    b = uit["bevindingen"][0]
    assert b["gevonden"].upper() == "IH-4"
    assert "IH-4" in b["waar"]


# ── wat er wél doorheen mag ──────────────────────────────────────────────────

@pytest.mark.parametrize("tekst", [
    "Vanaf volgende week kunt u meerdere items tegelijk valideren en publiceren.",
    "Dimensies uit een ander dataproduct zijn nu te hergebruiken zonder kopie.",
    "Rapporten over grote tabellen laden merkbaar sneller.",
    "Was er al een transformatie, dan wordt die stap overgeslagen.",
])
def test_een_gewone_zin_over_het_product_blijft_schoon(db, tekst):
    from services.boards import klantcommunicatie

    uit = klantcommunicatie.controleer(tekst, db)
    assert uit["schoon"], uit["bevindingen"]


def test_een_lege_tekst_is_schoon():
    from services.boards import klantcommunicatie

    assert klantcommunicatie.controleer("")["schoon"] is True
    assert klantcommunicatie.controleer(None)["schoon"] is True


def test_hetzelfde_woord_twee_keer_levert_een_bevinding(db):
    """Anders is de lijst langer dan de tekst."""
    from services.boards import klantcommunicatie

    uit = klantcommunicatie.controleer("Nectar hier, en Nectar daar.", db)
    assert len(uit["bevindingen"]) == 1


def test_een_kapotte_database_laat_de_vaste_laag_staan():
    """Een controle mag nooit de reden zijn dat er niets meer verstuurd kan
    worden -- maar hij mag ook niet stilletjes alles goedkeuren."""
    from services.boards import klantcommunicatie

    class _Stuk:
        def query(self, *a, **k):
            raise RuntimeError("database weg")

    uit = klantcommunicatie.controleer("Dit staat in Nectar.", _Stuk())
    assert not uit["schoon"]


def test_de_uitleg_noemt_wat_er_mis_is(db):
    from services.boards import klantcommunicatie

    uit = klantcommunicatie.controleer("Zie IH-4 en Nectar.", db)
    tekst = klantcommunicatie.uitleg(uit)
    assert "IH-4" in tekst and "Nectar" in tekst
    assert klantcommunicatie.uitleg({"schoon": True}) == ""


def test_een_korte_naam_slaat_niet_overal_op_aan():
    """Een cluster dat 'AI' heet mag niet elke tekst afkeuren."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    from models import board, cluster, lab  # noqa: F401
    from services.boards import klantcommunicatie

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    nu = "2026-10-19T15:00:00+00:00"
    s.add(cluster.Cluster(naam="AI", netwerk="n", created_at=nu, updated_at=nu))
    s.commit()

    uit = klantcommunicatie.controleer("De AI-functies zijn uitgebreid.", s)
    assert uit["schoon"]
    s.close()
