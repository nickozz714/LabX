"""
services/lab/verkenner.py

De verkenning: een repo lezen en een cluster voorstellen.

Hiervóór was een cluster iets dat je CONFIGUREERT — repo-URL, image,
startscript, diensten, allemaal met de hand ingetypt. Dat werkt precies één
keer, voor het cluster dat je samen opbouwt. De verkenner draait het om: je
geeft een repo, en LabX stelt het cluster voor.

**De loop.**

    repo-URL + branch
       -> wegwerpcontainer kloont ondiep           (er draait niets uit de repo)
       -> begrensd beeld van de bestanden          (verkenner_snapshot)
       -> detectoren vinden wat opzoekwerk is      (verkenner_detectoren)
       -> het model leest de repo en stelt voor
       -> elke modelvondst langs de detectoren
       -> PLAN, met bewijs en zekerheid per onderdeel
       -> jij keurt goed
       -> cluster

**Model leidend, detectoren als controle.** Het model leest de repo zoals een
mens dat zou doen; dat is de flexibele keuze en ze is bewust gemaakt, want een
repo die buiten de ladder van de detectoren valt levert anders een dun plan op
dat jij mag aanvullen — precies het handwerk waar de verkenner vanaf moest.

De prijs is reproduceerbaarheid, en die wordt op drie plekken afgekocht:

1. Een plan wordt BEVROREN bij goedkeuring. Er wordt nooit stilletjes opnieuw
   verkend; verkennen is iets dat jij doet.
2. Wat de detectoren niet kunnen staven komt wél in het plan, met vindplaats
   en lage zekerheid, maar staat in het goedkeurscherm UIT. Zo blijft "model
   leidend" iets anders dan "model beslist", en blijft de harde regel
   overeind: er draait geen container waar niemand op geklikt heeft.
3. De uitkomst hoort bij een COMMIT, zodat twee verkenningen te vergelijken
   zijn in plaats van verwarrend.
"""
from __future__ import annotations

import base64
import json
import os
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from services.lab import verkenner_detectoren as det
from services.lab.git_url import normaliseer as normaliseer_url
from services.lab.verkenner_snapshot import EXTRA_SCRIPT, SCRIPT

log = get_logger(__name__)

#: Hoeveel bestanden het model extra mag opvragen na de eerste blik. Eén
#: ronde, niet meer: dit is een verkenning, geen onderzoek.
MAX_EXTRA_BESTANDEN = 12

#: Tijdslimieten. Een kloon van een monorepo mag even duren; het model niet.
KLOON_TIJD = 600.0
LEES_TIJD = 120.0
MODEL_TIJD = 600.0

#: Het model dat de verkenning doet.
#:
#: Opus, en niet het goedkope model dat de chat gebruikt. Een verkenning is
#: één aanroep per repo waarvan de uitkomst de hele inrichting van een cluster
#: bepaalt -- en de fouten die een zwakker model maakt (een wachtwoord
#: verzinnen en er daarna naar vragen, twee keer dezelfde sleutel opvoeren)
#: kosten meer tijd dan het verschil in prijs. Te overrulen met
#: LABX_VERKENNER_MODEL.
VERKENNER_MODEL = os.environ.get("LABX_VERKENNER_MODEL", "opus").strip() or "opus"


class Verkenner:
    """Leest een repo en stelt een cluster voor."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── de buitenkant ────────────────────────────────────────────────────────

    async def verken(self, *, repo_url: str, branch: str = "main",
                     token_geheim: Optional[str] = None,
                     ssh_prive: Optional[str] = None,
                     host_id: Optional[int] = None,
                     instructie: Optional[str] = None,
                     azure_sessie: Optional[str] = None,
                     antwoorden: Optional[Dict[str, str]] = None,
                     opmerkingen: Optional[Dict[str, str]] = None,
                     opmerking: Optional[str] = None,
                     keuze: Optional[List[Dict[str, str]]] = None,
                     uitgezet: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """Een repo verkennen en een plan teruggeven.

        Laat niets achter: het volume waarin gekloond is gaat weg, ook als er
        halverwege iets misgaat. Een verkenning die een volume laat staan is
        een verkenning die je niet nog eens durft te doen.
        """
        herleid = normaliseer_url(repo_url)
        if not herleid.url:
            raise HTTPException(status_code=400, detail="Geef een repo-URL op.")
        repo_url = herleid.url
        branch = (branch or "main").strip() or "main"

        runtime = self._runtime(host_id)
        volume = f"labx-verken-{uuid.uuid4().hex[:10]}"
        image = self._image()

        try:
            await runtime.create_volume(volume)
            kop = await self._kloon(runtime, volume, image,
                                    repo_url=repo_url, branch=branch,
                                    token_geheim=token_geheim, ssh_prive=ssh_prive)
            beeld_ruw = await self._beeld(runtime, volume, image, map_=kop["map"])
            inhoud: det.RepoBeeld = dict(beeld_ruw.get("inhoud") or {})

            # De boom erbij: env-bestanden zijn te vinden op hun PAD, ook als
            # het snapshot hun inhoud niet las.
            vondsten = det.detecteer_alles(inhoud, beeld_ruw.get("boom") or [])
            modeluitslag = await self._vraag_model(
                runtime, volume, image, map_=kop["map"],
                beeld=beeld_ruw, inhoud=inhoud, vondsten=vondsten,
                repo_url=repo_url, branch=branch, instructie=instructie,
                antwoorden=antwoorden, opmerkingen=opmerkingen,
                opmerking=opmerking, keuze=keuze)
            inhoud.update(modeluitslag.pop("_nagelezen", {}))
            vondsten = self._voeg_samen(vondsten, modeluitslag, inhoud)
            # JOUW oordeel gaat boven dat van een detector.
            #
            # De samenvoegregel laat de detector winnen van het model, en dat
            # klopt -- die heeft een vindplaats. Maar het liet ook jou
            # verliezen: zeg je "mailpit hoort er niet bij", dan vond de
            # detector hem de volgende ronde gewoon weer, want in de repo is
            # er niets veranderd. Daarom wordt de terugkoppeling NA het
            # samenvoegen toegepast.
            #
            # De vondst verdwijnt niet: het bewijs is nog net zo waar. Hij
            # staat alleen niet meer vooraf aan, met jouw woorden erbij.
            _pas_terugkoppeling_toe(vondsten, opmerkingen, uitgezet)
            _pas_uitsluitingen_toe(vondsten, modeluitslag.get("niet_overnemen"))

            return {
                "repo_url": repo_url,
                "url_omgezet": herleid.omgezet,
                "url_melding": herleid.melding,
                "branch": kop.get("branch") or branch,
                "commit": kop.get("commit"),
                "boom": (beeld_ruw.get("boom") or [])[:400],
                "boom_afgekapt": bool(beeld_ruw.get("boom_afgekapt")),
                "bestanden_gelezen": len(inhoud),
                "vondsten": [v.to_dict() for v in vondsten],
                "samenvatting": modeluitslag.get("samenvatting") or "",
                "startscript": modeluitslag.get("startscript") or "",
                "vragen": list(modeluitslag.get("vragen") or [])[:5],
                "model": {
                    "gebruikt": bool(modeluitslag.get("_gebruikt")),
                    "fout": modeluitslag.get("_fout"),
                },
            }
        finally:
            try:
                await runtime.remove_volume(volume)
            except Exception as exc:  # noqa: BLE001 — opruimen mag nooit de fout worden
                log.warningx("Verkenningsvolume niet opgeruimd", volume=volume, error=str(exc)[:200])

    # ── de stappen ───────────────────────────────────────────────────────────

    def _runtime(self, host_id: Optional[int]):
        from services.lab.lab_service import LabService

        svc = LabService(self.db)
        return svc._docker_op_host(host_id) if host_id else svc.runtime

    def _image(self) -> str:
        """Het image waarin we klonen en lezen.

        Hetzelfde image als een gewoon lab: dat staat op elke machine die labs
        draait al klaar, en het heeft git en python aan boord. Een eigen
        verkennersimage zou een tweede pull betekenen op elke lab-host, voor
        gereedschap dat er al is.
        """
        from services.lab.lab_service import default_image

        return default_image()

    async def _kloon(self, runtime, volume: str, image: str, *,
                     repo_url: str, branch: str,
                     token_geheim: Optional[str],
                     ssh_prive: Optional[str] = None) -> Dict[str, Any]:
        """Ondiep klonen. We lezen alleen; historie hebben we hier niet nodig.

        Dit is bewust NIET dezelfde kloon als die van een branch-lab: daar
        mag `--depth 1` juist niet, want daar wil je van een basis aftakken en
        later pushen.
        """
        from services.lab import git_auth

        naam = _repomap(repo_url)
        token = self._token(token_geheim)
        toegang = git_auth.bouw(pat=token, ssh_prive=ssh_prive)
        env = {"REPO_URL": git_auth.url_voor(repo_url, toegang), "DEST": naam,
               "TAK": branch, **toegang.omgeving}

        script = toegang.script(
            # --quiet in plaats van stderr dichtgooien: dat haalt de
            # voortgangsbalk weg maar laat de FOUT staan. Met 2>/dev/null
            # bleef er van een mislukte kloon niets over dan "docker run
            # faalde (128)", en daar kan niemand iets mee.
            'git clone --quiet --depth 1 --branch "$TAK" "$REPO_URL" "$DEST"',
            '  || git clone --quiet --depth 1 "$REPO_URL" "$DEST";',
            'cd "$DEST";',
            'echo "COMMIT=$(git rev-parse HEAD)";',
            'echo "BRANCH=$(git rev-parse --abbrev-ref HEAD)"',
        )
        try:
            uit = await runtime.run_ephemeral(
                image=image, volume=volume, cmd=["sh", "-c", script],
                env=env, timeout=KLOON_TIJD)
        except Exception as exc:  # noqa: BLE001
            ruw = str(exc)
            reden = _git_reden(ruw)
            if toegang.soort == "geen" and _lijkt_prive(ruw):
                reden += (" — er is geen toegangstoken en geen deploy key meegegeven; "
                          "een privérepo kloont dan niet.")
            elif toegang.soort == "ssh" and _lijkt_prive(ruw):
                reden += (" — de deploy key wordt geweigerd. Staat de publieke helft bij "
                          "de repo onder Settings → Deploy keys?")
            raise HTTPException(
                status_code=502,
                detail=f"Verkennen mislukte bij het klonen: {reden[:500]}") from exc

        velden = dict(
            regel.split("=", 1) for regel in uit.splitlines() if "=" in regel
        )
        return {"map": naam, "commit": velden.get("COMMIT"), "branch": velden.get("BRANCH")}

    async def _beeld(self, runtime, volume: str, image: str, *, map_: str) -> Dict[str, Any]:
        uit = await runtime.run_ephemeral(
            image=image, volume=volume,
            cmd=["python3", "-c", SCRIPT, f"/workspace/{map_}"],
            timeout=LEES_TIJD)
        try:
            return json.loads(uit)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Het repo-beeld kwam er niet uit: {uit[:300]}") from exc

    async def _lees_extra(self, runtime, volume: str, image: str, *,
                          map_: str, paden: List[str]) -> Dict[str, str]:
        if not paden:
            return {}
        try:
            uit = await runtime.run_ephemeral(
                image=image, volume=volume,
                cmd=["python3", "-c", EXTRA_SCRIPT, f"/workspace/{map_}",
                     json.dumps(paden[:MAX_EXTRA_BESTANDEN])],
                timeout=LEES_TIJD)
            return json.loads(uit) or {}
        except Exception as exc:  # noqa: BLE001 — nalezen is een gunst, geen eis
            log.infox("Nalezen overgeslagen", error=str(exc)[:200])
            return {}

    def _token(self, naam: Optional[str]) -> Optional[str]:
        """Het toegangstoken uit de kluis, op naam.

        Alleen hier, en alleen om mee te klonen. Het token komt niet in een
        antwoord van de API, niet in een plan en niet in de context van een
        model.
        """
        if not naam:
            return None
        from services.secrets.vault import VaultService

        kluis = VaultService(self.db)
        rij = next((r for r in kluis.lijst() if r.name == naam), None)
        if rij is None:
            raise HTTPException(
                status_code=400,
                detail=f"Het geheim '{naam}' staat niet in de kluis.")
        waarde = kluis.waarde_van(rij)
        if not waarde:
            raise HTTPException(status_code=400, detail=f"Het geheim '{naam}' is niet te lezen.")
        return waarde

    # ── het model ────────────────────────────────────────────────────────────

    async def _vraag_model(self, runtime, volume: str, image: str, *, map_: str,
                           beeld: Dict[str, Any], inhoud: det.RepoBeeld,
                           vondsten: List[det.Vondst], repo_url: str,
                           branch: str,
                           instructie: Optional[str] = None,
                           azure_sessie: Optional[str] = None,
                           antwoorden: Optional[Dict[str, str]] = None,
                           opmerkingen: Optional[Dict[str, str]] = None,
                           opmerking: Optional[str] = None,
                           keuze: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """Het model leest de repo en stelt het cluster voor.

        Valt dit om — geen sleutel, geen netwerk, een tijdslimiet — dan is dat
        geen mislukte verkenning. De detectoren hebben dan al een plan
        neergelegd; het is alleen dunner, en het plan zegt erbij dat het model
        niet meegekeken heeft.
        """
        prompt = _prompt(repo_url=repo_url, branch=branch, beeld=beeld,
                         inhoud=inhoud, vondsten=vondsten, instructie=instructie,
                         azure_sessie=azure_sessie,
                         antwoorden=antwoorden, opmerkingen=opmerkingen,
                         opmerking=opmerking, keuze=keuze)
        try:
            ruw = await self._claude(prompt)
        except Exception as exc:  # noqa: BLE001
            log.warningx("Verkenning zonder model", error=str(exc)[:300])
            return {"_gebruikt": False, "_fout": str(exc)[:300], "_nagelezen": {}}

        uitslag = _lees_json(ruw)
        uitslag["_gebruikt"] = True
        uitslag["_fout"] = None

        gevraagd = [str(p) for p in (uitslag.get("lees_ook") or [])][:MAX_EXTRA_BESTANDEN]
        uitslag["_nagelezen"] = await self._lees_extra(
            runtime, volume, image, map_=map_, paden=gevraagd) if gevraagd else {}
        return uitslag

    async def _claude(self, prompt: str) -> str:
        from services.agent.claude_cli_provider import ClaudeCliProvider
        from services.settings_service import get_settings

        s = get_settings(self.db)
        provider = ClaudeCliProvider(
            default_model=VERKENNER_MODEL,
            oauth_token=getattr(s, "oauth_token", None),
            cli_path=s.cli_path or "claude",
            max_turns=1,
            timeout=MODEL_TIJD,
            # Geen gereedschap: dit model LEEST een beeld dat wij hebben
            # samengesteld. Zou het de gateway krijgen, dan kon het buiten de
            # redactie om bij de echte bestanden -- en dan is de hele
            # zorgvuldigheid van het beeld voor niets.
            enable_tool_search=False,
        )
        uit = await provider.chat(prompt)
        return uit.text

    # ── samenvoegen en staven ────────────────────────────────────────────────

    def _voeg_samen(self, vondsten: List[det.Vondst], uitslag: Dict[str, Any],
                    inhoud: det.RepoBeeld) -> List[det.Vondst]:
        """Detectorvondsten en modelvondsten tot één plan.

        Vindt de detector iets al, dan blijft ZIJN vondst staan — die heeft
        een echte vindplaats. Wat het model erover te zeggen heeft komt erbij
        als toelichting, want dat is vaak het stuk dat uitlegt waarom het er
        toe doet.

        En het vult aan waar de detector niets weet. Een compose-bestand zegt
        dát `ollama` draait; het zegt niet onder welke naam de applicatie hem
        zoekt. Dat laatste leest het model uit de code. Namen de detector
        gewoon over, dan kreeg een branch-lab een lege omgeving en zocht hij
        een dienst die er wel is — op een adres dat hij niet kent.
        """
        op_sleutel = {(v.soort, v.sleutel): v for v in vondsten}

        for mv in _model_vondsten(uitslag):
            kern = (mv.soort, mv.sleutel)
            bestaand = op_sleutel.get(kern)
            if bestaand is not None:
                if mv.toelichting:
                    bestaand.toelichting = mv.toelichting
                _vul_aan(bestaand, mv)
                continue
            op_sleutel[kern] = det.staaf(mv, inhoud)

        _ontdubbel_geheimen(op_sleutel)

        volgorde = {"dienst": 0, "applicatie": 1, "lab_image": 2, "lab_poorten": 3,
                    "startscript": 4, "omgeving": 5, "envbestand": 6, "extra": 7,
                    "extern": 8, "geheim": 9}
        return sorted(op_sleutel.values(),
                      key=lambda v: (volgorde.get(v.soort, 9),
                                     _image_voorkeur(v),
                                     0 if v.bevestigd else 1,
                                     v.sleutel))


# ── hulpjes ──────────────────────────────────────────────────────────────────

#: Binnen `lab_image` kan er maar EEN waar zijn: het is een keuze, geen
#: lijstje. Deze volgorde bepaalt welke vooraan staat en dus vooraf gekozen
#: is. Een manifest wint, omdat daar de taal en de pakketbeheerder staan die
#: een lab nodig heeft om te BOUWEN -- een Dockerfile uit de repo bouwt vaak
#: een productie-image zonder dat gereedschap erin.
_IMAGE_VOORKEUR = ("detector:manifest", "detector:dockerfile", "model")


#: Velden die het model mag aanvullen op een detectorvondst, zolang de
#: detector ze LEEG liet. Nooit overschrijven: de detector las ze af, het
#: model leidde ze af.
_AAN_TE_VULLEN = ("lab_omgeving", "omgeving_uit_kluis", "poorten", "omgeving")


def _vul_aan(bestaand: det.Vondst, van_model: det.Vondst) -> None:
    for veld in _AAN_TE_VULLEN:
        if not bestaand.waarde.get(veld) and van_model.waarde.get(veld):
            bestaand.waarde[veld] = van_model.waarde[veld]


def _image_voorkeur(v: det.Vondst) -> int:
    if v.soort != "lab_image":
        return 0
    for nummer, bron in enumerate(_IMAGE_VOORKEUR):
        if v.bron == bron:
            return nummer
    return len(_IMAGE_VOORKEUR)


#: Wat een model schrijft waar het een waarde niet WEET. Het bedoelt het
#: goed -- het wil niet gokken -- maar als omgevingsvariabele is het gif.
_PLAATSHOUDER = re.compile(r"^\s*(<.*>|\$\{.*\}|)\s*$", re.DOTALL)


def _splits_plaatshouders(omgeving: Dict[str, Any]) -> tuple[Dict[str, str], Dict[str, str]]:
    """Echte waarden scheiden van wat het model niet wist.

    Bij de eerste verkenning van een vreemde repo kwam dit terug:

        DB_PASS=<zelfde waarde als MYSQL_PASSWORD>
        JWT_SECRET=<geheim, verplicht: de app start niet zonder>

    Allebei terecht: die waarden STAAN niet in de repo. Maar als
    omgevingsvariabele is het rampzalig, want goedgekeurd zoals het is krijgt
    een branch-lab die zin letterlijk als JWT-sleutel -- en dan draait hij,
    met een sleutel die iedereen kan raden.

    Een plaatshouder is dus geen waarde maar een VRAAG, en die hoort op de
    tweede lijst: dit moet uit de kluis komen.
    """
    echt: Dict[str, str] = {}
    uit_kluis: Dict[str, str] = {}
    for sleutel, waarde in (omgeving or {}).items():
        tekst = str(waarde)
        if _PLAATSHOUDER.match(tekst):
            uit_kluis[str(sleutel)] = tekst.strip().strip("<>${}").strip() or "geen waarde gevonden in de repo"
        else:
            echt[str(sleutel)] = tekst
    return echt, uit_kluis


def _pas_uitsluitingen_toe(vondsten: List[det.Vondst], niet_overnemen: Any) -> None:
    """Wat er volgens de opdracht van de gebruiker niet in hoort.

    Een detector vindt wat er in de repo STAAT, en dat is precies zijn kracht:
    hij heeft een vindplaats. Maar hij weet niet wat je ermee wilt. Zeg je
    "de agent hoort er niet bij", dan blijft hij `postgres` gewoon vinden --
    en dan staat die er alsnog, aangevinkt, want de detector wint van het
    model.

    Het model mag detectorvondsten daarom UITZETTEN op grond van jouw
    opdracht. Niet weggooien: het bewijs blijft waar, en je ziet waarom hij
    uit staat.
    """
    per_kern = {(v.soort, v.sleutel): v for v in vondsten}
    for item in (niet_overnemen or [])[:20]:
        if not isinstance(item, dict):
            continue
        vondst = per_kern.get((str(item.get("soort")), str(item.get("sleutel"))))
        if vondst is None:
            continue
        vondst.uitgesloten = True
        waarom = str(item.get("waarom") or "").strip()
        if waarom:
            vondst.opmerking = waarom[:400]


def _ontdubbel_geheimen(op_sleutel: Dict[Any, det.Vondst]) -> None:
    """Een sleutel die al bij een dienst hoort, niet ook los opvoeren.

    De detector ziet `CLAUDE_CODE_OAUTH_TOKEN` en weet niet van welke dienst
    hij is, dus zet hem op de lijst losse geheimen. Het model herkent hem wél
    en voert hem op onder `extern`. Zonder ontdubbelen staat hij er twee keer
    en wordt er twee keer om hetzelfde gevraagd.

    De EXTERNE vondst wint: die weet bij welke dienst de sleutel hoort, en dat
    is precies wat je mist als er alleen een losse naam staat.
    """
    bij_dienst = {
        str(s)
        for v in op_sleutel.values() if v.soort == "extern"
        for s in (v.waarde.get("sleutels") or [])
    }
    for kern in [k for k in op_sleutel if k[0] == "geheim" and k[1] in bij_dienst]:
        op_sleutel.pop(kern, None)


def _pas_terugkoppeling_toe(vondsten: List[det.Vondst],
                            opmerkingen: Optional[Dict[str, str]],
                            uitgezet: Optional[List[Dict[str, str]]]) -> None:
    """Wat jij van het vorige plan vond, over het nieuwe heen leggen.

    Twee dingen, en ze zijn niet hetzelfde:

    - Een OPMERKING blijft bij het onderdeel staan, zodat je volgende ronde
      terugziet waarom het er zo bij staat.
    - Een UITGEVINKT onderdeel komt terug -- het bewijs is nog net zo waar --
      maar het staat niet meer vooraf aan. Dat is het verschil tussen "ik heb
      het niet gevonden" en "jij wilde het niet", en die twee horen niet
      hetzelfde te zien.
    """
    opmerkingen = opmerkingen or {}
    uit = {(k.get("soort"), k.get("sleutel")) for k in (uitgezet or [])}
    for v in vondsten:
        tekst = opmerkingen.get(f"{v.soort}::{v.sleutel}")
        if tekst:
            v.opmerking = str(tekst)[:600]
        if (v.soort, v.sleutel) in uit:
            v.door_gebruiker_uit = True


def _slug(ruw: Any, *, max_lengte: int = 40) -> str:
    """Een naam van het model tot een bruikbare sleutel.

    Het model antwoordt met wat het ziet, en dat is soms een hele zin:
    bij de eerste echte proef kwam er `llm-providers (OpenAI/Anthropic/Gemini/
    Azure Foundry)` terug. Als beschrijving klopt dat; als NAAM niet, want
    deze wordt de DNS-naam van een dienst en de naam waaronder je een geheim
    in de kluis zoekt. De zin zelf gaat niet verloren -- die staat in de
    toelichting.
    """
    schoon = re.sub(r"[^a-z0-9]+", "-", str(ruw or "").lower()).strip("-")
    if len(schoon) <= max_lengte:
        return schoon
    # Afkappen op een woordgrens. Midden in een woord afbreken levert
    # `...-gemini-az` op, en dat leest als een naam die iemand verkeerd
    # getypt heeft in plaats van als een afkorting.
    kort = schoon[:max_lengte]
    grens = kort.rfind("-")
    return (kort[:grens] if grens > max_lengte // 3 else kort).strip("-")


#: Hoe git zegt dat hij er niet bij mag. Hij zegt het nooit met zoveel
#: woorden -- "not found" is wat je krijgt als het token ontbreekt, want
#: zonder token BESTAAT een privérepo niet voor je.
_PRIVE = ("authentication", "not found", "could not read", "permission denied",
          "repository not found", "terminal prompts disabled",
          # Git zegt het ook zo, en dan staat "permission denied" op een
          # andere regel dan de regel die je uiteindelijk toont.
          "correct access rights", "repository exists")


def _lijkt_prive(reden: str) -> bool:
    laag = reden.lower()
    return any(t in laag for t in _PRIVE)


#: Regels waar de ECHTE oorzaak in staat. Git spreidt zijn fout over vijf
#: regels en zet de kern bovenaan:
#:
#:     git@github.com: Permission denied (publickey).
#:     fatal: Could not read from remote repository.
#:
#:     Please make sure you have the correct access rights
#:     and the repository exists.
#:
#: De laatste regel pakken levert dus "and the repository exists." op -- waar
#: en volstrekt nutteloos. Dit zoekt de regel die er wel toe doet.
_GIT_OORZAKEN = (
    "permission denied", "repository not found", "authentication failed",
    "could not read from remote", "invalid username or password",
    "terminal prompts disabled", "host key verification failed",
)


def _git_reden(ruw: str) -> str:
    """De regel uit een git-fout waar de oorzaak in staat."""
    from services.lab.clusters import _laatste_zinnige_regel

    for regel in (ruw or "").splitlines():
        kaal = regel.strip()
        if any(t in kaal.lower() for t in _GIT_OORZAKEN):
            return kaal[:300]
    return _laatste_zinnige_regel(ruw)


def _repomap(repo_url: str) -> str:
    naam = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    return re.sub(r"[^A-Za-z0-9._-]", "-", naam) or "repo"


def _model_vondsten(uitslag: Dict[str, Any]) -> List[det.Vondst]:
    """Het antwoord van het model als vondsten, elk nog ongestaafd."""
    uit: List[det.Vondst] = []

    for d in (uitslag.get("vaste_diensten") or [])[:12]:
        naam = _slug(d.get("naam")) if isinstance(d, dict) else ""
        if not naam:
            continue
        lab_omgeving, uit_kluis = _splits_plaatshouders(d.get("lab_omgeving") or {})
        uit.append(det.Vondst(
            soort="dienst", sleutel=naam,
            waarde={"image": str(d.get("image") or ""),
                    "poorten": [int(p) for p in (d.get("poorten") or []) if str(p).isdigit()],
                    "omgeving": {str(k): str(v) for k, v in (d.get("omgeving") or {}).items()},
                    "lab_omgeving": lab_omgeving,
                    "omgeving_uit_kluis": uit_kluis},
            bewijs=_model_bewijs(d.get("bewijs")),
            zekerheid="laag", bron="model", bevestigd=False,
            toelichting=_met_volledige_naam(d, str(d.get("waarom") or ""))[:400],
        ))

    for e in (uitslag.get("extern") or [])[:12]:
        naam = _slug(e.get("naam")) if isinstance(e, dict) else ""
        if not naam:
            continue
        uit.append(det.Vondst(
            soort="extern", sleutel=naam,
            waarde={"sleutels": [str(s) for s in (e.get("sleutels") or [])][:12]},
            bewijs=_model_bewijs(e.get("bewijs")),
            zekerheid="laag", bron="model", bevestigd=False,
            toelichting=_met_volledige_naam(e, str(e.get("waarom") or ""))[:400],
        ))

    li = uitslag.get("lab_image")
    if isinstance(li, dict) and li.get("image"):
        uit.append(det.Vondst(
            soort="lab_image", sleutel="voorstel",
            waarde={"image": str(li["image"]), "installeer": str(li.get("installeer") or "")},
            bewijs=_model_bewijs(li.get("bewijs")),
            zekerheid="laag", bron="model", bevestigd=False,
            toelichting=str(li.get("waarom") or "")[:400],
        ))

    poorten = [int(x) for x in (uitslag.get("lab_poorten") or []) if str(x).isdigit()]
    if poorten:
        uit.append(det.Vondst(
            # "voorstel" en niet "poorten": anders valt hij samen met de
            # vondst van de detector, en die wint het samenvoegen. Dan kan
            # het model de detector wél tegenspreken (uitsluiten) maar niet
            # VERVANGEN -- en blijft er helemaal geen poort over. Precies wat
            # er gebeurde: de detector gaf 8080/3000/80 uit het
            # productie-image, het model wist dat het 5173 moest zijn, en het
            # cluster hield er geen enkele over.
            soort="lab_poorten", sleutel="voorstel",
            waarde={"poorten": poorten[:6]},
            bewijs=_model_bewijs(uitslag.get("lab_poorten_bewijs")),
            zekerheid="laag", bron="model", bevestigd=False,
            toelichting=(
                "Hierop luistert de applicatie. LabX publiceert deze poorten naar "
                "buiten; staat hier het verkeerde nummer, dan is het lab onbereikbaar "
                "terwijl het wel draait."),
        ))

    if uitslag.get("geheimen_zelf_ophalen"):
        uit.append(det.Vondst(
            soort="omgeving", sleutel="geheimen_zelf_ophalen",
            waarde={"zelf": True},
            bewijs=_model_bewijs(uitslag.get("geheimen_zelf_bewijs")),
            zekerheid="laag", bron="model", bevestigd=False,
            toelichting=("Het project haalt zijn instellingen zelf op; dan hoeven ze "
                         "niet in de kluis van dit cluster."),
        ))

    # Env-paden die de DETECTOR niet kan zien. Die loopt langs bestanden die
    # in de repo staan; `components/app/.env.local` staat in .gitignore en
    # bestaat in een verse kloon dus niet -- terwijl de start-dev-skill van
    # IntelligentHive hem bij naam noemt. Documentatie lezen is precies wat
    # een model wel kan en een patroon niet.
    for pad in (uitslag.get("env_bestanden") or [])[:20]:
        pad = str(pad or "").strip()
        if not pad:
            continue
        uit.append(det.Vondst(
            soort="envbestand", sleutel=pad, waarde=None,
            bewijs=_model_bewijs(uitslag.get("env_bestanden_bewijs")),
            zekerheid="laag", bron="model", bevestigd=False,
            toelichting="Genoemd in de documentatie of de scripts, niet in de repo zelf.",
        ))

    # Extra pakketten uit de catalogus. Git, curl, jq en unzip komen al in
    # ELK lab (_BASE_STEPS), dus die hoort het model hier niet te noemen --
    # en als het toch gebeurt, valt het weg omdat de sleutel niet bestaat.
    for sleutel in (uitslag.get("lab_extras") or [])[:12]:
        sleutel = str(sleutel or "").strip().lower()
        if not sleutel:
            continue
        uit.append(det.Vondst(
            soort="extra", sleutel=sleutel, waarde=None,
            bewijs=_model_bewijs(uitslag.get("lab_extras_bewijs")),
            zekerheid="laag", bron="model", bevestigd=False,
            toelichting="Voorgesteld op grond van wat er in de repo staat.",
        ))

    profiel = uitslag.get("env_profiel")
    if isinstance(profiel, dict) and profiel.get("keuzes"):
        uit.append(det.Vondst(
            soort="omgeving", sleutel="env_profiel",
            waarde={"keuzes": [str(k) for k in profiel["keuzes"]][:12],
                    "standaard": str(profiel.get("standaard") or "")},
            bewijs=_model_bewijs(profiel.get("bewijs")),
            zekerheid="laag", bron="model", bevestigd=False,
            toelichting=str(profiel.get("waarom") or "")[:400],
        ))
    return uit


def _met_volledige_naam(bron: Dict[str, Any], waarom: str) -> str:
    """De naam zoals het model hem schreef erbij, als de slug hem inkortte."""
    ruw = str(bron.get("naam") or "")
    return f"{ruw} — {waarom}" if _slug(ruw) != ruw.lower() and waarom else (waarom or ruw)


def _model_bewijs(ruw: Any) -> List[det.Bewijs]:
    uit: List[det.Bewijs] = []
    for b in (ruw or [])[:6]:
        if isinstance(b, dict) and b.get("bestand"):
            regel = b.get("regel")
            uit.append(det.Bewijs(bestand=str(b["bestand"]),
                                  regel=int(regel) if str(regel or "").isdigit() else None))
        elif isinstance(b, str):
            uit.append(det.Bewijs(bestand=b))
    return uit


def _lees_json(ruw: str) -> Dict[str, Any]:
    """Het antwoord als JSON. Proza levert een leeg plan op, geen crash."""
    tekst = (ruw or "").strip()
    if tekst.startswith("```"):
        tekst = tekst.strip("`")
        tekst = tekst.split("\n", 1)[-1] if "\n" in tekst else tekst
        tekst = tekst.rsplit("```", 1)[0]
    begin, eind = tekst.find("{"), tekst.rfind("}")
    if begin >= 0 and eind > begin:
        try:
            uit = json.loads(tekst[begin:eind + 1])
            if isinstance(uit, dict):
                return uit
        except json.JSONDecodeError:
            pass
    log.infox("Verkenner kreeg geen JSON terug", lengte=len(tekst))
    return {"samenvatting": tekst[:600]}


SYSTEEM = """Je bent de verkenner van LabX. Je leest één repository en stelt voor hoe
daar een development cluster van gemaakt wordt.

Een cluster bestaat uit twee lagen:
- VASTE DIENSTEN: containers die blijven staan en door alle branches gedeeld
  worden. Een database, een emulator, een broker. Deze draaien NIET de code
  uit deze repo.
- BRANCH-LABS: per branch een container met de code uit deze repo, die de
  vaste diensten op naam bereikt (bijvoorbeeld http://cosmos:8081).

Harde regels:
1. GEEN VOORSTEL ZONDER VINDPLAATS. Noem bij elk onderdeel het bestand en
   zo mogelijk het regelnummer waarop je het baseert. Verzin geen bestanden;
   gebruik alleen paden die in de boom of in de inhoud staan.
2. Iets dat UIT DEZE REPO GEBOUWD wordt is de applicatie en hoort in een
   branch-lab, nooit in de vaste laag.
3. Een dienst waar geen emulator of image van bestaat (Application Insights,
   Sentry, een betaalde API) is GEEN vaste dienst. Die hoort bij "extern":
   daar is een geheim voor nodig.
   MAAR: haalt de repo zijn instellingen ZELF op -- een script dat ze uit een
   vault trekt, een SDK die ze bij het starten ophaalt -- zet dan
   `"geheimen_zelf_ophalen": true`. Dan hoeft er niets in de kluis en is het
   enige dat LabX moet leveren het gereedschap en een aanmelding. Iemand tien
   sleutels laten invullen die het project zelf ophaalt, is hem laten werken
   voor niets.
4. Omgevingsbestanden zijn geredigeerd: waarden van geheimen zijn weggelaten.
   Dat is geen fout en geen aanwijzing dat de sleutel ongebruikt is.
5. VRAAG NOOIT OM EEN WACHTWOORD VAN EEN DIENST DIE JE ZELF OPVOERT. Zet je
   een database of broker in `vaste_diensten`, dan maak JIJ de inloggegevens:
   dezelfde waarde in `omgeving` (waarmee de container start) en in
   `lab_omgeving` (waarmee de applicatie verbindt). Een plaatshouder hoort
   daar niet, en de gebruiker hoort er niet naar gevraagd te worden -- hij kan
   het niet weten, want de dienst bestaat nog niet. Alleen bij een ECHTE
   dienst van buiten (waar LabX geen container van maakt) hoort een geheim.
6. Noem een sleutel ÉÉN keer. Staat hij al bij een dienst onder `extern`, zet
   hem dan niet ook nog los in de lijst met losse geheimen -- dan wordt er
   twee keer om hetzelfde gevraagd.
7. EXTRA PAKKETTEN: LabX richt elk lab in met git, bash, curl, jq en unzip --
   ongeacht het image. Noem die dus niet. Heeft dit project meer nodig, kies
   dan uit deze catalogus en zet de SLEUTELS in `"lab_extras": [...]`:
{extras}
   Alleen wat de repo aantoonbaar nodig heeft; een pakket dat niemand
   gebruikt kost bij elke start installatietijd. Staat wat je nodig hebt er
   niet bij, zeg dat dan in `samenvatting` -- verzin geen sleutel die niet
   bestaat, want die valt stil weg.
8. Moet er een datastore ingericht worden (databases, collections, tabellen,
   schema's), zoek dan de definitie die het project ZELF als waarheid
   gebruikt: infra-templates (bicep, terraform, ARM), migraties, een
   schema-map. Een `setup`- of `seed`-script in de repo maakt vaak maar een
   deelverzameling aan -- genoeg voor waar het voor bedoeld was, te weinig om
   de applicatie te laten draaien. Dat verschil merk je pas als een scherm
   een 500 geeft op een collection die niemand heeft aangemaakt, en dan is
   de oorzaak ver weg van het symptoom.
9. Env-bestanden: LabX vindt zelf elk `.env*` dat IN de repo staat, en leidt
   uit `x/.env.example` af dat `x/.env` de plek is. Wat het NIET kan zien, is
   een bestand dat gitignored is en dus in een verse kloon ontbreekt terwijl
   de documentatie, een script of een skill het wel bij naam noemt --
   `components/app/.env.local` is zo'n geval. Kom je die tegen, zet het pad
   dan in `"env_bestanden": ["..."]`. Noem alleen paden die je ergens ZAG
   staan; verzin er geen, en herhaal niet wat al in de repo staat.
   Dit gaat over WAAR een bestand hoort, niet over wat erin staat: de inhoud
   levert de gebruiker, LabX zet hem bij elke start in de werkkopie.

WAT LABX ZELF REGELT — vraag hier niet naar, ga ervan uit:

- LabX publiceert de poort van het branch-lab naar buiten. De applicatie moet
  daarom binnen de container op 0.0.0.0 luisteren, nooit op 127.0.0.1 of
  localhost. Doet de repo dat niet uit zichzelf, dan DWINGT het startscript
  het af (`--host 0.0.0.0`, `HOST=0.0.0.0`, uvicorn rechtstreeks starten).
  Dit is geen vraag voor de gebruiker; het volgt uit hoe containers werken.
- Elk branch-lab krijgt deze variabelen van LabX. Gebruik ze in plaats van
  iets te verzinnen of te vragen:
    LABX_POORT        de poort waarop de applicatie IN de container hoort te
                      luisteren
    LABX_LAB_POORT    het poortnummer op de machine waarop hij van buiten
                      bereikbaar is
    LABX_LAB_URL      het volledige adres van dit lab van buitenaf; kan
                      ontbreken als het cluster op een andere machine draait
    LABX_BRANCH       de branch die dit lab draait
    LABX_ENV_PROFIEL  het gekozen omgevingsprofiel, als het cluster er een heeft
    LABX_ENV_BESTANDEN  de env-bestanden die LABX in de werkkopie heeft gezet,
                      met spaties gescheiden. Die zijn er al voordat jouw
                      script begint, met waarden die de gebruiker bewust koos.
                      Staat deze variabele gevuld, SLA DAN ELKE STAP OVER die
                      instellingen ergens anders vandaan haalt -- een vault,
                      een fetch-script, een `az keyvault`-aanroep. De
                      gebruiker is de bron, en dat is precies hoe een lokale
                      checkout ook werkt: zo'n stap zit meestal achter een
                      "draai ik in de devcontainer?"-controle en blijft daar
                      dus ook uit.
                      Doe je het toch, dan veeg je de import weg: die scripts
                      doen een `mv` of `>` over het pad, bij elke start
                      opnieuw en zonder spoor in het log.
  Hangt een variabele af van het adres van het lab (PUBLIC_BASE_URL,
  FRONTEND_BASE_URL, CORS_ORIGINS en dergelijke), zet hem dan in
  `lab_omgeving` af van $LABX_LAB_URL in plaats van ernaar te vragen.
- Zeg in `lab_poorten` op welke poort(en) de applicatie luistert. LabX
  publiceert die; raad je verkeerd, dan is het lab onbereikbaar terwijl het
  wel draait.

Antwoord met UITSLUITEND JSON, zonder uitleg eromheen:

{
  "samenvatting": "twee of drie zinnen: wat voor project dit is en hoe het draait",
  "vaste_diensten": [
    {"naam": "cosmos", "image": "...", "poorten": [8081],
     "omgeving": {"ACCEPT_EULA": "Y"},
     "lab_omgeving": {"COSMOS_ENDPOINT": "https://cosmos:8081"},
     "waarom": "één zin",
     "bewijs": [{"bestand": "components/api/.env.example", "regel": 12}]}
  ],
  "extern": [
    {"naam": "langfuse", "sleutels": ["LANGFUSE_SECRET_KEY"],
     "waarom": "één zin", "bewijs": [{"bestand": "...", "regel": 1}]}
  ],
  "lab_image": {"image": "node:22-bookworm", "installeer": "corepack enable && yarn install --immutable",
                "waarom": "één zin", "bewijs": [{"bestand": "package.json", "regel": 3}]},
  "lab_poorten": [8000],
  "startscript": "#!/bin/sh\\nset -e\\n... bouwen en starten, in de juiste volgorde",
  "env_profiel": {"keuzes": ["debug"], "standaard": "debug", "waarom": "..."},
  "geheimen_zelf_ophalen": false,
  "niet_overnemen": [
    {"soort": "dienst", "sleutel": "postgres", "waarom": "hoort bij de agent, en die valt buiten de opdracht"}
  ],
  "vragen": ["hooguit drie dingen die je niet uit de repo kon opmaken"],
  "lees_ook": ["pad/naar/bestand"]
}

Het startscript draait IN het branch-lab, in de werkkopie, als root, met
alleen wat het lab-image biedt. Schrijf het zoals iemand het met de hand zou
doen: eerst afhankelijkheden, dan bouwen, dan starten. Laat het draaien op de
voorgrond, zodat de container blijft leven, en op 0.0.0.0 -- een lab dat
alleen op localhost luistert is van buiten onbereikbaar terwijl het er wel
uitziet alsof het draait.

`niet_overnemen` is voor wat de DETECTOREN vonden en wat er toch niet in
hoort. Zet je iets op die lijst waar wél iets voor in de plaats moet komen --
andere poorten, een ander image -- geef dan OOK het juiste antwoord in het
bijbehorende veld (`lab_poorten`, `lab_image`). Alleen uitsluiten laat het
cluster met niets achter. Een detector ziet wat er in de repo staat en weet niet wat de gebruiker
ermee wil: zegt hij "de agent hoort er niet bij", dan blijft `postgres` gewoon
gevonden worden. Zet zulke vondsten hier, met de soort en sleutel zoals ze in
de lijst hierboven staan. Ze verdwijnen niet -- het bewijs blijft waar -- maar
ze staan niet meer vooraf aan.

Weet je iets niet, zet het dan in "vragen" in plaats van te gokken. Stel een
vraag alleen als het ANTWOORD het plan of het startscript verandert -- de
gebruiker kan erop antwoorden, en dan verken je opnieuw met zijn antwoord
erbij. Een vraag die nergens toe leidt kost hem alleen tijd.
Wil je een bestand zien dat wel in de boom staat maar niet in de inhoud, zet
het pad dan in "lees_ook"."""


def _extras_lijst() -> str:
    """De extra-catalogus als regels voor de prompt.

    Uit de catalogus zelf, niet overgetypt: een pakket dat erbij komt hoort
    het model te bereiken zonder dat iemand deze tekst bijwerkt. Een sleutel
    die het model verzint bestaat niet en valt stil weg, dus de echte lijst
    meegeven is het verschil tussen een voorstel en een gok.
    """
    from services.lab.extras_catalog import BUILTIN_EXTRAS

    regels = []
    for spec in BUILTIN_EXTRAS:
        omschrijving = str(spec.get("description") or spec.get("label") or "")
        regels.append(f"     {spec['key']:<20} {omschrijving[:110]}")
    return "\n".join(regels)


def _prompt(*, repo_url: str, branch: str, beeld: Dict[str, Any],
            inhoud: det.RepoBeeld, vondsten: List[det.Vondst],
            instructie: Optional[str] = None,
            azure_sessie: Optional[str] = None,
            antwoorden: Optional[Dict[str, str]] = None,
            opmerkingen: Optional[Dict[str, str]] = None,
            opmerking: Optional[str] = None,
            keuze: Optional[List[Dict[str, str]]] = None) -> str:
    """Het beeld als tekst, met wat de detectoren al gevonden hebben erbij.

    De detectorvondsten gaan MEE naar het model, als vertrekpunt en niet als
    antwoord. Dat scheelt het model het opzoekwerk dat al gedaan is, en het
    geeft hem meteen de vorm waarin hij zelf moet antwoorden.
    """
    # `.replace` en niet `.format`: SYSTEEM staat vol accolades (JSON-voorbeelden)
    # en die zou format als plaatshouders lezen.
    delen = [SYSTEEM.replace("{extras}", _extras_lijst()), "",
             f"Repository: {repo_url}", f"Branch: {branch}", ""]

    if azure_sessie:
        delen += [
            "ER IS AL EEN AZURE-AANMELDING in het lab. `az` is ingelogd als",
            f"{azure_sessie} -- een GEBRUIKERSsessie, gezet door LabX uit het",
            "profiel van het cluster. Gebruik die.",
            "",
            "Dus: GEEN `az login`, en zeker geen service principal met",
            "AZURE_CLIENT_ID/AZURE_CLIENT_SECRET -- die zijn er niet, en een script",
            "dat erop wacht slaat de hele stap over. Wil je weten of je erbij kunt,",
            "doe dan gewoon de aanroep (`az keyvault secret list ...`) en behandel",
            "een fout als een fout.",
            "",
        ]

    if instructie:
        # Vooraan, en met zoveel woorden zwaarder gewogen dan de rest: dit is
        # wat de gebruiker WIL, en de repo zegt alleen wat er is. Een briefing
        # die ergens onderaan tussen de bestanden staat, leest als een detail.
        delen += ["OPDRACHT VAN DE GEBRUIKER — dit is leidend. Wijkt de repo hiervan af,",
                  "volg dan deze opdracht en zeg in `samenvatting` wat je daarvoor hebt",
                  "laten vallen:",
                  str(instructie)[:4000], ""]

    if opmerking or opmerkingen or keuze:
        delen += ["WAT DE GEBRUIKER VAN HET VORIGE PLAN VOND — dit weegt zwaarder dan",
                  "wat jij of de detectoren ervan vonden. Neem het over:"]
        if opmerking:
            delen.append(f"- OVER HET PLAN: {str(opmerking)[:2000]}")
        for kern, tekst in list((opmerkingen or {}).items())[:20]:
            delen.append(f"- OVER {kern}: {str(tekst)[:800]}")
        if keuze:
            aan = ", ".join(f"{k.get('soort')}/{k.get('sleutel')}" for k in keuze[:30])
            delen += [f"- HIJ KOOS: {aan or 'niets'}",
                      "  Wat hij UITvinkte hoort niet in het nieuwe plan terug, tenzij",
                      "  zijn opmerking iets anders zegt."]
        delen.append("")

    if antwoorden:
        delen += ["AL BEANTWOORD DOOR DE GEBRUIKER — stel deze vragen NIET opnieuw,",
                  "en verwerk de antwoorden in het plan en het startscript:"]
        for vraag, antwoord in list(antwoorden.items())[:12]:
            delen += [f"- VRAAG: {str(vraag)[:500]}", f"  ANTWOORD: {str(antwoord)[:1500]}"]
        delen.append("")

    boom = beeld.get("boom") or []
    delen += [f"BESTANDEN ({len(boom)}{'+' if beeld.get('boom_afgekapt') else ''}):",
              "\n".join(boom[:800]), ""]

    if vondsten:
        delen += ["WAT DE DETECTOREN AL VONDEN (vertrekpunt, geen antwoord):"]
        for v in vondsten:
            plek = ", ".join(f"{b.bestand}:{b.regel or '?'}" for b in v.bewijs[:3])
            delen.append(f"- {v.soort}/{v.sleutel} ({v.zekerheid}) via {plek}")
        delen.append("")

    delen.append("INHOUD:")
    for pad, tekst in inhoud.items():
        delen += [f"--- {pad} ---", tekst[:20000], ""]
    return "\n".join(delen)
