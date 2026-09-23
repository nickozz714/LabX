"""
services/workflows/migratie.py

Oude workflows omzetten naar de graaf.

Een workflow was een lijst stappen; nu is het een graaf van activiteiten. Bij
het LEZEN en het UITVOEREN werd zo'n oude workflow al als rechte keten
ingelezen (`graph.zorg_voor_graaf`), dus kapot was er niets — maar dat gebeurde
elke keer opnieuw, in het geheugen, en het resultaat werd nergens bewaard. Dat
is op twee manieren vervelend:

- de posities op het doek werden telkens opnieuw verzonnen, dus je kon een
  workflow niet netjes neerzetten zonder hem eerst te bewerken;
- en een workflow die je nooit opende, bleef half in de oude wereld hangen.

Deze omzetting doet het één keer, echt: de keten wordt opgeslagen als
activiteiten met een eigen plek. Alleen voor workflows die nog geen graaf
hebben — wie er al één heeft (zelf getekend of eerder omgezet) wordt met rust
gelaten. Draait bij het opstarten, naast de andere seeds.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sqlalchemy.orm import Session

from component_logging import get_logger
from models.workflow import Workflow
from services.workflows import graph
from services.workflows.workflow_service import parse_markdown_to_steps

log = get_logger(__name__)

# Waar de eerste activiteit staat en hoeveel ruimte er tussen twee zit. Ruim
# genoeg dat de kaarten elkaar niet raken, en recht onder elkaar: een keten
# leest van boven naar beneden.
START_X = 80
START_Y = 40
AFSTAND_Y = 150


def _met_posities(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for i, node in enumerate(nodes):
        node["positie"] = {"x": START_X, "y": START_Y + i * AFSTAND_Y}
    return nodes


def zet_om(workflow: Workflow) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """De graaf voor deze ene workflow, of ([], []) als er niets om te zetten is."""
    if list(getattr(workflow, "nodes_json", None) or []):
        return [], []          # heeft er al een
    steps = list(getattr(workflow, "steps_json", None) or [])
    if not steps:
        # Een workflow die alleen markdown had (bijvoorbeeld via de API
        # aangemaakt): daar zitten de stappen gewoon in, ze waren alleen nooit
        # uitgelezen.
        steps = parse_markdown_to_steps(getattr(workflow, "markdown", "") or "")
    if not steps:
        return [], []
    nodes, edges = graph.uit_stappen(steps)
    return _met_posities(nodes), edges


def zet_oude_workflows_om(db: Session) -> int:
    """Alle workflows zonder graaf omzetten. Geeft terug hoeveel er zijn
    bijgewerkt; nul is het normale geval zodra het een keer gedraaid heeft."""
    omgezet = 0
    for workflow in db.query(Workflow).all():
        nodes, edges = zet_om(workflow)
        if not nodes:
            continue
        workflow.nodes_json = nodes
        workflow.edges_json = edges
        omgezet += 1
        log.infox("Workflow omgezet naar activiteiten", workflow=workflow.name,
                  activiteiten=len(nodes))
    if omgezet:
        db.commit()
        log.infox("Oude workflows omgezet", aantal=omgezet)
    return omgezet
