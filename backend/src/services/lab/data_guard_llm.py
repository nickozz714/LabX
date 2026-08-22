"""
services/lab/data_guard_llm.py

Track B of the data-egress guard: a SMALL, LOCAL model as a second opinion on
top of the rules in data_guard.py. Ported near-verbatim from
ND3X-public/src/services/playground/data_guard_llm.py. Judges whether
container output contains confidential customer/business data (including
DERIVED AGGREGATES) — the gap plain text rules can't see, like "PostNL 11,
DHL 7".

Hard constraints:
- LOCAL: the model runs on a local/LAN Ollama; the text under review must
  never leave the machine. A non-local URL is refused (that would itself be
  a leak) — the check then falls away silently (fail-open onto the rules).
- OPTIONAL: only active with DATA_GUARD_LLM_ENABLED=1 and a model configured.
- FAST: only called when the rules already allow the output; on a sample;
  with a hard timeout. On timeout/error, fail-open by default (the rules
  remain the hard floor); fail-closed is configurable for high-sensitivity.
- NO HARDCODED MODEL: the model name comes from config (DATA_GUARD_LLM_MODEL).
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import shutil
import sys
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from component_logging import get_logger

log = get_logger(__name__)

DEFAULT_GUARD_MODEL = "qwen2.5:1.5b"


def _settings_row_values() -> tuple[Optional[str], Optional[str]]:
    """(guard_llm_url, guard_llm_model) as saved via the Settings page, or
    (None, None) if unset/unreadable. Opened as a short-lived session — this
    module has no request-scoped db, and these are low-frequency status
    checks, not a hot per-exec path.

    Without this, the Settings page's Ollama-URL/model fields silently did
    nothing: this module only ever read the env vars, so an operator setting
    a custom URL there saw it "saved" but the guard kept using the computed
    default — a real bug behind "blijft unreachable aangeven" when the fix
    was to point at a non-default URL (e.g. the compose `ollama` sidecar)."""
    try:
        from db.database import SessionLocal
        from models.setting import AppSettings
        with SessionLocal() as db:
            row = db.get(AppSettings, 1)
            if not row:
                return (None, None)
            return (row.guard_llm_url or None, row.guard_llm_model or None)
    except Exception:  # noqa: BLE001 — settings lookup must never break the guard
        return (None, None)


def _model() -> str:
    db_url, db_model = _settings_row_values()
    if db_model:
        return db_model.strip()
    return (os.getenv("DATA_GUARD_LLM_MODEL") or DEFAULT_GUARD_MODEL).strip()


def _in_docker() -> bool:
    """Does LabX itself run in a container? Then 'localhost' is the
    container, not the host Ollama runs on."""
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "rt") as f:
            return any(x in f.read() for x in ("docker", "kubepods", "containerd"))
    except Exception:  # noqa: BLE001
        return False


def _url() -> str:
    """Ollama endpoint. Priority: the Settings-page value, then
    DATA_GUARD_LLM_URL, then a computed default (localhost on a plain host,
    host.docker.internal when LabX runs in a container)."""
    db_url, _db_model = _settings_row_values()
    if db_url:
        return db_url.strip().rstrip("/")
    env = (os.getenv("DATA_GUARD_LLM_URL") or "").strip()
    if env:
        return env.rstrip("/")
    host = "host.docker.internal" if _in_docker() else "localhost"
    return f"http://{host}:11434"


def _globally_disabled() -> bool:
    return (os.getenv("DATA_GUARD_LLM_DISABLED") or "").strip().lower() in ("1", "true", "yes", "on")


_SYSTEM = (
    "Je bent een lokaal data-egress-filter. Beoordeel of de container-output "
    "VERTROUWELIJKE KLANT- of BEDRIJFSDATA bevat die het systeem niet mag "
    "verlaten. Citeer de inhoud NIET. Antwoord UITSLUITEND als JSON: "
    '{\"confidential\": true|false, \"category\": \"...\", \"reason\": \"korte reden zonder de data te herhalen\"}.\n'
    "WEL vertrouwelijk (confidential=true): klant-records, persoonsgegevens, en "
    "AFGELEIDE AGGREGATEN van klantdata — aantallen/verdelingen/statistieken over "
    "echte klanten, orders, zendingen, omzet, vervoerders, steden, gewichten.\n"
    "NIET vertrouwelijk (confidential=false): infrastructuur-inventaris en "
    "technische metadata — namen van workspaces/servers/containers/tabellen/items, "
    "item-TYPES, schema's, KOLOMNAMEN en -types, versienummers, configuratiesleutels "
    "zonder waarden, TELLINGEN/AANTALLEN van objecten, BESTANDSNAMEN/PADEN, "
    "bestandsGROOTTES (bytes), GUID's/ID's, en DIFF-/vergelijkings-TELLINGEN "
    "(bv. 'run 2 vs 3: 0 verschillen'). Technische artefacten zijn GEEN klantdata.\n"
    "Alleen echte klant-/business-INHOUD of daarvan afgeleide statistieken zijn "
    "vertrouwelijk. Bij twijfel tussen 'technische metadata' en 'geen data': kies false.\n"
    "OOK NIET vertrouwelijk: access-tokens/JWT's die de agent zelf zojuist heeft "
    "opgevraagd voor zijn werk (az/Fabric/OAuth), en korte technische statusregels "
    "(bv. 'AZ_YES', 'check1', 'token_set=yes', exitcodes, ja/nee-checks).\n\n"
    "Voorbeelden:\n"
    'Output: "Vervoerders: PostNL 11, DHL 7, DPD 5. Gem. gewicht 19 kg." '
    '-> {\"confidential\": true, \"category\": \"customer-aggregate\", \"reason\": \"aggregaat van zendingsdata\"}\n'
    'Output: "2.847 actieve klanten, gem. orderwaarde EUR 63,20" '
    '-> {\"confidential\": true, \"category\": \"customer-aggregate\", \"reason\": \"geaggregeerde klantstatistiek\"}\n'
    'Output: "workspace_1 ... workspace_857 (West Europe)" '
    '-> {\"confidential\": false, \"category\": \"infra-inventory\", \"reason\": \"lijst infra-objectnamen\"}\n'
    'Output: "Table orders — columns: id, customer_id, carrier, created_at" '
    '-> {\"confidential\": false, \"category\": \"schema-metadata\", \"reason\": \"alleen schema/kolomnamen\"}\n'
    'Output: "part-00000-abc.snappy.parquet (10769 bytes), part-00001-def.parquet" '
    '-> {\"confidential\": false, \"category\": \"file-listing\", \"reason\": \"bestandsnamen/groottes, geen klantdata\"}\n'
    'Output: "Items: LH_TEST (Lakehouse), PL_BRONZE (DataPipeline)" '
    '-> {\"confidential\": false, \"category\": \"infra-inventory\", \"reason\": \"item-namen en -types\"}\n'
    'Output: "Vergelijking run2 vs run3: 0 verschillen, 7 bestanden identiek" '
    '-> {\"confidential\": false, \"category\": \"diff-count\", \"reason\": \"telling van verschillen, geen inhoud\"}\n'
    'Output: "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIs..." (door de agent opgevraagd token) '
    '-> {\"confidential\": false, \"category\": \"auth-token\", \"reason\": \"zelf opgevraagd werktoken, geen klantdata\"}\n'
    'Output: "AZ_YES" '
    '-> {\"confidential\": false, \"category\": \"system-utility\", \"reason\": \"korte technische statuscheck\"}'
)

_CONFIDENTIAL_CATEGORIES = ("klant", "customer", "aggreg", "record", "persoon", "pii", "business")
# Categories that are benign BY THE RUBRIC'S OWN DEFINITION. The tiny guard
# model's `confidential` boolean is miscalibrated (observed in the guard
# audit: it returned confidential=true for outputs it ITSELF categorized as
# infra-inventory/system-utility, and even labeled `echo azazaz` output as
# customer-aggregate) — so when the category clearly names a benign class,
# the category wins over the boolean.
_BENIGN_CATEGORIES = ("infra", "inventory", "schema", "metadata", "file", "diff",
                      "system", "utility", "config", "version", "token", "auth",
                      "status", "geen", "none")


def _fail_closed() -> bool:
    return (os.getenv("DATA_GUARD_LLM_FAIL_CLOSED") or "").strip().lower() in ("1", "true", "yes", "on")


def _is_local(url: str) -> bool:
    """Only localhost/LAN allowed — the text must never leave the machine.

    Beyond the literal localhost/host.docker.internal names and raw IPs, also
    resolve any other hostname via DNS and accept it if it lands on a
    loopback/private address. Without this, a docker-compose service name
    (e.g. `http://ollama:11434` for the optional `ollama` sidecar) fails this
    check — it's neither "localhost" nor a parseable IP literal — and the
    guard silently reports "non_local" even though the target never leaves
    the docker network."""
    try:
        host = (urlparse(url).hostname or "").strip()
    except Exception:  # noqa: BLE001
        return False
    if host in ("localhost", "host.docker.internal", ""):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private
    except ValueError:
        pass
    try:
        import socket
        resolved = socket.gethostbyname(host)
        ip = ipaddress.ip_address(resolved)
        return ip.is_loopback or ip.is_private
    except Exception:  # noqa: BLE001 — unresolvable/odd hostname -> not trusted as local
        return False


def _sample(text: str, head: int = 8000, tail: int = 2000) -> str:
    if len(text) <= head + tail:
        return text
    return text[:head] + "\n…\n" + text[-tail:]


async def llm_second_opinion(text: str, *, db: Any = None) -> Optional[Dict[str, Any]]:
    """Return {allowed, reason} from the local model, or None when the check
    didn't run (off, no model, non-local URL, or fail-open on timeout)."""
    if _globally_disabled() or not (text or "").strip():
        return None
    # Short technical outputs (an echo, an exit code, a yes/no check) carry
    # no meaningful aggregate to leak, but the tiny model over-blocks exactly
    # these (see guard audit: `echo check1` → "customer-aggregate"). The
    # rules layer (track A) has already run and found nothing at this point.
    min_chars = int(os.getenv("DATA_GUARD_LLM_MIN_CHARS") or 200)
    if len(text.strip()) < min_chars:
        return None
    model = _model()
    url = _url()
    if not _is_local(url):
        log.warningx("data-guard LLM overgeslagen: niet-lokale URL (lek-risico)", url=url)
        return None
    timeout = float(os.getenv("DATA_GUARD_LLM_TIMEOUT") or 2.5)

    payload = {
        "model": model,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": "Container-output:\n" + _sample(text)},
        ],
    }
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{url}/api/chat", json=payload)
            resp.raise_for_status()
            content = (resp.json().get("message") or {}).get("content") or "{}"
            data = json.loads(content)
        confidential = bool(data.get("confidential"))
        category = (data.get("category") or "").lower()
        if any(k in category for k in _CONFIDENTIAL_CATEGORIES):
            confidential = True
        elif any(k in category for k in _BENIGN_CATEGORIES):
            # Rubric-benign category beats the miscalibrated boolean.
            confidential = False
        reason = f"lokaal model: {data.get('category') or 'vertrouwelijke data'}"
        return {"allowed": not confidential, "reason": reason}
    except Exception as exc:  # noqa: BLE001 — availability must never break the lab
        if _fail_closed():
            log.warningx("data-guard LLM fout → fail-closed (blokkeer)", error=str(exc)[:200])
            return {"allowed": False, "reason": "data-guard-model niet bereikbaar (fail-closed)"}
        log.warningx("data-guard LLM fout → fail-open (regels blijven de vloer)", error=str(exc)[:200])
        return None


# ── Best-effort provisioning of Ollama + the guard model ─────────────────────
async def _reachable(url: str, timeout: float = 2.0) -> Optional[list]:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{url}/api/tags")
            r.raise_for_status()
            return [m.get("name", "") for m in (r.json().get("models") or [])]
    except Exception:  # noqa: BLE001
        return None


async def _spawn(cmd: str) -> None:
    await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True)


async def guard_model_status() -> Dict[str, Any]:
    """Pure status check (no side effects) for the GUI. state in
    ready | pulling | unreachable | non_local | disabled."""
    url, model = _url(), _model()
    if _globally_disabled():
        return {"model": model, "url": url, "local": True, "reachable": False,
                "ready": False, "state": "disabled"}
    if not _is_local(url):
        return {"model": model, "url": url, "local": False, "reachable": False,
                "ready": False, "state": "non_local"}
    models = await _reachable(url)
    if models is None:
        hint = (f"Ollama niet bereikbaar op {url}. "
                + ("LabX draait in een container: draai Ollama op de host "
                   "(bereikbaar via host.docker.internal) of als sidecar-container, "
                   "en zet zo nodig DATA_GUARD_LLM_URL."
                   if _in_docker() else
                   "Start Ollama (`ollama serve`) of installeer het."))
        return {"model": model, "url": url, "local": True, "reachable": False,
                "ready": False, "state": "unreachable", "in_docker": _in_docker(),
                "hint": hint}

    def _norm(s: str) -> str:
        return s if ":" in s else s + ":latest"
    want = _norm(model)
    ready = any(_norm(m) == want for m in models)
    return {"model": model, "url": url, "local": True, "reachable": True,
            "ready": ready, "state": "ready" if ready else "pulling",
            "in_docker": _in_docker(),
            "hint": None if ready else f"Model {model} wordt opgehaald…"}


async def ensure_guard_model() -> Dict[str, Any]:
    """Best-effort ensure the local guard model is usable. Never blocks on a
    model download: starts/installs Ollama and kicks off the pull, then
    returns. Until the model is there, the check fails open (rules remain)."""
    if _globally_disabled():
        return {"status": "disabled"}
    url, model = _url(), _model()
    if not _is_local(url):
        return {"status": "skipped", "reason": "non-local URL"}

    models = await _reachable(url)
    if models is not None:
        def _norm(s: str) -> str:
            return s if ":" in s else s + ":latest"
        want = _norm(model)
        if any(_norm(m) == want for m in models):
            return {"status": "ready", "model": model}
        try:
            if shutil.which("ollama"):
                await _spawn(f"ollama pull {model}")
            else:
                import httpx
                async with httpx.AsyncClient(timeout=5) as c:
                    await c.post(f"{url}/api/pull", json={"name": model, "stream": False})
        except Exception as exc:  # noqa: BLE001
            log.warningx("Guard-model pull-start mislukt", model=model, error=str(exc)[:200])
        log.infox("Guard-model wordt gepulld (achtergrond)", model=model)
        return {"status": "pulling", "model": model}

    if shutil.which("ollama"):
        await _spawn("ollama serve")
        await _spawn(f"sleep 3 && ollama pull {model}")
        log.infox("Ollama gestart + guard-model pull aangetrapt", model=model)
        return {"status": "starting", "model": model}

    if _in_docker():
        log.warningx("Ollama onbereikbaar en LabX draait in een container — "
                     "installeer Ollama op de host of draai een sidecar; "
                     "zet zo nodig DATA_GUARD_LLM_URL", url=url)
        return {"status": "unreachable", "in_docker": True,
                "reason": "run Ollama on the host (host.docker.internal) or as a sidecar"}

    if sys.platform == "darwin" and shutil.which("brew"):
        install = "brew install ollama && brew services start ollama || ollama serve"
    elif sys.platform.startswith("linux"):
        install = "curl -fsSL https://ollama.com/install.sh | sh"
    else:
        log.warningx("Ollama ontbreekt en kan niet automatisch geïnstalleerd worden "
                     "op dit platform — installeer handmatig", platform=sys.platform)
        return {"status": "missing", "reason": "no installer for platform"}
    try:
        await _spawn(f"{install} && sleep 3 && ollama pull {model}")
        log.infox("Ollama-installatie + guard-model pull aangetrapt (achtergrond)", model=model)
        return {"status": "installing", "model": model}
    except Exception as exc:  # noqa: BLE001
        log.warningx("Ollama auto-install mislukt", error=str(exc)[:200])
        return {"status": "error", "reason": str(exc)[:200]}
