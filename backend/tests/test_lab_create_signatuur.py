"""De router en LabService.create() mogen niet uit elkaar lopen.

Aanleiding: `lab_router.create_lab` gaf `chat_deelt_werker=` mee terwijl
`LabService.create()` dat argument nooit kreeg. Omdat de router een DEFAULT
gebruikt (`payload.get("chat_deelt_werker", True)`) ging het argument altijd
mee — en faalde dus élke poging om een lab aan te maken met een TypeError, ook
vanuit het scherm. Zoiets valt alleen op als je het echt probeert, en dan pas
bij de gebruiker.

Deze test leest uit de broncode welke argumenten de router doorgeeft en houdt
ze tegen de handtekening van de service. Geen mocks, geen database: het gaat om
de aansluiting van twee functies, en die is statisch te zien.
"""
import ast
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.lab.lab_service import LabService  # noqa: E402

ROUTER = Path(__file__).resolve().parents[1] / "src" / "routers" / "lab_router.py"


def _kwargs_van(functienaam: str, aanroep_op: str) -> set[str]:
    """De keyword-argumenten die `functienaam` meegeeft aan `<iets>.<aanroep_op>(…)`."""
    boom = ast.parse(ROUTER.read_text(encoding="utf-8"))
    for node in ast.walk(boom):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == functienaam:
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == aanroep_op):
                    return {kw.arg for kw in sub.keywords if kw.arg}
    raise AssertionError(f"geen aanroep van .{aanroep_op}() gevonden in {functienaam}()")


def test_de_router_geeft_alleen_argumenten_die_create_kent():
    doorgegeven = _kwargs_van("create_lab", "create")
    bekend = set(inspect.signature(LabService.create).parameters)
    onbekend = doorgegeven - bekend
    assert not onbekend, f"lab_router geeft argumenten mee die create() niet kent: {sorted(onbekend)}"


def test_de_router_geeft_alleen_argumenten_die_update_settings_kent():
    doorgegeven = _kwargs_van("update_lab", "update_settings")
    bekend = set(inspect.signature(LabService.update_settings).parameters)
    onbekend = doorgegeven - bekend
    assert not onbekend, f"lab_router geeft argumenten mee die update_settings() niet kent: {sorted(onbekend)}"
