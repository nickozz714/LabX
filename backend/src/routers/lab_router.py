"""routers/lab_router.py — Lab API. Ported from
ND3X-public/src/routers/playground_router.py, single-tenant (no
require_project/org scoping — require_user is enough)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from component_logging import get_logger
from services.lab.lab_service import LabService

log = get_logger(__name__)

router = APIRouter(prefix="/labs", tags=["labs"], dependencies=[Depends(require_user)])

# Separate router for the interactive terminal: a browser WebSocket can't set
# an Authorization header, so auth runs via a ?token= query param the endpoint
# validates itself.
ws_router = APIRouter(prefix="/labs", tags=["labs"])

# En eentje voor de zichtbare browser van een lab (het pakket "Zelf inloggen in
# de browser van het lab"). Dat is noVNC in een <iframe>, en een iframe stuurt
# geen Authorization-header mee — net zomin als een WebSocket. Daarom: één keer
# een ?token= dat hier wordt gecontroleerd en als pad-gebonden cookie wordt
# teruggegeven, waarna de vervolgverzoeken van de noVNC-pagina (scripts, en de
# WebSocket zelf) die cookie meesturen.
browser_router = APIRouter(prefix="/labs", tags=["labs"])

BROWSER_PORT = 6080
_BROWSER_COOKIE = "labx_browser"


def _service(db: Session) -> LabService:
    return LabService(db)


@router.get("")
def list_labs(db: Session = Depends(get_db)):
    return _service(db).list_all()


@router.get("/images")
async def list_images(db: Session = Depends(get_db)):
    from services.lab.lab_service import IMAGE_PRESETS, default_image
    svc = _service(db)
    try:
        local = await svc.runtime.local_images()
    except Exception:  # noqa: BLE001
        local = []
    return {"presets": IMAGE_PRESETS, "local_images": local, "default_image": default_image()}


@router.get("/images/search")
async def search_registry_images(q: str = Query(..., min_length=2)):
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://hub.docker.com/v2/search/repositories/",
                params={"query": q.strip(), "page_size": 12},
            )
            r.raise_for_status()
            data = r.json()
        results = [{
            "name": item.get("repo_name"),
            "description": (item.get("short_description") or "")[:300],
            "stars": int(item.get("star_count") or 0),
            "official": bool(item.get("is_official")),
            "pulls": item.get("pull_count"),
        } for item in (data.get("results") or []) if item.get("repo_name")]
        return {"ok": True, "results": results}
    except Exception as exc:  # noqa: BLE001 — registry unreachable is not an error
        return {"ok": False, "results": [], "error": f"Docker Hub niet bereikbaar: {str(exc)[:200]}"}


# ── lab-extra's (de catalogus van wat je in een lab kunt zetten) ────────────
# Deze routes staan BEWUST vóór /{lab_id}: FastAPI matcht op volgorde, en
# /labs/{lab_id} zou "extras" anders als lab-id opslokken.

def _extra_to_dict(e) -> Dict[str, Any]:
    return {
        "id": e.id, "key": e.key, "label": e.label, "description": e.description,
        "check_cmd": e.check_cmd, "install_script": e.install_script,
        "requires": list(e.requires or []), "timeout_s": e.timeout_s,
        "mcp_server": e.mcp_server,
        "default_on": bool(e.default_on), "is_enabled": bool(e.is_enabled),
        "builtin": bool(e.builtin), "sort_order": e.sort_order,
        "updated_at": e.updated_at,
    }


@router.get("/extras")
def list_lab_extras(db: Session = Depends(get_db)):
    from models.lab_extra import LabExtra
    rows = db.query(LabExtra).order_by(LabExtra.sort_order, LabExtra.id).all()
    return [_extra_to_dict(e) for e in rows]


@router.post("/extras")
def create_lab_extra(payload: Dict[str, Any], db: Session = Depends(get_db)):
    import re
    from datetime import datetime, timezone
    from models.lab_extra import LabExtra
    key = str(payload.get("key") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", key):
        raise HTTPException(status_code=400,
                            detail="Sleutel: kleine letters, cijfers, . _ - (2-64 tekens)")
    if db.query(LabExtra).filter(LabExtra.key == key).first():
        raise HTTPException(status_code=409, detail=f"Er bestaat al een pakket '{key}'")
    if not str(payload.get("install_script") or "").strip():
        raise HTTPException(status_code=400, detail="Een installatiescript is verplicht")
    now = datetime.now(timezone.utc).isoformat()
    row = LabExtra(
        key=key, label=str(payload.get("label") or key)[:255],
        description=payload.get("description"),
        check_cmd=(payload.get("check_cmd") or None),
        install_script=str(payload.get("install_script")),
        requires=[str(x) for x in (payload.get("requires") or [])],
        # Een eigen pakket mag ook een lab-MCP-server meebrengen; zelfde vorm
        # als bij de meegeleverde (zie models/lab_extra.py).
        mcp_server=payload.get("mcp_server") or None,
        timeout_s=max(30, min(int(payload.get("timeout_s") or 900), 7200)),
        default_on=bool(payload.get("default_on", False)),
        is_enabled=bool(payload.get("is_enabled", True)),
        builtin=False, sort_order=int(payload.get("sort_order") or 100),
        created_at=now, updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _extra_to_dict(row)


@router.patch("/extras/{extra_id}")
def update_lab_extra(extra_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)):
    from datetime import datetime, timezone
    from models.lab_extra import LabExtra
    row = db.get(LabExtra, extra_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Pakket niet gevonden")
    for field in ("label", "description", "check_cmd", "install_script"):
        if field in payload:
            setattr(row, field, payload[field] or None)
    if not (row.install_script or "").strip():
        raise HTTPException(status_code=400, detail="Een installatiescript is verplicht")
    if "requires" in payload:
        row.requires = [str(x) for x in (payload.get("requires") or [])]
    if "mcp_server" in payload:
        row.mcp_server = payload.get("mcp_server") or None
    if "timeout_s" in payload:
        row.timeout_s = max(30, min(int(payload.get("timeout_s") or 900), 7200))
    for flag in ("default_on", "is_enabled"):
        if flag in payload:
            setattr(row, flag, bool(payload[flag]))
    if "sort_order" in payload:
        row.sort_order = int(payload.get("sort_order") or 100)
    row.updated_at = datetime.now(timezone.utc).isoformat()
    db.commit()
    db.refresh(row)
    return _extra_to_dict(row)


@router.post("/extras/{extra_id}/reset")
def reset_lab_extra(extra_id: int, db: Session = Depends(get_db)):
    """Een meegeleverd pakket terugzetten naar het origineel — de uitweg als een
    eigen aanpassing het script gesloopt heeft."""
    from datetime import datetime, timezone
    from models.lab_extra import LabExtra
    from services.lab.extras_catalog import builtin_for
    row = db.get(LabExtra, extra_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Pakket niet gevonden")
    spec = builtin_for(row.key)
    if spec is None:
        raise HTTPException(status_code=400,
                            detail="Dit is een eigen pakket — er is geen origineel om naar terug te gaan")
    row.label = spec["label"]
    row.description = spec.get("description")
    row.check_cmd = spec.get("check_cmd")
    row.install_script = spec["install_script"]
    row.requires = list(spec.get("requires") or [])
    row.timeout_s = int(spec.get("timeout_s") or 900)
    row.sort_order = int(spec.get("sort_order") or 100)
    row.mcp_server = spec.get("mcp_server")
    # Weer gelijk aan het origineel, dus ook weer meegaan met toekomstige
    # verbeteringen daarvan (zie seed_builtin_extras).
    from services.lab.extras_catalog import _spec_fingerprint
    row.builtin_hash = _spec_fingerprint(spec)
    row.updated_at = datetime.now(timezone.utc).isoformat()
    db.commit()
    db.refresh(row)
    return _extra_to_dict(row)


@router.delete("/extras/{extra_id}")
def delete_lab_extra(extra_id: int, db: Session = Depends(get_db)):
    from models.lab_extra import LabExtra
    row = db.get(LabExtra, extra_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Pakket niet gevonden")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/guard-model/status")
async def guard_model_status():
    from services.lab.data_guard_llm import guard_model_status as _st
    return await _st()


@router.post("/guard-model/ensure")
async def guard_model_ensure():
    from services.lab.data_guard_llm import ensure_guard_model, guard_model_status as _st
    await ensure_guard_model()
    return await _st()


@router.get("/{lab_id}/guard-audit")
async def lab_guard_audit(
    lab_id: str,
    limit: int = Query(default=200, ge=1, le=2000),
    format: str = Query(default="json"),
    db: Session = Depends(get_db),
):
    await _service(db).detail(lab_id)
    from models.audit import AuditTraceEvent
    rows = (
        db.query(AuditTraceEvent)
        .filter(AuditTraceEvent.type == "lab_guard_exec")
        .filter(AuditTraceEvent.data_json.like(f"%{lab_id}%"))
        .order_by(AuditTraceEvent.ts.desc())
        .limit(limit)
        .all()
    )
    items = [r.to_dict() for r in rows]
    if format == "csv":
        import csv
        import io
        from fastapi import Response
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["ts", "level", "blocked", "gov_class", "provenance", "tainted",
                    "exit_code", "command", "guard_reason", "data_sink",
                    "model_output_bytes", "raw_output_bytes"])
        for it in items:
            d = it.get("data") or {}
            f = d.get("guard_facts") or {}
            w.writerow([it.get("ts"), it.get("level"), d.get("blocked"),
                        f.get("gov_class"), f.get("provenance"), f.get("tainted"),
                        d.get("exit_code"), d.get("command"), d.get("guard_reason"),
                        d.get("data_sink"), d.get("model_output_bytes"),
                        d.get("raw_output_bytes")])
        return Response(content=buf.getvalue(), media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="lab-guard-audit-{lab_id}.csv"'})
    return {"lab_id": lab_id, "total": len(items), "items": items}


@router.post("")
async def create_lab(payload: Dict[str, Any], db: Session = Depends(get_db)):
    return await _service(db).create(
        name=str(payload.get("name") or ""),
        image=payload.get("image"),
        repos=payload.get("repos"),
        cpu_limit=float(payload.get("cpu_limit") or 1.0),
        mem_limit_mb=int(payload.get("mem_limit_mb") or 2048),
        allow_network=bool(payload.get("allow_network", True)),
        ttl_hours=int(payload.get("ttl_hours") or 24),
        ports=payload.get("ports"),
        data_guard=bool(payload.get("data_guard", True)),
        llm_guard=bool(payload.get("llm_guard", True)),
        allowed_mcp=payload.get("allowed_mcp"),
        allowed_tools=payload.get("allowed_tools"),
        allowed_skills=payload.get("allowed_skills"),
        environment=(payload.get("environment") or "").strip() or None,
        extras=payload.get("extras"),
        setup_script=payload.get("setup_script"),
    )


@router.get("/{lab_id}")
async def get_lab(lab_id: str, db: Session = Depends(get_db)):
    return await _service(db).detail(lab_id)


@router.patch("/{lab_id}")
async def update_lab(lab_id: str, payload: Dict[str, Any], db: Session = Depends(get_db)):
    return await _service(db).update_settings(
        lab_id,
        data_guard=payload.get("data_guard") if "data_guard" in payload else None,
        llm_guard=payload.get("llm_guard") if "llm_guard" in payload else None,
        allowed_mcp=payload.get("allowed_mcp") if "allowed_mcp" in payload else None,
        allowed_tools=payload.get("allowed_tools") if "allowed_tools" in payload else None,
        allowed_skills=payload.get("allowed_skills") if "allowed_skills" in payload else None,
        extras=payload.get("extras") if "extras" in payload else None,
        setup_script=payload.get("setup_script") if "setup_script" in payload else "__unset__",
        azure_profile_id=payload.get("azure_profile_id") if "azure_profile_id" in payload else "__unset__",
    )


@router.post("/{lab_id}/provision")
async def provision_lab(lab_id: str, payload: Optional[Dict[str, Any]] = None,
                        db: Session = Depends(get_db)):
    """Opnieuw inrichten. Antwoordt meteen — het werk loopt op de achtergrond,
    de voortgang staat in provision_status/provision_log op het lab zelf.
    force=true slaat de "staat er al"-controles over, voor een tweede poging na
    een halve installatie."""
    from services.lab.lab_service import provision_in_background
    svc = _service(db)
    lab = svc.get(lab_id)
    if lab.status != "running":
        raise HTTPException(status_code=409, detail="Lab draait niet (start hem eerst)")
    if not lab.allow_network:
        raise HTTPException(status_code=409,
                            detail="Dit lab heeft geen netwerk — er valt niets binnen te halen")
    if not provision_in_background(lab_id, force=bool((payload or {}).get("force"))):
        raise HTTPException(status_code=409, detail="Er loopt al werk voor dit lab — wacht tot dat klaar is")
    lab.provision_status = "pending"
    db.commit()
    return {"ok": True, "provision_status": "pending"}


@router.post("/{lab_id}/rebuild")
async def rebuild_lab(lab_id: str, payload: Optional[Dict[str, Any]] = None,
                      db: Session = Depends(get_db)):
    """Het lab opnieuw opbouwen op (een nieuw) image — de manier om het image
    van een bestaand lab te wijzigen of bij te werken. /workspace blijft; de
    rest van de container wordt opnieuw gemaakt en daarna opnieuw ingericht.
    Antwoordt meteen: het ophalen van een image kan gigabytes zijn, dus het werk
    loopt op de achtergrond (volg `status` en `provision_status`)."""
    from services.lab.lab_service import rebuild_in_background
    svc = _service(db)
    lab = svc.get(lab_id)
    image = str((payload or {}).get("image") or "").strip() or None
    # `rebuild` zet de status zelf op "creating" zodra de taak begint; hier
    # alvast iets beweren dat misschien nooit gebeurt zou het lab voorgoed op
    # "creating" laten staan.
    if not rebuild_in_background(lab_id, image=image,
                                 pull=bool((payload or {}).get("pull", True))):
        raise HTTPException(status_code=409, detail="Er loopt al werk voor dit lab — wacht tot dat klaar is")
    return {"ok": True, "status": "creating", "image": image or lab.image}


@router.post("/{lab_id}/start")
async def start_lab(lab_id: str, db: Session = Depends(get_db)):
    return await _service(db).start(lab_id)


@router.post("/{lab_id}/stop")
async def stop_lab(lab_id: str, db: Session = Depends(get_db)):
    return await _service(db).stop(lab_id)


@router.delete("/{lab_id}")
async def delete_lab(lab_id: str, db: Session = Depends(get_db)):
    return await _service(db).delete(lab_id)


@router.post("/{lab_id}/exec")
async def exec_in_lab(lab_id: str, payload: Dict[str, Any], db: Session = Depends(get_db)):
    return await _service(db).exec_command(
        lab_id, str(payload.get("command") or ""), timeout=float(payload.get("timeout") or 120))


@router.get("/{lab_id}/files")
async def list_lab_files(lab_id: str, path: str = Query(default="/workspace"), db: Session = Depends(get_db)):
    return await _service(db).list_files(lab_id, path)


@router.get("/{lab_id}/file")
async def read_lab_file(lab_id: str, path: str = Query(...), db: Session = Depends(get_db)):
    return await _service(db).read_file(lab_id, path)


@router.put("/{lab_id}/file")
async def write_lab_file(lab_id: str, payload: Dict[str, Any], db: Session = Depends(get_db)):
    path: Optional[str] = payload.get("path")
    if not path:
        raise HTTPException(status_code=400, detail="path is verplicht")
    return await _service(db).write_file(lab_id, path, str(payload.get("content") or ""))


@router.post("/{lab_id}/publish")
async def publish_lab_repo(lab_id: str, payload: Dict[str, Any], db: Session = Depends(get_db)):
    return await _service(db).publish(
        lab_id,
        repo_name=str(payload.get("repo") or payload.get("repo_name") or ""),
        branch=payload.get("branch"),
        message=payload.get("message"),
        token=(payload.get("token") or "").strip() or None,
        remote_url=(payload.get("remote_url") or "").strip() or None,
    )


@router.post("/{lab_id}/az-login")
async def az_login_lab(lab_id: str, payload: Dict[str, Any], db: Session = Depends(get_db)):
    files = payload.get("files") if isinstance(payload.get("files"), dict) else None
    return await _service(db).az_login(lab_id, az_dir=(payload.get("az_dir") or "/root/.azure"), files=files)




# ── de zichtbare browser van een lab (noVNC) ────────────────────────────────

def _browser_auth(token: str, cookie: Optional[str]) -> None:
    from authentication import decode_access_token
    raw = (token or cookie or "").strip()
    if not raw:
        raise HTTPException(status_code=401, detail="Geen token")
    try:
        decode_access_token(raw)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="Ongeldig token")


def _browser_target(db: Session, lab_id: str) -> str:
    """De basis-URL van de noVNC-server IN het lab. Via de container-DNS-naam op
    het gedeelde bridge-netwerk — de labpoort wordt bewust niet op de host
    gepubliceerd, zodat de enige weg naar die browser via LabX loopt (en dus
    achter een login)."""
    from models.lab import Lab
    p = db.get(Lab, lab_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Lab niet gevonden")
    if p.status != "running" or not p.network_alias:
        raise HTTPException(status_code=409, detail="Lab draait niet — start hem eerst")
    if "browser-vnc" not in [str(x) for x in (p.extras or [])]:
        raise HTTPException(
            status_code=409,
            detail="Dit lab heeft het pakket 'Zelf inloggen in de browser van het lab' niet aan staan.")
    return f"http://{p.network_alias}:{BROWSER_PORT}"


@browser_router.get("/{lab_id}/browser")
async def browser_entry(lab_id: str, token: str = Query(default=""),
                        db: Session = Depends(get_db)):
    """Instap: token controleren, als pad-gebonden cookie zetten en doorsturen
    naar de noVNC-pagina die meteen verbinding maakt."""
    from fastapi.responses import RedirectResponse
    _browser_auth(token, None)
    _browser_target(db, lab_id)
    base = f"/api/labs/{lab_id}/browser"
    target = (f"{base}/vnc.html?path={base.lstrip('/')}/websockify"
              "&autoconnect=true&resize=scale&reconnect=true")
    resp = RedirectResponse(url=target, status_code=307)
    resp.set_cookie(_BROWSER_COOKIE, token, httponly=True, samesite="lax",
                    path=base, max_age=8 * 3600)
    return resp


@browser_router.get("/{lab_id}/browser/{path:path}")
async def browser_asset(lab_id: str, path: str, request: Request,
                        db: Session = Depends(get_db)):
    """De noVNC-bestanden uit het lab doorgeven (html/js/css)."""
    import httpx
    from fastapi.responses import Response as FastResponse
    _browser_auth(request.query_params.get("token", ""),
                  request.cookies.get(_BROWSER_COOKIE))
    base = _browser_target(db, lab_id)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{base}/{path}", params=dict(request.query_params))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Browser in het lab niet bereikbaar: {str(exc)[:200]}")
    return FastResponse(content=r.content, status_code=r.status_code,
                        media_type=r.headers.get("content-type", "application/octet-stream"))


@browser_router.websocket("/{lab_id}/browser/websockify")
async def browser_socket(websocket: WebSocket, lab_id: str, token: str = Query(default="")):
    """De VNC-stroom zelf, doorgegeven tussen de noVNC-pagina en het lab."""
    import asyncio
    import websockets

    from authentication import decode_access_token
    from db.database import SessionLocal
    raw = (token or websocket.cookies.get(_BROWSER_COOKIE) or "").strip()
    try:
        decode_access_token(raw)
    except Exception:  # noqa: BLE001
        await websocket.close(code=4401)
        return
    db = SessionLocal()
    try:
        target = _browser_target(db, lab_id).replace("http://", "ws://") + "/websockify"
    except HTTPException:
        await websocket.close(code=4409)
        return
    finally:
        db.close()

    await websocket.accept(subprotocol="binary")
    try:
        async with websockets.connect(target, subprotocols=["binary"],
                                      max_size=None, open_timeout=15) as upstream:
            async def naar_lab() -> None:
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    data = msg.get("bytes")
                    if data is None and msg.get("text") is not None:
                        data = msg["text"].encode()
                    if data is not None:
                        await upstream.send(data)

            async def naar_browser() -> None:
                async for data in upstream:
                    if isinstance(data, str):
                        data = data.encode()
                    await websocket.send_bytes(data)

            done, pending = await asyncio.wait(
                [asyncio.create_task(naar_lab()), asyncio.create_task(naar_browser())],
                return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warningx("Browser-verbinding met het lab verbroken", lab_id=lab_id,
                     error=str(exc)[:200])
    finally:
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass


@ws_router.websocket("/{lab_id}/terminal")
async def lab_terminal(websocket: WebSocket, lab_id: str, token: str = Query(default="")):
    """Interactive terminal: a real pty onto `docker exec -it` in the lab
    container, pumped over the WebSocket. Works identically from a
    sibling-container backend (the validation confirmed `-it` is a client-side
    tty check, unaffected by where the docker CLI process itself runs)."""
    import asyncio
    import fcntl
    import json
    import os
    import pty
    import struct
    import subprocess
    import termios

    await websocket.accept()

    from authentication import decode_access_token
    try:
        decode_access_token(token)
    except Exception:  # noqa: BLE001
        await websocket.send_text("\r\n[Authenticatie mislukt]\r\n")
        await websocket.close(code=4401)
        return

    from db.database import SessionLocal
    from models.lab import Lab
    db = SessionLocal()
    try:
        p = db.get(Lab, lab_id)
        if p is None:
            await websocket.send_text("\r\n[Lab niet gevonden]\r\n")
            await websocket.close(code=4404)
            return
        if p.status != "running" or not p.container_id:
            await websocket.send_text("\r\n[Lab draait niet — start hem eerst]\r\n")
            await websocket.close(code=4409)
            return
        cid = p.container_id
    finally:
        db.close()

    master, slave = pty.openpty()
    proc = subprocess.Popen(
        ["docker", "exec", "-it", "-e", "TERM=xterm-256color", "-w", "/workspace", cid,
         "sh", "-lc", "exec ${SHELL:-sh}"],
        stdin=slave, stdout=slave, stderr=slave,
        close_fds=True, start_new_session=True,
    )
    os.close(slave)
    loop = asyncio.get_running_loop()

    async def _pump_output() -> None:
        try:
            while True:
                data = await loop.run_in_executor(None, os.read, master, 4096)
                if not data:
                    break
                await websocket.send_text(data.decode("utf-8", "replace"))
        except OSError:
            pass
        except Exception:  # noqa: BLE001
            pass
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass

    reader = asyncio.create_task(_pump_output())
    try:
        while True:
            msg = await websocket.receive_text()
            if msg.startswith('{"resize"'):
                try:
                    cols, rows = (json.loads(msg).get("resize") or [80, 24])[:2]
                    fcntl.ioctl(master, termios.TIOCSWINSZ,
                                struct.pack("HHHH", int(rows), int(cols), 0, 0))
                    continue
                except Exception:  # noqa: BLE001
                    pass
            os.write(master, msg.encode("utf-8", "replace"))
    except WebSocketDisconnect:
        pass
    except OSError:
        pass
    finally:
        reader.cancel()
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        try:
            os.close(master)
        except OSError:
            pass
