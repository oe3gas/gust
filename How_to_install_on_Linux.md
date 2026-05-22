# GUST — Installation auf Linux

**Generic Universal Shortwave Telemetry — OE3GAS**
*Stand: Mai 2026 — v0.3.1*

Dieses Dokument beschreibt die Installation und Inbetriebnahme von GUST auf
Debian, Ubuntu und Raspberry Pi OS. Für Windows siehe `GETTING_STARTED.md`.

---

## Voraussetzungen

| Komponente             | Mindestversion | Hinweis                            |
|:-----------------------|:---------------|:-----------------------------------|
| **OS**                 | Debian 12 / Ubuntu 22.04 / Raspberry Pi OS Bookworm | älter geht meist, ist aber ungetestet |
| **Python**             | 3.11           | `python3 --version`                |
| **Architektur**        | x86_64, aarch64, armv7l | RPi 3/4/5 unterstützt       |
| **RAM**                | 512 MB         | RPi Zero 2 W reicht                |
| **Speicher**           | 200 MB         | ohne SDR-Treiber                   |

Für Hardware-TX (über IC-7610 oder ähnlich) zusätzlich:

- USB-Audio-Schnittstelle (z. B. IC-7610 CODEC oder USB-Soundkarte)
- CAT-Steuerung (USB-Seriell, hamlib-kompatibel)
- Optional: HackRF One (nur Labortest, **zwingend Python 3.9** für SoapySDR)

---

## Schnellstart (empfohlen)

```bash
# 1. Repository klonen
git clone https://github.com/OE3GAS/gust.git
cd gust

# 2. Skript ausführbar machen und starten
chmod +x install.sh
./install.sh
```

Das Skript:

- erkennt Plattform (Debian/Ubuntu/RPi OS) automatisch
- installiert alle apt-Systempakete
- installiert GUST via `pip install --user -e ".[dev]"`
- legt `~/.config/gust/gateway.json` interaktiv an
- installiert udev-Regeln für HackRF/SDRplay/RTL-SDR
- richtet optional einen systemd-Service ein

Nach erfolgreicher Installation:

```bash
gust devices                   # Audiogeräte auflisten
gust daemon --sim              # Daemon mit Simulator
# Browser:  http://localhost:8080
```

---

## Manuelle Installation

### Systemabhängigkeiten

```bash
sudo apt update
sudo apt install -y \
    portaudio19-dev \
    python3-dev python3-pip \
    libhamlib-dev libhamlib-utils \
    libusb-1.0-0-dev udev alsa-utils \
    build-essential git
```

> **Raspberry Pi:** zusätzlich `python3-rpi.gpio i2c-tools`.

### Python-Umgebung

GUST funktioniert sowohl im System-Python als auch in einer virtuellen
Umgebung. Auf Raspberry Pi OS Bookworm+ erzwingt PEP 668 entweder eine
venv oder `--break-system-packages`.

**Variante A — System-User-Install (empfohlen für Desktop/Server):**

```bash
python3 -m pip install --user -e ".[dev]"
```

**Variante B — Virtuelle Umgebung (sauber isoliert):**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

> **Raspberry Pi:** Variante C:
> ```bash
> python3 -m pip install --break-system-packages -e ".[rpi]"
> ```

### GUST installieren

`pip install -e .` (editable) bindet das Verzeichnis direkt ein —
Änderungen am Code wirken ohne Neuinstallation. Die wichtigsten Extras:

| Extra        | Inhalt                                            | Zielsystem       |
|:-------------|:--------------------------------------------------|:-----------------|
| `[dev]`      | pytest, pytest-asyncio, ruff                      | Entwicklung      |
| `[rpi]`      | RPi.GPIO, smbus2                                  | Raspberry Pi     |
| `[mqtt]`     | paho-mqtt                                         | Home Assistant   |
| `[meshtastic]` | meshtastic                                      | LoRa-Bridge      |

Kombination möglich: `pip install -e ".[dev,rpi]"`.

### Konfiguration (gateway.json)

```bash
mkdir -p ~/.config/gust
cp gateway.json.example ~/.config/gust/gateway.json
chmod 600 ~/.config/gust/gateway.json
${EDITOR:-nano} ~/.config/gust/gateway.json
```

Schlüsselparameter (Auszug — vollständig in `gateway.json.schema.json`):

```json
{
    "callsign": "OE3GAS",
    "audio": {
        "device": 0,
        "ptt_backend": "hamlib",
        "level": 10,
        "hamlib_host": "localhost",
        "hamlib_port": 4532
    },
    "web": { "port": 8080, "api_key": "" }
}
```

> **`"level": 10` bedeutet 10 %** (nicht 1000 %). Werte > 1 werden als
> Prozent interpretiert. Für IC-7610 mit ACC/USB Input Level = 40 % ist
> 10 % im Software-Pegel ein guter Startwert.

---

## Raspberry Pi Gateway

### Besonderheiten RPi

- **PEP 668:** Bookworm+ blockiert systemweites `pip install`.
  → `--break-system-packages` oder venv.
- **RPi.GPIO:** schneller über apt (`python3-rpi.gpio`) als über pip.
- **Audio:** RPi-Bordton ist nicht für TX geeignet. USB-Audio
  (z. B. C-Media-Klone oder direkt IC-7610-USB) verwenden.
- **Wärme:** Bei Dauerbetrieb passive Kühlung einplanen.

### GPIO-PTT Verdrahtung

Falls kein hamlib-kompatibles Funkgerät verwendet wird, kann der RPi
einen Optokoppler ansteuern (PTT-Schaltung):

```
RPi GPIO 17 (Pin 11) ──[1 kΩ]── LED-Anode (Optokoppler PC817)
RPi GND     (Pin 9 ) ──────── LED-Kathode

Optokoppler Kollektor ── PTT-Eingang Funkgerät
Optokoppler Emitter   ── Masse Funkgerät
```

In `gateway.json`:

```json
"audio": {
    "ptt_backend": "gpio",
    "gpio_pin": 17
}
```

### Als Systemdienst einrichten

`./install.sh` bietet die Option am Ende automatisch. Manuell:

```bash
# Platzhalter ersetzen und Service installieren
sed -e "s|%USER%|$USER|g" \
    -e "s|%GROUP%|$(id -gn)|g" \
    -e "s|%HOME%|$HOME|g" \
    -e "s|%EXEC%|$(which gust)|g" \
    systemd/gust.service | sudo tee /etc/systemd/system/gust.service

sudo systemctl daemon-reload
sudo systemctl enable --now gust.service
journalctl -u gust -f                # Live-Logs
```

---

## Hardware-Einrichtung

### IC-7610 (hamlib PTT)

**Funkgerät-Einstellungen:**

- USB-Buchse als CAT verbinden (z. B. `/dev/ttyUSB0`)
- Menü `Set → External Terminal → USB SEND` aktivieren
- Menü `Set → Connectors → ACC/USB AF Output Level` = 40 %
- Menü `Set → Connectors → ACC/USB MOD Level` = 40 % (TX-Pfad)

**rigctld starten:**

```bash
# IC-7610 = hamlib-Modell 3085
rigctld -m 3085 -r /dev/ttyUSB0 -s 19200
```

Test:
```bash
echo "f" | rigctl -m 3085 -r /dev/ttyUSB0 -s 19200    # Frequenz lesen
```

Als systemd-Unit (empfohlen):

```bash
sudo tee /etc/systemd/system/rigctld.service >/dev/null <<EOF
[Unit]
Description=Hamlib rigctld (IC-7610)
After=dev-ttyUSB0.device

[Service]
ExecStart=/usr/bin/rigctld -m 3085 -r /dev/ttyUSB0 -s 19200
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now rigctld
```

### HackRF One (Labortest-TX)

> **Achtung — Python-Sonderpfad:**
> HackRF-Python-Bindings (PothosSDR/SoapySDR) sind **zwingend Python 3.9**.
> GUST selbst läuft in Python 3.11+. Für HackRF-Tests wird eine **getrennte
> Umgebung** benötigt:

```bash
# PothosSDR (enthält SoapySDR + HackRF-Bindings für Python 3.9)
sudo apt install soapysdr-tools soapysdr-module-hackrf python3-soapysdr

# Test
hackrf_info                                    # ohne sudo (udev-Regel!)
SoapySDRUtil --probe="driver=hackrf"
```

Falls die Distribution kein passendes Python-3.9-Paket bereitstellt,
muss PothosSDR aus Source kompiliert oder eine separate Python-3.9-
Umgebung gebaut werden — Details siehe Upstream-Doku.

### SDRplay RSPdx2 (RX-Referenz)

```bash
# API-Treiber von sdrplay.com herunterladen (proprietär)
# → SDRplay_RSP_API-Linux-3.x.run

chmod +x SDRplay_RSP_API-Linux-3.x.run
sudo ./SDRplay_RSP_API-Linux-3.x.run

# SoapySDR-Modul
sudo apt install soapysdr-module-sdrplay3

# Test
SoapySDRUtil --probe="driver=sdrplay"
```

### Audiogeräte identifizieren

```bash
gust devices
```

Beispielausgabe:
```
ID  Name                                  In  Out  Default
─────────────────────────────────────────────────────────
 0  HDA Intel PCH: ALC256 Analog          2   2    *
 1  USB Audio CODEC                       2   2
 2  pulse                                 32  32
```

Trage die korrekte ID in `gateway.json` ein:
```json
"audio": { "device": 1 }
```

Alternative via ALSA:
```bash
aplay -l                       # Playback-Devices
arecord -l                     # Capture-Devices
```

---

## Erster Start

### Simulator-Modus (kein Hardware)

```bash
gust daemon --sim
```

Öffne im Browser: `http://localhost:8080`

Im Simulator generiert GUST alle 2 Minuten Test-Frames (Wetter,
Position, Text). Es wird **nichts gesendet** — Audio-Ausgabe ist
deaktiviert.

### Monitor-Modus (nur RX)

```bash
gust rx --device 1 -v
```

Empfängt kontinuierlich. Verbose-Modus zeigt Decoder-Versuche im Detail.

### TX-Test

```bash
# Trockenlauf — kein PTT, kein Audio
gust tx weather --temp 21.5 --dry-run

# Echt senden (PTT + Audio aktiv)
gust tx weather --temp 21.5 --device 1 --level 10
```

---

## Fehlerbehebung

### sounddevice: keine Geräte gefunden

```
PortAudioError: Error querying device
```

→ `portaudio19-dev` nicht installiert oder Treiber-Konflikt:
```bash
sudo apt install --reinstall portaudio19-dev libportaudio2
```

### hamlib: Verbindung fehlgeschlagen

```
hamlib: connect to localhost:4532 refused
```

→ `rigctld` läuft nicht. Prüfen mit:
```bash
ss -tlnp | grep 4532
sudo systemctl status rigctld
```

→ Serielles Gerät prüfen:
```bash
ls -l /dev/ttyUSB*
groups $USER | grep dialout      # muss vorhanden sein
```

### HackRF: Zugriff verweigert

```
hackrf_open() failed: HACKRF_ERROR_LIBUSB (-1000)
```

→ udev-Regel nicht aktiv oder User nicht in `plugdev`:
```bash
groups $USER | grep plugdev
sudo udevadm control --reload-rules
sudo udevadm trigger
# HackRF aus- und wieder einstecken
```

### Audio: falscher Pegel

**Symptom:** TX zu leise (kein Decode-Erfolg) oder zu laut (Splatter).

**Lösung:** Pegel in zwei Stufen einstellen — Funkgerät und Software.

| Schritt                  | Wert (IC-7610) |
|:-------------------------|:---------------|
| ACC/USB MOD Input Level  | 40 %           |
| `gateway.json` `level`   | 10 (= 10 %)    |

Mess-Hilfe: ALC-Anzeige am IC-7610 darf bei Aussendung **nicht** voll
ausschlagen. Optimal: ALC bleibt bei ~50 %.

---

## Dienst-Verwaltung (systemd)

```bash
sudo systemctl start gust          # Starten
sudo systemctl stop gust           # Stoppen
sudo systemctl restart gust        # Neustart (nach Config-Änderung)
sudo systemctl status gust         # Status
journalctl -u gust -f              # Live-Logs
journalctl -u gust --since "1h ago"
```

Config-Änderungen wirken erst nach `systemctl restart gust`.

---

## Bekannte Einschränkungen

Aus `CLAUDE.md` / `gust_backlog.md`:

- **BUG-01:** Rufzeichen > 6 Zeichen werden gekürzt (`VK2XX/P → VK2XX/`).
  Workaround: Suffix `/P`, `/M` etc. weglassen.
- **BUG-04:** RS(255,223) hat immer 32 Byte Overhead — auch für kurze Frames.
- **BUG-08:** Frame-Contention: zwei Frames in einem 8-s-Fenster können
  kollidieren, der zweite kann verloren gehen.
- **HackRF + Python 3.9** ist eine Hartanforderung der PothosSDR-Bindings —
  GUST selbst läuft mit 3.11+.

---

## Deinstallation

```bash
# systemd
sudo systemctl disable --now gust.service
sudo rm /etc/systemd/system/gust.service
sudo systemctl daemon-reload

# udev
sudo rm /etc/udev/rules.d/99-gust-sdr.rules
sudo udevadm control --reload-rules

# pip
pip uninstall gust-hf

# Konfiguration (optional — enthält Rufzeichen und ggf. API-Key)
rm -rf ~/.config/gust/
```

---

## Lizenz

GUST ist freie Software unter **CC BY-SA 4.0**.
Spezifikation, Code und Dokumentation dürfen frei verwendet, modifiziert
und weiterverbreitet werden, solange Urheber **OE3GAS** genannt und
abgeleitete Werke unter derselben Lizenz veröffentlicht werden.

Projekt-Repository: <https://github.com/OE3GAS/gust>

73 de OE3GAS
