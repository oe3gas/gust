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

*Dokument: gust_knowledge.md*
*Autor: OE3GAS*
*Stand: Mai 2026 — Phase 9: Costas-SYNC · 8-Kanal-Plan · IQ-Eingang · Connector-Konzept · Microham-Konfiguration*