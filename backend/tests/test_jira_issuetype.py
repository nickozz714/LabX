"""Het soort issue dat LabX in Jira aanmaakt.

Waargenomen bij Swinkels: élke push naar het BICC-project mislukte met
`Issuetype is een subtaak maar bovenliggende issuecode of id zijn niet
gespecifieerd`. Het bord stond op "Task", en dat blijkt in dat project een
SUBTAAK te zijn — en een subtaak kan niet zonder bovenliggend issue.

Die melding zegt niet dat je instelling fout staat, en er is geen manier om het
van buitenaf te weten: alleen Jira kent de issuetypes van een project. Dus
vragen in plaats van gokken, vóór het aanmaken, met een melding die zegt wat je
wél kunt kiezen.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.boards.sync.jira import JiraAdapter  # noqa: E402

CONFIG = {"base_url": "https://rsfb.atlassian.net", "email": "iemand@swinkels.com",
          "project_key": "BICC", "issue_type": "Task"}


def _adapter(soorten):
    a = JiraAdapter(dict(CONFIG), "token")

    async def _soorten():
        return soorten
    a.item_types = _soorten
    return a


SUBTAAK = {"id": "10003", "naam": "Task", "subtaak": True, "verplicht": []}
TAAK = {"id": "10001", "naam": "Taak", "subtaak": False, "verplicht": []}
BUG = {"id": "10004", "naam": "Bug", "subtaak": False, "verplicht": []}


def test_een_subtaak_wordt_geweigerd_met_de_soorten_die_wel_kunnen():
    """Dit is de fout uit productie: het bord stond op een subtaak-type."""
    a = _adapter([SUBTAAK, TAAK, BUG])
    with pytest.raises(RuntimeError) as fout:
        asyncio.run(a._issuetype_voor(None, None))
    melding = str(fout.value)
    assert "SUBTAAK" in melding
    assert "Taak" in melding and "Bug" in melding     # wat je wél kunt kiezen


def test_een_bestaand_soort_gaat_mee_als_id():
    """Op id en niet op naam: twee projecten kunnen dezelfde naam anders
    invullen, en een id is eenduidig."""
    a = _adapter([SUBTAAK, TAAK, BUG])
    assert asyncio.run(a._issuetype_voor(None, "Bug")) == {"id": "10004"}


def test_het_ticket_wint_van_het_bord():
    """Een bug en een taak horen op hetzelfde bord te kunnen staan."""
    a = _adapter([SUBTAAK, TAAK, BUG])
    assert asyncio.run(a._issuetype_voor(None, "Bug"))["id"] == BUG["id"]


def test_een_onbekend_soort_noemt_wat_er_wel_is():
    a = _adapter([TAAK, BUG])
    with pytest.raises(RuntimeError) as fout:
        asyncio.run(a._issuetype_voor(None, "Verhaal"))
    assert "kent geen issuetype 'Verhaal'" in str(fout.value)
    assert "Taak, Bug" in str(fout.value)


def test_verplichte_velden_die_labx_niet_kent_worden_vooraf_gemeld():
    """Het tweede deel van "er mist informatie in het ticket": Jira kan velden
    verplicht stellen die LabX niet heeft. Dan is een duidelijke melding beter
    dan een 400 waar de veldnaam in codevorm in staat."""
    a = _adapter([{"id": "10005", "naam": "Bug", "subtaak": False,
                   "verplicht": [{"veld": "customfield_10050", "naam": "Oorzaak"},
                                 {"veld": "components", "naam": "Component"}]}])
    with pytest.raises(RuntimeError) as fout:
        asyncio.run(a._issuetype_voor(None, "Bug"))
    assert "Oorzaak" in str(fout.value) and "Component" in str(fout.value)


def test_zonder_antwoord_van_jira_laten_we_jira_het_afkeuren():
    """Valt createmeta weg (rechten, een oude Jira), dan is doorgaan met de
    naam beter dan weigeren: dan bepaalt Jira zelf of het kan."""
    a = JiraAdapter(dict(CONFIG), "token")

    async def _stuk():
        raise RuntimeError("403")
    a.item_types = _stuk
    assert asyncio.run(a._issuetype_voor(None, "Task")) == {"name": "Task"}


def test_een_leeg_project_laat_de_naam_staan():
    a = _adapter([])
    assert asyncio.run(a._issuetype_voor(None, "Task")) == {"name": "Task"}


def test_hoofdletters_maken_niet_uit():
    a = _adapter([BUG])
    assert asyncio.run(a._issuetype_voor(None, "bug")) == {"id": "10004"}


# ── waar de types vandaan komen ─────────────────────────────────────────────

class _Antwoord:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.headers = {"content-type": "application/json"}
        self.text = ""

    def json(self):
        return self._body


class _Client:
    """Geeft per URL een vast antwoord, en onthoudt wat er gevraagd is."""

    def __init__(self, antwoorden):
        self.antwoorden = antwoorden
        self.gevraagd = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None, params=None):
        self.gevraagd.append(url)
        for stuk, antwoord in self.antwoorden.items():
            if stuk in url:
                return antwoord
        return _Antwoord(404, {})


def test_een_team_managed_project_valt_terug_op_de_oude_createmeta(monkeypatch):
    """Het echte geval bij Swinkels: BICC is een team-managed project, en daar
    geeft de NIEUWE createmeta HTTP 200 met een lege lijst. Zonder deze
    terugval viel LabX terug op de naam uit de instellingen — precies de
    situatie die we wilden voorkomen."""
    import httpx

    client = _Client({
        "createmeta": _Antwoord(200, {"projects": [{"issuetypes": [
            {"id": "10072", "name": "Taak", "subtask": True, "fields": {}},
            {"id": "10099", "name": "Task2", "subtask": False, "fields": {}},
            {"id": "10975", "name": "Bug", "subtask": False, "fields": {
                "summary": {"required": True, "name": "Samenvatting"},
                "customfield_10050": {"required": True, "name": "Oorzaak"}}},
        ]}]}),
    })
    monkeypatch.setattr(httpx, "AsyncClient", client)
    soorten = asyncio.run(JiraAdapter(dict(CONFIG), "token").item_types())

    assert [s["naam"] for s in soorten] == ["Taak", "Task2", "Bug"]
    assert soorten[0]["subtaak"] is True
    # `summary` sturen we altijd mee; dat is geen ontbrekende informatie.
    assert [v["naam"] for v in soorten[2]["verplicht"]] == ["Oorzaak"]


def test_zonder_createmeta_kent_het_project_zijn_types_nog_steeds(monkeypatch):
    """Laatste redmiddel: dan weten we de types wel, maar niet welke velden
    verplicht zijn."""
    import httpx

    client = _Client({
        "createmeta": _Antwoord(200, {"projects": []}),
        "/project/BICC": _Antwoord(200, {"issueTypes": [
            {"id": "10099", "name": "Task2", "subtask": False},
            {"id": "10072", "name": "Taak", "subtask": True}]}),
    })
    monkeypatch.setattr(httpx, "AsyncClient", client)
    soorten = asyncio.run(JiraAdapter(dict(CONFIG), "token").item_types())
    assert [(s["naam"], s["subtaak"]) for s in soorten] == [("Task2", False), ("Taak", True)]
    assert "verplicht" not in soorten[0]     # dat weten we hier niet
