"""Imports die pas bij de aanroep stukgaan.

Een endpoint dat zijn service binnen de functie importeert, laadt die pas bij
het eerste verzoek. Staat daar een naam die niet bestaat, dan importeert de
server gewoon, starten alle tests groen op, en geeft alleen dát ene endpoint
een 500 — in productie.

Dat gebeurde met `/boards/{id}/item-types`: de regel zei `import SyncService`
terwijl de klasse `BoardSyncService` heet. Gevolg: de keuzelijst met
issuetypes haalde niets op, en omdat het scherm bij een lege lijst niets toonde
was er ook niets te zien dat kapot was.

Deze test loopt alle uitgestelde imports in de routers na en controleert dat de
naam echt bestaat. Dat is statisch te doen, kost niets, en vangt precies de
fout die je anders pas in productie ziet.
"""
import ast
import importlib
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

ROUTERS = sorted((SRC / "routers").glob("*.py"))


def _uitgestelde_imports(pad: Path):
    """Elke `from x import y` die BINNEN een functie staat."""
    boom = ast.parse(pad.read_text(), filename=str(pad))
    uit = []
    for knoop in ast.walk(boom):
        if not isinstance(knoop, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for binnen in ast.walk(knoop):
            if isinstance(binnen, ast.ImportFrom) and binnen.module:
                for naam in binnen.names:
                    uit.append((binnen.module, naam.name, binnen.lineno))
    return uit


@pytest.mark.parametrize("pad", ROUTERS, ids=lambda p: p.name)
def test_elke_uitgestelde_import_in_een_router_bestaat(pad):
    ontbreekt = []
    for module, naam, regel in _uitgestelde_imports(pad):
        if not module.split(".")[0] in ("services", "models", "db", "utils", "schemas"):
            continue        # derden hoeven we hier niet te keuren
        try:
            mod = importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001
            ontbreekt.append(f"{pad.name}:{regel} — module {module} laadt niet: {exc}")
            continue
        if hasattr(mod, naam):
            continue
        # `from services.workflows import verwijzingen` haalt een SUBMODULE op,
        # en die bestaat pas als attribuut zodra hij geladen is. Dat is geldig
        # Python, dus dat mag deze test niet als fout aanrekenen.
        try:
            importlib.import_module(f"{module}.{naam}")
        except ImportError:
            ontbreekt.append(f"{pad.name}:{regel} — {module} heeft geen {naam!r}")
    assert not ontbreekt, "\n".join(ontbreekt)
