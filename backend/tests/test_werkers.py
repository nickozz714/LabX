"""Werkers die erbij komen moeten ook echt bruikbaar zijn.

Drie dingen gingen hier mis, en ze versterkten elkaar. Uit de logs van
11-09-2026 op het Krimpenerwaard-lab:

    16:12:32  Werker bijgezet werker=3
    16:12:32  Inrichten loopt al                 <- werker 3 blijft 'pending'
    16:42:48  Lab ingericht werkers=2            <- die ronde kende hem niet
    16:46:01  Ongebruikte werker opgeruimd werker=3
    16:46:43  Werker bijgezet werker=3           <- en weer van voren af aan

1. Een werker die BIJKOMT terwijl er al een inrichtronde loopt, wordt door die
   ronde niet gezien en blijft op 'pending'. Hij is dan niet claimbaar, dus de
   autoscaler leek op te schalen zonder dat er iets bij kwam.
2. De ongebruikt-klok liep vanaf het AANMAKEN. Inrichten duurt hier een
   halfuur (Playwright haalt een browser binnen), en al die tijd doet de
   werker per definitie niets — dus was hij "30 minuten ongebruikt" precies
   toen hij eindelijk klaar was, en ruimde de reaper hem meteen op.
3. Een werker waarvan pakketten MISLUKTEN gold als bruikbaar. Een agent die
   daarop landde, meldde dat hij zijn tools kwijt was.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.lab.lab_service import LabService  # noqa: E402


def _werker(index, prov, status="running", container="c"):
    return SimpleNamespace(id=index, index=index, provision_status=prov,
                           status=status, container_id=container)


def _svc(werkers):
    svc = LabService.__new__(LabService)
    svc.ensure_workers = lambda p: werkers
    return svc


def _keys(werkers):
    return [w.index for w in werkers]


def test_werker_1_doet_altijd_mee():
    """Dat is de container van het lab zelf. Hem uitsluiten zou elk vers lab
    minutenlang blokkeren, en een lab met één mislukt pakket helemaal
    onbruikbaar maken."""
    for prov in ("pending", "error", "ok", None):
        svc = _svc([_werker(1, prov)])
        assert _keys(svc.claimbare_werkers(SimpleNamespace())) == [1]


def test_extra_werker_die_nog_inricht_krijgt_geen_werk():
    svc = _svc([_werker(1, "ok"), _werker(2, "pending")])
    assert _keys(svc.claimbare_werkers(SimpleNamespace())) == [1]


def test_extra_werker_met_mislukte_pakketten_krijgt_geen_werk():
    """De kern van 'de agent heeft ineens zijn tools niet meer': dit was
    vroeger wél claimbaar, want er werd alleen op 'pending' getoetst."""
    svc = _svc([_werker(1, "ok"), _werker(2, "error"), _werker(3, "ok")])
    assert _keys(svc.claimbare_werkers(SimpleNamespace())) == [1, 3]


def test_werker_zonder_inrichting_krijgt_geen_werk():
    """provision_status None = we weten het niet. Bij werker 1 is dat historie,
    bij een extra werker is het een reden om hem niet te vertrouwen."""
    svc = _svc([_werker(1, "ok"), _werker(2, None)])
    assert _keys(svc.claimbare_werkers(SimpleNamespace())) == [1]


def test_lab_zonder_netwerk_mag_wel():
    """Zo'n lab richt bewust niets in ('skipped'); dat is geen fout."""
    svc = _svc([_werker(1, "skipped"), _werker(2, "skipped")])
    assert _keys(svc.claimbare_werkers(SimpleNamespace())) == [1, 2]


def test_gestopte_werker_telt_niet_mee():
    svc = _svc([_werker(1, "ok"), _werker(2, "ok", status="stopped"),
                _werker(3, "ok", container=None)])
    assert _keys(svc.claimbare_werkers(SimpleNamespace())) == [1]


# ── de tweede ronde na een werker die onderweg bijkwam ──────────────────────

def test_verzoek_tijdens_een_lopende_ronde_wordt_onthouden():
    """Niet weggooien maar inplannen: anders blijft de werker die halverwege
    bijkwam voorgoed op 'pending' staan."""
    from services.lab import lab_service as ls

    ls._PROVISION_NOGMAALS.discard("lab-x")
    ls._PROVISION_TASKS["lab-x"] = SimpleNamespace(done=lambda: False)
    try:
        assert ls.provision_in_background("lab-x") is False
        assert "lab-x" in ls._PROVISION_NOGMAALS
    finally:
        ls._PROVISION_TASKS.pop("lab-x", None)
        ls._PROVISION_NOGMAALS.discard("lab-x")


def test_na_afloop_wordt_de_tweede_ronde_gestart(monkeypatch):
    from services.lab import lab_service as ls

    gestart = []
    monkeypatch.setattr(ls, "provision_in_background",
                        lambda lab_id, **kw: gestart.append(lab_id))
    ls._PROVISION_NOGMAALS.add("lab-y")
    ls._PROVISION_TASKS["lab-y"] = SimpleNamespace(done=lambda: True)
    try:
        ls._na_inrichten("lab-y")
        assert gestart == ["lab-y"]
        assert "lab-y" not in ls._PROVISION_NOGMAALS
        assert "lab-y" not in ls._PROVISION_TASKS
    finally:
        ls._PROVISION_TASKS.pop("lab-y", None)
        ls._PROVISION_NOGMAALS.discard("lab-y")


def test_zonder_verzoek_gebeurt_er_niets_na_afloop(monkeypatch):
    from services.lab import lab_service as ls

    gestart = []
    monkeypatch.setattr(ls, "provision_in_background",
                        lambda lab_id, **kw: gestart.append(lab_id))
    ls._PROVISION_TASKS["lab-z"] = SimpleNamespace(done=lambda: True)
    try:
        ls._na_inrichten("lab-z")
        assert gestart == []
    finally:
        ls._PROVISION_TASKS.pop("lab-z", None)


# ── de opruimer ─────────────────────────────────────────────────────────────
#
# `asyncio.run` en geen pytest-asyncio: die zit niet in de afhankelijkheden van
# dit project, en één losse lus per test is hier goedkoper dan een plugin die
# iedereen apart moet installeren.

class NepDb:
    def __init__(self, labs, werkers):
        self._labs, self.werkers = labs, werkers
        self.verwijderd = []
    def query(self, model):
        return NepQuery(self._labs)
    def delete(self, obj):
        self.verwijderd.append(obj.index)
        self.werkers.remove(obj)
    def commit(self):
        pass


class NepQuery:
    def __init__(self, rijen): self.rijen = rijen
    def filter(self, *a, **k): return self
    def all(self): return self.rijen


def _reaper(werkers, bezet=()):
    """LabService met alleen wat reap_idle_workers aanraakt."""
    lab = SimpleNamespace(id="lab-1", name="lab", min_workers=1, max_workers=3,
                          worker_count=len(werkers), updated_at="")
    db = NepDb([lab], werkers)
    svc = LabService.__new__(LabService)
    svc.db = db
    svc.workers = lambda lab_id: werkers
    svc._bezette_werkers = lambda lab_id: set(bezet)
    svc._close_lab_mcp_sessions = _niets
    svc.runtime = SimpleNamespace(remove=_niets)
    return svc, db, lab


async def _niets(*a, **k):
    return None


def _oud(w, minuten=90):
    from datetime import datetime, timedelta, timezone
    w.last_used_at = (datetime.now(timezone.utc) - timedelta(minutes=minuten)).isoformat()
    w.created_at = w.last_used_at
    return w


def _vers(w):
    from datetime import datetime, timezone
    w.last_used_at = datetime.now(timezone.utc).isoformat()
    w.created_at = w.last_used_at
    return w


def test_werker_die_nog_inricht_wordt_niet_opgeruimd():
    """Precies de dure lus: inrichten duurt een halfuur, dus een werker die
    NET klaar is, is volgens de klok al een halfuur ongebruikt. Hem dan
    opruimen betekent dat halve uur downloaden weggooien en opnieuw beginnen."""
    werkers = [_vers(_werker(1, "ok")), _oud(_werker(2, "pending"))]
    svc, db, lab = _reaper(werkers)
    assert asyncio.run(svc.reap_idle_workers(idle_minutes=30)) == 0
    assert db.verwijderd == []


def test_mislukte_werker_gaat_meteen_weg():
    """Hij krijgt toch geen werk meer, maar telt wél mee voor het plafond —
    dus blokkeert hij het bijzetten van een werker die het wél doet."""
    werkers = [_vers(_werker(1, "ok")), _vers(_werker(2, "error"))]
    svc, db, lab = _reaper(werkers)
    assert asyncio.run(svc.reap_idle_workers(idle_minutes=30)) == 1
    assert db.verwijderd == [2]


def test_stille_werker_wordt_wel_opgeruimd():
    werkers = [_vers(_werker(1, "ok")), _oud(_werker(2, "ok"))]
    svc, db, lab = _reaper(werkers)
    assert asyncio.run(svc.reap_idle_workers(idle_minutes=30)) == 1
    assert db.verwijderd == [2]


def test_werker_met_werk_erop_blijft():
    werkers = [_vers(_werker(1, "ok")), _oud(_werker(2, "ok"))]
    svc, db, lab = _reaper(werkers, bezet=(2,))
    assert asyncio.run(svc.reap_idle_workers(idle_minutes=30)) == 0


def test_nooit_onder_de_ondergrens():
    werkers = [_oud(_werker(1, "ok"))]
    svc, db, lab = _reaper(werkers)
    assert asyncio.run(svc.reap_idle_workers(idle_minutes=30)) == 0
    assert db.verwijderd == []
