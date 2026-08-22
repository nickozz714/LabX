#!/usr/bin/env bash
# scripts/smoke.sh — exercises the main path against a running
# `docker compose up` stack: login, Docker diagnosis, lab create/exec/delete.
set -euo pipefail

API="${LABX_API_URL:-http://localhost:8090}"
USER="${LABX_ADMIN_USERNAME:-admin}"
PASS="${LABX_ADMIN_PASSWORD:?set LABX_ADMIN_PASSWORD}"

echo "== health =="
curl -sf "$API/api/system/health" | tee /dev/stderr | grep -q '"ok":true'

echo "== login =="
TOKEN=$(curl -sf -X POST "$API/api/auth/login" -H "Content-Type: application/json" \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
AUTH=(-H "Authorization: Bearer $TOKEN")

echo "== docker diagnose =="
curl -sf "$API/api/system/docker" "${AUTH[@]}" | tee /dev/stderr | grep -q '"daemon_up":true'

echo "== create lab =="
LAB=$(curl -sf -X POST "$API/api/labs" "${AUTH[@]}" -H "Content-Type: application/json" \
  -d '{"name":"smoke","environment":"debian","allow_network":true,"ttl_hours":1,"llm_guard":false}')
echo "$LAB"
LAB_ID=$(echo "$LAB" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
STATUS=$(echo "$LAB" | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])")
[ "$STATUS" = "running" ] || { echo "lab did not reach running: $STATUS"; exit 1; }

echo "== exec =="
curl -sf -X POST "$API/api/labs/$LAB_ID/exec" "${AUTH[@]}" -H "Content-Type: application/json" \
  -d '{"command":"echo smoke-ok"}' | tee /dev/stderr | grep -q "smoke-ok"

echo "== cleanup =="
curl -sf -X DELETE "$API/api/labs/$LAB_ID" "${AUTH[@]}" | grep -q '"ok":true'

echo "SMOKE OK"
