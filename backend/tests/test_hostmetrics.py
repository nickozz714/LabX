"""De host in beeld, en niet bijschalen als hij vol zit.

Aanleiding: de server draaide op 13-09-2026 met een load van 4.28 op vier
kernen en zes van de vierentwintig GB vrij — dus al over de rand — terwijl er
in LabX niets te zien was dat daar iets over zei. De autoscaler zet vanzelf
werkers bij, en elke werker is een eigen container met dezelfde limieten; twee
labs met vier werkers zijn acht containers. Zonder rem is dat precies hoe een
machine stilletjes omvalt.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.lab import hostmetrics  # noqa: E402

GB = 1024 ** 3


class _Db:
    """Genoeg database om `toezeggingen` te laten rekenen."""

    def __init__(self, labs, werkers_per_lab=1):
        self._labs = labs
        self._werkers = werkers_per_lab

    def query(self, model):
        naam = getattr(model, "__name__", str(model))
        if naam == "Lab":
            return SimpleNamespace(all=lambda: self._labs)
        return SimpleNamespace(filter=lambda *_a, **_k: SimpleNamespace(count=lambda: self._werkers))


def _lab(naam, mem_mb, cpu, status="running"):
    return SimpleNamespace(id=naam, name=naam, mem_limit_mb=mem_mb, cpu_limit=cpu, status=status)


# ── wat de labs claimen ─────────────────────────────────────────────────────

def test_alleen_draaiende_labs_tellen_mee():
    """Een gestopt lab kost niets. Het meetellen zou elke machine als vol
    aanmerken zodra je er een paar labs op hebt staan."""
    db = _Db([_lab("aan", 2048, 1.0), _lab("uit", 8192, 4.0, status="stopped")])
    claim = hostmetrics.toezeggingen(db)
    assert claim["labs_draaiend"] == 1
    assert claim["geheugen"] == 2048 * 1024 * 1024
    assert claim["cpu"] == 1.0


def test_elke_werker_telt_apart():
    """Dit is de kern van het probleem: een lab met vier werkers is vier
    containers met elk hetzelfde plafond, niet één."""
    db = _Db([_lab("groot", 2048, 1.0)], werkers_per_lab=4)
    claim = hostmetrics.toezeggingen(db)
    assert claim["geheugen"] == 4 * 2048 * 1024 * 1024
    assert claim["cpu"] == 4.0
    assert claim["per_lab"][0]["werkers"] == 4


# ── de waarschuwingen ───────────────────────────────────────────────────────

def _waarschuwingen(deel_gebruikt, load_per_kern, claim_geheugen=0, totaal=24 * GB,
                    kernen=4, claim_cpu=0.0, swap_totaal=0, swap_gebruikt=0):
    mem = {"totaal": totaal, "deel_gebruikt": deel_gebruikt,
           "swap_totaal": swap_totaal, "swap_gebruikt": swap_gebruikt}
    claim = {"geheugen": claim_geheugen, "cpu": claim_cpu}
    return hostmetrics._waarschuwingen(mem, load_per_kern, claim, kernen, None)


def test_een_rustige_machine_geeft_geen_ruis():
    assert _waarschuwingen(0.40, 0.3) == []


def test_geheugen_bijna_vol_is_ernstig():
    uit = _waarschuwingen(0.96, 0.3)
    assert [w["ernst"] for w in uit] == ["hoog"]
    assert "swappen" in uit[0]["tekst"]


def test_krap_geheugen_waarschuwt_zachter():
    uit = _waarschuwingen(0.88, 0.3)
    assert [w["ernst"] for w in uit] == ["midden"]


def test_de_load_telt_per_kern():
    """4.28 op vier kernen is elke kern bezet; 4.28 op zestien kernen is niets.
    Zonder die deling waarschuw je op een groot systeem continu en op een klein
    systeem nooit. Dit is de stand van de server op 13-09-2026."""
    assert _waarschuwingen(0.4, 4.28 / 4)[0]["ernst"] == "midden"
    assert _waarschuwingen(0.4, 4.28 / 16) == []


def test_pas_bij_echt_wachtrijgedrag_wordt_het_ernstig():
    """Elke kern bezet is werken; anderhalf keer bezet betekent dat er meer
    staat te wachten dan de machine kan bedienen, en dan wordt ALLES trager —
    ook wat al draait."""
    assert _waarschuwingen(0.4, 1.6)[0]["ernst"] == "hoog"


def test_overboeking_wordt_apart_gemeld():
    """Niet hetzelfde als 'vol': overboekt gaat goed tot alle labs tegelijk
    gaan werken. Dat verdient een aparte, zachtere melding."""
    uit = _waarschuwingen(0.4, 0.3, claim_geheugen=30 * GB)
    assert [w["onderwerp"] for w in uit] == ["overboeking"]
    assert uit[0]["ernst"] == "midden"


def test_volle_swap_valt_op_ook_bij_gezond_ogend_geheugen():
    """De stand van de server op 13-09-2026: 74% geheugen — ogenschijnlijk ruim —
    met de swap voor 100% vol. Alleen naar het momentane percentage kijken mist
    dat de kernel al pagina's naar de schijf heeft moeten duwen."""
    uit = _waarschuwingen(0.74, 0.9, swap_totaal=2 * GB, swap_gebruikt=2 * GB)
    assert [w["onderwerp"] for w in uit] == ["swap"]


def test_een_machine_zonder_swap_geeft_geen_swapmelding():
    """Delen door nul is hier geen theoretisch geval: veel containers en
    cloud-images draaien bewust zonder swap."""
    assert _waarschuwingen(0.4, 0.3, swap_totaal=0, swap_gebruikt=0) == []


def test_elke_waarschuwing_zegt_wat_je_kunt_doen():
    """Een waarschuwing zonder handelingsperspectief is ruis."""
    uit = _waarschuwingen(0.96, 1.6, claim_geheugen=30 * GB)
    assert len(uit) == 3
    for w in uit:
        assert len(w["tekst"]) > 60, w


# ── de rem op de autoscaler ─────────────────────────────────────────────────

def test_geen_werker_erbij_als_het_geheugen_op_is(monkeypatch):
    monkeypatch.setattr(hostmetrics, "_meminfo",
                        lambda: {"MemTotal": 24 * GB, "MemAvailable": 1 * GB})
    monkeypatch.setattr(hostmetrics, "_loadavg", lambda: [0.2, 0.2, 0.2])
    uit = hostmetrics.past_er_nog_een_bij(None, geheugen_mb=4096, cpu=1.0)
    assert uit["ok"] is False
    assert "beschikbaar" in uit["reden"]


def test_ook_niet_als_het_er_net_wel_in_past_maar_de_rand_raakt(monkeypatch):
    """Precies genoeg is niet genoeg: op 95% begint de kernel processen af te
    breken, en dan valt er een lab om zonder duidelijke oorzaak."""
    monkeypatch.setattr(hostmetrics, "_meminfo",
                        lambda: {"MemTotal": 24 * GB, "MemAvailable": 2 * GB})
    monkeypatch.setattr(hostmetrics, "_loadavg", lambda: [0.2, 0.2, 0.2])
    uit = hostmetrics.past_er_nog_een_bij(None, geheugen_mb=1800, cpu=1.0)
    assert uit["ok"] is False


def test_een_drukke_maar_niet_volle_machine_mag_wel_met_een_waarschuwing(monkeypatch):
    """Load is geen reden om te weigeren — traag werk is werk. Wel om het te
    zeggen, want het gaat ten koste van wat er al draait."""
    monkeypatch.setattr(hostmetrics, "_meminfo",
                        lambda: {"MemTotal": 24 * GB, "MemAvailable": 16 * GB})
    monkeypatch.setattr(hostmetrics, "_loadavg", lambda: [8.0, 8.0, 8.0])
    monkeypatch.setattr(hostmetrics, "_kernen", lambda: 4)
    uit = hostmetrics.past_er_nog_een_bij(None, geheugen_mb=2048, cpu=1.0)
    assert uit["ok"] is True
    assert uit["ernst"] == "midden"


def test_een_rustige_machine_laat_gewoon_bijschalen(monkeypatch):
    monkeypatch.setattr(hostmetrics, "_meminfo",
                        lambda: {"MemTotal": 24 * GB, "MemAvailable": 18 * GB})
    monkeypatch.setattr(hostmetrics, "_loadavg", lambda: [0.5, 0.5, 0.5])
    monkeypatch.setattr(hostmetrics, "_kernen", lambda: 4)
    uit = hostmetrics.past_er_nog_een_bij(None, geheugen_mb=2048, cpu=1.0)
    assert uit == {"ok": True, "ernst": "geen", "reden": ""}


def test_zonder_meminfo_blokkeert_hij_niets(monkeypatch):
    """Kan hij de machine niet meten, dan hoort hij niet de baas te spelen:
    een lege /proc mag geen reden zijn om het werk stil te leggen."""
    monkeypatch.setattr(hostmetrics, "_meminfo", lambda: {})
    monkeypatch.setattr(hostmetrics, "_loadavg", lambda: [])
    assert hostmetrics.past_er_nog_een_bij(None, geheugen_mb=2048, cpu=1.0)["ok"] is True
