"""Een promptsjabloon en zijn `.format()`-aanroep moeten dezelfde plekken kennen.

Aanleiding: `_HERSTEL_PROMPT.format(...)` kreeg `boom=` meegegeven terwijl het
sjabloon helemaal geen `{boom}` had. `str.format` negeert te veel argumenten
zonder morren, dus de bestandslijst én de Azure-aanmelding bereikten het model
nooit -- en elke herstelronde repareerde blind. Te weinig argumenten is de
andere kant: dan valt het pas om op het moment dat je het nodig hebt.

De test leest statisch welke plekken in het sjabloon staan en welke sleutels de
aanroep meegeeft, en eist dat die twee verzamelingen gelijk zijn.
"""
from __future__ import annotations

import ast
from pathlib import Path
from string import Formatter

BRON = Path(__file__).resolve().parents[1] / "src"


def _plekken(sjabloon: str) -> set[str]:
    return {naam for _, naam, _, _ in Formatter().parse(sjabloon) if naam}


def _sjablonen(boom: ast.Module) -> dict[str, str]:
    """Alle modulebrede tekstconstanten die op _PROMPT eindigen."""
    uit: dict[str, str] = {}
    for knoop in boom.body:
        if isinstance(knoop, ast.Assign) and isinstance(knoop.value, ast.Constant):
            for doel in knoop.targets:
                if (isinstance(doel, ast.Name) and doel.id.endswith("_PROMPT")
                        and isinstance(knoop.value.value, str)):
                    uit[doel.id] = knoop.value.value
    return uit


def test_elke_prompt_aanroep_past_op_zijn_sjabloon() -> None:
    fouten: list[str] = []

    for pad in sorted(BRON.rglob("*.py")):
        boom = ast.parse(pad.read_text(encoding="utf-8"), str(pad))
        sjablonen = _sjablonen(boom)
        if not sjablonen:
            continue
        for knoop in ast.walk(boom):
            if not (isinstance(knoop, ast.Call)
                    and isinstance(knoop.func, ast.Attribute)
                    and knoop.func.attr == "format"
                    and isinstance(knoop.func.value, ast.Name)
                    and knoop.func.value.id in sjablonen):
                continue
            if any(k.arg is None for k in knoop.keywords):  # **kwargs: niet te zien
                continue
            naam = knoop.func.value.id
            gegeven = {k.arg for k in knoop.keywords if k.arg}
            verwacht = _plekken(sjablonen[naam])
            if (te_veel := gegeven - verwacht):
                fouten.append(f"{pad.relative_to(BRON)}:{knoop.lineno}: {naam} krijgt "
                              f"{sorted(te_veel)}, maar het sjabloon gebruikt die niet")
            if (te_weinig := verwacht - gegeven):
                fouten.append(f"{pad.relative_to(BRON)}:{knoop.lineno}: {naam} mist "
                              f"{sorted(te_weinig)}")

    assert not fouten, "Prompt en aanroep lopen uiteen:\n" + "\n".join(fouten)
