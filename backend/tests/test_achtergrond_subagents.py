"""Een run die zijn werk op de achtergrond zet, is niet klaar.

De aanleiding: een agent-run op SWI-88 gaf de hele opruimtaak (TST -> ACC ->
PRD) aan een subagent met `run_in_background`, deed er een hive-klusje naast en
meldde "de opruimtaak loopt nog op de achtergrond; je hoort ervan". Dat bericht
kon nooit komen — de run is één headless aanroep en het proces valt om zodra
het eindantwoord er staat. De run stond op `completed`, het ticket zag er
afgehandeld uit, en het werk was nooit gebeurd.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.agent.claude_cli_provider import (  # noqa: E402
    NATIVE_TOOLS_TOEGESTAAN, achtergrond_subagents, onverwachte_native_tools)


def test_achtergrond_subagent_wordt_gezien():
    steps = [
        {"kind": "tool", "name": "mcp__labx__lab__shell_exec", "input": {"cmd": "ls"}},
        {"kind": "tool", "name": "Agent", "input": {
            "description": "SWI-88 ProductHierarchy cleanup TST/ACC/PRD",
            "subagent_type": "claude", "run_in_background": True, "prompt": "..."}},
    ]
    assert achtergrond_subagents(steps) == ["SWI-88 ProductHierarchy cleanup TST/ACC/PRD"]


def test_voorgrond_subagent_is_prima():
    """Daar wacht de run gewoon op; die overleeft het einde niet omdat hij
    er niet meer is."""
    steps = [{"kind": "tool", "name": "Agent",
              "input": {"description": "even uitzoeken", "subagent_type": "claude"}}]
    assert achtergrond_subagents(steps) == []
    steps[0]["input"]["run_in_background"] = False
    assert achtergrond_subagents(steps) == []


def test_meerdere_worden_allemaal_gemeld():
    steps = [
        {"kind": "tool", "name": "Agent",
         "input": {"description": "een", "run_in_background": True}},
        {"kind": "tool", "name": "Task",
         "input": {"description": "twee", "run_in_background": True}},
        {"kind": "tool", "name": "Agent",
         "input": {"description": "een", "run_in_background": True}},
    ]
    assert achtergrond_subagents(steps) == ["een", "twee"]


def test_naamloze_achtergrondtaak_valt_terug_op_het_soort():
    steps = [{"kind": "tool", "name": "Agent",
              "input": {"subagent_type": "Explore", "run_in_background": True}}]
    assert achtergrond_subagents(steps) == ["Explore"]


def test_rommel_laat_de_melder_niet_omvallen():
    for steps in (None, [], [None], [{}], [{"kind": "tool", "name": "Agent"}],
                  [{"kind": "tool", "name": "Agent", "input": "geen dict"}]):
        assert achtergrond_subagents(steps) == []


def test_agent_blijft_een_toegestane_tool():
    """De melder mag Agent niet ook nog als 'onbekende CLI-tool' aanmerken —
    het probleem is de achtergrondvlag, niet de tool."""
    assert "Agent" in NATIVE_TOOLS_TOEGESTAAN
    steps = [{"kind": "tool", "name": "Agent", "input": {"run_in_background": True}}]
    assert onverwachte_native_tools(steps) == []
