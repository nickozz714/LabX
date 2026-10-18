"""Een router mag niets meegeven wat de dienst eronder niet kent.

Aanleiding: `cluster_router.opnieuw` gaf `env_bestanden=` door aan
`ClusterService.opnieuw`, en die kende dat argument niet. Elke poging om
opnieuw te verkennen gaf daardoor een 500 -- met een TypeError in het log en
een gebruiker die denkt dat zijn antwoorden niet aankomen. De handtekening
stond in hetzelfde bestand als de aanroep; alleen niet in dezelfde functie.

Deze test leest statisch welke sleutelwoorden een router meegeeft aan een
`_svc(db).<naam>(...)`-aanroep en vergelijkt die met de parameters van die
methode. Goedkoper dan elke route aanroepen, en het vangt precies het gat dat
tussen twee lagen valt.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

BRON = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(BRON))


def _aanroepen(boom: ast.Module) -> list[tuple[str, set[str], int]]:
    """(methodenaam, meegegeven sleutelwoorden, regelnummer) per svc-aanroep."""
    uit = []
    for knoop in ast.walk(boom):
        if not isinstance(knoop, ast.Call):
            continue
        fn = knoop.func
        if not isinstance(fn, ast.Attribute):
            continue
        # `_svc(db).naam(...)` en `svc.naam(...)`
        doel = fn.value
        is_svc = (isinstance(doel, ast.Call) and isinstance(doel.func, ast.Name)
                  and doel.func.id == "_svc") or (
                 isinstance(doel, ast.Name) and doel.id == "svc")
        if not is_svc:
            continue
        namen = {k.arg for k in knoop.keywords if k.arg}
        if namen:
            uit.append((fn.attr, namen, knoop.lineno))
    return uit


def test_de_cluster_router_geeft_niets_mee_dat_de_dienst_niet_kent() -> None:
    from services.lab.clusters import ClusterService

    pad = BRON / "routers" / "cluster_router.py"
    boom = ast.parse(pad.read_text(encoding="utf-8"), str(pad))

    fouten: list[str] = []
    for naam, meegegeven, regel in _aanroepen(boom):
        methode = getattr(ClusterService, naam, None)
        if methode is None or not callable(methode):
            continue
        bekend = set(inspect.signature(methode).parameters)
        onbekend = meegegeven - bekend
        if onbekend:
            fouten.append(f"cluster_router.py:{regel}: ClusterService.{naam}() kent "
                          f"{sorted(onbekend)} niet")

    assert not fouten, "Router en dienst lopen uiteen:\n" + "\n".join(fouten)
