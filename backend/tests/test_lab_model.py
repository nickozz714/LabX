"""Een model per lab, en achtergrondtaken die op de standaard blijven.

Het model hing tot nu toe aan het GESPREK. Dat werkt zolang je chat, maar het
werk in LabX hangt aan een lab: een board-agent die tickets van dit lab oppakt,
hoort op hetzelfde model te draaien als de chat ernaast, zonder dat je dat bij
elke run opnieuw instelt.

De uitzondering is de achtergrondtaak. Die draait op het standaardmodel, ook
als het lab iets zwaarders gebruikt — achtergrondwerk is volghouden en
verzamelen, en dat uren op het duurste model laten lopen kost veel en levert
niets. De agent krijgt dat als instructie mee bij `task__start_background`, dus
hij weet wat hij erin kan stoppen.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.agent.chat_agent import ChatAgent  # noqa: E402


class _Db:
    """Alleen `get` — dat is alles wat de modelkeuze van de database vraagt."""

    def __init__(self, labs):
        self._labs = labs

    def get(self, _model, key):
        return self._labs.get(key)


def _agent(labs):
    return ChatAgent(_Db(labs))


def test_het_lab_bepaalt_het_model():
    agent = _agent({"lab-1": SimpleNamespace(model="opus")})
    assert agent._lab_model("lab-1") == "opus"


def test_zonder_keuze_blijft_het_de_standaard():
    """Leeg is "de standaard uit de instellingen", niet "geen model"."""
    agent = _agent({"lab-1": SimpleNamespace(model=None),
                    "lab-2": SimpleNamespace(model="")})
    assert agent._lab_model("lab-1") is None
    assert agent._lab_model("lab-2") is None


def test_een_lab_dat_niet_bestaat_valt_niet_om():
    """Dit draait in het pad van elke run; een verdwenen lab hoort daar geen
    uitzondering op te geven maar gewoon de standaard."""
    agent = _agent({})
    assert agent._lab_model("weg") is None
    assert agent._lab_model(None) is None


def test_een_expliciete_keuze_wint_van_het_lab():
    """Een chat die zelf een model koos, houdt die keuze: dat is een bewuste
    handeling van vlak ervoor en staat dichter bij de gebruiker dan de
    labinstelling."""
    agent = _agent({"lab-1": SimpleNamespace(model="opus")})
    settings = SimpleNamespace(
        default_model="claude-sonnet-5", oauth_token=None, cli_path="claude",
        max_turns=10, timeout_seconds=60, extra_args=[], enable_tool_search=True,
        fallback_model=None, max_budget_usd=None, autocompact=None,
        custom_agents_json=None, default_agent=None)

    _, uit_lab = agent._build_provider(agent._lab_model("lab-1"), None, settings)
    assert uit_lab == "opus"

    gekozen = "haiku"
    _, expliciet = agent._build_provider(gekozen or agent._lab_model("lab-1"), None, settings)
    assert expliciet == "haiku"

    _, zonder = agent._build_provider(None or agent._lab_model("lab-2"), None, settings)
    assert zonder == "claude-sonnet-5"


def test_een_onbekend_model_valt_terug_op_de_standaard():
    """Iemand typt "gpt-4" in de labinstelling. Dat mag de CLI niet laten
    afbreken — dan zou één typefout elke run in dat lab stukmaken."""
    agent = _agent({"lab-1": SimpleNamespace(model="gpt-4")})
    settings = SimpleNamespace(
        default_model="claude-sonnet-5", oauth_token=None, cli_path="claude",
        max_turns=10, timeout_seconds=60, extra_args=[], enable_tool_search=True,
        fallback_model=None, max_budget_usd=None, autocompact=None,
        custom_agents_json=None, default_agent=None)
    _, cc = agent._build_provider(agent._lab_model("lab-1"), None, settings)
    assert cc == "claude-sonnet-5"


def test_de_achtergrondtaak_krijgt_de_instructie_mee():
    """De regel is alleen zinnig als de agent hem kent: anders zet hij er
    redeneerwerk in en vraagt zich af waarom het antwoord tegenvalt."""
    import services.mcp.gateway as gateway

    bron = Path(gateway.__file__).read_text(encoding="utf-8")
    kop = bron[bron.index('name="task__start_background"'):]
    beschrijving = kop[:kop.index("parameters=")]
    assert "standaardmodel" in beschrijving
    assert "zwaarder model" in beschrijving
