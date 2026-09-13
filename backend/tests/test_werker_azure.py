"""Een bijgezette werker krijgt de az-sessie ook (KRI-44).

Het ticket beschreef het als een intermitterende storing: `az account
get-access-token` gaf soms "Please run 'az login'" en soms niet, en
`/root/.azure` leek bij elke containerstart leeg. Het was geen intermittentie
maar een gat. Gemeten op 13-09-2026 in het Krimpenerwaard-lab:

    werker 1  … msal_token_cache.json ✓
    werker 2  … msal_token_cache.json ✓
    werker 3  … ONTBREEKT

Een lab is sinds de autoscaler geen container meer maar een groep, maar het
doorzetten van de Azure-sessie ging nog naar één container: die van het lab
zelf. Landde een run op werker 3, dan was er geen sessie; landde hij op werker
1, dan wel. Vandaar dat het op toeval leek.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.lab.lab_service import LabService  # noqa: E402


def _werker(wid, index, status="running", container=True):
    return SimpleNamespace(id=wid, index=index, status=status,
                           container_id=f"c{wid}" if container else None)


class _Db:
    def __init__(self, profiel):
        self._profiel = profiel

    def get(self, _model, _key):
        return self._profiel


def _svc(werkers, profiel=SimpleNamespace(kind="msal_bundle")):
    svc = LabService.__new__(LabService)
    svc.db = _Db(profiel)
    svc.workers = lambda _lab_id: werkers
    return svc


class _Sync:
    """Legt vast welke werkers een sessie kregen."""

    def __init__(self):
        self.gezien = []

    def __call__(self, db):
        buiten = self

        class _Svc:
            async def sync(self, _pid, *, target, lab_id, worker_id=None, az_dir=None):
                buiten.gezien.append(worker_id)
                return {"ok": True}
        return _Svc()


def _draai(svc, lab, sync, worker_id=None):
    import services.azure.azure_profile_service as aps
    origineel = aps.AzureProfileService
    aps.AzureProfileService = sync
    try:
        asyncio.run(svc._sync_azure_profile_into_lab(lab, worker_id=worker_id))
    finally:
        aps.AzureProfileService = origineel


LAB = SimpleNamespace(id="lab-1", azure_profile_id=7)


def test_alle_draaiende_werkers_krijgen_de_sessie():
    """De kern van KRI-44: niet alleen werker 1."""
    sync = _Sync()
    svc = _svc([_werker(4, 1), _werker(5, 2), _werker(6, 3)])
    _draai(svc, LAB, sync)
    assert sync.gezien == [4, 5, 6]


def test_een_werker_zonder_container_wordt_overgeslagen():
    """Een werker die nog aan het starten is heeft niets om in te schrijven;
    dat is geen fout en mag de andere werkers niet blokkeren."""
    sync = _Sync()
    svc = _svc([_werker(4, 1), _werker(5, 2, status="creating", container=False),
                _werker(6, 3)])
    _draai(svc, LAB, sync)
    assert sync.gezien == [4, 6]


def test_een_net_bijgezette_werker_krijgt_alleen_zichzelf():
    """Bij het bijschalen hoeven de bestaande werkers geen schrijfronde: die
    hebben de sessie al, en een lab met acht werkers zou anders acht keer
    dezelfde bestanden krijgen."""
    sync = _Sync()
    svc = _svc([_werker(4, 1), _werker(5, 2), _werker(6, 3)])
    _draai(svc, LAB, sync, worker_id=6)
    assert sync.gezien == [6]


def test_een_lab_zonder_werkerrijen_valt_terug_op_het_lab_zelf():
    """Labs van vóór de autoscaler hebben geen werkerrijen. Die mogen hier niet
    stilletjes overgeslagen worden — dan verliezen ze juist wat ze wel hadden."""
    sync = _Sync()
    svc = _svc([])
    _draai(svc, LAB, sync)
    assert sync.gezien == [None], "één sync, naar de container van het lab zelf"


def test_zonder_profiel_gebeurt_er_niets():
    sync = _Sync()
    svc = _svc([_werker(4, 1)])
    _draai(svc, SimpleNamespace(id="lab-1", azure_profile_id=None), sync)
    assert sync.gezien == []


def test_alleen_een_msal_bundle_kan_gesynct_worden():
    """Een service principal logt in de container zelf in en een bearer heeft
    helemaal geen az-sessie; die hier meenemen levert alleen foutmeldingen."""
    sync = _Sync()
    svc = _svc([_werker(4, 1)], profiel=SimpleNamespace(kind="service_principal"))
    _draai(svc, LAB, sync)
    assert sync.gezien == []


def test_een_mislukte_werker_stopt_de_rest_niet():
    """Best-effort per werker. Eén container die net wegviel mag de andere twee
    niet zonder sessie laten — dat zou het probleem juist vergroten."""
    sync = _Sync()

    class _Stuk(_Sync):
        def __call__(self, db):
            buiten = self

            class _Svc:
                async def sync(self, _pid, *, target, lab_id, worker_id=None, az_dir=None):
                    buiten.gezien.append(worker_id)
                    if worker_id == 5:
                        raise RuntimeError("container weg")
                    return {"ok": True}
            return _Svc()

    stuk = _Stuk()
    svc = _svc([_werker(4, 1), _werker(5, 2), _werker(6, 3)])
    _draai(svc, LAB, stuk)
    assert stuk.gezien == [4, 5, 6]


def test_az_login_schrijft_in_de_opgegeven_werker():
    """Het pad dat de sync gebruikt moet de werker ook echt kunnen kiezen;
    zonder dit argument landde alles in de container van het lab zelf."""
    import inspect

    bron = inspect.getsource(LabService.az_login)
    assert "worker_id" in bron
    assert "self._require_running(p, worker_id)" in bron
