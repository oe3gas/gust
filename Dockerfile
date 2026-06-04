# ============================================================
# GUST — Generic Universal Shortwave Telemetry
# Docker Image — Simulator-Modus (kein Hardware erforderlich)
# ============================================================
# Verwendung:
#   docker build -t gust .
#   docker run --rm -p 8080:8080 gust
#   → http://localhost:8080
#
# Mit eigenem Rufzeichen:
#   docker run --rm -p 8080:8080 -e GUST_CALLSIGN=OE3XYZ gust
#
# Mit eigener gateway.json:
#   docker run --rm -p 8080:8080 -v ./my-gateway.json:/app/gateway.json gust
# ============================================================

FROM python:3.11-slim

LABEL maintainer="OE3GAS <github.com/oe3gas/gust>"
LABEL description="GUST — HF Telemetry Gateway (Simulator-Modus)"
LABEL version="0.3"

# PortAudio wird von sounddevice benötigt (auch ohne echte Hardware)
RUN apt-get update && apt-get install -y --no-install-recommends \
        portaudio19-dev \
        libportaudio2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Abhängigkeiten zuerst installieren (Docker Layer Cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Quellcode kopieren
COPY *.py ./

# Beispiel-Config als Fallback (wird von gateway.json überschrieben falls vorhanden)
COPY gateway.json.example ./gateway.json.example

# Startskript: erzeugt gateway.json aus Umgebungsvariable oder Beispiel-Config
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Web-UI Port
EXPOSE 8080

# Standardmäßig: Simulator-Modus, Port 8080, alle Interfaces
ENTRYPOINT ["./docker-entrypoint.sh"]
