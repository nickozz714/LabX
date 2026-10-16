#!/bin/sh
# uitrollen.sh — LabX bijwerken zonder iemands chat af te breken.
#
# Dit bestaat omdat het mis ging. Op 16-10-2026 is een lopende chat gesneuveld
# met "Backend herstart tijdens de run": er wás gecontroleerd op lopende runs,
# maar VOOR het ophalen van de images. Dat ophalen duurt minuten, en in dat
# gat begon iemand een gesprek. De controle hoort dus vlak voor de HERSTART,
# en niet ervoor.
#
# Volgorde: eerst alles ophalen (dat stoort niemand), dan wachten tot er niets
# meer loopt, en pas dan omwisselen.
set -eu

MAP="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="$MAP/docker-compose.server.yml"
ENVBESTAND="$MAP/../.env"
WACHT_MAX=600          # tien minuten; daarna beslis jij
STIL_NODIG=2           # twee metingen op rij leeg, zodat we geen gaatje pakken

dc() { docker compose --env-file "$ENVBESTAND" -f "$COMPOSE" "$@"; }

lopende_runs() {
    docker exec labx-api python -c "
import sqlite3
db = sqlite3.connect('/data/labx.db')
print(db.execute(\"select count(*) from background_runs where status='running'\").fetchone()[0])
" 2>/dev/null || echo "?"
}

echo "→ Images ophalen (dit stoort niemand)"
dc pull api web

echo "→ Wachten tot er niets meer draait"
stil=0
verstreken=0
while [ "$verstreken" -lt "$WACHT_MAX" ]; do
    n="$(lopende_runs)"
    if [ "$n" = "?" ]; then
        echo "  kan de lopende runs niet lezen — ik ga NIET herstarten." >&2
        echo "  Draait labx-api wel? Controleer handmatig en rol daarna uit." >&2
        exit 1
    fi
    if [ "$n" = "0" ]; then
        stil=$((stil + 1))
        [ "$stil" -ge "$STIL_NODIG" ] && break
    else
        [ "$stil" -gt 0 ] && echo "  er begon weer iets; opnieuw wachten"
        stil=0
        echo "  $n run(s) bezig… ($verstreken s)"
    fi
    sleep 5
    verstreken=$((verstreken + 5))
done

if [ "$stil" -lt "$STIL_NODIG" ]; then
    echo >&2
    echo "  Na $WACHT_MAX seconden draait er nog steeds werk." >&2
    echo "  Niet herstart. Een lange agent-run afbreken kost iemand zijn antwoord;" >&2
    echo "  wacht tot het klaar is, of breek de run bewust af in LabX." >&2
    exit 1
fi

echo "→ Omwisselen"
dc up -d

echo "→ Controleren"
i=0
while [ "$i" -lt 30 ]; do
    code="$(curl -s -o /dev/null -w '%{http_code}' "http://$(grep '^LABX_BIND_IP=' "$ENVBESTAND" | cut -d= -f2):$(grep '^LABX_WEB_PORT=' "$ENVBESTAND" | cut -d= -f2)/api/system/health" || true)"
    [ "$code" = "200" ] && break
    i=$((i + 1))
    sleep 5
done
[ "$code" = "200" ] || { echo "  de api antwoordt niet (laatste code: $code)" >&2; exit 1; }

versie="$(docker inspect labx-api --format '{{range .Config.Env}}{{println .}}{{end}}' | grep LABX_VERSION || true)"
echo "  ✓ draait — $versie"
