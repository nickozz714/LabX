"""Endpoints die werk WEGZETTEN moeten async zijn.

Aanleiding: "Nu uitvoeren" op een schedule gaf meteen een 500. Niet omdat er
iets misging met de schedule, maar omdat het endpoint een gewone `def` was.
FastAPI draait zo'n functie in een threadpool, en daar is geen event loop —
`asyncio.get_running_loop()` valt dan om met "no running event loop", nog
voordat er iets geprobeerd is. Het zag eruit als "de schedule mislukt", en dat
is precies het soort fout dat je een uur laat zoeken op de verkeerde plek.

Dit is statisch te zien, dus dat doen we hier: elk endpoint dat een taak op de
loop zet, is een coroutine.
"""
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from routers import lab_router, schedule_router, workflow_router  # noqa: E402


@pytest.mark.parametrize("functie", [
    schedule_router.run_schedule_now,   # zet _run_schedule op de loop
    workflow_router.run_workflow,       # zet de workflow-motor op de loop
    lab_router.provision_lab,           # zet het inrichten op de loop
])
def test_endpoint_is_async(functie):
    assert inspect.iscoroutinefunction(functie), (
        f"{functie.__name__} zet werk op de event loop en moet dus async zijn; "
        f"als gewone def draait hij in een threadpool zonder loop.")
