# GUST — Generic Universal Shortwave Telemetry
**OE3GAS — Amateurfunk-Digitalprotokoll für KW-Telemetrie**
*CLAUDE.md — Projektwissen für Claude Code*
*Stand: Mai 2026 — Phase 7 abgeschlossen*

---

## Zweck

GUST ist ein offenes, binäres Amateurfunk-Digitalprotokoll für die robuste Übertragung von
Telemetriedaten und Kurznachrichten auf Kurzwelle. Es dient als **HF-Backbone** zwischen lokalen
Datenquellen (Wetterstationen, LoRa/Meshtastic-Meshes, Sensorik) und entfernten Empfangsstationen —
auch ohne Internetinfrastruktur.

**Leitprinzipien:**
- Vollständig offene Spezifikation, kein proprietäres Element
- Binäres, kompaktes Datenformat (kein ASCII-Overhead)
- Robustheit auf KW-Kanälen als primäres Designziel
- Raspberry Pi als Referenz-Zielplattform (Gateway-Betrieb)
- Kompatibilität mit IC-7610, HackRF, SDRplay
- Web-Browser als GUI (lokal und remote via LAN/VPN)

---

## Aktueller Stand

**Phase 7 — Empfänger-Robustheit + SNR-Baseline — abgeschlossen (Mai 2026)**

### Protokollversion: v0.3

Kernänderungen gegenüber v0.2:
- SYNC: 8 Symbole (256 ms) statt 4 — für Breitband-Erkennung ohne Kanal-Vorwissen
- CHANNEL-Byte im Frame-Header — TX/RX-Konsistenzprüfung

### Was funktioniert (getestet und produktiv):

| Funktion | Status |
|:---------|:------:|
| MFSK-8 Modulator (phasenkontinuierlich, Raised Cosine) | ✅ |
| MFSK-8 Demodulator (FFT zero-padded 4096 Punkte) | ✅ |
| Breitband-SYNC-Erkennung (channel=None, alle 10 Kanäle) | ✅ |
| Frequenz-Fein-Refinement (_refine_sync, < 1 Hz Genauigkeit) | ✅ |
| Timing-Refinement (Halb-Block + Sample-genau) | ✅ |
| Reed-Solomon FEC RS(255,223), korrigiert bis 16 Byte-Fehler | ✅ |
| Frame-Layer v0.3 (SYNC 8 Sym, TYPE, CHANNEL, FROM, CRC-16) | ✅ |
| Alle 4 Frame-Typen (Wetter 0x01, Position 0x02, Notfall 0x20, Text 0x40) | ✅ |
| Kontinuierlicher RX-Loop (gust_rx.py, asyncio, Fixed-Cadence-Scheduling) | ✅ |
| HackRF Dual-Kanal-TX (transmit_iq, zwei IQ-Signale gemischt) | ✅ |
| Parallelkanal-Diversity (RX-Dedup, Diversity-Gewinn bestätigt 100% vs ~90%) | ✅ |
| TX-Pipeline IC-7610 via USB-Audio + hamlib PTT | ✅ |
| SNR-Schätzer adaptiv (BUG-06 gefixt, alle Kanäle konsistent) | ✅ |
| Web-UI (aiohttp, REST + WebSocket, 4 Tabs, Dark/Light Theme) | ✅ |
| Event-Bus (asyncio Fan-out, TTL-Filter) | ✅ |
| CLI (daemon/rx/tx/info/devices) | ✅ |
| Erster On-Air-Test 14.110 MHz (18. Mai 2026) | ✅ |
| SNR-Baseline: Decoder bis ≤ 10 dB SNR (21. Mai 2026, T-10.2) | ✅ |

### SNR-Baseline (T-10.2, empirisch):
- **Dual-Kanal:** Dekodiert bis mindestens 10,1 dB SNR (Gain 1 dB = HackRF-Minimum)
- **Echte Schwelle:** ≤ 10 dB (TX-Boden erreicht, bevor Decoder aussetzte)
- **FEC-Cliff-Verhalten:** Score durchgehend 0,94–1,00 (sauber dekodiert oder gar nicht)
- **Simplex vs. Dual:** Simplex ~90% (BUG-07-Fenstertiming), Dual 100% (15/15)

---

## Technologie-Stack

### Programmiersprache und Laufzeit
- **Python 3.14** (Haupt-Entwicklungsumgebung, Windows 11)
- **Python 3.9** zwingend für HackRF-Tests (PothosSDR/SoapySDR-Bindings)
  - `PYTHONPATH = C:\Program Files\PothosSDR\lib\python3.9`

### Kernbibliotheken

| Bibliothek | Einsatz |
|:-----------|:--------|
| `numpy` | FFT (rfft, n=4096), Sinuserzeugung, Array-Operationen |
| `scipy.signal` | Hilbert-Transform, Resampling (`resample_poly`) |
| `scipy.io.wavfile` | WAV lesen/schreiben (inkl. uint8 SDRplay-Export) |
| `reedsolo` | Reed-Solomon FEC (Shortened Code automatisch) |
| `sounddevice` | Audio TX/RX (Geräte per Integer-ID, nicht Name!) |
| `aiohttp` | Web-Server (REST + WebSocket, Port 8080) |
| `asyncio` | RX-Loop, Event-Bus, Web-Server |
| `struct` | Binäre Payload-Kodierung (Big-Endian `>`) |
| `hashlib` | SHA-256 für deterministische Kanalzuweisung |

### Hardware (Labor)
```
PC Windows 11, Python 3.14
  ├── USB-Audio ID 9 (MME)  →  IC-7610 ACC-Buchse (TX NF, Level 10%)
  ├── HackRF One            →  IC-7610 RX (SNR-Tests, Python 3.9)
  └── SDRplay RSPdx2        →  RX-Referenz On-Air (48 kHz WAV)

IC-7610:
  ├── USB-CAT  → rigctld -m 3085 → hamlib PTT
  ├── ACC/USB Input Level: 40%
  └── HF-Antenne (Dipol 14 MHz), 14.110,000 MHz USB
```

### Konfigurationsdatei
`gateway.json` — Stationsparameter (Rufzeichen, Audiogerät, Level, PTT-Backend)
- `"level": 10` → normalisiert zu 0.10 (Werte > 1 werden als Prozent interpretiert)
- Audiogerät: Integer-ID, nicht Name (Windows-Problem mit MME/DirectSound/WASAPI)

---

## Protokollarchitektur (kritisches Wissen)

### Frequenzarchitektur (FT8-Prinzip)
```
Dial-Frequenz: 14.110,000 MHz (USB, alle Stationen gleich)
NF-Kanäle 0–9: 400–2900 Hz (10 × 250 Hz, passt in SSB-Passband)
Tonabstand:     31,25 Hz (orthogonal, MFSK-8 identisch zu Olivia 8/250)
Symboldauer:    32 ms
```

### Kanalzuweisung
`SHA-256(Rufzeichen) % 10` = Kanal, `(hash >> 8) % 300` = Zeitversatz in Sekunden.
Deterministisch, kein Koordinationsaufwand, kein Beacon nötig.
**OE3GAS → Kanal 2, Versatz 220 s** (reproduzierbar)

### Frame-Struktur v0.3
```
┌─────────────┬──────────┬──────────────┬──────────┬────────────────┬──────────┐
│  SYNC (8)   │ TYPE (1) │ CHANNEL (1)  │ FROM (4) │ PAYLOAD (var.) │ CRC (2)  │
│   Symbole   │   Byte   │    Byte      │   Byte   │  max. 20 Byte  │   Byte   │
└─────────────┴──────────┴──────────────┴──────────┴────────────────┴──────────┘
```
SYNC-Tonfolge: [7,0,7,0,7,0,7,0] — alternierend höchster/niedrigster Ton.
Nach SYNC folgt Reed-Solomon-Encoding: immer +32 Byte Parität (RS(255,223) shortened).

### Wetter-Frame Beispiel (Größenverhältnisse):
```
encode_weather():   14 Byte Payload
build_frame():      22 Byte  (+8: TYPE+CHANNEL+FROM+CRC)
rs_encode():        54 Byte  (+32 Byte RS-Parität)
frame_to_symbols(): 152 Symbole  (8 SYNC + 144 Daten)
Audiodauer:          4,86 s  (mit PTT-Vor/Nachlauf)
```

### Wichtige DSP-Details
- **Zero-Padding:** FFT immer mit n=4096, sonst Bin-Fehler bis 31 Hz (Kanalbasen nicht ganzzahlig in 31,25-Hz-Raster)
- **Raised Cosine Windowing:** Symbolflanken glätten (`window=True`), reduziert Spectral Leakage ~27 dB
- **Breitband-Scan-Range:** 320–2760 Hz (nicht 380–2580 — sonst Kanal 9 verfehlt!)
- **Frequenz-Refinement:** `_refine_sync()` schärft grob erkannte f0 auf < 1 Hz nach
- **Timing-Refinement:** SYNC-Suche im 128-Sample-Raster + Sample-genaues Nachschärfen

### Fixed-Cadence-Scheduling (BUG-07-Fix, kritisch!)
```python
# FALSCH (alte Variante — Decode-Zeit bläht Intervall auf):
await asyncio.sleep(interval)
snapshot = ringbuffer.get_last(WINDOW_S)
decode(snapshot)

# RICHTIG (Fixed-Cadence — Decode-Zeit wird in Sleep absorbiert):
next_tick += interval
snapshot = ringbuffer.get_last(WINDOW_S)
await decode(snapshot)
await asyncio.sleep(max(0, next_tick - time.monotonic()))
```
**Invariante:** `WINDOW_S ≥ MAX_FRAME_S + SCAN_INTERVAL_S` (9,0 ≥ 5,5 + 2,0 = 7,5 s)
Diese Invariante beim Loop-Start prüfen und ausgeben!

---

## Dateistruktur

| Datei | Inhalt | Version |
|:------|:-------|:--------|
| `gust_frame.py` | Frame Layer: Encoder/Decoder, CRC-16, Kanalzuweisung | 0.3.0 |
| `gust_modulator.py` | MFSK-8 Mod/Demod, Breitband-RX, Refinement | 0.3.1 |
| `gust_audio.py` | Audio TX/RX, PTT-Backends, Auto-Mono/Stereo | 1.1.0 |
| `gust_rx.py` | Kontinuierlicher RX-Scan-Loop (asyncio) | 1.0.0 |
| `gust_hackrf.py` | HackRF TX, Einzel- + Dual-Kanal (`transmit_iq`) | 0.2.0 |
| `gust_decode.py` | Standalone Decoder, Breitband-Scan CLI | 0.2.0 |
| `gust_eventbus.py` | asyncio Fan-out Event-Bus, TTL-Filter | — |
| `gust_web.py` | aiohttp Web-Server, REST API, WebSocket | — |
| `gust.py` | CLI-Einstiegspunkt (daemon/rx/tx/info/devices) | 0.1.1 |
| `gust_tx_test.py` | TX-Mess-Skript (--channels, --gain-sequence) | 1.1.0 |
| `gateway.json` | Stationskonfiguration | — |
| `requirements.txt` | Python-Abhängigkeiten | — |

### Dokumentation
| Datei | Inhalt |
|:------|:-------|
| `gust_spec.md` | Vollständige Protokollspezifikation v0.3 |
| `gust_knowledge.md` | Designentscheidungen, DSP-Lernpunkte, Bug-History |
| `gust_backlog.md` | Offene Aufgaben, ADRs, Feature-Ideen |
| `gust_testplan.md` | Testplan mit Ergebnissen (T-1.x bis T-10.x) |

---

## Wichtige Konventionen

### Code-Stil
- Alle Kommentare und Dokumentation auf **Deutsch** (Projektsprache)
- Rufzeichen immer als String, Basis-40-kodiert für Übertragung (4 Byte)
- Frame-Typen als Hex-Konstanten: `0x01`, `0x02`, `0x20`, `0x40`
- Sample-Rate: **8000 Hz** intern (WAV-Dateien werden automatisch resampelt)
- Audio-Pegel: immer normalisiert als float in [0.0, 1.0]; gateway.json-Werte > 1 = Prozent

### Audiogeräte (Windows-spezifisch!)
- Geräte IMMER per Integer-ID ansprechen (nicht per Name)
- `py gust.py devices` zeigt verfügbare IDs
- IC-7610 USB Audio CODEC = ID 9 (MME) in dieser Testumgebung

### PTT-Backends
- `hamlib`: rigctld muss laufen (`rigctld -m 3085 -r COM3`)
- `null`: kein Hardware, für Tests
- `vox`: automatisch, kein CAT
- `release()` ist idempotent (mehrfacher Aufruf sicher — PTT-Triple-Release-Fix)

### RS-FEC
- `reedsolo` handhabt Shortened Code automatisch
- Overhead immer 32 Byte, unabhängig von Payload-Länge
- Bei `rs_decode()`: Range muss von `n_bytes_max` bis `rs_min` laufen (BUG-07E: nur 9 Schritte war falsch!)

### Dual-Kanal-TX (HackRF)
- `transmit_iq()` mischt zwei IQ-Signale vor der Übertragung
- Sendeleistung wird auf beide Kanäle aufgeteilt (~6 dB weniger pro Kanal)
- **Zwingend:** Default-Timeout für `writeStream` verwenden (langer Timeout → TX-Underrun → HackRF-Firmware hängt)
- HackRF-Python-Bindings nur mit Python 3.9 (PothosSDR)

### SNR-Schätzer
- Rauschreferenzband adaptiv wählen: beidseitig relativ zu [f0, f0+218,75 Hz] mit 80 Hz Guard
- Die niedrigere der beiden Schätzungen gewinnt (kontaminierte Seite verworfen)
- Kanal-0-SNR war früher ~30 dB zu niedrig (Rauschreferenz überlappt Signalband) — BUG-06

---

## Bekannte Einschränkungen & offene Bugs

| ID | Beschreibung | Status |
|:---|:-------------|:------:|
| BUG-01 | Rufzeichen > 6 Zeichen werden gekürzt (VK2XX/P → VK2XX/) | ⏸ Phase 8 |
| BUG-02 | inspectrum Frequenzachse ~600 Hz verschoben (Darstellungsartefakt) | ⏸ |
| BUG-03 | CF32-Export zeigt Rest-Spiegelbild (Hilbert + Stille-Abschnitte) | ⏸ |
| BUG-04 | RS(255,223) immer 32 Byte Overhead — ineffizient für kurze Frames | 🔲 Phase 8 |
| BUG-05 | `asyncio.get_event_loop()` deprecated (Python 3.10+, Meshtastic) | 🔲 |
| BUG-08 | Frame-Contention: zwei Frames in einem 8s-Fenster → zweiter kann verloren gehen | 🔲 |

---

## Offene Fragen / Nächste Schritte

### Phase 7 — noch offen
| ID | Aufgabe | Priorität |
|:---|:--------|:---------:|
| P7-04 | Soapy7610 TX-Pfad: IC-7610 direktes IQ-TX via SoapySDR | 🟡 |
| P7-07 | Preamble-Länge validieren: 256 ms ausreichend für reale KW? | 🟡 |
| P7-08 | Kollisionstest mit OE1XTU: zwei Stationen, gleicher Kanal | 🟡 |
| P7-09 | MeshCom End-to-End: LoRa → GUST-Gateway → HF → Remote | 🟢 |
| P7-10 | Demodulator als GNU Radio OOT-Block portieren | 🟢 |

### Phase 8 — Dokumentation & Veröffentlichung
| ID | Aufgabe | Priorität |
|:---|:--------|:---------:|
| P8-01 | Protokollspezifikation v0.3 finalisieren (publikationsreif) | 🟡 |
| P8-02 | Installationsanleitung Raspberry Pi | 🟡 |
| P8-03 | GitHub Repository (OE3GAS/gust, README, CC BY-SA 4.0) | 🟢 |
| P8-04 | ÖVSV-Präsentation vorbereiten | 🟢 |
| P8-05 | Protokoll bei ÖVSV einreichen | 🟢 |

### Phase 6 — MQTT-Bridge (zurückgestellt, noch offen)
| ID | Aufgabe |
|:---|:--------|
| P6-01 | `gust_mqtt.py`: RX-Frames → MQTT publishen |
| P6-02 | MQTT TX-Input → TX-Queue |
| P6-04 | Home Assistant MQTT Discovery |

### Wissenslücken / Forschungsfragen
- SNR-Vergleich GUST vs. Olivia unter gleichen Bedingungen
- Optimale Preamble-Länge für reale KW-Kanäle (Multipath, Doppler)
- RS-FEC Optimierung für kurze Frames (RS(31,15) als Alternative)
- Frequenz-Agility / CSMA-ähnliche Kollisionsvermeidung (IDEA-03)

---

## Testumgebung & Ausführung

### Schnellstart
```bash
# Daemon mit Simulator (kein Hardware nötig)
py gust.py daemon --sim --interval 15
# → http://localhost:8080

# TX On-Air (IC-7610, Audio-ID prüfen!)
py gust.py tx weather --temp 21.5 --device 9

# RX kontinuierlich (IC-7610 USB-Audio)
py gust_rx.py --device 1 -v

# WAV-Datei dekodieren (Breitband-Scan)
py gust_decode.py aufnahme.wav --scan

# Frame-Layer Selbsttest
py gust_frame.py

# Modulator Selbsttest inkl. Breitband-SYNC
py gust_modulator.py
```

### HackRF SNR-Test (Python 3.9 erforderlich!)
```bash
# PYTHONPATH setzen (PothosSDR)
set PYTHONPATH=C:\Program Files\PothosSDR\lib\python3.9

# Gain-Sweep Dual-Kanal
py gust_tx_test.py --channels 2 7 --gain-sequence 28 26 24 22 20 18 16 14 12 10 8 6 4 2 1
```

### Laborkonfiguration
- **IC-7610 ACC/USB Input Level:** 40%
- **Software Level (gateway.json):** `"level": 10` (= 10%)
- **Raised Cosine Windowing:** immer aktiv (`window=True`)
- **PTT:** `hamlib`, rigctld Modell 3085
- **HF-Frequenz:** 14.110,000 MHz USB

---

## Architekturentscheidungen (Zusammenfassung)

| ADR | Entscheidung | Begründung |
|:----|:-------------|:-----------|
| ADR-07 | Protokoll v0.3: SYNC 8 Sym + CHANNEL-Byte | Breitband-Erkennung ohne Kanal-Vorwissen, Frequenzversatz-Toleranz |
| ADR-08 | Audio-Level per gateway.json konfigurierbar | ACC-Eingang stark transceiver-abhängig (IC-7610: 40% + 10%) |
| ADR-09 | Audiogeräte per Integer-ID (nicht Name) | Windows meldet gleiche Geräte 3× mit MME/DirectSound/WASAPI |
| ADR-10 | SNR-Test via HackRF Gain-Stepping | Kein Abschwächer → Gain als Ersatzgröße; echte Schwelle ≤ 10 dB |
| ADR-11 | Frequenz- + Timing-Refinement | Ohne Refinement: 1/5 Frames; mit Refinement: 10/10 |
| ADR-12 | Parallelkanal-Diversity (Dual-Kanal-TX) | QRM-Schutz + Timing-Redundanz; Simplex ~90% → Dual 100% |
| ADR-13 | HackRF TX: Default-Timeout zwingend | Langer Timeout → TX-Underrun → Firmware-Hänger bis USB-Neustart |

---

*Dokument: CLAUDE.md*
*Autor: OE3GAS*
*Generiert: Mai 2026 aus gust_knowledge.md, gust_spec.md, gust_backlog.md, gust_testplan.md*