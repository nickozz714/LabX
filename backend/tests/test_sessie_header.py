"""LabX vertelt Nectar welke sessie er belt — maar alleen als dat mag.

Waarom dit er is: Nectar hangt zijn focus-banen aan een sessie, en het MODEL moest
zijn eigen sessietoken onthouden en bij elke aanroep meegeven. Dat deed het niet —
van de 22 banen op het gedeelde LabX-account had er geen één een sessie gebonden —
waarna Nectar terugviel op de projectbrede focus en elke agent de taak van een
ander ingespoten kreeg.

En waarom het een vinkje is en niet altijd aan: een server van een derde hoeft niet
te weten hoeveel gesprekken je voert of welk er belt.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _Sessie:
    """Legt vast met welke headers er verbonden werd."""

    def __init__(self, bak):
        self.bak = bak

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def initialize(self):
        return None

    async def call_tool(self, naam, args):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")],
                               isError=False, structuredContent=None)


@pytest.fixture()
def gevangen(monkeypatch):
    """Vangt de headers op waarmee de http-verbinding gelegd wordt."""
    bak = {}

    class _Ctx:
        def __init__(self, url, headers):
            bak["headers"] = headers

        async def __aenter__(self):
            return (None, None)

        async def __aexit__(self, *a):
            return False

    from services.mcp import mcp_client
    monkeypatch.setattr(mcp_client, "_streamable_http_ctx", lambda url, headers: _Ctx(url, headers))
    monkeypatch.setattr(mcp_client, "ClientSession", lambda r, w: _Sessie(bak), raising=False)

    async def _geen_auth(server, **kw):
        return {"Authorization": "Bearer x"}
    monkeypatch.setattr(mcp_client, "_resolve_auth_headers", _geen_auth)
    return bak


def _server(**kw):
    basis = dict(id=1, name="Nectar", slug="nectar", location="host",
                 server_type="http", base_url="http://hive/mcp", stuur_sessie=False)
    basis.update(kw)
    return SimpleNamespace(**basis)


def _roep(server, sessie):
    import asyncio

    from services.mcp import mcp_client
    import sys as _sys
    # ClientSession wordt binnen de functie geïmporteerd uit `mcp`; die import
    # laten we staan en vervangen door onze eigen via het mcp-pakket.
    return asyncio.run(mcp_client.call_tool(server, "focus_set", {}, sessie=sessie))


def test_zonder_vinkje_gaat_de_sessie_niet_mee(gevangen, monkeypatch):
    import mcp
    monkeypatch.setattr(mcp, "ClientSession", lambda r, w: _Sessie(gevangen), raising=False)
    _roep(_server(stuur_sessie=False), "thread-123")
    assert "X-Hive-Session" not in gevangen["headers"]


def test_met_vinkje_gaat_hij_wel_mee(gevangen, monkeypatch):
    import mcp
    monkeypatch.setattr(mcp, "ClientSession", lambda r, w: _Sessie(gevangen), raising=False)
    _roep(_server(stuur_sessie=True), "thread-123")
    assert gevangen["headers"]["X-Hive-Session"] == "thread-123"


def test_geen_sessie_bekend_stuurt_niets(gevangen, monkeypatch):
    """Geen thread (bijvoorbeeld een sync van de toolslijst): dan valt er ook
    niets te vertellen, en hoort er geen lege header mee te gaan."""
    import mcp
    monkeypatch.setattr(mcp, "ClientSession", lambda r, w: _Sessie(gevangen), raising=False)
    _roep(_server(stuur_sessie=True), None)
    assert "X-Hive-Session" not in gevangen["headers"]
