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
MODEL_TIJD = 300.0


class Verkenner:
    """Leest een repo en stelt een cluster voor."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── de buitenkant ────────────────────────────────────────────────────────

    async def verken(self, *, repo_url: str, branch: str = "main",
                     token_geheim: Optional[str] = None,
                     host_id: Optional[int] = None) -> Dict[str, Any]:
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
                                    token_geheim=token_geheim)
            beeld_ruw = await self._beeld(runtime, volume, image, map_=kop["map"])
            inhoud: det.RepoBeeld = dict(beeld_ruw.get("inhoud") or {})

            vondsten = det.detecteer_alles(inhoud)
            modeluitslag = await self._vraag_model(
                runtime, volume, image, map_=kop["map"],
                beeld=beeld_ruw, inhoud=inhoud, vondsten=vondsten,
                repo_url=repo_url, branch=branch)
            inhoud.update(modeluitslag.pop("_nagelezen", {}))
            vondsten = self._voeg_samen(vondsten, modeluitslag, inhoud)

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
                     token_geheim: Optional[str]) -> Dict[str, Any]:
        """Ondiep klonen. We lezen alleen; historie hebben we hier niet nodig.

        Dit is bewust NIET dezelfde kloon als die van een branch-lab: daar
        mag `--depth 1` juist niet, want daar wil je van een basis aftakken en
        later pushen.
        """
        naam = _repomap(repo_url)
        env = {"REPO_URL": repo_url, "DEST": naam, "TAK": branch}
        token = self._token(token_geheim)
        if token:
            env["GIT_AUTH"] = base64.b64encode(f"x-access-token:{token}".encode()).decode()

        # De header in de git-CONFIG zetten, niet per opdracht meegeven: de
        # shell splitst zo'n expansie alsnog op spaties, en git ziet "basic"
        # dan als een subcommando.
        script = (
            'set -e; '
            'if [ -n "$GIT_AUTH" ]; then '
            '  git config --global http.extraHeader "AUTHORIZATION: basic $GIT_AUTH"; '
            'fi; '
            'git clone --depth 1 --branch "$TAK" "$REPO_URL" "$DEST" >/dev/null 2>&1 '
            '  || git clone --depth 1 "$REPO_URL" "$DEST" >/dev/null 2>&1; '
            'cd "$DEST"; '
            'echo "COMMIT=$(git rev-parse HEAD)"; '
            'echo "BRANCH=$(git rev-parse --abbrev-ref HEAD)"'
        )
        try:
            uit = await runtime.run_ephemeral(
                image=image, volume=volume, cmd=["sh", "-c", script],
                env=env, timeout=KLOON_TIJD)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=f"Verkennen mislukte bij het klonen: {str(exc)[:400]}") from exc

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
                           branch: str) -> Dict[str, Any]:
        """Het model leest de repo en stelt het cluster voor.

        Valt dit om — geen sleutel, geen netwerk, een tijdslimiet — dan is dat
        geen mislukte verkenning. De detectoren hebben dan al een plan
        neergelegd; het is alleen dunner, en het plan zegt erbij dat het model
        niet meegekeken heeft.
        """
        prompt = _prompt(repo_url=repo_url, branch=branch, beeld=beeld,
                         inhoud=inhoud, vondsten=vondsten)
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
            default_model=getattr(s, "cli_default_model", None) or "sonnet",
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
        """
        op_sleutel = {(v.soort, v.sleutel): v for v in vondsten}

        for mv in _model_vondsten(uitslag):
            kern = (mv.soort, mv.sleutel)
            bestaand = op_sleutel.get(kern)
            if bestaand is not None:
                if mv.toelichting:
                    bestaand.toelichting = mv.toelichting
                continue
            op_sleutel[kern] = det.staaf(mv, inhoud)

        volgorde = {"dienst": 0, "lab_image": 1, "startscript": 2,
                    "omgeving": 3, "extern": 4, "geheim": 5}
        return sorted(op_sleutel.values(),
                      key=lambda v: (volgorde.get(v.soort, 9),
                                     0 if v.bevestigd else 1,
                                     v.sleutel))


# ── hulpjes ──────────────────────────────────────────────────────────────────

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
        uit.append(det.Vondst(
            soort="dienst", sleutel=naam,
            waarde={"image": str(d.get("image") or ""),
                    "poorten": [int(p) for p in (d.get("poorten") or []) if str(p).isdigit()],
                    "omgeving": {str(k): str(v) for k, v in (d.get("omgeving") or {}).items()},
                    "lab_omgeving": {str(k): str(v) for k, v in (d.get("lab_omgeving") or {}).items()}},
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
4. Omgevingsbestanden zijn geredigeerd: waarden van geheimen zijn weggelaten.
   Dat is geen fout en geen aanwijzing dat de sleutel ongebruikt is.

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
  "startscript": "#!/bin/sh\\nset -e\\n... bouwen en starten, in de juiste volgorde",
  "env_profiel": {"keuzes": ["debug"], "standaard": "debug", "waarom": "..."},
  "vragen": ["hooguit drie dingen die je niet uit de repo kon opmaken"],
  "lees_ook": ["pad/naar/bestand"]
}

Het startscript draait IN het branch-lab, in de werkkopie, als root, met
alleen wat het lab-image biedt. Schrijf het zoals iemand het met de hand zou
doen: eerst afhankelijkheden, dan bouwen, dan starten. Laat het draaien op de
voorgrond, zodat de container blijft leven.

Weet je iets niet, zet het dan in "vragen" in plaats van te gokken.
Wil je een bestand zien dat wel in de boom staat maar niet in de inhoud, zet
het pad dan in "lees_ook"."""


def _prompt(*, repo_url: str, branch: str, beeld: Dict[str, Any],
            inhoud: det.RepoBeeld, vondsten: List[det.Vondst]) -> str:
    """Het beeld als tekst, met wat de detectoren al gevonden hebben erbij.

    De detectorvondsten gaan MEE naar het model, als vertrekpunt en niet als
    antwoord. Dat scheelt het model het opzoekwerk dat al gedaan is, en het
    geeft hem meteen de vorm waarin hij zelf moet antwoorden.
    """
    delen = [SYSTEEM, "", f"Repository: {repo_url}", f"Branch: {branch}", ""]

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
