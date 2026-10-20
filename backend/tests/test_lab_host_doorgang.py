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


def test_een_doorgang_die_lang_stil_is_gaat_vanzelf_dicht(agent, monkeypatch):
    """Een doorgang die blijft bestaan wijst op een dag naar een lab dat er
    niet meer is. Dat is de reden dat dit vanzelf opruimt."""
    monkeypatch.setattr(agent, "STIL_MAX_SECONDEN", 0)
    doorgangen = agent.Doorgangen()
    try:
        doorgangen.open(18093)
        time.sleep(0.05)
        doorgangen.ruim_op()
        assert doorgangen.lijst() == []
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
