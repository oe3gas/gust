# GUST — Knowledge Base
**OE3GAS — GUST: Generic Universal Shortwave Telemetry**
*Phase 1–9 — Juni 2026 (inkl. Costas-SYNC · 8-Kanal-Plan · IQ-Eingang · Connector-Layer · TRX-Profil-Fixes · IPv4/IPv6-Fix)*

---

## Übersicht

Dieses Dokument hält Designentscheidungen, technische Erkenntnisse und
Lernmomente fest. Es ergänzt die Projektspezifikation um das *Warum* hinter
den Entscheidungen — für spätere Phasen und als Referenz bei Weiterentwicklungen.

---

## 1. Frequenzarchitektur — Die FT8-Analogie

### Entscheidung
GUST arbeitet wie FT8: alle Stationen stellen **dieselbe Dial-Frequenz** ein,
die individuelle NF-Tonhöhe bestimmt den Kanal.

```
Dial:    14.110,000 MHz (USB, alle Stationen gleich)
NF:      600 – 2.600 Hz  (8 Kanäle × 250 Hz, v0.5)
RF:      14.110,600 – 14.112,600 MHz
```

Jede Station sendet ihr MFSK-8-Signal in einem 250 Hz breiten NF-Fenster.
Der Empfänger sieht im Wasserfall alle Kanäle gleichzeitig.

### Warum nicht 20 Kanäle (ursprünglicher Entwurf)?

20 Kanäle × 250 Hz = 5.000 Hz Bandbreite — passt nicht in ein Standard-SSB-Passband
(300–3.000 Hz). 10 Kanäle × 250 Hz = 2.500 Hz passen problemlos hinein.

**Entscheidungskriterium: Interoperabilität > Kapazität.**

### Warum Reduktion von 10 auf 8 Kanäle (v0.5)?

On-Air-Analyse und SSB-Filtercharakteristik zeigten: Kanal 0 (400 Hz) und
Kanal 9 (2650–2900 Hz) lagen im Rolloff-Bereich des SSB-Bandpassfilters
(−3 bis −10 dB). Die Randtöne hatten deutlich schlechteres SNR als
die mittleren Kanäle — ein strukturelles Problem, das kein Software-Fix
vollständig lösen kann.

Lösung: CHANNEL_BASE_HZ von 400 auf 600 Hz angehoben, N_CHANNELS von 10
auf 8 reduziert. Neuer Span 600–2600 Hz = SSB-Plateau (±0,5 dB).
Kapazitätsverlust minimal — Pure-ALOHA-Limit greift vor der Kanalzahl.
Protokoll-Break auf v0.5 akzeptiert (GitHub noch nicht public zum Zeitpunkt
der Änderung).

### Kanalplan (v0.5, implementiert)

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

Gesamt: 600–2.600 Hz → SSB-Plateau ±0,5 dB ✓

---

## 2. Kanalzuweisung — SHA-256 und der Avalanche-Effekt

### Prinzip
Rufzeichen → SHA-256-Hash → `h % 8` = Kanal (v0.5: 8 Kanäle), `(h >> 8) % 300` = Zeitversatz.
Deterministisch, kein Koordinationsaufwand, kein Beacon nötig.

### Avalanche-Effekt (wichtige Erkenntnis)
Ähnliche Rufzeichen landen auf völlig verschiedenen Kanälen:
```
OE3GAS → Kanal 0,  Versatz 220 s
OE3GAT → Kanal 2,  Versatz  93 s
OE3GAU → Kanal 6,  Versatz 212 s
```
(Kanal-Beispiele mit h % 8 ggf. abweichend von Phase-7-Werten — durch
 Python neu berechnen: `from gust_frame import assign_channel; assign_channel('OE3GAS')`.
 Der grobe Anhaltspunkt `hashlib.sha256(b'OE3GAS').digest()[0] % 8` weicht ab,
 da `assign_channel()` den vollen Hex-Hash modulo 8 nimmt.)

Ein Buchstabe Unterschied → komplett anderer Hash. Das ist gewollt:
gute Verteilung auch bei strukturell ähnlichen Rufzeichen (OE3-Präfix).
Unter v0.5 (8 Kanäle) landen die drei sogar auf drei verschiedenen Kanälen (0/2/6).

### Kollision ohne Konflikt
Teilen zwei Stationen denselben Kanal, schützt der Zeitversatz: liegen ihre
Sende-Slots z.B. > 100 s auseinander, kollidieren sie bei ~4,9 s Framedauer
praktisch nie. Der Zeitversatz `(h >> 8) % 300` ist unabhängig von der
Kanalzahl — die v0.5-Reduktion 10→8 ändert nur die Kanal-, nicht die
Versatz-Werte.

---

## 3. FFT und Spectral Leakage — der wichtigste DSP-Lernmoment

### Das Problem
MFSK-8 Tonabstand = 31,25 Hz = Bin-Abstand bei 256 Samples @ 8000 Hz.
Aber die Kanalbasen (400, 650, 900 Hz...) sind **keine Vielfachen von 31,25 Hz**:
```
900 Hz / 31,25 = 28,8  → nicht ganzzahlig → kein exakter Bin!
```
Ohne Zero-Padding zeigt der FFT-Peak bei 906,25 Hz statt 900 Hz.

### Die Lösung: Zero-Padding auf 4096 Punkte
```
256 Samples → Bin-Abstand 31,25 Hz   (unbrauchbar)
4096 Punkte → Bin-Abstand  1,95 Hz   (ausreichend genau)
```
Bei 4096 Punkten liegt Bin 461 bei 900,39 Hz → Δ = 0,39 Hz. ✓

**Merksatz:** Zero-Padding erzeugt keine neue Information — es interpoliert
das vorhandene Spektrum feiner. Die Frequenzauflösung (Rayleigh-Kriterium)
bleibt durch die Fensterlänge begrenzt; die Ablesegenauigkeit verbessert sich.

### Spectral Leakage im Audacity-Spektrum
Das breite Frequenzkontinuum unter dem MFSK-Signal (von ~300 Hz bis ~3000 Hz)
ist Spectral Leakage durch rechteckige Symbolfenster. Jedes Symbol wird
abrupt ein- und ausgeblendet → Sinc-förmige Seitenkeullen im Spektrum.

**Abhilfe (implementiert in Phase 7):** Raised-Cosine-Fensterung der Symbolflanken
reduziert die Seitenkeullen um ~27 dB (Faktor ~500 weniger Splatter-Leistung).
Für On-Air-Betrieb aktiv (`window=True`).

---

## 4. Phasenkontinuität — was sie ist und wie man sie testet

### Was Phasenkontinuität bedeutet
Beim Übergang zwischen zwei MFSK-Tönen wird der **Phasenwinkel** der Sinuswelle
weitergeführt, nicht zurückgesetzt. Das vermeidet HF-Knackgeräusche und
Spektralausbreitung.

```python
# Phasenkontinuierlich (richtig):
phase = (phase + omega * SAMPLES_PER_SYM) % (2 * np.pi)

# Phasendiskontinuierlich (falsch):
phase = 0.0  # Reset bei jedem Symbol → Knack
```

### Was Phasenkontinuität NICHT bedeutet
Die Amplitude der Sinuswelle kann am Symbolübergang springen — das ist normal.
Eine Sinuswelle kann zwischen Sample N und Sample N+1 von +0,9 auf −0,8 fallen.
Das ist kein Phasenknick, sondern normales Sinus-Verhalten.

**Fehlerhafter Testansatz:** Amplitudensprung am Übergang messen.
**Richtiger Testansatz:** Alle 8 Symbole modulieren und demodulieren — kein
Symbolfehler = kein schädlicher Splatter = Phasenkontinuität funktioniert.

---

## 5. Reed-Solomon FEC — Größenverhältnisse

Ein typischer Wetter-Frame (Protokoll v0.3):

```
Payload (encode_weather):   14 Byte
Frame-Body (build_frame):   22 Byte  (+8: TYPE+CHANNEL+FROM+CRC)
RS-kodiert (rs_encode):     54 Byte  (+32: RS-Parität)
Symbol-Stream:              152 Symbole  (8 SYNC + 144 Daten)
Audiodauer:                  4,86 s  (mit PTT-Vor/Nachlauf)
```

**Wichtig:** RS(255,223) ist ein "shortened code" für kurze Frames.
`reedsolo` handhabt das automatisch durch Zero-Padding auf K=223 Byte.
Der Overhead ist trotzdem immer 32 Byte, egal wie kurz der Frame ist.

Für Frames unter ~20 Byte wäre RS(31,15) (16 Byte Parität für 15 Byte Daten)
effizienter. Das ist eine offene Optimierungsfrage für spätere Versionen.

### RS-Decoder Loop — kritischer Bug (Phase 7)

Im Breitband-Modus liefert der Demodulator viele Datensymbole (Signal + Stille
bis Dateiende). Die ursprüngliche RS-Loop suchte nur 9 Byte-Längen ab n_bytes_max:
```python
# ALT (fehlerhaft): nur 9 Schritte, erreicht nie 53 Byte bei n_bytes_max=72
range(n_bytes_max, max(n_bytes_max - 9, rs_min) - 1, -1)

# NEU (korrekt): voller Bereich bis rs_min
range(n_bytes_max, rs_min - 1, -1)
```
Symptom: Breitband-SYNC gefunden, CRC=None, RS-Fehler trotz gutem Signal.
Fix: Range auf `rs_min = RS_OVERHEAD + 9` (v0.3 Minimum) ausdehnen.

---

## 6. SYNC-Preamble — Protokoll v0.2 vs. v0.3

### v0.2: [7, 0, 7, 0] — 4 Symbole = 128 ms
Zu kurz für reale KW-Bedingungen. Decoder muss Kanal vorab kennen.

### v0.3: [7, 0, 7, 0, 7, 0, 7, 0] — 8 Symbole = 256 ms ✅
**Wichtige Eigenschaft:** Ton 7 und Ton 0 haben einen kanalunabhängigen
Abstand von `7 × 31,25 Hz = 218,75 Hz`. Ein Breitband-Decoder kann damit
das Signal ohne Vorabwissen über den Kanal finden:

```
Breitband-SYNC-Algorithmus:
  Für jedes Block-Paar (hoch/tief):
    Δf = f_hoch − f_tief
    wenn |Δf − 218,75 Hz| < 8 Hz  UND  Streuung < 15 Hz:
      SYNC gefunden → Basisfrequenz = f_tief
      Kanal = nächster Kanal zu f_tief
      Offset = f_tief − channel_frequency(Kanal)
```

**Ergebnis On-Air:** IC-7610 TX auf 14.110,000 MHz, SDRplay RX.
SYNC automatisch auf Kanal 2 gefunden, Offset +38,5 Hz erkannt, Frame
vollständig dekodiert — ohne jegliche manuelle Parameterübergabe.

---

## 7. Protokoll v0.3 — Frame-Header mit CHANNEL-Byte

### Neues Frame-Format
```
v0.2:  TYPE(1) | FROM(4) | PAYLOAD(var) | CRC(2)
v0.3:  TYPE(1) | CHANNEL(1) | FROM(4) | PAYLOAD(var) | CRC(2)
```

### CHANNEL-Byte
- Bits 3–0: Kanal 0–9
- Bits 7–4: reserviert (0x00)
- CRC deckt TYPE + CHANNEL + FROM + PAYLOAD

### Nutzen
Der Decoder kann nach dem SYNC-Fund die Kanalangabe im Header mit dem
erkannten Kanal vergleichen — doppelte Konsistenzprüfung. Fehlleitung durch
zufällige SYNC-Matches auf falschen Kanälen wird dadurch erkannt.

### Protokoll-Versionen sind nicht kompatibel
v0.2-Sender → v0.3-Decoder: CHANNEL-Byte fehlt → falsche FROM-Adresse, CRC-Fehler.
v0.3-Sender → v0.2-Decoder: 8-Symbol-SYNC wird nicht erkannt.
**Kein Backward-Compatibility-Mechanismus vorgesehen** (bewusste Entscheidung,
da Protokoll noch nicht veröffentlicht).

---

## 8. Dateiformat-Übersicht (WAV vs. CF32)

| Eigenschaft    | WAV (.wav)             | Complex Float 32 (.cf32)        |
|:---------------|:-----------------------|:--------------------------------|
| Inhalt         | Reelles Audiosignal    | Komplexe IQ-Samples (I+jQ)      |
| Öffnen mit     | Audacity, VLC, ...     | inspectrum, GNU Radio, GQRX     |
| Spektrum       | Einseitig (0–4000 Hz)  | Zweiseitig (−4000 bis +4000 Hz) |
| Erzeugung      | `save_wav()`           | `save_cf32()` via Hilbert-Trafo |
| Sample Rate    | in Datei gespeichert   | muss manuell in inspectrum      |
|                |                        | eingegeben werden (8000 Hz)     |

### load_wav() — robuste Implementation (Phase 7)
```python
# Unterstützt automatisch:
# - uint8  (8-Bit unsigned, SDRplay-Export)
# - int16  (Standard 16-Bit PCM)
# - int32  (24/32-Bit)
# - Stereo → Mono (Kanal 0)
# - Falsche Sample Rate → automatisches Resampling auf 8000 Hz
#   (scipy.signal.resample_poly, Anti-Aliasing eingebaut)
```

**Praxiserfahrung:** SDRplay RSPdx2 exportiert mit 48 kHz. Nach Resampling
6:1 auf 8 kHz wird der Frame korrekt dekodiert. Kein manueller Schritt nötig.

---

## 9. Basis-40 Rufzeichen-Kodierung — Grenzen

40 Zeichen → max. 40^6 = 4.096.000.000 Kombinationen → exakt 4 Byte (uint32).

**Wichtige Einschränkung:** Rufzeichen mit mehr als 6 Zeichen werden auf 6
Zeichen gekürzt. `VK2XX/P` (7 Zeichen) → `VK2XX/` (6 Zeichen) verlustbehaftet.

Für Portable- und Maritime-Mobile-Suffixe (/P, /MM, /M) ist das ein bekanntes
Problem. Lösungsansätze für spätere Versionen:
- Suffix als eigenes Feld im Frame-Header (1 Byte)
- Erweiterte Basis-40-Kodierung mit 5 Byte (40^7 = 163 Mrd. Kombinationen)

---

## 10. On-Air-Erfahrungen — IC-7610 + SDRplay RSPdx2

### Hardware-Setup
```
PC Windows 11, Python 3.14
  ├── IC-7610
  │     ├── USB-Audio → Soundkarte ID 9 (MME) → NF-Eingang ACC-Buchse
  │     ├── USB-CAT  → rigctld -m 3085 → hamlib PTT
  │     └── HF-Antenne (Dipol 14 MHz)
  └── SDRplay RSPdx2 (RX-Referenz, 48 kHz WAV-Export)
```

### IC-7610 Einstellungen für GUST TX
| Parameter | Wert | Bemerkung |
|:----------|:----:|:----------|
| ACC/USB AF Input Level | 40% | Höher → ALC-Übersteuerung |
| Software-Pegel (`level`) | 10% | In gateway.json |
| PTT-Backend | hamlib | rigctld auf localhost:4532 |
| Audio-Gerät | ID 9 (MME) | Nicht DirectSound/WASAPI (Namenskollision) |
| Betriebsart | USB | Pflicht für korrektes NF-Seitenband |
| Dial-Frequenz | 14.110,000 MHz | Gleich für TX und RX |

### ALC-Problem und Diagnose
Symptom: Spektrum zeigt zwei Peaks statt Kammstruktur, Signal verzerrt.
Ursache: ALC greift ein bei zu hohem NF-Eingangspegel.
Lösung: ACC/USB Input auf 40% + Software-Level auf 10%.
Diagnose-Tool: ALC-Anzeige am IC-7610 während TX — muss unter Rot bleiben.

### Frequenzversatz IC-7610 / SDRplay
Gemessen: **+38,5 Hz** (IC-7610 sendet bei 14.110,900 MHz,
SDRplay bei 14.110,000 MHz → NF erscheint bei 938,5 Hz statt 900 Hz).
Der Breitband-Decoder erkennt und kompensiert diesen Offset automatisch.
Für manuellen Direktscan: `--offset 38` oder `--offset 40`.

### Frequenzversatz HackRF / IC-7610 (SNR-Messaufbau)
Im SNR-Messaufbau (HackRF TX → IC-7610 RX, anderer Pfad) zeigte sich ein
konsistenter Netto-Offset von **−14 bis −20 Hz** über alle Decodes — Kombination
aus HackRF-TX-Ablage (kein TCXO) und IC-7610-RX-Dial. Das Frequenz-Refinement
(`_refine_sync`) erfasst und kompensiert ihn automatisch und zuverlässig; die enge
Streuung (±3 Hz) ist selbst ein Beleg für die Genauigkeit des Refinements.

### Audio-Gerät: Namenskollision unter Windows
Windows meldet denselben Gerätenamen dreimal (MME, DirectSound, WASAPI).
sounddevice wirft `Multiple output devices found` bei Namensübergabe.
**Lösung:** Gerätenummer (Integer) statt Name in gateway.json verwenden.
```json
"audio": { "device": 9 }   // ← Nummer, nicht Name
```
Gerätenummern mit `py gust.py devices` ermitteln.

### gateway.json Referenzkonfiguration
```json
{
    "callsign": "OE3GAS",
    "audio": {
        "device": 9,
        "ptt_backend": "hamlib",
        "level": 10,
        "hamlib_host": "localhost",
        "hamlib_port": 4532
    },
    "gateway": { "interval_s": 300 },
    "web": { "host": "0.0.0.0", "port": 8080, "api_key": "" }
}
```

---

## 11. PTT-Backend — Bugs und Fixes (Phase 7)

### Dreifacher PTT-Release (Bug)
**Symptom:** `T 0 → TX AUS` erscheint dreimal in der Ausgabe.
**Ursache:** `transmit_audio()` finally, `AudioTransmitter.close()` und
`HamlibPTT.close()` riefen alle einzeln `release()` auf.
**Fix:** Alle PTT-Backends (`NullPTT`, `GPIUPTT`, `HamlibPTT`) bekamen
ein `_active`-Flag. `release()` ist jetzt idempotent — Mehrfachaufrufe
sind lautlos. `HamlibPTT.close()` ruft `release()` nicht mehr selbst auf.

---

## 12. Decoder-Robustheit — die drei Ursachen (Phase 7 Überarbeitung)

### Symptom
Live-Tests (HackRF TX → IC-7610 RX) dekodierten trotz hohem SNR (20+ dB) nur
~1 von 5 Frames. Im Direktmodus (Kanal bekannt) klappte alles, im Breitbandmodus
(channel=None) fast nichts. Drei voneinander unabhängige Ursachen:

### Ursache 1 — Frequenz-Quantisierung
Die SYNC-Suche rasterte f0 in **8-Hz-Schritten**. Die `_fft_detect_symbol`-Funktion
tastet pro Ton genau **einen** FFT-Bin ab. Liegt der wahre f0 um mehr als ~4 Hz vom
Rasterpunkt entfernt, liegt die Tonenergie ~2 Bins daneben, der abgetastete Bin ist
fast leer → falsches Symbol → CRC scheitert. Messung: Decode klappt nur für f0
innerhalb ~±6 Hz vom wahren Wert; das 8-Hz-Raster traf das selten.
Verschärfend: der Dedup (±30 Hz) warf nahe, dekodierbare Rasterpunkte zugunsten des
höchstbewerteten aber undekodierbaren weg.

**Fix:** `_refine_sync()` schärft f0 nach dem Grob-Fund nach — Single-Bin-SYNC-Energie
über die 8 SYNC-Symbole hat ein **scharfes parabolisches Maximum** am wahren f0.
Grob ±18 Hz/2 Hz, dann fein ±2 Hz/0,5 Hz → < 1 Hz Restfehler.

### Ursache 2 — Scan-Range zu klein
Der Scan ging nur 380–2580 Hz. Kanal 9 liegt bei **2650 Hz** — komplett außerhalb.
**Fix:** Range auf 320–2760 Hz (deckt Kanal 0–9 inkl. ±100 Hz Offset; Ton 7 von
Kanal 9 = 2868,75 Hz < Nyquist 4000 Hz).

### Ursache 3 — Timing-Quantisierung
Die SYNC-Suche prüfte nur **block-ausgerichtete** Positionen (256-Sample-Raster).
Ein Frame im Ringpuffer beginnt aber an beliebiger Sample-Position. Bei +128 Samples
Versatz (halbes Symbol) straddelt jedes FFT-Fenster zwei Symbole zu gleichen Teilen
→ Decode scheitert. Da Frames zufällig im Puffer liegen, traf das ~die Hälfte.

**Fix:** SYNC-Suche auf **Halb-Block-Auflösung** (128-Sample-Raster, überlappende
256-Sample-Fenster via `as_strided`). Damit ist der Auswahl-Timingfehler ≤ ±64 Samples,
und `_refine_sync()` schärft anschließend sample-genau nach (Timing grob ±192/16,
fein ±16/4). Ergebnis: Sandbox 1/5 → **10/10 Kanäle**, alle Sample-Offsets **16/16**,
Dual **5/5**; live **5/5**.

**Merksatz:** Die grobe Suche dient nur dem Auffinden; die *Genauigkeit* (Frequenz
und Timing) kommt aus dem Refinement vor der Datenextraktion. Coarse-find +
fine-refine ist schneller und robuster als ein fein gerastertes Coarse-Scan.

---

## 13. HackRF Dual-Kanal-TX und der Underrun-Hänger

### Dual-Kanal-Erzeugung
Zwei NF-Kanäle werden getrennt moduliert, je per `nf_to_iq_usb()` ins komplexe
Basisband gehoben, summiert und auf 0,9 Peak normalisiert. `transmit_iq()` sendet
das fertige IQ-Array über dasselbe Device-Handle wie `transmit()`.

### Der Underrun-Hänger (wichtige Hardware-Erkenntnis)
Ein zu langer `writeStream`-Timeout (`timeoutUs=1000000`, 1 s) verursachte beim
ersten Lauf einen TX-Underrun. Folgenkette:
1. Underrun hinterließ die HackRF-**Firmware in festgefahrenem TX-Zustand**
   (akzeptierte exakt 1.966.080 Samples = 480 × 4096 = SoapySDR-Puffergröße,
   dann Stillstand, brummender Träger).
2. Ab da schlugen **alle** Sendungen fehl — auch mit korrektem Default-Timeout —
   bis zum **USB-Neustart** des HackRF.
3. Mit Default-Timeout + Neustart: 100% Übertragung.

**Lehre:** Nie einen langen `writeStream`-Timeout verwenden. Write-Loop exakt wie
die bewährte `transmit()`:
```python
BLOCK = 4096
pos = 0
while pos < len(iq):
    sr = self._sdr.writeStream(tx_stream, [iq[pos:pos+BLOCK]], BLOCK)  # Default-Timeout!
    pos += sr.ret if sr.ret > 0 else BLOCK
```
Diagnose-Tool `hackrf_diag.py` mit Watchdog-Timer und Per-Aufruf-Timing pinpointete
den hängenden `writeStream`. Bei wiederholten Fehlversuchen zuerst HackRF stromlos
machen (USB ab/an), bevor man Code-Ursachen sucht — die Firmware merkt sich den
schlechten Zustand.

---

## 13a. Generischer SoapySDR-TX (`gust_soapy_tx.py`, P7-04 / ADR-16)

`gust_hackrf.py` (HackRF) und `Soapy7610Transmitter` (IC-7610) sind beide nur
Spezialfälle desselben Musters: SoapySDR-Device öffnen, `CF32`-Stream aufbauen,
Block-weise schreiben, sauber schließen. ADR-16 zieht diese Schicht in
`gust_soapy_tx.py` heraus — `SoapyTxBackend` ist eine dünne, treiberneutrale
Klasse, „7610" ist kein Sonderfall mehr, sondern nur ein gespeicherter Args-Satz.

### Discovery statt Hardcoding
Geräte werden **ausschließlich** über `SoapySDR.Device.enumerate()` gefunden.
Im Web-UI gibt es bewusst kein Args-Eingabefeld — Recovery bei fehlendem Gerät
erfolgt über einen „Rescan"-Button (REST `GET /api/sdr/devices` re-enumeriert).

### Persistenz nach Identität, nicht nach Index
In `gateway.json.sdr_tx.device_args` werden die vollen Args (mindestens
`driver` + `serial`/`label`) gespeichert. **Nicht** der Enumerations-Index —
die Reihenfolge ist nach Reboot/USB-Replug instabil (dieselbe Lehre wie ADR-09
bei Audiogeräten). Die Identitätswahl bevorzugt `serial`, fällt zurück auf
`label`/`device_id`.

### TX-Fähigkeit
`enumerate_tx_devices()` öffnet jedes gefundene Gerät kurz und liest
`getNumChannels(SOAPY_SDR_TX)`. RX-only-Geräte (typisch RTL-SDR) bekommen
`tx_capable: false` — die Web-UI grayed sie aus.

### Treiberabhängige Parameter dynamisch
Nach Auswahl liest das UI über `GET /api/sdr/caps?driver=…&serial=…` die
Gain-Elemente (`listGains` + `getGainRange` je Element), Sample-Rate-Liste/Range
(`listSampleRates`/`getSampleRateRange`) und Antennen-Ports (`listAntennas`)
vom Gerät — nicht hartcodiert. Gain wird **normalisiert (0..1)** gespeichert
und beim Senden auf die Gesamt-Range gemappt; alternativ als benannte Elemente
(`{"AMP": 0, "VGA": 14}`), was treiberspezifisch ist.

### Write-Loop unverändert
Die `transmit_iq()`-Schleife ist Zeile für Zeile dieselbe wie
`gust_hackrf.HackRFTransmitter.transmit_iq()` (ADR-13): Default-Timeout,
`BLOCK = 4096`, `pos += sr.ret if sr.ret > 0 else BLOCK`. Der Underrun-Hänger
aus §13 ist eine harte Lektion, die generisch gilt.

### Modul-Diagnose
`SoapySDR.listModules()` (+ `getModuleVersion`) wird im UI als ausklappbare
Anzeige unter dem Dropdown gezeigt — rein diagnostisch, damit erkennbar ist,
ob das passende Treibermodul (`SoapyHackRF.dll`, `Soapy7610`, …) geladen ist.
Bewusst kein Eingabefeld (siehe ADR-16).

### Verdrahtung im TX-Pfad
`gust.py:cmd_tx()` prüft `cfg["sdr_tx"]["enabled"]`: bei `true` wird über
`_tx_via_sdr()` der SoapySDR-Pfad genutzt (NF→IQ via `nf_to_iq_usb()`, dann
`SoapyTxBackend.transmit_iq()`). Bei `false` läuft alles wie bisher über
`AudioTransmitter` + PTT. Frame-Layer, Modulator und Event-Bus bleiben
unangetastet. Die alten Klassen `HackRFTransmitter` / `Soapy7610Transmitter`
bleiben für Bestandscode (`gust_tx_test.py`) lauffähig.

---

## 14. Parallelkanal-Diversity

### Prinzip
Notfall-Frames können gleichzeitig auf zwei NF-Kanälen gesendet werden. Der RX-Scan
findet beide als getrennte SYNC-Kandidaten. Der erste, der die CRC besteht, wird als
Frame ausgegeben; der zweite (gleicher Inhalt) wird vom Dedup-Cache unterdrückt
(0 oder wenige „Duplikate" in der Statistik = beide dekodierten).

### Tradeoff (gemessen)
Die Sendeleistung wird auf zwei Kanäle aufgeteilt: nach Mischen und Normalisieren
hat jeder Kanal ~6 dB weniger als ein Simplex-Signal bei gleichem Gain. Gegenrechnung:
zwei unabhängige Empfangschancen.

| Bei gleichem Gain | Simplex SNR/Kanal | Dual SNR/Kanal |
|---|---|---|
| Gain 10 dB | 22,0 dB | 18,3 dB |
| Gain 1 dB | 15,9 dB | 10,1 dB |

### Diversity-Gewinn (empirisch)
Simplex verlor gelegentlich einen Frame (~1/6–1/11) durch Fenstertiming — jeder Frame
erscheint nur in 2–3 Scanfenstern, gelegentlich richtete sich keines sauber aus
(siehe BUG-07, inzwischen behoben → Abschnitt 16). Dual erreichte in allen Läufen
100% (15/15), weil der zweite Kanal genau diese Aussetzer auffängt. **Der Parallelkanal
verdient seine Existenz**, sobald die Bedingungen marginal werden — bei On-Air-QRM, das
einen Kanal trifft, übernimmt der andere. Der Timing-Fix beseitigt die Simplex-Misses,
der Diversity-Gewinn gegen *frequenzselektives* QRM bleibt davon unberührt.

---

## 15. SNR-Baseline und der Schätzer-Fehler an der Bandkante

### Baseline (P7-05, siehe Testplan T-10.2)
Decoder dekodiert bis mindestens **10,1 dB angezeigtem SNR** (Dual, Gain 1 dB).
Der TX-Boden wurde erreicht, *bevor* der Decoder aussetzte → echte Schwelle ≤ 10 dB.
Score bei erfolgreichen Decodes durchweg 0,94–1,00 (FEC-Cliff-Verhalten).
Absolutwert setup-spezifisch (starke Kopplung); belastbare Aussage: „zuverlässig
bis ~10 dB SNR".

### Der SNR-Schätzer-Fehler (Kanal 0/1)
`_measure_audio_snr()` misst Rauschen im Band **200–380 Hz**. Das liegt direkt unter
Kanal 0 (Töne 400–619 Hz). Die Signalenergie leckt in die „Rausch"-Messung und
invertiert das Verhältnis: Kanal-0-Frames dekodieren sauber (Score 0,96–0,999), die
SNR-Anzeige sagt aber **−7 bis −11 dB** — etwa 30 dB zu niedrig. Reiner Anzeigefehler,
der Decoder ist unbeeinträchtigt.

**Fix (implementiert):** Rauschreferenzband adaptiv relativ zum Signalband
[f0, f0+218,75 Hz] wählen — auf beiden Seiten mit 80 Hz Guard messen und die
*niedrigere* Schätzung nehmen. Die durch Signal-Skirt (Kanal 0 unten) oder einen
Nachbarkanal kontaminierte Seite wird damit automatisch verworfen. Ergebnis: alle
Kanäle lesen konsistent (Bandkanten wie Mitte), die SNR skaliert wieder sauber mit
der Signalstärke. Ein Frame mit echtem −11 dB SNR könnte nie mit Score 0,96
dekodieren; die frühere negative Anzeige war der eindeutige Beleg für den Messfehler.

### Kanal 0 ist solide
Reproduzierbarkeitstest (5× Kanal 0, Gain 6): **5/5 dekodiert**. Ein früherer
Einzelausfall auf Kanal 0 war Fenstertiming-Pech (BUG-07), kein Kanal-Problem.
Die gelegentlichen Simplex-Misses wandern über verschiedene Kanäle (mal 0, mal 8) —
sie sind timing-, nicht kanal- oder frequenzabhängig.

---

## 16. Scan-Scheduling und die Vollfenster-Garantie (BUG-07-Fix)

### Das Problem
Der RX-Loop scannt einen gleitenden Snapshot der letzten `WINDOW_S` Sekunden aus dem
Ringpuffer. Damit eine Sendung sauber dekodiert, muss sie in *mindestens einem* Scan
**vollständig und ausgerichtet** enthalten sein. Geometrisch: ein Frame der Dauer D ist
in einem Scan zum Zeitpunkt t_scan genau dann vollständig enthalten, wenn
t_scan ∈ [F+D, F+WINDOW_S] (F = Frame-Start im Puffer). Dieses Akzeptanzfenster ist
`WINDOW_S − D` breit. Liegt das **effektive** Capture-Intervall unter dieser Breite,
trifft garantiert jeder Frame ein Vollfenster.

### Die Ursache (subtil)
Die alte Schleife war `sleep(interval) → snapshot → decode`. Die variable Decode-Zeit
(0,5–1,3 s) addierte sich also auf das Intervall: effektives Capture-Intervall =
interval + decode = bis zu **3,3 s**. Bei Fenster 8 s und Frame 5,4 s ist die
Akzeptanzbreite nur 8 − 5,4 = **2,6 s**. 3,3 s > 2,6 s → es entsteht eine Lücke, durch
die Frames fallen. Eine Timing-Simulation (20 000 zufällige Frame-Phasen) reproduzierte
exakt die live beobachtete Miss-Rate: **10,55%** ≈ die empirischen ~1/6–1/11.

### Der Fix: Fixed-Cadence-Scheduling
Der nächste Snapshot wird auf einen *festen Zeitplan* gelegt (`next_tick += interval`),
nicht erst nach der Verarbeitung. Die Decode-Zeit wird in den verbleibenden Sleep
absorbiert. Solange ein Decode kürzer als das Intervall ist (1,3 s < 2,0 s), bleibt das
effektive Capture-Intervall konstant = `interval` = 2,0 s. Überzieht ein Decode
ausnahmsweise das Intervall, gibt es genau eine längere Lücke, danach resynchronisiert
der Zeitplan (`next_tick = jetzt`) — kein Aufstauen. Gemessener Drift über 10 Scans:
< 1 ms.

### Die Garantie als Invariante
Die Vollfenster-Bedingung ist jetzt eine explizite, prüfbare Invariante:

> **WINDOW_S ≥ MAX_FRAME_S + SCAN_INTERVAL_S**

Defaults: 9,0 ≥ 5,5 + 2,0 = 7,5 s → Marge **1,5 s**. Der RX-Loop prüft sie beim Start
und gibt sie aus (bzw. warnt, falls eine Konfiguration sie verletzt). Fenster von 8 auf
9 s erhöht, damit auch lange TEXT-Fragmente (~5,4 s) plus Timing-Jitter sicher passen.
Simulation nach Fix: **0 Misses** bei 20 000 Trials.

### Lernpunkt
Fixed-Cadence allein (sogar mit dem alten 8-s-Fenster) erreichte in der Simulation
schon 0% — die eigentliche Wurzel war das *aufgeblähte* Intervall, nicht das Fenster.
Das größere Fenster ist die Sicherheitsmarge gegen längere Frames und Jitter. Für die
Notfall-Frames bleibt der Parallelkanal die zweite, von Timing unabhängige Absicherung.

---

## X. Costas-Array SYNC — Warum nicht mehr [7,0,7,0,7,0,7,0]?

### Das Problem mit dem alternierenden SYNC

Der ursprüngliche SYNC `[7,0,7,0,7,0,7,0]` hatte drei Schwächen:

1. **Nur 2 von 8 Tönen** werden angeregt → kein Passband-Equalizer möglich
2. **Mehrdeutige Autokorrelation**: der periodische Wechsel erzeugt Nebenpeaks
   → Fehlsynchronisation bei schlechtem SNR
3. **Binäre Detektion**: der Detektor konnte nur "Ton 0 vs. Ton 7" unterscheiden

### Costas-Array Ordnung 8

Ein Costas-Array der Ordnung N ist eine Permutation von {0…N−1}, bei der
alle Differenzvektoren (Δposition, Δtonwert) eindeutig sind. Die Konsequenz:
die diskrete 2D-Autokorrelationsmatrix hat exakt einen einzigen Peak.

FT8 von Joe Taylor K1JT verwendet das Costas-Prinzip für seinen 7-FSK-SYNC.
GUST übernimmt es für MFSK-8 (8 Töne → Ordnung 8).

**GUST v0.5 SYNC:** `[2, 0, 6, 7, 1, 4, 3, 5]`

Maschinell verifiziert (alle 28 Differenzvektoren eindeutig):
```python
def is_costas(seq):
    seen = set()
    for i in range(len(seq)):
        for j in range(i+1, len(seq)):
            vec = (j-i, seq[j]-seq[i])
            if vec in seen: return False
            seen.add(vec)
    return True
assert is_costas([2,0,6,7,1,4,3,5])   # → True
```

### Bonus: Passband-Equalizer

Da alle 8 Töne je einmal in der SYNC-Sequenz vorkommen, kann der Empfänger
die Amplitude jedes Tons messen und daraus einen Korrektionsvektor ableiten.
`_build_equalizer()` in `gust_modulator.py` implementiert das.
Aktivierung: `demodulate(audio, use_equalizer=True)`.

### Sync-Detektor-Änderung

Der bisherige binäre Energie-Vergleich (Ton 0 vs. Ton 7) in
`_find_sync_candidates()` wurde durch ein 8-Ton-Scoring ersetzt:

```
Für jede Kandidaten-Position und Basisfrequenz f0:
  score = mittlere Energie-Fraktion des erwarteten Tons
          über alle 8 SYNC-Positionen
SCORE_MIN = 0.35 (statt 0.70 beim binären SYNC)
```

Der Wert 0.35 erscheint niedrig, ist aber korrekt: bei 8 gleichmäßig
verteilten Tönen ist die Fraktion des richtigen Tons im Rauschen 1/8 = 0.125,
bei sauberem Signal ~0.5–0.8. SCORE_MIN=0.35 liegt komfortabel dazwischen.
Der CRC-Check verhindert Fehldecodierungen zuverlässig.

---

## Y. IQ-Eingang — Direktempfang ohne Transceiver (Phase 9)

### Motivation

Audio-Dekodierung über den SSB-Demodulator eines Transceivers hat einen
fundamentalen Nachteil: der Transceiver-Filter ist für Sprachverständlichkeit
optimiert, nicht für GUST. Randkanäle leiden unter dem Rolloff.

Mit einem SDR (RTL-SDR, SDRplay, HackRF) im IQ-Modus entfällt dieses Problem:
- Eigener FIR-Bandpass, ±0,1 dB Flatness über die gesamten 2 kHz
- Alle 8 Kanäle gleichzeitig durch digitales Filterbank
- Kein Transceiver erforderlich (RTL-SDR ~25 EUR)

### Architektur (gust_iq_rx.py)

```
RTL-SDR @ 250 kHz IQ
    │
    ├─ PPM-Korrektur (Frequenzfehler-Kompensation)
    │
    ├─ 8× FIR-Bandpass (scipy.signal.firwin, je 250 Hz breit)
    │   Kanal 0: 550–900 Hz, Kanal 7: 2300–2650 Hz
    │
    ├─ Downsampling 250 kHz → 8000 Hz (resample_poly)
    │
    └─ demodulate(audio, channel=k, use_equalizer=True)
       ↑ bestehende Funktion, unverändert
```

### RTL-SDR Kalibrierung

RTL-SDR-Oszillatoren haben typisch ±50–100 ppm Frequenzfehler.
Bei 14 MHz = ±700–1400 Hz — kritisch für GUST.
Einmalige Kalibrierung mit `rtl_test -p`, Wert in `gateway.json`:
```json
"rtlsdr": { "ppm_correction": 3 }
```
Nach Kalibrierung: ±2–5 ppm → ±28–70 Hz → unkritisch für GUST.

### SNR-Verbesserung gegenüber Audio

| Kanal | Audio (SSB) | IQ-Eingang |
|---|---|---|
| Randkanäle (0, 7) | Basis | +3…+8 dB eff. SNR |
| Mittelkanäle | Basis | +0…+2 dB |
| Alle Kanäle gleichzeitig | Nein | Ja (Filterbank) |

---

## Z. Connector Layer — Semantic Bridging (Phase 6, Konzept)

### Problem: Semantic Impedance

MQTT-Nachrichten sprechen eine andere Sprache als GUST-Frames.
Ein JSON von einer Wetterstation enthält `"temperature": 18.3` —
GUST erwartet `encode_weather(temp_c=18.3, ...)`.
Die Übersetzung muss irgendwo stattfinden.

### Lösung: Dedizierte Connector-Schicht

```
Externe Welt (MQTT, HTTP-Webhook, Meshtastic, APRS)
    ↓
gust_connector.py — GustConnector ABC + ConnectorRegistry
    ↓
gust_transforms.py — Transform-Funktionen + SemanticMapping
    ↓
connectors.yaml — Konfiguration: Topics, Mappings, Broker
    ↓
Event-Bus — unverändert
    ↓
Frame-Layer — gust_frame.py unverändert
```

### Kernprinzipien

- **gust_frame.py bleibt unberührt** — kein einziger Byte geändert
- **Semantik ist konfiguriert**, nicht hart kodiert (YAML-Mapping)
- **Bidirektional**: eingehend (extern → HF) und ausgehend (HF → extern)
- **Erweiterbar**: MQTT ist nur ein Connector von vielen (Webhook, Meshtastic...)

### Neue Dateien (Phase 6)

| Datei | Inhalt |
|---|---|
| `gust_connector.py` | `GustConnector` ABC + `ConnectorRegistry` |
| `gust_mqtt.py` | `MQTTConnector` Implementierung (ersetzt P6-01/02) |
| `gust_transforms.py` | Transform-Bibliothek: `weather_from_ha_json`, etc. |
| `connectors.yaml` | Topic-Routing, Broker-Config, Mappings |

### MQTT-Topic-Schema

Inbound: `gust/tx/weather` → WEATHER Frame  
Outbound: `gust/rx/weather/<rufzeichen>` → JSON mit Wetterdaten
Home Assistant: Auto-Discovery via `homeassistant/sensor/gust_*/config`

---

## 17. Implementierter Software-Stack

### Dateien

| Datei                     | Inhalt                                    | Version |
|:--------------------------|:------------------------------------------|:--------|
| `gust_frame.py`        | Frame Layer: Encoder/Decoder, CRC, Kanal  | 0.3.0   |
| `gust_modulator.py`    | MFSK-8 Mod/Demod, Breitband-RX + Refinement| 0.3.1   |
| `gust_audio.py`        | Audio TX/RX, PTT-Backends, Auto-Mono/Stereo| 1.1.0   |
| `gust_rx.py`           | Kontinuierlicher RX-Scan-Loop (asyncio)   | 1.0.0   |
| `gust_hackrf.py`       | HackRF + Soapy7610 TX (Bestand). Neuer Code soll `gust_soapy_tx` verwenden | 1.0.0 |
| `gust_soapy_tx.py`     | Generischer SoapySDR-TX-Backend (P7-04 / ADR-16) | 1.0.0   |
| `gust_decode.py`       | Standalone Decoder, Breitband-Scan CLI    | 0.2.0   |
| `gust_tx_test.py`         | TX-Mess-Skript (--channels, --gain-sequence)| 1.1.0 |
| `gust.py`              | CLI-Einstiegspunkt                        | 0.1.1   |
| `gust_meshcore_bridge.py` | MeshCore USB-Serial Bridge → RX_FRAME EventBus (P6-19) | 1.0.0 |
| `meshcore_smoketest.py`   | MeshCore Companion Smoke-Test (P6-21)     | 1.0.0   |
| `requirements.txt`        | Python-Abhängigkeiten                     | —       |

### Getestete Funktionen

| Funktion                                         | Status |
|:-------------------------------------------------|:------:|
| Rufzeichen Basis-40 Encode/Decode                | ✓      |
| CRC-16/CCITT-FALSE (Referenzwert 0x29B1)         | ✓      |
| Payload-Encoder: 0x01 Wetter                     | ✓      |
| Payload-Encoder: 0x02 Position                   | ✓      |
| Payload-Encoder: 0x20 Notfall-Beacon             | ✓      |
| Payload-Encoder: 0x40 Freitext (Fragment.)       | ✓      |
| Frame-Build / Frame-Parse v0.3 (CHANNEL-Byte)    | ✓      |
| Reed-Solomon Encode/Decode (5 Fehler korr.)      | ✓      |
| MFSK-8 Modulator (phasenkontinuierlich)          | ✓      |
| MFSK-8 Raised Cosine Windowing                   | ✓      |
| MFSK-8 Demodulator (FFT zero-padded)             | ✓      |
| Breitband-SYNC-Erkennung (channel=None)          | ✓      |
| Automatische Frequenzoffset-Erkennung            | ✓      |
| Frequenz-Fein-Refinement (_refine_sync, <1 Hz)   | ✓      |
| Timing-Refinement / Halb-Block-Auflösung         | ✓      |
| Scan-Range alle 8 Kanäle (500–2510 Hz, v0.5)     | ✓      |
| Kontinuierlicher RX-Loop (gust_rx.py)         | ✓      |
| HackRF Dual-Kanal-TX (transmit_iq)               | ✓      |
| Parallelkanal-Diversity (RX-Dedup)               | ✓      |
| load_wav() uint8 + Resampling                    | ✓      |
| WAV-Export / -Import                             | ✓      |
| CF32-Export (Hilbert)                            | ✓      |
| Kanalzuweisung (SHA-256)                         | ✓      |
| TX-Pipeline via IC-7610 + hamlib PTT             | ✓      |
| RX-Pipeline aus SDRplay 48 kHz WAV               | ✓      |
| Erster On-Air Loopback-Test (14.110 MHz, 20m)    | ✓      |
| Protokoll v0.5 — 8-Kanal-Plan (600–2600 Hz)      | ✓      |
| Costas-Array SYNC [2,0,6,7,1,4,3,5]              | ✓      |
| 8-Ton Sync-Detektor (SCORE_MIN=0.35)             | ✓      |
| Passband-Equalizer (_build_equalizer)             | ✓      |
| IQ-Eingang gust_iq_rx.py (RTL-SDR)               | ✓      |
| Connector Layer Konzept (gust_connector_konzept)  | ✓      |

---

## 18. Offene technische Fragen (nach Phase 7)

| Frage | Priorität | Wann klärbar |
|:------|:---------:|:-------------|
| ~~SNR-Schwelle GUST~~ ✅ ≤ 10 dB SNR (P7-05, T-10.2) | — | erledigt Mai 2026 |
| SNR-Vergleich GUST vs. Olivia (gleiche Bedingungen) | mittel | Phase 7/8 |
| ~~SNR-Schätzer-Fehler Kanal 0/1~~ ✅ adaptives Rauschband (BUG-06) | — | erledigt Mai 2026 |
| ~~Preamble-Länge~~ ✅ 256 ms, Costas-SYNC (P9-02) | — | erledigt Mai 2026 |
| ~~Soapy7610 TX-Pfad~~ ✅ generisch in `gust_soapy_tx.py` (P7-04 / ADR-16) | — | erledigt Mai 2026 |
| Bandplankonformität OE (§ 16 AFG) | hoch | vor regulärem Betrieb |
| RS-FEC Optimierung für kurze Frames | niedrig | Phase 8 |
| Rufzeichen > 6 Zeichen (Suffix /P) | niedrig | Phase 8 |
| Demodulator GNU Radio OOT | niedrig | Phase 7/8 |
| SCORE_MIN Costas-SYNC empirisch validieren (On-Air) | mittel | Phase 9 |
| IQ-Empfang On-Air Test (RTL-SDR, T-09.5)           | mittel | Phase 9 |
| Connector Layer implementieren (P6-06–P6-09)        | mittel | Phase 6 |

---

## 19. Verwendete Python-Bibliotheken und Lernpunkte

| Bibliothek | Einsatz | Lernpunkt |
|:-----------|:--------|:----------|
| `numpy` | FFT, Sinuserzeugung, Array-Ops | Zero-Padding mit `rfft(n=4096)` |
| `scipy.signal` | Hilbert-Transform, Resampling | `resample_poly` für beliebige Raten |
| `scipy.io.wavfile` | WAV lesen/schreiben | uint8-Format bei SDRplay-Export |
| `reedsolo` | Reed-Solomon FEC | Shortened Code automatisch |
| `struct` | Binäre Payload-Kodierung | Big-Endian `>`, Padding-Bytes `x` |
| `hashlib` | SHA-256 für Kanalzuweisung | Avalanche-Effekt in der Praxis |
| `sounddevice` | Audio TX/RX | Geräte per ID statt Name (Windows) |
| `argparse` | CLI | Subcommand-Optionen doppelt registrieren |

---

## 20. Hardware-Konfiguration — Microham USB Interface III + rigctld

### Kontext

Der Microham USB Interface III ist ein universelles CAT/PTT/CW-Interface für Transceiver. Es erstellt virtuelle serielle COM-Ports und routet CAT, PTT und CW getrennt. Bei Verwendung mit GUST und rigctld ist die korrekte Konfiguration im Microham USB Device Router entscheidend.

### Kritische Einstellung: PTT auf „none"

**Problem:** Wenn im Microham USB Device Router unter „PTT" ein COM-Port eingetragen ist (z.B. COM10), übernimmt der Router die PTT-Steuerung via RTS/DTR. rigctld versucht gleichzeitig PTT über das CAT-Protokoll (`T 1`/`T 0`) zu steuern — das führt zu Konflikten: PTT bleibt hängen, TRx schaltet nicht zurück auf RX.

**Lösung:** Im Microham USB Device Router:

| Feld | Einstellung |
|------|-------------|
| Radio | COM-Port + Baudrate (z.B. COM10, 4800 Baud) |
| CW | COM-Port + DTR (für CW-Keying, optional) |
| **PTT** | **none** ← kritisch! |
| SQL | none |

rigctld übernimmt PTT **vollständig** über das Kenwood-CAT-Protokoll (`T 1` = TX ein, `T 0` = TX aus). Kein Hardware-RTS/DTR nötig.

### Getestete Konfiguration (TS-790E + Microham USB III)

```
Microham USB Device Router:
  Radio:  COM10, 4800 8N2
  CW:     COM10, DTR
  PTT:    none          ← PTT via CAT (rigctld)
  SQL:    none

rigctld (gateway.json):
  rig_model: 2007       (Kenwood TS-790)
  device:    COM10
  baud:      4800
  auto_start: true

GUST:
  ptt_backend: hamlib
  hamlib_host: localhost
  hamlib_port: 4532
```

### Diagnose-Ablauf bei PTT-Problemen

1. GUST stoppen
2. rigctld stoppen (falls noch läuft)
3. Direkttest: `rigctl -m 2007 -r COM10 -s 4800 T 1` → TRx auf TX?
4. Falls nein: Microham USB Device Router prüfen (PTT = none?)
5. Falls ja: `rigctl -m 2007 -r COM10 -s 4800 T 0` → zurück auf RX

### Tune-Button

GUST hat einen Tune-Button (analog WSJT-X) im Config-Tab → Transceiver (Hamlib). Er sendet 15 Sekunden lang einen 1000-Hz-Sinuston mit aktivierter PTT — nützlich um Ausgangsleistung und SWR zu prüfen. Der Button ist ein Toggle: erster Klick startet, zweiter Klick stoppt vorzeitig. Das Frequenz-Polling wird während Tune automatisch pausiert um CAT-Kollisionen zu vermeiden.

### Windows IPv4/IPv6-Konflikt bei rigctld

**Windows IPv4/IPv6-Konflikt bei rigctld**
Windows löst `localhost` systemabhängig als IPv6 (`::1`) auf.
rigctld bindet dann nur auf `::1`, Python-`socket.create_connection`
verbindet jedoch auf `127.0.0.1` (IPv4) → `ConnectionRefused`, obwohl
rigctld läuft und im Task-Manager sichtbar ist.
Diagnose: `netstat -ano | findstr ":4532"` zeigt `[::1]:4532` statt
`0.0.0.0:4532`.
Fix: rigctld immer mit `-T 127.0.0.1` starten (nicht `-T localhost`).
In gateway.json: `"host": "127.0.0.1"` im `rigctld`-Block.

---

## 21. Docker-Deployment & Fresh-Install-Test

### Hintergrund

Um GUST aus der Perspektive eines Neunutzers zu testen (GitHub-Clone → Installation →
Betrieb), wurde ein Docker-Setup erstellt. Docker in WSL2 (Ubuntu) auf Windows 11
ermöglicht einen sauberen, reproduzierbaren Installationstest ohne das
Entwicklungssystem zu beeinflussen.

### Docker-Setup (vier Dateien im Repo-Root)

| Datei | Zweck |
|:------|:------|
| `Dockerfile` | `python:3.11-slim` + PortAudio + `requirements.txt` + Quellcode |
| `docker-entrypoint.sh` | Erzeugt `gateway.json` aus Umgebungsvariable, startet `daemon --sim` |
| `docker-compose.yml` | Komfortabler Wrapper mit Healthcheck auf `/api/status` |
| `.dockerignore` | Hält Image schlank (keine WAV, kein `.git`, keine `gateway.json`) |

### Verwendung

```bash
# Image bauen
docker build -t gust .

# Starten (Simulator-Modus, kein Hardware erforderlich)
docker run --rm -p 8080:8080 -e GUST_CALLSIGN=OE3GAS gust

# Mit eigener gateway.json
docker run --rm -p 8080:8080 -v ./gateway.json:/app/gateway.json gust

# Per docker compose
docker compose up
```

Web UI erreichbar unter `http://localhost:8080` (Port-Forwarding WSL2 → Windows
funktioniert automatisch).

### Docker auf Windows 11 / WSL2

Docker Desktop ist nicht zwingend erforderlich. Docker Engine direkt in WSL2
(Ubuntu) installierbar:

```bash
sudo apt update && sudo apt install -y ca-certificates curl gnupg
# Docker-Repository hinzufügen, dann:
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo service docker start
sudo usermod -aG docker $USER
```

In WSL2 kein systemd → Daemon manuell starten. Empfehlung: in `~/.bashrc` eintragen:
```bash
sudo service docker start 2>/dev/null
```

### Erkenntnisse beim ersten Build

**BUG: `requirements.txt` unvollständig** — `aiohttp` fehlte.
Auf dem Entwicklungs-PC war `aiohttp` bereits global installiert und fiel nie auf.
Der Docker-Build zeigte den Fehler sofort:
```
ModuleNotFoundError: No module named 'aiohttp'
```
Fix: `aiohttp` in `requirements.txt` ergänzt. Damit ist die Datei jetzt vollständig
und für Neuinstallationen korrekt.

**Vollständige Abhängigkeitsliste (Stand Juni 2026):**
- `numpy` — FFT, Signalverarbeitung
- `scipy` — Hilbert, Resampling, WAV
- `reedsolo` — Reed-Solomon FEC
- `sounddevice` — Audio TX/RX
- `aiohttp` — Web-Server, REST API, WebSocket ← neu ergänzt

### Scope des Docker-Deployments

| Funktion | Docker ✅ | Docker ❌ |
|:---------|:----------|:----------|
| Simulator-Modus (`--sim`) | ✅ vollständig | |
| Web UI + REST API | ✅ via Port 8080 | |
| WebSocket RX-Live-Feed | ✅ | |
| Audio TX (Soundkarte) | | ❌ kein WASAPI/MME |
| PTT / CAT (COM-Port) | | ❌ kein USB-Passthrough |
| HackRF TX | | ❌ kein USB |

Docker eignet sich als **Ziel-Deployment für Raspberry Pi Gateway-Betrieb** —
dort läuft Audio über USB-Device-Passthrough mit `--device /dev/snd`.

---

## 22. Lesson Learned: Shortened RS und FEC-Trade-off (Juni 2026)

### Ausgangslage

SNR-Sweep-Stresstest (Schicht 4) zeigte FEC-Cliff bei ~−12 dB
(Mehrkanal, 8 Kanäle gleichzeitig). Als Verbesserungsmaßnahme wurde
RS(255,223) → RS(255,191) evaluiert (32 → 64 Byte Overhead,
16 → 32 korrigierbare Byte-Fehler).

### Der Denkfehler: Annahme eines fixen 255-Byte-Blocks

Die fehlerhafte Annahme war: "Der RS-Block ist immer 255 Byte groß —
mehr Overhead bedeutet nur eine andere interne Aufteilung, die
Sendedauer bleibt konstant."

Das gilt für **full-length RS** wo tatsächlich immer 255 Byte
übertragen werden. GUST verwendet aber **shortened RS**.

### Was Shortened RS tatsächlich überträgt

Bei shortened RS wird nur so viel übertragen wie nötig:

```
Übertragene Bytes = Payload + 8 (Header: TYPE+CHANNEL+FROM+CRC) + RS_OVERHEAD
```

Beispiel WEATHER-Frame (14 Byte Payload, vgl. §5):

```
RS(255,223): 14 + 8 + 32 = 54 Byte übertragen  → 4,86 s Sendedauer
RS(255,191): 14 + 8 + 64 = 86 Byte übertragen  → 7,62 s Sendedauer
```

Das ist +59 % mehr Bytes — und damit +57 % mehr Sendezeit
(der fixe SYNC-Anteil dämpft die Verlängerung minimal).

### Auswirkung auf alle Frame-Typen

Sendedauer = (⌈Bytes·8/3⌉ + 8 SYNC) × 32 ms:

| Frame-Typ    | Payload | RS(255,223)     | RS(255,191)     | Verlängerung |
|---|---|---|---|---|
| CQ           |  5 Byte | 45 B → 4,10 s | 77 B → 6,85 s |    +67 %     |
| EMERG_RSRC   |  8 Byte | 48 B → 4,35 s | 80 B → 7,10 s |    +63 %     |
| STATION_TLM  | 10 Byte | 50 B → 4,54 s | 82 B → 7,26 s |    +60 %     |
| WEATHER      | 14 Byte | 54 B → 4,86 s | 86 B → 7,62 s |    +57 %     |
| POSITION     | 18 Byte | 58 B → 5,22 s | 90 B → 7,94 s |    +52 %     |
| EMERG_BEACON | 20 Byte | 60 B → 5,38 s | 92 B → 8,13 s |    +51 %     |
| TEXT         | 20 Byte | 60 B → 5,38 s | 92 B → 8,13 s |    +51 %     |

(8,13 s für TEXT unter RS(255,191) im Stresstest vom 06.06.2026 gemessen ✓)

Folgewirkung: Die längsten Frames (~8,1 s) verletzen die
Vollfenster-Garantie aus §16 (`WINDOW_S 9,0 ≥ MAX_FRAME_S 5,5 + 2,0`) —
ein RS-Upgrade müsste `MAX_FRAME_S` auf ~8,5 und `WINDOW_S` auf ≥ 11 s
anheben. Im Stresstest brach die Dekodierrate ohne diese Anpassung
von 87,5 % auf 25 % ein (mit 12-s-Fenster: 81,2 %).

### Bewertung und Entscheidung

Der Trade-off ist eindeutig ungünstig:

- **Kosten:** +51–67 % längere Aussendedauer je Frame-Typ,
  dazu größere RX-Fenster (mehr Latenz, mehr Contention pro Fenster, BUG-08)
- **Nutzen:** ~3–5 dB bessere SNR-Schwelle (synthetischer Stresstest)

Die kurze Aussendedauer ist eine **Kerneigenschaft von GUST** —
kompakte Frames für Telemetrie und Notfunk sind das zentrale Design-Ziel.
Eine um 50–70 % längere Sendezeit für eine marginale FEC-Verbesserung
stellt dieses Ziel in Frage.

**Entscheidung: RS(255,223) bleibt. Kein FEC-Upgrade.**

### Was der Stresstest-Cliff tatsächlich bedeutet

Der FEC-Cliff bei −12 dB im Mehrkanal-Stresstest ist kein Defekt,
sondern ein korrekter Messwert unter spezifischen Bedingungen:
8 gleichzeitige Kanäle, je ×(1/8) Amplitude → effektiv −18 dB
weniger SNR pro Kanal als bei Einzel-TX. Der On-Air-Test T-10.2
(Einzel-TX) zeigte ≤ 10 dB Schwelle — das ist der relevante Wert
für den Realbetrieb.

### Merksatz

> **Bei shortened RS skaliert die Sendedauer linear mit dem
> RS-Overhead.** Kleine Payloads (< 50 Byte) profitieren nicht
> von der 255-Byte-Block-Effizienz — jedes zusätzliche
> Paritätsbyte kostet direkt Airtime.

---

## 23. Live-Decoder Dekodierrate — Root Cause und Deep-Decoder (Juni 2026)

### Befund

Der Live-Decoder (Short: 9s/2s) erreicht auf VAC-Audio nur ~54–57%
Dekodierrate, der Batch-Decoder auf derselben Audio-Quelle 88%.
Ausgeschlossen als Ursache (empirisch verifiziert):

- Samplerate (8kHz / 44.1kHz / 48kHz getestet — kein Unterschied)
- Ringpuffer-Größe (30s → 120s — kein Unterschied)
- Scan-Intervall (100ms / 500ms / 2s — identische Rate)
- Audio-Clipping (keines)
- Ringpuffer-Wrap-around (Wahrscheinlichkeit bei 120s nur 4.6%)

Root Cause konnte nicht isoliert werden. Der einzige verbleibende
Unterschied: der Batch-Decoder verarbeitet ein statisches WAV-File,
der Live-Decoder arbeitet auf einem kontinuierlich beschriebenen
Ringpuffer. Die Hypothese lautet: der Decoder findet in manchen
Ringpuffer-Fenster-Positionen keinen sauberen SYNC, obwohl das
Audio dekodierbar wäre (Fenster-Timing vs. Frame-Phase).

### Lösung: Deep-Decoder

Statt Root Cause zu lösen: paralleler Task der analog zum
Batch-Decoder arbeitet.

**Parameter (aus Optimierungsrechnung):**
- Fenster: 20s (4× mehr Randabstand als 9s-Fenster)
- Intervall: 15s (Latenz für Nachlieferung)
- Schritt: 2s (identisch zum Short-Decoder)
- CPU: 0.3× Zusatzlast des Short-Decoders

**Architektur:**

```
AudioRXLoop.run()
  ├─ Short-Decoder      9s-Fenster / 2s-Takt, self._executor (8 Worker)
  ├─ _level_publisher   RMS/Peak alle 250 ms (Web-UI Audio-Meter)
  └─ _deep_decoder      nur bei rx.deep_decode=true (gateway.json)
       alle 15s:  get_snapshot(20s)  ← SELBER Ringpuffer
         Sliding-Window, Schritt 2s:
           pro Offset 8 Kanäle parallel auf _deep_executor
           (eigener Pool! BUG-18: geteilter Pool → Short-Decoder
            verhungert, Regression 55 → 44 Frames)
         tx_start_s = deep_tick − 20s + offset_s + sync_offset_s
         gemeinsamer _DedupCache (TOL_S 1,5s, BUG-19)
           → Short-Decoder-Funde automatisch unterdrückt,
             nur echte Zusatzfunde passieren
         Event mit deep=True → Web-UI zeigt 🔍-Badge
```

**Wichtige Implementierungspunkte:**

1. **Eigener ThreadPoolExecutor** (`_deep_executor`, N_CHANNELS
   Worker, nur bei aktiviertem Feature angelegt) — die ~80
   Deep-Decodes pro Scan dürfen den Short-Decoder-Pool nicht
   belegen (BUG-18 / ADR-27).
2. **Gemeinsamer Dedup-Cache** mit dem Short-Decoder: der
   Sendezeitstempel-Schlüssel (Kanal + Rufzeichen + tx_start)
   funktioniert scan-übergreifend. Der `sync_offset_s`-Jitter
   zwischen verschiedenen Snapshot-Ankern beträgt bis ~0,7 s —
   TOL_S musste deshalb von 0,5 auf 1,5 s (BUG-19 / ADR-28).
3. **Fixed-Cadence** auch im Deep-Loop (Resync statt Aufstauen),
   Mute-Respekt während eigenem TX, Stille-Skip.
4. **Opt-in**: `"rx": {"deep_decode": true}` + Daemon-Neustart;
   Toggle im Web-UI (Audio & PTT) mit Neustart-Hinweis.

### Ergebnis (Stresstest via VAC, Juni 2026)

| Konfiguration | Dekodierrate |
|---|---|
| Short-Decoder allein | ~54–57 % |
| Short + Deep (eigener Executor) | **86–90 %** ✅ PASS (≥ 80 %) |
| Short + Deep (geteilter Executor, BUG-18) | 44 Frames — schlechter als ohne |
| Batch-Decoder (Referenz, statisches WAV) | 88 % |

Der Deep-Decoder liefert verpasste Frames mit ~15 s Verzögerung
nach — für Telemetrie/Notfunk unkritisch, für die Live-Anzeige
durch das 🔍-Badge transparent.

### Merksatz

> **Wenn zwei Tasks denselben ThreadPool teilen, verhungert der
> latenzkritische.** Parallele Decoder-Pfade brauchen getrennte
> Executors — CPU-Konkurrenz zeigt sich nicht als Fehler, sondern
> als stille Raten-Regression.

---

## 24. SDR-Profil-System — Konfiguration, API, Laufzeit-Bindung (Juni 2026)

### Motivation

Die SDR-Konfiguration bestand aus zwei unverbundenen Einzelblöcken
(`sdr_tx` für SoapySDR-TX, `rtlsdr` für IQ-RX) — ein Gerät pro
Richtung, hart verdrahtet. Mit mehreren Geräten im Shack (HackRF,
SDRplay, RTL-SDR) braucht es dasselbe Muster wie bei den
TRX-Profilen: benannte Profile + ein Zeiger auf das aktive.

### Konfigurationsmodell (gateway.json)

```
"sdr_profiles": [
  { "name": "HackRF",  "type": "trx", "driver": "hackrf",
    "serial": "", "rx": {…}, "tx": {…} },
  { "name": "SDRplay", "type": "rx",  "driver": "sdrplay",
    "rx": {center_freq_hz, sample_rate, gain, ppm_correction} }
],
"active_sdr_rx_profile": null,    ← null = Audio/VAC-RX
"active_sdr_tx_profile": null     ← null = Audio-TX-Pfad
```

RX- und TX-Pfad sind unabhängig wählbar (z.B. SDRplay-RX +
Audio-TX über den TRX). `sdr_tx`/`rtlsdr` bleiben als
Lese-Fallback erhalten — keine Migration nötig.

### type-Ableitung aus der Hardware

`type` (rx|tx|trx) wird nicht manuell gepflegt:
`enumerate_all_devices()` (gust_soapy_tx.py) öffnet jedes Gerät
kurz und liest `getNumChannels(RX)` + `getNumChannels(TX)` —
RTL-SDR ⇒ rx, HackRF ⇒ trx. Im Web-UI ist das Typ-Feld readonly
und wird per „Geräte scannen"/„Übernehmen" befüllt.

### API (gust_web.py)

| Endpunkt | Zweck |
|---|---|
| GET /api/sdr/scan | enumerate + RX/TX-Caps + Profile + aktive |
| POST /api/sdr/profile/save | anlegen/ersetzen (per Name) |
| DELETE /api/sdr/profile?name= | löschen (Schutz: aktiv/letztes → 409) |
| POST /api/sdr/profile/activate/rx | active_sdr_rx_profile (null = aus) |
| POST /api/sdr/profile/activate/tx | active_sdr_tx_profile (null = aus) |

activate prüft die Richtungs-Fähigkeit (rx-Profil ist nicht als
TX aktivierbar → 409). Beide activate-Handler teilen sich eine
Implementierung (`_sdr_activate(request, direction)`).

### Laufzeit-Bindung (gust.py / gust_iq_rx.py)

TX: `_resolve_sdr_tx_cfg(cfg)` mit Priorität
`active_sdr_tx_profile` → Legacy `sdr_tx.enabled` → None
(Audio-Pfad). Baut aus dem Profil das Dict, das `_tx_via_sdr()`
erwartet (device_args aus driver+serial, freq, gain, …).

RX: `cmd_daemon()`/`cmd_rx()` starten den IQReceiver **parallel**
zum AudioRXLoop — beide publizieren in denselben EventBus.

`_run_soapy()` ist treiberneutral: Device-Args aus
`driver`+`serial`, CF32-Stream, blockierendes `readStream`
im Executor (Event-Loop bleibt reaktiv), PPM-Korrektur
via `_apply_ppm()` (komplexer Frequenz-Mischer).

`build_iq_receiver(cfg)` als Factory-Funktion:
active_sdr_rx_profile → Legacy-rtlsdr → None.

### Wichtige Einschränkung: Python-Version

SoapySDR-Bindings existieren derzeit nur in der Python-3.9-
Umgebung (PothosSDR auf Windows). Unter Python 3.14 (normaler
GUST-Daemon) greift bei `driver=rtlsdr` der pyrtlsdr-Fallback;
SDRplay und HackRF-RX loggen einen klaren Fehler. Für
produktiven SDR-RX-Betrieb mit SDRplay/HackRF muss entweder
der Daemon unter Python 3.9 laufen oder SoapySDR-Bindings
für 3.14 installiert werden.

---

## 25. Logging-Architektur — VITAL-Level + Quiet-Mode (Juni 2026)

VITAL ist ein eigener Level: `logging.addLevelName(35,
"VITAL")` + Monkey-Patch `logging.Logger.vital()`.
Alle Komponenten (gust_audio, gust_web, gust_rx) können
`log.vital()` aufrufen sobald gust.py geladen ist.
(Standalone-Fallback in gust_audio.py: definiert `vital`
selbst, falls gust.py nicht geladen ist — sonst würde
der PortAudio-Callback-Thread mit AttributeError abbrechen.)

### Was VITAL bekommt

- `[RX Audio] input overflow` — immer sichtbar weil
  Betriebsproblem (Deep-Decoder-Scan alle 15s)
- rigctld gestartet/gestoppt (via GUI + Daemon-Start)
- TRX-Profil aktiviert
- GUST Web-Server gestartet
- PTT EIN/AUS (HamlibPTT, GPIUPTT)
- TX-Pipeline Start/Ende

### _GustStreamHandler

`_classify(record)` erkennt RX/TX-Labels aus dem
Message-Inhalt (`"[RX]" in msg` → `"RX ◀"`). Dadurch
können bestehende `log.info()`-Aufrufe in `gust_rx.py`
nach der Migration weiterhin korrekt gefiltert werden.
ERROR-Records geben immer `""` zurück (nie als RX ◀
gerendert).

### print() → log.*() Migration

Alle `print()`-Aufrufe in `gust_rx.py` und
`gust_audio.py` auf Logger umgestellt:
- Fehler → `log.error()`
- Betriebsereignisse → `log.vital()`
- Statusmeldungen → `log.debug()`
- CLI-Funktionen (`list_audio_devices`, `ptt_test`,
  `_run_demo`) behalten `print()` — sie laufen im
  direkten User-Kontext, nicht im Daemon.

---

## 26. Deep-Decoder Thread-Priorität — Windows ctypes 64-bit Fix (Juni 2026)

### Problem

Input-Overflow-Meldungen häuften sich alle 15s exakt
im Takt des Deep-Decoder-Scan-Intervalls. Die 8
parallelen Deep-Threads verdrängten den PortAudio-
Callback-Thread (Audio-ISR-ähnlicher Kontext).

### Fix: BELOW_NORMAL via initializer

```python
def _set_low_priority() -> None:
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.GetCurrentThread()
        ctypes.windll.kernel32.SetThreadPriority(handle, -1)
    else:
        import os; os.nice(5)
```

Als `initializer` im `ThreadPoolExecutor` wird die
Funktion einmalig beim Thread-Start aufgerufen.

### Kritische ctypes-Details (64-bit Windows)

`GetCurrentThread()` gibt ein Pseudo-Handle zurück
(Wert: `-2` = `0xFFFFFFFFFFFFFFFE`). Ohne explizite
`restype`/`argtypes`:
- ctypes behandelt Rückgabe als 32-bit int
- `-2` wird zu `0xFFFFFFFE` (truncated)
- `SetThreadPriority(0xFFFFFFFE, -1)` → ungültiges Handle
- Kein Fehler, keine Wirkung — stille Fehlfunktion

**Fix:**
```python
k32 = ctypes.windll.kernel32
k32.GetCurrentThread.restype  = ctypes.c_void_p
k32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
handle = k32.GetCurrentThread()
k32.SetThreadPriority(handle, -1)
```

### Auswirkung auf Dekodierrate

Keine messbare Verschlechterung. BELOW_NORMAL auf einem
Mehrkern-PC bedeutet nur "weiche aus wenn CPU knapp" —
bei ausreichend Kernen läuft der Thread fast gleich
schnell wie mit NORMAL-Priorität.

---

## 27. LDPC Blocklängen-Evaluation und Hard-vs-Soft-Decision (Juni 2026)

### Ausgangslage

Nach Implementierung von `gust_ldpc.py` (Etappe 2, n=48, Rate 3/4)
wurde festgestellt dass n=48 zu kurz für messbaren LDPC-Coding-Gain ist.
Etappe 2b führte eine Monte-Carlo-Evaluation durch:
AWGN/BPSK, regulärer (3,12)-LDPC Rate 3/4, python-ldpc min-sum,
Blocklängen n ∈ {48, 128, 256, 512, 1024}.

Ergebnisdateien:
  `ldpc_planung/ldpc_blocklen_eval.py`         — reproduzierbarer Simulator
  `ldpc_planung/ldpc_blocklen_eval_ergebnis.md` — vollständiger Bericht
  `ldpc_planung/ldpc_blocklen_curves.csv`       — Rohdaten (Eb/N0 vs. FER)

### Messergebnisse (Eb/N0 für FER = 1e-2)

| Verfahren                      | Eb/N0 @ FER=1e-2 | Bewertung |
|---|---|---|
| LDPC n=1024 soft               | 3,35 dB | bester Wert |
| LDPC n=256 soft                | 3,96 dB | Sweetspot |
| LDPC n=48 soft                 | 5,40 dB | zu kurz |
| **RS(255,223) hard (produktiv)** | **5,91 dB** | **Referenz** |
| LDPC hard, alle Blocklängen    | > 7 dB  | schlechter als RS |

### Die drei entscheidenden Befunde

**1. n=48 ist zu kurz.**
Nur 12 Prüfgleichungen — Belief Propagation liefert keinen Coding-Gain.
Das implementierte `gust_ldpc.py` (n=48) ist korrekt, aber ohne SNR-Vorteil.

**2. Hard-Decision ist die eigentliche Bremse.**
Auf dem aktuellen GUST-Pfad (MFSK-Demod → Hard-Bytes) ist LDPC für
**jede Blockgröße schlechter als RS**. Ein Wechsel auf LDPC mit dem
heutigen Demodulator wäre eine FEC-Regression.

**3. LDPC gewinnt nur unter zwei Bedingungen gleichzeitig:**
- Soft-Output-Demodulator (Bin-Energien → bitweise LLR)
- n ≥ 256 (Sweetspot: n=256 soft → ~3,96 dB, ~2 dB besser als RS)

Bei n=256 passt der 24-Byte-Datenblock fast exakt auf einen WEATHER-Frame
(14 Byte Payload + 8 Byte Header = 22 Byte) — idealer Sweetspot.
Darüber (n=512, n=1024) sind die Blöcke größer als RS-Frames → keine
Sendedauer-Einsparung mehr.

### Entscheidung

**Etappe 3 (gust_frame.py Integration) zurückgestellt.**
Voraussetzungen für eine sinnvolle LDPC-Integration:

1. **Soft-Output-Demodulator:** `_fft_detect_symbol()` muss
   Log-Likelihood-Ratios (LLR) statt Hard-Decisions liefern.
   Konkret: Bin-Energie des stärksten Tons vs. Summe aller anderen
   Bins → bitweise LLR für den BP-Decoder.

2. **Blockgröße n=256:** `gust_ldpc.py` neu parametrisieren.

3. **Frame-Aggregation:** Bei n=256 und Rate 3/4 sind 192 Datenbits
   (24 Byte) pro Block verfügbar. Kurze Payloads (CQ: 5 Byte) benötigen
   mehrere Blöcke oder Zero-Padding-Strategie.

### Aktueller Status der LDPC-Dateien

| Datei | Status | Verwendung |
|---|---|---|
| `gust_fec.py` | ✅ produktiv | FEC-Interface, RS-Wrapper |
| `gust_ldpc.py` (n=48) | ✅ korrekt, experimentell | nicht v1.0-Kandidat |
| `cc_ldpc_etappe3_integration.md` | ⛔ gesperrt | erst nach Soft-Demod |

### Merksatz

> **LDPC braucht Soft-Decision-Input.** Ohne LLR vom Demodulator ist
> Belief Propagation blind — Hard-Bytes geben keine Zuverlässigkeits-
> information, BP degeneriert zu einem schlechten Bit-Flip-Decoder.
> RS(255,223) ist unter diesen Bedingungen überlegen.

---

## 28. AUTH-Frame Design-Entscheidungen (Juni 2026)

> **Status:** Vollstaendig implementiert (Juni 2026). P8-11 abgeschlossen.
> 0x50 AUTH (GUST-S): §3.4/§3.5 — Crypto-Kern, TX-Tooling, RX-Puffer,
> Web-UI Badge alle fertig. 0x85+0x86 AUTH_EX (GUST-X, ECDSA): §3.9,
> P8-12 — spezifiziert, Implementierung wartet auf GUST-X.

### Warum zwei verschiedene AUTH-Verfahren?

GUST verwendet je nach Anwendungsfall unterschiedliche Authentifizierung:

**0x50 AUTH (GUST-S) — HMAC-SHA256, bilateral:**
Für geschlossene Gruppen mit bestehendem Vertrauensverhältnis.
HMAC-SHA256 truncated auf 16 Byte passt in die 20-Byte-GUST-S-Payload.
Jede Station hat pro Gegenstelle einen eigenen Schlüssel — einfach,
wartungsarm, keine Infrastruktur erforderlich.

**0x85+0x86 AUTH_EX (GUST-X) — ECDSA P-256, öffentlich:**
Für Stationen die öffentlich verifizierbar sein wollen (Notfunk-Organisationen,
Expeditionen, bekannte Rufzeichen). Die vollständige 64-Byte-ECDSA-Signatur
(r=32B + s=32B) erfordert zwei GUST-X-Frames. Öffentlicher Schlüssel
auf QRZ.com — jeder Empfänger kann ohne Vorwissen verifizieren.

### Warum kein Key-Exchange über GUST?

Diffie-Hellman über HF wäre theoretisch möglich, führt aber zu
Verschlüsselung — lizenzrechtlich problematisch auf Amateurfunk (§16 AFG).
Der Out-of-Band-Austausch ist bewusste Designentscheidung: Stationen die
Authentifizierung wollen, haben ohnehin eine direkte Beziehung.

### Warum 60-Sekunden-Fenster?

Kompromiss zwischen:
- Zu kurz (< 30 s): AUTH-Frame könnte bei schlechter Ausbreitung verloren gehen
  bevor er gesendet oder empfangen wurde
- Zu lang (> 120 s): Replay-Fenster wird unkomfortabel groß

60 s entspricht 12 × dem TX-Zyklus bei 300-s-Intervall — genug Puffer.

### Warum Timestamp statt Sequenznummer?

Ein REF_SEQ-Feld würde eine Sequenznummer im Daten-Frame-Header
voraussetzen — die GUST-S v0.5 nicht hat (kein seq-Feld in
TYPE|CHANNEL|FROM|PAYLOAD|CRC). Eine Sequenznummer im CHANNEL-Byte
(Bits 4-6, Werteraum 0-7) wäre für Wetter und Position ausreichend,
aber für TEXT-QSO mit Fragmentierung problematisch: TEXT-Frames
haben bereits eine eigene Sequenznummer im Payload (für Reassembly).
Zwei verschiedene Sequenznummern würden verwirren.

Der TIMESTAMP löst das Problem universell:
- Kein neues Feld im Daten-Frame-Header nötig
- Funktioniert für alle Frame-Typen gleichermassen
- Dient gleichzeitig als Referenz UND Replay-Schutz
- Voraussetzung: GPS/NTP-Synchronisation (Toleranz ±30 s)

Puffer-Schlüssel: Rufzeichen + REF_TYPE (letzter Frame dieses Typs).

(Damit ist auch der frühere P8-11-RX-Blocker — fehlendes seq-Feld —
gegenstandslos; die RX-Integration braucht kein Frame-Sequencing mehr.)

### Ergänzung: Wann HMAC, wann ECDSA?

HMAC (0x50 AUTH, GUST-S) und ECDSA (0x85+0x86 AUTH_EX, GUST-X) lösen
verschiedene Probleme:

**HMAC:** "Ist das wirklich mein Freund OE3GAS?" — nur der
Schlüsselpartner kann prüfen. Richtig für bilaterale Beziehungen,
geschlossene Notfunk-Gruppen, Ortsverbände. Jeder hat seinen eigenen
Schlüssel pro Gegenstelle — Identitätsdiebstahl erfordert dass
OE1XTU seinen eigenen Schlüssel weitergibt (widersinnig).

**ECDSA:** "Ist das wirklich OE1XRK vom Roten Kreuz?" — jeder kann
prüfen ohne vorherigen Kontakt. Richtig wenn unbekannte Empfänger
die Herkunft verifizieren wollen: ein Gateway der noch nie mit OE1XRK
kommuniziert hat, kann einen Notruf trotzdem authentifizieren
(öffentlicher Schlüssel auf QRZ.com).

Der symmetrische Schlüssel-Einwand ("Wer verifizieren kann, kann
auch fälschen") gilt nur wenn der Schlüssel weitergegeben wird —
was dem Inhaber selbst schadet. Für geschlossene Gruppen ist HMAC
vollständig ausreichend und einfacher zu verwalten.
ECDSA wird relevant wenn Traffic zunimmt, interessante Stationen
(Expeditionen, Organisationen wie Rotes Kreuz) öffentlich
verifizierbar sein wollen.

### Implementierungsnotiz: _raw_frame_body und JSON-Serialisierung

Die RX-Verifikation braucht den rohen Frame-Body (exakt die gleichen
Bytes die auth_tag() beim Senden verarbeitet hat). gust_modulator.py
legt diesen als _raw_frame_body (bytes) im result-Dict ab.

Problem: bytes ist nicht JSON-serialisierbar. gust_eventbus.py entfernt
_raw_frame_body vor dem WebSocket-Broadcast, damit kein TypeError den
RX-Frame-Broadcast abbricht. Das Feld lebt nur im gust_rx.py-internen
Verarbeitungspfad.

Merksatz: Interne Diagnosefelder (Unterstrich-Prefix) nie ueber den
EventBus weitergeben wenn sie nicht JSON-serialisierbar sind.

---

## 29. GUST-X Design-Entscheidungen (Juni 2026)

> **Status:** Entwurf — GUST-X (P8-12) ist geplant, nicht implementiert.
> Spezifikation: gust_spec.md §3.9, ADR-37.

### Warum 7,5 Sekunden als Maximum?

FT8 hat ~15 s Frames, GUST-S ~5 s. Die Diskussion:
- 10 s: nähert sich FT8 an, schwächt das "GUST ist kurz"-Argument
- 7,5 s: 50 % länger als GUST-S, immer noch 2× kürzer als FT8
- Effizienz: +50 % Sendedauer liefert +120 % Payload — asymmetrisch vorteilhaft

7,5 s ist das Maximum. Kürzere GUST-X Frames (< 44 Byte Payload)
sind selbstverständlich möglich und üblich.

### Warum Timestamp als Pflichtfeld?

In einer Welt wo GUST-Frames über Relais, Gateways, MQTT-Broker
und Datenbanken wandern, ist die Frage "wann wurde dieser Wert
gemessen?" fundamental. Die Empfangszeit ist kein Ersatz:

```
Station sendet WEATHER @ 12:00:00 (Messung)
Frame läuft über 2 Relais: +45 s
Gateway empfängt @ 12:00:45
Datenbank speichert Empfangszeit → falscher Messzeitpunkt
```

4 Byte GPS/NTP-Timestamp in jedem GUST-X Frame löst dieses Problem
vollständig. Overhead: +0,3 s Sendedauer — vernachlässigbar.

### Warum 9-Symbol-SYNC statt Mode-Bit im Header?

Der FEC-Decoder muss wissen welches Verfahren (RS oder LDPC) und
welche Datenblock-Länge erwartet wird — bevor er dekodiert.
Ein Mode-Bit im Frame-Header liegt aber im FEC-geschützten Bereich
→ Zirkelschluss. Das 9. SYNC-Symbol steht vor der FEC und ist
ungeschützt dekodierbar. Einzelbit-Fehler im Variantensymbol werden
durch den CRC am Frame-Ende erkannt.

### Warum LDPC statt mehr RS-Parität?

RS(255,191) (+32 Byte Parität) würde +62–86 % Sendedauer kosten
(Shortened RS, siehe §22). LDPC n=256 bei Rate 3/4 gibt ~2 dB
SNR-Gewinn ohne Sendezeitverlängerung — und nutzt den gewonnenen
Platz (44 Byte statt 20 Byte) für echte Nutzlast.

### Warum ECDSA für AUTH_EX, nicht HMAC?

HMAC skaliert nicht auf öffentliche Verifikation — jeder Empfänger
bräuchte einen bilateralen Schlüssel. ECDSA erlaubt dass jede Station
weltweit einen Frame von OE1XRK verifizieren kann, ohne je Kontakt
gehabt zu haben. Das ist der qualitative Sprung den GUST-X gegenüber
GUST-S bietet: von Gruppen-Authentifizierung zu öffentlicher
Authentifizierung.

Eine vollständige ECDSA-P-256-Signatur (r + s = 64 Byte) passt nicht in
ein einzelnes 44-Byte-GUST-X-Payload, und eine gekürzte Signatur ist
nicht verifizierbar (Verifikation braucht r und s vollständig). Daher
wird die Signatur über **zwei Frames** übertragen: 0x85 AUTH_EX (r) und
0x86 AUTH_EX_B (s). Sicherheitsniveau ~128 Bit (P-256) — für Amateur-Funk
ausreichend, da kein finanzieller Anreiz für aufwändige Angriffe besteht.
Kosten: ein zusätzlicher Frame, akzeptabel weil Authentifizierung opt-in
für wichtige bzw. Notruf-Frames ist.

### Neue Frame-Typen 0x80–0xCF

Der 0x80-Bereich ist im bisherigen GUST-S Namespace leer.
Bit 7 gesetzt = GUST-X Frame — ein GUST-S Decoder der versehentlich
ein solches TYPE-Byte sieht (nach Fehl-SYNC) erkennt es als
unbekannten Typ und verwirft den Frame (CRC-Fehler tut den Rest).

---

## 30. MQTT als zentrale Drehscheibe (Architektur-Entscheidung, Juni 2026)

**Prinzip:** Alle externen Datenquellen (MeshCore, Wetterstation, APRS, Meshtastic,
künftige Quellen) kommunizieren ausschließlich über MQTT mit GUST. Kein Connector hat
direkten Zugriff auf Hardware — das ist Aufgabe der jeweiligen Bridge-Skripte.

**Warum nicht direkt Serial→GUST:** Eine direkte Serial-Anbindung wäre eine
Insellösung. MQTT als Zwischenschicht ermöglicht:
- Mehrere Quellen gleichzeitig ohne GUST-Codeänderung
- Einfaches Hinzufügen neuer Quellen (neues Bridge-Skript + neues Topic)
- Debugging via `mosquitto_sub -v -t '#'`
- Spätere Verteilung auf mehrere Hosts

**Komponenten-Hierarchie:**
```
Hardware/Quelle
    → Bridge-Skript (Serial/HTTP/UDP → MQTT publish)
    → mosquitto (localhost:1883)
    → GustConnector (MQTT subscribe → Event-Bus)
    → GUST Core → HF TX
```

**Rückrichtung (HF → extern):**
```
HF RX → RX_FRAME Event
    → GustConnector (Event-Bus → MQTT publish)
    → mosquitto
    → Bridge-Skript (MQTT subscribe → Serial/HTTP/UDP)
    → Hardware/Ziel
```

**Referenz:** P6-17, P6-18, P6-19, gust_connector_konzept.md §2

---

## 31. MeshCore-Anbindung via USB-Serial + MQTT-Bridge (Juni 2026)

**Hintergrund:** Für den Heltec WiFi LoRa 32 V4 existiert kein offizielles
WiFi/TCP-Companion-Binary im MeshCore-Flasher (Stand Juni 2026 — v1.16.0).
Nur `companion_radio_ble` und `companion_radio_usb` sind verfügbar.
Eine WiFi-direkte MQTT-Verbindung vom Node zum Broker ist daher nicht möglich.

**Gewählte Architektur:** USB-Serial + lokale Bridge

```
MeshCore-Node (Heltec V4, Firmware: companion_radio_usb)
    → USB-Kabel (/dev/ttyUSB0 oder COMx)
    → gust_meshcore_bridge.py
        liest: meshcore-Bibliothek (pypi: meshcore≥2.3.7)
        publiziert: meshcore/rx/text, meshcore/rx/position
        abonniert: meshcore/tx/text
    → mosquitto (localhost:1883)
    → MeshCoreConnector (gust_connector.py)
    → GUST Event-Bus → HF TX
```

**Getestetes Setup (Juni 2026):**
- Hardware: Heltec WiFi LoRa 32 V4, EU868, 869.618 MHz / BW 62.5 / SF8 / CR8
- Firmware: MeshCore v1.16.0-07a3ca9, Rolle: companion_radio_usb
- Konfiguration: meshcore-cli v1.5.7 via `meshcore-cli -s COMx`
- Kanäle: Public, #at-hl, #hollabrunn, #noe, #test, #vienna
- Node-Name: AT-HL-OE3GAS-🦚

**meshcore-cli Kurzreferenz (Windows):**
```powershell
# Installation
pip install meshcore-cli

# Node-Info
meshcore-cli -s COM18 infos

# Kanäle anzeigen
meshcore-cli -s COM18 get_channels

# Kanal setzen
meshcore-cli -s COM18 set_channel 0 "Public" "KEY"

# Parameter setzen
meshcore-cli -s COM18 set name "AT-HL-OE3GAS"
meshcore-cli -s COM18 set radio "869.618,62.5,8,8"
meshcore-cli -s COM18 set tx 20

# Hilfe
meshcore-cli -s COM18 set help
```

**Bekannte Einschränkung:** `config.meshcore.io` (Web-UI) funktioniert nur
mit Repeater/Room-Server-Firmware, nicht mit Companion-Firmware —
Command-timeout-Fehler ist erwartetes Verhalten.

**Referenz:** P6-17, P6-18, P6-19, gust_knowledge.md §30

**Verifizierte meshcore-Python-Library API (v≥2.3, Juni 2026):**

| Was | Wie | Anmerkung |
|---|---|---|
| Node-Name | `mc.self_info['name']` | Wird ~2s nach Verbindung befüllt |
| Public Key | `mc.self_info['public_key']` | 64-Zeichen Hex; `[:12]` = pubkey_prefix |
| Firmware | `send_device_query().payload['ver']` | Nicht in self_info! (BUG-MC-01) |
| Radio-Parameter | `mc.self_info['radio_freq/bw/sf/cr']` | Direkt lesbar |
| Kanal-Info | `mc.commands.get_channel(i)` | Einzelabfrage; leere Slots haben `channel_name==""` |
| `get_channels()` | **EXISTIERT NICHT** | Immer `get_channel(i)`-Loop verwenden |
| Message-Empfang | `mc.start_auto_message_fetching()` + `mc.subscribe(EventType.CHANNEL_MSG_RECV, cb)` | Background-Polling |
| `is_connected` | Property, nicht Methode | `if not mc.is_connected:` — kein `()` |
| Sender-Identität | **Kein pubkey_prefix im Wire-Format** | Nur über Kontaktliste auflösbar |

**Zwei fragment_text-Versionen — synchron halten:**
- `gust_frame.fragment_text(text, dest_call, seq_nr, chunk_size=14)` — produktiv
- `gust_meshcore_bridge.fragment_text(text, chunk_size=14)` — Bridge-Fallback, andere Signatur
- Beide wurden für BUG-MC-03 (UTF-8-Zeichengrenze) gefixt
- Bei Änderungen immer beide Versionen prüfen

**UTF-8 in GUST-Frames (BUG-MC-03, behoben Juni 2026):**
Frame 0x40 Payload-Limit: 14 Byte Text. chunk_size=14 bezieht sich auf **Bytes**,
nicht auf Zeichen. Multibyte-Zeichen (Emoji 4B, Kyrillisch 2B, Griechisch 2B) müssen
auf Zeichengrenzen geschnitten werden. Algorithmus: zurückgehen bis
`(encoded[end] & 0xC0) != 0x80` (kein Continuation-Byte).

**MeshCore-Kanal-Namenskonvention:**
Node-Namen folgen dem Muster `REGION-ORT-RUFZEICHEN-EMOJI`, z.B. `AT-HL-OE3GAS-🦚`.
Rufzeichen-Extraktion via Regex: `re.search(r'([A-Z]{1,3}[0-9][A-Z]{1,4})', name.upper())`.

**USB/BLE-Firmware-Einschränkung:**
`companion_radio_usb` und `companion_radio_ble` sind separate Builds.
Kein gleichzeitiger USB-Python-Zugriff + BLE-App-Verbindung möglich.
Für Tests: entweder USB-Bridge ODER App, nicht beides gleichzeitig.

**Repeater (zweiter Heltec V4, COM19):**
Repeater-Firmware ist nicht über meshcore-Python-Library ansprechbar.
Nur Text-CLI via `meshcore-cli -r -s COM19`. Konfiguration:
```powershell
meshcore-cli -r -s COM19    # Verbinden
# Dann interaktive Befehle: set radio, set name, etc.
```

---

## 32. P8-14 Ergebnis: LDPC Soft-Decision schlaegt RS bei niedrigem SNR (Juni 2026)

### Ausgangslage

Nach P8-14 (Soft-Decision in receive() verdrahtet) wurde der volle
SNR-Sweep RS vs. LDPC-Soft durchgefuehrt (seed 42, 60 s,
5 Frames/Kanal, ldpc_stress_gen.py + ldpc_stress_decode.py).

### Messergebnisse

| SNR | RS | LDPC-Soft | Bewertung |
|---|---|---|---|
| Baseline (kein Rauschen) | 90,0 % | 75,0 % | RS staerker |
| −15 dB | 82,5 % | 80,0 % | ~gleich |
| **−10 dB** | **32,5 %** | **50,0 %** | **LDPC +17,5 pp** |
| −6 dB | 0,0 % | 0,0 % | beide unter Schwelle |

### Kernergebnis

**LDPC-Soft schlaegt RS bei niedrigem SNR (−10 dB): +17,5 Prozentpunkte.**

Das bestaetigt den theoretischen ~2 dB FEC-Cliff-Verschiebung aus
der Blocklängen-Evaluation (§27) — nicht im Simulator, nicht im
isolierten Loopback, sondern im echten GUST-Frame-Pfad:
echtes MFSK-8 Signal, echter SYNC, echte Demodulation, echter CRC.

### Warum RS bei Baseline staerker ist

RS(255,223) korrigiert 16 Byte-Fehler pro Block. Bei wenig Rauschen
(seltene Demodulationsfehler) ist das dominanter als LDPC n=255.
LDPC-Vorteil liegt im SNR-Cliff-Bereich (hier: ~−10 dB), wo RS
bereits einbricht und LDPC noch stabil dekodiert.

### Technische Details der Implementierung (P8-14)

1. **Bit-Reihenfolge:** symbols_to_bit_llr_array liefert [bit0,bit1,bit2],
   aber symbols_to_bytes streamt MSB-first [bit2,bit1,bit0].
   gust_modulator.py dreht jedes Symbol-Triple um — ohne dieses
   Alignment wuerde Soft-Decision keine Verbesserung bringen.

2. **Direkter Backend-Aufruf:** rs_decode() leitet llr_blocks nicht durch
   (Dispatcher + ReedSolomonFEC kennen es nicht). Der Soft-Pfad ruft
   _get_fec().decode(raw, llr_blocks=) direkt — ohne gust_ldpc oder
   gust_frame zu aendern.

3. **Breitband-Pfad:** Multi-Hypothesen-Modus verwendet noch Hard-Decision
   (TODO-Kommentar in gust_modulator.py). Stresstest laeuft ueber
   channel=ch (Direktmodus) — ausreichend fuer die Verifikation.

### Offene Punkte (nicht geblockt, Verbesserungen)

- **Baseline-Rate:** Tail-Block-Padding in gust_ldpc.decode — letzter
  LLR-Block leicht unscharf weil Multi-Try die Codewortlaenge nicht
  kennt. Behebung koennte Baseline 75% → ~85% heben.
  Benoetigt Eingriff in gust_ldpc.decode (ausserhalb bisherigem Scope).

- **Breitband-Soft-Pfad:** Multi-Hypothesen-Modus auf Soft-Decision
  umstellen. Niedrigere Prioritaet, da Stresstest Direktmodus nutzt.

### Historische Einordnung

Dies ist der erste empirische Beweis dass LDPC in einem vollstaendigen
HF-Telemetrie-Frame-Pfad RS bei niedrigem SNR schlaegt. Die gesamte
Entwicklungskette:

  Blocklängen-Eval (§27)       → n=255 Sweetspot identifiziert
  Loopback Soft-Decision       → 2 dB Cliff-Verschiebung bestaetigt
  Etappe 4 Hard-Decision       → 56 % (LDPC < RS, erwarteter Befund)
  P8-14 Soft-Decision          → −10 dB: LDPC 50 % vs. RS 33 % ✅

---

*Dokument: gust_knowledge.md*
*Autor: OE3GAS*
*Stand: Juni 2026 — §25 Logging-Architektur (VITAL) · §26 Deep-Decoder Thread-Priorität (ctypes 64-bit) · §27 LDPC Blocklängen-Evaluation (Juni 2026) · §28 AUTH-Frame Design-Entscheidungen (Entwurf, Juni 2026) · §29 GUST-X Design-Entscheidungen (Entwurf, Juni 2026) · §30 MQTT als zentrale Drehscheibe (Juni 2026) · §31 MeshCore-Anbindung + API-Erkenntnisse + UTF-8-Fix (Juni 2026) · §32 P8-14 LDPC-Soft schlägt RS bei −10 dB (Juni 2026) · AUTH: 0x50 HMAC / 0x85+0x86 ECDSA-64 (2-Frame) · Phase 9: Costas-SYNC · 8-Kanal-Plan · IQ-Eingang · Docker-Deployment*