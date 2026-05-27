# GUST — Backlog
**OE3GAS — Offene Aufgaben, Ideen und bekannte Probleme**
*Stand: Mai 2026 — Phase 7 Empfänger-Robustheit + SNR-Baseline abgeschlossen*

---

## Legende

**Priorität:** 🔴 Hoch · 🟡 Mittel · 🟢 Niedrig
**Status:** 🔲 Offen · 🚧 In Arbeit · ✅ Erledigt · ⏸ Zurückgestellt
**Typ:** `feature` · `bug` · `refactor` · `research` · `docs`

---

## PHASE 5 — Web-Interface & Event-Bus ✅ ABGESCHLOSSEN

| ID | Prio | Typ | Titel | Beschreibung | Status |
|---|---|---|---|---|---|
| P5-01 | 🔴 | feature | `gust_eventbus.py` | asyncio Fan-out Event-Bus, TTL-Filterung, EventBusLogHandler | ✅ |
| P5-02 | 🔴 | feature | `gust_msg_simulator.py` | SimAdapter alle 4 Frame-Typen + Prios, FileAdapter Stub | ✅ |
| P5-03 | 🔴 | feature | `gust_web.py` — aiohttp Server | AppRunner, Port 8080, 0.0.0.0, Task-Start-Guard | ✅ |
| P5-04 | 🔴 | feature | `gust_web.py` — REST API | /api/status, /api/config, /api/tx/*, /api/log | ✅ |
| P5-05 | 🔴 | feature | `gust_web.py` — WebSocket | /ws/rx, /ws/log | ✅ |
| P5-06 | 🔴 | feature | Web-UI Vanilla JS | Monitor, TX-Formular, Status, Log — 4 Tabs | ✅ |
| P5-07 | 🔴 | feature | `gust.py` — CLI | daemon, rx, tx, info, devices — 13 Parser-Tests | ✅ |
| P5-08 | 🟡 | feature | Web-UI: Kanalübersicht | 10-Kanal-Grid, ★ Heimatkanal, Aktivitäts-Flash | ✅ |
| P5-09 | 🟡 | feature | API-Key Authentifizierung | X-API-Key + Bearer Token, WS via ?api_key= | ✅ |
| P5-10 | 🟡 | refactor | Event-Bus in Gateway | Verdrahtung in demo_wiring.py + gust.py daemon | ✅ |
| P5-11 | 🟢 | feature | Web-UI: Dark + Light Theme | Dark Amber + Light Clean, localStorage | ✅ |
| P5-12 | 🟢 | feature | Web-UI: Frame-History | deque(maxlen=50), /api/log beim Laden, /ws/rx Echtzeit | ✅ |

---

## PHASE 6 — Connector Layer + MQTT-Bridge ← KONZEPT FERTIG, IMPLEMENTIERUNG OFFEN

Dieses Phase-6-Konzept wurde durch `gust_connector_konzept.md` erweitert.
Statt einer einfachen MQTT-Brücke wird ein vollständiger Connector Layer
implementiert, der semantisches Bridging für beliebige externe Protokolle
ermöglicht. Siehe §11 im Connector-Konzept-Dokument.

| ID | Prio | Typ | Titel | Beschreibung | Status |
|---|---|---|---|---|---|
| P6-01 | 🟡 | feature | `gust_mqtt.py` — MQTTConnector Outbound | RX_FRAME Events → SemanticMapping.map_outbound() → MQTT publish; ersetzt alte MQTTBridge-Outbound-Logik | 🔲 |
| P6-02 | 🟡 | feature | `gust_mqtt.py` — MQTTConnector Inbound | MQTT subscribe → SemanticMapping.map_inbound() → CONNECTOR_RX Event → TX-Queue | 🔲 |
| P6-03 | 🟡 | docs | `connectors.yaml` — Topic-Schema | Vollständige YAML-Konfiguration: Broker, Inbound-Rules, Outbound-Templates; ersetzt separate Topic-Doku | 🔲 |
| P6-04 | 🟢 | feature | Home Assistant Integration | Transform `weather_from_ha_json`; HA Auto-Discovery via `homeassistant/sensor/gust_*/config` | 🔲 |
| P6-05 | 🟢 | feature | Node-RED Flow-Beispiel | Beispiel-Flow: `gust/rx/*` → Dashboard; unverändert im Scope | 🔲 |
| P6-06 | 🟡 | feature | `gust_connector.py` — ABC + Registry | `GustConnector` Abstract Base Class + `ConnectorRegistry`; Grundlage für alle Connector-Implementierungen | 🔲 |
| P6-07 | 🟡 | feature | `gust_transforms.py` — Transform-Bibliothek | `weather_from_ha_json`, `position_from_aprs_json`, `passthrough`, `sensor_from_json`; `SemanticMapping` YAML-Loader + Matcher | 🔲 |
| P6-08 | 🟡 | feature | `connectors.yaml` — Konfigurations-Schema | YAML-Schema mit Inbound/Outbound-Regeln, topic-Wildcard-Matching, from_call-Templates | 🔲 |
| P6-09 | 🟢 | feature | `gust_eventbus.py` — CONNECTOR_RX EventType | Neue EventType-Konstante `CONNECTOR_RX = "connector_rx"`; MQTT_RX bleibt als Alias | 🔲 |

### Ergänzend geplante Connectors (Phase 8/9)

| ID | Typ | Titel | Beschreibung |
|---|---|---|---|
| P6-10 | feature | `WebhookConnector` | aiohttp POST-Handler, kein Broker nötig |
| P6-11 | feature | `MeshtasticConnector` | LoRa-Mesh-Bridge (siehe P7-09); from_call aus Node-ID |
| P6-12 | feature | `APRSConnector` | APRS-IS oder TNC; `position_from_aprs_json` bereits in gust_transforms.py |

---

## PHASE 7 — On-Air Tests & Signalqualität ← TEILWEISE ABGESCHLOSSEN

| ID | Prio | Typ | Titel | Beschreibung | Status |
|---|---|---|---|---|---|
| P7-01 | 🔴 | research | Bandplan OE prüfen | §16 AFG geklärt: GUST-Aussendungen sind lizenzkonform als Datenübertragung im digitalen Sub-Band eingestuft. Testfrequenzen dokumentiert in gust_spec.md §8. | ✅ |
| P7-02 | 🔴 | feature | TX-Hardware-Verdrahtung in gust.py | cmd_tx(): gust_frame → gust_modulator → gust_audio verketten | ✅ |
| P7-03 | 🔴 | feature | Symbol-Windowing Raised Cosine | Flanken glätten, Spectral Leakage reduzieren | ✅ |
| P7-04 | 🔴 | feature | Soapy7610 TX-Pfad | IC-7610 direktes IQ-TX via SoapySDR | 🔲 |
| P7-05 | 🔴 | research | SNR-Schwelle messen | HackRF TX-Gain-Stepping (Gain-Folge via `tx_test.py --gain-sequence`); empirische Baseline ermittelt → siehe T-10.2 | ✅ |
| P7-06 | 🟡 | feature | Erster On-Air-Test | IC-7610 TX → SDRplay RX, Frame dekodieren | ✅ |
| P7-07 | 🟡 | research | Preamble-Länge optimieren | 8 Symbole (256 ms) — ausreichend für KW? Weitere Tests nötig | 🔲 |
| P7-08 | 🟡 | research | Kollisionstest mit OE1XTU | Zwei Stationen, gleicher Kanal, Frameverlustrate | 🔲 |
| P7-09 | 🟢 | feature | MeshCom End-to-End Test | LoRa → GUST-Gateway → HF → Remote → LoRa | 🔲 |
| P7-10 | 🟢 | research | Demodulator GNU Radio OOT | Python-Demodulator als GNU Radio OOT-Block portieren | 🔲 |

### Zusätzlich in Phase 7 erledigt (nicht im ursprünglichen Backlog)

| ID | Typ | Titel | Beschreibung |
|---|---|---|---|
| P7-A | feature | Protokoll v0.3 | 8-Symbol SYNC + CHANNEL-Byte im Frame-Header |
| P7-B | feature | Breitband-SYNC-Erkennung | `_find_sync_wideband()`: automatische Kanal- + Offseterkennung |
| P7-C | feature | `load_wav()` Resampling | uint8-Support + automatisches Resampling beliebiger Sample Rates |
| P7-D | bug | PTT Triple-Release Fix | `_active`-Flag in allen PTT-Backends, idempotentes `release()` |
| P7-E | bug | RS-Decoder Loop Range Fix | Breitband-Modus: Loop bis `rs_min` statt nur 9 Schritte |
| P7-F | feature | CLI-Verbesserungen | `--dry-run`, `--callsign`, `--device`, `--level` nach Subcommand |
| P7-G | feature | gateway.json level-Normalisierung | Wert > 1 wird als % interpretiert (50 → 0.5) |
| P7-H | feature | Frequenz-Fein-Refinement | `_refine_sync()`: f0 von 8-Hz-Raster auf < 1 Hz nachschärfen (Single-Bin-Energiemaximum) |
| P7-I | feature | Scan-Range-Erweiterung | Breitband-Scan 320–2760 Hz statt 380–2580 Hz → Kanal 9 (2650 Hz) jetzt erfasst |
| P7-J | feature | Halb-Block-Timing-Auflösung | SYNC-Suche im 128-Sample-Raster + Sample-genaues Timing-Refinement → Frames an beliebiger Pufferposition dekodierbar |
| P7-K | feature | Kontinuierlicher RX-Loop | `gust_rx.py`: asyncio-Scan-Loop über Ringpuffer, Dedup-Cache, EventBus-Anbindung |
| P7-L | feature | HackRF Dual-Kanal-TX + Parallelkanal | `transmit_iq()`: zwei Kanäle gleichzeitig senden; Diversity-Gewinn im SNR-Test bestätigt |
| P7-M | bug | HackRF TX-Underrun | Langer `writeStream`-Timeout (1 s) verursachte Firmware-Hänger → Default-Timeout, originale Write-Loop |
| P7-N | feature | `tx_test.py` Mess-Skript | TX-Testharness: Einzel-/Dual-Kanal, `--channels`, `--gain-sequence`, CSV-Log |
| P7-O | feature | Scan-Obergrenze 2760→2900 Hz | Breitband-Scan in `_find_sync_candidates` erweitert → Kanal 9 erhält symmetrische positive Offset-Toleranz (vorher Einbruch ab +140 Hz, jetzt ≥ +200 Hz). Keine Regression (Kanäle 0–9 weiter 20/20 in AWGN). Behebt NUR den Offset-Anteil des Kanal-9-Problems, nicht den Filter-Cut → siehe ADR-14. |

---

## PHASE 9 — Protokoll v0.5: Costas-SYNC · Kanalreduktion · IQ-Eingang ✅ ABGESCHLOSSEN

| ID | Prio | Typ | Titel | Beschreibung | Status |
|---|---|---|---|---|---|
| P9-01 | 🔴 | feature | Kanalplan v0.5 | `CHANNEL_BASE_HZ=600`, `N_CHANNELS=8`; Scan-Range 500–2510 Hz; Protokoll-Break dokumentiert | ✅ |
| P9-02 | 🔴 | feature | Costas-8 SYNC | `SYNC_SYMBOLS=[2,0,6,7,1,4,3,5]`; 8-Ton-Scoring in `_find_sync_candidates()`; Selbsttest angepasst | ✅ |
| P9-03 | 🟡 | feature | Passband-Equalizer | `_build_equalizer()` + `_fft_detect_symbol_eq()`; `demodulate(use_equalizer=True)` | ✅ |
| P9-04 | 🟡 | feature | `gust_iq_rx.py` — IQ-Eingang | RTL-SDR Filterbank, Breitband-Modus, `IQReceiver` asyncio-Klasse; CF32-Datei-Dekodierung | ✅ |
| P9-05 | 🟡 | feature | Web-UI 8-Kanal-Grid | CSS Grid 5→4 Spalten; Kanalplan 600–2600 Hz in `buildChannelGrid()` | ✅ |
| P9-06 | 🟢 | docs | Spec v0.5 | §3.1/§3.2 Kanalplan, §3.3 Costas-SYNC, §3.x IQ-Eingang | ✅ |
| P9-07 | 🟢 | docs | Knowledge-Update Phase 9 | gust_knowledge.md: Costas-Abschnitt, IQ-Abschnitt, Connector-Konzept-Übersicht | ✅ |
| P9-08 | 🔴 | feature | GitHub Repository | OE3GAS/gust initialisieren, README.md, .gitignore, Commit | ✅ |

### Aus Feature-Ideen übernommen

| ID | Aktion |
|---|---|
| IDEA-05 | → P9-04 (SDR-Monitor-Modus) ✅ umgesetzt |
| P7-07   | → Geschlossen: Preamble-Länge bleibt 256 ms; SYNC-Qualität durch Costas-Array verbessert |

---

## PHASE 8 — Dokumentation & Veröffentlichung

| ID | Prio | Typ | Titel | Beschreibung | Status |
|---|---|---|---|---|---|
| P8-01 | 🟡 | docs | Protokollspezifikation finalisieren | Vollständiges Markdown-Dokument v0.3, publikationsreif | 🔲 |
| P8-02 | 🟡 | docs | Installationsanleitung RPi | Schritt-für-Schritt: OS, Python, Hardware, gateway.json | 🔲 |
| P8-03 | 🟢 | docs | GitHub Repository aufsetzen | OE3GAS/gust, README, Lizenz CC BY-SA 4.0 | ✅ |
| P8-04 | 🟢 | docs | ÖVSV-Präsentation vorbereiten | Folien für OE-Community, Protokollvorstellung | 🔲 |
| P8-05 | 🟢 | docs | Protokoll bei ÖVSV einreichen | Offizielle Registrierung als OE-Digitalmode | 🔲 |
| P8-06 | 🟡 | research | Kanalplan vs. SSB-Passband entscheiden | Kanal 9 (obere Töne bis 2868,75 Hz) wird vom Rig-SSB-Filter gecuttet → OTA CRC-Fail. Optionen A–D in **ADR-14** abgewogen → **Entscheidung gefallen** (Variante C-Ableitung: 8 Kanäle 600–2600 Hz, v0.5). Umgesetzt in P9-01. | ✅ |

---

## BEKANNTE BUGS & TECHNISCHE SCHULDEN

| ID | Prio | Typ | Titel | Details | Status |
|---|---|---|---|---|---|
| BUG-01 | 🟡 | bug | Rufzeichen > 6 Zeichen werden gekürzt | VK2XX/P → VK2XX/ — Suffix /P geht verloren. Fix: 1-Byte-Suffix-Feld | ⏸ Phase 8 |
| BUG-02 | 🟢 | bug | inspectrum Frequenzachse verschoben | ~600 Hz Offset bei 8 kHz SR — Darstellungsartefakt, keine Auswirkung | ⏸ |
| BUG-03 | 🟢 | bug | CF32-Export zeigt Rest-Spiegelbild | Randeffekt durch Stille-Abschnitte nach Hilbert-Transform | ⏸ |
| BUG-04 | 🟡 | refactor | RS-FEC ineffizient für kurze Frames | RS(255,223): immer 32 Byte Overhead. RS(31,15) wäre effizienter | 🔲 Phase 8 |
| BUG-05 | 🟢 | refactor | asyncio.get_event_loop() deprecated | Python 3.10+: auf get_running_loop() umstellen (Meshtastic-Adapter) | 🔲 |
| BUG-06 | 🟡 | bug | SNR-Schätzer falsch an unterer Bandkante | Rauschreferenzband 200–380 Hz überlappte Kanal-0-Töne (400+ Hz) → SNR-Anzeige bis ~30 dB zu niedrig (negativ trotz sauberem Decode). **Fix:** Rauschband adaptiv relativ zum Signalband [f0, f0+218,75 Hz], beidseitig mit 80 Hz Guard gemessen, niedrigere Schätzung gewinnt (kontaminierte Seite verworfen). Alle Kanäle jetzt konsistent, volle Dynamik erhalten. | ✅ |
| BUG-07 | 🟢 | research | Simplex-Fenstertiming-Miss | Ohne Diversity gelegentlich (~1/6–1/11) ein Frame verloren: erschien nur in 2–3 Scanfenstern, keines sauber ausgerichtet bevor er aus dem Puffer rollte. **Ursache:** Schleife schlief `interval` und scannte DANN — die variable Decode-Zeit (0,5–1,3 s) blähte das effektive Capture-Intervall auf bis 3,3 s > Containment-Marge (8−5,4=2,6 s) → Lücke. **Fix:** Fixed-Cadence-Scheduling (`next_tick += interval`, Decode wird in den Sleep absorbiert → effektives Intervall = 2,0 s konstant) + Fenster 8→9 s + Startup-Garantieprüfung. Simulation: ALT 10,55% Miss → NEU 0%. | ✅ |
| BUG-08 | 🟢 | refactor | Frame-Contention bei dichter Folge | Zwei Frames im selben 8s-Fenster: Single-Pass-Auswahl rastet auf den höher bewerteten, der zweite kann verloren gehen | 🔲 |
| BUG-09 | 🟡 | bug | Freitext-Längenlimit nur clientseitig | Das 56-Byte-/4-Frame-Limit für 0x40-Freitext wird **nur in der Web-UI** durchgesetzt (`sendTx()`-`alert()` + Byte-Counter). `fragment_text()` hat **keine Obergrenze** und zerlegt beliebig langen Text in `ceil(len/14)` Fragmente; das Wire-Format trägt via 4-Bit-Gesamtfeld bis zu **16** Fragmente. Direkte API-/CLI-Aufrufe (`/api/tx/text`, `gust.py tx`) umgehen die Grenze. **Bewusst offengelassen** (kein Server-Hard-Limit gewünscht) — relevant, falls künftige Frame-Varianten serverseitige Fragmentgrenzen brauchen: dann zentral in `fragment_text()` (Parameter `max_fragments`) lösen. | 🔲 |

---

## FEATURE-IDEEN (unpriorisiert)

| ID | Typ | Titel | Beschreibung |
|---|---|---|---|
| IDEA-01 | feature | APRS-Gateway | GUST-Position-Frames als APRS-Pakete weiterleiten (aprs.fi sichtbar) |
| IDEA-02 | feature | Winlink-Integration | GUST als alternativer Transportkanal für Winlink-Nachrichten |
| IDEA-03 | feature | Frequenz-Agility | Automatischer Kanalwechsel bei hoher Kollisionsrate (CSMA-ähnlich) |
| IDEA-04 | feature | GPS-Direktanbindung | NMEA-Stream von USB-GPS direkt als Positions-Frame-Quelle |
| ~~IDEA-05~~ | feature | ~~SDR-Monitor-Modus~~ | ~~Direkter IQ-Eingang von SDRplay/RTL-SDR ohne Soundkarte~~ → **Umgesetzt als P9-04** |
| IDEA-06 | feature | Frame-Statistik-Dashboard | Langzeit-Statistik: Frames/Tag, Kanalbelegung, Top-Stationen |
| IDEA-07 | feature | htmx/Alpine.js Migration | Web-UI reaktiver machen ohne Build-System |
| IDEA-08 | research | GUST auf VHF/UHF | Gleiche Protokollschicht, andere Frequenzarchitektur für 2m/70cm |
| IDEA-09 | feature | Mehrsprachige Web-UI | DE/EN Sprachauswahl |
| IDEA-10 | feature | AX.25-Kompatibilität | FROM/TO-Felder AX.25-kompatibel für Rückwärtskompatibilität |

---

## ABGESCHLOSSENE ITEMS (Auswahl Phase 7)

| ID | Phase | Typ | Titel | Abgeschlossen |
|---|---|---|---|---|
| ✅ P7-02 | 7 | feature | TX-Hardware-Verdrahtung gust.py | Mai 2026 |
| ✅ P7-03 | 7 | feature | Symbol-Windowing Raised Cosine | Mai 2026 |
| ✅ P7-06 | 7 | feature | Erster On-Air-Test 14.110 MHz | Mai 2026 |
| ✅ P7-A  | 7 | feature | Protokoll v0.3 (SYNC 8 Symbole + CHANNEL-Byte) | Mai 2026 |
| ✅ P7-B  | 7 | feature | Breitband-SYNC-Detektor | Mai 2026 |
| ✅ P7-C  | 7 | feature | load_wav() Resampling + uint8 | Mai 2026 |
| ✅ P7-D  | 7 | bug | PTT Triple-Release Fix | Mai 2026 |
| ✅ P7-E  | 7 | bug | RS-Decoder Loop Range Fix | Mai 2026 |
| ✅ P7-F  | 7 | feature | CLI --dry-run/--callsign/--device/--level nach Subcommand | Mai 2026 |
| ✅ P7-G  | 7 | feature | gateway.json level-Normalisierung | Mai 2026 |
| ✅ P7-05 | 7 | research | SNR-Baseline gemessen (Simplex + Dual, Gain 28→1) | Mai 2026 |
| ✅ P7-H  | 7 | feature | Frequenz-Fein-Refinement (_refine_sync) | Mai 2026 |
| ✅ P7-I  | 7 | feature | Scan-Range 320–2760 Hz (Kanal 9 erfasst) | Mai 2026 |
| ✅ P7-J  | 7 | feature | Halb-Block-Timing-Auflösung + Sample-Refinement | Mai 2026 |
| ✅ P7-K  | 7 | feature | Kontinuierlicher RX-Loop (gust_rx.py) | Mai 2026 |
| ✅ P7-L  | 7 | feature | HackRF Dual-Kanal-TX + Parallelkanal-Diversity | Mai 2026 |
| ✅ P7-M  | 7 | bug | HackRF TX-Underrun (Default-Timeout-Fix) | Mai 2026 |
| ✅ P7-N  | 7 | feature | tx_test.py Mess-Skript (--channels, --gain-sequence) | Mai 2026 |
| ✅ P7-O  | 7 | feature | Scan-Obergrenze 2760→2900 Hz (Kanal-9-Offset-Symmetrie) | Mai 2026 |

---

## ARCHITEKTURENTSCHEIDUNGEN (ADR)

### ADR-01 bis ADR-06 (Phase 1–5) — unverändert, siehe vorherige Version

### ADR-07: Protokoll v0.3 — 8-Symbol SYNC + CHANNEL-Byte ✅
Motivation: On-Air-Tests zeigten dass der Decoder den Kanal vorab kennen
musste und kein Frequenzversatz toleriert wurde. Lösung: SYNC auf 8 Symbole
verlängert (Δf = 218,75 Hz kanalunabhängig → Breitband-Erkennung möglich),
CHANNEL-Byte im Header (Konsistenzprüfung TX ↔ RX). Protokoll-Break bewusst
akzeptiert (noch nicht veröffentlicht).

### ADR-08: Audio-Level per gateway.json + CLI ✅
NF-Pegel am IC-7610-ACC-Eingang stark abhängig von der Transceiver-Konfiguration
(ACC/USB Input Level). Daher konfigurierbar statt hardcodiert.
Normalisierung: Wert > 1 = Prozent (50 → 0.5), Wert ≤ 1 = bereits normalisiert.
Referenzwert IC-7610: ACC Input 40%, Software Level 10%.

### ADR-09: Geräteadressierung per ID statt Name ✅
Windows meldet dasselbe Audiogerät dreimal mit verschiedenen APIs
(MME, DirectSound, WASAPI). sounddevice wirft Fehler bei Namensübergabe.
Lösung: Geräte-ID (Integer) in gateway.json. `py gust.py devices` zeigt IDs.

### ADR-10: SNR-Test via HackRF Gain-Stepping ✅ (durchgeführt)
Kein Abschwächer vorhanden. Stattdessen: HackRF TX-Gain in 1-dB-Schritten,
IC-7610 als RX-Referenz (USB-Audio, Gerät 1), `gust_rx.py` als kontinuierlicher
Decoder. Mess-Skript: `tx_test.py --gain-sequence`. Ergebnis siehe Testplan T-10.2.
**Kernergebnis:** Decoder dekodiert bis mindestens 10,1 dB angezeigtem SNR (Dual),
der TX-Boden (1 dB Gain) wurde erreicht *bevor* der Decoder aussetzte — die echte
Schwelle liegt also ≤ 10 dB SNR. Absolutwert setup-spezifisch (starke Kopplung),
aber als Praxisaussage „zuverlässig bis 10 dB SNR" belastbar.

### ADR-11: Decoder-Robustheit — Frequenz- + Timing-Refinement ✅
Motivation: Live-Tests dekodierten trotz hohem SNR nur ~1/5 Frames. Drei separate
Ursachen identifiziert: (1) 8-Hz-Frequenzraster traf nie den wahren f0 → Symbol-
Detektion verfehlt; (2) Scan-Range endete bei 2580 Hz, Kanal 9 (2650 Hz) lag außerhalb;
(3) block-ausgerichtete SYNC-Suche → bis ±128 Samples Timing-Fehler, halbes Symbol
versetzte Frames scheiterten. Lösung: `_refine_sync()` schärft f0 (< 1 Hz) und Timing
(sample-genau) nach, Scan-Range auf 320–2760 Hz erweitert, SYNC-Suche auf Halb-Block-
Auflösung (128 Samples). Sandbox: 1/5 → 10/10 Kanäle + alle Sample-Offsets. Live: 5/5.

### ADR-12: Parallelkanal-Diversity (Dual-Kanal-TX) ✅
Motivation: Bei On-Air-QRM kann ein einzelner Kanal von einer Störung getroffen werden.
Lösung: Notfall-Frames können gleichzeitig auf zwei NF-Kanälen gesendet werden
(`transmit_iq()` mischt zwei IQ-Signale). Der RX dekodiert beide als getrennte
Kandidaten; der erste mit CRC-OK gewinnt, der zweite wird vom Dedup unterdrückt.
Tradeoff: Sendeleistung wird auf zwei Kanäle aufgeteilt (~6 dB weniger pro Kanal),
dafür zwei unabhängige Empfangschancen. Empirisch: Simplex ~90% Dekodierrate,
Dual 100% (15/15) — die Redundanz fängt Fenstertiming-Aussetzer ab.

### ADR-13: HackRF TX — Default-Timeout zwingend ✅
Ein langer `writeStream`-Timeout (1 s) in `transmit_iq()` verursachte beim ersten
Lauf einen TX-Underrun, der die HackRF-Firmware in einem festgefahrenen Zustand
hinterließ (exakt 1.966.080 Samples akzeptiert, dann Stillstand, brummender Träger).
Danach schlugen alle Sendungen fehl bis zum USB-Neustart. Lösung: Default-Timeout
verwenden, Write-Loop exakt wie die funktionierende `transmit()` (`pos += sr.ret if
sr.ret > 0 else BLOCK`). Diagnose-Tool `hackrf_diag.py` mit Watchdog-Timer half beim
Aufspüren.

### ADR-14: Protokoll v0.5 — Kanalplan 8 Kanäle + Costas-SYNC + IQ-Eingang ✅
**Status:** Entschieden und umgesetzt (Phase 9, Mai 2026). Löst den ursprünglichen
Entwurf (Kanal-9/SSB-Passband-Kante) und P8-06 auf.

**Problem (OTA Mai 2026):** Kanal 9 wird über die Luft nur selten dekodiert.
Empirische Analyse (AWGN- + Filtersimulation, Kanal 9 vs. Kanal 2):
- In **reiner AWGN** dekodiert Kanal 9 identisch zu Kanal 2 (bis ≤ 0 dB SNR, 20/20). **Kein Decoder-/Logik-Bug.**
- Mit **steilem SSB-Filter (Höhencut ~2700 Hz)** fällt Kanal 9 auf ~22/30; mit zusätzlich +60 Hz Dial-Offset → ~0/30.
- Die Fehlerart ist **immer CRC-Fail, nie „kein-SYNC"**: SYNC wird zuverlässig gefunden,
  aber die oberen Daten-Töne von Kanal 9 (Ton 5/6/7 bei 2806/2837/**2868,75** Hz) liegen
  am/über der Kante eines typischen 2,4–2,7-kHz-SSB-Filters. Das Rig dämpft sie weg →
  FFT-argmax liest die Symbole falsch → RS-FEC überlastet → CRC-Fail. **Verlorene HF-Energie,
  die der Decoder nicht zurückholen kann.**

Ursache ist also der Kanalplan (400–2900 Hz nutzt mehr Bandbreite, als ein Standard-
SSB-Filter durchlässt), nicht die Software. Ein bereits umgesetzter Decoder-Fix
(P7-O: Breitband-Scan-Obergrenze 2760 → 2900 Hz) behebt nur die *Offset*-bedingten
Ausfälle (Kanal-9-Offset-Toleranz jetzt symmetrisch ≥ +200 Hz), nicht den Filter-Cut.

**Abgewogene Optionen (Entwurf):** A — Status quo + Betriebshinweis (kein Break);
B — Kanalplan nach unten schieben (Basis 300 Hz, 10 Kanäle, Break); C — auf 9 Kanäle
reduzieren (Kanal 9 streichen, Break + `SHA-256 % 9`); D — engerer Tonabstand
(größerer DSP-Eingriff, Orthogonalität gefährdet). Die umgesetzte v0.5 ist eine
konsequentere Ableitung von C: **beide** Randkanäle (0+9 alt) entfernt.

**Motivation:** On-Air-Tests und Analyse zeigten dass Kanäle 0 und 9 (alt: 400 Hz /
2650–2900 Hz) im SSB-Filterrolloff lagen (bis −10 dB). Der alternierende SYNC
[7,0,7,0,7,0,7,0] verwendete nur 2 von 8 Tönen — kein Equalizer möglich und
suboptimale Autokorrelation. IQ-Eingang war als IDEA-05 bereits priorisiert.
**Entscheidung:** Sofortiger Schnitt auf 8 Kanäle (600–2600 Hz, SSB-Plateau ±0,5 dB),
Ersatz durch verifiziertes Costas-Array der Ordnung 8 ([2,0,6,7,1,4,3,5]),
additiver IQ-Eingang als neues Modul gust_iq_rx.py. Protokoll-Break auf v0.5
akzeptiert — GitHub-Repository noch nicht angelegt (P8-03 offen), kein
Rückwärtskompatibilitätsproblem. Decoder-Eingriff: minimal — nur SYNC_SYMBOLS-
Konstante + Sync-Scoring-Algorithmus in _find_sync_candidates(), Datendekodierung
vollständig unverändert.

### ADR-15: Connector Layer — Semantic Bridging statt simpler MQTT-Brücke
Motivation: P6-01/02 (einfache MQTT-Brücke) ist semantisch blind — externer JSON
landet als Rohbytes, der Gateway muss den Typ erraten. Für Home Assistant,
APRS, Meshtastic usw. braucht es eine echte Übersetzungsschicht.
Entscheidung: Neues Abstraktionsmodell (gust_connector_konzept.md):
GustConnector ABC + ConnectorRegistry + SemanticMapping (YAML-konfiguriert) +
Transform-Bibliothek. Frame-Layer und Event-Bus bleiben vollständig unverändert.
P6-01 bis P6-05 bleiben als Items, werden aber inhaltlich auf das neue Modell
angehoben. Neue Items P6-06 bis P6-09 für die Basisschicht.

---

*Dokument: gust_backlog.md*
*Autor: OE3GAS*
*Stand: Mai 2026 — Phase 9 (Protokoll v0.5: Costas-SYNC, Kanalplan 8 Kanäle 600–2600 Hz, IQ-Eingang) abgeschlossen; ADR-14 entschieden & umgesetzt, P8-06 geschlossen; Phase 7 (Empfänger-Robustheit + SNR-Baseline) abgeschlossen*