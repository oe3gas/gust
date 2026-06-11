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
0x50  AUTH — HMAC-SHA256 Frame-Authentifizierung (GUST-S, siehe 3.5)
0x80–0xCF  GUST-X Frame-Typen (Extended, 9-Symbol-SYNC erforderlich, siehe 3.9)
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

#### Frame 0x50 — AUTH (HMAC-SHA256, 20 Byte)

Bilaterale Authentifizierung eines zuvor gesendeten Daten-Frames für
geschlossene Gruppen mit gemeinsamem Schlüssel (Gegenstück zum öffentlich
verifizierbaren AUTH_EX 0x85/0x86 in §3.9). Der AUTH-Frame folgt dem
Daten-Frame und referenziert ihn über dessen TIMESTAMP.
```
Offset  Größe  Inhalt
  0       4    TIMESTAMP  Unix-Timestamp des Daten-Frames (uint32, big-endian)
  4       1    REF_TYPE   Frame-Typ des Daten-Frames (z.B. 0x01 für WEATHER)
  5       1    KEY_ID     Schlüssel-Identifier (welcher gemeinsame Schlüssel)
  6      14    HMAC       HMAC-SHA256(key, frame_body + TIMESTAMP), 14 Byte
```
Gesamt: 20 Byte — füllt die GUST-S-Payload exakt aus.

**HMAC-Berechnung und -Prüfung:**
```python
import hashlib, hmac, struct

def auth_tag(frame_body: bytes, timestamp: int, key: bytes) -> bytes:
    """HMAC-SHA256 über Frame-Body + TIMESTAMP, truncated auf 14 Byte."""
    import struct
    msg = frame_body + struct.pack(">I", timestamp)
    return hmac.new(key, msg, hashlib.sha256).digest()[:14]

def verify_auth(frame_body: bytes, timestamp: int,
                tag: bytes, key: bytes, max_age_s: int = 60) -> bool:
    import time
    if abs(time.time() - timestamp) > max_age_s:
        return False   # Replay-Schutz (TIMESTAMP zu alt)
    return hmac.compare_digest(tag, auth_tag(frame_body, timestamp, key))
```

**Replay-Schutz:** Der TIMESTAMP im AUTH-Frame dient gleichzeitig als
Referenz und als Replay-Schutz. Der Empfänger prüft:
  `abs(time.time() - TIMESTAMP) <= 60 s`
Frames älter als 60 s werden abgewiesen, auch wenn der HMAC stimmt.
TX und RX müssen auf GPS/NTP synchronisiert sein (Toleranz: ±30 s).

Puffer-Schlüssel am Empfänger: Rufzeichen + REF_TYPE
Der Empfänger puffert den letzten Frame jedes Typs pro Station
(60-s-Fenster). Für TEXT-Fragmente: der zuletzt empfangene Daten-Frame.
Siehe gust_knowledge.md §28.

**Sicherheitsniveau:** ~128 Bit gegen Fälschung (HMAC-16). Nur der
Schlüsselpartner kann verifizieren — bewusst, für geschlossene Gruppen.

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

### 3.9 GUST-X — Erweiterte Protokollvariante

> **Status:** Geplant (P8-12, 🔲) — noch nicht implementiert. Voraussetzung für
> vollen FEC-Gewinn ist der Soft-Output-Demodulator (P8-13). Siehe ADR-37.

#### Überblick

GUST existiert in zwei wählbaren Varianten:

| Variante | SYNC | FEC | Max. Payload | Sendedauer | Timestamp |
|---|---|---|---|---|---|
| **GUST-S** (Slim) | 8 Symbole | RS(255,223) | 20 Byte | ≤ 5 s | optional |
| **GUST-X** (Extended) | 9 Symbole | LDPC n=256 | 44 Byte | ≤ 7,5 s | **Pflicht** |

GUST-S bleibt der Standard für alle bestehenden Stationen und
Anwendungen. GUST-X ist eine opt-in Erweiterung — eine Station
wählt pro Aussendung welche Variante sie verwendet. Beide Varianten
können im selben Frequenzband gleichzeitig aktiv sein.

#### Variantenerkennung: das 9. SYNC-Symbol

GUST-S und GUST-X verwenden denselben Costas-Array-SYNC:
```
Costas-Basis: [2, 0, 6, 7, 1, 4, 3, 5]   (8 Symbole = 256 ms)
```

GUST-X hängt ein **neuntes Symbol** an den SYNC (total 9 Symbole = 288 ms):
```
GUST-S SYNC: [2, 0, 6, 7, 1, 4, 3, 5]          (8 Symbole)
GUST-X SYNC: [2, 0, 6, 7, 1, 4, 3, 5, V]        (9 Symbole)
             wobei V = Variantensymbol (1–6)
```

Das Variantensymbol V kodiert die GUST-X-Untervariante:

| V | Bedeutung |
|---|---|
| 0 | reserviert (würde SYNC mehrdeutig machen) |
| **1** | **GUST-X v1 (LDPC n=256, Payload 44 Byte, Timestamp Pflicht)** |
| 2–6 | reserviert für künftige Varianten |
| 7 | reserviert (würde SYNC mehrdeutig machen) |

Das 9-Symbol-Muster ist für einen GUST-S Decoder unsichtbar — er
sucht nur das 8-Symbol-Muster und findet GUST-X Frames nicht.
Ein GUST-X Decoder erkennt beide Varianten: 8-Symbol = GUST-S,
9-Symbol = GUST-X.

**Vorteil gegenüber Mode-Bit im Frame-Header:**
Das Variantensymbol steht vor der FEC — kein Henne-Ei-Problem.
Der Decoder weiß bevor er FEC anwendet welches Verfahren
(RS oder LDPC) und welche Payload-Länge erwartet wird.

#### GUST-X Frame-Struktur

```
[SYNC 9 Symbole] [LDPC-kodierte Daten]
     288 ms           variable

Frame-Body (vor LDPC):
  TYPE     (1 Byte)   0x80–0xCF für GUST-X-spezifische Typen
                      0x01–0x4F weiterhin gültig (kompatible Typen)
  CHANNEL  (1 Byte)   wie GUST-S (Kanal 0–7, Flags)
  FROM     (4 Byte)   Rufzeichen Base-40 kodiert
  TIMESTAMP (4 Byte)  Unix-Timestamp, 32 Bit, Sekunden  ← PFLICHTFELD
  PAYLOAD  (var)      bis 44 Byte
  CRC      (2 Byte)   CRC-16 über TYPE..PAYLOAD

  Pflichtfelder (Overhead) = TYPE 1 + CHANNEL 1 + FROM 4 + TIMESTAMP 4 + CRC 2
                           = 12 Byte
```

**Timestamp als Pflichtfeld:**
Jeder GUST-X Frame trägt einen GPS/NTP-synchronisierten Zeitstempel.
Damit ist der Messzeitpunkt eindeutig dokumentiert — unabhängig davon
wann der Frame empfangen wurde, über wieviele Relais er lief oder
wie lange er in einem Puffer lag. In zeitkritischen Anwendungen
(Notfunk, Umweltmonitoring, Ionosphären-Messung) ist das ein
fundamentaler Mehrwert gegenüber impliziter Empfangszeit.

#### GUST-X Frame-Typen (0x80–0xCF)

```
0x81  WEATHER_EX     — Erweitertes Wetter + Position kombiniert (32 Byte)
0x82  EMERG_EX       — Erweiterter Notfall-Beacon mit Freitext    (44 Byte)
0x83  SENSOR_EX      — Erweiterte Sensor-TLV, 5–6 Kanäle          (40 Byte)
0x84  POSITION_EX    — Position mit Track (3 Punkte + Heading)     (28 Byte)
0x85  AUTH_EX        — ECDSA P-256, Signatur-Hälfte r  (2-Frame, siehe unten)
0x86  AUTH_EX_B      — ECDSA P-256, Signatur-Hälfte s  (2. Frame zu 0x85)
0x87  RELAY          — Mesh-Relay-Header + Original-Frame-Referenz (20 Byte)
```

#### AUTH_EX (0x85 + 0x86) — ECDSA P-256, öffentlich verifizierbar

AUTH_EX ist die asymmetrische Ergänzung zum HMAC-basierten AUTH-Frame
(0x50). Der entscheidende Unterschied:

| Frame | Signatur | Verifizierbar durch | Schlüsselaustausch |
|---|---|---|---|
| 0x50 AUTH | HMAC-16 | nur Schlüsselpartner | bilateral, außerhalb GUST |
| 0x85+0x86 AUTH_EX | ECDSA-64 (P-256, voll) | **jeden** | öffentlicher Schlüssel auf QRZ.com |

**Warum zwei Frames?**
Eine vollständige, verifizierbare ECDSA-P-256-Signatur ist r + s = 64 Byte
und passt nicht in ein 44-Byte-GUST-X-Payload. Eine *gekürzte* Signatur ist
prinzipiell **nicht** verifizierbar — die Verifikation benötigt r und s
vollständig. Daher wird die Signatur über zwei Frames übertragen:

- **0x85 AUTH_EX**   → r (32 Byte, vollständig)
- **0x86 AUTH_EX_B** → s (32 Byte, vollständig)

Beide Frames tragen denselben TIMESTAMP / KEY_ID und werden vom
Empfänger im 60-s-Fenster zusammengeführt; danach erfolgt eine normale
ECDSA-Verifikation.

**Anwendungsfall:**
Ein Gateway der noch nie Kontakt mit OE1XRK (Rotes Kreuz) hatte,
empfängt einen EMERG_BEACON mit AUTH_EX. Er lädt den öffentlichen
Schlüssel von OE1XRK von QRZ.com oder dem GUST-Key-Register und
verifiziert: dieser Notruf kommt wirklich von OE1XRK.

**Payload-Layout je Frame (44 Byte, identischer Header):**
```
Offset  Länge  Inhalt
──────────────────────────────────────────────────────
  0       4    TIMESTAMP  Unix-Timestamp des Daten-Frames (uint32)
  4       1    REF_TYPE   Frame-Typ des Daten-Frames
  5       1    KEY_ID     Schlüssel-Identifier (welcher Public Key)
  6      32    SIG_HALF   0x85: r[0:32]  |  0x86: s[0:32]  (jeweils vollständig)
 38       6    reserviert
──────────────────────────────────────────────────────
Gesamt: 44 Byte Payload je Frame
```

**Sicherheitsniveau:** ~128 Bit (ECDSA P-256, vollständige Signatur).

**Signatur erzeugen und prüfen (Standard-ECDSA, voll verifizierbar):**
```python
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.utils import (
    encode_dss_signature, decode_dss_signature)
import struct, time

def sign_frame_ecdsa(frame_body: bytes, timestamp: int,
                     private_key) -> tuple:
    """
    Signiert einen Daten-Frame mit ECDSA P-256.
    Gibt (r_bytes, s_bytes) zurück — je 32 Byte, vollständig.
    r_bytes → AUTH_EX (0x85),  s_bytes → AUTH_EX_B (0x86).
    """
    msg = frame_body + struct.pack(">I", timestamp)
    sig_der = private_key.sign(msg, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(sig_der)
    return r.to_bytes(32, "big"), s.to_bytes(32, "big")

def verify_frame_ecdsa(frame_body: bytes, r_bytes: bytes, s_bytes: bytes,
                       timestamp: int, public_key,
                       max_age_s: int = 60) -> bool:
    """
    Verifiziert AUTH_EX (0x85) + AUTH_EX_B (0x86) zusammen.
    Standard-ECDSA P-256 — voll verifizierbar, kein Brute-Force.
    public_key: ec.EllipticCurvePublicKey (P-256, z.B. von QRZ.com)
    """
    if abs(time.time() - timestamp) > max_age_s:
        return False   # Replay-Schutz
    r = int.from_bytes(r_bytes, "big")
    s = int.from_bytes(s_bytes, "big")
    sig_der = encode_dss_signature(r, s)
    msg = frame_body + struct.pack(">I", timestamp)
    try:
        public_key.verify(sig_der, msg, ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        return False
```

**Schlüsselverwaltung:**
```python
# Einmalig: Schlüsselpaar erzeugen
from cryptography.hazmat.primitives.asymmetric import ec
private_key = ec.generate_private_key(ec.SECP256R1())
public_key  = private_key.public_key()
# public_key auf QRZ.com publizieren (PEM-Format)
```
```json
"auth_ex": {
    "private_key_pem": "/etc/gust/ecdsa_private.pem",
    "comment": "Nie ins Repo! In .gitignore."
}
```

Bestehende GUST-S Typen (0x01–0x4F) sind auch in GUST-X gültig —
eine GUST-X Station kann kurze Frames (z.B. CQ 0x41) ohne den
GUST-X Overhead senden, indem sie den 8-Symbol-SYNC verwendet.

#### Payload-Kapazität GUST-X

```
Sendedauer-Budget:        ≤ 7,5 s
Symbole im Budget:        (7500 − 288 SYNC) / 32 ≈ 225 Symbole
Codiert (3 bit/Symbol):   225 × 3 / 8 ≈ 84 Byte
Daten (LDPC Rate 3/4):    84 × 0,75 ≈ 63 Byte
Theoretische Max-Payload: 63 − 12 (Pflichtfelder) ≈ 51 Byte

GUST-X v1 setzt die Payload bewusst auf 44 Byte (Reserve im Budget):
  44 B Payload + 12 B Pflichtfelder = 56 B Frame-Body
  56 / 0,75 ≈ 75 B LDPC-codiert ≈ 200 Symbole + 9 SYNC = 209 Symbole ≈ 6,7 s

Vergleich:
  GUST-S:     20 Byte Payload,  ≤ 5,0 s
  GUST-X v1:  44 Byte Payload,  ≈ 6,7 s   (Budget ≤ 7,5 s)
  Effizienz:  +120 % Payload im erweiterten Sendedauer-Budget (max. +50 %)
```

#### FEC: LDPC n=256, Rate 3/4

GUST-X verwendet LDPC statt RS(255,223):

| Eigenschaft | GUST-S RS | GUST-X LDPC |
|---|---|---|
| Blockgröße | 255 Byte (shortened) | 256 Bit |
| Code-Rate | ~87 % (shortened) | 75 % |
| Fehlerkorrektur | Hard-Decision | Soft-Decision (Belief Propagation) |
| SNR-Gewinn | Referenz | ~2 dB besser |
| Voraussetzung | — | Soft-Output-Demodulator |

**Hinweis:** LDPC entfaltet seinen vollen Gewinn nur mit einem
Soft-Output-Demodulator der Log-Likelihood-Ratios (LLR) statt
Hard-Decisions liefert. Bis zur Implementierung des Soft-Demodulators
kann GUST-X mit Hard-Decision-Fallback betrieben werden (kein
SNR-Vorteil, aber längere Payload und Timestamp).
Empirische Grundlage: Blocklängen-Evaluation §27 / gust_knowledge.md §27.

#### Rückwärtskompatibilität

```
GUST-S sendet → GUST-X Decoder:  vollständig dekodierbar ✓
GUST-X sendet → GUST-S Decoder:  unsichtbar (9-Symbol-SYNC nicht erkannt)
                                   kein Fehler, kein falsches Dekodat
GUST-X sendet 0x01 WEATHER →
               GUST-S Decoder:   vollständig dekodierbar ✓ (8-Symbol-SYNC)
```

#### gateway.json Konfiguration

```json
"protocol": {
    "variant": "gust-s",
    "comment": "gust-s (Standard) oder gust-x (Extended, LDPC + 44B Payload)"
}
```

Standard ist `gust-s`. Eine Station die `gust-x` konfiguriert sendet
automatisch mit 9-Symbol-SYNC und LDPC. Empfang beider Varianten ist
immer aktiv (der Decoder erkennt beide).

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

GUST verwendet einen eigenen Log-Level **VITAL** (35)
zwischen WARNING (30) und ERROR (40).

**Ohne `--verbose` (Standard):**
- VITAL und ERROR erscheinen auf der Konsole
- Timestamp HH:MM:SS vor jeder Meldung
- RX/TX-Frame-Events, Heartbeat, CRC-Meldungen: stumm

**Mit `--verbose`:**
- Alle Level ab DEBUG sichtbar
- RX ◀ / TX ▶ / Heartbeat farbig hervorgehoben

**VITAL-Ereignisse (immer sichtbar):**
- `GUST Web-Server gestartet`
- `rigctld gestartet/gestoppt`
- `TRX-Profil aktiviert`
- `[RX Audio] input overflow`
- PTT EIN/AUS, TX-Pipeline

**Farbcodierung:**
- Magenta: VITAL
- Grün: INFO
- Gelb: TX ▶
- Blau: RX ◀
- Grau: Heartbeat ▸ / DEBUG
- Rot: ERROR

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
*Stand: Juni 2026 — v0.5 · QSO-Modus · TRX-Profile · CLI-Logging · BUG-10/11/12 · 3 TRX-Profile (IC-7610, FT-818, TS-790) · §3.9 GUST-X (Entwurf) · AUTH_EX 0x85/0x86 ECDSA-64 (2-Frame)*
*Lizenz: CC BY-SA 4.0 (geplant für Veröffentlichung)*