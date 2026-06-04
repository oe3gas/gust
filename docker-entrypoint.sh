#!/bin/sh
# ============================================================
# GUST Docker Entrypoint
# Erzeugt gateway.json falls nicht via Volume gemountet,
# dann startet den Daemon im Simulator-Modus.
# ============================================================

set -e

CALLSIGN="${GUST_CALLSIGN:-N0CALL}"
CONFIG="/app/gateway.json"

# Wenn keine gateway.json via Volume gemountet wurde, eine minimale erzeugen
if [ ! -f "$CONFIG" ]; then
    echo "[GUST] Kein gateway.json gefunden — erzeuge Minimal-Konfiguration..."
    cat > "$CONFIG" << EOF
{
  "callsign": "${CALLSIGN}",
  "web": {
    "host": "0.0.0.0",
    "port": 8080,
    "api_key": ""
  },
  "gateway": {
    "interval_s": 60,
    "min_tx_gap_s": 10
  },
  "source": {
    "adapter": "sim",
    "sim": {
      "frames": ["weather", "position", "text"],
      "weather_interval_s": 60,
      "position_interval_s": 90,
      "text_interval_s": 45,
      "emergency_enabled": false,
      "lat": 48.2082,
      "lon": 16.3738,
      "alt_m": 180,
      "drift": false
    }
  },
  "audio": {
    "device": 0,
    "ptt_backend": "null",
    "level": 30
  },
  "rx": {
    "enabled": false
  }
}
EOF
    echo "[GUST] Rufzeichen: ${CALLSIGN}"
    echo "[GUST] Web UI:     http://localhost:8080"
else
    echo "[GUST] Verwende gemountetes gateway.json"
    echo "[GUST] Web UI: http://localhost:8080"
fi

# GUST Daemon starten (Simulator, kein Audio-Device nötig)
exec python gust.py daemon --sim
