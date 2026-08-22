"""routers/skill_router.py — Skill CRUD + the Skill Wizard's tool linking with
per-tool instructions (issue 3: "Per Tool moet ik ook instructies kunnen
opgeven die agent moet hanteren... schema of input laten zien"). Each linked
tool's `argument` (its MCP input schema) rides along in the response so the
front-end can render it next to the instruction field without a second call
per tool."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from models.mcp_server import MCPServer
from models.skill import Skill
from models.skill_tool import SkillTool
from models.tool import Tool

router = APIRouter(prefix="/skills", tags=["skills"], dependencies=[Depends(require_user)])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _skill_dict(s: Skill) -> Dict[str, Any]:
    return {
        "id": s.id, "name": s.name, "display_name": s.display_name,
        "description": s.description, "instructions": s.instructions,
        "input_schema": s.input_schema, "output_schema": s.output_schema,
        "is_system": s.is_system, "is_enabled": s.is_enabled, "priority": s.priority,
        "created_at": s.created_at, "updated_at": s.updated_at,
    }


def _linked_tools(db: Session, skill_id: int) -> List[Dict[str, Any]]:
    rows = (db.query(SkillTool, Tool).join(Tool, Tool.id == SkillTool.tool_id)
            .filter(SkillTool.skill_id == skill_id).all())
    out = []
    for link, tool in rows:
        server = db.get(MCPServer, tool.mcp_server_id) if tool.mcp_server_id else None
        out.append({
            "link_id": link.id, "tool_id": tool.id, "tool_name": tool.name,
            "tool_description": tool.description,
            "argument": tool.argument or {"type": "object", "properties": {}},
            "mcp_server": {"id": server.id, "name": server.name, "location": server.location} if server else None,
            "is_enabled": link.is_enabled, "instructions": link.instructions,
        })
    return out


@router.get("")
def list_skills(db: Session = Depends(get_db)):
    rows = db.query(Skill).order_by(Skill.priority.desc(), Skill.name.asc()).all()
    return [_skill_dict(s) for s in rows]


@router.post("")
def create_skill(payload: Dict[str, Any], db: Session = Depends(get_db)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is verplicht")
    if db.query(Skill).filter(Skill.name == name).first():
        raise HTTPException(status_code=409, detail=f"Skill '{name}' bestaat al")
    now = _now_iso()
    s = Skill(
        name=name, display_name=payload.get("display_name") or name,
        description=payload.get("description") or "",
        instructions=payload.get("instructions") or "",
        input_schema=payload.get("input_schema"), output_schema=payload.get("output_schema"),
        is_enabled=bool(payload.get("is_enabled", True)), priority=int(payload.get("priority") or 0),
        created_at=now, updated_at=now,
    )
    db.add(s)
    db.commit()
    # The wizard's tool-selection step: [{tool_id, instructions}]
    for entry in (payload.get("tools") or []):
        tool_id = entry.get("tool_id")
        if not tool_id or not db.get(Tool, int(tool_id)):
            continue
        db.add(SkillTool(skill_id=s.id, tool_id=int(tool_id),
                         is_enabled=bool(entry.get("is_enabled", True)),
                         instructions=(entry.get("instructions") or None)))
    db.commit()
    return {**_skill_dict(s), "tools": _linked_tools(db, s.id)}


@router.get("/{skill_id}")
def get_skill(skill_id: int, db: Session = Depends(get_db)):
    s = db.get(Skill, skill_id)
    if not s:
        raise HTTPException(status_code=404, detail="Skill niet gevonden")
    return {**_skill_dict(s), "tools": _linked_tools(db, s.id)}


@router.patch("/{skill_id}")
def update_skill(skill_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)):
    s = db.get(Skill, skill_id)
    if not s:
        raise HTTPException(status_code=404, detail="Skill niet gevonden")
    for field in ("display_name", "description", "instructions", "input_schema",
                  "output_schema", "is_enabled", "priority"):
        if field in payload:
            setattr(s, field, payload[field])
    s.updated_at = _now_iso()
    db.commit()
    return {**_skill_dict(s), "tools": _linked_tools(db, s.id)}


@router.delete("/{skill_id}")
def delete_skill(skill_id: int, db: Session = Depends(get_db)):
    s = db.get(Skill, skill_id)
    if not s:
        raise HTTPException(status_code=404, detail="Skill niet gevonden")
    db.query(SkillTool).filter(SkillTool.skill_id == skill_id).delete(synchronize_session=False)
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.put("/{skill_id}/tools")
def set_skill_tools(skill_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)):
    """Replace the skill's tool set in one call — [{tool_id, instructions,
    is_enabled}] — the shape the wizard's tool-picker step submits."""
    s = db.get(Skill, skill_id)
    if not s:
        raise HTTPException(status_code=404, detail="Skill niet gevonden")
    db.query(SkillTool).filter(SkillTool.skill_id == skill_id).delete(synchronize_session=False)
    for entry in (payload.get("tools") or []):
        tool_id = entry.get("tool_id")
        if not tool_id or not db.get(Tool, int(tool_id)):
            continue
        db.add(SkillTool(skill_id=skill_id, tool_id=int(tool_id),
                         is_enabled=bool(entry.get("is_enabled", True)),
                         instructions=(entry.get("instructions") or None)))
    s.updated_at = _now_iso()
    db.commit()
    return {**_skill_dict(s), "tools": _linked_tools(db, s.id)}
