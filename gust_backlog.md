# GUST — Backlog
**OE3GAS — Generic Universal Shortwave Telemetry**
*Stand: Juni 2026 — i18n DE/EN (P5-21) · Inbox-Kosmetik fehlende Frames (P5-22) · CLI-Verbesserungen · QSO-Modus · TRX-Profile · BUG-10 root fix · gust_tx_test Fixes*

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
| P5-13 | 🟡 | refactor | Web-UI Config-Tab: Strukturierung in Unterseiten | Sub-Navigation mit 4 Unterseiten: Audio & PTT, Transceiver (Hamlib), SDR-TX (SoapySDR), Darstellung. Aktiver Sub-Tab in localStorage. | ✅ |
| P5-14 | 🟡 | feature | Web-UI: Hamlib/rigctld-Konfiguration | 6 Endpunkte: ports/models/status/start/stop/config. COM-Port-Dropdown, Rig-Modell-Suche, Baudrate, Auto-Start, Verbinden & Testen. | ✅ |
| P5-15 | 🟡 | feature | Web-UI: Tune-Button | 15-s-Sinuston (1000 Hz) mit PTT via Hamlib, Countdown-Anzeige, vorzeitiger Stop, Polling-Pause während TX. | ✅ |
| P5-16 | 🟡 | feature | Web-UI: Kommunikations-Tab | Inbox (RX-Freitext mit Fragment-Reassembly), Gesendet-Liste (tx_done), Sub-Navigation RX/TX. | ✅ |
| P5-17 | 🟡 | feature | Web-UI: Aktivitätslog + ON AIR Banner | Echtzeit-Systemlog via /ws/log, ON AIR Banner während TX. | ✅ |
| P5-18 | 🟡 | feature | TRX-Profile: Mehrgeräteverwaltung | `gateway.json` speichert Array `trx_profiles` + `active_trx_profile`. Web-UI Dropdown → `POST /api/trx/activate` → schreibt rigctld/audio-Block + stößt conflict-aware rigctld-Neustart an. Profil-Anlage manuell in gateway.json. Erstes Profil wird beim Hamlib-Config-Speichern automatisch angelegt. Rückwärtskompatibel. Felder: `name`, `rig_model`, `device`, `baud`, `audio_device_tx`, `audio_device_rx`, `ptt_backend`, `auto_start`. | ✅ |
| P5-19 | 🟡 | feature | Web-UI: QSO-Modus (Freitext-Schnellsendung) | Toggle im Text-Formular verkürzt Fragment-Intervall von txInterval (300 s) auf 60 s. Nur über Web-UI aktivierbar — bewusst nicht in gateway.json/API (Designentscheidung: nur für interaktiven Ham-Betrieb, nicht für automatischen Betrieb). Bleibt aktiv bis manuell deaktiviert. Dokumentiert in gust_spec.md §3.4. | ✅ |
| P5-20 | 🟡 | feature | Web-UI: TX-Warteschlange löschen | Button „✕ Warteschlange löschen" über der TX-Queue-Anzeige. `DELETE /api/tx/queue` → `TxGateway.clear_queue()` → gibt Anzahl gelöschter Frames zurück. Frames die gerade gesendet werden bleiben unberührt. | ✅ |
| P5-21 | 🟢 | feature | Web-UI: Mehrsprachigkeit (i18n DE/EN) | `locales/de.json` + `locales/en.json` (194 Keys). `/api/lang/<code>`-Endpunkt. JS-Mechanismus `t('key')`, `loadLang()`, `applyI18n()` mit `data-i18n` / `data-i18n-html` / `data-i18n-placeholder`. Sprachschalter im Darstellung-Tab, `localStorage`-Persistenz. | ✅ |
| P5-22 | 🟢 | feature | Web-UI: Inbox-Kosmetik fehlende Frames | Fehlende Frames im Nachrichtentext als `[…fehlt…]`-Badge (inline, orange gestrichelt). Nachrichtentext in `var(--text)` und `var(--fs-lg)` für bessere Lesbarkeit. | ✅ |

### Umsetzungsnotiz P5-13 / P5-14 (Mai 2026)

Alle Änderungen ausschließlich in `gust_web.py`; Spec in `gust_spec.md §4.4` ergänzt.

- **Sub-Navigation** im Config-Tab umgesetzt mit 4 Unterseiten: **Audio & PTT**
  (inkl. PTT-Lead/Tail), **Transceiver (Hamlib)**, **SDR-TX (SoapySDR)**,
  **Darstellung** (Theme/Schrift). Aktiver Sub-Tab in `localStorage`
  (`gust_cfg_subtab`), System-Status-Grid bleibt dauerhaft oben sichtbar.
- **6 Hamlib-Endpunkte** wie spezifiziert implementiert
  (`ports`/`models`/`status`/`start`/`stop`/`config`); `_handle_hamlib_*`-Methoden
  + `self._rigctld_proc`-Handle. End-to-End gegen Standalone-Server getestet.
- **Abweichungen:** Gateway- und Connectors-Sektion zurückgestellt; Hamlib-Unterseite
  immer sichtbar (kein Auto-Ausblenden); models-Label = vollständige rigctld-Zeile.

### Umsetzungsnotiz P5-18 / P5-19 / P5-20 (Juni 2026)

- **TRX-Profile (P5-18):** JSON-Schema mit `audio_device_tx`/`audio_device_rx` statt
  einzelnem `audio_device` — unterstützt unterschiedliche TX/RX-Soundkarten pro Gerät.
  `_handle_trx_activate()` schreibt audio.device + rx.device separat. Conflict-aware
  rigctld-Restart über gemeinsamen `_restart_or_report_conflict()`-Flow.
- **QSO-Modus (P5-19):** `_nextCycleSecs()` verwendet im QSO-Modus festes 60-s-Raster
  (kein Offset-Phasenversatz). Confirm-Dialog zeigt korrekte Zeitschätzung. CSS `.hidden`-
  Bug gefixt (generische Regel ergänzt). Designentscheidung in gust_spec.md §3.4 dokumentiert.
- **Warteschlange löschen (P5-20):** `TxGateway.clear_queue()` gibt Anzahl zurück;
  `DELETE /api/tx/queue` in gust_spec.md §4.4 dokumentiert.

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

### Transform-Bibliothek Erweiterungen

| ID | Prio | Typ | Titel | Beschreibung | Status |
|---|---|---|---|---|---|
| P6-10 | 🟡 | feature | `weather_from_ecowitt` — Froggit/Ambient/Fine-Offset | Transform für Ecowitt-Protokoll. PASSKEY wird aktiv verworfen und nie geloggt. | 🔲 |
| P6-11 | 🟡 | feature | `field_map` — generischer YAML-Transform (Stufe 2) | Transform ohne Python-Code via connectors.yaml Key-Mapping + optionalem scale-Faktor. | 🔲 |

### Test-Infrastruktur

| ID | Prio | Typ | Titel | Beschreibung | Status |
|---|---|---|---|---|---|
| P6-12 | 🟡 | feature | `test_transforms.py` — offline Unit-Tests | Vollständige Unit-Tests für alle Transform-Funktionen ohne Broker. | 🔲 |
| P6-13 | 🟡 | feature | `amqtt` pytest-Fixture für Integrationstests | In-Process-Broker-Fixture für pytest, Port 18830. | 🔲 |

### Ergänzend geplante Connectors (Phase 8/9)

| ID | Typ | Titel | Beschreibung |
|---|---|---|---|
| P6-14 | feature | `WebhookConnector` | aiohttp POST-Handler, kein Broker nötig |
| P6-15 | feature | `MeshtasticConnector` | LoRa-Mesh-Bridge (siehe P7-09); from_call aus Node-ID |
| P6-16 | feature | `APRSConnector` | APRS-IS oder TNC; `position_from_aprs_json` bereits in gust_transforms.py |

---

## PHASE 7 — On-Air Tests & Signalqualität ← TEILWEISE ABGESCHLOSSEN

| ID | Prio | Typ | Titel | Beschreibung | Status |
|---|---|---|---|---|---|
| P7-01 | 🔴 | research | Bandplan OE prüfen | §16 AFG geklärt: GUST-Aussendungen sind lizenzkonform. Testfrequenzen in gust_spec.md §8. | ✅ |
| P7-02 | 🔴 | feature | TX-Hardware-Verdrahtung in gust.py | cmd_tx(): gust_frame → gust_modulator → gust_audio verketten | ✅ |
| P7-03 | 🔴 | feature | Symbol-Windowing Raised Cosine | Flanken glätten, Spectral Leakage reduzieren | ✅ |
| P7-04 | 🔴 | feature | SoapySDR TX-Pfad (generisch) | `gust_soapy_tx.py`, Device-Discovery, ADR-16. | ✅ |
| P7-05 | 🔴 | research | SNR-Schwelle messen | HackRF TX-Gain-Stepping; Decode-Schwelle ≤ 10 dB SNR ermittelt → T-10.2. | ✅ |
| P7-06 | 🟡 | feature | Erster On-Air-Test | IC-7610 TX → SDRplay RX, Frame dekodieren | ✅ |
| P7-07 | 🟡 | research | Preamble-Länge optimieren | Geschlossen: 256 ms ausreichend, Costas-Array verbessert SYNC-Qualität | ✅ |
| P7-08 | 🟡 | research | Kollisionstest mit OE1XTU | Zwei Stationen, gleicher Kanal, Frameverlustrate messen. Testplan T-10.3 vollständig ausgearbeitet (Juni 2026). | 🔲 |
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
| P7-H | feature | Frequenz-Fein-Refinement | `_refine_sync()`: f0 von 8-Hz-Raster auf < 1 Hz nachschärfen |
| P7-I | feature | Scan-Range-Erweiterung | Breitband-Scan 320–2760 Hz statt 380–2580 Hz |
| P7-J | feature | Halb-Block-Timing-Auflösung | SYNC-Suche im 128-Sample-Raster + Sample-genaues Timing-Refinement |
| P7-K | feature | Kontinuierlicher RX-Loop | `gust_rx.py`: asyncio-Scan-Loop über Ringpuffer, Dedup-Cache, EventBus-Anbindung |
| P7-L | feature | HackRF Dual-Kanal-TX + Parallelkanal | `transmit_iq()`: zwei Kanäle gleichzeitig; Diversity-Gewinn bestätigt |
| P7-M | bug | HackRF TX-Underrun | Default-Timeout-Fix, originale Write-Loop |
| P7-N | feature | `tx_test.py` Mess-Skript | TX-Testharness: Einzel-/Dual-Kanal, `--channels`, `--gain-sequence`, CSV-Log |
| P7-O | feature | Scan-Obergrenze 2760→2900 Hz | Kanal-9-Offset-Symmetrie ≥ +200 Hz |

---

## PHASE 9 — Protokoll v0.5: Costas-SYNC · Kanalreduktion · IQ-Eingang ✅ ABGESCHLOSSEN

| ID | Prio | Typ | Titel | Beschreibung | Status |
|---|---|---|---|---|---|
| P9-01 | 🔴 | feature | Kanalplan v0.5 | `CHANNEL_BASE_HZ=600`, `N_CHANNELS=8`; Scan-Range 500–2510 Hz; Protokoll-Break dokumentiert | ✅ |
| P9-02 | 🔴 | feature | Costas-8 SYNC | `SYNC_SYMBOLS=[2,0,6,7,1,4,3,5]`; 8-Ton-Scoring; Selbsttest angepasst | ✅ |
| P9-03 | 🟡 | feature | Passband-Equalizer | `_build_equalizer()` + `_fft_detect_symbol_eq()` | ✅ |
| P9-04 | 🟡 | feature | `gust_iq_rx.py` — IQ-Eingang | RTL-SDR Filterbank, Breitband-Modus, `IQReceiver` asyncio-Klasse | ✅ |
| P9-05 | 🟡 | feature | Web-UI 8-Kanal-Grid | CSS Grid; Kanalplan 600–2600 Hz in `buildChannelGrid()` | ✅ |
| P9-06 | 🟢 | docs | Spec v0.5 | §3.1/§3.2 Kanalplan, §3.3 Costas-SYNC, §3.x IQ-Eingang | ✅ |
| P9-07 | 🟢 | docs | Knowledge-Update Phase 9 | gust_knowledge.md: Costas-Abschnitt, IQ-Abschnitt, Connector-Konzept-Übersicht | ✅ |
| P9-08 | 🔴 | feature | GitHub Repository | OE3GAS/gust initialisiert, committed + gepusht (Juni 2026) | ✅ |

---

## PHASE 8 — Dokumentation & Veröffentlichung

| ID | Prio | Typ | Titel | Beschreibung | Status |
|---|---|---|---|---|---|
| P8-01 | 🟡 | docs | Protokollspezifikation finalisieren | Vollständiges Markdown-Dokument v0.5, publikationsreif | 🔲 |
| P8-02 | 🟡 | docs | Installationsanleitung RPi | Schritt-für-Schritt: OS, Python, Hardware, gateway.json | 🔲 |
| P8-03 | 🟢 | docs | GitHub Repository aufsetzen | OE3GAS/gust, README, Lizenz CC BY-SA 4.0 | ✅ |
| P8-04 | 🟢 | docs | ÖVSV-Präsentation vorbereiten | Folien für OE-Community, Protokollvorstellung | 🔲 |
| P8-05 | 🟢 | docs | Protokoll bei ÖVSV einreichen | Offizielle Registrierung als OE-Digitalmode | 🔲 |
| P8-06 | 🟡 | research | Kanalplan vs. SSB-Passband | Entschieden: 8 Kanäle 600–2600 Hz, v0.5. Umgesetzt in P9-01. | ✅ |
| P8-07 | 🟡 | feature | Docker-Deployment | `Dockerfile`, `docker-compose.yml`, `docker-entrypoint.sh`, `.dockerignore` im Repo-Root. Simulator-Modus out-of-the-box, Rufzeichen per `GUST_CALLSIGN`-Umgebungsvariable. Healthcheck auf `/api/status`. | ✅ |
| P8-08 | 🟢 | docs | Docker RPi Audio-Passthrough | Anleitung für `--device /dev/snd` und USB-Audio in Docker auf Raspberry Pi ergänzen (für Hardware-TX im Container-Betrieb). | 🔲 |

---

## BEKANNTE BUGS & TECHNISCHE SCHULDEN

| ID | Prio | Typ | Titel | Details | Status |
|---|---|---|---|---|---|
| BUG-01 | 🟡 | bug | Rufzeichen > 6 Zeichen werden gekürzt | VK2XX/P → VK2XX/ — Suffix /P geht verloren. Fix: 1-Byte-Suffix-Feld | ⏸ Phase 8 |
| BUG-02 | 🟢 | bug | inspectrum Frequenzachse verschoben | ~600 Hz Offset bei 8 kHz SR — Darstellungsartefakt, keine Auswirkung | ⏸ |
| BUG-03 | 🟢 | bug | CF32-Export zeigt Rest-Spiegelbild | Randeffekt durch Stille-Abschnitte nach Hilbert-Transform | ⏸ |
| BUG-04 | 🟡 | refactor | RS-FEC ineffizient für kurze Frames | RS(255,223): immer 32 Byte Overhead. RS(31,15) wäre effizienter | 🔲 Phase 8 |
| BUG-05 | 🟢 | refactor | asyncio.get_event_loop() deprecated | Python 3.10+: auf get_running_loop() umstellen. **Fix (Juni 2026):** alle Vorkommen in `gust.py` (4 Stellen) ersetzt. | ✅ |
| BUG-06 | 🟡 | bug | SNR-Schätzer falsch an unterer Bandkante | Fix: adaptives Rauschband beidseitig mit 80 Hz Guard. | ✅ |
| BUG-07 | 🟢 | research | Simplex-Fenstertiming-Miss | Fix: Fixed-Cadence-Scheduling + Fenster 9 s. Simulation: 10,55% → 0% Miss. | ✅ |
| BUG-08 | 🟢 | refactor | Frame-Contention bei dichter Folge | Zwei Frames im selben 8s-Fenster: Single-Pass-Auswahl — zweiter kann verloren gehen | 🔲 |
| BUG-09 | 🟡 | bug | Freitext-Längenlimit nur clientseitig | 56-Byte-/4-Frame-Limit nur in Web-UI durchgesetzt. Bewusst offengelassen. | 🔲 |
| BUG-10 | 🔴 | bug | rigctld nicht neu gestartet nach TRx-Wechsel | **Fix (Juni 2026, zweiteilig):** (1) `_handle_hamlib_config`: prüft via `_find_port_owner()` ob PID == `self._rigctld_proc.pid` → eigener Prozess → stiller Neustart, kein Konflikt-Dialog. (2) Root cause: `create_ptt()` in `gust_audio.py` merkt Popen-Handle an `ptt._rigctld_proc`; `cmd_daemon()` in `gust.py` übergibt Handle an `server._rigctld_proc` nach `gateway.start()`. | ✅ |
| BUG-11 | 🟡 | bug | Hamlib-Status-Dot nach Profilwechsel rot | rigctld-Status-Poll sofort nach Neustart schlägt fehl (rigctld noch hochfahrend). **Fix:** `_testHamlibDelayed(2000)` — 2 s Delay vor Poll; Tune-Button während Delay deaktiviert. | ✅ |
| BUG-12 | 🟡 | bug | gust_tx_test.py: ConnectionRefused nach rigctld-Test | Windows-rigctld schließt TCP nach jedem Kommando. `ensure_rigctld_running()` + `HamlibPTT._connect()` öffnen zwei Verbindungen in zu kurzem Abstand → ConnectionRefused. **Fix:** 300 ms sleep zwischen `ensure_rigctld_running()` und `HamlibPTT()`-Instanziierung. Außerdem: hardcodierter Fehlertext (`/dev/ttyUSB0`) durch generischen ersetzt; `load_gateway_config()` liest jetzt auch den `rigctld`-Block. | ✅ |
| BUG-13 | 🔴 | bug | TRX-Profil: Felder werden nach Aktivieren nicht aktualisiert | `activateTrxProfile()` prüfte `if (r.ok)` — bei rigctld-Startfehler blieb das Formular auf altem Profil. **Fix:** `if (!r.conflict)` statt `if (r.ok)` — Formular wird immer aktualisiert wenn kein Port-Konflikt vorliegt. | ✅ |
| BUG-14 | 🔴 | bug | TRX-Profil: Konflikt-Dialog beim Aktivieren | `_handle_trx_activate()` rief `_restart_or_report_conflict()` auf — beim Profil-Wechsel erschien Konflikt-Dialog statt stillem Neustart. **Fix:** direkter Aufruf von `_do_rigctld_restart()` in `_handle_trx_activate()`. | ✅ |
| BUG-15 | 🔴 | bug | rigctld wird beim Daemon-Start nicht gestartet | rigctld startete erst lazy beim ersten TX (build_ptt im Worker). Frequenz-Polling und PTT nicht sofort verfügbar. **Fix:** Früh-Start-Block in `cmd_daemon()` nach `gateway.start()` — startet rigctld sofort bei `ptt_backend=hamlib` + `auto_start=true`. | ✅ |
| BUG-16 | 🔴 | bug | Windows IPv4/IPv6-Konflikt: rigctld auf ::1, Python verbindet auf 127.0.0.1 | Windows löst `localhost` als `::1` (IPv6) auf. rigctld bindet auf `::1`, `HamlibPTT` verbindet auf `127.0.0.1` → ConnectionRefused obwohl rigctld läuft. **Fix:** `RIGCTLD_HOST_DEFAULT = "127.0.0.1"` in `gust_audio.py`; `ensure_rigctld_running()` ersetzt `localhost` → `127.0.0.1` beim rigctld-Start (`-T`-Flag). Diagnose: `netstat -ano | findstr ":4532"` zeigt `[::1]:4532` statt `0.0.0.0:4532`. | ✅ |
| BUG-17 | 🟡 | bug | Tune: doppelter rigctld-Start | `_handle_tx_tune._run()` rief `ensure_rigctld_running()` auf — auf Windows (nur 1 TCP-Conn) entstand ein zweiter rigctld-Prozess wenn Polling-Loop gerade verbunden war. **Fix:** `ensure_rigctld_running()` aus Tune entfernt; Tune verlässt sich auf Früh-Start in `cmd_daemon()`. | ✅ |

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
| ~~IDEA-09~~ | feature | ~~Mehrsprachige Web-UI~~ | ~~DE/EN Sprachauswahl~~ → **Umgesetzt als P5-21** |
| IDEA-10 | feature | AX.25-Kompatibilität | FROM/TO-Felder AX.25-kompatibel für Rückwärtskompatibilität |

---

## ABGESCHLOSSENE ITEMS (Auswahl Phase 7)

| ID | Phase | Typ | Titel | Abgeschlossen |
|---|---|---|---|---|
| ✅ P7-02 | 7 | feature | TX-Hardware-Verdrahtung gust.py | Mai 2026 |
| ✅ P7-03 | 7 | feature | Symbol-Windowing Raised Cosine | Mai 2026 |
| ✅ P7-06 | 7 | feature | Erster On-Air-Test 14.110 MHz | Mai 2026 |
| ✅ P7-04 | 7 | feature | Generischer SoapySDR-TX-Backend (`gust_soapy_tx.py`, ADR-16) | Mai 2026 |
| ✅ P7-05 | 7 | research | SNR-Baseline gemessen (Simplex + Dual, Gain 28→1) | Mai 2026 |
| ✅ P5-15 | 5 | feature | Tune-Button (15-s Sinuston, PTT, Countdown) | Mai 2026 |
| ✅ P5-16 | 5 | feature | Kommunikations-Tab (Inbox + Gesendet) | Mai 2026 |
| ✅ P5-17 | 5 | feature | Aktivitätslog + ON AIR Banner | Mai 2026 |
| ✅ P5-18 | 5 | feature | TRX-Profile Mehrgeräteverwaltung | Juni 2026 |
| ✅ P5-19 | 5 | feature | QSO-Modus (60 s Fragment-Intervall, Web-UI only) | Juni 2026 |
| ✅ P5-20 | 5 | feature | TX-Warteschlange löschen (DELETE /api/tx/queue) | Juni 2026 |
| ✅ BUG-10 | — | bug | rigctld-Konflikt-Dialog bei eigenem Prozess + root cause fix | Juni 2026 |
| ✅ BUG-11 | — | bug | Hamlib-Status-Dot nach Profilwechsel (2s-Delay) | Juni 2026 |
| ✅ BUG-12 | — | bug | gust_tx_test.py Windows-TCP-Race + hardcodierter Fehlertext | Juni 2026 |
| ✅ P5-21 | 5 | feature | Web-UI i18n DE/EN (194 Keys, /api/lang, localStorage) | Juni 2026 |
| ✅ P5-22 | 5 | feature | Inbox-Kosmetik: fehlende Frames als […fehlt…]-Badge | Juni 2026 |
| ✅ BUG-05 | — | refactor | asyncio.get_event_loop() → get_running_loop() in gust.py | Juni 2026 |
| ✅ BUG-13 | — | bug | TRX-Profil: Formular-Aktualisierung nach Aktivieren | Juni 2026 |
| ✅ BUG-14 | — | bug | TRX-Profil: Konflikt-Dialog beim Profil-Wechsel entfernt | Juni 2026 |
| ✅ BUG-15 | — | bug | rigctld Früh-Start beim Daemon-Start | Juni 2026 |
| ✅ BUG-16 | — | bug | Windows IPv4/IPv6-Konflikt rigctld/HamlibPTT | Juni 2026 |
| ✅ BUG-17 | — | bug | Tune: doppelter rigctld-Prozess verhindert | Juni 2026 |

---

## ARCHITEKTURENTSCHEIDUNGEN (ADR)

### ADR-01 bis ADR-06 (Phase 1–5) — unverändert, siehe vorherige Version

### ADR-07: Protokoll v0.3 — 8-Symbol SYNC + CHANNEL-Byte ✅
Protokoll-Break bewusst akzeptiert. SYNC auf 8 Symbole verlängert, CHANNEL-Byte im Header.

### ADR-08: Audio-Level per gateway.json + CLI ✅
Wert > 1 = Prozent (50 → 0.5). Referenzwert IC-7610: ACC Input 40%, Software Level 10%.

### ADR-09: Geräteadressierung per ID statt Name ✅
Windows meldet dasselbe Gerät dreimal (MME/DS/WASAPI). Geräte-ID in gateway.json.

### ADR-10: SNR-Test via HackRF Gain-Stepping ✅
Decode-Schwelle ≤ 10 dB SNR. Dual-Kanal 100%, Simplex ~90%.

### ADR-11: Decoder-Robustheit — Frequenz- + Timing-Refinement ✅
`_refine_sync()`: f0 < 1 Hz, Timing sample-genau. Sandbox: 1/5 → 10/10.

### ADR-12: Parallelkanal-Diversity (Dual-Kanal-TX) ✅
`transmit_iq()` mischt zwei IQ-Signale. Simplex ~90% → Dual 100% Dekodierrate.

### ADR-13: HackRF TX — Default-Timeout zwingend ✅
Langer Timeout → TX-Underrun → Firmware-Hänger. Default-Timeout + originale Write-Loop.

### ADR-14: Protokoll v0.5 — Kanalplan 8 Kanäle + Costas-SYNC + IQ-Eingang ✅
Kanäle 0+9 (alt) im SSB-Filterrolloff → entfernt. 8 Kanäle 600–2600 Hz. Costas-Array [2,0,6,7,1,4,3,5].

### ADR-15: Connector Layer — Semantic Bridging statt simpler MQTT-Brücke
GustConnector ABC + ConnectorRegistry + SemanticMapping (YAML) + Transform-Bibliothek.

### ADR-16: SoapySDR TX — Geräteauswahl per Discovery, kein Hardcoding
Nur `SoapySDR.Device.enumerate()`. Device-Args (driver+serial) in gateway.json, nicht Index.

### ADR-17: QSO-Modus — nur Web-UI, kein gateway.json/API ✅ (Juni 2026)
GUST ist primär ein Telemetrie-Protokoll. Der QSO-Modus (60 s Fragment-Intervall) ist
ausschließlich über den Web-Client aktivierbar — bewusst nicht in gateway.json, REST-API
oder CLI exponiert. Automatische Stationen sollen ihn nicht aktivieren können (Duty Cycle).
Dokumentiert in gust_spec.md §3.4.

### ADR-18: TRX-Profile — audio_device_tx/rx statt audio_device ✅ (Juni 2026)
Profile verwenden separate Felder `audio_device_tx` → `audio.device` und
`audio_device_rx` → `rx.device`, weil TX- und RX-Soundkarten pro TRX unterschiedlich
sein können (z.B. IC-7610: TX 14, RX 2; FT-818: TX 10, RX 5).

### ADR-19: rigctld-Handle-Weitergabe (BUG-10 root cause) ✅ (Juni 2026)
`create_ptt()` in `gust_audio.py` merkt Popen-Handle an `ptt._rigctld_proc`.
`cmd_daemon()` übergibt Handle nach `gateway.start()` an `server._rigctld_proc`.
Damit erkennt `_handle_hamlib_config()` GUST-eigene rigctld-Prozesse und zeigt
keinen Konflikt-Dialog. Option A (Handle-Plumbing) gegenüber C (Modul-Global) bevorzugt.

### ADR-20: i18n — externe JSON-Locale-Dateien statt hartkodierter Strings ✅ (Juni 2026)
Alle UI-Strings in `locales/de.json` + `locales/en.json` ausgelagert (194 Keys).
Server liefert `/api/lang/<code>` aus. JS lädt beim Start die passende Datei,
`applyI18n()` setzt `data-i18n`-Attribute via `textContent` (Plaintext) oder
`innerHTML` (formatierte Infotexte via `data-i18n-html`). Sprachauswahl in
`localStorage` persistent. Neue Sprachen: nur JSON-Datei hinzufügen, kein Code ändern.

### ADR-21: TRX-Profil-Wechsel — direkter _do_rigctld_restart, kein Konflikt-Dialog ✅ (Juni 2026)
Beim Profil-Wechsel via `_handle_trx_activate()` wird rigctld immer still neu gestartet
(`_do_rigctld_restart()` direkt), ohne Konflikt-Dialog. Der laufende rigctld (egal ob
GUST-eigen oder extern) wird in `_do_rigctld_restart()` via psutil beendet.
Konflikt-Dialog bleibt nur für manuelles Speichern (`_handle_hamlib_config()`).

### ADR-22: rigctld Früh-Start in cmd_daemon ✅ (Juni 2026)
rigctld wird beim Daemon-Start sofort gestartet (nicht lazy beim ersten TX).
Bedingung: `ptt_backend=hamlib` + `rigctld.auto_start=true` + kein dry_run.
Handle → `server._rigctld_proc` damit `_managed_rigctld_pid()` den Prozess kennt.

### ADR-23: RIGCTLD_HOST_DEFAULT = "127.0.0.1" ✅ (Juni 2026)
Windows löst `localhost` als `::1` (IPv6) auf; Python-Sockets verbinden auf
`127.0.0.1` (IPv4) → Mismatch → ConnectionRefused obwohl rigctld läuft.
Fix: `RIGCTLD_HOST_DEFAULT = "127.0.0.1"` in `gust_audio.py` als Modul-Konstante.
Alle rigctld-Starts via `ensure_rigctld_running()` verwenden `127.0.0.1` statt `localhost`.
Dokumentiert in `gust_knowledge.md` §20.

### ADR-24: Docker-Basis-Image python:3.11-slim ✅ (Juni 2026)
`python:3.11-slim` als Basis gewählt (nicht Alpine): Alpine verwendet musl-libc,
PortAudio und `sounddevice` sind dort schwer zu kompilieren. `slim` ist kompakt
genug (~180 MB Image) und vermeidet Kompatibilitätsprobleme mit nativen Libs.

---

*Dokument: gust_backlog.md*
*Autor: OE3GAS*
*Stand: Juni 2026 — BUG-13–17 TRX-Profil/Tune/rigctld-Fixes · ADR-21–24 · Docker-Deployment (P8-07) · requirements.txt-Fix (aiohttp)*