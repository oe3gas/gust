# GUST — Testplan
**OE3GAS — Systematische Verifikation aller Komponenten**
*Stand: Mai 2026 — Phase 7 On-Air-Test abgeschlossen*

---

## Übersicht

| Kürzel | Kategorie | Beschreibung |
|---|---|---|
| **UT** | Unit Test | Eine Funktion, kein Hardware, isoliert |
| **IT** | Integration Test | Zwei oder mehr Module zusammen |
| **HT** | Hardware Test | Echte Hardware erforderlich |
| **OA** | On-Air Test | Lizenzkonformer HF-Betrieb erforderlich |

**Status:** ✅ bestanden · 🔲 ausstehend · ⚠️ bekanntes Problem · ❌ fehlgeschlagen

---

## Modul 1 — Frame Layer (`gust_frame.py`) ✅

### T-1.1 Basis-40 Rufzeichen-Kodierung (UT) ✅
Encode/Decode-Roundtrip für alle gültigen Rufzeichen-Formate bestanden.
Bekannt: Rufzeichen > 6 Zeichen werden auf 6 Zeichen gekürzt — kein Bug.

### T-1.2 CRC-16 Verifikation (UT) ✅
```python
assert crc16(b"123456789") == 0x29B1   # IEEE-Referenzwert ✅
```

### T-1.3 Payload-Encoder alle Frame-Typen (UT) ✅

| Frame | Payload-Länge | Status |
|---|---|---|
| 0x01 Wetter | 14 Byte | ✅ |
| 0x02 Position | 18 Byte | ✅ |
| 0x20 Notfall | 16 Byte | ✅ |
| 0x40 Freitext | ≤ 20 Byte | ✅ |

### T-1.4 Frame-Build / Frame-Parse Roundtrip v0.3 (UT) ✅
`build_frame()` → `parse_frame()` → Werte identisch inkl. CHANNEL-Byte. ✅

### T-1.5 Kanalzuweisung Determinismus (UT) ✅
```
OE3GAS → Kanal 2, Versatz 220 s  (reproduzierbar über Neustarts) ✅
OE3GAT → Kanal 0, Versatz  93 s  ✅
```

---

## Modul 2 — Modulator/Demodulator (`gust_modulator.py`) ✅

### T-2.1 Phasenkontinuität (UT) ✅
Alle 8 Symbole moduliert/demoduliert → kein Symbolfehler. ✅

### T-2.2 Frequenzgenauigkeit FFT (UT) ✅
Zero-Padding 4096 Punkte: Ablesefehler < 2 Hz bei Kanal-2-Basis (900 Hz). ✅

### T-2.3 WAV-Loopback (UT) ✅
`transmit()` → WAV → `receive()` → FROM, TYPE, CHANNEL, CRC korrekt. ✅

### T-2.4 Channel-Scan alle 10 Kanäle (UT) ✅
Wetter-Frame auf Kanal 7 → Scan findet ihn korrekt. ✅

### T-2.5 Reed-Solomon Fehlerkorrektur (UT) ✅
5 zufällige Byte-Fehler → korrigiert. 17+ Fehler → ReedSolomonError. ✅

### T-2.6 Raised Cosine Windowing (UT) ✅
Seitenkeullen-Reduktion ≥ 5 dB gegenüber Rechteckfenster gemessen. ✅

### T-2.7 Breitband-SYNC-Erkennung (IT) ✅
`receive(audio, channel=None)` → Kanal und Offset automatisch erkannt.
Loopback-Test: Kanal 2 erkannt, Offset −1,6 Hz (< 2 Hz Fehler). ✅

### T-2.8 load_wav() Resampling (UT) ✅
48 kHz WAV → automatisch auf 8 kHz resampelt, Frame dekodierbar. ✅

### T-2.9 load_wav() uint8-Support (UT) ✅
8-Bit unsigned WAV (SDRplay-Export) → korrekt normalisiert auf float32. ✅

### T-2.10 Frequenz-Fein-Refinement (UT) ✅
`_refine_sync()` schärft die grob (8-Hz-Raster) erkannte Basisfrequenz nach.
Sandbox mit simuliertem HackRF-Offset (+39,5 Hz): ohne Refinement 1/5 Frames,
mit Refinement 4/5 (f0 z.B. 1140 → 1150,0 Hz korrigiert). ✅

### T-2.11 Scan-Range deckt alle Kanäle (UT) ✅
Breitband-Scan 320–2760 Hz. Kanal 9 (2650 Hz, Ton 7 = 2868,75 Hz) wird jetzt
erfasst — lag vorher außerhalb der Range (380–2580 Hz) und scheiterte komplett. ✅

### T-2.12 Timing-Robustheit / Halb-Block-Auflösung (UT) ✅
Frame an allen Sub-Symbol-Sample-Offsets (0–240 in 16er-Schritten) im 8s-Fenster:
**16/16 dekodiert** (vorher ~50% — Offsets nahe +128 Samples = halbes Symbol scheiterten).
Halb-Block-Suche (128-Sample-Raster) + Sample-genaues Timing-Refinement. ✅

### T-2.13 Voller Decode-Test alle Kanäle + Offset + Versatz (IT) ✅
10 Kanäle, simulierter +39,5 Hz Offset, ungerader Sample-Versatz: **10/10**.
Dual-Kanal-Paare (gemischt, normalisiert): **5/5**. Decode-Zeit ~110 ms mit Frame,
~620 ms worst case (nur Rauschen). ✅

### T-2.14 Vollfenster-Garantie / Fixed-Cadence (UT, BUG-07) ✅
Timing-Simulation, 20 000 zufällige Frame-Ankunftsphasen, Framedauer 5,4 s.
Alte Schleife (variabel, Fenster 8 s, interval+decode): **10,55% Misses** — deckt
sich mit der live beobachteten Simplex-Rate (~1/6–1/11). Fixed-Cadence + Fenster 9 s:
**0 Misses**. Scheduling-Logik separat verifiziert: Capture-Kadenz bleibt bei
variablen Decodes < Intervall stabil (Drift < 1 ms / 10 Scans), Resync nach Überzug
ohne Aufstauen. Startup-Invariante WINDOW_S ≥ MAX_FRAME_S + SCAN_INTERVAL_S
(9,0 ≥ 7,5, Marge 1,5 s) wird beim Loop-Start geprüft. ✅

---

## Modul 3 — Audio TX (`gust_audio.py`) ✅

### T-3.1 PTT null-Backend (HT) ✅
PTT aktivieren/deaktivieren ohne Hardware — kein Fehler. ✅

### T-3.2 PTT idempotent (UT) ✅
Mehrfacher `release()`-Aufruf → nur ein "TX AUS" in Ausgabe. ✅

### T-3.3 Audio TX Loopback via USB-Soundkarte (HT) ✅
Line-Out → Line-In Loopback → Frame dekodiert. ✅

### T-3.4 HackRF TX → AirSpy RX (HT) ✅
NF-Signal über HackRF moduliert, AirSpy empfängt, Audacity zeigt MFSK-Muster. ✅

### T-3.5 IC-7610 TX via hamlib PTT (HT) ✅
```
py gust.py tx weather --temp 21.5 --device 9
→ PTT EIN, Audio 4.67s, PTT AUS (einmalig) ✅
```
Gerät: IC-7610 USB Audio CODEC, ID 9 (MME).
ALC: nicht im roten Bereich bei ACC Input 40% + Level 10%. ✅

---

## Modul 4 — Gateway / Quellen (`gust_gateway.py`) ✅

### T-4.1–T-4.5 — unverändert, alle bestanden ✅

---

## Modul 5 — Event-Bus (`gust_eventbus.py`) ✅

### T-5.1–T-5.7 — unverändert, alle bestanden ✅

---

## Modul 6 — Web-Server (`gust_web.py`) ✅

### T-6.1–T-6.13 — unverändert, alle bestanden ✅

### T-6.14 Remote-Zugriff via LAN (manuell) 🔲

---

## Modul 7 — SimAdapter (`gust_msg_simulator.py`) ✅

### T-7.1–T-7.10 — unverändert, alle bestanden ✅

---

## Modul 8 — CLI (`gust.py`) ✅

### T-8.1–T-8.6 — unverändert, alle bestanden ✅

### T-8.7 CLI-Optionen nach Subcommand (UT) ✅
```
py gust.py tx weather --temp 21.5 --callsign OE3GAS --dry-run  ✅
py gust.py tx weather --temp 21.5 --device 9 --level 50        ✅
```
`--callsign`, `--dry-run`, `--device`, `--level` funktionieren nach Subcommand. ✅

### T-8.8 gateway.json level-Normalisierung (UT) ✅
`"level": 10` in JSON → AudioTransmitter Level = 0.10 (10%). ✅
`"level": 50` in JSON → korrekt 0.50, nicht 50.0 (5000%). ✅

---

## Modul 9 — MQTT-Bridge (`gust_mqtt.py`) 🔲

### T-9.1 RX-Frame → MQTT-Publish (IT) 🔲
### T-9.2 MQTT-Subscribe → TX-Queue (IT) 🔲

---

## Modul 10 — On-Air Tests (OA)

### T-10.1 Erste Aussendung auf HF (OA) ✅
**Datum:** 18. Mai 2026
**Aufbau:** IC-7610 → USB-Audio → gust.py tx weather → 14.110,000 MHz USB
**Empfang:** SDRplay RSPdx2, 48 kHz WAV, Audacity-Spektrum
**Ergebnis:**
```
✓  Kanal 2 (900 Hz)  Offset +38.5 Hz  → OE3GAS [WEATHER]
CRC: ✓ OK  |  temp_c: 21.5°C  |  humidity_pct: 65
```
Breitband-Decoder hat Kanal und Frequenzversatz automatisch erkannt. ✅

**Einstellungen:**
- ACC/USB AF Input Level: 40%
- Software Level: 10% (`gateway.json: "level": 10`)
- Raised Cosine Windowing: aktiv
- PTT: hamlib rigctld, IC-7610 Modell 3085

### T-10.2 SNR-Schwelle messen (OA) ✅
**Datum:** 21. Mai 2026
**Methode:** HackRF TX-Gain-Stepping über `tx_test.py --gain-sequence`,
IC-7610 als RX (USB-Audio Gerät 1), `gust_rx.py` kontinuierlicher Decoder.
**Aufbau-Hinweis:** Starke feste Kopplung HackRF → IC-7610 → Absolutwerte
setup-spezifisch; die SNR-Achse ist die belastbare Aussage, nicht die Gain-Achse.

**Dual-Kanal-Sweep (Kanal 2+7, Gain 28→1 dB) — 15/15 dekodiert:**

| Gain | SNR | | Gain | SNR | | Gain | SNR |
|---|---|---|---|---|---|---|---|
| 28 | 24,5 | | 16 | 21,6 | | 6 | 15,9 |
| 26 | 24,5 | | 14 | 20,0 | | 4 | 13,1 |
| 24 | 23,5 | | 12 | 19,3 | | 2 | 12,4 |
| 22 | 23,4 | | 10 | 18,3 | | 1 | **10,1** |
| 20 | 22,7 | | 8 | 16,5 | | | |
| 18 | 22,2 | | | | | | |

**Kernergebnis:** Decode-Schwelle **nicht erreicht** — bei 1 dB Gain (HackRF-Minimum)
lag der SNR noch bei 10,1 dB und der Frame dekodierte sauber (Score 0,99). Die echte
Schwelle liegt ≤ 10 dB SNR. Score bei allen erfolgreichen Decodes 0,94–1,00 (FEC-Cliff:
sauber dekodiert oder gar nicht). Kompression der Gain→SNR-Kurve oben (HackRF-VGA-
Sättigung), linear unterhalb ~14 dB Gain.

**Simplex-Vergleich (gleiche Gains):** ~6 dB höherer SNR pro Kanal als Dual — die
Sendeleistung wird im Dual-Modus auf zwei Kanäle aufgeteilt. Simplex-Dekodierrate
~90% (gelegentlicher Fenstertiming-Miss, BUG-07), Dual 100% → Diversity-Gewinn bestätigt.

**Kanal-0-Reproduzierbarkeit (5× Gain 6):** 5/5 dekodiert (Score 0,96–0,999), aber
alle mit *negativem* angezeigtem SNR (−7,7 bis −11,3 dB) — SNR-Schätzer-Fehler an der
unteren Bandkante, siehe BUG-06. Decode selbst einwandfrei.

**Frequenzoffset HackRF → IC-7610:** konsistent −14 bis −20 Hz über alle Decodes,
vom Refinement zuverlässig erfasst und kompensiert.

### T-10.3 Kollisionstest mit OE1XTU (OA) 🔲
Zwei Stationen, gleicher Kanal, Frameverlustrate messen.

### T-10.4 MeshCom End-to-End (OA) 🔲
LoRa → GUST-Gateway → HF → Remote-Empfänger → MQTT-Echo.

---

## Anhang: Testausführung

```bash
# Alle automatisierten Tests Phase 5 (51 Tests)
py test_phase5.py -v

# Frame-Layer Selbsttest (v0.3)
py gust_frame.py

# Modulator Selbsttest inkl. Breitband-SYNC
py gust_modulator.py

# Decoder Breitband-Test
py gust_decode.py aufnahme.wav --scan

# TX On-Air (IC-7610)
py gust.py tx weather --temp 21.5 --device 9

# Daemon mit Simulator
py gust.py daemon --sim --interval 15
# → http://localhost:8080
```

### Testumgebung

**Laboraufbau (Phasen 1–7):**
```
PC Windows 11, Python 3.14
  ├── USB-Audio ID 9 (MME)  →  IC-7610 ACC-Buchse
  ├── HackRF One (TX-Tests SNR)
  └── SDRplay RSPdx2 (RX-Referenz, 48 kHz WAV)
```

**On-Air-Aufbau (Phase 7):**
```
IC-7610
  ├── USB-Audio ID 9 → PC (TX NF, Level 10%)
  ├── USB-CAT  → rigctld -m 3085 → hamlib PTT
  ├── ACC/USB Input: 40%
  └── HF-Antenne (Dipol 14 MHz), 14.110,000 MHz USB
```

**SNR-/Dual-Kanal-Messaufbau (Phase 7, P7-05 / T-10.2):**
```
HackRF One #0  ──(starke Kopplung)──→  IC-7610 RX (USB-Audio Gerät 1)
  │                                       │
  └── tx_test.py --gain-sequence          └── gust_rx.py --device 1 -v
      (Gain-Stepping, Einzel-/Dual-Kanal)     (kontinuierlicher Scan-Loop)

Python 3.9 erforderlich (PothosSDR/SoapySDR-Bindings für HackRF).
PYTHONPATH = C:\Program Files\PothosSDR\lib\python3.9
HackRF-TX-Offset: konstant, vom Decoder automatisch kompensiert.
```

### Benötigte Software

| Tool | Zweck | Verfügbar |
|---|---|---|
| Audacity | Spektrum-Verifikation WAV | ✅ |
| inspectrum | MFSK-Muster CF32 | ✅ |
| aiohttp (Python) | Web-Server + Test-Client | ✅ |
| mosquitto | MQTT-Broker für T-9.x | 🔲 |
| rigctld (hamlib) | PTT IC-7610 | ✅ |
| SDRplay RSPdx2 | RX-Referenz On-Air | ✅ |

---

*Dokument: gust_testplan.md*
*Autor: OE3GAS*
*Stand: Mai 2026 — Phase 7 Empfänger-Robustheit + SNR-Baseline (T-10.2) abgeschlossen*
*Gilt für: Phase 1–10*