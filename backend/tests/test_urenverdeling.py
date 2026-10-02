"""Tijd verdelen over projecten die tegelijk liepen.

De aanleiding: twintig uur aan project X en vijfentwintig aan project Y is
vijfenveertig uur, maar als vijftien daarvan parallel liepen is er maar dertig
uur klok voorbij. Op de echte data van 2026-10-02 bleek 34 van de 123,6 gemeten
uren overlap te zijn — dat is wat je te veel zou schrijven als je de
bruto-getallen per klant overneemt.

Deze tests leggen de eigenschap vast waar alles op staat of valt: de som van de
verdeelde tijd is NOOIT meer dan de werkelijk verstreken klok.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.time.verdeling import intervallen_van, klok, verdeel  # noqa: E402

T0 = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)


def u(uren: float) -> datetime:
    return T0 + timedelta(hours=uren)


def test_het_voorbeeld_uit_de_vraag():
    """20 uur X, 25 uur Y, 15 uur daarvan parallel."""
    intervallen = [(u(0), u(20), "X"), (u(5), u(30), "Y")]
    uit = verdeel(intervallen)

    assert uit["X"]["bruto"] == 20 * 60
    assert uit["Y"]["bruto"] == 25 * 60
    # X liep 5 uur alleen + 15 uur gedeeld door twee = 12,5 uur.
    assert uit["X"]["verdeeld"] == 12.5 * 60
    # Y liep 10 uur alleen + 7,5 = 17,5 uur.
    assert uit["Y"]["verdeeld"] == 17.5 * 60
    assert uit["X"]["overlap"] == 7.5 * 60
    assert uit["Y"]["overlap"] == 7.5 * 60


def test_de_som_is_nooit_meer_dan_de_klok():
    """De eigenschap die dit alles rechtvaardigt."""
    gevallen = [
        [(u(0), u(2), "A")],                                        # één project
        [(u(0), u(2), "A"), (u(3), u(5), "B")],                     # na elkaar
        [(u(0), u(4), "A"), (u(1), u(2), "B"), (u(1), u(2), "C")],  # drie tegelijk
        [(u(0), u(1), "A"), (u(0), u(1), "B"), (u(0), u(1), "C"),
         (u(0), u(1), "D")],                                        # volledig gelijk
        [(u(0), u(10), "A"), (u(2), u(3), "B"), (u(2.5), u(4), "C"),
         (u(9), u(12), "D")],                                       # rommelig
    ]
    for intervallen in gevallen:
        uit = verdeel(intervallen)
        som = sum(v["verdeeld"] for v in uit.values())
        echt = klok(intervallen)
        assert abs(som - echt) < 0.2, f"{som} != {echt} bij {intervallen}"


def test_alleen_lopend_project_houdt_zijn_volle_tijd():
    uit = verdeel([(u(0), u(3), "Solo")])
    assert uit["Solo"]["verdeeld"] == uit["Solo"]["bruto"] == 3 * 60
    assert uit["Solo"]["overlap"] == 0


def test_twee_runs_van_hetzelfde_project_maken_het_niet_zwaarder():
    """Twee tegelijk lopende runs van ÉÉN project delen de klok niet op.

    Er gaat nog steeds maar één klok. Zou je per RUN verdelen in plaats van per
    project, dan zou een klant die toevallig twee werkers tegelijk gebruikt een
    groter deel van de gedeelde tijd krijgen — en dat is geen eerlijke grond om
    meer te schrijven.
    """
    intervallen = [(u(0), u(2), "X"), (u(0), u(2), "X"), (u(0), u(2), "Y")]
    uit = verdeel(intervallen)
    assert uit["X"]["verdeeld"] == uit["Y"]["verdeeld"] == 1 * 60
    # Bruto telt de runs wel los: X heeft twee regels van twee uur.
    assert uit["X"]["bruto"] == 4 * 60


def test_regels_zonder_tijdstippen_tellen_niet_als_overlap():
    """Een handmatige regel zonder begin en eind kan niet overlappen: we weten
    niet wannéér hij viel. Hem meenemen zou overlap verzinnen."""
    regels = [
        {"started_at": u(0).isoformat(), "ended_at": u(2).isoformat(), "project": "A"},
        {"started_at": None, "ended_at": None, "project": "B"},
        {"started_at": u(1).isoformat(), "ended_at": None, "project": "C"},
    ]
    intervallen = intervallen_van(regels)
    assert len(intervallen) == 1
    assert intervallen[0][2] == "A"


def test_een_omgekeerd_of_leeg_interval_wordt_genegeerd():
    regels = [
        {"started_at": u(5).isoformat(), "ended_at": u(2).isoformat(), "project": "fout"},
        {"started_at": u(1).isoformat(), "ended_at": u(1).isoformat(), "project": "leeg"},
    ]
    assert intervallen_van(regels) == []
    assert verdeel([]) == {}
    assert klok([]) == 0.0


def test_klok_telt_aaneengesloten_blokken_als_een():
    # 0-2 en 2-4 sluiten op elkaar aan: samen vier uur, niet twee keer twee
    # met een gat ertussen.
    assert klok([(u(0), u(2), "A"), (u(2), u(4), "B")]) == 4 * 60
    # Met een gat van een uur ertussen: drie uur klok, niet vier.
    assert klok([(u(0), u(1), "A"), (u(2), u(4), "B")]) == 3 * 60
