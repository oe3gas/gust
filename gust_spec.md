# GUST — Projektspezifikation
**OE3GAS — Generic Universal Shortwave Telemetry**

> *GUST — Generic Universal Shortwave Telemetry — a terse HF broadcast protocol inspired by Olivia, optimized for sub-5-second transmissions.*
*Version 0.5 — Juni 2026*

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
- SNR-Schwelle empirisch ermittelt: ≤ 10 dB SNR (T-10.2)

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
NF-Kanal 0:      600– 850 Hz  → RF 14.110,600–14.110,850 MHz
NF-Kanal 7:     2350–2600 Hz  → RF 14.112,350–14.112,600 MHz
Gesamt-Span:     600–2600 Hz  = 2.000 Hz  → SSB-Plateau ±0,5 dB ✓
(Kanäle 0+9 alt bei 400/2650–2900 Hz entfernt — lagen im SSB-Filterrolloff)
```

### 3.2 Kanalplan

| Kanal | NF-Unterkante | NF-Oberkante | Ton 0       | Ton 7       |
|------:|:-------------:|:------------:|:-----------:|:-----------:|
| 0     | 600 Hz        | 850 Hz       | 600,00 Hz   | 818,75 Hz   |
| 1     | 850 Hz        | 1100 Hz      | 850,00 Hz   | 1068,75 Hz  |
| 2     | 1100 Hz       | 1350 Hz      | 1100,00 Hz  | 1318,75 Hz  |
| 3     | 1350 Hz       | 1600 Hz      | 1350,00 Hz  | 1568,75 Hz  |
| 4     | 1600 Hz       | 1850 Hz      | 1600,00 Hz  | 1818,75 Hz  |
| 5     | 1850 Hz       | 2100 Hz      | 1850,00 Hz  | 2068,75 Hz  |
| 6     | 2100 Hz       | 2350 Hz      | 2100,00 Hz  | 2318,75 Hz  |
| 7     | 2350 Hz       | 2600 Hz      | 2350,00 Hz  | 2568,75 Hz  |

### 3.3 Frame-Struktur

```
┌──────────┬──────────┬──────────┬──────────┬────────────────┬──────────┐
│ SYNC (8) │ TYPE (1) │  CH  (1) │ FROM (4) │ PAYLOAD (var.) │ CRC (2)  │
│  Symbole │   Byte   │   Byte   │   Byte   │  max. 20 Byte  │   Byte   │
└──────────┴──────────┴──────────┴──────────┴────────────────┴──────────┘
```

| Feld | Größe | Beschreibung |
|---|---|---|
| SYNC | 8 Symbole / 256 ms | Costas-Array [2,0,6,7,1,4,3,5] — alle 8 Töne je einmal, optimale Autokorrelation |
| TYPE | 1 Byte | Frame-Typ (siehe 3.4) |
| CH | 1 Byte | Kanal + Flags (siehe unten) |
| FROM | 4 Byte | Rufzeichen, Basis-40-kodiert (max. 6 Zeichen) |
| PAYLOAD | 1–20 Byte | Nutzdaten, typ-abhängig |
| CRC-16 | 2 Byte | CRC-16/CCITT-FALSE über TYPE+CH+FROM+PAYLOAD |

#### CH-Byte (Kanal + Flags)

```
Bit 7   : TEST-Flag  — 1 = Frame ist als Testframe gekennzeichnet
Bit 6–4 : reserviert (0x00)
Bit 3–0 : Kanal 0–7  (0x00–0x07)

Beispiel: 0x82 = Bit 7 gesetzt (TEST) + Kanal 2
```

Rückwärtskompatibilität: ältere Decoder maskieren mit `& 0x0F` und lesen
den Kanal korrekt — das TEST-Bit wird stillschweigend ignoriert.

**Gesamtrahmen (Wetter-Beispiel, gemessen):**
```
Payload encode_weather():   14 Byte
build_frame():              22 Byte  (TYPE+CH+FROM+PAYLOAD+CRC)
rs_encode():                54 Byte  (+32 Byte RS-Parität)
frame_to_symbol_stream():   60 Symbole  (8 SYNC + 52 Daten)
Audiodauer (Nutzsignal):     1,92 s
Audiodauer (mit Stille):     4,87 s  (inkl. 200 ms Pause vor/nach)
```

### 3.x Eingangsquellen

GUST unterstützt ab v0.5 zwei unabhängige Empfangspfade:

| Quelle | Modul | Hardware | Kanäle gleichzeitig |
|---|---|---|---|
| Audio (SSB) | `gust_rx.py` | IC-7610, beliebiger Transceiver | 1 (aktiver Kanal) |
| IQ-Strom | `gust_iq_rx.py` | RTL-SDR, SDRplay, HackRF RX | alle 8 parallel |

**IQ-Empfangspfad (v0.5):**
- Direkte Verbindung zum SDR, keine SSB-Demodulation
- Digitales FIR-Filterbank: 8 × 250 Hz Bandpass, ±0,1 dB Flatness
- Passband-Equalizer: aus Costas-SYNC automatisch kalibriert
- RTL-SDR konfigurierbar via `gateway.json` Block `rtlsdr`
- PPM-Kalibrierung: `rtl_test -p` → Wert in `ppm_correction` eintragen
- Mindestanforderung: RTL-SDR mit 250 kHz Sample-Rate (alle gängigen Modelle)

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

#### QSO-Modus (Frame 0x40 — Designentscheidung)

Der QSO-Modus verkürzt das Fragment-Intervall für `0x40`-Frames von
`txInterval` (Standard 300 s) auf **60 s**. Er ist ausschließlich über
den Web-Client aktivierbar (Toggle im Text-Formular) und bewusst **nicht**
in `gateway.json`, REST-API oder CLI exponiert.

**Begründung:** GUST ist primär ein Telemetrie-Protokoll. Automatische
Stationen (Baken, Gateways) sollen den QSO-Modus nicht aktivieren können,
um Kanalkapazität zu schonen (Duty Cycle ≤ 8 % bei 60 s). Der QSO-Modus
dient ausschließlich der interaktiven Ham-Kommunikation über den Web-Client.

| Modus | Intervall | 4-Fragment-Nachricht | Duty Cycle | Verwendung |
|---|---|---|---|---|
| Standard | 300 s (5 min) | ~20 min | < 2 % | Telemetrie, automatischer Betrieb |
| **QSO-Modus** | **60 s** | **~4 min** | **~8 %** | **Interaktiver Ham-Betrieb (Web-UI)** |

Der QSO-Modus bleibt bis zur manuellen Deaktivierung aktiv
(kein automatisches Zurückschalten nach der Nachricht).

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

#### Frame 0x20 — Notfall-Beacon (20 Byte)
```
Offset  Größe  Inhalt
  0       4    Latitude (int32, Mikrograd)
  4       4    Longitude (int32, Mikrograd)
  8       1    Personenanzahl (uint8)
  9       1    Verletzungscode (uint8: 0=unbekannt, 1=leicht, 2=schwer, 3=kritisch)
 10       1    Ressourcenflags (bit0=Wasser, bit1=Nahrung, bit2=Medizin, bit3=Evak.)
 11       1    Prioritätsstufe (uint8: 0=niedrig, 1=mittel, 2=hoch, 3=sofort)
 12       8    Freitext-Snippet (8 Byte ASCII)

struct-Format: '>iiBBBB8s'
```

#### Frame 0x40 — Freitext / QSO (variabel, max. 20 Byte)
```
Offset  Größe  Inhalt
  0       4    TO-Rufzeichen (Basis-40, oder BROADCAST=0xFFFFFFFF)
  4       1    Sequenznummer (uint8)
  5       1    Fragment-Info (Bits 7-4: Fragment-Index, Bits 3-0: Gesamt-1)
  6      ≤14   UTF-8-Text
```

**Fragmentation:**
- Maximale Fragmente pro Nachricht: **4** (Protokoll-Limit)
- Maximale Textlänge: **56 Byte UTF-8** (= 56 ASCII-Zeichen)
- Kapazität nach Fragmentanzahl:

| Fragmente | Textlänge | Sendedauer (Standard) | Sendedauer (QSO-Modus) |
|:---------:|:---------:|:---------------------:|:----------------------:|
| 1         | 1–14 Byte | ~5 min                | ~1 min                 |
| 2         | 15–28 Byte| ~10 min               | ~2 min                 |
| 3         | 29–42 Byte| ~15 min               | ~3 min                 |
| 4         | 43–56 Byte| ~20 min (Maximum)     | ~4 min                 |

**Web-UI-Verhalten:**
- Byte-Counter zeigt laufend: verwendete Bytes / 56, Frame-Anzahl, verbleibende Bytes
- Bei >1 Frame: Bestätigungsdialog mit Frame-Anzahl und Zeitschätzung (schedule-basiert)
- **Schedule-getaktetes Senden:** Das Web-UI fragmentiert mehrteilige Texte selbst
  (byte-korrekt in 14-Byte-Chunks, gemeinsame Sequenznummer) und sendet **ein Fragment
  pro Schedule-Slot** über `POST /api/tx/text_fragment` — nicht alle back-to-back.
  Nach jedem `tx_done` wird bis zum nächsten Slot gewartet, dann das nächste Fragment.
- **RX-Reassembly:** Eingehende Fragmente werden im Browser anhand `Rufzeichen:Seq-Nr`
  gesammelt und bei Vollständigkeit als zusammengesetzte Klartextzeile angezeigt
  (mit `[n/n Frg. ✓]`); unvollständige Nachrichten zeigen den ausstehenden Rest.
- **Kein serverseitiges Hard-Limit (Stand v0.3):** Das 56-Byte-/4-Frame-Limit wird
  **ausschließlich clientseitig** durchgesetzt. Siehe BUG-09.

### 3.6 Rufzeichen-Kodierung (Basis-40)

40 gültige Zeichen (` 0-9 A-Z / . - +`) → 4 Byte für bis zu **6 Zeichen**.
Rufzeichen > 6 Zeichen werden auf 6 Zeichen gekürzt (bekannte Einschränkung, siehe Knowledge Base).

### 3.7 Kanalzuweisung (deterministisch, ohne Koordination)

```python
import hashlib

def assign_channel(callsign, n_channels=8, interval=300):
    h = int(hashlib.sha256(callsign.upper().encode()).hexdigest(), 16)
    channel     = h % n_channels       # Heimatkanal (0–7)
    time_offset = (h >> 8) % interval  # Zeitversatz (s)
    return channel, time_offset

# OE3GAS → Kanal 2, Versatz 220 s
```

### 3.8 Kanalkapazität und Timing

#### Frame-Dauer

Die Länge eines GUST-Frames hängt vom Payload-Typ ab:

| Frame-Typ | Payload | Symbole (RS+SYNC) | Audiodauer |
|---|---|---|---|
| CQ (0x41) | 5 Byte | 44 | ~1,4 s |
| STATION_TLM (0x03) | 10 Byte | 48 | ~1,5 s |
| WEATHER (0x01) | 14 Byte | 60 | ~1,9 s |
| POSITION (0x02) | 18 Byte | 64 | ~2,1 s |
| EMERG_RSRC (0x21) | 8 Byte | 52 | ~1,7 s |
| EMERG_BEACON (0x20) | 20 Byte | 68 | ~2,2 s |
| TEXT/0x40 (1 Fragment) | ≤ 20 Byte | 68 | ~2,2 s |

Mit 200 ms Stille vor/nach dem Nutzsignal (PTT-Vorlauf/-Nachlauf):
**typische Gesamtdauer 1,8–2,6 s** je Frame.

Der Decoder benötigt mindestens die vollständige Frame-Dauer plus das
Scan-Intervall im Empfangspuffer (Vollfenster-Garantie, `gust_rx.py`):
```
WINDOW_S ≥ MAX_FRAME_S + SCAN_INTERVAL_S
9,0 s    ≥ 5,5 s       + 2,0 s            (Reserve: 1,5 s)
```

#### Duty Cycle pro Kanal

```
Duty Cycle = Frame-Dauer / TX-Intervall

Normalbetrieb (300 s):  2,5 s / 300 s  ≈  0,8 %
QSO-Modus    ( 60 s):   2,5 s /  60 s  ≈  4,2 %
```

Der QSO-Modus ist auf ≤ 8 % Duty Cycle begrenzt (4-Fragment-Nachricht,
60-s-Intervall: 4 × 2,5 s / 60 s ≈ 16,7 % — deshalb gilt QSO-Modus
nur für interaktiven Betrieb über den Web-Client, nicht für Baken).

#### Mindestabstand zwischen Frames (empirisch)

Aus Stresstest-Messungen (gust_stresstest.py + gust_stress_decode.py,
Juni 2026) ergibt sich: Der Decoder benötigt einen Mindestabstand von
**≥ 11 s** zwischen zwei aufeinanderfolgenden Frames auf demselben Kanal,
damit keine zeitlichen Kollisionen im FFT-Decoder-Fenster entstehen.

```
Mindestabstand = 2 × MAX_FRAME_S ≈ 2 × 5,5 s = 11 s
```

Im Normalbetrieb (300 s Intervall) liegt der tatsächliche Abstand bei
~300 s — weit oberhalb dieser Grenze. Relevant wird der Mindestabstand
nur bei manuell ausgelösten Frames (One-Shot TX via Web-UI) und im
QSO-Modus.

#### Kanalkapazität (Pure ALOHA, deterministisches Scheduling)

GUST verwendet kein CSMA (kein Horchen vor dem Senden). Der Hash-Schedule
(`assign_channel()`) verteilt Stationen deterministisch über Zeit und
Kanäle. Die theoretische Kapazität ohne Kollisionen:

```
Kapazität pro Kanal  =  TX-Intervall / Mindestabstand
                     =  300 s / 11 s  ≈  27 Stationen

Gesamtkapazität (8 Kanäle)  =  27 × 8  =  216 Stationen
```

Diese Zahl gilt für den Normalfall (kein manueller TX, alle Stationen
mit gleichem `interval_s = 300`). In der Praxis ist die Kapazität
deutlich höher, weil die Hash-Funktion Stationen auch zeitlich verteilt
(nicht alle 27 Stationen pro Kanal senden zum selben Zeitpunkt).

**Einordnung:** Für GUST-typische Szenarien (regionale Notfallnetze,
Katastrophenschutz) ist diese Kapazität sehr komfortabel. Relevant wird
sie erst bei öffentlich genutzten Gateways mit vielen simultanen Stationen.

| Szenario | Stationen | Bewertung |
|---|---|---|
| Lokales Netz (1 Gemeinde) | 5–20 | unkritisch |
| Regionalnetz (Bundesland) | 20–80 | unkritisch |
| Nationales Netz | 80–216 | im sicheren Bereich |
| Überregional (> 216) | > 216 | Kollisionswahrscheinlichkeit steigt |

---

## 4. Systemarchitektur

### 4.1 Übersicht

GUST besteht aus einem **einzigen Daemon-Prozess** mit eingebettetem Web-Server. Der Web-Browser dient als GUI — lokal oder remote über LAN/VPN.

```
┌─────────────────────────────────────────────────────────────┐
│                    GUST Daemon                              │
│                                                             │
│  TX-Stack           Interner          RX-Stack             │
│  ─────────          Event-Bus         ───────────          │
│  Quellen ──→        asyncio           Audio-In             │
│  Queue   ──→  pub ──────────── sub ←─ Demodulator          │
│  Modulator          Fan-out           Decoder              │
│  Audio-TX                             Output-Router        │
│                        │                                    │
│              ┌─────────┴──────────┐                        │
│              │  Eingebetteter     │                         │
│              │  Web-Server        │                         │
│              │  aiohttp :8080     │                         │
│              │  REST   /api/…     │                         │
│              │  WS     /ws/rx     │                         │
│              │  WS     /ws/log    │                         │
│              └────────────────────┘                        │
└─────────────────────────────────────────────────────────────┘
                         │  LAN / VPN
                    ┌────┴────┐
                    │ Browser │  lokal oder remote
                    └─────────┘
```

### 4.2 Betriebsmodi

**Monitor-Modus** (`gust rx`):
- Dauerlauf, beobachtet alle 8 Kanäle gleichzeitig
- FFT-basierter Channel-Scan mit 4096-Punkte Zero-Padding
- Dekodierte Frames → Event-Bus → WebSocket → Browser

**Sender-Modus** (`gust tx <typ>`):
- One-Shot: einmal senden, beenden
- Prioritäts-Queue (Prio 1–4) mit Kanal-Cooldown (10 s Standard)

**Daemon-Modus** (`gust daemon`):
- TX + RX gleichzeitig, Vollbetrieb
- Alle Quellen aktiv (BME280, MQTT, Meshtastic, File)
- Web-Server läuft für GUI-Zugriff

**CLI-Startoptionen:**

| Aufruf | Modus | Beschreibung |
|---|---|---|
| `gust.py daemon` | `DAEMON` | TX + RX Vollbetrieb, echte Hardware |
| `gust.py daemon --sim` | `DAEMON · SIM` | SimAdapter erzeugt synthetische Frames |
| `gust.py daemon --dry-run` | `DAEMON · DRY-RUN` | TX-Pipeline aktiv, kein Audio/PTT |
| `gust.py daemon --sim --dry-run` | `DAEMON · SIM · DRY-RUN` | Reine Software-Simulation |
| `gust.py rx` | `RX-MONITOR` | Nur Empfang + Web-UI |

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
| GET | `/api/status` | Daemon-Status, Queue-Statistik, `active_trx_profile`, `trx_profile_count` |
| GET | `/api/config` | Aktuelle Konfiguration (ohne Passwörter) |
| PATCH | `/api/config` | Partielles Konfig-Update (z.B. `audio.ptt_delay_ms`), schreibt `gateway.json` |
| POST | `/api/tx/weather` | One-Shot Wetter-Frame |
| POST | `/api/tx/position` | One-Shot Positions-Frame |
| POST | `/api/tx/text` | Text-Frame (serverseitig fragmentiert, back-to-back) |
| POST | `/api/tx/text_fragment` | Einzelnes vorberechnetes Text-Fragment (Schedule-getaktet vom Web-UI) |
| POST | `/api/tx/emergency` | Notfall-Frame (sofort, Prio 1) |
| POST | `/api/tx/tune` | 15-s-Sinuston (1000 Hz) mit PTT — für Antennenabgleich |
| POST | `/api/tx/tune_stop` | Tune sofort abbrechen, PTT lösen |
| GET | `/api/log` | Letzte N Einträge aus dem Logfile |
| DELETE | `/api/tx/queue` | Alle ausstehenden TX-Frames löschen — gibt `{cleared: N}` zurück. Frames die gerade gesendet werden bleiben unberührt. |
| POST | `/api/trx/activate` | TRX-Profil aktivieren — Body: `{"name": "IC-7610"}`. Schreibt rigctld/audio-Block, stößt conflict-aware rigctld-Neustart an. |
| GET | `/api/hamlib/ports` | Verfügbare serielle Ports (pyserial `list_ports`) |
| GET | `/api/hamlib/models` | Hamlib-Rig-Liste (`?q=`Suchstring, max. 50 Treffer) |
| GET | `/api/hamlib/status` | rigctld TCP-Erreichbarkeit + aktuelle Frequenz |
| POST | `/api/hamlib/start` | rigctld starten (`ensure_rigctld_running`) |
| POST | `/api/hamlib/stop` | rigctld stoppen (nur wenn GUST ihn gestartet hat) |
| POST | `/api/hamlib/config` | rigctld-Block + `ptt_backend` in `gateway.json` schreiben |
| POST | `/api/hamlib/force_restart` | rigctld-Prozess (PID) beenden und neu starten |
| GET | `/api/sdr/devices` | SoapySDR-Geräte enumerieren (enumerate + Rescan) |
| GET | `/api/sdr/caps` | SoapySDR-Geräteeigenschaften (Gain, Sample-Rate, Antennen) |
| POST | `/api/sdr/config` | SoapySDR-TX-Konfiguration in `gateway.json` schreiben |

**WebSocket-Endpunkte:**

| Pfad | Inhalt | Format |
|---|---|---|
| `/ws/rx` | Dekodierte RX-Frames, tx_done, heartbeat (Echtzeit) | JSON-Objekt |
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

### 4.5 TRX-Profile (gateway.json)

GUST unterstützt ab Juni 2026 mehrere Transceiver-Profile in `gateway.json`.
Das aktive Profil wird über das Web-UI ausgewählt und in `active_trx_profile` gespeichert.

```json
"trx_profiles": [
  {
    "name":            "IC-7610",
    "rig_model":       3078,
    "device":          "COM11",
    "baud":            19200,
    "audio_device_tx": 14,
    "audio_device_rx": 2,
    "ptt_backend":     "hamlib",
    "auto_start":      true
  },
  {
    "name":            "FT-818",
    "rig_model":       1045,
    "device":          "COM7",
    "baud":            38400,
    "audio_device_tx": 10,
    "audio_device_rx": 5,
    "ptt_backend":     "hamlib",
    "auto_start":      true
  }
],
"active_trx_profile": "IC-7610"
```

**Designentscheidungen:**
- `audio_device_tx` → `audio.device` (TX-Soundkarte)
- `audio_device_rx` → `rx.device` (RX-Soundkarte)
- Profil-Anlage manuell in `gateway.json`; erstes Profil wird beim Hamlib-Config-Speichern automatisch angelegt
- Rückwärtskompatibel: fehlt `trx_profiles`, arbeitet GUST wie bisher mit dem einzelnen `rigctld`-Block
- Profilwechsel stößt conflict-aware rigctld-Neustart an (gemeinsamer Flow mit Hamlib-Config-Tab)
- Status-Poll nach Neustart: 2 s Delay (`_testHamlibDelayed`) — gibt rigctld Zeit zum Hochfahren

### 4.6 Wetter-Adapter-Bibliothek

Alle Adapter implementieren `WeatherAdapterBase` und liefern ein einheitliches Dict:

| Adapter | Quelle | Beschreibung |
|---|---|---|
| `SimAdapter` | intern | Simulierte Werte mit Tagesgang (immer verfügbar) |
| `FileAdapter` | CSV/JSON-Datei | Polling einer Datei, flexibles Format |
| `Rtl433Adapter` | rtl_433 JSON-Stream | 433-MHz-Sensoren via RTL-SDR-Dongle |
| `WeeWxAdapter` | WeeWx SQLite-DB | Häufigste RPi-Wetterstation |
| `MqttWeatherAdapter` | MQTT-Topic | WiFi-Wetterstationen mit MQTT-Output |

### 4.7 MQTT-Bridge (optional)

Die MQTT-Bridge ist ein optionaler Event-Bus-Subscriber/-Publisher. Nur aktiv wenn `mqtt.enabled: true`.

**Output-Topics (RX → MQTT):**
```
gust/rx/weather    → dekodierte Wetter-Frames
gust/rx/position   → dekodierte Positions-Frames
gust/rx/text       → dekodierte Text-Frames
gust/status        → Daemon-Status (60 s Intervall)
```

---

## 5. Implementierung

### 5.1 Software-Dateien (aktueller Stand)

| Datei | Inhalt | Phase | Status |
|---|---|---|---|
| `gust_frame.py` | Frame Layer: alle Encoder/Decoder, CRC, Rufzeichen, Kanal | 1+2 | ✅ fertig |
| `gust_modulator.py` | MFSK-8 Modulator/Demodulator, TX/RX Pipeline, WAV/CF32 | 1 | ✅ fertig |
| `gust_decode.py` | Standalone Decoder, Channel-Scan CLI | 2 | ✅ fertig |
| `gust_audio.py` | Audio TX, PTT-Steuerung (GPIO, hamlib, null); `create_ptt()` gibt Popen-Handle zurück | 3 | ✅ fertig |
| `gust_hackrf.py` | HackRF TX-Pfad | 3 | ✅ fertig |
| `gust_soapy_tx.py` | Generischer SoapySDR-TX-Backend (ADR-16) | 7 | ✅ fertig |
| `gust_sources.py` | Datenquellen-Adapter (BME280, MQTT, Meshtastic) | 4 | ✅ fertig |
| `gust_gateway.py` | Sendeplaner, Prioritäts-Queue, Aggregator; `clear_queue()` | 4 | ✅ fertig |
| `gust_weather.py` | Wetter-Adapter-Bibliothek (Sim, File, rtl_433, WeeWx) | 5 | ✅ fertig |
| `gust_eventbus.py` | Interner asyncio Fan-out Event-Bus | 5 | ✅ fertig |
| `gust_iq_rx.py` | IQ-Empfangspfad, RTL-SDR Filterbank, asyncio IQReceiver | 9 | ✅ fertig |
| `gust_web.py` | aiohttp Web-Server, REST API, WebSocket, Static UI; TRX-Profile, QSO-Modus, Queue-Clear | 5 | ✅ fertig |
| `gust_mqtt.py` | Optionale MQTT-Bridge (Subscriber + Publisher) | 6 | 🔲 geplant |
| `gust.py` | CLI-Einstiegspunkt; 80-Zeichen-Banner, ANSI-Logging, `_mode_badges` | 5 | ✅ fertig |
| `gust_tx_test.py` | TX-Testharness HackRF/IC-7610; Epilog mit Beispielaufrufen + Hinweis kein gleichzeitiger daemon | 7 | ✅ fertig |
| `gateway.json` | Konfiguration inkl. `trx_profiles` + `active_trx_profile` | 4 | ✅ fertig |
| `requirements.txt` | Python-Abhängigkeiten (PC + RPi); `psutil` ergänzt | 4 | ✅ fertig |

### 5.2 CLI-Interface

```
gust <subcommand> [optionen]

Subcommands:
  tx weather   Wetter-Frame senden (one-shot)
  tx position  Positions-Frame senden
  tx text      Freitext senden
  tx emergency Notfall-Frame (sofort)
  rx           Monitor-Modus (alle 8 Kanäle, Dauerlauf)
  daemon       Vollbetrieb (TX + RX + Web-Server)
  info         Kanalinfo für Rufzeichen anzeigen
  devices      Verfügbare Audio-Geräte listen

Globale Optionen:
  --callsign OE3GAS     Rufzeichen
  --config gateway.json Konfigurationsdatei
  --dry-run             Kein TX, kein Audio/PTT
  --sim                 SimAdapter als Datenquelle
  --verbose / -v        Debug-Logging
```

### 5.3 CLI-Logging (gust.py)

Farbiges ANSI-Logging via `GustFormatter` + `_GustStreamHandler`:

| Log-Kategorie | Farbe | Label | Verhalten |
|---|---|---|---|
| `INFO` allgemein | grün | `INFO` | scrollend |
| TX-Gateway-Event | gelb | `TX ▶` | scrollend, kompakt |
| RX-Frame-Event | blau | `RX ◀` | scrollend, kompakt |
| RX-Heartbeat (periodisch) | grau | `▸` | **überschreibend** (`\r`) |
| `WARNING` | orange | `WARNING` | scrollend |
| `ERROR` | rot | `ERROR` | scrollend |
| `aiohttp.access` | — | — | **unterdrückt** (WARNING-Level) |

`_GustStreamHandler.emit()` prüft nach dem Formatieren ob der Formatter einen leeren String geliefert hat — falls ja, wird nichts ausgegeben (kein Leerzeilen-Artefakt nach `\r`-Statuszeile).

### 5.4 PTT-Steuerung (implementiert in gust_audio.py)

```python
# Drei Backends, konfigurierbar:
ptt_backend: "null"    # Simulation / Dry-Run
ptt_backend: "gpio"    # Raspberry Pi GPIO-Pin
ptt_backend: "hamlib"  # rigctld (IC-7610, FT-818, TS-790, ...)
```

`create_ptt()` merkt den von `ensure_rigctld_running()` gestarteten `subprocess.Popen`-Handle
an `ptt._rigctld_proc`. `cmd_daemon()` übergibt diesen Handle nach `gateway.start()` an
`server._rigctld_proc`, damit `_handle_hamlib_config()` eigene rigctld-Prozesse erkennt
und keinen Konflikt-Dialog zeigt (ADR-19).

### 5.5 Sicherheit (Web-Server)

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
GUST:        Backbone      (Gateway ↔ Gateway, kontinental, HF)

[MeshCom Wien] ←──LoRa──→ [HF-Gateway OE3GAS] ←──HF──→ [HF-Gateway remote]
```

### 6.2 Sendeprioritäten

```
Priorität 1 (sofort):       Frame-Typ 0x20/0x21  — Notfall
Priorität 2 (≤30 s):        Frame-Typ 0x40       — Text / QSO
Priorität 3 (nächster       Frame-Typ 0x02       — Position
             TX-Schedule):
Priorität 4 (zyklisch):     Frame-Typ 0x01/0x03  — Telemetrie
```

Notfall-Frames (Prio 1) überspringen den Kanal-Cooldown. Alle anderen warten min. `min_tx_gap_s` (Standard: 10 s).

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
| Freitext / QSO (Standard) | 0x40 | 300 s / auf Anfrage | fix (Hash) |
| Freitext / QSO (QSO-Modus) | 0x40 | **60 s** (Web-UI only) | fix (Hash) |

---

## 8. Geplante Testfrequenzen

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

### VHF/UHF — Zusatztests

| Band | Frequenz | Anmerkung |
|------|----------|-----------|
| 2 m | 144,900 MHz | Digitales/Datensegment OE |
| 70 cm | 432,200 MHz | Digitales/Datensegment OE |

> **§16 AFG ✅ geklärt:** GUST-Aussendungen sind als Datenübertragung
> im digitalen Sub-Band lizenzkonform. Bandbreite (250 Hz/Kanal), Betriebsart
> und die dokumentierten Testfrequenzen entsprechen dem ÖVSV-Bandplan und §16 AFG.

---

## 9. Implementierungsplan

```
Phase 1 — Modulator/Demodulator  ✅ ABGESCHLOSSEN
Phase 2 — Frame-Layer  ✅ ABGESCHLOSSEN
Phase 3 — Hardware-Integration  ✅ ABGESCHLOSSEN
Phase 4 — Quellen-Integration  ✅ ABGESCHLOSSEN
Phase 5 — Web-Interface & Event-Bus  ✅ ABGESCHLOSSEN
  ✅ QSO-Modus (60 s Fragment-Intervall, Web-UI only) — Juni 2026
  ✅ TX-Warteschlange löschen (DELETE /api/tx/queue) — Juni 2026
  ✅ TRX-Profile Mehrgeräteverwaltung (trx_profiles, POST /api/trx/activate) — Juni 2026
  ✅ CLI-Logging: ANSI-Farben, TX ▶ / RX ◀, überschreibende Statuszeile — Juni 2026
  ✅ Banner 80 Zeichen, Aufrufe-Übersicht, korrekte Modus-Label — Juni 2026

Phase 6 — MQTT-Bridge  (optional, zurückgestellt)
  🔲 gust_mqtt.py: MQTTBridge als Event-Bus-Subscriber/-Publisher

Phase 7 — On-Air-Tests und Decoder-Robustheit  ✅ WEITGEHEND ABGESCHLOSSEN
  ✅ SNR-Baseline: Decode-Schwelle ≤ 10 dB SNR (T-10.2)
  ✅ Erster On-Air-Test auf 14.110 MHz
  🔲 Kollisionstests mit zweiter Station (T-10.3 ausgearbeitet)
  🔲 MeshCom End-to-End Test

Phase 8 — Veröffentlichung  ← AKTUELL
  ✅ GitHub Repository (OE3GAS/gust) — committed + gepusht Juni 2026
  🔲 README.md für GitHub
  🔲 LICENSE (CC BY-SA 4.0)
  🔲 Installationsanleitung Raspberry Pi Gateway
  🔲 Präsentation ÖVSV / OE-Amateurfunk-Community

Phase 9 — Protokoll v0.5  ✅ ABGESCHLOSSEN
  ✅ 8 Kanäle 600–2600 Hz (ADR-14)
  ✅ Costas-Array SYNC [2,0,6,7,1,4,3,5]
  ✅ IQ-Eingang gust_iq_rx.py
```

---

## 10. Verfügbare Hardware

| Gerät | Rolle im Projekt |
|---|---|
| **ICOM IC-7610** | Primärer HF-Transceiver, TX/RX, TRX-Profil COM11 |
| **Yaesu FT-818** | Portabler TRX, TRX-Profil COM7 |
| **Kenwood TS-790** | VHF/UHF-TRX, TRX-Profil COM10 |
| **SDRplay RSPdx2** | Referenzempfang, Parallelbeobachtung |
| **HackRF One** | Labortest-TX, Loopback-Tests, SNR-Baseline |
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
aiohttp          — Phase 5, implementiert ✅
psutil           — BUG-10 Fix, Port-Konflikt-Erkennung ✅
RPi.GPIO         — nur RPi, Phase 3 ✅
smbus2           — nur RPi, Phase 4 ✅
meshtastic       — Phase 4, optional
```

### Offene technische Fragen

| Frage | Priorität | Phase |
|---|---|---|
| Demodulator: Python vs. GNU Radio OOT | niedrig | 7 |
| Soapy7610 TX-Pfad IC-7610 direktes IQ-TX | mittel | 7 |
| ~~Bandplankonformität OE (KW-Segment)~~ ✅ | hoch | geklärt Mai 2026 |
| ~~Lizenzrechtlich: Telemetrie §16 AFG~~ ✅ | hoch | geklärt Mai 2026 |
| ~~SNR-Schwelle GUST vs. Olivia~~ ✅ | hoch | geklärt (≤ 10 dB SNR, T-10.2) |
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

*Dokument: gust_spec.md*
*Autor: OE3GAS*
*Stand: Juni 2026 — v0.5 · QSO-Modus · TRX-Profile · CLI-Logging · BUG-10/11/12 · 3 TRX-Profile (IC-7610, FT-818, TS-790)*
*Lizenz: CC BY-SA 4.0 (geplant für Veröffentlichung)*