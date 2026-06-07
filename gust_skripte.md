# GUST — Python-Skript-Übersicht

**Projekt:** GUST (Generic Universal Shortwave Telemetry)  
**Rufzeichen:** OE3GAS  
**Stand:** Juni 2026  

---

## Legende

| Symbol | Bedeutung |
|--------|-----------|
| 🔧 | Bibliotheks-Modul — wird von anderen Modulen importiert, nicht direkt ausgeführt |
| ▶ | Eigenständig ausführbar — hat eigenen Einstiegspunkt (`if __name__ == "__main__"`) |
| ⚙ | Läuft als Teil des Daemons (wird von `gust.py` gestartet/importiert) |
| 🗄 | Archiv — historische Vorgängerversion, nicht mehr aktiv |

---

## Aktive Module

### `gust.py` — CLI-Einstiegspunkt ▶
**Phase 5**  
Zentraler Einstiegspunkt für alle GUST-Funktionen. Liest `gateway.json`,
startet den Daemon mit allen Subsystemen (TX-Gateway, RX-Loop, Web-Server,
IQ-RX) oder führt Einzelbefehle aus (TX, RX-Monitor, Geräteliste).

```
py gust.py daemon          Vollbetrieb (TX + RX + Web)
py gust.py rx              Monitor-Modus (nur RX + Web)
py gust.py tx weather      Einzelnen Frame senden
py gust.py devices         Audiogeräte anzeigen
```
**Zusammenarbeit:** Orchestriert alle anderen Module. Importiert
`gust_audio`, `gust_rx`, `gust_gateway`, `gust_web`, `gust_eventbus`,
`gust_iq_rx`, `gust_soapy_tx`, `gust_msg_simulator`.

---

### `gust_frame.py` — Frame Layer 🔧
**Phase 2**  
Kernbibliothek des Protokolls. Implementiert Rufzeichen-Codec (Basis-40),
CRC-16/CCITT, Payload-Encoder/-Decoder für alle Frame-Typen (WEATHER,
POSITION, TEXT, EMERG_BEACON, EMERG_RSRC, STATION_TLM, CQ …),
Reed-Solomon FEC, Kanalzuweisung per Rufzeichen-Hash sowie die
Bytes-zu-Symbole-Konvertierung für den MFSK-Modulator.

**Zusammenarbeit:** Wird von nahezu allen anderen Modulen importiert
(`gust_modulator`, `gust_rx`, `gust_decode`, `gust_stresstest`, …).
Keine eigenen Abhängigkeiten außer `reedsolo`.

---

### `gust_modulator.py` — MFSK-8 Modulator/Demodulator 🔧
**Phase 1/3**  
MFSK-8 Modulator (phasenkontinuierlich, Raised-Cosine-Windowing) und
Demodulator (FFT-basiert). TX-Pipeline: Frame-Body → Audio in einem
Aufruf. RX-Pipeline: Audio → Frame-Body → Dict. WAV-Utilities für
Audacity/inspectrum. Costas-Array-Sync für Frame-Erkennung.

**Zusammenarbeit:** Wird von `gust_rx`, `gust_decode`, `gust_stresstest`,
`gust_stress_decode`, `gust_visualize`, `gust_wav_tx` importiert.

---

### `gust_audio.py` — Audio TX/RX + PTT-Steuerung 🔧 ⚙
**Phase 3**  
Hardware-Integration: PTT-Backends (`NullPTT`, `GPIUPTT`, `HamlibPTT`),
`AudioTransmitter` (PTT-EIN → Stille → Audio → Stille → PTT-AUS),
`AudioReceiver` (kontinuierlicher Ringpuffer für RX), `build_ptt()`,
`list_audio_devices()`. Enthält auch `ensure_rigctld_running()` für
den automatischen rigctld-Start.

**Zusammenarbeit:** Wird von `gust.py`, `gust_gateway`, `gust_rx`,
`gust_beacon`, `gust_tx_test`, `gust_wav_tx` importiert.

---

### `gust_rx.py` — Kontinuierlicher RX-Loop 🔧 ⚙
**Phase 7**  
Dauerhafter Audio-Empfang: `AudioReceiver` (Ringpuffer 120s, 48 kHz),
`AudioRXLoop` mit Short-Decoder (9s/2s) und Deep-Decoder (20s/15s,
separater Executor). `DedupCache` (TOL_S=1.5s). `build_rx_loop(cfg)`
als Factory-Funktion. Deep-Decoder aktivierbar via
`rx_audio.deep_decode: true` — verbessert Dekodierrate von ~55% auf ~90%.

**Zusammenarbeit:** Wird von `gust.py` (`cmd_daemon`, `cmd_rx`) gestartet.

---

### `gust_eventbus.py` — Event-Bus 🔧 ⚙
**Phase 5**  
Fan-out Publisher: ein Event → alle Subscriber. `EventType`-Konstanten,
`SubscriberQueue` (asyncio.Queue mit TTL-Filterung), `EventBus`,
`make_*_event()`-Hilfsfunktionen. Thread-sicheres Einliefern für
externe Callbacks (MQTT, Meshtastic).

**Zusammenarbeit:** Verbindet alle Laufzeit-Komponenten. Wird von
`gust.py`, `gust_rx`, `gust_gateway`, `gust_web`, `gust_iq_rx` verwendet.

---

### `gust_web.py` — Web-Server + REST-API + Web-UI 🔧 ⚙
**Phase 5**  
aiohttp-basierter Web-Server (Port 8080). REST-API für Konfiguration,
TX-Steuerung, TRX-Profil-Verwaltung, SDR-Profil-Verwaltung, Status.
WebSocket-Streams für RX-Frames und Log. Eingebettetes HTML/CSS/JS
Dashboard (Tabs: Monitor, Senden, Konfiguration, Log). Alle
Konfigurationsänderungen werden atomar in `gateway.json` geschrieben.

**Zusammenarbeit:** Wird von `gust.py` gestartet. Kommuniziert über
`EventBus` und ruft `gust.py cmd_tx()` für TX-Anfragen auf.

---

### `gust_gateway.py` — TX-Gateway 🔧 ⚙
**Phase 7**  
Sende-Gateway zwischen Web-Schicht und Audio-TX-Pipeline.
`TxGateway.enqueue()` reiht Frames in eine asyncio-PriorityQueue ein
(P1=Notfall, P4=niedrigste). Ein Worker-Task sendet sequenziell.
Cooldown (`min_tx_gap_s`) zwischen Nicht-Notfall-Frames. Notfall
überspringt Cooldown und überholt wartende Frames.

**Zusammenarbeit:** Wird von `gust.py` instanziiert und von `gust_web.py`
über die API befüllt. Ruft `gust_audio.AudioTransmitter` für TX auf.

---

### `gust_iq_rx.py` — IQ-Eingang SDR 🔧 ⚙
**Phase 9**  
IQ-Empfangspfad für SDR-Hardware (RTL-SDR, SDRplay, HackRF).
`IQReceiver`: konfigurierbar aus `sdr_profiles`-Eintrag oder Legacy
`rtlsdr`-Block. `_run_soapy()` (SoapySDR, universell),
`_run_pyrtlsdr()` (RTL-SDR Fallback), `_apply_ppm()` (PPM-Korrektur
via komplexem Mischer). `build_iq_receiver(cfg)` als Factory.
Läuft parallel zu `AudioRXLoop` wenn `active_sdr_rx_profile` gesetzt.

**Zusammenarbeit:** Wird von `gust.py` (`cmd_daemon`, `cmd_rx`) parallel
zu `gust_rx.py` gestartet.

---

### `gust_soapy_tx.py` — Generischer SoapySDR-TX 🔧
**P7-04 / ADR-16**  
Treiberneutrale TX-Schicht über SoapySDR. `SoapyTxBackend`: Device-
Enumeration, IQ-Stream-Ausgabe. `enumerate_tx_devices()` und
`enumerate_all_devices()` (liefert RX- und TX-Fähigkeiten für
SDR-Profil-Erkennung — type rx/tx/trx automatisch aus
`getNumChannels()`). `list_modules()`, `soapy_available()`.

**Zusammenarbeit:** Wird von `gust.py` (`_resolve_sdr_tx_cfg`) und
`gust_web.py` (`/api/sdr/scan`) importiert.

---

### `gust_msg_simulator.py` — Simulations-Adapter 🔧 ⚙
**Phase 5**  
`SimAdapter`: liefert simulierte Telemetrie-Frames (WEATHER, POSITION,
TEXT, EMERG) für den Daemon-Betrieb ohne echte Sensorhardware.
Konfigurierbar über `source.sim` in `gateway.json`.
`read_all_due()` gibt fällige Frames zurück, `next_due_in()` die
Wartezeit bis zum nächsten Frame.

**Zusammenarbeit:** Wird von `gust.py` (`cmd_daemon --sim`) verwendet.

---

### `gust_decode.py` — Standalone WAV-Decoder ▶
**Phase 2/3**  
Dekodiert eine WAV-Datei auf einem oder allen Kanälen (`--scan`).
Nützlich für schnelle manuelle Auswertung einer Aufnahme ohne
vollständigen Daemon.

```
py gust_decode.py aufnahme.wav --scan
py gust_decode.py aufnahme.wav 2
```
**Zusammenarbeit:** Eigenständig. Importiert `gust_modulator`,
`gust_frame`. Keine Abhängigkeit von `gust.py`.

---

### `gust_stresstest.py` — Stresstest-Generator ▶
**Testinfrastruktur**  
Erzeugt WAV-Dateien mit gleichzeitig laufenden randomisierten Frames
auf allen 8 Kanälen (wie auf einer echten KW-Frequenz). Emergency-Frames
werden automatisch auf einem zweiten Kanal gespiegelt (Dual-Channel
Diversity). Ausgabe: WAV + CSV (Ground-Truth für Auswertung).

```
py gust_stresstest.py --seed 43 --out baseline_s43
py gust_stresstest.py --duration 120 --frames-per-ch 10
```
**Zusammenarbeit:** Eigenständig. Importiert `gust_frame`,
`gust_modulator`. Keine Abhängigkeit von `gust.py`.

---

### `gust_stress_decode.py` — Batch-Decoder / Stresstest-Auswertung ▶
**Testinfrastruktur**  
Liest eine Stresstest-WAV vollständig durch (Sliding-Window-Scan),
vergleicht gegen das zugehörige CSV und gibt eine Dekodierrate aus
(PASS ≥ 80%). Exit-Code 0 = PASS, 1 = FAIL.

```
py gust_stress_decode.py baseline_s43.wav --csv baseline_s43.csv
py gust_stress_decode.py kiwi_aufnahme.wav --csv baseline_s43.csv
```
**Zusammenarbeit:** Eigenständig. Importiert `gust_modulator`,
`gust_frame`. Keine Abhängigkeit von `gust.py`.

---

### `gust_visualize.py` — Swimlane-Visualisierung ▶
**Testinfrastruktur**  
Liest eine Stresstest-WAV, dekodiert alle 8 Kanäle und erstellt ein
Swimlane-Diagramm (Kanäle als Spalten, Zeit vertikal). Optional mit
Ground-Truth-CSV-Überlagerung. Ausgabe als JPG.

```
py gust_visualize.py baseline_s43.wav --csv baseline_s43.csv
```
**Zusammenarbeit:** Eigenständig. Importiert `gust_modulator`,
`gust_frame`.

---

### `gust_wav_tx.py` — WAV-Datei mit PTT senden ▶
**Testinfrastruktur / On-Air**  
Sendet eine WAV-Datei über den in `gateway.json` konfigurierten
TX-Audio-Pfad mit PTT-Steuerung. Gedacht für On-Air-Stresstests:
Stresstest-WAV lokal erzeugen → über IC-7610 aussenden → KiwiSDR
empfangen → mit `gust_stress_decode.py` auswerten.

```
py gust_wav_tx.py baseline_s43.wav --repeat 3 --gap 10
py gust_wav_tx.py baseline_s43.wav --dry-run
py gust_wav_tx.py baseline_s43.wav --level 25
```
**Zusammenarbeit:** Eigenständig. Liest `gateway.json` direkt.
Importiert `gust_audio` (AudioTransmitter, PTT), `gust_modulator`
(load_wav). Keine Abhängigkeit vom laufenden Daemon.

---

### `gust_beacon.py` — Standalone Bake ▶
**Phase 7**  
Eigenständiges Beacon-Skript für Dauerbetrieb (systemd-Service,
Autostart). Sendet konfigurierbare Frame-Typen (Wetter, Position,
Text, Notfall) im einstellbaren Intervall mit CSV-Logging.
Im Gegensatz zur interaktiven Bake in `gust_tx_test.py` komplett
über CLI-Parameter steuerbar.

```
py gust_beacon.py --call OE3GAS --types wpt --interval 300
```
**Zusammenarbeit:** Eigenständig. Importiert `gust_audio`,
`gust_frame`, `gust_modulator`. Liest `gateway.json` direkt.

---

### `gust_tx_test.py` — TX-Testskript ▶
**Phase 7**  
Interaktives Test- und Diagnose-Skript für den TX-Pfad. Sendet
zufällige Frames (WEATHER, POSITION, TEXT, Dual-Kanal-Emergency)
wahlweise via Audio-PTT oder HackRF. Freitext-Modus (`--text-only`).
Enthält auch eine interaktive Bake-Funktion.

```
py gust_tx_test.py
py gust_tx_test.py --text-only
py gust_tx_test.py --beacon
```
**Zusammenarbeit:** Eigenständig. Importiert `gust_audio`,
`gust_frame`, `gust_modulator`, `gust_hackrf`. Liest `gateway.json`.

---

### `gust_hackrf.py` — HackRF/SoapySDR TX-Pfad 🔧
**Phase 3**  
Ältere HackRF-spezifische TX-Implementierung (`HackRFTransmitter`).
Unterliegt `gust_soapy_tx.py` als neuem, treiberneutralen TX-Pfad.
Bleibt als Fallback für `gust_tx_test.py` erhalten.

**Zusammenarbeit:** Wird von `gust_tx_test.py` importiert.

---

## Diagnose-Skripte

### `gust_audio_diag.py` — Audio-Diagnose ▶
Testet alle verfügbaren Audio-Eingänge und zeigt welche ein Signal
liefern. Ideal für die Diagnose der VAC/Audio-Repeater-Kette
(Browser → Audio Repeater → VAC → gust_rx).

```
py gust_audio_diag.py
py gust_audio_diag.py --device 3
```
**Zusammenarbeit:** Eigenständig. Keine Abhängigkeit von `gust.py`.

---

### `gust_hackrf_diag.py` — HackRF Dual-Kanal-Diagnose ▶
**Phase 7**  
Findet heraus welcher SoapySDR-Aufruf beim Dual-Kanal-TX hängt.
Protokolliert jeden Aufruf mit Timing, Watchdog-Thread meldet
Hänger (> 10s). Wurde zur Diagnose von BUG-13 (TX-Underrun) verwendet.

```
py gust_hackrf_diag.py --gain 30 --freq 14110000
```
**Zusammenarbeit:** Eigenständig. Keine Abhängigkeit von `gust.py`.

---

## Archiv-Module (`archiv/`)

Historische Vorgängerversionen aus der OE3Mode-Phase (vor Umbenennung
zu GUST). Nicht mehr aktiv verwendet — nur als Referenz erhalten.

| Datei | Inhalt |
|-------|--------|
| `archiv/oe3mode.py` | OE3Mode CLI-Einstiegspunkt (Vorgänger von `gust.py`) |
| `archiv/oe3mode_audio.py` | Audio TX/RX (Vorgänger von `gust_audio.py`) |
| `archiv/oe3mode_decode.py` | Decoder (Vorgänger von `gust_decode.py`) |
| `archiv/oe3mode_decoder.py` | Älterer Decoder-Prototyp |
| `archiv/oe3mode_eventbus.py` | Event-Bus (Vorgänger von `gust_eventbus.py`) |
| `archiv/oe3mode_frame.py` | Frame Layer (Vorgänger von `gust_frame.py`) |
| `archiv/oe3mode_hackrf.py` | HackRF TX (Vorgänger von `gust_hackrf.py`) |
| `archiv/oe3mode_mfsk.py` | MFSK-Implementierung (Vorgänger von `gust_modulator.py`) |
| `archiv/oe3mode_modulator.py` | Modulator (Vorgänger von `gust_modulator.py`) |
| `archiv/oe3mode_msg_simulator.py` | Simulator (Vorgänger von `gust_msg_simulator.py`) |
| `archiv/oe3mode_rx.py` | RX-Loop (Vorgänger von `gust_rx.py`) |
| `archiv/oe3mode_web.py` | Web-Server (Vorgänger von `gust_web.py`) |
| `archiv/demo_wiring.py` | Früher Verdrahtungs-Demo-Code |
| `archiv/hackrf_diag.py` | HackRF-Diagnose (Vorgänger von `gust_hackrf_diag.py`) |
| `archiv/rx_tx_test.py` | Früher RX/TX-Loopback-Test |
| `archiv/sinuston_test.py` | Sinuston-Testskript für Audiogeräte-Kalibrierung |
| `archiv/snr_test_p705.py` | SNR-Baseline-Messung (Phase 7, P7-05) |
| `archiv/test_phase1.py` | Phase-1-Integrationstests |
| `archiv/test_phase5.py` | Phase-5-Integrationstests |
| `archiv/tx_test.py` | Früher TX-Test (Vorgänger von `gust_tx_test.py`) |
| `archiv/vac_loopback_test.py` | VAC-Loopback-Verifikation |

---

## Abhängigkeits-Übersicht

```
gust.py (Einstiegspunkt)
├── gust_audio.py       ← PTT, AudioTransmitter, AudioReceiver
├── gust_rx.py          ← Short + Deep Decoder, Ringpuffer
│   └── gust_modulator.py
│       └── gust_frame.py
├── gust_gateway.py     ← TX-Queue, Priority, Cooldown
├── gust_web.py         ← REST-API, Web-UI, WebSocket
├── gust_eventbus.py    ← Fan-out Publisher
├── gust_iq_rx.py       ← SoapySDR / pyrtlsdr IQ-Empfang
├── gust_soapy_tx.py    ← SoapySDR TX, enumerate_all_devices
└── gust_msg_simulator.py ← Simulations-Daten

Eigenständige Tools (kein laufender Daemon nötig):
├── gust_stresstest.py      → erzeugt WAV + CSV
├── gust_stress_decode.py   → wertet WAV gegen CSV aus
├── gust_visualize.py       → Swimlane-Diagramm
├── gust_wav_tx.py          → WAV mit PTT senden
├── gust_beacon.py          → Dauerbetrieb-Bake
├── gust_tx_test.py         → interaktiver TX-Test
├── gust_decode.py          → schneller WAV-Decoder
├── gust_audio_diag.py      → Audio-Eingangs-Diagnose
└── gust_hackrf_diag.py     → HackRF TX-Diagnose
```

---

## Kreuz-Referenz: Zusammenarbeit zwischen Modulen

`●` = Zeilen-Modul importiert / verwendet Spalten-Modul. Leere Zelle = keine direkte Abhängigkeit.  
Kurzbezeichnungen: `.py` weggelassen, `gust_` durch `g_` ersetzt.

<!-- Kreuz-Tabelle als HTML für korrekte Darstellung (rotierte Spaltenköpfe, alternierende Spaltenfarben) -->
<style>
.dep-wrap { overflow-x: auto; margin: 1rem 0; }
table.dep { border-collapse: collapse; font-size: 11px; font-family: monospace; }
table.dep th, table.dep td {
  border: 1px solid #aaa;
  text-align: center;
  padding: 0;
}
table.dep td { width: 26px; height: 26px; min-width: 26px; }
table.dep th.row-label {
  text-align: right;
  padding: 2px 8px 2px 4px;
  white-space: nowrap;
  font-weight: normal;
  border-right: 2px solid #666;
  font-size: 11px;
}
table.dep th.col-header {
  height: 120px; width: 26px; min-width: 26px;
  vertical-align: bottom; padding: 0;
  border-bottom: 2px solid #666;
}
table.dep th.col-header div {
  writing-mode: vertical-rl;
  transform: rotate(180deg);
  white-space: nowrap;
  font-weight: normal;
  font-size: 11px;
  padding: 4px 7px 4px 3px;
  text-align: left;
  font-family: monospace;
}
table.dep th.corner {
  border-right: 2px solid #666;
  border-bottom: 2px solid #666;
}
.col-even { background-color: #e8e8e8; }
.col-odd  { background-color: #ffffff; }
.cell-self { background-color: #c0c0c0 !important; }
</style>
<div class="dep-wrap">
<table class="dep">
<thead><tr>
  <th class="corner"></th>
  <th class="col-header col-odd"><div>gust</div></th>
  <th class="col-header col-even"><div>g_audio</div></th>
  <th class="col-header col-odd"><div>g_beacon</div></th>
  <th class="col-header col-even"><div>g_decode</div></th>
  <th class="col-header col-odd"><div>g_eventbus</div></th>
  <th class="col-header col-even"><div>g_frame</div></th>
  <th class="col-header col-odd"><div>g_gateway</div></th>
  <th class="col-header col-even"><div>g_hackrf</div></th>
  <th class="col-header col-odd"><div>g_hackrf_diag</div></th>
  <th class="col-header col-even"><div>g_iq_rx</div></th>
  <th class="col-header col-odd"><div>g_modulator</div></th>
  <th class="col-header col-even"><div>g_msg_sim</div></th>
  <th class="col-header col-odd"><div>g_rx</div></th>
  <th class="col-header col-even"><div>g_soapy_tx</div></th>
  <th class="col-header col-odd"><div>g_stress_dec</div></th>
  <th class="col-header col-even"><div>g_stresstest</div></th>
  <th class="col-header col-odd"><div>g_tx_test</div></th>
  <th class="col-header col-even"><div>g_visualize</div></th>
  <th class="col-header col-odd"><div>g_wav_tx</div></th>
  <th class="col-header col-even"><div>g_web</div></th>
</tr></thead>
<tbody>
<tr><th class="row-label">gust</th>
  <td class="col-odd cell-self"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd">●</td><td class="col-even">●</td><td class="col-odd">●</td><td class="col-even"></td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd">●</td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even">●</td>
</tr>
<tr><th class="row-label">g_audio</th>
  <td class="col-odd"></td><td class="col-even cell-self"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd">●</td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_beacon</th>
  <td class="col-odd"></td><td class="col-even">●</td><td class="col-odd cell-self"></td><td class="col-even"></td><td class="col-odd">●</td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd">●</td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd">●</td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_decode</th>
  <td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even cell-self"></td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd">●</td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_eventbus</th>
  <td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd cell-self"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_frame</th>
  <td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even cell-self"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_gateway</th>
  <td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd">●</td><td class="col-even"></td><td class="col-odd cell-self"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_hackrf</th>
  <td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even cell-self"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd">●</td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_hackrf_diag</th>
  <td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd cell-self"></td><td class="col-even"></td><td class="col-odd">●</td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_iq_rx</th>
  <td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even cell-self"></td><td class="col-odd">●</td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_modulator</th>
  <td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd cell-self"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_msg_sim</th>
  <td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even cell-self"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_rx</th>
  <td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd cell-self"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_soapy_tx</th>
  <td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even cell-self"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_stress_dec</th>
  <td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd">●</td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd cell-self"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_stresstest</th>
  <td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd">●</td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even cell-self"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_tx_test</th>
  <td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd">●</td><td class="col-even"></td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd cell-self"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_visualize</th>
  <td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd">●</td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even cell-self"></td><td class="col-odd"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_wav_tx</th>
  <td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd">●</td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd cell-self"></td><td class="col-even"></td>
</tr>
<tr><th class="row-label">g_web</th>
  <td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd">●</td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even">●</td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even"></td><td class="col-odd"></td><td class="col-even cell-self"></td>
</tr>
</tbody>
</table>
</div>

### Meistverwendete Module (Spalten-Summen)

| Modul | Wird verwendet von | Bedeutung |
|-------|-------------------|-----------|
| `gust_frame` | 14 Module | Protokoll-Kern — unverzichtbare Basis |
| `gust_modulator` | 11 Module | Signal-Kern — MFSK-8 TX/RX |
| `gust_audio` | 5 Module | Hardware-Integration PTT/Audio |
| `gust_eventbus` | 4 Module | Laufzeit-Kommunikation |
| `gust_soapy_tx` | 3 Module | SDR TX-Abstraktionsschicht |
| `gust_hackrf` | 3 Module | HackRF-spezifischer TX-Pfad |

---

*Stand: Juni 2026 — GUST Protokoll v0.5 — OE3GAS*