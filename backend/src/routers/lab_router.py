"""routers/lab_router.py — Lab API. Ported from
ND3X-public/src/routers/playground_router.py, single-tenant (no
require_project/org scoping — require_user is enough)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from services.lab.lab_service import LabService

router = APIRouter(prefix="/labs", tags=["labs"], dependencies=[Depends(require_user)])

# Separate router for the interactive terminal: a browser WebSocket can't set
# an Authorization header, so auth runs via a ?token= query param the endpoint
# validates itself.
ws_router = APIRouter(prefix="/labs", tags=["labs"])


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
        azure_profile_id=payload.get("azure_profile_id") if "azure_profile_id" in payload else "__unset__",
    )


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
