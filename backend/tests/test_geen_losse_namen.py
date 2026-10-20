"""Namen die nergens gebonden zijn, vangen we voordat de code ze raakt.

Aanleiding: `_herstel` in proefbouw.py gaf een sjabloon `boom=boom` mee terwijl
die naam daar niet bestond. Dat pad draait alleen als een proefbouwronde faalt,
dus de fout bleef onzichtbaar tot precies het moment dat herstel moest werken --
en toen viel de hele proefbouw om met een 500. Dezelfde scan vond meteen een
tweede geval (`json` zonder import in de Jira-sync, alleen geraakt als
`field_defaults` als tekst is opgeslagen).

De controle is opzettelijk ruim: hij kijkt per functie of elke gelezen naam
ergens gebonden wordt -- als parameter, toekenning, import, except-naam of in
een omliggende scope. Twijfelgevallen horen bij `TOEGESTAAN` met uitleg, niet
bij een soepelere regel.
"""
from __future__ import annotations

import ast
import builtins
from pathlib import Path

BRON = Path(__file__).resolve().parents[1] / "src"

#: Module-dunders bestaan op modulenniveau zonder dat iets ze toekent.
TOEGESTAAN = {"__file__", "__name__", "__doc__", "__package__", "__spec__"}


class _Scope:
    def __init__(self, ouder: "_Scope | None" = None) -> None:
        self.ouder, self.namen = ouder, set()

    def heeft(self, naam: str) -> bool:
        return naam in self.namen or (self.ouder.heeft(naam) if self.ouder else False)


def _bind(knoop: ast.AST, scope: _Scope) -> None:
    """Verzamel alles wat binnen deze knoop een naam krijgt."""
    for k in ast.walk(knoop):
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope.namen.add(k.name)
        elif isinstance(k, ast.Name) and isinstance(k.ctx, (ast.Store, ast.Del)):
            scope.namen.add(k.id)
        elif isinstance(k, ast.arg):
            scope.namen.add(k.arg)
        elif isinstance(k, (ast.Import, ast.ImportFrom)):
            for a in k.names:
                scope.namen.add((a.asname or a.name).split(".")[0])
        elif isinstance(k, ast.ExceptHandler) and k.name:
            scope.namen.add(k.name)
        elif isinstance(k, (ast.Global, ast.Nonlocal)):
            scope.namen.update(k.names)


def test_elke_gelezen_naam_bestaat() -> None:
    ingebouwd = set(dir(builtins)) | TOEGESTAAN
    los: list[str] = []

    for pad in sorted(BRON.rglob("*.py")):
        boom = ast.parse(pad.read_text(encoding="utf-8"), str(pad))
        top = _Scope()
        _bind(boom, top)
        for fn in [n for n in ast.walk(boom)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            scope = _Scope(top)
            _bind(fn, scope)
            for n in ast.walk(fn):
                if (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                        and n.id not in ingebouwd and not scope.heeft(n.id)):
                    los.append(f"{pad.relative_to(BRON)}:{n.lineno}: {n.id} (in {fn.name})")

    assert not los, "Namen zonder binding:\n" + "\n".join(sorted(set(los)))
