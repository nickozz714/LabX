"""services/lab/clusters.py — development clusters.

Een cluster is een groep containers die bij elkaar horen omdat ze dezelfde
applicatie draaien: vaste diensten die blijven staan, en branch-labs die komen
en gaan. Zie docs/CLUSTERS.md voor het waarom.

Deze laag doet stap 1: het cluster zelf, zijn netwerk, en de vaste diensten.
Branch-labs komen daar bovenop en zijn gewone labs -- dat is met opzet, zodat
werkers, data-guard, geheimen en verplaatsen niet twee keer bestaan.

DRIE DINGEN DIE DE REST VERKLAREN:

- **Eén cluster, één machine.** De vaste diensten en de branch-labs moeten
  elkaar over een lokaal netwerk kunnen vinden; verspreid over machines kan
  dat niet. Welke machine is wel vrij -- dezelfde lab-hosts als overal.

- **Namen in plaats van poorten.** Binnen het cluster praat alles op
  containernaam (`ih-cosmos:8081`). Poorten publiceren is alleen voor JOU,
  en dan op de loopback van die machine -- net als bij labpoorten.

- **Een dienst is wegwerpbaar, zijn data niet.** De container mag je opnieuw
  opbouwen vanaf een andere branch; het volume eronder blijft. Anders is
  "even opnieuw opbouwen" een beslissing met gevolgen die niemand overzag.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from models.cluster import Cluster, ClusterDienst, ClusterPlan
from services.lab import git_auth as _git

log = get_logger(__name__)


def _nu() -> str:
    return datetime.now(timezone.utc).isoformat()


def _laatste_zinnige_regel(tekst: str) -> str:
    """De regel die zegt wat er mis ging, niet de ruis ervoor.

    Docker schrijft voortgang en fouten door elkaar. Een mislukte start gaf
    daardoor een scherm vol "Pulling fs layer" waar nergens uit bleek wat er
    aan de hand was. We nemen de laatste regel die geen voortgang is.
    """
    # Een regel met "error" erin wint: docker sluit een gebruiksfout af met
    # "See 'docker run --help'.", en dan is de laatste regel juist de minst
    # bruikbare.
    ruis = ("pulling fs layer", "waiting", "downloading", "extracting",
            "verifying checksum", "download complete", "pull complete",
            "already exists", "digest:", "status:")
    regels = [r.strip() for r in (tekst or "").splitlines() if r.strip()]
    echt = [r for r in regels
            if not any(r.lower().startswith(x) or x in r.lower()[:40] for x in ruis)]
    fouten = [r for r in echt if "error" in r.lower()]
    if fouten:
        return fouten[-1]
    return (echt[-1] if echt else (regels[-1] if regels else "onbekende fout"))


def _veilige_naam(naam: str) -> str:
    """Een naam die docker accepteert als netwerk- en containernaam."""
    kaal = re.sub(r"[^a-zA-Z0-9_.-]+", "-", (naam or "").strip().lower()).strip("-")
    return kaal or "cluster"


#: Soorten waarvan een leeg vinkje GEEN afwijzing is.
#:
#: - `applicatie` is een vaststelling; daar staat sowieso geen vinkje bij.
#: - `lab_image` is een of-of: er kan er maar een aan staan, dus de andere
#:   drie zijn niet afgewezen -- je hebt er een gekozen. Ze alsnog "door jou
#:   afgewezen" noemen maakt dat label betekenisloos, en dan leest niemand
#:   het nog waar het wel toe doet.
_GEEN_AFWIJZING = ("applicatie", "lab_image")


#: Sleutels in de omgeving van een CONTAINER die een inloggegeven zijn.
_WACHTWOORD_SLEUTEL = re.compile(r"(PASSWORD|PASSWD|_PASS$|ROOT_PASSWORD|_SECRET|_KEY)$", re.I)

#: Waarden die geen wachtwoord zijn maar een belofte: leeg, een plaatshouder,
#: of een voorbeeld uit de documentatie van het project.
_ZWAK = re.compile(r"^\s*$|^<.*>$|^\$\{.*\}$|^(labx|dev|test|secret|password|changeme|example)[-_a-z0-9]*$", re.I)


#: `${VAR:-standaard}` en `${VAR-standaard}` -- de enige vorm waarvan de
#: bedoelde waarde zwart op wit in de tekst staat.
_MET_STANDAARD = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*:?-(.*)\}$", re.DOTALL)
#: Elke andere vorm van shell-expansie.
_EXPANSIE = re.compile(r"^\$\{.*\}$|^\$[A-Za-z_][A-Za-z0-9_]*$", re.DOTALL)


def _zonder_expansie(omgeving: Dict[str, str], *, leeg_laten: bool = True) -> Dict[str, str]:
    """Shell-expansie uit een container-omgeving halen.

    Een compose-bestand vult `${DB_NAME:-slimmer_politiek}` in; `docker run -e`
    doet dat niet. Die tekst komt dan LETTERLIJK in de container terecht, en
    MySQL maakt een database die zo heet -- of weigert de naam. Het ziet er
    bovendien uit alsof het klopt, want in de compose stond het er net zo.

    Staat de bedoelde waarde in de tekst (`:-standaard`), dan nemen we die.
    Staat hij er niet, dan hangt het van de sleutel af:

    - een INLOGGEGEVEN blijft staan, leeg. Hij moet er zijn, en de stap
      hierna vult hem met een gegenereerd wachtwoord. Hem hier weggooien
      betekende dat MySQL zonder wachtwoord startte;
    - al het andere valt weg. Een image gebruikt dan zijn eigen standaard, en
      dat is altijd beter dan starten met een naam die niemand bedoeld heeft.
    """
    uit: Dict[str, str] = {}
    for sleutel, waarde in (omgeving or {}).items():
        treffer = _MET_STANDAARD.match(waarde)
        if treffer:
            uit[sleutel] = treffer.group(1)
        elif _EXPANSIE.match(waarde):
            if leeg_laten and _WACHTWOORD_SLEUTEL.search(sleutel):
                uit[sleutel] = ""
            else:
                log.infox("Omgevingsvariabele met shell-expansie weggelaten",
                          sleutel=sleutel)
        else:
            uit[sleutel] = waarde
    return uit


def _wachtwoord() -> str:
    """Een wachtwoord dat niemand hoeft te onthouden.

    LabX start deze container zelf, dus LabX kiest de inloggegevens. Dat is
    niet alleen veiliger dan `labx:labx` -- het haalt ook de vraag weg die
    nergens over ging: het model maakte een MySQL aan, verzon er op eigen
    houtje een wachtwoord bij, en vroeg de gebruiker daarna wat het
    wachtwoord van de database moest zijn. Die kon dat niet weten; de
    database bestond nog niet.
    """
    import secrets

    return secrets.token_urlsafe(24)


def _naam_uit_url(repo_url: str) -> str:
    """De repo-naam als clusternaam, zodat je er geen hoeft te verzinnen."""
    naam = (repo_url or "").rstrip("/").split("/")[-1].removesuffix(".git")
    return naam.strip() or "cluster"


def _dienst_container(c: Cluster, d: ClusterDienst) -> str:
    """Hoe deze dienst bij de Docker-daemon heet.

    Niet gelijk aan `d.naam`: die is de DNS-naam op het clusternetwerk en
    hoort in elk cluster `cosmos` te mogen heten. De containernaam is globaal,
    en twee clusters die hem delen vechten erom -- het starten van de tweede
    maakt de eerste dood, want starten begint met `docker rm -f` op de naam.
    """
    return f"labx-c{c.id}-{_veilige_naam(d.naam)}"[:120]


def _hernoem_host(waarde: str, oud: str, nieuw: str) -> str:
    """De dienstnaam vervangen waar hij als HOSTNAAM staat.

    Een lab-omgeving komt in drie vormen binnen en alle drie moeten mee:

        COSMOS_ENDPOINT=https://cosmos:8081      na een //
        AMQP_URL=amqp://gast:gast@rabbitmq:5672  na een @
        OLLAMA_HOST=ollama:11434                 kaal, aan het begin

    Die laatste was de eerste die misging. Alleen op `//` en `@` letten lijkt
    genoeg tot er een dienst langskomt die zonder schema wordt opgegeven, en
    dan wijst de variabele stilletjes naar een host die op dit netwerk niet
    bestaat.

    Alleen op een echte grens, zodat `ollama-proxy` niet half hernoemd wordt.
    """
    patroon = rf"(^|//|@){re.escape(oud)}(?=[:/]|$)"
    return re.sub(patroon, lambda m: m.group(1) + nieuw, waarde)


def _voorvoegsel(clusternaam: str) -> str:
    """Een kort voorvoegsel voor de diensten van dit cluster: `ih-cosmos`.

    De naam van een dienst is ook zijn DNS-naam op het clusternetwerk. Zonder
    voorvoegsel heet de emulator van elk cluster `cosmos`, en dan is het de
    volgorde van aanmaken die bepaalt welke je krijgt zodra er een tweede
    cluster op dezelfde machine staat.
    """
    delen = [d for d in re.split(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])", clusternaam or "") if d]
    letters = "".join(d[0] for d in delen).lower()
    if len(letters) >= 2:
        return letters[:4]
    return (re.sub(r"[^a-z0-9]", "", (clusternaam or "").lower())[:3]) or "c"


class ClusterService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── lezen ────────────────────────────────────────────────────────────────

    def get_or_404(self, cluster_id: int) -> Cluster:
        rij = self.db.get(Cluster, int(cluster_id))
        if rij is None:
            raise HTTPException(status_code=404, detail="Cluster niet gevonden")
        return rij

    def lijst(self) -> List[Cluster]:
        return self.db.query(Cluster).order_by(Cluster.naam).all()

    def diensten(self, cluster_id: int) -> List[ClusterDienst]:
        return (self.db.query(ClusterDienst)
                .filter(ClusterDienst.cluster_id == cluster_id)
                .order_by(ClusterDienst.naam).all())

    def _runtime(self, c: Cluster):
        from services.lab.lab_service import LabService

        svc = LabService(self.db)
        return svc._docker_op_host(c.host_id) if c.host_id else svc.runtime

    # ── aanmaken en wijzigen ─────────────────────────────────────────────────

    def maak(self, *, naam: str, host_id: Optional[int] = None,
             **velden: Any) -> Cluster:
        naam = (naam or "").strip()
        if not naam:
            raise HTTPException(status_code=400, detail="Naam is verplicht")
        if self.db.query(Cluster).filter(Cluster.naam == naam).first():
            raise HTTPException(status_code=409,
                                detail=f"Er is al een cluster '{naam}'.")
        if host_id:
            # Dezelfde controle als bij een lab: een machine die niet
            # gekoppeld is, krijgt geen werk.
            from services.lab.lab_service import LabService
            LabService(self.db)._docker_op_host(int(host_id))

        rij = Cluster(naam=naam[:120], host_id=host_id or None,
                      netwerk=f"labx-cluster-{_veilige_naam(naam)}"[:120],
                      status="nieuw", omgeving={}, created_at=_nu(), updated_at=_nu())
        self._zet_velden(rij, velden)
        self.db.add(rij)
        self.db.commit()
        self.db.refresh(rij)
        log.infox("Cluster aangemaakt", naam=rij.naam, netwerk=rij.netwerk,
                  host_id=rij.host_id)
        return rij

    def wijzig(self, cluster_id: int, **velden: Any) -> Cluster:
        rij = self.get_or_404(cluster_id)
        self._zet_velden(rij, velden)
        rij.updated_at = _nu()
        self.db.commit()
        self.db.refresh(rij)
        return rij

    @staticmethod
    def _zet_velden(rij: Cluster, velden: Dict[str, Any]) -> None:
        for sleutel in ("repo_url", "repo_token_geheim", "lab_image",
                        "start_script", "env_profiel", "notitie"):
            if sleutel in velden:
                waarde = velden[sleutel]
                setattr(rij, sleutel, (str(waarde).strip() or None) if waarde else None)
        if "omgeving" in velden and isinstance(velden["omgeving"], dict):
            rij.omgeving = {str(k): str(v) for k, v in velden["omgeving"].items()}

    async def verwijder(self, cluster_id: int) -> Dict[str, Any]:
        """Het cluster weg, inclusief wat het bij Docker heeft achtergelaten.

        Alleen de rijen uit de database halen is niet genoeg en voelt alleen
        zo: de containers van de vaste diensten blijven dan draaien en het
        netwerk blijft staan. Dat kost geheugen dat niemand nog kan vinden, en
        bij een volgend cluster met dezelfde naam erft het netwerk van zijn
        voorganger.

        Opruimen dat halverwege struikelt is erger dan opruimen dat iets laat
        staan: wat bij Docker misgaat wordt gemeld en teruggegeven, maar de
        rijen gaan hoe dan ook weg.
        """
        from models.lab import Lab

        rij = self.get_or_404(cluster_id)
        labs = [n for (n,) in self.db.query(Lab.name)
                .filter(Lab.cluster_id == cluster_id).all()]
        if labs:
            raise HTTPException(
                status_code=409,
                detail=(f"Er horen nog branch-labs bij dit cluster: "
                        f"{', '.join(labs)}. Ruim die eerst op — anders blijven "
                        f"ze achter zonder vaste diensten om mee te praten."))

        runtime = self._runtime(rij)
        klachten: List[str] = []
        for d in self.diensten(cluster_id):
            if d.container_id:
                try:
                    await runtime.remove(d.container_id)
                except Exception as exc:  # noqa: BLE001
                    klachten.append(f"container {d.naam}: {str(exc)[:160]}")
            self.db.delete(d)
        try:
            await runtime.verwijder_netwerk(rij.netwerk)
        except Exception as exc:  # noqa: BLE001
            klachten.append(f"netwerk {rij.netwerk}: {str(exc)[:160]}")

        from models.cluster_secret import ClusterSecret

        # De kluis van dit cluster gaat mee. Achterblijvende geheimen van een
        # cluster dat niet meer bestaat zijn onvindbaar en onverwijderbaar --
        # en ze staan er nog wel.
        (self.db.query(ClusterSecret)
         .filter(ClusterSecret.cluster_id == cluster_id).delete())
        self.db.delete(rij)
        self.db.commit()
        if klachten:
            log.warningx("Cluster verwijderd, maar niet alles opgeruimd",
                         cluster=rij.naam, klachten="; ".join(klachten))
        return {"ok": True, "niet_opgeruimd": klachten}

    # ── vaste diensten ───────────────────────────────────────────────────────

    def voeg_dienst_toe(self, cluster_id: int, *, naam: str, image: str,
                        poorten: Optional[List[int]] = None,
                        omgeving: Optional[Dict[str, str]] = None) -> ClusterDienst:
        c = self.get_or_404(cluster_id)
        naam = _veilige_naam(naam)
        if not image.strip():
            raise HTTPException(status_code=400, detail="Een dienst heeft een image nodig.")
        bestaat = (self.db.query(ClusterDienst)
                   .filter(ClusterDienst.cluster_id == c.id,
                           ClusterDienst.naam == naam).first())
        if bestaat:
            raise HTTPException(
                status_code=409,
                detail=(f"'{naam}' bestaat al in dit cluster. De naam is ook de "
                        f"DNS-naam op het netwerk, dus hij moet uniek zijn."))
        rij = ClusterDienst(cluster_id=c.id, naam=naam, image=image.strip()[:255],
                            poorten=[int(p) for p in (poorten or [])][:8],
                            omgeving={str(k): str(v) for k, v in (omgeving or {}).items()},
                            status="gestopt", created_at=_nu(), updated_at=_nu())
        self.db.add(rij)
        self.db.commit()
        self.db.refresh(rij)
        return rij

    #: Vanaf welk nummer de poorten van clusters op de machine lopen. Hoog
    #: genoeg om niets te overschrijven wat er al draait, en op één hoop zodat
    #: je in één oogopslag ziet wat van LabX is.
    POORT_BASIS = 18000

    def _bezette_poorten(self, behalve_dienst: Optional[int] = None,
                         behalve_lab: Optional[str] = None) -> set:
        """Alle machinepoorten die dit LabX al vergeven heeft.

        Diensten en branch-labs delen één bereik, want ze delen één machine.
        Apart tellen zou twee diensten op hetzelfde nummer zetten zodra er
        ook labs zijn.
        """
        from models.lab import Lab

        bezet = set()
        for ander in self.db.query(ClusterDienst).all():
            if ander.id != behalve_dienst:
                bezet.update(int(v) for v in (ander.poort_map or {}).values())
        for lab in self.db.query(Lab).filter(Lab.cluster_id.isnot(None)).all():
            if lab.id != behalve_lab:
                bezet.update(int(v) for v in (lab.poort_map or {}).values())
        return bezet

    def _ken_poorten_toe(self, d: ClusterDienst) -> Dict[int, int]:
        """{containerpoort: machinepoort}, stabiel zolang de dienst bestaat.

        De containerpoort op de machine publiceren leek simpel, maar botst op
        alles wat daar al luistert -- bij de eerste proef liep de emulator op
        8081 vast op "address already in use". Een eigen bereik lost dat op en
        geeft meteen het adres dat je wilt: http://machine:18000.
        """
        huidig = {int(k): int(v) for k, v in (d.poort_map or {}).items()}
        bezet = self._bezette_poorten(behalve_dienst=d.id)
        volgende = self.POORT_BASIS
        uit: Dict[int, int] = {}
        for p in [int(x) for x in (d.poorten or [])]:
            if p in huidig and huidig[p] not in bezet:
                uit[p] = huidig[p]
            else:
                while volgende in bezet or volgende in uit.values():
                    volgende += 1
                uit[p] = volgende
            bezet.add(uit[p])
        return uit

    async def start_dienst(self, dienst_id: int) -> ClusterDienst:
        d = self._dienst_of_404(dienst_id)
        c = self.get_or_404(d.cluster_id)
        rt = self._runtime(c)
        await rt.ensure_network_named(c.netwerk)

        # Eerst de oude weg, anders botst de naam. Een dienst is wegwerpbaar;
        # zijn volume niet, en dat blijft dus staan.
        if d.container_id:
            try:
                await rt.remove(d.container_id, timeout=120)
            except Exception as exc:  # noqa: BLE001
                log.warningx("Oude dienst-container opruimen mislukt",
                             dienst=d.naam, error=str(exc)[:200])
        try:
            kaart = self._ken_poorten_toe(d)
            d.poort_map = {str(k): v for k, v in kaart.items()}
            d.container_id = await rt.run_dienst_container(
                naam=d.naam, container_naam=_dienst_container(c, d),
                image=d.image, netwerk=c.netwerk,
                volume=f"labx_dienst_{c.id}_{d.naam}",
                poorten=kaart,
                omgeving=dict(d.omgeving or {}))
            d.status, d.fout = "draait", None
            log.infox("Clusterdienst gestart", cluster=c.naam, dienst=d.naam)
        except Exception as exc:  # noqa: BLE001
            reden = _laatste_zinnige_regel(str(exc))
            d.status, d.fout = "fout", reden[:500]
            c.status, c.fout = "fout", f"{d.naam}: {reden[:300]}"
            log.warningx("Clusterdienst starten mislukt", cluster=c.naam,
                         dienst=d.naam, error=str(exc)[:200])
        d.updated_at = c.updated_at = _nu()
        if d.status == "draait" and all(
                x.status == "draait" for x in self.diensten(c.id)):
            c.status, c.fout = "draait", None
        self.db.commit()
        self.db.refresh(d)
        return d

    async def stop_dienst(self, dienst_id: int) -> ClusterDienst:
        d = self._dienst_of_404(dienst_id)
        c = self.get_or_404(d.cluster_id)
        if d.container_id:
            try:
                await self._runtime(c).remove(d.container_id, timeout=120)
            except Exception as exc:  # noqa: BLE001
                log.warningx("Dienst stoppen mislukt", dienst=d.naam,
                             error=str(exc)[:200])
        d.container_id, d.status, d.fout = None, "gestopt", None
        d.updated_at = _nu()
        self.db.commit()
        self.db.refresh(d)
        return d

    async def verwijder_dienst(self, dienst_id: int) -> None:
        d = self._dienst_of_404(dienst_id)
        await self.stop_dienst(dienst_id)
        self.db.delete(d)
        self.db.commit()

    def _dienst_of_404(self, dienst_id: int) -> ClusterDienst:
        rij = self.db.get(ClusterDienst, int(dienst_id))
        if rij is None:
            raise HTTPException(status_code=404, detail="Dienst niet gevonden")
        return rij

    # ── branch-labs ──────────────────────────────────────────────────────────

    def _pat(self, c: Cluster) -> Optional[str]:
        """Het toegangstoken uit de kluis, of None.

        Alleen hier, en alleen op het moment dat er gekloond wordt. Het token
        staat nergens in een clusterrij, komt niet in een antwoord van de API
        en niet in de context van een model -- het cluster kent alleen de
        NAAM van het geheim.
        """
        if not c.repo_token_geheim:
            return None
        from services.secrets.vault import VaultService

        kluis = VaultService(self.db)
        rij = next((r for r in kluis.lijst() if r.name == c.repo_token_geheim), None)
        if rij is None:
            raise HTTPException(
                status_code=400,
                detail=(f"Het geheim '{c.repo_token_geheim}' staat niet in de kluis. "
                        f"Zet hem erin, of pas de naam aan bij het cluster."))
        waarde = kluis.waarde_van(rij)
        if not waarde:
            raise HTTPException(
                status_code=400,
                detail=f"Het geheim '{c.repo_token_geheim}' is niet te lezen.")
        return waarde

    # ── de deploy key van een cluster ────────────────────────────────────────

    def maak_sleutel(self, cluster_id: int) -> Dict[str, Any]:
        """Een sleutelpaar voor dit cluster maken.

        Per cluster een eigen sleutel, en niet één voor heel LabX: intrekken
        raakt dan alleen dit cluster, en je ziet bij de repo welke omgeving
        toegang heeft. De publieke helft zet jij bij de repo onder
        Settings → Deploy keys.

        Opnieuw maken vervangt de oude. Dat is geen ongeluk dat je per ongeluk
        begaat -- het staat in de bevestiging -- maar de oude sleutel werkt
        daarna niet meer en moet bij de repo weg.
        """
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519

        from utils.crypto import encrypt

        c = self.get_or_404(cluster_id)
        sleutel = ed25519.Ed25519PrivateKey.generate()
        prive = sleutel.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=serialization.NoEncryption()).decode()
        publiek = sleutel.public_key().public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH).decode()
        publiek = f"{publiek} labx-cluster-{_veilige_naam(c.naam)}"

        c.ssh_publiek = publiek
        c.ssh_prive_encrypted = encrypt(prive)
        c.updated_at = _nu()
        self.db.commit()
        log.infox("Deploy key gemaakt", cluster=c.naam)
        return {"publieke_sleutel": publiek, "vervangen": True}

    def verwijder_sleutel(self, cluster_id: int) -> Dict[str, Any]:
        c = self.get_or_404(cluster_id)
        c.ssh_publiek = None
        c.ssh_prive_encrypted = None
        c.updated_at = _nu()
        self.db.commit()
        return {"ok": True}

    def ssh_prive(self, c: Cluster) -> Optional[str]:
        """De privésleutel, of None. Alleen voor het klonen.

        Komt nergens anders vandaan en gaat nergens anders heen: niet naar een
        antwoord van de API, niet naar een scherm, niet naar een model.
        """
        if not c.ssh_prive_encrypted:
            return None
        try:
            from utils.crypto import decrypt

            return decrypt(c.ssh_prive_encrypted)
        except Exception as exc:  # noqa: BLE001
            log.warningx("Deploy key niet te ontsleutelen", cluster=c.naam,
                         error=str(exc)[:200])
            return None

    def toegang_voor(self, c: Cluster):
        """Hoe dit cluster bij zijn repo komt: sleutel als die er is, anders
        het token."""
        from services.lab import git_auth

        return git_auth.bouw(pat=self._pat(c), ssh_prive=self.ssh_prive(c))

    async def controleer_toegang(self, c: Cluster) -> Dict[str, Any]:
        """Kan dit cluster bij zijn repo, en mag het pushen?

        Vóór het klonen, want een mislukte kloon geeft een kale git-fout
        ("The requested URL returned error: 403") waar drie heel verschillende
        oorzaken achter kunnen zitten.

        Twee manieren, want er zijn twee methoden:

        - Met een DEPLOY KEY wordt er echt geprobeerd: `git ls-remote` in een
          wegwerpcontainer. Dat is geen benadering maar precies de handeling
          die straks ook moet lukken.
        - Met een TOKEN vraagt de API van GitHub het, want die zegt het
          preciezer dan een kale git-fout -- en dat scheelt een avond zoeken.
        """
        if not c.repo_url:
            return {"gecontroleerd": False, "hint": "Dit cluster heeft nog geen repo-URL."}

        if c.ssh_prive_encrypted:
            return await self._toegang_via_sleutel(c)
        return await self._toegang_via_api(c)

    async def _toegang_via_sleutel(self, c: Cluster) -> Dict[str, Any]:
        """De echte handeling proberen: branches opvragen met de deploy key.

        Of de sleutel ook MAG pushen is hier niet af te lezen -- dat staat bij
        de repo in het vinkje 'Allow write access', en GitHub verraadt het niet
        bij het ophalen. Dat eerlijk zeggen is beter dan het raden: een
        "mag pushen: ja" die nergens op gebaseerd is, kost je een halve
        agent-run om te ontdekken.
        """
        from services.lab import git_auth
        from services.lab.lab_service import default_image

        toegang = self.toegang_voor(c)
        env = {"REPO_URL": git_auth.url_voor(c.repo_url, toegang), **toegang.omgeving}
        script = toegang.script('git ls-remote --heads "$REPO_URL" | wc -l')
        try:
            uit = await self._runtime(c).run_ephemeral(
                image=default_image(), volume=None,
                cmd=["sh", "-c", script], env=env, timeout=120)
            takken = int((uit or "0").strip().splitlines()[-1].strip() or 0)
            return {"gecontroleerd": True, "ok": True, "methode": "deploy key",
                    "branches": takken,
                    "hint": ("De sleutel mag lezen. Of hij ook mag pushen staat bij de repo "
                             "in 'Allow write access'; dat is hier niet af te lezen.")}
        except Exception as exc:  # noqa: BLE001
            from services.lab.verkenner import _git_reden

            reden = _git_reden(str(exc))
            return {"gecontroleerd": True, "ok": False, "methode": "deploy key",
                    "hint": (f"{reden} — staat de publieke helft bij de repo onder "
                             f"Settings → Deploy keys?")}

    async def _toegang_via_api(self, c: Cluster) -> Dict[str, Any]:
        """Geen github.com? Dan slaan we het over -- we weten dan niet welke
        API erbij hoort, en raden is erger dan niet kijken."""
        import json as _json
        import urllib.error
        import urllib.request

        if "github.com" not in c.repo_url:
            return {"gecontroleerd": False,
                    "hint": "Geen GitHub-repo; hier valt niets te vragen. Een deploy key "
                            "maken en die testen werkt wel."}
        pat = self._pat(c)
        if not pat:
            return {"gecontroleerd": False,
                    "hint": "Dit cluster heeft geen token en geen deploy key; een privérepo "
                            "kloont dan niet."}

        deel = c.repo_url.split("github.com/")[-1].removesuffix(".git").strip("/")
        verzoek = urllib.request.Request(
            f"https://api.github.com/repos/{deel}",
            headers={"Authorization": f"Bearer {pat}",
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "labx"})
        try:
            with urllib.request.urlopen(verzoek, timeout=20) as a:
                data = _json.loads(a.read() or b"{}")
            mag_pushen = bool((data.get("permissions") or {}).get("push"))
            return {"gecontroleerd": True, "ok": True, "methode": "token",
                    "mag_pushen": mag_pushen,
                    "hint": None if mag_pushen else
                            ("Het token kan lezen maar niet pushen. Zet Contents op "
                             "'Read and write' als de agent wijzigingen moet kunnen "
                             "terugduwen.")}
        except urllib.error.HTTPError as fout:
            if fout.code == 404:
                return {"gecontroleerd": True, "ok": False, "methode": "token", "hint": (
                    f"Het token ziet '{deel}' niet. Drie oorzaken, in volgorde van "
                    f"waarschijnlijkheid: (1) de organisatie heeft het token nog niet "
                    f"goedgekeurd — kijk bij Organization settings → Personal access "
                    f"tokens → Pending requests; (2) de repository staat niet in "
                    f"'Repository access' van het token; (3) de resource owner van het "
                    f"token is een persoonlijk account in plaats van de organisatie, en "
                    f"dat is achteraf niet te wijzigen. Een deploy key omzeilt dit alles: "
                    f"die staat op repo-niveau.")}
            if fout.code in (401, 403):
                return {"gecontroleerd": True, "ok": False, "methode": "token", "hint": (
                    "Het token wordt geweigerd (ongeldig of verlopen). Maak een nieuw "
                    "token en vervang het geheim in de kluis.")}
            return {"gecontroleerd": True, "ok": False, "methode": "token",
                    "hint": f"GitHub antwoordde {fout.code}."}
        except Exception as exc:  # noqa: BLE001 — geen net? dan niet oordelen
            log.warningx("Toegangscontrole overgeslagen", error=str(exc)[:200])
            return {"gecontroleerd": False}

    async def maak_branch_lab(self, cluster_id: int, *, branch: str,
                              basis: str = "main",
                              naam: Optional[str] = None) -> Dict[str, Any]:
        """Een lab voor één branch, afgetakt van een basis naar keuze.

        `basis` is met opzet geen vast "main": je wilt net zo goed van
        `release/2026.09` aftakken, en dat achteraf rechtzetten in een lab is
        omslachtiger dan het meteen goed doen.

        Bestaat `branch` al op de remote, dan wordt die uitgecheckt. Bestaat
        hij niet, dan wordt hij van `basis` afgetakt. Dat onderscheid maakt
        git zelf; wij hoeven het niet te raden.
        """
        from services.lab.lab_service import LabService

        c = self.get_or_404(cluster_id)
        branch = (branch or "").strip()
        if not branch:
            raise HTTPException(status_code=400, detail="Geef een branchnaam op.")
        if not c.repo_url:
            raise HTTPException(
                status_code=400,
                detail="Dit cluster heeft nog geen repo-URL; zonder repo valt er niets uit te checken.")

        from models.lab import Lab

        bestaat = (self.db.query(Lab)
                   .filter(Lab.cluster_id == c.id, Lab.branch == branch).first())
        if bestaat is not None:
            raise HTTPException(
                status_code=409,
                detail=(f"Er is al een lab voor '{branch}' in dit cluster: "
                        f"{bestaat.name}."))

        # Eerst kijken of we er überhaupt bij kunnen. Een lab aanmaken en
        # het daarna weer opruimen omdat het token niet mag, is werk en
        # verwarring voor niets.
        toegang = await self.controleer_toegang(c)
        if toegang.get("gecontroleerd") and not toegang.get("ok"):
            raise HTTPException(status_code=400, detail=toegang["hint"])

        lab_svc = LabService(self.db)
        uit = await lab_svc.create(
            name=(naam or f"{c.naam} · {branch}")[:255],
            image=c.lab_image or None,
            host_id=c.host_id,
            # Geen repos= : we klonen zelf, want we hebben een BASIS en een
            # nieuwe branch nodig en dat kan de gewone kloon niet.
            ttl_hours=24 * 7,
            allow_network=True,
        )
        p = self.db.get(Lab, uit["id"])
        p.cluster_id = c.id
        p.branch = branch
        # Het startscript hoort bij het CLUSTER, maar draait in het LAB. Het
        # hier overnemen betekent dat het bestaande inrichten het uitvoert --
        # met zijn log, zijn statussen en zijn herstel na een herstart. Een
        # tweede manier om scripts te draaien zou al die dingen opnieuw moeten
        # uitvinden.
        if c.start_script:
            p.setup_script = c.start_script
        self.db.commit()

        try:
            await self._kloon_branch(c, p, branch=branch, basis=basis)
        except Exception as exc:  # noqa: BLE001
            # Het lab dat WIJ net maakten weer opruimen. Laten staan lijkt
            # behulpzaam ("dan zie je wat er misging") maar is het niet: er
            # staat niets in, en hij bezet de branchnaam -- dus de tweede
            # poging strandde op "er is al een lab voor deze branch", een
            # melding over onze eigen rommel.
            reden = _laatste_zinnige_regel(str(exc))
            try:
                await lab_svc.delete(p.id)
            except Exception as exc2:  # noqa: BLE001
                log.warningx("Half aangemaakt branch-lab opruimen mislukt",
                             lab_id=p.id, error=str(exc2)[:200])
            raise HTTPException(
                status_code=502,
                detail=f"Klonen van '{branch}' mislukte: {reden[:400]}") from exc

        log.infox("Branch-lab aangemaakt", cluster=c.naam, branch=branch,
                  basis=basis, lab_id=p.id)
        self.db.refresh(p)
        return lab_svc._to_dict(p)

    async def _kloon_branch(self, c: Cluster, p: Any, *, branch: str,
                            basis: str) -> None:
        """De repo in het volume zetten, op de juiste branch.

        Eén shell-opdracht, want de stappen horen bij elkaar: klonen op de
        basis, dan de branch pakken of aanmaken. Halverwege stoppen laat een
        werkkopie achter waarvan niemand weet op welke commit hij staat.
        """
        from services.lab.lab_service import LabService

        toegang = self.toegang_voor(c)
        naam = c.repo_url.rstrip("/").split("/")[-1].removesuffix(".git") or "repo"
        env = {"REPO_URL": _git.url_voor(c.repo_url, toegang), "DEST": naam,
               "BASIS": basis, "TAK": branch, **toegang.omgeving}

        script = toegang.script(
            # Geen --depth 1: je wilt van een BASIS kunnen aftakken en later
            # kunnen pushen, en dat gaat niet met een afgeknipte historie.
            'git clone --branch "$BASIS" "$REPO_URL" "$DEST";',
            'cd "$DEST";',
            # Bestaat de branch al op de remote? Dan die, anders aftakken.
            'if git ls-remote --exit-code --heads origin "$TAK" >/dev/null 2>&1; then',
            '  git fetch origin "$TAK":"$TAK" && git checkout "$TAK";',
            'else',
            '  git checkout -b "$TAK";',
            'fi;',
            'git rev-parse --abbrev-ref HEAD',
        )
        await LabService(self.db).runtime_voor(p).run_ephemeral(
            image=p.image, volume=p.volume_name, cmd=["sh", "-c", script],
            env=env, timeout=1800)

    async def start_branch_lab(self, lab_id: str) -> Dict[str, Any]:
        """Een branch-lab aanzetten op het netwerk van zijn cluster.

        Niet via LabService.start(): dat zet een lab op het GEDEELDE
        labnetwerk, en dan vindt het de vaste diensten niet op naam. Dit is
        hetzelfde lab, alleen op de juiste bridge en met een vast adres.
        """
        from models.lab import Lab
        from services.lab.lab_service import LabService, provision_in_background

        p = self.db.get(Lab, lab_id)
        if p is None or not p.cluster_id:
            raise HTTPException(status_code=404,
                                detail="Dit is geen branch-lab van een cluster.")
        c = self.get_or_404(p.cluster_id)
        lab_svc = LabService(self.db)
        rt = lab_svc.runtime_voor(p)

        kaart = self._lab_poorten(p, c)
        p.poort_map = {str(k): v for k, v in kaart.items()}

        omgeving = dict(c.omgeving or {})
        if c.env_profiel:
            omgeving.setdefault("LABX_ENV_PROFIEL", c.env_profiel)
        omgeving.setdefault("LABX_BRANCH", p.branch or "")
        omgeving.update(self._adres_omgeving(c, kaart))
        # De geheimen die deze applicatie nodig heeft, uit de kluis van DIT
        # cluster (en anders de gedeelde). Alleen wat gevonden wordt: een
        # ontbrekend geheim levert geen LEGE variabele op, want een applicatie
        # die JWT_SECRET="" krijgt start vaak gewoon -- met een sleutel die
        # iedereen kan raden.
        from services.lab.cluster_kluis import ClusterKluis

        omgeving.update(ClusterKluis(self.db).los_op(c.id, c.benodigde_geheimen or {}))

        for w in lab_svc.ensure_workers(p):
            if w.container_id:
                try:
                    await rt.remove(w.container_id, timeout=120)
                except Exception as exc:  # noqa: BLE001
                    log.warningx("Oude branch-container opruimen mislukt",
                                 lab_id=p.id, error=str(exc)[:200])
            w.container_id = await rt.run_container(
                name=w.network_alias, image=p.image, volume=p.volume_name,
                cpu_limit=p.cpu_limit, mem_limit_mb=p.mem_limit_mb,
                allow_network=True,
                netwerk=c.netwerk,
                poort_map=kaart if w.index <= 1 else None,
                omgeving=omgeving)
            w.status, w.error = "running", None
            w.provision_status = "pending"
            w.updated_at = _nu()
        lab_svc._spiegel_werker1(p)
        p.status = "running"
        p.error = None
        p.updated_at = _nu()
        self.db.commit()
        if c.start_script:
            provision_in_background(p.id)
        log.infox("Branch-lab gestart", cluster=c.naam, branch=p.branch,
                  poorten=p.poort_map)
        self.db.refresh(p)
        return lab_svc._to_dict(p)

    @staticmethod
    def _adres_omgeving(c: Cluster, kaart: Dict[int, int]) -> Dict[str, str]:
        """Waar dit lab zichzelf kan vinden.

        Zonder dit moest het startscript raden waarop het hoorde te luisteren,
        en moest de gebruiker vragen beantwoorden over PUBLIC_BASE_URL en
        CORS_ORIGINS die LabX zelf weet. Dat zijn geen beslissingen; het zijn
        gevolgen van hoe het lab gepubliceerd wordt.

        `LABX_LAB_URL` blijft weg als het cluster op een ANDERE machine
        draait: die poort staat daar op de loopback en wordt hierheen
        getunneld op een nummer dat pas bij het openen vastligt. Een adres dat
        er wel staat maar niet klopt is erger dan een dat ontbreekt.
        """
        if not kaart:
            return {}
        container_poort, machine_poort = sorted(kaart.items())[0]
        uit = {"LABX_POORT": str(container_poort), "LABX_LAB_POORT": str(machine_poort)}
        if not c.host_id:
            uit["LABX_LAB_URL"] = f"http://localhost:{machine_poort}"
        return uit

    def _lab_poorten(self, p: Any, c: Cluster) -> Dict[int, int]:
        """Welke poorten van dit branch-lab naar buiten gaan, en op welk
        nummer. Hetzelfde bereik als de diensten: één machine, één reeks."""
        # Eerst wat dit LAB zelf opgaf, dan wat het CLUSTER weet, en pas
        # daarna de oude standaard. Die standaard is een gok, en een gok
        # maakt een lab onbereikbaar terwijl het wel draait: publiceer 3000
        # terwijl de applicatie op 8000 luistert en er gebeurt niets, zonder
        # een enkele foutmelding.
        gewenst = ([int(x) for x in (p.ports or [])]
                   or [int(x) for x in (c.lab_poorten or [])]
                   or [3000, 7071])
        huidig = {int(k): int(v) for k, v in (p.poort_map or {}).items()}
        bezet = self._bezette_poorten(behalve_lab=p.id)
        volgende = self.POORT_BASIS
        uit: Dict[int, int] = {}
        for poort in gewenst:
            if poort in huidig and huidig[poort] not in bezet:
                uit[poort] = huidig[poort]
            else:
                while volgende in bezet or volgende in uit.values():
                    volgende += 1
                uit[poort] = volgende
            bezet.add(uit[poort])
        return uit

    # ── voor het scherm ──────────────────────────────────────────────────────

    def to_dict(self, c: Cluster) -> Dict[str, Any]:
        from models.lab import Lab
        from models.lab_host import LabHost

        host = self.db.get(LabHost, c.host_id) if c.host_id else None
        labs = [{"id": i, "name": n, "branch": b} for i, n, b in
                self.db.query(Lab.id, Lab.name, Lab.branch)
                .filter(Lab.cluster_id == c.id).all()]
        return {
            "id": c.id, "naam": c.naam, "status": c.status, "fout": c.fout,
            "netwerk": c.netwerk,
            "host_id": c.host_id, "machine": host.naam if host else "deze server",
            "repo_url": c.repo_url, "repo_token_geheim": c.repo_token_geheim,
            "lab_image": c.lab_image, "start_script": c.start_script,
            "env_profiel": c.env_profiel, "omgeving": c.omgeving or {},
            # Wat er in de kluis moet staan voordat een branch-lab kan
            # draaien, en wat daarvan nog ontbreekt. Die tweede vraag wil je
            # vóór het eerste branch-lab beantwoord hebben, niet veertig
            # minuten later halverwege een bouw.
            "benodigde_geheimen": c.benodigde_geheimen or {},
            # De publieke helft is geen geheim; die hoort juist gekopieerd te
            # worden. De privéhelft komt hier nooit in.
            "ssh_publiek": c.ssh_publiek,
            "toegang_soort": "ssh" if c.ssh_prive_encrypted else ("https" if c.repo_token_geheim else "geen"),
            "lab_poorten": c.lab_poorten or [],
            "geheimen_ontbreken": self._ontbrekende_geheimen(c.benodigde_geheimen or {}, c.id),
            "notitie": c.notitie,
            "diensten": [self.dienst_dict(d) for d in self.diensten(c.id)],
            "labs": labs,
            "created_at": c.created_at, "updated_at": c.updated_at,
        }


    # ── verkennen en goedkeuren ──────────────────────────────────────────────
    #
    # De omkering van het hele hoofdstuk: hiervóór typte je een cluster in,
    # vanaf hier stelt LabX er een voor. Het plan is wat daartussen staat --
    # een voorstel met bewijs, dat jij per onderdeel aan of uit zet.

    async def verken(self, *, repo_url: str, branch: str = "main",
                     token_geheim: Optional[str] = None,
                     host_id: Optional[int] = None,
                     cluster_id: Optional[int] = None,
                     antwoorden: Optional[Dict[str, str]] = None,
                     opmerkingen: Optional[Dict[str, str]] = None,
                     opmerking: Optional[str] = None,
                     keuze: Optional[List[Dict[str, str]]] = None,
                     uitgezet: Optional[List[Dict[str, str]]] = None,
                     vorig_plan_id: Optional[int] = None) -> ClusterPlan:
        """Een repo verkennen en het voorstel bewaren.

        Het plan wordt BEWAARD en niet meteen toegepast. Dat is de prijs van
        een model dat leidend is: twee verkenningen van dezelfde repo kunnen
        verschillen, en dan wil je ze naast elkaar kunnen leggen in plaats van
        dat de tweede de eerste stilletjes overschrijft.
        """
        from services.lab.verkenner import Verkenner

        ssh_prive = None
        if cluster_id:
            c = self.get_or_404(cluster_id)
            repo_url = repo_url or c.repo_url or ""
            token_geheim = token_geheim or c.repo_token_geheim
            host_id = host_id if host_id is not None else c.host_id
            ssh_prive = self.ssh_prive(c)

        uitslag = await Verkenner(self.db).verken(
            repo_url=repo_url, branch=branch,
            token_geheim=token_geheim, ssh_prive=ssh_prive, host_id=host_id,
            antwoorden=antwoorden, opmerkingen=opmerkingen,
            opmerking=opmerking, keuze=keuze, uitgezet=uitgezet)

        rij = ClusterPlan(
            cluster_id=cluster_id,
            repo_url=uitslag["repo_url"],
            branch=uitslag["branch"],
            commit=uitslag.get("commit"),
            status="voorstel",
            plan=uitslag,
            keuzes={},
            token_geheim=token_geheim,
            antwoorden=dict(antwoorden or {}),
            opmerkingen=dict(opmerkingen or {}),
            opmerking=(opmerking or None),
            vorig_plan_id=vorig_plan_id,
            model_gebruikt=1 if (uitslag.get("model") or {}).get("gebruikt") else 0,
            fout=(uitslag.get("model") or {}).get("fout"),
            created_at=_nu(), updated_at=_nu())
        self.db.add(rij)
        self.db.commit()
        self.db.refresh(rij)
        log.infox("Repo verkend", repo=rij.repo_url, branch=rij.branch,
                  commit=(rij.commit or "")[:8], vondsten=len(uitslag.get("vondsten") or []),
                  model=bool(rij.model_gebruikt))
        return rij

    async def opnieuw(self, plan_id: int, *,
                      antwoorden: Optional[Dict[str, str]] = None,
                      opmerkingen: Optional[Dict[str, str]] = None,
                      opmerking: Optional[str] = None,
                      keuze: Optional[List[Dict[str, str]]] = None) -> ClusterPlan:
        """Terugkoppeling geven en opnieuw verkennen.

        Dit was eerst alleen `antwoorden`, en dat was de verkeerde helft: je
        kon alleen iets zeggen over vragen die het model toevallig stelde. Een
        plan zonder vragen liet je met niets, terwijl je er wel iets van kunt
        vinden -- over een dienst die er niet in hoort, over een lab-image dat
        niet klopt, over het geheel.

        Vier soorten terugkoppeling, alle vier optioneel:

        - `antwoorden`   op de vragen die het model stelde
        - `opmerkingen`  per onderdeel ("dienst::mailpit": "hoort er niet bij")
        - `opmerking`    over het plan als geheel
        - `keuze`        wat je aan- en uitgevinkt had; wat uit staat hoort
                         niet terug te komen

        Het levert een NIEUW plan op naast het oude. Niet uit voorzichtigheid:
        het verschil tussen de twee is juist wat je wilt zien.
        """
        oud = self.plan_of_404(plan_id)

        schone_antwoorden = {str(v).strip(): str(a).strip()
                             for v, a in (antwoorden or {}).items() if str(a).strip()}
        schone_opmerkingen = {str(k).strip(): str(w).strip()
                              for k, w in (opmerkingen or {}).items() if str(w).strip()}
        schone_opmerking = (opmerking or "").strip()

        if not (schone_antwoorden or schone_opmerkingen or schone_opmerking):
            raise HTTPException(
                status_code=400,
                detail=("Geef iets mee: een antwoord, een opmerking bij een onderdeel, "
                        "of iets over het plan als geheel. Opnieuw verkennen zonder "
                        "terugkoppeling levert hetzelfde plan op."))

        # Het token van het PLAN eerst: een plan kan zonder cluster bestaan,
        # en dan is er verder niets om op terug te vallen.
        token = oud.token_geheim
        if not token and oud.cluster_id:
            token = self.get_or_404(oud.cluster_id).repo_token_geheim

        gekozen = keuze if keuze is not None else self.standaardkeuze(oud.plan or {})
        # Wat je bij het VORIGE plan hebt uitgezet. Precies dat, en niet
        # "alles wat niet gekozen is": een onderdeel dat deze ronde nieuw
        # opduikt heb jij nooit afgewezen.
        aan = {(k.get("soort"), k.get("sleutel")) for k in gekozen}
        uitgezet = [{"soort": v["soort"], "sleutel": v["sleutel"]}
                    for v in (oud.plan or {}).get("vondsten") or []
                    if v.get("soort") not in _GEEN_AFWIJZING
                    and (v["soort"], v["sleutel"]) not in aan]

        return await self.verken(
            repo_url=oud.repo_url, branch=oud.branch,
            cluster_id=oud.cluster_id, token_geheim=token,
            antwoorden={**(oud.antwoorden or {}), **schone_antwoorden},
            opmerkingen={**(oud.opmerkingen or {}), **schone_opmerkingen},
            opmerking=schone_opmerking or oud.opmerking,
            keuze=gekozen, uitgezet=uitgezet,
            vorig_plan_id=oud.id)

    def plan_of_404(self, plan_id: int) -> ClusterPlan:
        rij = self.db.get(ClusterPlan, plan_id)
        if rij is None:
            raise HTTPException(status_code=404, detail="Plan niet gevonden")
        return rij

    def plannen(self, cluster_id: Optional[int] = None) -> List[ClusterPlan]:
        vraag = self.db.query(ClusterPlan)
        if cluster_id is not None:
            vraag = vraag.filter(ClusterPlan.cluster_id == cluster_id)
        return vraag.order_by(ClusterPlan.id.desc()).all()

    @staticmethod
    def standaardkeuze(plan: Dict[str, Any]) -> List[Dict[str, str]]:
        """Wat er vooraf aangevinkt staat: alles wat gestaafd is.

        Een vondst die de detectoren niet konden terugvinden in de repo blijft
        zichtbaar, maar staat UIT. Je kunt hem aanzetten; hij gaat niet
        vanzelf mee. Dat is het verschil tussen "model leidend" en "model
        beslist".
        """
        uit: List[Dict[str, str]] = []
        image_gekozen = False
        for v in plan.get("vondsten") or []:
            if v.get("door_gebruiker_uit"):
                # Je hebt hem eerder uitgezet. Hij staat er nog, met zijn
                # bewijs, maar hij gaat niet vanzelf weer aan.
                continue
            if not v.get("bevestigd"):
                continue
            if v.get("soort") == "applicatie":
                # Een vaststelling, geen voorstel: hier valt niets te kiezen.
                continue
            if v.get("soort") == "lab_image":
                # Een keuze, geen lijstje. De vondsten staan al op voorkeur
                # gesorteerd, dus de eerste is de beste -- vijf aangevinkte
                # images leveren er alsnog maar een op, en dan bepaalt de
                # volgorde van een lus wat je krijgt.
                if image_gekozen:
                    continue
                image_gekozen = True
            uit.append({"soort": v["soort"], "sleutel": v["sleutel"]})
        return uit

    def keur_goed(self, plan_id: int, *, naam: Optional[str] = None,
                  host_id: Optional[int] = None,
                  gekozen: Optional[List[Dict[str, str]]] = None,
                  token_geheim: Optional[str] = None,
                  env_profiel: Optional[str] = None) -> Dict[str, Any]:
        """Het plan omzetten in een cluster.

        Vanaf hier is het plan BEVROREN: het is de configuratie van dit
        cluster. Opnieuw verkennen levert een nieuw plan op naast dit, zodat
        het verschil zichtbaar is in plaats van verwarrend.
        """
        rij = self.plan_of_404(plan_id)
        if rij.status == "goedgekeurd":
            raise HTTPException(
                status_code=409,
                detail=("Dit plan is al goedgekeurd. Verken opnieuw als er iets "
                        "veranderd is; dan zie je het verschil."))
        plan = dict(rij.plan or {})
        keuze = gekozen if gekozen is not None else self.standaardkeuze(plan)
        wil = {(k.get("soort"), k.get("sleutel")) for k in keuze}

        if rij.cluster_id:
            c = self.get_or_404(rij.cluster_id)
        else:
            c = self.maak(naam=(naam or _naam_uit_url(rij.repo_url)),
                          host_id=host_id)

        c.repo_url = rij.repo_url
        if token_geheim:
            c.repo_token_geheim = token_geheim

        omgeving = dict(c.omgeving or {})
        geheimen = dict(c.benodigde_geheimen or {})
        toegevoegd: List[str] = []
        image_gezet = False

        for v in plan.get("vondsten") or []:
            if (v.get("soort"), v.get("sleutel")) not in wil:
                continue
            soort, sleutel, waarde = v["soort"], v["sleutel"], (v.get("waarde") or {})

            if soort == "dienst":
                image = str(waarde.get("image") or "")
                if not image:
                    continue
                # LabX start deze container, dus LabX kiest de inloggegevens.
                #
                # EEN wachtwoord per dienst, overal hetzelfde. Het model
                # verzon er twee verschillende -- in de container
                # `${DB_PASS:?zet DB_PASS in .env}` en in de lab-omgeving
                # `labx-mysql-root-7f3k9q` -- en dan start de database met het
                # ene en verbindt de applicatie met het andere. Dat merk je
                # pas als het lab al draait.
                #
                # Dat root en gebruiker hetzelfde wachtwoord krijgen is voor
                # een wegwerpdatabase in een lab geen bezwaar; een stille
                # mismatch wel.
                container_omgeving = {str(k): str(w) for k, w in (waarde.get("omgeving") or {}).items()}
                lab_omgeving = {str(k): str(w) for k, w in (waarde.get("lab_omgeving") or {}).items()}

                # Eerst de shell-expansie eruit, dan pas wachtwoorden kiezen:
                # anders wordt `${DB_PASS:?...}` als "zwak" herkend en krijgt
                # het de generatie, terwijl `${DB_NAME:-sp}` blijft staan.
                container_omgeving = _zonder_expansie(container_omgeving)
                lab_omgeving = _zonder_expansie(lab_omgeving)

                ww = _wachtwoord()
                oude_waarden = set()
                for env_sleutel, oud_ww in list(container_omgeving.items()):
                    if _WACHTWOORD_SLEUTEL.search(env_sleutel) and _ZWAK.match(oud_ww):
                        container_omgeving[env_sleutel] = ww
                        if oud_ww:
                            oude_waarden.add(oud_ww)
                for env_sleutel, oud_ww in list(lab_omgeving.items()):
                    if _WACHTWOORD_SLEUTEL.search(env_sleutel) and _ZWAK.match(oud_ww):
                        lab_omgeving[env_sleutel] = ww
                        if oud_ww:
                            oude_waarden.add(oud_ww)
                # En wat er in een verbindingsreeks staat moet mee.
                for weg in oude_waarden:
                    lab_omgeving = {k: w.replace(weg, ww) for k, w in lab_omgeving.items()}

                # Naar de inloggegevens van een dienst die LabX zelf start
                # hoort niemand gevraagd te worden: de gebruiker kan ze niet
                # weten, want de dienst bestaat nog niet.
                for env_sleutel in list((waarde.get("omgeving_uit_kluis") or {})):
                    if _WACHTWOORD_SLEUTEL.search(env_sleutel):
                        lab_omgeving.setdefault(env_sleutel, ww)
                    geheimen.pop(env_sleutel, None)
                waarde["omgeving_uit_kluis"] = {}

                dienstnaam = _veilige_naam(f"{_voorvoegsel(c.naam)}-{sleutel}")
                bestaat = (self.db.query(ClusterDienst)
                           .filter(ClusterDienst.cluster_id == c.id,
                                   ClusterDienst.naam == dienstnaam).first())
                if bestaat is None:
                    self.voeg_dienst_toe(
                        c.id, naam=dienstnaam, image=image,
                        poorten=[int(p) for p in (waarde.get("poorten") or [])],
                        omgeving=container_omgeving)
                    toegevoegd.append(dienstnaam)
                # Wat een branch-lab moet weten om deze dienst te vinden. De
                # naam in de lab-omgeving moet de ECHTE dienstnaam zijn, niet
                # die uit de catalogus of uit het model -- anders wijst
                # COSMOS_ENDPOINT naar een host die op dit netwerk niet
                # bestaat, en dat merk je pas als een lab al draait.
                for k, w in lab_omgeving.items():
                    omgeving[str(k)] = _hernoem_host(str(w), sleutel, dienstnaam)
                # Wat het model niet WIST staat niet in de omgeving maar op
                # de lijst "moet uit de kluis komen". Een plaatshouder als
                # waarde geeft een lab dat draait met een sleutel die
                # iedereen kan raden -- erger dan een lab dat niet start.
                for k, uitleg in (waarde.get("omgeving_uit_kluis") or {}).items():
                    geheimen[str(k)] = str(uitleg)[:300]

            elif soort == "lab_image":
                image = str(waarde.get("image") or "")[:255]
                if image and not image_gezet:
                    c.lab_image = image
                    image_gezet = True

            elif soort == "lab_poorten":
                poorten = [int(x) for x in (waarde.get("poorten") or []) if str(x).isdigit()]
                if poorten:
                    c.lab_poorten = poorten[:6]

            elif soort == "startscript":
                c.start_script = plan.get("startscript") or c.start_script

            elif soort == "omgeving" and sleutel == "env_profiel":
                c.env_profiel = (env_profiel or str(waarde.get("standaard") or ""))[:64] or None

            elif soort in ("extern", "geheim"):
                geheimen[sleutel] = (v.get("toelichting") or "")[:300]

        # Het startscript hoort bij het cluster, ook als er geen aparte
        # vondst voor aangevinkt stond -- het model schrijft het op uit alles
        # bij elkaar en niet uit een enkele vindplaats.
        if not c.start_script and plan.get("startscript"):
            c.start_script = plan["startscript"]
        if env_profiel:
            c.env_profiel = env_profiel[:64]

        # Een sleutel die LabX zelf in de omgeving zet, is geen geheim dat
        # jij moet aanleveren. Dat ving de lus hierboven per dienst al op,
        # maar een detector die `DB_PASS` los in een .env zag staan niet --
        # en dan vraagt het scherm om een wachtwoord dat er allang is.
        geheimen = {k: w for k, w in geheimen.items() if k not in omgeving}

        c.omgeving = omgeving
        c.benodigde_geheimen = geheimen
        c.updated_at = _nu()

        rij.cluster_id = c.id
        rij.status = "goedgekeurd"
        rij.keuzes = {"gekozen": keuze}
        rij.updated_at = _nu()
        self.db.commit()
        self.db.refresh(c)

        ontbreekt = self._ontbrekende_geheimen(geheimen, c.id)
        log.infox("Plan goedgekeurd", cluster=c.naam, diensten=len(toegevoegd),
                  geheimen_open=len(ontbreekt))
        return {
            "cluster": self.to_dict(c),
            "diensten_toegevoegd": toegevoegd,
            "geheimen_ontbreken": ontbreekt,
        }

    def _ontbrekende_geheimen(self, benodigd: Dict[str, Any],
                              cluster_id: Optional[int] = None) -> List[str]:
        """Welke van de benodigde geheimen nergens te vinden zijn.

        Eerst de kluis van dit cluster, dan de gedeelde. Dit is de vraag die
        je vóór het eerste branch-lab beantwoord wilt hebben, niet veertig
        minuten later halverwege een bouw.
        """
        if not benodigd:
            return []
        try:
            from services.lab.cluster_kluis import ClusterKluis

            kluis = ClusterKluis(self.db)
            if cluster_id:
                return sorted(n for n in benodigd if not kluis.bestaat(cluster_id, n))
            from services.secrets.vault import VaultService

            aanwezig = {r.name for r in VaultService(self.db).lijst()}
            return sorted(n for n in benodigd if n not in aanwezig)
        except Exception as exc:  # noqa: BLE001 — geen kluis? dan niets beweren
            log.infox("Kluis niet te lezen", error=str(exc)[:200])
            return []

    @staticmethod
    def plan_dict(rij: ClusterPlan) -> Dict[str, Any]:
        plan = dict(rij.plan or {})
        return {
            "id": rij.id,
            "cluster_id": rij.cluster_id,
            "repo_url": rij.repo_url,
            "branch": rij.branch,
            "commit": rij.commit,
            "status": rij.status,
            "model_gebruikt": bool(rij.model_gebruikt),
            "fout": rij.fout,
            "samenvatting": plan.get("samenvatting"),
            "startscript": plan.get("startscript"),
            "vragen": plan.get("vragen") or [],
            "token_geheim": rij.token_geheim,
            "antwoorden": rij.antwoorden or {},
            "opmerkingen": rij.opmerkingen or {},
            "opmerking": rij.opmerking,
            "vorig_plan_id": rij.vorig_plan_id,
            "vondsten": plan.get("vondsten") or [],
            "url_omgezet": plan.get("url_omgezet"),
            "url_melding": plan.get("url_melding"),
            "boom_afgekapt": plan.get("boom_afgekapt"),
            "bestanden_gelezen": plan.get("bestanden_gelezen"),
            "standaardkeuze": ClusterService.standaardkeuze(plan),
            "keuzes": (rij.keuzes or {}).get("gekozen") or [],
            "created_at": rij.created_at,
        }

    @staticmethod
    def dienst_dict(d: ClusterDienst) -> Dict[str, Any]:
        return {"id": d.id, "naam": d.naam, "image": d.image,
                "poorten": d.poorten or [], "omgeving": d.omgeving or {},
                # Waar je hem van buiten bereikt: {containerpoort: machinepoort}.
                "poort_map": d.poort_map or {},
                "status": d.status, "fout": d.fout,
                "draait": bool(d.container_id)}
