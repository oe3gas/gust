# GUST — Testplan
**OE3GAS — Systematische Verifikation aller Komponenten**
*Stand: Mai 2026 — Phase 7 On-Air-Test abgeschlossen · Phase 9 Tests definiert*

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
| 0x20 Notfall | 20 Byte | ✅ |
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

## Modul 9 — Connector Layer / MQTT (`gust_connector.py`, `gust_mqtt.py`, `gust_transforms.py`)

> **Teststrategie:** Drei Ebenen — (1) Transform-Unit-Tests ohne jede Infrastruktur,
> (2) Integrationstests gegen öffentlichen Broker, (3) Vollständige Roundtrip-Tests
> mit lokalem In-Process-Broker (`amqtt`). Ebene 1 ist die wichtigste — sie testet
> die Semantik. Ebene 3 erfordert `pip install amqtt`.

---

### T-9.1 Transform-Offline: `weather_from_ecowitt` Feldzuordnung (UT) 🔲

**Infrastruktur:** keine — reiner Python-Funktionsaufruf

**Methode:** `weather_from_ecowitt("froggit/data", FROGGIT_SAMPLE)` mit realem
Froggit HP2000 Pro JSON-Payload.

**Erwartete Felder und Werte:**

| Feld | Quelle | Erwarteter Wert |
|---|---|---|
| `temp_c` | `temp` | 12.5 |
| `humidity_pct` | `humidity` | 88 |
| `pressure_hpa` | `baromrel` | 1028.65 |
| `wind_kmh` | `windspeed` | 0 |
| `wind_deg` | `winddir` | 210 |
| `rain_mm_h` | `rainrate` | 0.0 |
| `uv_index` | `uv` | 0 |
| `flags` | `batt==0` → gut | 0x03 |

---

### T-9.2 Transform-Offline: PASSKEY-Ausschluss (UT, Security) 🔲

**Infrastruktur:** keine

**Methode:** `weather_from_ecowitt()` mit vollständigem Froggit-JSON (inkl. PASSKEY).

**Erwartung:** `"PASSKEY"` kommt in keinem Key oder Value des Ergebnis-Dicts vor.
PASSKEY darf auch nicht in Log-Ausgaben erscheinen.

```python
result = weather_from_ecowitt(topic, raw_with_passkey)
assert "PASSKEY" not in str(result)
assert "08124B0F" not in str(result)   # Wert-Fragment
```

---

### T-9.3 Transform-Offline: Ecowitt → `encode_weather()` Roundtrip (UT) 🔲

**Infrastruktur:** keine

**Methode:** Transform-Ergebnis direkt an `encode_weather(**result)` übergeben.

**Erwartung:** Kein `TypeError`, kein `ValueError`, `len(encoded) == 14`.

Dies ist der Nachweis dass Transform-Output und Frame-Layer-Input kompatibel sind.

---

### T-9.4 Transform-Offline: `field_map` YAML-Konfiguration (UT) 🔲

**Infrastruktur:** keine

**Methode:** `SemanticMapping` mit einer `field_map`-Regel laden, einen eigenen
Sensor-JSON durchleiten.

```python
yaml_rule = {
    "topic": "eigener/sensor",
    "frame_type": "WEATHER",
    "transform": "field_map",
    "field_map": {
        "temp_c": "temp_aussen",
        "humidity_pct": "feuchte",
        "pressure_hpa": {"key": "luftdruck_raw", "scale": 0.1}
    }
}
raw = {"temp_aussen": 14.2, "feuchte": 75, "luftdruck_raw": 10186}
# Erwartung: temp_c=14.2, humidity_pct=75, pressure_hpa=1018.6
```

---

### T-9.5 Transform-Offline: `SemanticMapping.map_inbound()` Topic-Routing (UT) 🔲

**Infrastruktur:** keine

**Methode:** Verschiedene Topics gegen die Mapping-Tabelle matchen.

| Topic | Erwarteter FrameType | Erwarteter Transform |
|---|---|---|
| `homeassistant/sensor/outdoor/state` | `WEATHER` | `weather_from_ha_json` |
| `froggit/gateway/data` | `WEATHER` | `weather_from_ecowitt` |
| `aprs/position/OE3GAS` | `POSITION` | `position_from_aprs_json` |
| `unbekannt/topic` | — | `None` (kein Match) |

---

### T-9.6 Transform-Offline: `SemanticMapping.map_outbound()` Topic-Template (UT) 🔲

**Infrastruktur:** keine

**Methode:** Simuliertes `RX_FRAME`-Dict durch `map_outbound()` leiten.

**Erwartung:** WEATHER-Frame von OE3GAS → Topic `gust/rx/weather/OE3GAS`,
Payload enthält `temp_c`, `_from: "OE3GAS"`, `_crc_ok: true`.
Kein POSITION-Mapping für einen TEXT-Frame wenn nicht konfiguriert → `None`.

---

### T-9.7 Integration: MQTTConnector Outbound — öffentlicher Broker (IT) 🔲

**Infrastruktur:** `test.mosquitto.org:1883` (Internet erforderlich)

**Hinweis:** Nur synthetische Testdaten — kein PASSKEY, keine Echtstation.

**Methode:**
1. MQTTConnector starten, Subscriber auf `gust/rx/weather/#` einrichten
2. `RX_FRAME`-Event (simulierter WEATHER-Frame OE3GAS) auf Event-Bus publizieren
3. Subscriber empfängt Payload

**Erwartung:** Payload auf `gust/rx/weather/OE3GAS`, `temp_c` korrekt, Roundtrip < 3 s.

---

### T-9.8 Integration: MQTTConnector Inbound — öffentlicher Broker (IT) 🔲

**Infrastruktur:** `test.mosquitto.org:1883` (Internet erforderlich)

**Methode:**
1. MQTTConnector subscribt auf `froggit/test/data`
2. Froggit-JSON per `paho` publizieren
3. Event-Bus prüfen: `CONNECTOR_RX`-Event erscheint

**Erwartung:** `CONNECTOR_RX`-Event enthält `frame_type=WEATHER`,
`payload_dict["temp_c"] == 12.5`, `from_call == "OE3GAS"`.

---

### T-9.9 Integration: Vollständiger Roundtrip — lokaler amqtt-Broker (IT) 🔲

**Infrastruktur:** `pip install amqtt` — kein Netzwerk, kein Systemdienst

**Setup:** amqtt als pytest-Fixture auf Port 18830.

**Ablauf:**
```
MQTT publish (Froggit-JSON auf froggit/gateway/data)
  → MQTTConnector Inbound
  → SemanticMapping.map_inbound() → WEATHER, payload_dict
  → CONNECTOR_RX Event auf Bus
  → TX-Queue → build_frame(WEATHER, "OE3GAS", encode_weather(...))
  → RX_FRAME Event auf Bus
  → MQTTConnector Outbound
  → MQTT publish auf gust/rx/weather/OE3GAS
```

**Erwartung:** Subscriber empfängt auf `gust/rx/weather/OE3GAS`
mit `temp_c: 12.5`. Vollständiger Weg Frame-frei von externem JSON
bis zu MQTT-Ausgabe ohne direkten Frame-Layer-Aufruf im Test.

---

### T-9.10 ConnectorRegistry Start / Stop (UT) 🔲

**Infrastruktur:** keine (Mocked MQTT-Client)

**Methode:** Registry mit einem gemockten Connector starten und stoppen.

**Erwartung:** `start_all()` und `stop_all()` werfen keine Exception,
kein hängender Task in asyncio, sauberes Shutdown auch bei schnellem Stop.

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

### T-10.3 Kollisionstest mit OE1XTU / OE3GAT (OA) 🔲

**Ziel:** Empirisch messen wie sich GUST verhält wenn zwei Stationen gleichzeitig
auf demselben Kanal senden — Frameverlustrate und Verhalten des deterministischen
Hash-Schedulings unter realen Bedingungen.

**Hintergrund:** GUST hat kein CSMA (kein Horchen vor dem Senden). Kollisionen
entstehen wenn zwei Stationen zufällig im selben Zeitfenster senden. Der Hash-Schedule
verteilt Stationen deterministisch — aber nur bei gleichem `interval_s`. Bei
unterschiedlichen Intervallen oder manuell ausgelösten Frames ist Kollision jederzeit
möglich.

| Szenario | Beschreibung | Erwartung |
|---|---|---|
| A — Verschiedene Kanäle | Kontrollfall, kein Konflikt | 100 % Dekodierrate |
| B — Gleicher Kanal, Versatz > 10 s | Hash-Schedule schützt | ≥ 80 % Dekodierrate |
| C — Gleicher Kanal, erzwungen (≤ 2 s) | Absichtliche Überlappung | Dokumentation ohne Zielwert |

**Aufbau:**
```
Station OE3GAS (Wien)               Station OE1XTU / OE3GAT (remote)
  IC-7610                               beliebiger TRX
  gust.py daemon --sim                  gust.py daemon --sim
  14.110,000 MHz USB                    14.110,000 MHz USB
  Kanal per Hash (OE3GAS → Kanal 2)    Kanal per Hash der Gegenstation
  SDRplay RSPdx2 als RX-Monitor         eigener RX-Monitor
```

Beide Stationen benötigen: laufenden `gust.py daemon` mit aktivem RX-Loop,
gleiche Dial-Frequenz (14.110,000 MHz USB), Web-GUI offen, Logging aktiv.

**Phase 1 — Kontrollmessung, verschiedene Kanäle (15 min)**
Beide Stationen senden auf ihren Hash-Kanälen — kein Eingriff.
Baseline: Dekodierrate Frames gesendet vs. beim Gegenüber dekodiert.
Erwartung: ≥ 95 %.

**Phase 2 — Zufällige Kollision, gleicher Kanal (30 min)**
Gegenstation wechselt manuell auf Kanal 2 (`gateway.json: channel: 2`),
behält eigenen `time_offset`. Beide `interval_s = 300`. Kollisionen entstehen
nur wenn Versatz zufällig < 5 s. Beobachtung über ~6 Zyklen.
Erwartung: bei Versatz > 10 s keine Kollision, Dekodierrate ≥ 80 %.

**Phase 3 — Erzwungene Kollision, One-Shot (~10 Versuche)**
Koordiniert per Telefon/Chat: beide senden gleichzeitig (±2 s) per Web-GUI
One-Shot Wetter-Frame auf Kanal 2. Pro Versuch dokumentieren:
- Keiner dekodiert (beide Frames zerstört)
- Einer dekodiert (stärkeres Signal gewinnt)
- Beide dekodieren (Frames in verschiedenen Scan-Fenstern)

**Messprotokoll (pro Versuch):**

| Zeitstempel | Kanal | Station TX | Station RX | Ergebnis | SNR dB | Anmerkung |
|---|---|---|---|---|---|---|
| HH:MM:SS | 2 | OE3GAS | OE1XTU | ✓/✗ | — | |

**Erfolgskriterien:**

| Kriterium | Ziel |
|---|---|
| Dekodierrate Phase 1 (kein Konflikt) | ≥ 95 % |
| Dekodierrate Phase 2 (Versatz > 10 s) | ≥ 80 % |
| Dekodierrate Phase 3 (erzwungen ≤ 2 s) | Messung — kein Zielwert |
| RS-FEC bei Kollision | Entweder sauber dekodiert oder CRC-Fail — kein falsches Ergebnis |

**Erwartetes Ergebnis:** Das deterministische Hash-Scheduling reicht bei
Telemetrie-Intervallen von 5 min und 8 Kanälen für den Praxisbetrieb aus.
Das Ergebnis entscheidet, ob ein CSMA-Mechanismus für eine spätere Version
nötig wäre.

### T-10.4 MeshCom End-to-End (OA) 🔲
LoRa → GUST-Gateway → HF → Remote-Empfänger → MQTT-Echo.

### T-10.5 — Live-Decoder Stresstest via VAC + Deep-Decoder ✅ (Juni 2026)

**Ziel:** Live-Dekodierrate des Daemon-RX (Echtzeit-Ringpuffer) gegen
den Batch-Decoder auf identischer Audio-Quelle messen; Deep-Decoder
(ADR-27) als Nachliefer-Pfad validieren.
**Methode:** Stresstest-WAV (`gust_stresstest.py`, 8 Kanäle) via
Virtual Audio Cable in den laufenden Daemon einspielen;
Session-Recorder (Web-UI, Tab Stresstest) aufzeichnen; Auswertung
gegen Ground-Truth-CSV via `match_live_session()`.
**Erwartung:** ≥ 80 % Dekodierrate (Akzeptanzkriterium).
**Ergebnis:**

| Konfiguration | Rate |
|---|---|
| Short-Decoder allein (9s/2s) | ~54–57 % |
| Short + Deep-Decoder (`rx.deep_decode`) | **86–90 %** ✅ PASS |
| Batch-Referenz (`gust_stress_decode.py`, gleiche WAV) | 88 % |

Befunde dokumentiert: Root-Cause-Eingrenzung `gust_knowledge.md` §23;
BUG-18 (Executor-Konkurrenz), BUG-19 (Dedup-TOL), ADR-26–28.

---

## Phase 9 — Protokoll v0.5 Tests

### T-09.1 — Kanalplan-Regression (8 Kanäle)

**Ziel:** Sicherstellen dass alle neuen Kanäle korrekt moduliert und dekodiert werden.
**Methode:** `python gust_modulator.py` → Selbsttest "Test 7: Alle Kanäle"
**Erwartung:** 8 Kanäle (600, 850, 1100, 1350, 1600, 1850, 2100, 2350 Hz), alle ✓
**Status:** 🔲

### T-09.2 — Costas-SYNC Loopback

**Ziel:** Vollständiger TX→WAV→RX Loopback mit neuem SYNC.
**Methode:** `python gust_modulator.py` Loopback-Test (oder vac_loopback_test.py)
**Erwartung:** Frame korrekt dekodiert, SYNC gefunden, CRC OK, alle Kanäle 0–7
**Status:** 🔲

### T-09.3 — Costas-SYNC Timing-Robustheit

**Ziel:** Sicherstellen dass der neue Sync-Detektor bei beliebigem Sample-Offset funktioniert.
**Methode:** Wie T-ADR-11 — Frame an zufälligen Positionen im Puffer testen
**Erwartung:** Alle Offsets 0–255 Samples dekodierbar
**Status:** 🔲

### T-09.4 — SCORE_MIN Kalibrierung

**Ziel:** Optimalen SCORE_MIN-Wert für Costas-SYNC empirisch bestimmen.
**Methode:** TX mit HackRF bei abnehmenden Gain-Stufen (wie T-10.2), SCORE_MIN variieren
**Erwartung:** SCORE_MIN=0.35 → keine Fehldetektionen bei SNR > 10 dB
**Hinweis:** Falls Fehldetektionen auftreten, SCORE_MIN auf 0.40 erhöhen (CRC fängt Rest ab)
**Status:** 🔲

### T-09.5 — IQ-Eingang Loopback

**Ziel:** gust_iq_rx.py dekodiert eine CF32-Datei korrekt.
**Methode:**
  1. `python gust_tx_test.py --channels 0 3 7 --save-cf32 test.cf32`
  2. `python gust_iq_rx.py --file test.cf32 --freq 14110000`
**Erwartung:** Alle 3 Frames auf Kanälen 0, 3, 7 dekodiert
**Status:** 🔲

### T-09.6 — Equalizer Wirksamkeit

**Ziel:** Equalizer verbessert Dekodierung auf simulierten Randkanälen.
**Methode:** Audio mit künstlichem Hochpass filtern (simuliert SSB-Rolloff), Kanal 0
  dekodieren mit und ohne use_equalizer=True.
**Erwartung:** use_equalizer=True dekodiert auch bei -6 dB Tondämpfung
**Status:** 🔲

---

## Modul 12 — SDR-Profil-System (`gust_soapy_tx.py`, `gust_iq_rx.py`, `gust.py`)

### T-12.1 enumerate_all_devices() — type-Ableitung (UT) 🔲
`enumerate_all_devices()` gibt für RTL-SDR type=`"rx"`,
für HackRF type=`"trx"` zurück (RX+TX-Kanäle korrekt gelesen).
Ohne Hardware: leere Liste, kein Exception.

### T-12.2 _resolve_sdr_tx_cfg() — Priorität (UT) ✅
```python
# active_sdr_tx_profile=null → None
# active_sdr_tx_profile="HackRF" → dict mit driver="hackrf"
# RX-only-Profil als TX → None
# Legacy sdr_tx.enabled=true → dict (Fallback)
```
Alle vier Fälle verifikationsgetestet (Juni 2026).

### T-12.3 build_iq_receiver() — Priorität (UT) ✅
```python
# active_sdr_rx_profile=null → None
# active_sdr_rx_profile="SDRplay" → IQReceiver(driver="sdrplay")
# active_sdr_rx_profile="HackRF" (trx) → IQReceiver(driver="hackrf")
# unbekanntes Profil → None + Warnung
# Legacy rtlsdr.enabled=true → IQReceiver (Fallback)
```
Alle fünf Fälle verifikationsgetestet (Juni 2026).

### T-12.4 SDR-Profil API — CRUD + Schutzregeln (IT) ✅
Live-REST gegen isolierte Config (Port 8181):
- scan (available=false ohne SoapySDR, profiles=2) ✓
- activate rx SDRplay ✓
- activate tx SDRplay → 409 (nicht TX-fähig) ✓
- activate tx HackRF ✓
- delete aktives Profil → 409 ✓
- save RTL-SDR (count=3) ✓
- deactivate rx (null) ✓
- delete RTL-SDR (2 verbleibend) ✓

### T-12.5 IQReceiver SoapySDR Hardware-Test (HT) 🔲
SDRplay RSPdx2 an Python-3.9-Umgebung:
- `active_sdr_rx_profile: "SDRplay"` in gateway.json setzen
- Daemon starten → Log zeigt "IQ-RX-Loop aktiv · SDRplay · 14.110 MHz"
- GUST-Signal auf 14.110 MHz → Frame dekodiert via IQ-Pfad
Voraussetzung: SoapySDR-Bindings + Daemon unter Python 3.9.

---

## Anhang: Testausführung

```bash
# Alle automatisierten Tests Phase 5 (51 Tests)
py test_phase5.py -v

# Connector-Layer Transform-Tests (offline, kein Broker)
py test_transforms.py -v

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
| amqtt (Python, pip) | Lokaler In-Process-Broker für T-9.9 / pytest-Fixture | 🔲 |
| test.mosquitto.org | Öffentlicher Testbroker für T-9.7/T-9.8 (kein Install) | ✅ |
| mosquitto (System) | Optional: lokaler Systembroker (Produktionsbetrieb) | 🔲 |
| rigctld (hamlib) | PTT IC-7610 | ✅ |
| SDRplay RSPdx2 | RX-Referenz On-Air | ✅ |

---

---

## Modul 11 — MeshCore Bridge Tests (MC)

### T-MC-01 Companion Verbindung + Node-Info (IT) ✅
**Datum:** Juni 2026
**Infrastruktur:** Heltec V4 Companion, COM18, Firmware v1.16.0-07a3ca9

**Methode:** `py meshcore_smoketest.py --port COM18 --timeout 30`

**Ergebnis:**
- Verbindung: ✅
- Node-Name: `AT-HL-OE3GAS-🦚` ✅
- Rufzeichen-Extraktion: `OE3GAS` ✅
- pubkey_prefix: `332f9faf62bd` ✅
- Kanäle: 6 (Public, at-hl, hollabrunn, noe, test, vienna) ✅
- CHANNEL_MSG_RECV: nicht getestet (USB/BLE-Einschränkung) ⏭

**Hinweis:** CHANNEL_MSG_RECV-Verifikation erfolgt in T-MC-03.

---

### T-MC-02 Bridge Standalone-Betrieb (IT) ✅
**Datum:** Juni 2026
**Infrastruktur:** Companion COM18, Repeater COM19

**Methode:** `py gust_meshcore_bridge.py --verbose`

**Ergebnis:**
- Verbindung + Kanal-Load: ✅
- Auto-Message-Fetching: ✅
- is_connected als Property (nicht Methode): ✅ (BUG behoben)
- Ctrl+C sauberes Shutdown: ✅

---

### T-MC-03 Ende-zu-Ende: MeshCore → RX_FRAME → WebGUI (IT) ✅
**Datum:** Juni 2026
**Infrastruktur:** Companion COM18, Repeater COM19, OE3TEC als Testpartner

**Methode:**
1. `py gust.py --sim daemon` (gateway.json: meshcore.enabled=true)
2. OE3TEC sendet Textnachricht auf #test
3. Prüfung im WebGUI Monitor-Tab

**Ergebnis:**
- Bridge-Start im Daemon: ✅ "MeshCore Bridge gestartet | Companion: COM18"
- CHANNEL_MSG_RECV empfangen: ✅ (von OE3TEC, ch=test)
- RX_FRAME-Events auf EventBus: ✅ (4 Fragmente)
- Frames im WebGUI sichtbar: ✅
- TEXT ✓ Sammelzeile mit ▶/▼: ✅
- Detail-Modal: Von, An, Kanal, Volltext, Quelle "MeshCore Bridge": ✅
- MC-Badge [MC] lila: ✅

---

### T-MC-04 UTF-8 Fragment-Roundtrip (UT) ✅
**Datum:** Juni 2026
**Infrastruktur:** Keine Hardware

**Methode:** Inline-Test via `py -c "from gust_meshcore_bridge import fragment_text..."`

**Testfälle:**
- Emoji 🧸 in "AT-MA-H1🧸: Test vom Brenntenriegel" → 4 Fragmente, Emoji ganz: ✅
- ASCII-Text → identisch reassembliert: ✅
- Kyrillisch/Griechisch (Μεš⚡Bοt): ✅
- Jedes Fragment ≤ 14 Byte Text: ✅

**Befund:** BUG-MC-03 behoben. chunk_size=14 Bytes (nicht Zeichen),
UTF-8-Zeichengrenzen werden respektiert.

---

### T-MC-05 MC-Badge + Status-Badge WebGUI (IT) ✅
**Datum:** Juni 2026
**Infrastruktur:** Browser, Daemon mit MeshCore-Bridge

**Methode:** Visueller Browser-Test

**Ergebnis:**
- Header-Badge [MC ●] grün wenn Bridge connected: ✅
- Header-Badge [MC ●] unsichtbar wenn enabled=false: ✅
- Lila MC-Badge auf MeshCore-Frames im Feed: ✅
- Lila Randbalken auf MeshCore-Frame-Zeilen: ✅

---

### Offene Tests

| ID | Titel | Voraussetzung |
|---|---|---|
| T-MC-06 | CHANNEL_MSG_RECV bei BLE-Companion | Gerät mit companion_radio_ble |
| T-MC-07 | TX-Pfad GUST → MeshCore | P6-20 implementiert |
| T-MC-08 | Repeater-Steuerung via CLI | P6-22 dokumentiert |
| T-MC-09 | Auto-Reconnect nach Verbindungsabbruch | Companion kurz trennen |

---

*Dokument: gust_testplan.md*
*Autor: OE3GAS*
*Stand: Juni 2026 — T-10.5 Live-Decoder VAC-Stresstest + Deep-Decoder (86–90 % PASS) · Modul 12 SDR-Profile-System (T-12.1–T-12.5); davor: Phase 7 Empfänger-Robustheit + SNR-Baseline (T-10.2); Modul 9 Connector Layer / MQTT (T-9.1–T-9.10): 3-Ebenen-Teststrategie (offline / öffentlicher Broker / amqtt lokal)*
*Gilt für: Phase 1–10*