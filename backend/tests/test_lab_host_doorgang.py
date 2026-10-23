"""De doorgang die de agent op een lab-host kan openen.

Deze agent zet geen commando's door maar een TCP-poort, en dat is precies waar
je nauwkeurig in moet zijn: wat hier binnenkomt is een getal, en een getal
zonder grens is een doorgang naar alles wat op die machine luistert.
"""
import importlib.util
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

_PAD = Path(__file__).resolve().parents[2] / "lab-host" / "labx_lab_host.py"


@pytest.fixture(scope="module")
def agent():
    spec = importlib.util.spec_from_file_location("labx_lab_host", _PAD)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["labx_lab_host"] = mod
    spec.loader.exec_module(mod)
    return mod


def _luisteraar(poort):
    """Een nepdienst op de loopback, zoals een lab er een is."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", poort))
    s.listen(4)

    def bedien():
        while True:
            try:
                k, _ = s.accept()
            except OSError:
                return
            k.sendall(b"hallo uit het lab")
            k.close()

    threading.Thread(target=bedien, daemon=True).start()
    return s


def test_een_poort_buiten_het_labbereik_gaat_er_niet_doorheen(agent):
    """22 is ssh, 5432 is je database. Die horen hier niet open te kunnen."""
    doorgangen = agent.Doorgangen()

    for verboden in (22, 80, 5432, 17999, 19000):
        with pytest.raises(ValueError) as fout:
            doorgangen.open(verboden)
        assert "bereik" in str(fout.value)


def test_een_labpoort_wordt_doorgezet_en_geeft_het_lab_terug(agent):
    poort = 18090
    dienst = _luisteraar(poort)
    doorgangen = agent.Doorgangen()
    try:
        luik = doorgangen.open(poort)
        assert luik.poort == poort + agent.POORT_VERSCHUIVING

        with socket.create_connection(("127.0.0.1", luik.poort), timeout=5) as k:
            assert b"hallo uit het lab" in k.recv(100)
    finally:
        doorgangen.alles_dicht()
        dienst.close()


def test_twee_keer_openen_geeft_dezelfde_doorgang(agent):
    """Anders loopt het aantal open poorten op met elke keer dat je op
    Openen drukt, en dan is 'wat staat er open' niet meer te beantwoorden."""
    doorgangen = agent.Doorgangen()
    try:
        een = doorgangen.open(18091)
        twee = doorgangen.open(18091)
        assert een is twee
        assert len(doorgangen.lijst()) == 1
    finally:
        doorgangen.alles_dicht()


def test_sluiten_maakt_de_poort_echt_dicht(agent):
    doorgangen = agent.Doorgangen()
    luik = doorgangen.open(18092)
    assert doorgangen.sluit(18092) is True
    time.sleep(1.2)          # de luisterlus kijkt elke seconde of hij weg mag

    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", luik.poort), timeout=2)


def test_een_stille_doorgang_naar_een_DRAAIEND_lab_blijft(agent, monkeypatch):
    """Stilte is geen reden om op te ruimen.

    Gemeten op 23-10-2026: een tunnelscript stond 46 uur open naar de API van
    een lab. Die API werd een halve dag niet gebruikt, de doorgang ging na
    vier uur dicht, en toen hij nodig was kwam er "localhost didn't send any
    data". De API draaide gewoon; alleen de weg ernaartoe was opgeruimd.
    """
    monkeypatch.setattr(agent, "STIL_VOOR_CONTROLE", 0)
    poort = 18093
    dienst = _luisteraar(poort)
    doorgangen = agent.Doorgangen()
    try:
        doorgangen.open(poort)
        time.sleep(0.05)
        doorgangen.ruim_op()
        assert [q["lab_poort"] for q in doorgangen.lijst()] == [poort]
    finally:
        doorgangen.alles_dicht()
        dienst.close()


def test_een_doorgang_naar_een_DODE_poort_gaat_dicht(agent, monkeypatch):
    """Dat is waar het opruimen voor bedoeld was: LabX hergebruikt
    poortnummers, en een doorgang naar een verdwenen lab mag geen nieuw lab
    gaan aanwijzen."""
    monkeypatch.setattr(agent, "STIL_VOOR_CONTROLE", 0)
    doorgangen = agent.Doorgangen()
    try:
        doorgangen.open(18094)      # hier luistert niets
        time.sleep(0.05)
        doorgangen.ruim_op()
        assert doorgangen.lijst() == []
    finally:
        doorgangen.alles_dicht()


def test_kort_stil_wordt_niet_eens_nagekeken(agent):
    """Anders opent elke tik een socket per doorgang, voor niets."""
    doorgangen = agent.Doorgangen()
    try:
        doorgangen.open(18099)      # niets luistert, maar hij is net geopend
        doorgangen.ruim_op()
        assert [q["lab_poort"] for q in doorgangen.lijst()] == [18099]
    finally:
        doorgangen.alles_dicht()


def test_zonder_token_neemt_de_ingang_niets_aan(agent):
    """De ingang bestaat pas als LabX een token heeft gegeven, en dat geeft
    LabX pas nadat een mens de koppelcode heeft bevestigd."""
    agent._AgentHandler.token = ""

    class _Nep:
        headers = {"Authorization": "Bearer wat-dan-ook"}

    assert agent._AgentHandler._mag(_Nep()) is False

    agent._AgentHandler.token = "geheim"
    assert agent._AgentHandler._mag(_Nep()) is False
    _Nep.headers = {"Authorization": "Bearer geheim"}
    assert agent._AgentHandler._mag(_Nep()) is True


def test_een_stille_verbinding_wordt_niet_afgekapt(agent, monkeypatch):
    """Dit is de bug die eruitzag als een front-end die blijft herstarten.

    `create_connection(timeout=...)` laat die time-out op de socket staan, dus
    erfde elke recv hem en ging een verbinding die even stil was eruit. Een
    HMR-websocket is precies zo'n stille verbinding: die werd afgekapt, de
    dev-server-client verbond opnieuw en herlaadde de pagina. Rechtstreeks op
    de machine gebeurde het niet, want daar zit de doorgang er niet tussen.
    """
    monkeypatch.setattr(agent, "VERBIND_TIJD", 1)
    poort = 18095
    dienst = _echo(poort)
    doorgangen = agent.Doorgangen()
    try:
        luik = doorgangen.open(poort)
        with socket.create_connection(("127.0.0.1", luik.poort), timeout=10) as k:
            k.sendall(b"hoi")
            assert k.recv(100) == b"hoi"
            time.sleep(2.5)          # langer stil dan VERBIND_TIJD
            k.sendall(b"nog steeds hier")
            assert k.recv(100) == b"nog steeds hier", "de verbinding is afgekapt"
    finally:
        doorgangen.alles_dicht()
        dienst.close()


def _echo(poort):
    """Een dienst die teruggeeft wat je stuurt, en blijft luisteren."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", poort))
    s.listen(4)

    def bedien():
        while True:
            try:
                k, _ = s.accept()
            except OSError:
                return
            threading.Thread(target=_praat, args=(k,), daemon=True).start()

    threading.Thread(target=bedien, daemon=True).start()
    return s


def _praat(k):
    try:
        while True:
            brok = k.recv(1024)
            if not brok:
                return
            k.sendall(brok)
    except OSError:
        pass
    finally:
        k.close()


def test_wat_open_stond_komt_terug_na_een_herstart(agent, tmp_path, monkeypatch):
    """Het tunnelscript op jouw machine wijst naar een vast nummer en weet
    niet dat deze agent even weg was. Zonder dit "dropt de tunnel de
    connectie" zonder dat iemand kan zien waarom."""
    monkeypatch.setattr(agent, "DOORGANG_BESTAND", tmp_path / "doorgangen.json")

    eerste = agent.Doorgangen()
    try:
        eerste.open(18096)
        assert (tmp_path / "doorgangen.json").exists()
    finally:
        eerste.alles_dicht()

    # Een echte herstart is een nieuw proces; hier zit de oude luisterlus nog
    # in dezelfde, en die laat zijn socket pas na een tel los.
    time.sleep(1.5)

    tweede = agent.Doorgangen()
    try:
        tweede.zet_terug()
        assert [q["lab_poort"] for q in tweede.lijst()] == [18096]
    finally:
        tweede.alles_dicht()


def test_een_oude_lijst_wordt_niet_teruggezet(agent, tmp_path, monkeypatch):
    """LabX hergebruikt vrijgekomen poortnummers, dus een doorgang van vorige
    week kan intussen naar een ander lab wijzen. Liever niets dan het
    verkeerde."""
    import json as _json

    bestand = tmp_path / "doorgangen.json"
    bestand.write_text(_json.dumps({
        "bijgewerkt": time.time() - (agent.ONTHOUDEN_UREN + 1) * 3600,
        "poorten": [18097],
    }))
    monkeypatch.setattr(agent, "DOORGANG_BESTAND", bestand)

    d = agent.Doorgangen()
    try:
        d.zet_terug()
        assert d.lijst() == []
    finally:
        d.alles_dicht()


def test_sluiten_wordt_ook_onthouden(agent, tmp_path, monkeypatch):
    """Anders komt een doorgang die jij dichtzette na een herstart terug."""
    import json as _json

    bestand = tmp_path / "doorgangen.json"
    monkeypatch.setattr(agent, "DOORGANG_BESTAND", bestand)

    d = agent.Doorgangen()
    d.open(18098)
    d.sluit(18098)

    assert _json.loads(bestand.read_text())["poorten"] == []
