"""Het bevestigingswoord van de spraaklaag.

Dit is het enige dat tussen een verkeerd verstane zin en een actie in een
klantsysteem staat, dus het verdient scherpere tests dan de rest.

De regel die hier wordt vastgelegd: het woord moet het HELE antwoord zijn.
Zou "bevat het woord" volstaan, dan bevestigt "Henk, kun jij even kijken?" een
openstaande actie terwijl je tegen een collega praat — en juist een naam als
bevestigingswoord maakt dat waarschijnlijk.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.voice.bevestiging import (  # noqa: E402
    STANDAARD_WOORD, hoe_bevestigen, is_bevestiging, is_verlopen, normaliseer, vervalt_op,
)


def test_het_woord_alleen_bevestigt():
    assert is_bevestiging("Henk", "Henk")
    assert is_bevestiging("henk", "Henk")          # hoofdletters doen niet mee
    assert is_bevestiging("  Henk.  ", "Henk")      # spaties en leestekens ook niet


def test_het_woord_in_een_zin_bevestigt_NIET():
    """De kern van de hele regel."""
    assert not is_bevestiging("Henk, kun jij even kijken?", "Henk")
    assert not is_bevestiging("ja Henk", "Henk")
    assert not is_bevestiging("doe maar Henk", "Henk")
    assert not is_bevestiging("ik had het over Henk van de helpdesk", "Henk")


def test_ja_bevestigt_niet_als_het_woord_anders_is():
    """Precies de winst ten opzichte van 'ja': een omstander die toevallig
    'ja' zegt doet niets meer."""
    assert not is_bevestiging("ja", "Henk")
    assert not is_bevestiging("ja hoor", "Henk")
    assert not is_bevestiging("prima", "Henk")


def test_leegte_en_onzin_bevestigen_nooit():
    for antwoord in (None, "", "   ", "...", "ehm"):
        assert not is_bevestiging(antwoord, "Henk")


def test_zonder_ingesteld_woord_geldt_de_standaard_en_dat_is_niet_ja():
    assert is_bevestiging(STANDAARD_WOORD, None)
    assert not is_bevestiging("ja", None)


def test_accenten_mogen_de_actie_niet_bepalen():
    """Een transcriptie haalt 'Henk' en 'Hènk' door elkaar; dat verschil mag
    niet uitmaken of je actie doorgaat."""
    assert is_bevestiging("Hènk", "Henk")
    assert is_bevestiging("Henk", "Hènk")


def test_normaliseren_trekt_spaties_samen():
    assert normaliseer("  Twee   woorden! ") == "twee woorden"
    assert normaliseer(None) == ""


def test_een_woord_van_twee_delen_werkt_ook():
    assert is_bevestiging("rode kool", "Rode Kool")
    assert not is_bevestiging("rode", "Rode Kool")


def test_verval_is_twintig_seconden():
    nu = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    grens = vervalt_op(nu)
    assert not is_verlopen(grens, nu + timedelta(seconds=19))
    assert is_verlopen(grens, nu + timedelta(seconds=21))


def test_een_kapotte_of_lege_vervaltijd_telt_als_verlopen():
    """Bij twijfel niet uitvoeren: een actie waarvan we de klok niet kennen,
    hoort niet alsnog af te gaan."""
    assert is_verlopen(None)
    assert is_verlopen("geen datum")


def test_de_assistent_vertelt_hoe_je_bevestigt_per_stand():
    assert "scherm" in hoe_bevestigen("klik", "Henk")
    assert "Henk" not in hoe_bevestigen("klik", "Henk")   # roepen heeft hier geen zin
    assert "Henk" in hoe_bevestigen("spraak", "Henk")
    assert "scherm" not in hoe_bevestigen("spraak", "Henk")
    beide = hoe_bevestigen("beide", "Henk")
    assert "Henk" in beide and "scherm" in beide
