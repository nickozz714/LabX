"""services/mcp/internal_auth.py — shared secret for the loopback call from
the stdio gateway subprocess back into the main API process. Ported in
spirit from ND3X's services.mcp.internal_auth (INTERNAL_MCP_TOKEN)."""
from __future__ import annotations

import secrets

from config import settings

# Generated once per process if the operator didn't pin one via env — the
# gateway subprocess receives it via env at spawn time (see gateway.py), so a
# per-process random value is fine (it never needs to survive a restart).
INTERNAL_MCP_TOKEN = settings.INTERNAL_TOKEN or secrets.token_urlsafe(32)
