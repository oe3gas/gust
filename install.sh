#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════
# GUST — Installations-Skript für Linux (Debian/Ubuntu/Raspberry Pi OS)
# OE3GAS — Amateurfunk-Digitalprotokoll für KW-Telemetrie
# ────────────────────────────────────────────────────────────────────────
# Verwendung:   ./install.sh
# Vorbedingung: Skript ausführbar (chmod +x install.sh)
#               Im GUST-Projektverzeichnis ausführen.
#
# Was tut dieses Skript?
#   1. Erkennt Plattform (Ubuntu/Debian/Raspberry Pi OS)
#   2. Prüft Python-Version (>=3.11 erforderlich)
#   3. Installiert apt-Systempakete (portaudio, hamlib, libusb, ...)
#   4. Installiert GUST via pip (editable, mit dev-Extras)
#   5. Erstellt ~/.config/gust/gateway.json (interaktive Abfrage)
#   6. Installiert udev-Regeln für SDR-Hardware
#   7. Installiert optional einen systemd-Service
# ════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── ANSI-Farben für Ausgabe ─────────────────────────────────────────────
readonly C_RESET=$'\033[0m'
readonly C_GREEN=$'\033[32m'
readonly C_YELLOW=$'\033[33m'
readonly C_RED=$'\033[31m'
readonly C_BLUE=$'\033[34m'
readonly C_BOLD=$'\033[1m'

msg_ok()   { printf '%s✓%s %s\n' "$C_GREEN"  "$C_RESET" "$*"; }
msg_warn() { printf '%s!%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
msg_err()  { printf '%s✗%s %s\n' "$C_RED"    "$C_RESET" "$*" >&2; }
msg_info() { printf '%s→%s %s\n' "$C_BLUE"   "$C_RESET" "$*"; }
msg_step() { printf '\n%s%s%s\n' "$C_BOLD" "$*" "$C_RESET"; }

die() { msg_err "$*"; exit 1; }

# ── Sicherheitsprüfungen ────────────────────────────────────────────────
[[ $EUID -eq 0 ]] && die "Bitte NICHT als root ausführen. sudo wird gezielt verwendet."

GUST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$GUST_DIR"

[[ -f gust.py ]] || die "gust.py nicht gefunden. Im GUST-Projektverzeichnis ausführen."
[[ -f pyproject.toml ]] || die "pyproject.toml nicht gefunden."

# ════════════════════════════════════════════════════════════════════════
# Schritt 1: Plattform erkennen
# ════════════════════════════════════════════════════════════════════════
msg_step "[1/7] Plattform erkennen"

ARCH=$(uname -m)
IS_RPI=false
DISTRO_ID=""
DISTRO_VERSION=""

if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    DISTRO_ID="${ID:-unknown}"
    DISTRO_VERSION="${VERSION_ID:-unknown}"
fi

if [[ -f /proc/device-tree/model ]]; then
    RPI_MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo "")
    if [[ "$RPI_MODEL" == *"Raspberry Pi"* ]]; then
        IS_RPI=true
        msg_info "Raspberry Pi erkannt: $RPI_MODEL"
    fi
fi

case "$ARCH" in
    aarch64|armv7l|armv6l)
        [[ "$IS_RPI" == "false" ]] && msg_warn "ARM-Architektur ($ARCH) ohne RPi — fortfahren"
        ;;
    x86_64)
        msg_info "x86_64 — Desktop/Server"
        ;;
    *)
        msg_warn "Unbekannte Architektur: $ARCH"
        ;;
esac

msg_ok "Distribution: ${DISTRO_ID} ${DISTRO_VERSION} (${ARCH})"

# ════════════════════════════════════════════════════════════════════════
# Schritt 2: Python-Version prüfen
# ════════════════════════════════════════════════════════════════════════
msg_step "[2/7] Python-Version prüfen"

if ! command -v python3 >/dev/null 2>&1; then
    die "python3 nicht gefunden. Bitte installieren: sudo apt install python3"
fi

PY_VERSION=$(python3 -c 'import sys; print("{}.{}".format(sys.version_info.major, sys.version_info.minor))')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [[ "$PY_MAJOR" -lt 3 ]] || { [[ "$PY_MAJOR" -eq 3 ]] && [[ "$PY_MINOR" -lt 11 ]]; }; then
    die "Python 3.11 oder neuer erforderlich (gefunden: $PY_VERSION)."
fi

msg_ok "Python $PY_VERSION"

# ════════════════════════════════════════════════════════════════════════
# Schritt 3: Bereits installiert?
# ════════════════════════════════════════════════════════════════════════
msg_step "[3/7] Bestehende Installation prüfen"

EXISTING_INSTALL=false
if python3 -c "import gust" >/dev/null 2>&1; then
    EXISTING_INSTALL=true
    msg_warn "GUST ist bereits installiert."
    read -rp "Neu installieren / aktualisieren? [j/N] " response
    if [[ ! "$response" =~ ^[jJyY]$ ]]; then
        msg_info "Installation abgebrochen."
        exit 0
    fi
else
    msg_ok "Keine bestehende Installation gefunden."
fi

# ════════════════════════════════════════════════════════════════════════
# Schritt 4: apt-Systemabhängigkeiten
# ════════════════════════════════════════════════════════════════════════
msg_step "[4/7] Systemabhängigkeiten installieren (apt)"

APT_PACKAGES=(
    portaudio19-dev      # PortAudio C-Bibliothek (für sounddevice)
    python3-dev          # Python-Header für C-Extensions
    python3-pip          # pip selbst
    libhamlib-dev        # hamlib (rigctld für PTT)
    libhamlib-utils      # rigctld Binary
    libusb-1.0-0-dev     # USB-Geräte (HackRF, SDRplay)
    udev                 # udev-Regeln
    alsa-utils           # aplay/arecord Diagnose
    build-essential      # gcc/make für C-Extensions
    git                  # für editable Installs aus git
)

if [[ "$IS_RPI" == "true" ]]; then
    APT_PACKAGES+=(
        python3-rpi.gpio        # RPi.GPIO (System-Paket, schneller)
        i2c-tools               # i2cdetect für BME280-Check
    )
fi

msg_info "Pakete: ${APT_PACKAGES[*]}"
sudo apt-get update -qq
sudo apt-get install -y "${APT_PACKAGES[@]}"
msg_ok "apt-Pakete installiert"

# ════════════════════════════════════════════════════════════════════════
# Schritt 5: GUST via pip installieren
# ════════════════════════════════════════════════════════════════════════
msg_step "[5/7] GUST installieren (pip, editable)"

PIP_ARGS=(install --user -e)
EXTRAS="[dev]"

if [[ "$IS_RPI" == "true" ]]; then
    # Raspberry Pi OS Bookworm+ erzwingt PEP 668 (externally-managed)
    PIP_ARGS=(install --break-system-packages -e)
    EXTRAS="[rpi,dev]"
    msg_info "Raspberry Pi: --break-system-packages aktiv (PEP 668)"
fi

python3 -m pip "${PIP_ARGS[@]}" ".${EXTRAS}"

# `gust`-Binary muss jetzt verfügbar sein
GUST_BIN="$(command -v gust || true)"
if [[ -z "$GUST_BIN" ]]; then
    # Fallback: ~/.local/bin/gust
    if [[ -x "$HOME/.local/bin/gust" ]]; then
        GUST_BIN="$HOME/.local/bin/gust"
        msg_warn "$HOME/.local/bin nicht in PATH — füge das in ~/.bashrc hinzu:"
        msg_warn "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    else
        die "gust-Binary nicht gefunden nach pip install."
    fi
fi
msg_ok "GUST installiert: $GUST_BIN"

# ════════════════════════════════════════════════════════════════════════
# Schritt 6: Konfiguration (~/.config/gust/gateway.json)
# ════════════════════════════════════════════════════════════════════════
msg_step "[6/7] Konfiguration anlegen"

CONFIG_DIR="$HOME/.config/gust"
CONFIG_FILE="$CONFIG_DIR/gateway.json"
mkdir -p "$CONFIG_DIR"

if [[ -f "$CONFIG_FILE" ]]; then
    msg_warn "$CONFIG_FILE existiert bereits."
    read -rp "Überschreiben? [j/N] " response
    if [[ ! "$response" =~ ^[jJyY]$ ]]; then
        msg_info "Konfiguration unverändert."
        SKIP_CONFIG=true
    else
        cp "$CONFIG_FILE" "$CONFIG_FILE.bak.$(date +%Y%m%d-%H%M%S)"
        msg_info "Backup angelegt."
        SKIP_CONFIG=false
    fi
else
    SKIP_CONFIG=false
fi

if [[ "${SKIP_CONFIG:-false}" == "false" ]]; then
    # ── Rufzeichen ──
    while true; do
        read -rp "Rufzeichen (3-6 Zeichen, Großbuchstaben/Ziffern): " CALLSIGN
        CALLSIGN=$(echo "$CALLSIGN" | tr '[:lower:]' '[:upper:]')
        if [[ "$CALLSIGN" =~ ^[A-Z0-9]{3,6}$ ]]; then
            break
        fi
        msg_warn "Ungültig. Beispiel: OE3GAS, DL1ABC."
    done

    # ── Audiogerät ──
    read -rp "Audiogerät-ID [0]: " AUDIO_DEV
    AUDIO_DEV="${AUDIO_DEV:-0}"

    # ── PTT-Backend ──
    echo "PTT-Backend wählen:"
    echo "  1) null   — kein Hardware-PTT (Tests, dry-run)"
    echo "  2) hamlib — rigctld (IC-7610, IC-705, Yaesu, ...)"
    echo "  3) vox    — VOX (automatisch, kein CAT)"
    [[ "$IS_RPI" == "true" ]] && echo "  4) gpio   — Raspberry Pi GPIO-Pin"
    read -rp "Auswahl [2]: " PTT_CHOICE
    PTT_CHOICE="${PTT_CHOICE:-2}"

    case "$PTT_CHOICE" in
        1) PTT_BACKEND="null" ;;
        2) PTT_BACKEND="hamlib" ;;
        3) PTT_BACKEND="vox" ;;
        4) PTT_BACKEND="gpio" ;;
        *) PTT_BACKEND="hamlib" ;;
    esac

    HAMLIB_PORT=4532
    if [[ "$PTT_BACKEND" == "hamlib" ]]; then
        read -rp "hamlib-Port (rigctld) [4532]: " HAMLIB_PORT_IN
        HAMLIB_PORT="${HAMLIB_PORT_IN:-4532}"
    fi

    # ── Web-UI ──
    read -rp "Web-UI Port [8080]: " WEB_PORT
    WEB_PORT="${WEB_PORT:-8080}"

    read -rp "API-Key (leer = kein Auth): " API_KEY

    # ── gateway.json schreiben (Python für sicheres JSON-Quoting) ──
    python3 - "$CONFIG_FILE" "$CALLSIGN" "$AUDIO_DEV" "$PTT_BACKEND" \
        "$HAMLIB_PORT" "$WEB_PORT" "$API_KEY" <<'PYEOF'
import json, sys
path, cs, dev, ptt, hl_port, web_port, api_key = sys.argv[1:]
try:
    dev_v = int(dev)
except ValueError:
    dev_v = dev
cfg = {
    "callsign": cs,
    "audio": {
        "device": dev_v,
        "ptt_backend": ptt,
        "level": 10,
        "hamlib_host": "localhost",
        "hamlib_port": int(hl_port),
    },
    "rx": {
        "enabled": True,
        "device": None,
        "scan_interval_s": 2.0,
        "window_s": 9.0,
        "dedup_ttl_s": 30,
    },
    "gateway": {
        "interval_s": 300,
        "min_tx_gap_s": 10,
    },
    "web": {
        "host": "0.0.0.0",
        "port": int(web_port),
        "api_key": api_key,
    },
    "log": {
        "level": "INFO",
        "file": None,
    },
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=4, ensure_ascii=False)
    f.write("\n")
PYEOF
    chmod 600 "$CONFIG_FILE"
    msg_ok "$CONFIG_FILE angelegt (0600)"
fi

# ════════════════════════════════════════════════════════════════════════
# Schritt 7: udev-Regeln + systemd
# ════════════════════════════════════════════════════════════════════════
msg_step "[7/7] udev-Regeln und systemd-Service"

# ── udev ──
if [[ -f "$GUST_DIR/udev/99-gust-sdr.rules" ]]; then
    read -rp "udev-Regeln für SDR-Hardware installieren? [J/n] " response
    if [[ ! "$response" =~ ^[nN]$ ]]; then
        sudo cp "$GUST_DIR/udev/99-gust-sdr.rules" /etc/udev/rules.d/
        sudo udevadm control --reload-rules
        sudo udevadm trigger
        # User zur plugdev-Gruppe hinzufügen
        if ! groups "$USER" | grep -qw plugdev; then
            sudo usermod -aG plugdev "$USER"
            msg_warn "User $USER wurde Gruppe 'plugdev' hinzugefügt."
            msg_warn "Bitte einmal abmelden und neu anmelden, damit es wirkt."
        fi
        # dialout für serielle CAT-Adapter
        if ! groups "$USER" | grep -qw dialout; then
            sudo usermod -aG dialout "$USER"
            msg_warn "User $USER wurde Gruppe 'dialout' hinzugefügt (serielles CAT)."
        fi
        msg_ok "udev-Regeln installiert"
    fi
fi

# ── systemd-Service ──
HAS_SYSTEMD=false
if command -v systemctl >/dev/null 2>&1 && \
   systemctl is-system-running --quiet 2>/dev/null || \
   [[ "$(systemctl is-system-running 2>/dev/null || echo offline)" =~ ^(running|degraded|starting)$ ]]; then
    HAS_SYSTEMD=true
fi

if [[ "$HAS_SYSTEMD" == "true" ]] && [[ -f "$GUST_DIR/systemd/gust.service" ]]; then
    read -rp "systemd-Service 'gust' installieren und aktivieren? [j/N] " response
    if [[ "$response" =~ ^[jJyY]$ ]]; then
        SERVICE_TMP=$(mktemp)
        sed \
            -e "s|%USER%|$USER|g" \
            -e "s|%GROUP%|$(id -gn)|g" \
            -e "s|%HOME%|$HOME|g" \
            -e "s|%EXEC%|$GUST_BIN|g" \
            "$GUST_DIR/systemd/gust.service" > "$SERVICE_TMP"
        sudo install -m 644 "$SERVICE_TMP" /etc/systemd/system/gust.service
        rm -f "$SERVICE_TMP"
        sudo systemctl daemon-reload
        read -rp "Jetzt starten und beim Boot aktivieren? [j/N] " response2
        if [[ "$response2" =~ ^[jJyY]$ ]]; then
            sudo systemctl enable --now gust.service
            msg_ok "gust.service läuft. Status: systemctl status gust"
        else
            msg_info "Service installiert (nicht aktiviert)."
            msg_info "Starten mit: sudo systemctl start gust"
        fi
    fi
else
    msg_info "systemd nicht aktiv — Service-Installation übersprungen."
fi

# ════════════════════════════════════════════════════════════════════════
# Abschluss
# ════════════════════════════════════════════════════════════════════════
msg_step "Installation abgeschlossen"
cat <<EOF

${C_GREEN}${C_BOLD}GUST wurde erfolgreich installiert!${C_RESET}

Nächste Schritte:
  ${C_BOLD}1.${C_RESET} Verfügbare Audiogeräte prüfen:
       ${C_BLUE}gust devices${C_RESET}

  ${C_BOLD}2.${C_RESET} Daemon im Simulator-Modus starten (kein Hardware nötig):
       ${C_BLUE}gust daemon --sim${C_RESET}
       Browser:  ${C_BLUE}http://localhost:${WEB_PORT:-8080}${C_RESET}

  ${C_BOLD}3.${C_RESET} Kanal- und Zeitversatz für eigenes Rufzeichen:
       ${C_BLUE}gust info${C_RESET}

  ${C_BOLD}4.${C_RESET} Konfiguration anpassen:
       ${C_BLUE}\$EDITOR $CONFIG_FILE${C_RESET}

Dokumentation:    ${C_BLUE}How_to_install_on_Linux.md${C_RESET}
Systemd-Logs:     ${C_BLUE}journalctl -u gust -f${C_RESET}
Repository:       ${C_BLUE}https://github.com/OE3GAS/gust${C_RESET}

73 de OE3GAS
EOF
