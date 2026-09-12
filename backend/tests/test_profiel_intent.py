"""Beveiligingsprofiel en verklaarde intentie.

Op 12-09-2026 wees de audit twee dingen aan die niet met méér regels op te
lossen waren. Om 11:18 werd uitvoer geweigerd die pure tabelmetadata was — een
kolommenlijst uit INFORMATION_SCHEMA. En Presidio maskeerde in een lijst van
Fabric-pipelines van alles weg, tot "PID" en "python3" aan toe. Beide keren
keek de guard naar iets zonder te weten waar hij naar keek.

Twee antwoorden, die elkaar aanvullen:

- Het **profiel** zegt in welke wereld dit lab werkt. In een Fabric-omgeving is
  technische uitvoer de norm; namenherkenning levert daar alleen ruis op.
- De **intentie** zegt wat de agent ophaalt. Dat verruimt wat er op het
  commando mag, maar de uitvoer wordt eraan getoetst — anders is het een
  vrijbrief in plaats van een verklaring.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.lab import intent as intents  # noqa: E402
from services.lab import profielen  # noqa: E402
from services.lab.classifier import beoordeel_opdracht, beoordeel_uitvoer  # noqa: E402

# Twintig regels die eruitzien als klantgegevens; genoeg voor beide drempels.
KLANTRIJEN = "\n".join(
    f"{i},Jansen,Amsterdam,2024-01-{i:02d},€ {i*13},45" for i in range(1, 21))
# Drie regels: onder de gewone drempel van tien, boven de strenge van drie.
DRIE_RIJEN = "\n".join(
    f"{i},Jansen,Amsterdam,2024-01-{i:02d},€ {i*13},45" for i in range(1, 4))


# ── het profiel ─────────────────────────────────────────────────────────────

def test_fabric_laat_delta_metadata_door(guard_db):
    """`DESCRIBE HISTORY` en numRecords zijn het transactielogboek: hoeveel
    rijen er zijn, niet wat erin staat."""
    cmd = "spark.sql('DESCRIBE HISTORY delta.`/Tables/klanten`').show()"
    generiek = beoordeel_opdracht(guard_db, cmd, profiel_key="generiek")
    fabric = beoordeel_opdracht(guard_db, cmd, profiel_key="fabric")
    assert fabric["actie"] == "doorgelaten"
    assert "Fabric" in (fabric.get("reden") or "")
    # En in een generiek lab verandert er niets aan het oude gedrag.
    assert generiek["actie"] == beoordeel_opdracht(guard_db, cmd)["actie"]


def test_fabric_zet_de_namenherkenning_uit(guard_db):
    """Het geval uit de audit: een procestabel waarin "PID" en "python3" als
    plaatsnaam werden gemaskeerd, 35 keer in één antwoord."""
    ps = ("PID   TTY      TIME     CMD\n"
          "1     ?        00:00:01 python3\n"
          "42    pts/0    00:00:00 bash\n")
    uit = beoordeel_uitvoer(guard_db, ps, profiel_key="fabric")
    assert uit["actie"] == "doorgelaten"
    assert uit["tekst"] == ps
    # En het bewijs dat dit iets deed: op een echte Nederlandse zin maskeert
    # dezelfde detector nog steeds. Zonder deze tweede helft zou deze test ook
    # slagen als Presidio helemaal niet draaide.
    zin = ("Beste Jan de Vries, uw aanvraag is in behandeling genomen door ons "
           "kantoor in Rotterdam en wij nemen binnen vijf werkdagen contact op.")
    assert "[person verborgen]" in beoordeel_uitvoer(guard_db, zin)["tekst"]


def test_een_onbekend_profiel_valt_terug_op_generiek():
    """Een typefout in een instelling mag de guard niet uitschakelen én niet
    laten omvallen."""
    assert profielen.profiel("bestaat-niet")["label"] == profielen.PROFIELEN["generiek"]["label"]
    assert profielen.profiel(None)["presidio"] is True


def test_ook_in_fabric_blijft_een_dataset_dicht(guard_db):
    """Het profiel verschuift wat als NORMAAL geldt, niet wat er beschermd
    wordt. Veertig klantrijen zijn overal veertig klantrijen."""
    uit = beoordeel_uitvoer(guard_db, KLANTRIJEN, profiel_key="fabric")
    assert uit["actie"] == "geblokkeerd"


# ── de intentie ─────────────────────────────────────────────────────────────

def test_verklaarde_metadata_verruimt_de_opdracht(guard_db):
    """Precies de weigering van 11:18: een SELECT op INFORMATION_SCHEMA."""
    cmd = "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='klant'"
    zonder = beoordeel_opdracht(guard_db, cmd, intent=None)
    met = beoordeel_opdracht(guard_db, cmd, intent="metadata")
    assert met["actie"] == "doorgelaten"
    # (deze gaat ook zonder verklaring al door dankzij de structuurregel; de
    #  verklaring mag daar in elk geval niets aan verslechteren)
    assert zonder["actie"] == "doorgelaten"


def test_verklaring_verruimt_een_echte_blokkade(guard_db):
    cmd = "spark.sql('SELECT klantnaam FROM lakehouse.klanten').show()"
    assert beoordeel_opdracht(guard_db, cmd)["actie"] == "geweigerd"
    verruimd = beoordeel_opdracht(guard_db, cmd, intent="metadata")
    assert verruimd["actie"] == "doorgelaten"
    assert any(f["actie"] == "verruimd" for f in verruimd["findings"]), \
        "de verruiming hoort in de bevindingen te staan, niet stilletjes te gebeuren"


def test_een_verkeerde_verklaring_kost_je_de_uitvoer(guard_db):
    """De kern van declare-and-verify: zeg je 'metadata' en er komen klantrijen
    uit, dan gaat het alsnog dicht."""
    uit = beoordeel_uitvoer(guard_db, KLANTRIJEN, intent="metadata")
    assert uit["actie"] == "geblokkeerd"
    mismatch = uit["intent_mismatch"]
    assert mismatch["intent"] == "metadata" and mismatch["blokkeren"] is True
    assert "klantgegevens" in mismatch["gevonden"]


def test_wie_verklaart_wordt_scherper_nagekeken(guard_db):
    """Drie recordregels zijn normaal te weinig om op te blokkeren — een
    foutmelding met wat velden erin haalt dat ook. Maar wie zelf zei dat er
    geen gegevens uit komen, wordt aan die drempel gehouden."""
    assert beoordeel_uitvoer(guard_db, DRIE_RIJEN)["actie"] != "geblokkeerd"
    assert beoordeel_uitvoer(guard_db, DRIE_RIJEN, intent="telling")["actie"] == "geblokkeerd"


def test_een_mismatch_die_niet_blokkeert_staat_wel_in_de_audit(guard_db):
    """Een e-mailadres in een logregel hoeft geen heel antwoord te kosten —
    maskeren beschermt daar volledig. Maar de afwijking hoort zichtbaar te
    blijven, anders is er niets te controleren."""
    tekst = "INFO build ok, contact: beheerder@voorbeeld.nl\nexit 0"
    uit = beoordeel_uitvoer(guard_db, tekst, intent="code")
    assert uit["actie"] == "gemaskeerd"
    assert "beheerder@voorbeeld.nl" not in uit["tekst"]
    assert uit["intent_mismatch"]["blokkeren"] is False


def test_zonder_verklaring_verandert_er_niets(guard_db):
    tekst = "INFO build ok\nexit 0"
    assert beoordeel_uitvoer(guard_db, tekst)["actie"] == "doorgelaten"
    assert beoordeel_uitvoer(guard_db, tekst, intent=None).get("intent_mismatch") is None


@pytest.mark.parametrize("waarde,verwacht", [
    ("metadata", "metadata"), ("Telling", "telling"), ("TELLING", "telling"),
    ("", None), (None, None), ("verzin-maar-wat", None),
])
def test_een_onzinnige_verklaring_telt_gewoon_niet(waarde, verwacht):
    """Het model mag hier van alles neerzetten; alles wat niet in de lijst
    staat betekent 'niets verklaard', en dan gelden de regels zoals ze staan."""
    assert intents.normaliseer(waarde) == verwacht


def test_klantdata_verklaren_verruimt_niets(guard_db):
    cmd = "spark.sql('SELECT klantnaam FROM lakehouse.klanten').show()"
    assert beoordeel_opdracht(guard_db, cmd, intent="klantdata")["actie"] == "geweigerd"
