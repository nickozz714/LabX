#!/bin/sh
# install.sh — de LabX lab-host-agent op deze computer zetten.
#
# Bewust een POSIX-sh-script zonder afhankelijkheden: dit draait op een
# machine waar nog niets van LabX staat, en alles wat we hier nodig hebben
# (curl, python3) zit er al op.
#
# Hij installeert de agent als ACHTERGRONDDIENST en start hem. Dat hoort ook
# zo: een lab-host die je alleen draait zolang er een terminalvenster open
# staat, is geen lab-host.
#
# Eén ding staat die achtergronddienst op macOS in de weg: toestemming voor
# het lokale netwerk. Die vraag komt alleen in beeld bij een proces met een
# venster, en een dienst onder launchd krijgt hem nooit te zien -- hij wordt
# stil geweigerd en lijkt gewoon te draaien. Daarom CONTROLEERT dit script na
# het starten of de agent LabX werkelijk bereikt, en zegt hij precies wat je
# moet doen als dat niet zo is. Meten in plaats van aannemen.
set -eu

BRON="https://raw.githubusercontent.com/nickozz714/LabX/main/lab-host/labx_lab_host.py"
MAP="$HOME/labx-lab-host"
LABX=""
VOORGROND="nee"
SSH_ADRES=""

while [ $# -gt 0 ]; do
    case "$1" in
        --labx)      LABX="${2:-}"; shift 2 ;;
        --ssh)       SSH_ADRES="${2:-}"; shift 2 ;;
        --voorgrond) VOORGROND="ja"; shift ;;
        --autostart) shift ;;   # was de oude naam; achtergrond is nu standaard
        -h|--help)
            echo "Gebruik: install.sh --labx http://jouw-labx:8081 [--ssh gebruiker@pc] [--voorgrond]"
            echo
            echo "Standaard draait de agent als achtergronddienst en start hij"
            echo "bij het inloggen opnieuw. --voorgrond draait hem in dit venster."
            exit 0 ;;
        *) echo "Onbekende optie: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$LABX" ]; then
    echo "Geef op waar LabX draait, bijvoorbeeld:" >&2
    echo "  install.sh --labx http://192.168.2.10:8081" >&2
    echo >&2
    echo "Dat adres staat in LabX bij Labs -> Machines, met een kopieerknop." >&2
    exit 2
fi

case "$LABX" in
    *localhost*|*127.0.0.1*)
        echo "LET OP: '$LABX' wijst op DEZE computer naar zichzelf." >&2
        echo "Gebruik het adres waarop LabX in je netwerk te bereiken is." >&2
        exit 2 ;;
esac

echo "→ Controleren wat deze machine te bieden heeft"

if ! command -v python3 >/dev/null 2>&1; then
    echo "  ✗ python3 ontbreekt. Installeer dat eerst." >&2
    exit 1
fi
echo "  ✓ $(python3 --version 2>&1)"

# Docker hoeft niet in het PATH van DIT venster te staan; de agent zoekt hem
# zelf op de plekken waar Docker Desktop hem neerzet. Maar helemaal geen
# docker betekent: hier valt niets te hosten.
if command -v docker >/dev/null 2>&1; then
    echo "  ✓ $(docker --version 2>&1 | head -1)"
elif [ -x /usr/local/bin/docker ] || [ -x "$HOME/.docker/bin/docker" ]; then
    echo "  ✓ docker gevonden (staat niet in je PATH; de agent regelt dat)"
else
    echo "  ✗ geen docker. Start Docker Desktop en probeer opnieuw." >&2
    exit 1
fi

# Luistert er SSH? Dat is de enige vraag die telt, en de enige die eerlijk te
# beantwoorden is. `systemsetup -getremotelogin` lijkt het juiste gereedschap,
# maar vereist Volledige Schijftoegang en geeft zonder dat niets terug -- dan
# waarschuwt hij dus dat externe login uit staat terwijl je er op dat moment
# via SSH op zit. Een melding die je naar een instelling stuurt die al goed
# staat, is erger dan geen melding.
if python3 -c "
import socket, sys
s = socket.socket(); s.settimeout(2)
try:
    s.connect(('127.0.0.1', 22))
except Exception:
    sys.exit(1)
finally:
    s.close()
" 2>/dev/null; then
    echo "  ✓ SSH luistert op poort 22"
else
    echo "  ! er luistert niets op poort 22. LabX stuurt containers aan over"
    echo "    SSH, dus zonder dat kan deze machine geen labs draaien."
    if [ "$(uname -s)" = "Darwin" ]; then
        echo "    Systeeminstellingen -> Algemeen -> Delen -> Externe login."
    else
        echo "    Zet de ssh-server aan (bijv. systemctl enable --now ssh)."
    fi
fi

echo "→ Agent ophalen"
mkdir -p "$MAP"
if ! curl -fsSL "$BRON" -o "$MAP/labx_lab_host.py"; then
    echo "  GitHub niet bereikbaar; dan via LabX zelf" >&2
    curl -fsSL "$LABX/api/lab-hosts/agent" -o "$MAP/labx_lab_host.py"
fi
python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$MAP/labx_lab_host.py"
echo "  ✓ $MAP/labx_lab_host.py"

if [ "$VOORGROND" = "ja" ]; then
    echo "→ Starten in dit venster. Laat het open."
    echo
    exec python3 "$MAP/labx_lab_host.py" --labx "$LABX" ${SSH_ADRES:+--ssh "$SSH_ADRES"}
fi

# ── als achtergronddienst ───────────────────────────────────────────────────

LOG="$MAP/agent.log"
: > "$LOG"
PY_BIN="$(command -v python3)"

if [ "$(uname -s)" = "Darwin" ]; then
    echo "→ Installeren als achtergronddienst (launchd)"
    PLIST="$HOME/Library/LaunchAgents/nl.labx.labhost.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    {
        echo '<?xml version="1.0" encoding="UTF-8"?>'
        echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
        echo '<plist version="1.0"><dict>'
        echo '  <key>Label</key><string>nl.labx.labhost</string>'
        echo '  <key>ProgramArguments</key><array>'
        echo "    <string>$PY_BIN</string>"
        echo "    <string>$MAP/labx_lab_host.py</string>"
        echo '    <string>--labx</string>'
        echo "    <string>$LABX</string>"
        [ -n "$SSH_ADRES" ] && { echo '    <string>--ssh</string>'; echo "    <string>$SSH_ADRES</string>"; }
        echo '  </array>'
        echo '  <key>RunAtLoad</key><true/>'
        echo '  <key>KeepAlive</key><true/>'
        echo "  <key>StandardOutPath</key><string>$LOG</string>"
        echo "  <key>StandardErrorPath</key><string>$LOG</string>"
        echo '</dict></plist>'
    } > "$PLIST"
    launchctl bootout "gui/$(id -u)/nl.labx.labhost" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    HERSTART="launchctl kickstart -k gui/$(id -u)/nl.labx.labhost"
    echo "  ✓ draait, en start voortaan bij het inloggen"
else
    echo "→ Installeren als achtergronddienst (systemd --user)"
    systemctl --user stop labx-labhost 2>/dev/null || true
    # shellcheck disable=SC2086
    systemd-run --user --unit=labx-labhost --collect \
        --property=Restart=always --setenv=PYTHONUNBUFFERED=1 \
        "$PY_BIN" "$MAP/labx_lab_host.py" --labx "$LABX" ${SSH_ADRES:+--ssh "$SSH_ADRES"} \
        >/dev/null 2>&1
    HERSTART="systemctl --user restart labx-labhost"
    # systemd schrijft naar de journal, niet naar ons logbestand.
    sleep 3
    journalctl --user -u labx-labhost --no-pager -n 50 > "$LOG" 2>/dev/null || true
    echo "  ✓ draait (logboek: journalctl --user -u labx-labhost)"
fi

# ── en dan: komt hij er werkelijk aan? ──────────────────────────────────────
#
# Hier zit de hele reden dat dit script bestaat. Een achtergronddienst die
# draait maar LabX niet bereikt, ziet er precies zo uit als eentje die het
# doet -- en dan sta je morgen te kijken naar een machine die zich nooit
# gemeld heeft.
echo "→ Controleren of hij LabX bereikt"
GELUKT="nee"
i=0
while [ $i -lt 20 ]; do
    [ -f "$LOG" ] || : > "$LOG"
    if [ "$(uname -s)" != "Darwin" ]; then
        journalctl --user -u labx-labhost --no-pager -n 50 > "$LOG" 2>/dev/null || true
    fi
    if grep -q "Gekoppeld\|Koppelcode\|nog niet gekoppeld" "$LOG" 2>/dev/null; then
        GELUKT="ja"
        break
    fi
    if grep -q "mislukt\|niet bereiken" "$LOG" 2>/dev/null; then
        break
    fi
    i=$((i + 1))
    sleep 2
done

echo
if [ "$GELUKT" = "ja" ]; then
    sed -n '/─────/,/─────/p' "$LOG" 2>/dev/null | head -8
    grep -q "Gekoppeld" "$LOG" 2>/dev/null \
        && echo "  ✓ Gekoppeld — deze machine kan labs draaien." \
        || echo "  ✓ Aangemeld. Vul de koppelcode hierboven in bij Labs → Machines."
    echo
    echo "  Logboek:  $LOG"
    echo "  Herstart: $HERSTART"
    exit 0
fi

echo "  ✗ Hij draait, maar bereikt LabX niet. Uit het logboek:"
tail -3 "$LOG" 2>/dev/null | sed 's/^/     /'
echo
if [ "$(uname -s)" = "Darwin" ]; then
    cat <<'UITLEG'
  Dit is bijna altijd één ding: macOS laat een achtergronddienst niet zomaar
  op het lokale netwerk, en de vraag om toestemming komt alleen in beeld bij
  een programma met een venster. Hij staat nu wel in de lijst, dus:

    1. Systeeminstellingen → Privacy en beveiliging → Lokaal netwerk
    2. Zet het vinkje aan bij Python (of python3)
    3. Draai daarna:
UITLEG
    echo "         $HERSTART"
else
    echo "  Kan deze machine bij LabX? Probeer: curl -v $LABX/api/system/health"
fi
echo
echo "  Logboek: $LOG"
exit 1
