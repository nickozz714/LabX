"""Geheimen die overal te gebruiken zijn.

De afspraak in één zin: een `{{secret:naam}}` wordt alleen ingevuld op weg naar
BUITEN — een tool, een commando, een HTTP-aanroep — en nooit op weg naar het
model. Het model mag geheimen KIEZEN, niet KENNEN.

Deze tests leggen die afspraak vast, plus de twee dingen die hem waard maken:
een onbekende naam is een fout in plaats van een letterlijke `{{secret:...}}`
die naar een externe dienst vertrekt, en een waarde die tóch terugkomt wordt
gemaskeerd.
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models.secret import Secret  # noqa: E402
from services.secrets.vault import VaultService, verwijzingen_in  # noqa: E402

WEBHOOK = "https://prod-12.westeurope.logic.azure.com/workflows/abc123/triggers/manual"


@pytest.fixture()
def db():
    from db.database import Base
    motor = create_engine("sqlite://")
    Base.metadata.create_all(motor, tables=[Secret.__table__])
    sessie = sessionmaker(bind=motor)()
    yield sessie
    sessie.close()


@pytest.fixture()
def kluis(db):
    svc = VaultService(db)
    svc.zet(naam="teams-webhook", waarde=WEBHOOK, omschrijving="Power Automate")
    return svc


# ── verwijzingen vinden ─────────────────────────────────────────────────────

def test_verwijzingen_worden_ook_diep_gevonden():
    """Een tool-argument is zelden een platte string."""
    args = {"url": "{{secret:teams-webhook}}",
            "headers": [{"name": "Authorization", "value": "Bearer {{ secret:token }}"}],
            "aantal": 3}
    assert verwijzingen_in(args) == ["teams-webhook", "token"]


@pytest.mark.parametrize("tekst", ["geen geheim", "{{secret}}", "{secret:x}", ""])
def test_zonder_verwijzing_geen_treffer(tekst):
    assert verwijzingen_in(tekst) == []


# ── invullen ────────────────────────────────────────────────────────────────

def test_een_verwijzing_wordt_de_waarde(kluis):
    uit, gebruikt, onbekend = kluis.vul_in({"url": "{{secret:teams-webhook}}"})
    assert uit["url"] == WEBHOOK
    assert gebruikt == ["teams-webhook"] and onbekend == []


def test_invullen_werkt_midden_in_een_string(kluis):
    uit, _, _ = kluis.vul_in("curl -X POST {{secret:teams-webhook}} -d @-")
    assert uit == f"curl -X POST {WEBHOOK} -d @-"


def test_een_onbekende_naam_is_een_fout_en_geen_stilte(kluis):
    """Zou hij blijven staan, dan vertrekt `{{secret:typefout}}` letterlijk
    naar een externe dienst — die hem niet begrijpt, of opslaat."""
    uit, gebruikt, onbekend = kluis.vul_in({"url": "{{secret:typefout}}"})
    assert onbekend == ["typefout"] and gebruikt == []
    assert uit["url"] == "{{secret:typefout}}", "de aanroeper hoort hierop te stoppen"


def test_gebruik_wordt_geteld_maar_de_waarde_niet_bewaard(kluis, db):
    kluis.vul_in("{{secret:teams-webhook}}")
    kluis.vul_in("{{secret:teams-webhook}}")
    rij = kluis.haal("teams-webhook")
    assert rij.use_count == 2 and rij.last_used_at


# ── reikwijdte ──────────────────────────────────────────────────────────────

def test_een_geheim_zonder_labs_geldt_overal(kluis):
    assert kluis.vul_in("{{secret:teams-webhook}}", lab_id="lab-1")[1] == ["teams-webhook"]
    assert kluis.vul_in("{{secret:teams-webhook}}", lab_id=None)[1] == ["teams-webhook"]


def test_een_geheim_van_een_klant_werkt_niet_in_een_ander_lab(kluis):
    kluis.zet(naam="klant-a-sleutel", waarde="zeer-geheime-waarde", lab_ids=["lab-a"])
    assert kluis.vul_in("{{secret:klant-a-sleutel}}", lab_id="lab-a")[1] == ["klant-a-sleutel"]
    uit, gebruikt, onbekend = kluis.vul_in("{{secret:klant-a-sleutel}}", lab_id="lab-b")
    assert gebruikt == [] and onbekend == ["klant-a-sleutel"]


def test_buiten_een_lab_geldt_een_labgebonden_geheim_niet(kluis):
    """Er is dan niets om aan te toetsen; doorlaten zou de afspraak uithollen."""
    kluis.zet(naam="klant-a-sleutel", waarde="zeer-geheime-waarde", lab_ids=["lab-a"])
    assert kluis.vul_in("{{secret:klant-a-sleutel}}", lab_id=None)[2] == ["klant-a-sleutel"]


# ── maskeren ────────────────────────────────────────────────────────────────

def test_een_waarde_die_terugkomt_wordt_gemaskeerd(kluis):
    """Een dienst die je sleutel terugspiegelt in een foutmelding, of een tool
    die zijn eigen aanroep echoot."""
    uit = kluis.maskeer(f"POST naar {WEBHOOK} gaf 202")
    assert WEBHOOK not in uit
    assert "{{secret:teams-webhook}}" in uit


def test_korte_waarden_worden_niet_gemaskeerd(kluis):
    """Anders raak je willekeurige tekst die er niets mee te maken heeft."""
    kluis.zet(naam="kort", waarde="ok")
    assert kluis.maskeer("alles ok hier") == "alles ok hier"


# ── beheer ──────────────────────────────────────────────────────────────────

def test_de_waarde_komt_nooit_uit_de_api(kluis):
    uit = kluis.to_dict(kluis.haal("teams-webhook"))
    assert WEBHOOK not in repr(uit)
    assert uit["placeholder"] == "{{secret:teams-webhook}}"


def test_bijwerken_zonder_waarde_laat_de_waarde_staan(kluis):
    kluis.zet(naam="teams-webhook", omschrijving="nieuwe uitleg")
    rij = kluis.haal("teams-webhook")
    assert rij.description == "nieuwe uitleg"
    assert kluis.waarde_van(rij) == WEBHOOK


@pytest.mark.parametrize("naam", ["met spatie", "punt.jes", "", "x" * 65, "$(whoami)"])
def test_een_onbruikbare_naam_wordt_geweigerd(kluis, naam):
    """De naam komt in {{secret:...}} terecht; alles wat daar kan ontsnappen
    hoort er niet in."""
    with pytest.raises(ValueError):
        kluis.zet(naam=naam, waarde="x")


def test_een_nieuw_geheim_zonder_waarde_kan_niet(kluis):
    with pytest.raises(ValueError):
        kluis.zet(naam="leeg", omschrijving="alleen uitleg")


# ── de plek waar het ertoe doet: de tool-aanroep ────────────────────────────

@pytest.fixture()
def tool_db(db):
    """Een database met een tool op een host-server, plus de kluis."""
    from db.database import Base
    from models.mcp_server import MCPServer
    from models.tool import Tool
    from models.audit import AuditTraceEvent

    Base.metadata.create_all(db.get_bind(), tables=[
        MCPServer.__table__, Tool.__table__, AuditTraceEvent.__table__])
    db.add(MCPServer(id=1, name="Teams", slug="teams", location="host",
                     is_enabled=True, created_at="nu", updated_at="nu"))
    db.add(Tool(id=10, mcp_server_id=1, name="post_message", remote_name="post_message",
                description="", is_enabled=True, created_at="nu", updated_at="nu"))
    db.commit()
    VaultService(db).zet(naam="teams-webhook", waarde=WEBHOOK)
    return db


def _voer_uit(db, args, monkeypatch, antwoord="ok"):
    import asyncio
    from services.mcp import mcp_client, tool_execution_service
    from services.mcp.tool_execution_service import ToolExecutionService

    gezien = {}

    async def _nep_call(server, remote_name, args_, **kw):
        gezien["args"] = args_
        return antwoord

    monkeypatch.setattr(mcp_client, "call_tool", _nep_call)
    monkeypatch.setattr(tool_execution_service.mcp_client, "call_tool", _nep_call)
    uit = asyncio.run(ToolExecutionService(db).execute_tool(10, args))
    return uit, gezien.get("args")


def test_een_tool_krijgt_de_waarde_het_model_nooit(tool_db, monkeypatch):
    """De kern van de afspraak: het model schrijft de NAAM in het argument, de
    tool krijgt de WAARDE, en niemand daartussen ziet allebei."""
    _, doorgegeven = _voer_uit(tool_db, {"url": "{{secret:teams-webhook}}", "tekst": "hoi"},
                               monkeypatch)
    assert doorgegeven["url"] == WEBHOOK
    assert doorgegeven["tekst"] == "hoi"


def test_een_onbekend_geheim_stopt_de_aanroep(tool_db, monkeypatch):
    """Anders vertrekt `{{secret:typefout}}` letterlijk naar de dienst."""
    with pytest.raises(RuntimeError) as fout:
        _voer_uit(tool_db, {"url": "{{secret:typefout}}"}, monkeypatch)
    assert "typefout" in str(fout.value)
    assert "teams-webhook" in str(fout.value), "het model hoort te horen wat er wél is"


def test_een_teruggespiegelde_waarde_wordt_gemaskeerd(tool_db, monkeypatch):
    uit, _ = _voer_uit(tool_db, {"url": "{{secret:teams-webhook}}"}, monkeypatch,
                       antwoord=f"400 Bad Request bij {WEBHOOK}")
    assert WEBHOOK not in uit
    assert "{{secret:teams-webhook}}" in uit


def test_het_gebruik_belandt_in_het_auditspoor_zonder_de_waarde(tool_db, monkeypatch):
    from models.audit import AuditTraceEvent

    _voer_uit(tool_db, {"url": "{{secret:teams-webhook}}"}, monkeypatch)
    rijen = tool_db.query(AuditTraceEvent).filter(AuditTraceEvent.type == "secret_used").all()
    assert len(rijen) == 1
    assert "teams-webhook" in rijen[0].data_json
    assert WEBHOOK not in rijen[0].data_json


def test_zonder_geheimen_verandert_er_niets_aan_de_aanroep(tool_db, monkeypatch):
    _, doorgegeven = _voer_uit(tool_db, {"tekst": "gewoon een bericht"}, monkeypatch)
    assert doorgegeven == {"tekst": "gewoon een bericht"}


# ── de kluis in een lab ─────────────────────────────────────────────────────
#
# Dit is wat "overal te gebruiken" waard maakt: een geheim uit de kluis hoort
# in een shell-commando in élk lab te werken, langs dezelfde weg als een geheim
# van het lab zelf — dus via een bestand in de container en nooit als tekst op
# een commandoregel.

@pytest.fixture()
def labdb():
    from db.database import Base
    from models.lab import Lab
    from models.lab_secret import LabSecret
    motor = create_engine("sqlite://")
    Base.metadata.create_all(motor, tables=[
        Secret.__table__, LabSecret.__table__, Lab.__table__])
    sessie = sessionmaker(bind=motor)()
    yield sessie
    sessie.close()


class _NepRuntime:
    """Onthoudt wat er de container in ging, zonder er een te hebben."""

    def __init__(self):
        self.stdin = []

    async def exec(self, _cid, _argv, stdin=None, timeout=None):
        if stdin:
            self.stdin.append(stdin.decode())
        return {"output": "", "exit_code": 0}


def _bereid_voor(db, commando, lab_id="lab-1"):
    import asyncio
    from services.lab.secrets import SecretService
    runtime = _NepRuntime()
    uit = asyncio.run(SecretService(db).bereid_voor(
        lab_id, commando, runtime=runtime, container_id="c1"))
    return uit, runtime


def test_een_geheim_uit_de_kluis_werkt_in_een_shell_commando(labdb):
    VaultService(labdb).zet(naam="teams-webhook", waarde=WEBHOOK)
    (commando, gebruikt, onbekend), runtime = _bereid_voor(
        labdb, 'curl -X POST "{{secret:teams-webhook}}" -d @bericht.json')
    assert gebruikt == ["teams-webhook"] and onbekend == []
    # De waarde staat NIET in het commando — daar staat de variabele.
    assert WEBHOOK not in commando
    assert "${LABX_SECRET_TEAMS_WEBHOOK}" in commando
    # En hij ging via stdin naar het bestand in de container.
    assert any(WEBHOOK in s for s in runtime.stdin)


def test_een_geheim_van_een_andere_klant_werkt_hier_niet(labdb):
    VaultService(labdb).zet(naam="klantsleutel", waarde=WEBHOOK, lab_ids=["ander-lab"])
    (commando, gebruikt, onbekend), _ = _bereid_voor(labdb, 'curl "{{secret:klantsleutel}}"')
    assert onbekend == ["klantsleutel"] and gebruikt == []
    assert "{{secret:klantsleutel}}" in commando      # en dus geen waarde


def test_het_lab_wint_van_de_kluis(labdb):
    """De specifiekere afspraak telt: zo overschrijft een lab een algemene
    waarde zonder dat de kluis aangepast hoeft te worden."""
    from services.lab.secrets import SecretService
    VaultService(labdb).zet(naam="token", waarde="uit-de-kluis-maar-lang-genoeg")
    SecretService(labdb).zet("lab-1", naam="token", waarde="van-het-lab-zelf-en-lang")
    (_, gebruikt, _), runtime = _bereid_voor(labdb, 'echo "{{secret:token}}"')
    assert gebruikt == ["token"]
    assert any("van-het-lab-zelf-en-lang" in s for s in runtime.stdin)
    assert not any("uit-de-kluis-maar-lang-genoeg" in s for s in runtime.stdin)


def test_een_kluiswaarde_die_terugkomt_wordt_ook_in_een_lab_gemaskeerd(labdb):
    from services.lab.secrets import SecretService
    VaultService(labdb).zet(naam="teams-webhook", waarde=WEBHOOK)
    uit = SecretService(labdb).maskeer_in("lab-1", f"curl gaf 202 voor {WEBHOOK}")
    assert WEBHOOK not in uit


# ── de weg van de verwijzing door het gesprek ───────────────────────────────
#
# Een `{{secret:naam}}` die je in een skill, een chatbericht of een
# board-ticket schrijft, reist als TEKST naar het model en wordt pas bij de
# tool of het commando ingevuld. Dat werkt alleen als het model weet dat hij
# hem ongewijzigd moet doorgeven; anders vraagt hij jou om de waarde, en dan is
# de hele kluis een omweg. Die afspraak staat in de systeemprompt, en hoort
# daar niet stilletjes uit te verdwijnen.

def test_de_agent_krijgt_te_horen_wat_een_verwijzing_is():
    from services.agent.chat_agent import AGENT_PREAMBLE

    assert "{{secret:name}}" in AGENT_PREAMBLE
    tekst = AGENT_PREAMBLE.lower()
    # verbatim doorgeven, niet invullen en niet om de waarde vragen
    assert "verbatim" in tekst
    assert "never ask the user for the value" in tekst
    assert "lab__secret_list" in AGENT_PREAMBLE


def test_de_verwijzing_gaat_ongewijzigd_naar_het_model(kluis):
    """Wat in een ticket of een bericht staat, gaat zoals het er staat naar het
    model: de kluis vult alleen in op weg naar BUITEN."""
    ticket = ("Stuur een samenvatting naar het incidentenkanaal met "
              "`curl -X POST \"{{secret:teams-webhook}}\" -d @bericht.json`")
    # niets in de gespreksweg raakt de tekst aan — dit is de invariant, en de
    # tegenhanger van test_een_tool_krijgt_de_waarde_het_model_nooit.
    assert WEBHOOK not in ticket
    assert "{{secret:teams-webhook}}" in ticket
    # en pas bij de aanroep komt de waarde erin
    ingevuld, gebruikt, onbekend = kluis.vul_in({"url": "{{secret:teams-webhook}}"})
    assert ingevuld["url"] == WEBHOOK and gebruikt == ["teams-webhook"] and onbekend == []
