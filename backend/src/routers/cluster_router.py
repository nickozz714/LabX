"""routers/cluster_router.py — development clusters.

Een cluster is een groep containers die bij elkaar horen: vaste diensten die
blijven staan, en branch-labs die komen en gaan. Zie docs/CLUSTERS.md.

De endpoints zijn bewust dun: alles wat een beslissing is, staat in
ClusterService. Dat scheelt een tweede plek waar de regels wonen.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from services.lab.clusters import ClusterService
from services.lab.branch_git import BranchGit
from services.lab.bundels import MAX_ARCHIEF_BYTES, BundelService
from services.lab.dienst_acties import DienstActieService

router = APIRouter(prefix="/clusters", tags=["clusters"],
                   dependencies=[Depends(require_user)])


def _svc(db: Session) -> ClusterService:
    return ClusterService(db)


@router.get("")
def lijst(db: Session = Depends(get_db)):
    svc = _svc(db)
    return [svc.to_dict(c) for c in svc.lijst()]


@router.post("")
def maak(body: Dict[str, Any], db: Session = Depends(get_db)):
    svc = _svc(db)
    c = svc.maak(naam=str(body.get("naam") or ""),
                 host_id=(int(body["host_id"]) if body.get("host_id") else None),
                 **{k: v for k, v in body.items() if k not in ("naam", "host_id")})
    return svc.to_dict(c)


@router.get("/{cluster_id}")
def lees(cluster_id: int, db: Session = Depends(get_db)):
    svc = _svc(db)
    return svc.to_dict(svc.get_or_404(cluster_id))


@router.patch("/{cluster_id}")
def wijzig(cluster_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    svc = _svc(db)
    return svc.to_dict(svc.wijzig(cluster_id, **body))


@router.delete("/{cluster_id}")
async def verwijder(cluster_id: int, db: Session = Depends(get_db)):
    return await _svc(db).verwijder(cluster_id)


# ── branch-labs ──────────────────────────────────────────────────────────────

@router.post("/{cluster_id}/branches")
async def maak_branch_lab(cluster_id: int, body: Dict[str, Any],
                          db: Session = Depends(get_db)):
    """Een lab voor één branch, afgetakt van een basis naar keuze.

    `basis` is niet standaard "main" omdat dat vaak klopt maar niet altijd:
    van `release/2026.09` aftakken is net zo gewoon, en dat achteraf
    rechtzetten in een lab is omslachtiger dan het meteen goed doen.
    """
    return await _svc(db).maak_branch_lab(
        cluster_id,
        branch=str(body.get("branch") or ""),
        basis=str(body.get("basis") or "main"),
        titel=str(body.get("titel") or ""),
        beschrijving=str(body.get("beschrijving") or ""),
        naam=(str(body["naam"]) if body.get("naam") else None))


@router.post("/{cluster_id}/toegang")
async def controleer_toegang(cluster_id: int, db: Session = Depends(get_db)):
    """Kan dit cluster bij zijn repo, en mag het pushen?

    Meten in plaats van het bij de eerste kloon te ontdekken: een git-fout
    zegt "403" waar drie verschillende oorzaken achter kunnen zitten.
    """
    svc = _svc(db)
    return await svc.controleer_toegang(svc.get_or_404(cluster_id))


@router.post("/branches/{lab_id}/herstart-app")
async def branch_app_herstarten(lab_id: str, db: Session = Depends(get_db)):
    """De applicatie in dit branch-lab opnieuw starten.

    Nodig zodra het startscript bouwt in plaats van een devserver draait: een
    agent die de code wijzigt is dan pas te zien na een nieuwe bouw. Bij een
    devserver met file-watching (Vite, nodemon) hoeft dit niet -- die pikt de
    wijziging zelf op.
    """
    from services.lab.lab_service import LabService

    return await LabService(db).start_applicatie(lab_id)


# ── env-bestanden van het cluster ────────────────────────────────────────────
#
# De bestanden die een applicatie lokaal laten werken staan vaak in
# .gitignore en nergens anders. Het cluster is de plek om ze te bewaren: hij
# hoort al bij één repo en elk branch-lab eronder heeft ze nodig. Zie
# services/lab/cluster_env.py.


@router.get("/{cluster_id}/env-bestanden")
def env_bestanden(cluster_id: int, db: Session = Depends(get_db)):
    """Welke bestanden er staan, met de NAMEN van hun sleutels -- nooit de waarden."""
    from services.lab.cluster_env import ClusterEnvBestanden

    _svc(db).get_or_404(cluster_id)
    return ClusterEnvBestanden(db).lijst(cluster_id)


@router.post("/{cluster_id}/env-bestanden")
def env_bestand_zetten(cluster_id: int, payload: Dict[str, Any],
                       db: Session = Depends(get_db)):
    """Een env-bestand importeren of vervangen.

    `pad` is waar het in de werkkopie hoort (bijvoorbeeld
    `components/api/.env`), `inhoud` is het bestand zelf.
    """
    from services.lab.cluster_env import ClusterEnvBestanden

    _svc(db).get_or_404(cluster_id)
    return ClusterEnvBestanden(db).zet(cluster_id,
                                       str(payload.get("pad") or ""),
                                       str(payload.get("inhoud") or ""))


@router.delete("/{cluster_id}/env-bestanden/{bestand_id}")
def env_bestand_verwijderen(cluster_id: int, bestand_id: int,
                            db: Session = Depends(get_db)):
    from services.lab.cluster_env import ClusterEnvBestanden

    _svc(db).get_or_404(cluster_id)
    return ClusterEnvBestanden(db).verwijder(cluster_id, bestand_id)


@router.get("/branches/{lab_id}/tunnel")
def branch_tunnel(lab_id: str, os: str = "mac", poort: int | None = None,
                  container_poort: int | None = None,
                  db: Session = Depends(get_db)):
    """Een scriptje dat een VASTE poort op jouw machine naar dit lab stuurt.

    Nodig omdat LabX oplopende machinepoorten uitdeelt en sommige applicaties
    juist een vast nummer eisen: een OAuth-redirect staat op
    `http://localhost:5173` geregistreerd en nergens anders. Zie
    services/lab/tunnel.py voor waarom je hem zelf draait.
    """
    from fastapi.responses import PlainTextResponse
    from services.lab.tunnel import Tunnel

    uit = Tunnel(db).script(lab_id, os_naam=os, lokaal=poort,
                            container_poort=container_poort)
    return PlainTextResponse(
        uit["inhoud"],
        headers={"Content-Disposition": f'attachment; filename="{uit["bestandsnaam"]}"',
                 # De front-end leest deze om te kunnen zeggen welk adres je
                 # straks moet openen; zonder dit is het script stil over
                 # waar je terechtkomt.
                 "X-Labx-Tunnel-Url": uit["url"],
                 "Access-Control-Expose-Headers": "X-Labx-Tunnel-Url"})


# ── git aan een branch-lab ───────────────────────────────────────────────────
#
# Een branch-lab IS een werkkopie van een echte branch, maar je kon er alles
# mee behalve het enige waar het om gaat: je werk eruit krijgen. Dat moest via
# de terminal in het lab, met een token dat daar niet hoort te staan.


def _git(db: Session) -> BranchGit:
    return BranchGit(db)


@router.get("/branches/{lab_id}/git")
async def branch_git_status(lab_id: str, db: Session = Depends(get_db)):
    return await _git(db).status(lab_id)


@router.post("/branches/{lab_id}/git/push")
async def branch_git_push(lab_id: str, body: Dict[str, Any],
                          db: Session = Depends(get_db)):
    return await _git(db).push(
        lab_id,
        bericht=str(body.get("bericht") or ""),
        bestanden=[str(x) for x in (body.get("bestanden") or [])],
        alles=bool(body.get("alles")))


@router.patch("/branches/{lab_id}")
def branch_wijzigen(lab_id: str, body: Dict[str, Any],
                    db: Session = Depends(get_db)):
    """Titel en beschrijving bijstellen.

    De BRANCHNAAM staat hier bewust niet bij: die is na het aanmaken een feit
    in de werkkopie en op de remote, en hem in LabX hernoemen zou alleen de
    naam in dit scherm veranderen -- met een push die daarna naar een branch
    gaat die niemand kent.
    """
    from models.lab import Lab

    lab = db.get(Lab, lab_id)
    if lab is None or not lab.cluster_id:
        raise HTTPException(status_code=404,
                            detail="Dit is geen branch-lab van een cluster.")
    if "titel" in body:
        lab.feature_titel = str(body["titel"]).strip()[:200] or None
    if "beschrijving" in body:
        lab.feature_beschrijving = str(body["beschrijving"]).strip() or None
    db.commit()
    return {"ok": True, "titel": lab.feature_titel or "",
            "beschrijving": lab.feature_beschrijving or ""}


@router.get("/branches/{lab_id}/start-log")
async def branch_start_log(lab_id: str, db: Session = Depends(get_db)):
    """De uitvoer van het startscript. Waar je kijkt als er niets antwoordt."""
    from services.lab.lab_service import LabService

    return {"log": await LabService(db).start_log(lab_id, regels=200)}


@router.post("/branches/{lab_id}/start")
async def start_branch_lab(lab_id: str, db: Session = Depends(get_db)):
    """Een branch-lab aanzetten op het netwerk van zijn cluster."""
    return await _svc(db).start_branch_lab(lab_id)


# ── vaste diensten ───────────────────────────────────────────────────────────

@router.post("/{cluster_id}/diensten")
def voeg_dienst_toe(cluster_id: int, body: Dict[str, Any],
                    db: Session = Depends(get_db)):
    svc = _svc(db)
    d = svc.voeg_dienst_toe(cluster_id, naam=str(body.get("naam") or ""),
                            image=str(body.get("image") or ""),
                            poorten=body.get("poorten"),
                            omgeving=body.get("omgeving"))
    return svc.dienst_dict(d)


@router.post("/diensten/{dienst_id}/start")
async def start_dienst(dienst_id: int, db: Session = Depends(get_db)):
    svc = _svc(db)
    return svc.dienst_dict(await svc.start_dienst(dienst_id))


@router.post("/diensten/{dienst_id}/stop")
async def stop_dienst(dienst_id: int, db: Session = Depends(get_db)):
    svc = _svc(db)
    return svc.dienst_dict(await svc.stop_dienst(dienst_id))


@router.delete("/diensten/{dienst_id}")
async def verwijder_dienst(dienst_id: int, db: Session = Depends(get_db)):
    await _svc(db).verwijder_dienst(dienst_id)
    return {"ok": True}


# ── bundels: bestanden van buiten, met een opdracht erbij ────────────────────
#
# Een vaste dienst begint leeg, en de bron waaruit hij gevuld hoort te worden
# is niet altijd bereikbaar -- een firewall, een omgeving waar je niet bij
# kunt. De export staat dan gewoon op je schijf. Die hier naar binnen kunnen
# brengen is het verschil tussen een werkend cluster en wachten op iemand
# anders.


def _bundels(db: Session) -> BundelService:
    return BundelService(db)


@router.get("/{cluster_id}/bundels")
def bundels(cluster_id: int, db: Session = Depends(get_db)):
    svc = _bundels(db)
    return [svc.dict(b) for b in svc.lijst(cluster_id)]


@router.post("/{cluster_id}/bundels")
async def bundel_uploaden(cluster_id: int,
                          bestand: UploadFile = File(...),
                          naam: str = Form(""),
                          instructie: str = Form(""),
                          db: Session = Depends(get_db)):
    """Een archief binnenhalen en uitpakken in een eigen volume.

    De upload gaat in stukjes naar schijf en niet in het geheugen: een
    Cosmos-export is tientallen megabytes, en die in één `await read()` laden
    zou de backend evenveel geheugen kosten als het bestand groot is.
    """
    import os
    import tempfile

    gezien = 0
    fd, pad = tempfile.mkstemp(prefix="labx-upload-")
    try:
        with os.fdopen(fd, "wb") as uit:
            while brok := await bestand.read(1024 * 1024):
                gezien += len(brok)
                if gezien > MAX_ARCHIEF_BYTES:
                    raise HTTPException(status_code=400,
                                        detail="Het archief is te groot.")
                uit.write(brok)
        svc = _bundels(db)
        b = await svc.zet(cluster_id, bestandsnaam=bestand.filename or "bundel.zip",
                          pad=pad, naam=naam, instructie=instructie)
        return svc.dict(b)
    finally:
        try:
            os.unlink(pad)
        except OSError:
            pass


@router.patch("/bundels/{bundel_id}")
def bundel_wijzigen(bundel_id: int, body: Dict[str, Any],
                    db: Session = Depends(get_db)):
    svc = _bundels(db)
    return svc.dict(svc.wijzig(bundel_id, naam=body.get("naam"),
                               instructie=body.get("instructie")))


@router.delete("/bundels/{bundel_id}")
async def bundel_weg(bundel_id: int, db: Session = Depends(get_db)):
    await _bundels(db).verwijder(bundel_id)
    return {"ok": True}


@router.post("/bundels/{bundel_id}/toepassen")
async def bundel_toepassen(bundel_id: int, body: Dict[str, Any],
                           db: Session = Depends(get_db)):
    return await _bundels(db).toepassen(
        bundel_id,
        dienst_id=int(body.get("dienst_id") or 0),
        instructie=str(body.get("instructie") or ""),
        script=str(body.get("script") or ""),
        image=str(body.get("image") or ""))


# ── knoppen bij een vaste dienst ─────────────────────────────────────────────
#
# Een vaste dienst is geen lab: er staat geen agent in en je kunt er niet in
# chatten. Toch is hij juist het ding waar je wat MEE moet -- Cosmos leegmaken,
# opnieuw seeden, even kijken wat erin zit. Het model schrijft die knoppen en
# het script eronder; LabX voert ze uit en bewaart de uitslag.


def _acties(db: Session) -> DienstActieService:
    return DienstActieService(db)


@router.get("/diensten/{dienst_id}/acties")
def dienst_acties(dienst_id: int, db: Session = Depends(get_db)):
    svc = _acties(db)
    return [svc.dict(a) for a in svc.lijst(dienst_id)]


@router.post("/diensten/{dienst_id}/acties/voorstellen")
async def dienst_acties_voorstellen(dienst_id: int, body: Dict[str, Any] | None = None,
                                    db: Session = Depends(get_db)):
    return await _acties(db).voorstellen(
        dienst_id, opdracht=str((body or {}).get("opdracht") or ""))


@router.post("/diensten/{dienst_id}/acties")
def dienst_actie_zetten(dienst_id: int, body: Dict[str, Any],
                        db: Session = Depends(get_db)):
    svc = _acties(db)
    a = svc.zet(dienst_id,
                actie_id=body.get("id"),
                label=str(body.get("label") or ""),
                script=str(body.get("script") or ""),
                uitleg=str(body.get("uitleg") or ""),
                plek=str(body.get("plek") or "in"),
                image=str(body.get("image") or ""),
                gevaarlijk=bool(body.get("gevaarlijk")),
                bron=str(body.get("bron") or "zelf"))
    return svc.dict(a)


@router.post("/diensten/acties/{actie_id}/draaien")
async def dienst_actie_draaien(actie_id: int, db: Session = Depends(get_db)):
    return await _acties(db).draai(actie_id)


@router.delete("/diensten/acties/{actie_id}")
def dienst_actie_weg(actie_id: int, db: Session = Depends(get_db)):
    _acties(db).verwijder(actie_id)
    return {"ok": True}


@router.post("/diensten/{dienst_id}/chat")
async def dienst_chat(dienst_id: int, body: Dict[str, Any],
                      db: Session = Depends(get_db)):
    return await _acties(db).chat(
        dienst_id, str(body.get("vraag") or ""),
        geschiedenis=list(body.get("geschiedenis") or []))


# ── de proefbouw ─────────────────────────────────────────────────────────────
#
# Een plan dat er goed uitziet maar waarvan het script omvalt, heeft zijn werk
# niet af. En "af" is niet "exitcode 0": af is wanneer de gebruiker zegt dat
# de branch goed is. Daarom blijft het proeflab staan tot hij bevestigt.


@router.post("/{cluster_id}/proefbouw")
async def proefbouw(cluster_id: int, body: Dict[str, Any] | None = None,
                    db: Session = Depends(get_db)):
    """Het startscript echt draaien op een proefbranch, en bijstellen tot het
    werkt. Duurt zo lang als de bouw duurt."""
    from services.lab.proefbouw import Proefbouw

    return await Proefbouw(db).start(
        cluster_id, branch=(str((body or {}).get("branch")) if (body or {}).get("branch") else None))


@router.post("/{cluster_id}/proefbouw/bevestigen")
def proefbouw_bevestigen(cluster_id: int, db: Session = Depends(get_db)):
    """Jij zegt dat de branch goed is. Pas nu is het cluster klaar."""
    from services.lab.proefbouw import Proefbouw

    return Proefbouw(db).bevestig(cluster_id)


@router.post("/{cluster_id}/proefbouw/afkeuren")
async def proefbouw_afkeuren(cluster_id: int, body: Dict[str, Any],
                             db: Session = Depends(get_db)):
    """Nog niet goed. Jouw woorden gaan mee de volgende ronde in."""
    from services.lab.proefbouw import Proefbouw

    return await Proefbouw(db).afkeuren(
        cluster_id, opmerking=str(body.get("opmerking") or ""))


# ── de kluis van een cluster ─────────────────────────────────────────────────
#
# Apart van de gedeelde kluis, want een cluster verwijst naar geheimen met de
# naam van een OMGEVINGSVARIABELE -- JWT_SECRET, DB_PASS -- en die namen zijn
# juist niet uniek. In de gedeelde kluis vechten twee clusters dan om
# hetzelfde geheim, en wie het laatst opslaat wint, zonder dat iemand het
# merkt: de waarde is nergens te zien.


@router.post("/{cluster_id}/sleutel")
def sleutel_maken(cluster_id: int, db: Session = Depends(get_db)):
    """Een deploy key voor dit cluster maken.

    De publieke helft komt terug; die zet jij bij de repo onder
    Settings → Deploy keys. Deploy keys zijn op repo-niveau en vallen buiten
    het OAuth- en PAT-goedkeuringsbeleid van een organisatie.
    """
    return _svc(db).maak_sleutel(cluster_id)


@router.delete("/{cluster_id}/sleutel")
def sleutel_weg(cluster_id: int, db: Session = Depends(get_db)):
    return _svc(db).verwijder_sleutel(cluster_id)


@router.get("/{cluster_id}/geheimen")
def geheimen(cluster_id: int, db: Session = Depends(get_db)):
    """Wat er in de kluis van dit cluster staat, en wat er nog ontbreekt.

    Nooit waarden -- alleen namen, lengtes en waar ze vandaan komen.
    """
    from services.lab.cluster_env import ClusterEnvBestanden
    from services.lab.cluster_kluis import ClusterKluis

    svc = _svc(db)
    c = svc.get_or_404(cluster_id)
    kluis = ClusterKluis(db)
    eigen = {r.naam for r in kluis.lijst(cluster_id)}
    nodig = c.benodigde_geheimen or {}

    # Wat een geïmporteerd env-bestand al levert, hoeft niet in de kluis.
    # Zonder dit staat er een rode "ontbreekt" bij een sleutel die allang
    # geleverd wordt, en vraagt het scherm om een waarde die je niet hoeft
    # te geven -- precies het werk dat importeren juist wegnam.
    uit_env: Dict[str, str] = {}
    for bestand in ClusterEnvBestanden(db).lijst(cluster_id):
        for sleutel in bestand["sleutels"]:
            uit_env.setdefault(sleutel, bestand["pad"])

    return {
        "eigen": [kluis.to_dict(r) for r in kluis.lijst(cluster_id)],
        "benodigd": [
            {"naam": n, "waarvoor": w,
             "in_eigen_kluis": n in eigen,
             "in_env_bestand": uit_env.get(n),
             "gevonden": kluis.bestaat(cluster_id, n) or n in uit_env}
            for n, w in sorted(nodig.items())
        ],
    }


@router.put("/{cluster_id}/geheimen/{naam}")
def geheim_zetten(cluster_id: int, naam: str, body: Dict[str, Any],
                  db: Session = Depends(get_db)):
    from services.lab.cluster_kluis import ClusterKluis

    _svc(db).get_or_404(cluster_id)
    rij = ClusterKluis(db).zet(
        cluster_id, naam=naam,
        waarde=(str(body["waarde"]) if body.get("waarde") else None),
        omschrijving=(str(body["omschrijving"]) if body.get("omschrijving") is not None else None))
    return ClusterKluis(db).to_dict(rij)


@router.delete("/{cluster_id}/geheimen/{naam}")
def geheim_weg(cluster_id: int, naam: str, db: Session = Depends(get_db)):
    from services.lab.cluster_kluis import ClusterKluis

    _svc(db).get_or_404(cluster_id)
    return {"ok": ClusterKluis(db).verwijder(cluster_id, naam)}


# ── verkennen en goedkeuren ──────────────────────────────────────────────────
#
# `/verkennen` maakt NIETS aan. Het kloont ondiep in een wegwerpcontainer,
# leest, en levert een voorstel. Pas `/goedkeuren` zet dat om in een cluster.
# Die scheiding is de hele kern: er draait geen container waar niemand op
# geklikt heeft.


@router.post("/verkennen")
async def verkennen(body: Dict[str, Any], db: Session = Depends(get_db)):
    svc = _svc(db)
    rij = await svc.verken(
        repo_url=str(body.get("repo_url") or ""),
        branch=str(body.get("branch") or "main"),
        token_geheim=(str(body["token_geheim"]) if body.get("token_geheim") else None),
        host_id=(int(body["host_id"]) if body.get("host_id") else None),
        cluster_id=(int(body["cluster_id"]) if body.get("cluster_id") else None),
        instructie=(str(body["instructie"]) if body.get("instructie") else None),
        # Env-bestanden mogen hier al mee: [{"pad": ..., "inhoud": ...}].
        # Het model krijgt alleen de NAMEN te zien en kan ze dan in het
        # startscript gebruiken in plaats van ernaast iets te verzinnen.
        env_bestanden=(list(body["env_bestanden"]) if body.get("env_bestanden") else None),
        lab_extras=(list(body["lab_extras"]) if body.get("lab_extras") else None))
    return svc.plan_dict(rij)


@router.post("/plannen/{plan_id}/opnieuw")
async def opnieuw(plan_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    """Terugkoppeling geven en opnieuw verkennen.

    Levert een NIEUW plan op naast het oude. Alle vier de soorten
    terugkoppeling zijn optioneel, maar er moet er minstens een zijn --
    opnieuw verkennen zonder iets te zeggen levert hetzelfde plan op en kost
    wel een modelaanroep.
    """
    def _kaart(sleutel: str) -> Dict[str, str]:
        waarde = body.get(sleutel)
        return {str(k): str(v) for k, v in waarde.items()} if isinstance(waarde, dict) else {}

    keuze = body.get("keuze")
    return _svc(db).plan_dict(await _svc(db).opnieuw(
        plan_id,
        antwoorden=_kaart("antwoorden"),
        opmerkingen=_kaart("opmerkingen"),
        opmerking=(str(body["opmerking"]) if body.get("opmerking") else None),
        instructie=(str(body["instructie"]) if body.get("instructie") else None),
        # Ook hier: ben je ze bij de eerste ronde vergeten, dan kun je ze nu
        # alsnog meegeven. Laat je het leeg, dan pakt `verken` wat er bij het
        # cluster staat -- dat maakt opnieuw verkennen na een import zinvol.
        env_bestanden=(list(body["env_bestanden"]) if body.get("env_bestanden") else None),
        lab_extras=(list(body["lab_extras"]) if body.get("lab_extras") else None),
        keuze=(list(keuze) if isinstance(keuze, list) else None)))


@router.get("/plannen/{plan_id}")
def lees_plan(plan_id: int, db: Session = Depends(get_db)):
    svc = _svc(db)
    return svc.plan_dict(svc.plan_of_404(plan_id))


@router.get("/{cluster_id}/plannen")
def plannen_van_cluster(cluster_id: int, db: Session = Depends(get_db)):
    svc = _svc(db)
    svc.get_or_404(cluster_id)
    return [svc.plan_dict(p) for p in svc.plannen(cluster_id)]


@router.post("/plannen/{plan_id}/goedkeuren")
def goedkeuren(plan_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    """Het plan omzetten in een cluster.

    `gekozen` weglaten betekent: neem over wat gestaafd is. Een vondst die de
    detectoren niet konden terugvinden gaat dan NIET mee -- die moet je
    expliciet aanvinken.
    """
    gekozen = body.get("gekozen")
    return _svc(db).keur_goed(
        plan_id,
        naam=(str(body["naam"]) if body.get("naam") else None),
        host_id=(int(body["host_id"]) if body.get("host_id") else None),
        gekozen=(list(gekozen) if isinstance(gekozen, list) else None),
        token_geheim=(str(body["token_geheim"]) if body.get("token_geheim") else None),
        env_profiel=(str(body["env_profiel"]) if body.get("env_profiel") else None))
