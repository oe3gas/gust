# GUST — Backlog
**OE3GAS — Generic Universal Shortwave Telemetry**
*Stand: Juni 2026 — ADR-29–32 SDR-Profil-System · Phase 10 History & Logging (P10-01/P10-02) · Swimlane: laufende Zeitachse, Canvas-Scroll, Pause-Snapshot, Frame-Klick*

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

MeshCore-Anbindung (P6-17–P6-19) erfolgt via USB-Serial-Bridge + lokalem MQTT-Broker
als zentrale Drehscheibe — kein WiFi-Companion erforderlich (kein fertiges Binary für
Heltec V4, Stand Juni 2026). Siehe gust_knowledge.md §31.

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

### MeshCore-Anbindung (USB-Serial + MQTT-Bridge)

| ID | Prio | Typ | Titel | Beschreibung | Status |
|---|---|---|---|---|---|
| P6-17 | 🟢 | feature | MeshCore↔GUST Gateway | Bidirektionale Bridge zwischen MeshCore LoRa-Mesh und GUST HF-Backbone. Architektur: `gust_meshcore_bridge.py` (Serial→MQTT) + `MeshCoreConnector` in `gust_connector.py` (MQTT→Event-Bus). Transforms: `text_from_meshcore()`, `position_from_meshcore()`, `meshcore_from_text()`. MQTT als zentrale Drehscheibe — alle weiteren Connector-Anbindungen nutzen dieselbe Infrastruktur. Voraussetzung: P6-18 (mosquitto), P6-19 (Bridge), P6-06 (GustConnector ABC), P6-01/02 (MQTTConnector). | 🔲 |
| P6-18 | 🟢 | feature | MQTT-Broker Setup (mosquitto) | mosquitto als zentrale MQTT-Drehscheibe auf GUST-Gateway (RPi oder PC). Installation, Konfiguration, Autostart (systemd). `gateway.json`-Erweiterung: `"mqtt": {"host": "127.0.0.1", "port": 1883}`. Basis für alle Connector-Anbindungen (MeshCore, Wetterstation, APRS, Meshtastic). Testbar mit `mosquitto_pub`/`mosquitto_sub`. | 🔲 |
| P6-19 | 🟢 | feature | `gust_meshcore_bridge.py` | Eigenständiges Bridge-Skript: liest MeshCore-Companion-Node per USB-Serial (`/dev/ttyUSB0` bzw. `COMx`, Bibliothek: `meshcore` + `pyserial`), parsed eingehende Nachrichten (Text, Position, Telemetry) und publiziert auf MQTT-Topics (`meshcore/rx/text`, `meshcore/rx/position`). Abonniert `meshcore/tx/text` und schreibt ausgehende Nachrichten per Serial auf den Node. Konfigurationsblock in `gateway.json`: `"meshcore_bridge": {"port": "/dev/ttyUSB0", "baudrate": 115200, "broker_host": "127.0.0.1", "broker_port": 1883}`. Autostart als systemd-Service auf RPi. Abhängigkeit: P6-18. **Umgesetzt Juni 2026:** Standalone-Modus (StandaloneEventBus) + Daemon-Integration (P6-19b: meshcore-Task in `cmd_daemon`, gesteuert via `gateway.json` `"meshcore.enabled"`). WebGUI-Integration: MC-Badge `[MC]` lila im Feed, STATUS-Badge im Header, TEXT-Fragment-Collapsing mit ▶/▼, Detail-Modal. Konfiguration: `meshcore.json`. Hinweis: tatsächlich EventBus-direkt statt MQTT realisiert (MQTT-Pfad → P6-20). | ✅ |
| P6-21 | 🟡 | feature | `meshcore_smoketest.py` — Companion Smoke-Test | Verbindung COM18, self_info, get_channel(i)-Loop, CHANNEL_MSG_RECV. PASS Juni 2026. | ✅ |

### MeshCore — Offene Punkte (aus Session Juni 2026)

| ID | Prio | Typ | Titel | Beschreibung | Status |
|---|---|---|---|---|---|
| P6-20 | 🟢 | feature | MeshCore TX-Pfad: GUST → MQTT → MeshCore | GUST RX_FRAME (TEXT) → MQTTConnector Outbound → MQTT: `meshcore/tx/text` → `gust_meshcore_bridge.py` → USB-Serial → Heltec V4 → LoRa-Netz. Voraussetzung: P6-19 ✅, P6-01 (MQTTConnector Outbound). Hinweis: zwei `fragment_text`-Versionen (`gust_frame.py` + Bridge-Fallback) — beide bei Änderungen synchron halten. | 🔲 |
| P6-22 | 🟢 | feature | Repeater-Steuerung via Text-CLI | Zweiter Heltec V4 (COM19) läuft als Repeater-Firmware. Steuerung nur via `meshcore-cli -r -s COM19` (Text-CLI). Nicht über meshcore-Python-Library ansprechbar. Dokumentation der CLI-Befehle in `gust_knowledge.md`. | 🔲 |

### MeshCore — Bugs (aus Session Juni 2026)

| ID | Typ | Beschreibung | Status |
|---|---|---|---|
| BUG-MC-01 | bug | `self_info` hat keinen `ver`-Key — Firmware-Version aus `send_device_query().payload['ver']` lesen, nicht aus self_info | 🔲 |
| BUG-MC-02 | bug | `get_channel(i)`-Loop bricht nicht bei leeren Slots ab — Abbruch wenn `channel_name == ""` implementieren | 🔲 |
| BUG-MC-03 | bug | UTF-8-Schnitt mitten in Multibyte-Zeichen (Emoji) — **BEHOBEN** in `gust_meshcore_bridge.py` (Juni 2026). `fragment_text()` in `gust_frame.py` noch nicht gefixt (chunk_size=14 Zeichen statt Bytes). | ✅ Bridge-Fix |
| BUG-MC-04 | bug | Channel-Messages haben kein `pubkey_prefix` im Wire-Format — Sender-Auflösung basiert auf Kontaktliste. Bei unbekanntem Sender: Fallback auf `unknown_sender_policy`. Kein Bug, dokumentiertes MeshCore-Verhalten. | ℹ️ Won't fix |

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
| P8-10 | 🟢 | research | FEC-Backend-Abstraktion + LDPC-Evaluierung | Flanschbares FEC-Modul, RS(255,223) bleibt produktiver Default. Nicht vor v1.0 (Protokollbruch). Details siehe unten; ADR-25. | 🔲 Phase 8/9 |
| P8-11 | 🟢 | feature | AUTH 0x50 — HMAC-SHA256 Frame-Authentifizierung (GUST-S) | Bilaterale Authentifizierung: 0x50 AUTH-Frame (REF_SEQ + REF_TYPE + KEY_ID + HMAC-16 = 20 B), HMAC-SHA256 truncated, 60-s-Replay-Fenster über REF_SEQ, Schlüsselverwaltung in gateway.json. Spec §3.4/§3.5, gust_knowledge.md §28. Gegenstück: AUTH_EX (P8-12). Nicht vor v1.0. | 🔲 |
| P8-12 | 🟢 | feature | GUST-X Protokollvariante — 9-Symbol-SYNC + LDPC + 44B Payload | GUST-X v1 implementieren: 9-Symbol-SYNC Erkennung, LDPC n=256 Integration, Timestamp-Pflichtfeld, neue Frame-Typen 0x81-0x87 (inkl. AUTH_EX 2-Frame ECDSA), gateway.json `protocol.variant`. Voraussetzung: Soft-Output-Demodulator (P8-13). Spec §3.9, ADR-37. | 🔲 |
| P8-13 | 🟢 | research | Soft-Output-Demodulator für LDPC | _fft_detect_symbol() gibt LLR-Array statt Hard-Symbol zurück. Bin-Energien → bitweise Log-Likelihood-Ratios. Voraussetzung für LDPC SNR-Gewinn. | 🔲 |

### P8-10: FEC-Backend-Abstraktion + LDPC-Evaluierung

**Status:** Etappen 1+2 abgeschlossen. Etappe 3 zurückgestellt.

**Was bereits implementiert ist:**
- `gust_fec.py` — FECBackend-Interface + ReedSolomonFEC-Wrapper ✅
- `gust_ldpc.py` (n=48, Rate 3/4) — korrekt, kein SNR-Vorteil ✅
- Blocklängen-Evaluation (ldpc_planung/) — abgeschlossen ✅

**Kernergebnis der Evaluation (Juni 2026):**
LDPC ist auf dem Hard-Decision-Pfad für jede Blockgröße schlechter
als RS(255,223). Kein SNR-Gewinn ohne Soft-Output-Demodulator.
Details: `gust_knowledge.md` §27,
         `ldpc_planung/ldpc_blocklen_eval_ergebnis.md`

**Voraussetzung für Etappe 3 (Integration):**
1. Soft-Output-Demodulator: `_fft_detect_symbol()` → LLR statt Hard-Bytes
2. gust_ldpc.py neu parametrisieren: n=256, Rate 3/4 (Sweetspot)
3. Frame-Aggregation für kurze Payloads

**Nicht vor v1.0** — erfordert Protokollbruch + Frame-Header-Versionierung.

**Status Etappe 3:** `cc_ldpc_etappe3_integration.md` ist freigegeben.
Alle Voraussetzungen erfuellt (P8-13 abgeschlossen, n=255 symbol-aligned).

**Referenz:** ADR-25, gust_knowledge.md §22+§27,
             ldpc_planung/ldpc_blocklen_eval_ergebnis.md

---

### P8-11: AUTH 0x50 — HMAC-SHA256 Frame-Authentifizierung (GUST-S)

**Ziel:** Bilaterale Authentifizierung für geschlossene Gruppen mit
gemeinsamem Schlüssel. Gegenstück zum öffentlich verifizierbaren
AUTH_EX (0x85/0x86, P8-12).

**Frame-Layout (0x50, 20 Byte Payload — füllt GUST-S exakt):**
- REF_SEQ (2 B)   — Sequenznummer des authentifizierten Daten-Frames
- REF_TYPE (1 B)  — Frame-Typ des Daten-Frames
- KEY_ID (1 B)    — Schlüssel-Identifier
- HMAC (16 B)     — HMAC-SHA256(key, body + REF_SEQ) truncated

**Implementierungsschritte:**
1. `gust_frame.py`: Frame-Typ 0x50, encode/decode AUTH-Payload
2. `gust_frame.py`: `auth_tag()` / `verify_auth()` (HMAC-SHA256, 16 B)
3. Schlüsselverwaltung in `gateway.json` (KEY_ID → shared key, nie ins Repo)
4. RX: REF_SEQ gegen 60-s-Empfangspuffer prüfen (Replay-Schutz)
5. Web-UI: authentifizierte Frames mit [🔑]-Badge markieren

**Kein Timestamp-Feld:** Replay-Schutz über REF_SEQ + 60-s-Fenster
(kein Platz in 20 B). Siehe gust_knowledge.md §28.

**Abhängigkeit:** keine externe Bibliothek (`hmac`/`hashlib` aus stdlib).

**Referenz:** gust_spec.md §3.4/§3.5, gust_knowledge.md §28. Nicht vor v1.0.

---

### P8-12: GUST-X Protokollvariante

**Ziel:** Optionale erweiterte Protokollvariante mit mehr Payload,
LDPC-FEC und Pflicht-Timestamp. Rückwärtskompatibel zu GUST-S.

**Kernmerkmale GUST-X v1:**
- 9-Symbol-SYNC (Costas + Variantensymbol V=1)
- LDPC n=256, Rate 3/4 (nach Soft-Demod: ~2 dB SNR-Gewinn)
- Max. Payload: 44 Byte (vs. 20 Byte GUST-S)
- Timestamp: 4-Byte-Pflichtfeld in jedem Frame
- Sendedauer: ≤ 7,5 s (vs. ≤ 5 s GUST-S)

**Neue Frame-Typen:**
- 0x81 WEATHER_EX    (Wetter + Position kombiniert, 32 Byte)
- 0x82 EMERG_EX      (Erweiterter Notfall + Freitext, 44 Byte)
- 0x83 SENSOR_EX     (5-6 Sensor-Kanäle, 40 Byte)
- 0x84 POSITION_EX   (Track 3 Punkte + Heading, 28 Byte)
- 0x85 AUTH_EX       (ECDSA P-256 Signatur-Hälfte r, 2-Frame)
- 0x86 AUTH_EX_B     (ECDSA P-256 Signatur-Hälfte s, 2. Frame zu 0x85)
- 0x87 RELAY         (Mesh-Relay-Header, 20 Byte)

**Implementierungsschritte:**
1. `gust_modulator.py`: 9-Symbol-SYNC Erkennung in `_find_sync_candidates()`
2. `gust_frame.py`: neue Frame-Typen 0x81–0x86, Timestamp-Pflichtfeld
3. `gust_ldpc.py`: n=256 Parametrisierung (statt n=48)
4. `gust.py`: `protocol.variant` aus gateway.json lesen
5. Web-UI: GUST-X Frames mit [X]-Badge markieren
6. Stresstest: GUST-X WAV-Generator + Decoder (analog Etappe 4)

**Voraussetzung P8-13** für vollen SNR-Gewinn, aber:
GUST-X ist auch ohne Soft-Demod sinnvoll (44 Byte + Timestamp).

**Referenz:** gust_spec.md §3.9, ADR-37, gust_knowledge.md §29

---

### P8-13: Soft-Output-Demodulator

**Ziel:** `_fft_detect_symbol()` in `gust_modulator.py` gibt statt
einem Hard-Symbol (int 0–7) ein LLR-Array (8 float-Werte) zurück.

**Warum:** LDPC Belief Propagation braucht Zuverlässigkeitsinformation.
Ohne LLR degeneriert BP zu einem schlechten Bit-Flip-Decoder —
schlechter als RS. Mit LLR: ~2 dB SNR-Gewinn gegenüber RS(255,223).

**Konkret:**
- Bin-Energien aller 8 FFT-Töne berechnen (bereits intern vorhanden)
- LLR(bit_k) = log(P(bit_k=0) / P(bit_k=1)) aus Bin-Energien
- Aufrufort: `demodulate()` → `_fft_detect_symbol()` → LLR-Array
- Rückwärtskompatibel: Hard-Decision = argmax(LLR) für GUST-S

**Referenz:** gust_knowledge.md §27 (LDPC Blocklängen-Evaluation),
             cc_ldpc_etappe3_integration.md (freigegeben, P8-13 fertig)

---

## PHASE 10 — History & Logging ← IN ARBEIT

| ID | Prio | Typ | Titel | Beschreibung | Status |
|---|---|---|---|---|---|
| P10-01 | 🟡 | feature | Zeitbasierte History — Swimlane 600 s | Backend: `_rx_history` von `deque(maxlen=50)` auf `deque(maxlen=350)` erhöht. Frontend: `/api/log` wird beim Seitenladen via `slLoadHistory()` auch in die Swimlane gespeist — Zeitachsen-Kalibrierung über `tx_start_s` (txT0 = ältester Frame, `_nowS()` startet beim neuesten und läuft live weiter), Batch-Insert via `slAddFrame(data, isHistory=true)` ohne Clamp/Scroll pro Frame. Client begrenzt auf `SL_MAX_WINDOW_S` (600 s). | ✅ |
| P10-02 | 🟡 | feature | Anzahlbasierte Langzeit-History (Tabelle) | `gust_log.py` (geplant): persistentes Langzeit-Log im Daemon; anzahlbasierte Abfragen („die letzten 500 Frames von OE3XTU"), Darstellung als Tabelle — nicht Swimlane (bei 24 h History wären Frames nur 1–2 px hoch). Dazu `rx_history_maxlen` in gateway.json konfigurierbar machen (Daemon filtert/bewahrt auf, Client bekommt nur was er anfragt). | 🔲 |

### Design-Entscheidungen (Juni 2026)

- **Zeit vs. Anzahl:** "Die letzten 500 Frames von OE3XTU"
  und "die letzten 600 Sekunden" sind verschiedene Dimensionen.
  P10-01 (zeitbasiert, 600s Swimlane) und P10-02
  (anzahlbasiert, Tabellenform) lösen dieses Problem getrennt.

- **Swimlane bei langen Zeiträumen ungeeignet:**
  Bei 24h History mit ~20 Frames/Tag wären Frames nur
  1-2px hoch. Swimlane bleibt auf 600s begrenzt.
  P10-02 verwendet Tabellen-Darstellung für lange Zeiträume.

- **Filterung im Daemon, Darstellung im Client:**
  Der Daemon entscheidet welche Frames aufbewahrt werden
  (Speichereffizienz). Der Client bekommt nur was er anfragt.

---

## BEKANNTE BUGS & TECHNISCHE SCHULDEN

| ID | Prio | Typ | Titel | Details | Status |
|---|---|---|---|---|---|
| BUG-01 | 🟡 | bug | Rufzeichen > 6 Zeichen werden gekürzt | VK2XX/P → VK2XX/ — Suffix /P geht verloren. Fix: 1-Byte-Suffix-Feld | ⏸ Phase 8 |
| BUG-02 | 🟢 | bug | inspectrum Frequenzachse verschoben | ~600 Hz Offset bei 8 kHz SR — Darstellungsartefakt, keine Auswirkung | ⏸ |
| BUG-03 | 🟢 | bug | CF32-Export zeigt Rest-Spiegelbild | Randeffekt durch Stille-Abschnitte nach Hilbert-Transform | ⏸ |
| BUG-04 | 🟡 | refactor | RS-FEC ineffizient für kurze Frames | RS(255,223): immer 32 Byte Overhead. RS(31,15) wäre effizienter. **Hinweis (Juni 2026):** mehr Parität ist keine Option — RS(255,191) evaluiert und verworfen (+51–67 % Airtime, ADR-25); nur kürzere Codes weiterverfolgen | 🔲 Phase 8 |
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
| BUG-18 | 🔴 | bug | Deep-Decoder CPU-Konkurrenz | Deep-Decoder nutzte `self._executor` (8 Worker, geteilt mit Short-Decoder) — 80 Deep-Calls/Scan verhungerten den Short-Decoder (Regression 55 → 44 Frames). **Fix:** eigener `_deep_executor` (N_CHANNELS Worker, nur bei `deep_decode=true`), Shutdown im finally. Siehe ADR-27. | ✅ |
| BUG-19 | 🟡 | bug | DedupCache TOL_S zu eng | `sync_offset_s`-Jitter zwischen zwei Scans (Short/Short oder Deep/Short) ~0,7 s > TOL_S 0,5 s → 9 Überzählige pro Session. **Fix:** TOL_S = 1,5 s (> Jitter, < ~4 s Mindest-Sendeabstand). Siehe ADR-28. | ✅ |

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
| ✅ BUG-18 | — | bug | Deep-Decoder CPU-Konkurrenz (gemeinsamer Executor) | Juni 2026 |
| ✅ BUG-19 | — | bug | DedupCache TOL_S zu eng (0.5s → 1.5s) | Juni 2026 |

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

### ADR-25: RS(255,223) bleibt — kein FEC-Upgrade auf RS(255,191) ✅ (Juni 2026)
Als Reaktion auf den Stresstest-FEC-Cliff bei ~−12 dB (Mehrkanal, Schicht 4) wurde
RS(255,191) (64 Byte Parität, 32 korrigierbare Byte-Fehler) evaluiert und **verworfen**.
GUST nutzt shortened RS: übertragen wird `Payload + 8 Header + RS_OVERHEAD`, nie der
volle 255-Byte-Block — die +32 Paritätsbytes kosten +51–67 % Sendezeit je Frame-Typ
(WEATHER 4,86 s → 7,62 s) für nur ~3–5 dB Cliff-Verschiebung. Folgekosten: längste
Frames ~8,1 s verletzen die Vollfenster-Garantie → `MAX_FRAME_S`/`WINDOW_S` müssten
auf ~8,5/≥ 11 s wachsen (mehr Latenz, mehr Contention, BUG-08). Kurze Aussendedauer
ist Kern-Designziel (Telemetrie/Notfunk). Der Cliff ist zudem ein Mehrkanal-Artefakt
(1/8-Amplitude = −18 dB/Kanal); für Einzel-TX gilt T-10.2 (≤ 10 dB). Falls FEC-Tuning
nötig wird: kürzere Codes (RS(31,15)/RS(63,31), BUG-04), nicht mehr Parität.
Analyse: `gust_knowledge.md` §22, Methodik: `stresstest.md` Schicht 4.

### ADR-26: Ringpuffer-Mindestgröße 120s ✅ (Juni 2026)
Bei 30s Puffer und 5.5s Frame-Dauer liegt die Wrap-around-
Wahrscheinlichkeit bei 18.3% pro Frame → ~60% Live-Dekodierrate.
Bei 120s sinkt sie auf 4.6%. Speicherbedarf: 120s × native SR ×
float32 ≈ 21 MB bei 48 kHz — unkritisch auch auf RPi.
Fix: `buffer_seconds = max(self._window * 2, 120.0)` in
`AudioReceiver.__init__()` (gust_rx.py).

### ADR-27: Deep-Decoder — paralleler RX-Task mit eigenem Executor ✅ (Juni 2026)
Der Short-Decoder (9s/2s) erreicht auf VAC-Audio nur ~54-57%
Dekodierrate. Root Cause ungeklärt (nicht Ringpuffer, nicht
Samplerate, nicht Scan-Intervall). Lösung: paralleler Deep-Decoder
(20s Fenster / 15s Intervall, Sliding-Window analog Batch-Decoder)
über `rx.deep_decode: true` in gateway.json aktivierbar.
Gemeinsamer `_DedupCache` mit Short-Decoder verhindert doppelte
Events. Kritisch: eigener separater ThreadPoolExecutor
(`_deep_executor`, N_CHANNELS Worker) — der Short-Decoder-Pool
darf nicht geteilt werden (sonst CPU-Starvation → Regression von
55 auf 44 Frames). Ergebnis: Short ~57 + Deep ~29 = 86-90% Gesamt.
Implementiert in gust_rx.py (`_deep_decoder` asyncio Task).

### ADR-28: DedupCache TOL_S = 1.5s ✅ (Juni 2026)
Ursprünglicher Wert TOL_S = 0.5s führte zu 9 Überzähligen pro
Session: sync_offset_s-Jitter zwischen zwei Scans (Short-Short oder
Deep-Short) beträgt ~0.7s > 0.5s → Dedup ließ durch. Neuer Wert
1.5s: größer als max. beobachteter Jitter (0.7s), kleiner als
Mindest-Sendeabstand (~4s Framedauer). Keine legitimen Frames
werden unterdrückt.

### ADR-29: gateway.json v2 — tx_audio/rx_audio + _doc-Block ✅ (Juni 2026)
Umbenennung `audio` → `tx_audio`, `rx` → `rx_audio` (sprechende
Namen: TX-Pipe vs. RX-Pipe) plus `_doc`-Block, der alle Schlüssel
in der Datei selbst dokumentiert. Rückwärtskompatibilität über
Helper `_get_tx_audio()`/`_get_rx_audio()` in gust.py (neu-vor-alt)
und Inline-Fallbacks in gust_gateway/gust_audio/gust_web/
gust_tx_test/gust_beacon. Schreibpfade nutzen das Seeding-Pattern
`setdefault("tx_audio", cfg.get("audio", {}))` — bei Alt-Configs
zeigen beide Keys auf dasselbe Dict, beide Sichten bleiben konsistent.

### ADR-30: TRX-Profil-Verwaltung im Web-UI (CRUD-API) ✅ (Juni 2026)
`POST /api/trx/save` (anlegen/ersetzen per Name) und
`DELETE /api/trx/profile?name=…` mit Schutzregeln: aktives Profil
und letztes Profil nicht löschbar (409). Bearbeitungsformular im
Transceiver-Sub-Tab, vom Profil-Dropdown automatisch befüllt.
Profile sind damit vollständig ohne Handbearbeitung der
gateway.json verwaltbar (vorher: Anlage nur manuell).

### ADR-31: SDR-Profile — sdr_profiles + active_sdr_rx/tx_profile ✅ (Juni 2026)
Analog zu TRX-Profilen ersetzt ein `sdr_profiles`-Array die
Einzelblöcke `sdr_tx`/`rtlsdr` (die als Lese-Fallback erhalten
bleiben). Jedes Profil: name, type (rx|tx|trx — automatisch per
`enumerate_all_devices()` aus der Hardware abgeleitet, nicht
manuell), driver, serial, rx{…}/tx{…}-Unterobjekte.
`active_sdr_rx_profile`/`active_sdr_tx_profile` wählen RX- und
TX-Pfad unabhängig (null = Audio-Pfad). API: /api/sdr/scan,
profile/save, profile (DELETE), profile/activate/rx|tx —
Schutzregeln wie ADR-30.

### ADR-32: IQReceiver auf SoapySDR (treiberneutral) + Factory ✅ (Juni 2026)
`IQReceiver` akzeptiert ein sdr_profiles-Profil und empfängt via
SoapySDR (`_run_soapy()`: RTL-SDR, SDRplay, HackRF-RX); pyrtlsdr
bleibt als Fallback für driver=rtlsdr ohne SoapySDR
(`_run_pyrtlsdr()`). PPM-Korrektur als komplexer Mischer
(`_apply_ppm()`), blockierendes readStream im Executor.
`build_iq_receiver(cfg)` als Factory (Profil → Legacy-rtlsdr →
None); cmd_daemon/cmd_rx starten den IQ-Task parallel zum
AudioRXLoop. TX-Seite analog: `_resolve_sdr_tx_cfg()` mit
Priorität active_sdr_tx_profile → sdr_tx.enabled → Audio.
Einschränkung: SoapySDR-Bindings derzeit nur unter Python 3.9
(PothosSDR) — siehe gust_knowledge.md §24.

### ADR-33: Deep-Decoder Threads mit BELOW_NORMAL-Priorität ✅ (Juni 2026)
Der Deep-Decoder startet alle 15 s bis zu N_CHANNELS=8 Threads
gleichzeitig; diese verdrängten den PortAudio-Callback-Thread →
`input overflow` exakt im 15-s-Takt. Fix: `_set_low_priority()` als
`initializer` im `_deep_executor` (ThreadPoolExecutor) — Windows
`SetThreadPriority(handle, -1)` = BELOW_NORMAL, Linux/macOS
`os.nice(5)`. Kritisch auf 64-bit-Windows: `GetCurrentThread()`
liefert das Pseudo-Handle −2; ohne `restype=c_void_p` /
`argtypes=[c_void_p, c_int]` wird es auf 32 bit verstümmelt →
`SetThreadPriority` schlägt **still** fehl (kein Fehler, keine
Wirkung). Keine messbare Verschlechterung der Dekodierrate
(BELOW_NORMAL weicht nur bei CPU-Knappheit aus). Siehe
gust_knowledge.md §26.

### ADR-34: VITAL-Log-Level + Quiet-Mode + print()→log.*() ✅ (Juni 2026)
Eigener Log-Level **VITAL** (35, zwischen WARNING und ERROR) via
`logging.addLevelName(35, "VITAL")` + Monkey-Patch
`logging.Logger.vital()`. Ohne `--verbose` zeigt die Konsole nur
VITAL+ERROR (mit Timestamp); RX/TX-Frame-Events, Heartbeat und
CRC-Meldungen sind stumm. `_GustStreamHandler._classify()` erkennt
RX/TX-Labels am Message-Inhalt (`"[RX]" in msg` → `RX ◀`), sodass
bestehende `log.info()`-Aufrufe nach der Migration korrekt gefiltert
werden; ERROR-Records geben immer `""` zurück (nie als RX ◀
gerendert). Alle `print()`-Aufrufe in `gust_rx.py`/`gust_audio.py`
migriert: Fehler→`log.error()`, Betriebsereignisse→`log.vital()`,
Status→`log.debug()`; CLI-Funktionen (`list_audio_devices`,
`ptt_test`, `_run_demo`) behalten `print()`. VITAL-Ereignisse:
Web-Server-Start, rigctld Start/Stop, TRX-Profil aktiviert,
`[RX Audio] input overflow`, PTT EIN/AUS, TX-Pipeline. Siehe
gust_knowledge.md §25, gust_spec.md §5.3.

### ADR-35: Swimlane — laufende Zeitachse + Canvas-internes Scrollen ✅ (Juni 2026)
Die Swimlane-Zeitachse läuft mit der Browser-Wanduhr (`sl._nowS()`,
verankert bei erstem Frame über `browserT0`) — auch ohne neue Frames;
neueste Frames oben („jetzt"), ältere nach unten, Achsen-Labels als
Alter (`-10s`/`-20s`). Fixe Canvas-Höhe (70 vh) mit Canvas-internem
Scroll via `scrollOffsetPx` (`ctx.translate`), gezeichnete Scrollbar,
Mausrad/Tastatur-Steuerung; Auto-Scroll positionsgesteuert (oben =
live). Pause friert einen Snapshot ein (`frozenFrames`/`frozenNowS`).
Fixes 600-s-Fenster, zwei Zoom-Stufen (6/10 px/s). History via
`/api/log` → `slLoadHistory()` (Backend `deque(maxlen=350)`).
Frame-Klick öffnet das Detail-Modal (`slOnClick` → `openFrameModal`).

### ADR-36: Inbox-Antwortfunktion (Mini-Compose) ✅ (Juni 2026)
Das Inbox-Detail-Modal (`showInboxDetail`) zeigt bei vollständigen
Nachrichten (`complete=true`) einen Antwort-Bereich: Textarea,
byte-korrekter Fragment-Zähler (14 Byte/Fragment, BUG-09-Grenze
16 Fragmente), „↩ Senden" → `sendInboxReply(toCall)` → `POST
/api/tx/text` mit Feld `to` (nicht `dest` — das Gateway liest
`data.get("to")` und fragmentiert serverseitig). Status-Zeile mit
benutzerfreundlicher Fehlerübersetzung (roher HTTP-Body → JSON-error
extrahiert). Unvollständige Multi-Frame-Nachrichten erhalten keinen
Antwort-Bereich.

### ADR-37: GUST-X — 9-Symbol-SYNC als Protokollvarianten-Erkennung 🔲 (Juni 2026, geplant)

**Problem:** Eine erweiterte Protokollvariante (größere Payload, LDPC,
Timestamp) muss vom Decoder erkannt werden bevor FEC angewendet wird —
sonst Henne-Ei-Problem (FEC-Verfahren muss vor der FEC-Dekodierung bekannt sein).

**Evaluierte Optionen:**
- A: Mode-Bit im CHANNEL-Byte → nach FEC → Zirkelschluss ✗
- B: 9. SYNC-Symbol → vor FEC → eindeutig ✓ (gewählt)
- C: Zwei-Hypothesen-Dekodierung → doppelter CPU-Aufwand → RPi ✗

**Entscheidung:** Option B — 9-Symbol-SYNC.
Das 9. Symbol (Variantensymbol V) steht zwischen SYNC und FEC-Daten.
V=0 und V=7 reserviert (SYNC-Stabilität), V=1 = GUST-X v1,
V=2–6 für künftige Varianten (6 Slots total).

**Eigenschaften:**
- GUST-S Decoder: ignoriert GUST-X Frames (9-Symbol-SYNC nicht erkannt)
- GUST-X Decoder: erkennt beide Varianten
- Overhead: +1 Symbol = +32 ms — vernachlässigbar
- Zukunftssicher: 6 Varianten-Slots verfügbar

**Variantenname:** GUST-S (Slim, 8-Symbol-SYNC) / GUST-X (Extended, 9-Symbol-SYNC)
**Referenz:** gust_spec.md §3.9, P8-12, P8-13

---

*Dokument: gust_backlog.md*
*Autor: OE3GAS*
*Stand: Juni 2026 — ADR-33–36: Deep-Decoder Thread-Prio · VITAL-Logging + print()→log.*() · Swimlane laufende Zeitachse/Canvas-Scroll · Inbox-Antwort · ADR-37 GUST-X 9-Symbol-SYNC (geplant)*