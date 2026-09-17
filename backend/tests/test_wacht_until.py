"""Een run die eindigt met een lopende `board__wait_until` is niet klaar.

In de nacht van 16 op 17 september ging dit vier keer achter elkaar mis. De
agent parkeerde het ticket netjes ("3 export-pipelines draaien, 60 notebook-runs")
en rondde zijn beurt af zoals hem gevraagd was. Twintig seconden later
verplaatste de afloop-hook van LabX het ticket naar Klaar — want de RUN was
"completed" — waarna de planning het item afvinkte als "buitenom afgerond" en
de hervatting om 05:25 liet vallen. Het zilver en het goud zijn nooit gedraaid.

Twee toetsen, want er zaten twee gaten:
  1. de afloop-hook mag een geparkeerd ticket niet naar de klaar-kolom duwen;
  2. de planning mag een lopende wachttijd niet wegstrepen op de kolom.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


class _NepQuery:
    def __init__(self, item):
        self._item = item

    def filter(self, *_a, **_k):
        return self

    def first(self):
        return self._item


class _NepDb:
    def __init__(self, item):
        self._item = item

    def query(self, *_a, **_k):
        return _NepQuery(self._item)


def test_wachtend_item_telt_niet_als_klaar():
    from services.boards.agent_work import _wacht_nog

    item = SimpleNamespace(state="waiting", resume_at=_iso(minutes=20), run_id="r1")
    assert _wacht_nog(_NepDb(item), SimpleNamespace(id="r1")) is True


def test_verlopen_wachttijd_telt_wel_als_klaar():
    """Is de tijd om, dan hoort de gewone afhandeling weer te gelden — anders
    blijft een ticket dat niemand meer oppakt voorgoed in de wachtstand."""
    from services.boards.agent_work import _wacht_nog

    item = SimpleNamespace(state="waiting", resume_at=_iso(minutes=-5), run_id="r1")
    assert _wacht_nog(_NepDb(item), SimpleNamespace(id="r1")) is False


def test_item_zonder_wachttijd_telt_als_klaar():
    from services.boards.agent_work import _wacht_nog

    item = SimpleNamespace(state="running", resume_at=None, run_id="r1")
    assert _wacht_nog(_NepDb(item), SimpleNamespace(id="r1")) is False
    assert _wacht_nog(_NepDb(None), SimpleNamespace(id="r1")) is False
    assert _wacht_nog(_NepDb(item), SimpleNamespace(id="")) is False
