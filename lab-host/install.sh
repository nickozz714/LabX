#!/bin/sh
# install.sh — de LabX lab-host-agent op deze computer zetten.
#
# Bewust een POSIX-sh-script zonder afhankelijkheden: dit draait op een
# machine waar nog niets van LabX staat, en alles wat we hier nodig hebben
# (curl, python3) zit er al op.
#
# Het haalt het script op, controleert of deze machine de eisen haalt, en
# start hem in dit venster. NIET als achtergrondtaak, en dat is de kern:
# macOS vraagt de eerste keer toestemming om het lokale netwerk op te mogen,
# en die vraag komt alleen in beeld bij een proces met een venster. Een
# losgekoppeld proces wordt stil geweigerd en lijkt gewoon te draaien.
set -eu

BRON="https://raw.githubusercontent.com/nickozz714/LabX/main/lab-host/labx_lab_host.py"
MAP="$HOME/labx-lab-host"
LABX=""
AUTOSTART="nee"
SSH_ADRES=""

while [ $# -gt 0 ]; do
    case "$1" in
        --labx)      LABX="${2:-}"; shift 2 ;;
        --ssh)       SSH_ADRES="${2:-}"; shift 2 ;;
        --autostart) AUTOSTART="ja"; shift ;;
        -h|--help)
            echo "Gebruik: install.sh --labx http://jouw-labx:8081 [--ssh gebruiker@pc] [--autostart]"
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

if [ "$(uname -s)" = "Darwin" ]; then
    if systemsetup -getremotelogin 2>/dev/null | grep -qi "on"; then
        echo "  ✓ externe login staat aan"
    else
        echo "  ! externe login lijkt UIT te staan. LabX stuurt containers aan"
        echo "    over SSH, dus zonder dat kan deze machine geen labs draaien."
        echo "    Systeeminstellingen -> Algemeen -> Delen -> Externe login."
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

if [ "$AUTOSTART" = "ja" ] && [ "$(uname -s)" = "Darwin" ]; then
    PLIST="$HOME/Library/LaunchAgents/nl.labx.labhost.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    PY="$(command -v python3)"
    {
        echo '<?xml version="1.0" encoding="UTF-8"?>'
        echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
        echo '<plist version="1.0"><dict>'
        echo '  <key>Label</key><string>nl.labx.labhost</string>'
        echo '  <key>ProgramArguments</key><array>'
        echo "    <string>$PY</string>"
        echo "    <string>$MAP/labx_lab_host.py</string>"
        echo '    <string>--labx</string>'
        echo "    <string>$LABX</string>"
        [ -n "$SSH_ADRES" ] && { echo '    <string>--ssh</string>'; echo "    <string>$SSH_ADRES</string>"; }
        echo '  </array>'
        echo '  <key>RunAtLoad</key><true/>'
        echo '  <key>KeepAlive</key><true/>'
        echo "  <key>StandardOutPath</key><string>$MAP/agent.log</string>"
        echo "  <key>StandardErrorPath</key><string>$MAP/agent.log</string>"
        echo '</dict></plist>'
    } > "$PLIST"
    launchctl bootout "gui/$(id -u)/nl.labx.labhost" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "  ✓ start voortaan bij inloggen ($PLIST)"
    echo
    echo "  LET OP: een achtergrondtaak krijgt de vraag om lokaal-netwerktoegang"
    echo "  niet in beeld. Laat de agent hieronder één keer met de hand draaien"
    echo "  en geef toestemming; daarna werkt de LaunchAgent ook."
    echo
fi

echo "→ Starten. Laat dit venster open."
echo
exec python3 "$MAP/labx_lab_host.py" --labx "$LABX" ${SSH_ADRES:+--ssh "$SSH_ADRES"}
