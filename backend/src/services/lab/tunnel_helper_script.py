"""services/lab/tunnel_helper_script.py — de tekst van de helper zelf.

Apart van tunnel_helper.py omdat het shell is en geen Python: hier gaat het
om wat er op JOUW machine draait, en dat wil je als één blok kunnen lezen.

Keuzes die erin zitten en waarom:

- Hij praat met LabX via curl en python3. Allebei staan op elke Mac; node of
  jq aannemen zou een installatie betekenen voor iets wat een doorgeefluik is.
- De doorgang vraagt hij bij ELKE keuze opnieuw op. Dat is het hele punt:
  een poortnummer dat je opschrijft, klopt morgen niet meer.
- Het doorgeefluik is socat in een container, net als eerst. Docker heb je
  al, anders draaide LabX niet.
- Hij blijft open staan met een menu. Wisselen van branch is dan een cijfer
  intypen in plaats van een nieuwe download.
"""
from __future__ import annotations

SJABLOON = r"""#!/bin/sh
# LabX tunnel-helper
#
# Eén bestand voor al je branch-labs. Dubbelklikken, een lab kiezen, klaar.
# Wisselen van lab kan in het menu; sluiten ruimt de doorgeefluiken op.
#
# Wat hij doet: hij vraagt LabX welke labs er zijn, laat LabX de doorgang naar
# die machine openen, en zet op jouw machine localhost:<poort> door naar die
# doorgang. Dat laatste is nodig omdat sommige applicaties zichzelf op een
# vast nummer verwachten -- een OAuth-redirect staat op localhost:5173
# geregistreerd en nergens anders.
set -u

LABX="__LABX__"
SLEUTEL="__SLEUTEL__"
IMAGE="__IMAGE__"
NAAMVOORVOEGSEL="labx-tunnel-"

kleur() { printf '\033[1m%s\033[0m\n' "$1"; }

vraag_labx() {
  # $1 = pad, $2 = eventuele body (dan POST)
  if [ $# -gt 1 ]; then
    curl -sk -m 20 -X POST "$LABX$1" \
      -H "X-LabX-Tunnelsleutel: $SLEUTEL" \
      -H "Content-Type: application/json" -d "$2"
  else
    curl -sk -m 20 "$LABX$1" -H "X-LabX-Tunnelsleutel: $SLEUTEL"
  fi
}

opruimen() {
  for naam in $(docker ps -a --format '{{.Names}}' 2>/dev/null | grep "^$NAAMVOORVOEGSEL" || true); do
    docker rm -f "$naam" >/dev/null 2>&1
  done
}

afsluiten() {
  echo
  kleur "Tunnels opruimen..."
  opruimen
  exit 0
}
trap afsluiten INT TERM

if ! docker info >/dev/null 2>&1; then
  kleur "Docker draait niet. Start Docker en probeer het opnieuw."
  echo "Druk op Enter om te sluiten."; read -r _; exit 1
fi

while true; do
  clear
  kleur "LabX tunnel-helper"
  echo "$LABX"
  echo

  LIJST=$(vraag_labx "/api/tunnel/labs")
  if [ -z "$LIJST" ]; then
    kleur "Geen contact met LabX op $LABX"
    echo "Staat de server aan, en zit je op het juiste netwerk?"
    echo; echo "Druk op Enter om opnieuw te proberen, of Ctrl-C om te stoppen."
    read -r _; continue
  fi

  # De lijst uitschrijven en tegelijk bewaren, zodat de keuze een id oplevert.
  # Let op de vorm van deze f-strings: geen aanhalingstekens IN de
  # accolades. Dat is geen stijl maar een eis -- een backslash-escape binnen
  # een f-string-expressie is een SyntaxError, en dan valt dit script om op
  # de eerste regel die het moet tonen.
  REGELS=$(printf '%s' "$LIJST" | python3 -c '
import json, sys
labs = json.load(sys.stdin)
for i, p in enumerate(labs, 1):
    merk = "draait" if p["draait"] else "staat uit"
    poorten = ", ".join(str(q) for q in p["poorten"]) or "geen"
    lid = p["id"]
    naam = p["naam"]
    tak = p["branch"] or "geen branch"
    print(f"{i}\t{lid}\t{naam} ({tak}) - {merk}, poorten {poorten}")
')
  if [ -z "$REGELS" ]; then
    kleur "LabX kent geen branch-labs."
    echo "Maak er een aan bij een cluster, dan verschijnt hij hier."
    echo; echo "Druk op Enter om te verversen."; read -r _; continue
  fi

  printf '%s\n' "$REGELS" | while IFS="$(printf '\t')" read -r n _id rest; do
    printf '  %s) %s\n' "$n" "$rest"
  done
  echo
  echo "  v) verversen     s) stoppen en opruimen"
  echo
  printf 'Welk lab? '
  read -r KEUZE

  case "$KEUZE" in
    s|S) afsluiten ;;
    v|V|"") continue ;;
  esac

  LAB=$(printf '%s\n' "$REGELS" | awk -F"\t" -v k="$KEUZE" '$1==k {print $2}')
  NAAM=$(printf '%s\n' "$REGELS" | awk -F"\t" -v k="$KEUZE" '$1==k {print $3}')
  if [ -z "$LAB" ]; then
    kleur "Dat is geen van de nummers hierboven."
    sleep 1; continue
  fi

  kleur "Doorgangen opvragen voor $NAAM"
  ADRESSEN=$(vraag_labx "/api/tunnel/adres" "{\"lab_id\": \"$LAB\"}")
  if [ -z "$ADRESSEN" ]; then
    kleur "LabX gaf geen adres terug."
    echo "Draait dit lab wel? Een lab dat uit staat publiceert geen poorten."
    echo; echo "Druk op Enter."; read -r _; continue
  fi

  FOUT=$(printf '%s' "$ADRESSEN" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("LabX gaf geen leesbaar antwoord."); raise SystemExit
print(d.get("detail", "") if isinstance(d, dict) and "detail" in d else "")
')
  if [ -n "$FOUT" ]; then
    kleur "$FOUT"
    echo; echo "Druk op Enter."; read -r _; continue
  fi

  # Eerst het oude opruimen: één poort kan maar naar één branch tegelijk.
  opruimen

  printf '%s' "$ADRESSEN" | python3 -c '
import json, sys
for p in json.load(sys.stdin)["poorten"]:
    lokaal = p["lokaal"]
    host = p["host"]
    poort = p["poort"]
    print(f"{lokaal}\t{host}\t{poort}")
' | while IFS="$(printf '\t')" read -r lokaal host poort; do
    docker run -d --rm --name "$NAAMVOORVOEGSEL$lokaal" \
      -p "127.0.0.1:$lokaal:$lokaal" "$IMAGE" \
      "tcp-listen:$lokaal,fork,reuseaddr" "tcp:$host:$poort" >/dev/null 2>&1 \
      && printf '  open: http://localhost:%s\n' "$lokaal" \
      || printf '  MISLUKT op poort %s — gebruikt iets anders die al?\n' "$lokaal"
  done

  echo
  kleur "$NAAM staat open."
  echo "Laat dit venster open staan. Een ander lab kiezen kan hieronder;"
  echo "sluiten of Ctrl-C ruimt de doorgeefluiken op."
  echo
  printf 'Enter voor het menu, of s om te stoppen: '
  read -r NA
  case "$NA" in s|S) afsluiten ;; esac
done
"""


def script(*, labx_url: str, sleutel: str, image: str) -> str:
    """Het sjabloon met jouw server en sleutel erin."""
    return (SJABLOON
            .replace("__LABX__", labx_url.rstrip("/"))
            .replace("__SLEUTEL__", sleutel)
            .replace("__IMAGE__", image))
