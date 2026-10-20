"""Een router mag niets meegeven wat de dienst eronder niet kent.

Aanleiding: `cluster_router.opnieuw` gaf `env_bestanden=` door aan
`ClusterService.opnieuw`, en die kende dat argument niet. Elke poging om
opnieuw te verkennen gaf daardoor een 500 -- met een TypeError in het log en
een gebruiker die denkt dat zijn antwoorden niet aankomen. De handtekening
stond in hetzelfde bestand als de aanroep; alleen niet in dezelfde functie.

Deze test leest statisch welke sleutelwoorden een router meegeeft aan een
dienst-aanroep en vergelijkt die met de parameters van die methode. Goedkoper
dan elke route aanroepen, en het vangt precies het gat dat tussen twee lagen
valt.

WELKE dienst, dat volgt uit de fabriek waarmee hij gemaakt is. Die stap zat
er eerst niet in: elke `svc.<naam>()` werd tegen ClusterService gehouden, en
toen de router er een tweede en derde dienst bij kreeg sloeg de test aan op
een `wijzig()` die prima bestond -- alleen op een andere klasse. Een test die
bij groei vals alarm geeft, wordt uitgezet, en dan vangt hij ook het echte
geval niet meer.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

BRON = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(BRON))

#: De fabrieksfuncties in cluster_router.py en de klasse die ze opleveren.
FABRIEKEN = {
    "_svc": ("services.lab.clusters", "ClusterService"),
    "_acties": ("services.lab.dienst_acties", "DienstActieService"),
    "_bundels": ("services.lab.bundels", "BundelService"),
}


def _klasse(fabriek: str):
    module, naam = FABRIEKEN[fabriek]
    return getattr(__import__(module, fromlist=[naam]), naam)


def _aanroepen(boom: ast.Module) -> list[tuple[str, str, set[str], int]]:
    """(fabriek, methodenaam, meegegeven sleutelwoorden, regelnummer).

    Per functie wordt bijgehouden welke naam aan welke fabriek hangt, zodat
    `svc = _bundels(db)` niet tegen ClusterService gelegd wordt.
    """
    uit = []
    for functie in ast.walk(boom):
        if not isinstance(functie, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        gebonden: dict[str, str] = {}
        for knoop in ast.walk(functie):
            if isinstance(knoop, ast.Assign):
                doel = knoop.value
                if (isinstance(doel, ast.Call) and isinstance(doel.func, ast.Name)
                        and doel.func.id in FABRIEKEN):
                    for t in knoop.targets:
                        if isinstance(t, ast.Name):
                            gebonden[t.id] = doel.func.id
        for knoop in ast.walk(functie):
            if not isinstance(knoop, ast.Call) or not isinstance(knoop.func, ast.Attribute):
                continue
            doel = knoop.func.value
            # `_svc(db).naam(...)` — rechtstreeks op de fabriek
            if (isinstance(doel, ast.Call) and isinstance(doel.func, ast.Name)
                    and doel.func.id in FABRIEKEN):
                fabriek = doel.func.id
            # `svc.naam(...)` — via een naam die eerder gebonden is
            elif isinstance(doel, ast.Name) and doel.id in gebonden:
                fabriek = gebonden[doel.id]
            else:
                continue
            namen = {k.arg for k in knoop.keywords if k.arg}
            if namen:
                uit.append((fabriek, knoop.func.attr, namen, knoop.lineno))
    return uit


def test_de_cluster_router_geeft_niets_mee_dat_de_dienst_niet_kent() -> None:
    pad = BRON / "routers" / "cluster_router.py"
    boom = ast.parse(pad.read_text(encoding="utf-8"), str(pad))

    gezien = 0
    fouten: list[str] = []
    for fabriek, naam, meegegeven, regel in _aanroepen(boom):
        klasse = _klasse(fabriek)
        methode = getattr(klasse, naam, None)
        if methode is None or not callable(methode):
            fouten.append(f"cluster_router.py:{regel}: {klasse.__name__} heeft geen {naam}()")
            continue
        gezien += 1
        bekend = set(inspect.signature(methode).parameters)
        onbekend = meegegeven - bekend
        if onbekend:
            fouten.append(f"cluster_router.py:{regel}: {klasse.__name__}.{naam}() kent "
                          f"{sorted(onbekend)} niet")

    assert not fouten, "Router en dienst lopen uiteen:\n" + "\n".join(fouten)
    # Vangt het stille geval: een hernoemde fabriek laat de lus leeglopen en
    # dan slaagt deze test zonder iets gecontroleerd te hebben.
    assert gezien >= 8, f"Er zijn er maar {gezien} gecontroleerd; herkent de test de aanroepen nog?"


def test_de_test_ziet_een_argument_dat_de_dienst_niet_kent() -> None:
    """De bewaker zelf nagerekend, op de fout waarvoor hij gemaakt is."""
    boom = ast.parse("def f(db):\n    svc = _bundels(db)\n    svc.wijzig(bestaat_niet=1)\n")
    aanroepen = _aanroepen(boom)

    assert aanroepen == [("_bundels", "wijzig", {"bestaat_niet"}, 3)]
    bekend = set(inspect.signature(_klasse("_bundels").wijzig).parameters)
    assert "bestaat_niet" not in bekend
