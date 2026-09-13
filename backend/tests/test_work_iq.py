"""Work IQ: een eigen Entra-app, en een token voor de juiste API.

Work IQ is Microsofts intelligentielaag over Microsoft 365 en biedt Teams,
Outlook, agenda en bestanden aan als MCP-server. LabX praat al MCP, dus het
werk zit niet in de koppeling maar in de IDENTITEIT. Twee dingen maakten de
bestaande profieltypen ongeschikt, allebei gemeten en niet gegokt:

    $ az account get-access-token --resource api://workiq.svc.cloud.microsoft
    AADSTS65002: Consent between first party application '04b07795…' (Azure CLI)
    and first party resource 'fdcc1f02…' (Work IQ) must be configured via
    preauthorization.

Microsoft autoriseert de Azure CLI niet voor deze API, dus een `msal_bundle`
kan er niet bij. En de Work IQ-machtigingen bestaan alleen als DELEGATED — er
is geen application-variant — dus een `service_principal` met client
credentials ook niet. Blijft over: een eigen app-registratie met een
device-code-inlog.
"""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.azure import entra_app_login  # noqa: E402
from services.mcp.catalog import find  # noqa: E402

WIQ = "api://workiq.svc.cloud.microsoft/.default"


# ── de scope-regel ──────────────────────────────────────────────────────────

def test_offline_access_gaat_er_altijd_bij():
    """Zonder `offline_access` geeft Entra geen verversingstoken, en dan is de
    inlog na een uur weg — precies het soort storing dat je pas merkt als je
    het nodig hebt."""
    regel = entra_app_login.scope_regel([WIQ])
    assert "offline_access" in regel.split()
    assert WIQ in regel.split()


def test_een_losse_string_mag_ook():
    """De UI stuurt een lijst, de code een enkele scope. Allebei moeten werken,
    anders krijg je een scope-regel als 'a p i : / /' — per teken gesplitst."""
    assert WIQ in entra_app_login.scope_regel(WIQ).split()


def test_geen_dubbele_scopes():
    regel = entra_app_login.scope_regel([WIQ, WIQ, "offline_access"]).split()
    assert regel.count(WIQ) == 1
    assert regel.count("offline_access") == 1


# ── de tokencache ───────────────────────────────────────────────────────────

def test_een_geldig_token_wordt_hergebruikt():
    """Elke aanroep opnieuw inwisselen is een extra netwerkronde per tool-call
    én een teller die bij Entra oploopt."""
    payload = {"tenant_id": "t", "client_id": "c", "refresh_token": "r",
               "tokens": {WIQ: {"access_token": "abc", "expires_at": time.time() + 3600}}}
    token, nieuw = asyncio.run(entra_app_login.token_voor_scope(payload, WIQ))
    assert token == "abc"
    assert nieuw is payload, "niets te bewaren, dus ook geen schrijfactie"


def test_zonder_inlog_een_duidelijke_melding():
    payload = {"tenant_id": "t", "client_id": "c"}
    try:
        asyncio.run(entra_app_login.token_voor_scope(payload, WIQ))
    except RuntimeError as exc:
        assert "device-code" in str(exc)
    else:
        raise AssertionError("dit hoort te falen zolang er niet is ingelogd")


def test_elke_api_zijn_eigen_token():
    """Eén inlog bedient meerdere API's: een verversingstoken hangt aan de APP
    en aan wat je die app hebt toegestaan, niet aan één resource. De cache moet
    dus per scope zijn — anders krijgt Graph het Work IQ-token en omgekeerd,
    en dan klopt de audience niet."""
    graph = "https://graph.microsoft.com/.default"
    payload = {"tenant_id": "t", "client_id": "c", "refresh_token": "r",
               "tokens": {WIQ: {"access_token": "wiq", "expires_at": time.time() + 3600},
                          graph: {"access_token": "gr", "expires_at": time.time() + 3600}}}
    assert asyncio.run(entra_app_login.token_voor_scope(payload, WIQ))[0] == "wiq"
    assert asyncio.run(entra_app_login.token_voor_scope(payload, graph))[0] == "gr"


def test_een_bijna_verlopen_token_telt_als_verlopen():
    """Een token dat over tien seconden verloopt, is bij een trage aanroep al
    dood voordat hij aankomt. De marge is er om dat te voorkomen."""
    payload = {"tenant_id": "t", "client_id": "c", "refresh_token": "",
               "tokens": {WIQ: {"access_token": "bijna", "expires_at": time.time() + 30}}}
    try:
        asyncio.run(entra_app_login.token_voor_scope(payload, WIQ))
    except RuntimeError:
        pass    # geen refresh_token, dus hij probeert te verversen en struikelt — dat is het bewijs
    else:
        raise AssertionError("een token binnen de marge hoort ververst te worden")


# ── de catalogusregel ───────────────────────────────────────────────────────

def test_work_iq_staat_in_de_catalogus_met_de_juiste_scope():
    """Een ARM-token wordt door Work IQ geweigerd omdat de audience niet klopt;
    zonder deze regel installeert de server met de standaardscope en faalt elke
    aanroep met een melding die daar niet naar verwijst."""
    entry = find("work-iq")
    assert entry is not None
    assert entry["base_url"] == "https://workiq.svc.cloud.microsoft/mcp"
    assert entry["token_scope"] == WIQ
    assert entry["provenance"] == "data", "dit levert mail- en chatinhoud, geen metadata"


def test_de_beheerdersstappen_staan_erbij():
    """De gebruiker moet dit zelf kunnen inregelen of gericht kunnen aanvragen.
    Twee dingen worden altijd vergeten en moeten dus met naam genoemd staan:
    de openbare clientstroom en de beheerderstoestemming."""
    stappen = " ".join(find("work-iq")["setup"]["stappen"]).lower()
    assert "openbare clientstromen" in stappen
    assert "beheerderstoestemming" in stappen
    assert "workiqagent.ask" in stappen
    assert "schrijfacties" in stappen, "Work IQ staat standaard op alleen-lezen"


def test_de_frontend_noemt_dezelfde_valkuilen():
    """De uitleg staat op twee plekken — de catalogus en het inlogscherm — en
    die mogen niet uiteenlopen: dan volg je de ene lijst en mist de andere stap.

    Overgeslagen waar de frontend niet meegekopieerd is (de testcontainer heeft
    alleen backend/); daar is niets te vergelijken en een harde fout zou alleen
    maar zeggen dat het pad anders is."""
    pad = Path(__file__).resolve().parents[2] / "frontend/src/components/EntraAppLogin.tsx"
    if not pad.exists():
        pytest.skip("frontend niet aanwezig in deze omgeving")
    bron = pad.read_text(encoding="utf-8").lower()
    assert "openbare clientstromen" in bron
    assert "beheerderstoestemming" in bron
    assert "workiqagent.ask" in bron


# ── de scope komt bij de aanroep terecht ────────────────────────────────────

def test_de_server_bepaalt_voor_welke_api_het_token_geldt():
    """Het profiel is de identiteit, de server zegt vóór welke API. Eén inlog
    bedient zo meerdere API's; zonder dit veld zou je per API een apart profiel
    moeten aanmaken en opnieuw moeten inloggen."""
    import inspect

    from services.mcp import mcp_client

    bron = inspect.getsource(mcp_client._resolve_auth_headers)
    assert "server.token_scope" in bron
    assert "management.azure.com" in bron, "zonder lab-server blijft ARM de standaard"


# ── verdwenen tools (de tweede helft van KRI-44) ────────────────────────────

def test_een_sync_zet_verdwenen_tools_uit():
    """Microsoft hernoemde in de Fabric MCP-server alle `onelake_*`-tools van
    underscores naar streepjes (`onelake_list_tables` → `onelake_list-tables`).
    De sync voegde de nieuwe namen toe en liet de oude staan, dus LabX bleef 35
    tools aanbieden die niet meer bestonden: de agent kreeg ze in zijn lijst en
    daarna "The tool was not found". `datafactory_*` en `core_*` werkten in
    dezelfde sessie wél — die waren niet hernoemd, en juist dat maakte het
    raadselachtig.

    De `seen`-verzameling bestond al; er werd alleen nooit iets mee gedaan."""
    import inspect

    from services.mcp import mcp_client

    bron = inspect.getsource(mcp_client.sync_tools)
    assert "Tool.remote_name.notin_(seen)" in bron, "verdwenen tools moeten uit"
    assert "row.is_enabled = False" in bron, "uitzetten, niet verwijderen"
    assert "if seen:" in bron, "een lege lijst is een storing, geen lege server"


# ── het pad ernaartoe (wat er ontbrak) ──────────────────────────────────────

def test_een_taskgroup_fout_wordt_leesbaar():
    """Wat er in de UI stond toen Work IQ niet werkte: "unhandled errors in a
    TaskGroup (1 sub-exception)". De MCP-client draait zijn verbinding in een
    TaskGroup en die verpakt élke fout in een groep; de echte oorzaak — een
    401, een DNS-fout, een proces dat niet start — zit een laag dieper. Zonder
    uitpakken is de melding letterlijk onbruikbaar."""
    from services.mcp.mcp_client import _leesbare_fout

    groep = ExceptionGroup("unhandled errors in a TaskGroup",
                           [RuntimeError("HTTP 401 Unauthorized")])
    uit = _leesbare_fout(groep)
    assert "401" in uit
    assert "TaskGroup" not in uit


def test_geneste_groepen_worden_ook_uitgepakt():
    from services.mcp.mcp_client import _leesbare_fout

    binnenin = ExceptionGroup("inner", [ValueError("scope klopt niet")])
    uit = _leesbare_fout(ExceptionGroup("outer", [binnenin]))
    assert "scope klopt niet" in uit


def test_een_profiel_dat_niet_past_zegt_dat():
    """Het echte geval: een msal_bundle-profiel gekoppeld aan Work IQ. Dat gaf
    stilletjes GEEN headers terug — de aanroep ging zonder Authorization de
    deur uit en kwam terug als 401. Zwijgen is hier het slechtste antwoord: de
    gebruiker kan dit zelf oplossen zodra hij weet wát er mis is."""
    import asyncio
    from types import SimpleNamespace

    from services.azure.azure_mcp_auth import ProfielPastNiet, bearer_header_for_profile

    profiel = SimpleNamespace(kind="msal_bundle", name="Beeminds", secret_encrypted=None)
    try:
        asyncio.run(bearer_header_for_profile(profiel, scope=WIQ, db=None))
    except ProfielPastNiet as exc:
        assert "Beeminds" in str(exc)
        assert "Entra-app" in str(exc), "zeg ook wat het WEL moet zijn"
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"verkeerde soort fout: {type(exc).__name__}: {exc}")
    else:
        raise AssertionError("een msal_bundle hoort hier te weigeren, niet stil te blijven")
