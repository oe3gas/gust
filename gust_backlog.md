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

## PHASE 6 — MQTT-Bridge (optional)

| ID | Prio | Typ | Titel | Beschreibung | Status |
|---|---|---|---|---|---|
| P6-01 | 🟡 | feature | `gust_mqtt.py` — MQTTBridge | Event-Bus-Subscriber: RX-Frames → gust/rx/* publishen | 🔲 |
| P6-02 | 🟡 | feature | MQTT TX-Input | gust/tx/* subscriben → TX-Queue einreihen | 🔲 |
| P6-03 | 🟢 | docs | MQTT Topic-Schema dokumentieren | Vollständige Topic-Liste, Payload-Schemata, Beispiele | 🔲 |
| P6-04 | 🟢 | feature | Home Assistant Integration testen | MQTT Discovery-Format für HA-Sensoren | 🔲 |
| P6-05 | 🟢 | feature | Node-RED Flow-Beispiel | Beispiel-Flow: GUST RX → Dashboard | 🔲 |

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

---

## PHASE 8 — Dokumentation & Veröffentlichung

| ID | Prio | Typ | Titel | Beschreibung | Status |
|---|---|---|---|---|---|
| P8-01 | 🟡 | docs | Protokollspezifikation finalisieren | Vollständiges Markdown-Dokument v0.3, publikationsreif | 🔲 |
| P8-02 | 🟡 | docs | Installationsanleitung RPi | Schritt-für-Schritt: OS, Python, Hardware, gateway.json | 🔲 |
| P8-03 | 🟢 | docs | GitHub Repository aufsetzen | OE3GAS/oe3mode, README, Lizenz CC BY-SA 4.0 | 🔲 |
| P8-04 | 🟢 | docs | ÖVSV-Präsentation vorbereiten | Folien für OE-Community, Protokollvorstellung | 🔲 |
| P8-05 | 🟢 | docs | Protokoll bei ÖVSV einreichen | Offizielle Registrierung als OE-Digitalmode | 🔲 |

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
| IDEA-05 | feature | SDR-Monitor-Modus | Direkter IQ-Eingang von SDRplay/RTL-SDR ohne Soundkarte |
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

---

*Dokument: gust_backlog.md*
*Autor: OE3GAS*
*Stand: Mai 2026 — Phase 7 Empfänger-Robustheit + SNR-Baseline abgeschlossen; P7-05/H/I/J/K/L/M/N erledigt*