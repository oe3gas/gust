# GUST — Projektspezifikation
**OE3GAS — Generic Universal Shortwave Telemetry**

> *GUST — Generic Universal Shortwave Telemetry — a terse HF broadcast protocol inspired by Olivia, optimized for sub-5-second transmissions.*
*Version 0.4 — 17. Mai 2026*

---

## 1. Projektziel

GUST ist ein offenes, binäres Amateurfunk-Digitalprotokoll für die robuste Übertragung von Telemetriedaten und Kurznachrichten auf Kurzwelle. Es dient als **HF-Backbone** zwischen lokalen Datenquellen (Wetterstationen, LoRa/Meshtastic-Meshes, Sensorik) und entfernten Empfangsstationen — auch ohne Internetinfrastruktur.

**Leitprinzipien:**
- Vollständig offene Spezifikation (kein proprietäres Element)
- Binäres, kompaktes Datenformat — keine ASCII-Ineffizienz
- Robustheit auf KW-Kanälen als primäres Designziel
- Raspberry Pi als Referenz-Zielplattform (Gateway-Betrieb)
- Kompatibilität mit bestehender SDR-Hardware (IC-7610, HackRF, SDRplay)
- Web-Browser als GUI — lokal und remote (LAN/VPN) erreichbar

---

## 2. Technische Grundlage: Olivia als Ausgangspunkt

GUST baut auf der bewährten **Olivia-Modulationsarchitektur** auf und ersetzt deren FEC-Schicht durch ein für Binärdaten effizienteres Verfahren.

### 2.1 Olivia — Was übernommen wird

Olivia (Pawel Jalocha SP9VRC, ca. 2003) verwendet MFSK mit orthogonalen Tönen und Walsh-Hadamard-FEC. Von dieser Architektur übernimmt GUST:

| Parameter | Olivia 8/250 | GUST |
|---|---|---|
| Modulation | MFSK-8 | MFSK-8 (identisch) |
| Bandbreite | 250 Hz | 250 Hz |
| Tonabstand (Δf) | 31,25 Hz | 31,25 Hz |
| Symboldauer (T) | 32 ms | 32 ms |
| Bits/Symbol | 3 (log₂8) | 3 (log₂8) |
| Rohbitrate | 93,75 bit/s | 93,75 bit/s |

Die Töne sind **orthogonal** (Δf = 1/T), was optimale Spektraleffizienz ohne Inter-Symbol-Interferenz sichert.

### 2.2 Was verändert wird: FEC und Datenformat

**GUST-FEC (Reed-Solomon RS(255,223)):**
- Byteorientiert — passt nativ zu Binärdaten
- Overhead: 32 Byte pro Frame (shortened code via reedsolo)
- Korrigiert bis zu 16 Byte-Fehler pro Block
- SNR-Schwelle ähnlich Olivia (~−12 bis −14 dB, durch HF-Tests zu bestimmen)

**Nettobitrate mit RS-FEC:**
```
93,75 bit/s × (223/255) ≈ 82 bit/s netto
```

**Datenformat:** Binäres TLV (Type-Length-Value) statt ASCII. Faktor 5–6× kompakter als Olivia-Freitext für strukturierte Telemetriedaten.

---

## 3. Protokollspezifikation

### 3.1 Frequenzarchitektur (FT8-Prinzip)

Alle Stationen stellen **dieselbe Dial-Frequenz** ein. Die NF-Tonhöhe des MFSK-Signals bestimmt den Kanal.

```
Dial-Frequenz:  z.B. 14.110,000 MHz (USB)
NF-Kanal 0:     400– 650 Hz  → RF 14.110,400–14.110,650 MHz
NF-Kanal 9:    2650–2900 Hz  → RF 14.112,650–14.112,900 MHz
Gesamt-Span:    400–2900 Hz  = 2.500 Hz  → Standard-SSB-Passband ✓
```

### 3.2 Kanalplan

| Kanal | NF-Unterkante | NF-Oberkante | Ton 0      | Ton 7       |
|------:|:-------------:|:------------:|:----------:|:-----------:|
| 0     | 400 Hz        | 650 Hz       | 400,00 Hz  | 618,75 Hz   |
| 1     | 650 Hz        | 900 Hz       | 650,00 Hz  | 868,75 Hz   |
| 2     | 900 Hz        | 1150 Hz      | 900,00 Hz  | 1118,75 Hz  |
| 3     | 1150 Hz       | 1400 Hz      | 1150,00 Hz | 1368,75 Hz  |
| 4     | 1400 Hz       | 1650 Hz      | 1400,00 Hz | 1618,75 Hz  |
| 5     | 1650 Hz       | 1900 Hz      | 1650,00 Hz | 1868,75 Hz  |
| 6     | 1900 Hz       | 2150 Hz      | 1900,00 Hz | 2118,75 Hz  |
| 7     | 2150 Hz       | 2400 Hz      | 2150,00 Hz | 2368,75 Hz  |
| 8     | 2400 Hz       | 2650 Hz      | 2400,00 Hz | 2618,75 Hz  |
| 9     | 2650 Hz       | 2900 Hz      | 2650,00 Hz | 2868,75 Hz  |

### 3.3 Frame-Struktur

```
┌──────────┬──────────┬──────────┬──────────┬────────────────┬──────────┐
│ SYNC (4) │ TYPE (1) │  CH  (1) │ FROM (4) │ PAYLOAD (var.) │ CRC (2)  │
│  Symbole │   Byte   │   Byte   │   Byte   │  max. 20 Byte  │   Byte   │
└──────────┴──────────┴──────────┴──────────┴────────────────┴──────────┘
```

| Feld | Größe | Beschreibung |
|---|---|---|
| SYNC | 4 Symbole / 128 ms | Tonfolge [7,0,7,0] — alternierend höchster/niedrigster Ton |
| TYPE | 1 Byte | Frame-Typ (siehe 3.4) |
| CH | 1 Byte | Kanal + Flags (siehe unten) |
| FROM | 4 Byte | Rufzeichen, Basis-40-kodiert (max. 6 Zeichen) |
| PAYLOAD | 1–20 Byte | Nutzdaten, typ-abhängig |
| CRC-16 | 2 Byte | CRC-16/CCITT-FALSE über TYPE+CH+FROM+PAYLOAD |

#### CH-Byte (Kanal + Flags)

```
Bit 7   : TEST-Flag  — 1 = Frame ist als Testframe gekennzeichnet
Bit 6–4 : reserviert (0x00)
Bit 3–0 : Kanal 0–9  (0x00–0x09)

Beispiel: 0x82 = Bit 7 gesetzt (TEST) + Kanal 2
```

Rückwärtskompatibilität: ältere Decoder maskieren mit `& 0x0F` und lesen
den Kanal korrekt — das TEST-Bit wird stillschweigend ignoriert.

**Gesamtrahmen (Wetter-Beispiel, gemessen):**
```
Payload encode_weather():   14 Byte
build_frame():              22 Byte  (TYPE+CH+FROM+PAYLOAD+CRC)
rs_encode():                54 Byte  (+32 Byte RS-Parität)
frame_to_symbol_stream():   60 Symbole  (4 SYNC + 56 Daten)
Audiodauer (Nutzsignal):     1,92 s
Audiodauer (mit Stille):     4,87 s  (inkl. 200 ms Pause vor/nach)
```

### 3.4 Frame-Typen

```
0x01  Wetter-Telemetrie
0x02  Position (APRS-kompatibel)
0x03  Stations-Telemetrie
0x10  Rotor-Status
0x11  Rotor-Steuerbefehl
0x20  Notfall-Beacon
0x21  Notfall-Ressourcenstatus
0x30  Generische Messung (Sensor-TLV)
0x40  Freitext / QSO-Fragment
0x41  QSO-CQ / Anruf
0xF0  Protokoll-Management (Kanalzuweisung, Timing)
0xFF  Erweiterungsreserviert
```

### 3.5 Payload-Definitionen

#### Frame 0x01 — Wetter (14 Byte)
```
Offset  Größe  Inhalt
  0       2    Temperatur (int16, Einheit 0,1°C)
  2       1    Luftfeuchte (uint8, %)
  3       1    [Padding]
  4       2    Luftdruck (uint16, Einheit 0,1 hPa)
  6       1    Windgeschwindigkeit (uint8, km/h)
  7       1    [Padding]
  8       2    Windrichtung (uint16, Grad 0–359)
 10       2    Niederschlag (uint16, Einheit 0,1 mm/h)
 12       1    UV-Index (uint8)
 13       1    Statusflags (bit0=Batterie OK, bit1=Sensor OK)

struct-Format: '>hBxHBxHHBB'
```

#### Frame 0x02 — Position (18 Byte)
```
Offset  Größe  Inhalt
  0       4    Latitude (int32, Mikrograd)
  4       4    Longitude (int32, Mikrograd)
  8       2    Altitude (int16, Meter)
 10       1    Speed (uint8, km/h)
 11       1    [Padding]
 12       2    Heading (uint16, Grad)
 14       2    Timestamp (uint16, Modulo 65536 s)
 16       2    Statusflags (bit0=mobil, bit1=GPS-Fix, bit2=Notfall)

struct-Format: '>iihBxHHH'
```

#### Frame 0x20 — Notfall-Beacon (16 Byte)
```
Offset  Größe  Inhalt
  0       4    Latitude (int32, Mikrograd)
  4       4    Longitude (int32, Mikrograd)
  8       1    Personenanzahl (uint8)
  9       1    Verletzungscode (uint8: 0=unbekannt, 1=leicht, 2=schwer, 3=kritisch)
 10       1    Ressourcenflags (bit0=Wasser, bit1=Nahrung, bit2=Medizin, bit3=Evak.)
 11       1    Prioritätsstufe (uint8: 0=niedrig, 1=mittel, 2=hoch, 3=sofort)
 12       4    Freitext-Snippet (4 Byte ASCII)

struct-Format: '>iiBBBB4s'
```

#### Frame 0x40 — Freitext / QSO (variabel, max. 20 Byte)
```
Offset  Größe  Inhalt
  0       4    TO-Rufzeichen (Basis-40, oder BROADCAST=0xFFFFFFFF)
  4       1    Sequenznummer (uint8)
  5       1    Fragment-Info (Bits 7-4: Fragment-Index, Bits 3-0: Gesamt-1)
  6      ≤14   UTF-8-Text
```

### 3.6 Rufzeichen-Kodierung (Basis-40)

40 gültige Zeichen (` 0-9 A-Z / . - +`) → 4 Byte für bis zu **6 Zeichen**.
Rufzeichen > 6 Zeichen werden auf 6 Zeichen gekürzt (bekannte Einschränkung, siehe Knowledge Base).

### 3.7 Kanalzuweisung (deterministisch, ohne Koordination)

```python
import hashlib

def assign_channel(callsign, n_channels=10, interval=300):
    h = int(hashlib.sha256(callsign.upper().encode()).hexdigest(), 16)
    channel     = h % n_channels       # Heimatkanal (0–9)
    time_offset = (h >> 8) % interval  # Zeitversatz (s)
    return channel, time_offset

# OE3GAS → Kanal 2, Versatz 220 s
```

---

## 4. Systemarchitektur

### 4.1 Übersicht

GUST besteht aus einem **einzigen Daemon-Prozess** mit eingebettetem Web-Server. Der Web-Browser dient als GUI — lokal oder remote über LAN/VPN.

```
┌─────────────────────────────────────────────────────────────┐
│                    GUST Daemon                            │
│                                                              │
│  TX-Stack           Interner          RX-Stack              │
│  ─────────          Event-Bus         ───────────           │
│  Quellen ──→        asyncio           Audio-In              │
│  Queue   ──→  pub ──────────── sub ←─ Demodulator           │
│  Modulator          Fan-out           Decoder               │
│  Audio-TX                             Output-Router         │
│                        │                                     │
│              ┌─────────┴──────────┐                         │
│              │  Eingebetteter     │                          │
│              │  Web-Server        │                          │
│              │  aiohttp :8080     │                          │
│              │                    │                          │
│              │  REST   /api/…     │                          │
│              │  WS     /ws/rx     │                          │
│              │  WS     /ws/log    │                          │
│              │  Static /          │                          │
│              └────────────────────┘                         │
└─────────────────────────────────────────────────────────────┘
                         │  LAN / VPN
                    ┌────┴────┐
                    │ Browser │  lokal oder remote
                    └─────────┘
```

### 4.2 Betriebsmodi

**Monitor-Modus** (`gust rx`):
- Dauerlauf, beobachtet alle 10 Kanäle gleichzeitig
- FFT-basierter Channel-Scan mit 4096-Punkte Zero-Padding
- Dekodierte Frames → Event-Bus → WebSocket → Browser
- Dekodierte Frames → JSON-Lines Logfile (rotierend)
- Optional: MQTT-Publish (konfigurierbar)

**Sender-Modus** (`gust tx <typ>`):
- One-Shot: einmal senden, beenden
- Daemon: periodisch senden aus konfigurierten Quellen
- Prioritäts-Queue (Prio 1–4) mit Kanal-Cooldown (10 s Standard)

**Daemon-Modus** (`gust daemon`):
- TX + RX gleichzeitig, Vollbetrieb
- Alle Quellen aktiv (BME280, MQTT, Meshtastic, File)
- Web-Server läuft für GUI-Zugriff

### 4.3 Interner Event-Bus

Der Event-Bus entkoppelt alle Komponenten. Jeder Subscriber bekommt eine eigene asyncio.Queue (Fan-out).

```python
class EventBus:
    def subscribe(self) -> asyncio.Queue:  # eigene Queue pro Subscriber
        ...
    async def publish(self, event: dict) -> None:
        # an alle Subscriber gleichzeitig, non-blocking
        ...
```

**Publisher:** TX-Quellen, RX-Decoder
**Subscriber:** WebSocket-Handler, TX-Scheduler, MQTT-Bridge (optional), Logfile-Writer

### 4.4 Web-API

**REST-Endpunkte:**

| Methode | Pfad | Funktion |
|---|---|---|
| GET | `/` | Web-UI (HTML/JS/CSS, eingebettet) |
| GET | `/api/status` | Daemon-Status, Queue-Statistik |
| GET | `/api/config` | Aktuelle Konfiguration (ohne Passwörter) |
| POST | `/api/config` | Konfiguration aktualisieren |
| POST | `/api/tx/weather` | One-Shot Wetter-Frame |
| POST | `/api/tx/position` | One-Shot Positions-Frame |
| POST | `/api/tx/text` | One-Shot Text-Frame |
| POST | `/api/tx/emergency` | Notfall-Frame (sofort, Prio 1) |
| GET | `/api/log` | Letzte N Einträge aus dem Logfile |

**WebSocket-Endpunkte:**

| Pfad | Inhalt | Format |
|---|---|---|
| `/ws/rx` | Dekodierte RX-Frames (Echtzeit) | JSON-Objekt |
| `/ws/log` | Systemlog-Einträge (Echtzeit) | JSON-Objekt |

**RX-Frame JSON-Format:**
```json
{
  "ts":      "2026-05-17T14:23:11Z",
  "channel": 2,
  "from":    "OE3GAT",
  "type":    "weather",
  "snr_db":  14.2,
  "data": {
    "temp_c": 18.5,
    "humidity_pct": 72,
    "pressure_hpa": 1018.3
  }
}
```

### 4.5 Wetter-Adapter-Bibliothek

Alle Adapter implementieren `WeatherAdapterBase` und liefern ein einheitliches Dict:

| Adapter | Quelle | Beschreibung |
|---|---|---|
| `SimAdapter` | intern | Simulierte Werte mit Tagesgang (immer verfügbar) |
| `FileAdapter` | CSV/JSON-Datei | Polling einer Datei, flexibles Format |
| `Rtl433Adapter` | rtl_433 JSON-Stream | 433-MHz-Sensoren via RTL-SDR-Dongle |
| `WeeWxAdapter` | WeeWx SQLite-DB | Häufigste RPi-Wetterstation |
| `MqttWeatherAdapter` | MQTT-Topic | WiFi-Wetterstationen mit MQTT-Output |

Konfiguration in `gateway.json`:
```json
"weather_source": {
  "adapter": "sim",
  "file_path": "/var/lib/weewx/weewx.sdb",
  "rtl433_cmd": "rtl_433 -F json"
}
```

### 4.6 MQTT-Bridge (optional)

Die MQTT-Bridge ist ein optionaler Event-Bus-Subscriber/-Publisher. Sie wird nur instanziiert wenn `mqtt.enabled: true` in der Konfiguration. Der Rest des Systems ist davon vollständig unabhängig.

**Output-Topics (RX → MQTT):**
```
gust/rx/weather    → dekodierte Wetter-Frames
gust/rx/position   → dekodierte Positions-Frames
gust/rx/text       → dekodierte Text-Frames
gust/status        → Daemon-Status (60 s Intervall)
```

**Input-Topics (MQTT → TX):**
```
gust/tx/weather    → Wetter-Frame senden
gust/tx/text       → Text-Frame senden
gust/tx/position   → Positions-Frame senden
```

---

## 5. Implementierung

### 5.1 Software-Dateien (aktueller Stand)

| Datei | Inhalt | Phase | Status |
|---|---|---|---|
| `gust_frame.py` | Frame Layer: alle Encoder/Decoder, CRC, Rufzeichen, Kanal | 1+2 | ✅ fertig |
| `gust_modulator.py` | MFSK-8 Modulator/Demodulator, TX/RX Pipeline, WAV/CF32 | 1 | ✅ fertig |
| `gust_decode.py` | Standalone Decoder, Channel-Scan CLI | 2 | ✅ fertig |
| `gust_audio.py` | Audio TX, PTT-Steuerung (GPIO, hamlib, null) | 3 | ✅ fertig |
| `gust_hackrf.py` | HackRF TX-Pfad | 3 | ✅ fertig |
| `gust_sources.py` | Datenquellen-Adapter (BME280, MQTT, Meshtastic) | 4 | ✅ fertig |
| `gust_gateway.py` | Sendeplaner, Prioritäts-Queue, Aggregator | 4 | ✅ fertig |
| `gust_weather.py` | Wetter-Adapter-Bibliothek (Sim, File, rtl_433, WeeWx) | 5 | 🔲 geplant |
| `gust_eventbus.py` | Interner asyncio Fan-out Event-Bus | 5 | 🔲 geplant |
| `gust_web.py` | aiohttp Web-Server, REST API, WebSocket, Static UI | 5 | 🔲 geplant |
| `gust_mqtt.py` | Optionale MQTT-Bridge (Subscriber + Publisher) | 6 | 🔲 geplant |
| `gust.py` | Einheitlicher CLI-Einstiegspunkt | 5 | 🔲 geplant |
| `gateway.json` | Konfigurationsvorlage | 4 | ✅ fertig |
| `requirements.txt` | Python-Abhängigkeiten (PC + RPi) | 4 | ✅ fertig |

### 5.2 CLI-Interface (geplant)

```
gust <subcommand> [optionen]

Subcommands:
  tx weather   Wetter-Frame senden (one-shot oder daemon)
  tx position  Positions-Frame senden
  tx text      Freitext senden
  tx emergency Notfall-Frame (sofort)
  rx           Monitor-Modus (alle 10 Kanäle, Dauerlauf)
  daemon       Vollbetrieb (TX + RX + Web-Server)
  info         Kanalinfo für Rufzeichen anzeigen
  devices      Verfügbare Audio-Geräte listen

Globale Optionen:
  --callsign OE3GAS     Rufzeichen
  --config gateway.json Konfigurationsdatei
  --dry-run             Kein TX, WAV speichern
  --verbose / -v        Debug-Logging
```

### 5.3 Technologie-Stack

| Schicht | Technologie | Begründung |
|---|---|---|
| Async-Runtime | `asyncio` (stdlib) | Durchgängig, kein Threading-Mix |
| Web-Server | `aiohttp` | asyncio-nativ, WS + REST + Static in einem |
| MQTT | `paho-mqtt` | Bewährt, Thread-sicher, optional |
| Audio | `sounddevice` | Plattformübergreifend |
| PTT | `RPi.GPIO` / `hamlib` | RPi-nativ / TRX-agnostisch |
| FEC | `reedsolo` | Reed-Solomon, getestet ✅ |
| DSP | `numpy`, `scipy` | FFT, Hilbert, Modulation |
| Sensor | `smbus2` | I²C BME280, nur RPi |

### 5.4 PTT-Steuerung (implementiert in gust_audio.py)

```python
# Drei Backends, konfigurierbar:
ptt_backend: "null"    # Simulation / Dry-Run
ptt_backend: "gpio"    # Raspberry Pi GPIO-Pin
ptt_backend: "hamlib"  # IC-7610, rigctld
```

### 5.5 Sicherheit (Web-Server)

Für Betrieb im Privatnetz + VPN reicht ein einfacher API-Key im HTTP-Header. HTTPS über den VPN-Tunnel. Kein volles Auth-Framework nötig.

```json
"web": {
  "host":    "0.0.0.0",
  "port":    8080,
  "api_key": "oe3gas-secret"
}
```

---

## 6. Quellen-Integration und Priorisierung

### 6.1 MeshCom / Meshtastic Gateway

```
Meshtastic:  letzte Meile  (Node ↔ Node, 5–50 km, LoRa 868 MHz)
GUST:     Backbone      (Gateway ↔ Gateway, kontinental, HF)

[MeshCom Wien] ←──LoRa──→ [HF-Gateway OE3GAS] ←──HF──→ [HF-Gateway remote]
```

### 6.2 Sendeprioritäten

```
Priorität 1 (sofort):       Frame-Typ 0x20/0x21  — Notfall
Priorität 2 (≤30 s):        Frame-Typ 0x40       — Text / QSO
Priorität 3 (nächster       Frame-Typ 0x02       — Position
             Sendezyklus):
Priorität 4 (zyklisch):     Frame-Typ 0x01/0x03  — Telemetrie
```

Notfall-Frames (Prio 1) überspringen den Kanal-Cooldown. Alle anderen warten min. `min_tx_gap_s` (Standard: 10 s) nach dem letzten TX auf demselben Kanal.

---

## 7. Anwendungsfälle

| Anwendungsfall | Frame-Typ | Sendeintervall | Kanal |
|---|---|---|---|
| Wetterstation (Berg/SOTA) | 0x01 | 5–10 min | fix (Hash) |
| Positionsbake (mobil) | 0x02 | 5 min | fix (Hash) |
| MeshCom-HF-Gateway | 0x02 / 0x40 | bedarfsgesteuert | fix (Hash) |
| Notfunk-Beacon | 0x20 | 1 min | fix (Hash) |
| Remote-Stationsmonitoring | 0x03 | 10 min | fix (Hash) |
| Rotorsteuerung | 0x10 / 0x11 | auf Anfrage | fix (Hash) |
| Freitext / QSO | 0x40 | auf Anfrage | fix (Hash) |

---

## 8. Geplante Testfrequenzen

Testbetrieb mit ausgewählten Gegenstationen ist für die folgenden KW-Segmente
geplant. Die Segmente entsprechen den digitalen Sub-Bändern des IARU-Region-1-
Bandplans und decken sich mit dem ÖVSV-Bandplan für OE.

| Band | Segment | MHz-Schreibweise | Anmerkung |
|------|---------|-----------------|-----------|
| 630 m | 475–479 kHz | 0,475–0,479 | Schmalband-Digital, QRP |
| 160 m | 1838–1840 kHz | 1,838–1,840 | Digitales Sub-Band |
| 80 m | 3570–3600 kHz | 3,570–3,600 | Digitales Sub-Band |
| 40 m | 7040–7050 kHz | 7,040–7,050 | Digitales Sub-Band |
| 30 m | 10130–10150 kHz | 10,130–10,150 | WARC, digital |
| 20 m | 14110–14125 kHz | **14,110–14,125** | **Aktuell im Einsatz** (Dial 14,110 MHz) |
| 17 m | 18105–18109 kHz | 18,105–18,109 | WARC, digital |
| 15 m | 21090–21110 kHz | 21,090–21,110 | Digitales Sub-Band |
| 10 m | 28120–28150 kHz | 28,120–28,150 | Digitales Sub-Band |

Die Dial-Frequenz wird je Band so gewählt, dass die NF-Kanäle 0–9
(400–2900 Hz) vollständig innerhalb des jeweiligen Segments liegen.
Beispiel 20 m: Dial 14,110 MHz → RF 14,1104–14,1129 MHz ∈ [14,110–14,125] ✓

### VHF/UHF — Zusatztests

GUST wurde ursprünglich für KW entwickelt. Für ergänzende Tests unter
UKW-Ausbreitungsbedingungen (Tropo, lokale Reichweite, NVIS-Vergleich)
werden zusätzlich folgende Segmente herangezogen:

| Band | Frequenz | Anmerkung |
|------|----------|-----------|
| 2 m | 144,900 MHz | Digitales/Datensegment OE |
| 70 cm | 432,200 MHz | Digitales/Datensegment OE |

> **§16 AFG ✅ geklärt:** GUST-Aussendungen sind als Datenübertragung
> im digitalen Sub-Band lizenzkonform. Bandbreite (250 Hz/Kanal), Betriebsart
> und die dokumentierten Testfrequenzen (gust_spec.md §8) entsprechen
> dem ÖVSV-Bandplan und §16 des österreichischen Amateurfunkgesetzes.

---

## 9. Implementierungsplan

```
Phase 1 — Modulator/Demodulator  ✅ ABGESCHLOSSEN
  ✅ Python MFSK-8 Modulator (phasenkontinuierlich)
  ✅ WAV-Ausgabe → Verifikation in Audacity
  ✅ CF32-Ausgabe → Verifikation in inspectrum
  ✅ Reed-Solomon Encoder/Decoder (reedsolo, 5 Fehler korrigiert)
  ✅ FFT Demodulator mit Zero-Padding (4096 Punkte)

Phase 2 — Frame-Layer  ✅ ABGESCHLOSSEN
  ✅ TLV-Encoder für alle Frame-Typen (0x01–0x41)
  ✅ CRC-16/CCITT-FALSE
  ✅ Rufzeichen Basis-40 Kodierung
  ✅ Kanalzuweisung per SHA-256-Hash
  ✅ Vollständiger Loopback-Test

Phase 3 — Hardware-Integration  ✅ ABGESCHLOSSEN
  ✅ Audio-Ausgabe über USB-Soundkarte (sounddevice)
  ✅ PTT-Steuerung GPIO / hamlib / null
  ✅ HackRF TX-Pfad (gust_hackrf.py)

Phase 4 — Quellen-Integration  ✅ ABGESCHLOSSEN
  ✅ Wetter-Adapter BME280 (I²C + Simulation-Fallback)
  ✅ Meshtastic/MeshCom Gateway-Adapter
  ✅ MQTT-Input für WiFi-Quellen
  ✅ Prioritäts-Queue und Sendeplaner (gust_gateway.py)

Phase 5 — Web-Interface & Event-Bus  ✅ ABGESCHLOSSEN
  ✅ gust_eventbus.py: asyncio Fan-out Event-Bus, TTL-Filterung
  ✅ gust_weather.py:  Wetter-Adapter (Simulation + File-Fallback)
  ✅ gust_web.py:      aiohttp Server, REST API, WebSocket (/ws/rx, /ws/log)
  ✅ Web-UI: HTML + Vanilla JS, eingebettet in gust_web.py
      ✅ Monitor-Ansicht: alle 10 Kanäle, Echtzeit-Frames via WebSocket
      ✅ One-Shot TX: Formular für alle Frame-Typen
      ✅ Status-Dashboard: Queue, Uptime, letzte TX/RX
      ✅ Dark / Light Theme
      ✅ API-Key-Authentifizierung (X-API-Key + Bearer)
  ✅ gust.py: CLI-Einstiegspunkt (tx/rx/daemon/info/devices)

Phase 6 — MQTT-Bridge  (optional, zurückgestellt)
  🔲 gust_mqtt.py: MQTTBridge als Event-Bus-Subscriber/-Publisher
  🔲 Home Assistant / Node-RED Integration testen
  🔲 Dokumentation MQTT-Topic-Schema

Phase 7 — On-Air-Tests und Decoder-Robustheit  ✅ WEITGEHEND ABGESCHLOSSEN
  ✅ Protokoll v0.3: 8-Symbol-SYNC + CHANNEL-Byte im Frame-Header
  ✅ TEST-Flag (Bit 7 im CH-Byte): rückwärtskompatible Testframe-Kennzeichnung
  ✅ Symbol-Windowing (Raised Cosine) — sauberes Spektrum verifiziert
  ✅ Breitband-SYNC-Detektor: automatische Kanal- + Offseterkennung
  ✅ Decoder-Robustheit: Frequenz-Fein-Refinement (< 1 Hz nach Kalibrierung)
  ✅ Decoder-Robustheit: Scan-Range 320–2760 Hz (alle 10 Kanäle erfasst)
  ✅ Decoder-Robustheit: Halb-Block-Timing (128-Sample-Raster + Sample-genaues Refinement)
  ✅ Kontinuierlicher RX-Loop: gust_rx.py, asyncio, Dedup-Cache, EventBus
  ✅ HackRF Dual-Kanal-TX + Parallelkanal-Diversity (Diversity-Gewinn bestätigt)
  ✅ SNR-Baseline HackRF→IC-7610: Decode-Schwelle ≤ 10 dB SNR ermittelt
  ✅ SNR-Schätzer: adaptives Rauschband (BUG-06, alle Kanäle konsistent)
  ✅ Vollfenster-Garantie: Fixed-Cadence-Scheduling, 100 % Simplex-Rate (BUG-07)
  ✅ Erster On-Air-Test auf 14.110 MHz (IC-7610 TX → IC-7610/SDRplay RX)
  ✅ Bandplan §16 AFG: lizenzkonform geklärt — Datenübertragung im digitalen Sub-Band
  🔲 Soapy7610 TX-Pfad IC-7610 direktes IQ-TX
  🔲 Kollisionstests mit zweiter Station (OE1XTU oder OE3GAT)
  🔲 MeshCom End-to-End Test (LoRa → GUST-Gateway → HF → LoRa)

Phase 8 — Veröffentlichung  ← AKTUELL
  ✅ Protokoll umbenennen: OE3Mode → GUST (Generic Universal Shortwave Telemetry)
  ✅ TEST-Flag in Protokollspezifikation dokumentiert (CH-Byte Bit 7)
  ✅ Alle Module auf gust_*.py umgestellt, Imports konsistent
  🔲 README.md für GitHub (Beschreibung, Quickstart, Protokollüberblick)
  🔲 LICENSE (CC BY-SA 4.0)
  🔲 .gitignore + Repository-Struktur
  ✅ Bandplan §16 AFG: lizenzkonform geklärt
  🔲 Installationsanleitung Raspberry Pi Gateway
  🔲 Veröffentlichung auf GitHub (OE3GAS/gust)
  🔲 Präsentation ÖVSV / OE-Amateurfunk-Community
```

---

## 10. Verfügbare Hardware

| Gerät | Rolle im Projekt |
|---|---|
| **ICOM IC-7610** | Primärer HF-Transceiver, TX/RX, IQ via Soapy7610 |
| **SDRplay RSPdx2** | Referenzempfang, Parallelbeobachtung |
| **HackRF One** | Labortest-TX, Loopback-Tests |
| **AirSpy R2** | Empfang der HackRF-Testsignale |
| **RF-Space SDR-IQ** | Schmalbandanalyse, Referenz |
| **Raspberry Pi** | Gateway-Zielplattform (ab Beta-Phase) |
| **Arduino** | Hilfsmittel (PTT, Sensor-Interface) |

---

## 11. Abhängigkeiten und offene Fragen

### Python-Bibliotheken

```
numpy, scipy     — installiert, getestet ✅
reedsolo         — installiert, getestet ✅
sounddevice      — Phase 3, implementiert ✅
paho-mqtt        — Phase 4, implementiert ✅
aiohttp          — Phase 5, neu hinzugefügt
RPi.GPIO         — nur RPi, Phase 3 ✅
smbus2           — nur RPi, Phase 4 ✅
meshtastic       — Phase 4, optional
```

### Offene technische Fragen

| Frage | Priorität | Phase |
|---|---|---|
| SNR-Schwelle GUST vs. Olivia | hoch | 7 |
| Symbol-Windowing (Raised Cosine) | mittel | 7 |
| Optimale Preamble-Länge für reale KW-Bedingungen | mittel | 7 |
| Demodulator: Python vs. GNU Radio OOT | niedrig | 7 |
| Soapy7610 TX-Pfad IC-7610 | hoch | 7 |
| ~~Bandplankonformität OE (KW-Segment)~~ ✅ | hoch | geklärt Mai 2026 |
| ~~Lizenzrechtlich: Telemetrie §16 AFG~~ ✅ | hoch | geklärt Mai 2026 |
| Rufzeichen > 6 Zeichen (Suffix /P etc.) | niedrig | 8 |
| RS-FEC Optimierung für sehr kurze Frames | niedrig | 8 |

---

## 12. Verwandte Projekte / Referenzen

- Olivia-Spezifikation: Pawel Jalocha SP9VRC
- Fldigi Quellcode: github.com/w1hkj/fldigi
- Soapy7610: bestehende Entwicklung OE3GAS
- MeshCom: meshcom.oevsv.at (ÖVSV)
- Meshtastic: meshtastic.org
- rtl_433: github.com/merbanan/rtl_433
- WeeWx: weewx.com
- aiohttp: docs.aiohttp.org

---

## 13. Querverbindungen zu laufenden Lernprojekten

| Lernprojekt | Relevanz für GUST |
|---|---|
| Oszilloskop (Projekt A) | NF-Signal vor Soundkarte analysieren, HF-Envelope beim TX prüfen |
| inspectrum | Modulationsverifikation, Tonabstand messen ✅ |
| GNU Radio | MFSK-Demodulator-Flowgraph, Soapy7610-Integration |
| URH | Protokoll-Debugging, Frame-Struktur verifizieren |
| Soapy7610 | IC-7610 als Referenz-RX, TX-Entwicklungsziel Phase 7 |

---

*Dokument: gust_projektspezifikation.md*
*Autor: OE3GAS*
*Stand: 17. Mai 2026 — lebendes Dokument, wird laufend aktualisiert*
*Lizenz: CC BY-SA 4.0 (geplant für Veröffentlichung)*