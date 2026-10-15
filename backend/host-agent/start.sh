#!/usr/bin/env bash
# start.sh — de hostagent starten, met een token dat blijft bestaan.
#
# Het token wordt één keer gemaakt en bewaard in ~/.labx-host-agent-token, met
# rechten 600. Dat is bewust: zonder bewaarplek maak je elke keer een nieuw
# token en moet je het elke keer opnieuw in LabX zetten -- en dan zet je het
# uiteindelijk niet meer en laat je de agent zonder.
set -euo pipefail

TOKENBESTAND="${LABX_HOST_AGENT_TOKEN_FILE:-$HOME/.labx-host-agent-token}"

if [ ! -f "$TOKENBESTAND" ]; then
  python3 -c 'import secrets;print(secrets.token_urlsafe(32))' > "$TOKENBESTAND"
  chmod 600 "$TOKENBESTAND"
  echo "Nieuw token gemaakt in $TOKENBESTAND"
fi

export LABX_HOST_AGENT_TOKEN="$(cat "$TOKENBESTAND")"
echo "Token (zet dit in LabX bij het hostlab):"
echo
echo "    $LABX_HOST_AGENT_TOKEN"
echo
exec python3 "$(dirname "$0")/labx_host_agent.py"
