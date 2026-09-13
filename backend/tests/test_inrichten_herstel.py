"""Inrichten dat is blijven hangen, komt er bij de volgende start uit.

Op 13-09-2026 sneuvelde het inrichten van Krimpenerwaard midden in een deploy.
`provision_status` gaat op "running" bij de start van een ronde en pas aan het
EIND naar ok/error, dus het lab bleef voorgoed denken dat het bezig was en
werker 2 en 3 bleven op "pending" staan. Zo'n werker is niet claimbaar: de
autoscaler zette wel containers bij, maar er kwam nooit werk op. Er was niets in
LabX dat daar uit kwam — alleen handmatig ingrijpen hielp.

Bij het opstarten is er per definitie geen ronde meer aan de gang: de
takenlijst leeft in het geheugen van het proces dat net weg is. Alles wat dan
nog op "bezig" staat, is dus afgebroken.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import services.lab.lab_service as lab_service  # noqa: E402


def test_het_herstel_zit_in_de_opstartreconciliatie():
    """De plek doet ertoe: `reconcile_on_start` draait al bij elke start en
    doet precies dit soort opruimwerk voor containers. Een aparte scheduler-taak
    zou pas na het eerste interval aanslaan — en dan heeft de autoscaler
    ondertussen al werkers bijgezet die niemand kan claimen."""
    import inspect

    bron = inspect.getsource(lab_service.LabService.reconcile_on_start)
    assert 'Lab.provision_status == "running"' in bron
    assert 'p.provision_status = "pending"' in bron, "terug naar pending, niet naar error"
    assert "provision_in_background(p.id)" in bron


def test_pending_en_niet_error():
    """Er is niets stuk, het is alleen niet af — en inrichten is idempotent, dus
    de volgende ronde maakt het gewoon af. Op "error" zetten zou een lab
    markeren als kapot terwijl er alleen een deploy tussendoor kwam, en dat is
    precies het soort valse alarm dat mensen leren negeren."""
    import inspect

    bron = inspect.getsource(lab_service.LabService.reconcile_on_start)
    blok = bron[bron.index('Lab.provision_status == "running"'):]
    assert 'provision_status = "error"' not in blok[:600]


def test_ook_een_werker_die_alleen_bleef_hangen():
    """De spiegelvorm: het lab denkt dat het klaar is, maar een werker die
    tijdens een lopende ronde werd bijgezet stond niet in de lijst die díé ronde
    afwerkte. Normaal vangt `_na_inrichten` dat op — maar ook dat leeft in het
    geheugen van een proces dat kan sneuvelen."""
    import inspect

    bron = inspect.getsource(lab_service.LabService.reconcile_on_start)
    assert 'LabWorker.provision_status == "pending"' in bron
    assert 'Lab.provision_status.notin_(("running",))' in bron


def test_een_lab_zonder_netwerk_wordt_met_rust_gelaten():
    """Daar valt niets binnen te halen; een ronde inplannen zou alleen maar een
    mislukking opleveren."""
    import inspect

    bron = inspect.getsource(lab_service.LabService.reconcile_on_start)
    assert bron.count("p.allow_network") >= 2


def test_provision_in_background_doet_niets_zonder_event_loop():
    """Bij een script of een test is er geen loop. Dan hoort hij te zwijgen en
    False terug te geven, niet om te vallen — anders sleept elke test die dit
    pad raakt een halve backend mee."""
    assert lab_service.provision_in_background("lab-bestaat-niet") is False
