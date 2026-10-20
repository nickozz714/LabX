"""Geheimen uit een logbestand, voordat het een scherm of een model bereikt.

Dit bestaat door een gemeten geval: het startlog van een branch-lab bevatte de
Key Vault-responses van de applicatie op DEBUG-niveau, met het value-veld
erin. De kluismaskering van LabX ving die niet -- ze kwamen uit een vault die
LabX niet beheert. Wat overblijft is de VORM herkennen.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.logmaskering import VERVANGING, maskeer_log  # noqa: E402


def test_de_key_vault_vorm_die_dit_nodig_maakte():
    regel = ('[DEBUG] {"value":"pxVpZsYcuWABlFG4nlH1zf8nk1fOXnG8HNvF8B6a'
             'xc68Dv90jeWeAH0o7wiVOdH2spfUE0eD1NREACDb7UZvkw==","id":'
             '"https://kv.vault.azure.net/secrets/COSMOS-KEY/f2cc"}')

    uit = maskeer_log(regel)

    assert "pxVpZsYcuWAB" not in uit
    assert VERVANGING in uit
    # De omliggende tekst blijft staan: je wilt zien WELKE sleutel er is
    # opgehaald, en daar heb je de waarde niet voor nodig.
    assert "COSMOS-KEY" in uit


def test_een_sleutel_in_een_omgevingsvariabele():
    uit = maskeer_log("API_KEY=be1cb3a26be54706a351619ffc188d4d")

    assert "be1cb3a26be54706" not in uit
    assert uit.startswith("API_KEY=")


def test_een_bearer_token():
    uit = maskeer_log("Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsIng1")

    assert "eyJ0eXAiOiJKV1Qi" not in uit
    assert "Bearer" in uit


def test_gewone_logregels_blijven_leesbaar():
    """Anders is het venster waardeloos: je leest een log om te zien wat er
    misging, niet om naar blokjes te kijken."""
    log = ("ready in 102 ms\n"
           "[ERROR] 401 https://api.powerbi.com/v1.0/myorg/admin/capacities\n"
           "azure:identity:error ManagedIdentityCredential => getToken() => ERROR.\n"
           "  Attempted to use the IMDS endpoint, but it is not available.")

    assert maskeer_log(log) == log


def test_een_lange_sleutelbrok_zonder_label_gaat_er_ook_uit():
    """Niet elk geheim komt met een naam ervoor. Deze kant mag onnauwkeurig
    zijn: liever een versienummer onleesbaar dan een sleutel leesbaar."""
    uit = maskeer_log("token is AbCdEf0123456789AbCdEf0123456789AbCdEf0123456789 klaar")

    assert "AbCdEf0123456789AbCdEf0123456789" not in uit
    assert uit.startswith("token is ") and uit.endswith(" klaar")


def test_leeg_blijft_leeg():
    assert maskeer_log("") == ""
