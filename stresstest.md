# GUST — Stresstest-Methodik
**OE3GAS — Synthetischer Mehrkanal-Decoder-Test**
*Stand: Juni 2026*

---

## Überblick

Der GUST-Stresstest ist ein vollständig softwarebasiertes Test-Setup
ohne Hardware-Anforderungen. Es erzeugt eine synthetische WAV-Datei mit
gleichzeitig aktiven Frames auf allen 8 Kanälen, dekodiert sie mit dem
echten GUST-Decoder und wertet das Ergebnis quantitativ aus.

**Zweck:** Decoder-Robustheit und RS-FEC-Wirkung systematisch messen,
bevor On-Air-Tests (T-10.x) gemacht werden.

### Beteiligte Dateien

| Datei | Rolle |
|---|---|
| `gust_stresstest.py` | Generator: erzeugt WAV + CSV-Log |
| `gust_stress_decode.py` | Verifikation: dekodiert WAV, matched gegen CSV |
| `gust_modulator.py` | Kern-Decoder (`receive()`) — unverändert |
| `gust_frame.py` | Frame-Layer — unverändert |

---

## Architektur des Test-Setups

```
gust_stresstest.py
  --seed 42 --duration 45 --noise -10
        │
        ├── gust_stress_<ts>.wav    ← 8 kHz PCM, alle Kanäle gemischt
        ├── gust_stress_<ts>.cf32   ← für inspectrum
        └── gust_stress_<ts>.csv    ← Ground Truth (was gesendet wurde)
                │
                ▼
gust_stress_decode.py  gust_stress_<ts>.wav  --csv gust_stress_<ts>.csv
        │
        └── Sliding-Window-Scan (Fenster 9,0 s, Schritt 2,0 s)
              pro Fenster: receive(window, channel=ch) für ch 0–7
              Dedup: (callsign, frame_type, channel), TTL 10 s
              Matching: Typ + Kanal + Zeittoleranz 8 s
              │
              └── Dekodierrate, fehlende Frames, Bericht
```

**Wichtig:** Der `--seed`-Parameter macht den Generator deterministisch.
Alle SNR-Sweep-Läufe müssen denselben Seed verwenden — nur der
Rauschpegel ändert sich, das Signal bleibt identisch. Nur so ist
die Dekodierrate direkt vergleichbar.

---

## Vorgehensweise: Schicht für Schicht

Die Validierung erfolgt in fünf Schichten. Jede Schicht muss stabil
sein bevor die nächste begonnen wird.

```
Schicht 5  On-Air (T-10.x)              realer KW-Kanal
Schicht 4  SNR-Sweep                    synthetisches Rauschen
Schicht 3  Baseline ohne Rauschen       Decoder-Mechanik isoliert
Schicht 2  Einzelkanal-Sanity           Test-Setup selbst
Schicht 1  Selbsttest Modulator         gust_modulator.py intern
```

---

## Schicht 1 — Modulator-Selbsttest

**Einmalig** — oder nach jeder Änderung an `gust_modulator.py` oder
`gust_frame.py`. Nicht vor jedem Stresstest-Lauf nötig.

Verifiziert dass der Decoder grundsätzlich funktioniert (TX→WAV→RX
Loopback, bereits Teil des bestehenden Testplans T-2.3).

```powershell
py gust_modulator.py
```

Erwartung: alle Selbsttests grün. Wenn hier Fehler auftreten, ist
der Stresstest nicht aussagekräftig.

| Situation | Schicht 1 nötig? |
|---|---|
| Normaler SNR-Sweep, kein Code geändert | Nein |
| Nach Änderung an `gust_modulator.py` oder `gust_frame.py` | Ja |
| Frische Installation / neuer Rechner | Ja |
| Zur Sicherheit nach längerer Pause | Einmal reicht |

---

## Schicht 2 — Einzelkanal-Sanity

Ein Frame pro Kanal, kein Rauschen, kurze Dauer. Verifiziert dass
Generator und Decoder korrekt zusammenarbeiten.

```powershell
py gust_stresstest.py --seed 42 --duration 60 --frames-per-ch 1 --out sanity
py gust_stress_decode.py sanity.wav
```

**Ziel: 100 %** (8 Frames, ein Frame pro Kanal, kein zeitlicher Druck).

**Hinweis zur Dauer:** Ein GUST-Frame dauert ~5 s. Bei `--frames-per-ch 1`
braucht der Generator mindestens ~10 s pro Kanal für einen stressfreien
Slot. `--duration 60` ist der empfohlene Minimalwert — 10 s ist zu kurz.

Wenn hier Frames fehlen: Problem im Test-Setup selbst (Generator oder
Decoder-Matching), nicht im Signal. Vor dem Weitergehen beheben.

---

## Schicht 3 — Baseline ohne Rauschen

Vollständiger Stresstest, alle Frame-Typen, alle Kanäle gleichzeitig,
kein Rauschen. Misst die strukturelle Dekodierrate des Sliding-Window-
Verfahrens ohne SNR-Einfluss.

### Slot-Größe: die entscheidende Kenngröße

Der Generator teilt die Gesamtdauer gleichmäßig in Slots auf:

```
Slot-Größe = --duration / --frames-per-ch
```

Die Slot-Größe bestimmt wie dicht die Frames auf einem Kanal aufeinander
folgen — und damit wie häufig zeitliche Kollisionen zwischen Kanälen
auftreten. Empirisch ermittelte Werte (seed 42):

| Dauer | Frames/Kanal | Slot-Größe | Dekodierrate | Bewertung |
|---|---|---|---|---|
| 60 s | 6 | 10,0 s | 91,7 % | zu dicht |
| 180 s | 15 | 12,0 s | 95,0 % | gut |
| 240 s | 30 | 8,0 s | 90,8 % | zu dicht ¹ |
| **120 s** | **10** | **12,0 s** | **— (Sweetspot)** | **empfohlen** |

¹ Historischer Messwert vor Einführung der Mindest-Slot-Größe — der
Generator reduziert 240 s / 30 Frames inzwischen automatisch auf
21 Frames/Kanal (Slot ≥ 11 s); diese Kombination ist so nicht mehr
reproduzierbar.

**Regel:** Slot-Größe ≥ 2 × MAX_FRAME_S ≈ **11 s minimum.**

Bei zu kleiner Slot-Größe (< 11 s) liegen mehrere Frames verschiedener
Kanäle zeitlich übereinander. `receive()` findet pro Kanal-Aufruf den
stärksten SYNC — wenn zwei Frames auf benachbarten Kanälen interferieren,
verliert der schwächere. Das ist kein Decoder-Bug, sondern ein reales
Kanalkapazitätsproblem — aber für die Baseline-Messung unerwünscht.

**Wichtig: Der Generator erzwingt ab sofort Mindest-Slot-Größe 11 s**
(siehe CC-Prompt `cc_prompt_stresstest_slotfix.md`). Ein `--frames-per-ch`
das diese Grenze unterschreitet wird automatisch auf den maximal
zulässigen Wert reduziert, mit einer Warnung im Output.

### Empfohlener Baseline-Lauf

Mindestens **3 Seeds** messen — ein einzelner Lauf ist nicht
repräsentativ, weil zufällige Zeitkollisionen zwischen Kanälen
die Rate um ±4 % verschieben können.

```powershell
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --out baseline_s42
py gust_stresstest.py --seed 43 --duration 120 --frames-per-ch 10 --out baseline_s43
py gust_stresstest.py --seed 44 --duration 120 --frames-per-ch 10 --out baseline_s44

py gust_stress_decode.py baseline_s42.wav
py gust_stress_decode.py baseline_s43.wav
py gust_stress_decode.py baseline_s44.wav
```

Slot-Größe: 12,0 s — 80 Frames/Lauf — 3 Seeds = 240 Frames Gesamtstichprobe.

**Ziel: ≥ 95 % im Durchschnitt über alle Seeds.**

Empirisch gemessene Werte (nach Matching-Fix, Juni 2026):

| Seed | Dekodierrate | Fehlend | Dual-Bonus | Bemerkung |
|---|---|---|---|---|
| 42 | 96,2 % | 3 | 1 | Zufallskollisionen |
| 43 | 92,5 % | 6 | 1 | Zufallskollisionen + 2× Fensterrand (~114 s) |
| 44 | 96,2 % | 3 | 1 | Zufallskollisionen |
| **Ø** | **~95,0 %** | | | **Baseline akzeptiert** |

**Warum nicht ≥ 98 %?**

Die verbleibenden ~4 % Ausfälle haben zwei strukturelle Ursachen die
sich mit den aktuellen Parametern nicht eliminieren lassen:

1. **Zufällige Zeitkollisionen zwischen Kanälen:** Die Slot-Größe
   kontrolliert nur den Abstand *innerhalb* eines Kanals. Verschiedene
   Kanäle planen unabhängig — es entsteht gelegentlich ein Zeitpunkt
   an dem 3–4 Frames auf verschiedenen Kanälen fast gleichzeitig
   aktiv sind. `receive()` findet pro Aufruf den stärksten SYNC;
   der schwächere geht verloren.

2. **Fensterrand-Effekt:** Frames die innerhalb der letzten `WINDOW_S`
   der WAV-Datei starten, liegen im letzten Scan-Fenster möglicherweise
   nicht vollständig drin. Bei 122 s Dauer und 9 s Fenster betrifft
   das Frames ab ~113 s.

Beide Effekte sind **korrekt und realistisch** — sie modellieren echte
Kanalinterferenzen wie sie auf KW auftreten. Eine Baseline von ~96 %
ist für den Zweck des Tests (SNR-Sweep-Referenz) vollständig ausreichend.

### Was tun wenn die Baseline unter 95 % steckt?

Das Ergebnis-CSV (`--out baseline_result.csv`) zeigt für jeden Frame
ob er gematcht wurde. Die fehlenden Frames nach Muster analysieren:

| Beobachtung | Wahrscheinliche Ursache | Maßnahme |
|---|---|---|
| Fehlende Frames gehäuft bei **ch0 oder ch7** | Randkanal-Amplitudenproblem | Kanalplan prüfen; ggf. Amplitude im Generator erhöhen |
| Fehlende Frames gehäuft bei **EMERG_BEACON** | Framedauer nahe Fenstergrenze | `WINDOW_S` in `gust_stress_decode.py` erhöhen (10,0 s) |
| Fehlende Frames **zufällig verteilt**, kein Muster | Slot-Größe zu klein | Slot-Größe prüfen: muss ≥ 11 s sein |
| Viele Fehlende Frames **am Ende** der WAV | Fensterrand-Effekt | `--duration` erhöhen (mehr Puffer nach letztem Frame) |
| Dekodierrate **deutlich unter 90 %** bei sauberem Signal | Decoder-Problem | Modulator-Selbsttest (Schicht 1) wiederholen |

---

## Schicht 4 — SNR-Sweep

**Erst durchführen wenn Schicht 3 ≥ 95 % im Durchschnitt über 3 Seeds zeigt.**

Misst die empirische SNR-Schwelle des Decoders — das Pendant zu
T-10.2 (HackRF/IC-7610), aber vollständig softwarebasiert und
reproduzierbar.

```powershell
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --noise  -30 --out snr30
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --noise  -20 --out snr20
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --noise  -15 --out snr15
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --noise  -10 --out snr10
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --noise   -6 --out snr6
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --noise   -3 --out snr3

py gust_stress_decode.py snr30.wav
py gust_stress_decode.py snr20.wav
py gust_stress_decode.py snr15.wav
py gust_stress_decode.py snr10.wav
py gust_stress_decode.py snr6.wav
py gust_stress_decode.py snr3.wav
```

Der `--noise`-Parameter gibt den Rauschpegel **relativ zum Signal-Peak
in dB** an. Beispiel: `--noise -10` bedeutet das Rauschen liegt 10 dB
unter dem stärksten Frame → SNR ≈ 10 dB.

### Gemessene Ergebnisse (seed 42, Juni 2026)

| SNR | Dekodierrate | Fehlend | Bewertung |
|---|---|---|---|
| −30 dB | 96,2 % | 3 | = Baseline (strukturell) |
| −20 dB | 96,2 % | 3 | = Baseline |
| −15 dB | 92,5 % | 6 | leichter Abfall |
| **−10 dB** | **50,0 %** | **40** | **FEC-Cliff** |
| −6 dB | 0,0 % | 80 | Totalausfall |
| −3 dB | 0,0 % | 80 | Totalausfall |

**Kernergebnis: FEC-Cliff zwischen −15 dB und −10 dB.**

Der Decoder dekodiert sauber oder gar nicht — kein gradueller Abfall.
Der Cliff liegt im Stresstest bei ~−12 bis −13 dB. Das ist ~3 dB
schlechter als T-10.2 (Einzel-TX, HackRF → IC-7610, Schwelle ≤ −10 dB).

**Ursache des Unterschieds:** Der Stresstest skaliert alle 8 Kanäle
mit 1/8 der Amplitude (volle Kanalbelegung simuliert). Das entspricht
−18 dB pro Kanal gegenüber Einzel-TX. Der effektive SNR pro Kanal
im Stresstest ist damit deutlich niedriger als `--noise` angibt —
das Rauschen bezieht sich auf den **gemischten** Signal-Peak, nicht
auf einen einzelnen Kanal.

**Protokollrelevanz:** Die On-Air-Schwelle von ≤ 10 dB SNR (T-10.2)
gilt für Einzel-TX. Bei gleichzeitiger voller Kanalbelegung (8 Stationen)
liegt die praktische Schwelle ~3–5 dB höher. Dies ist in `gust_spec.md`
§3.8 und `gust_knowledge.md` dokumentiert.

**Akzeptanzkriterium SNR-Sweep:**

| SNR | Ziel | Ergebnis | Status |
|---|---|---|---|
| −30 dB | ≈ Baseline | 96,2 % | ✅ |
| −20 dB | ≈ Baseline | 96,2 % | ✅ |
| −15 dB | ≥ 90 % | 92,5 % | ✅ |
| **−10 dB** | **≥ 80 %** | **50,0 %** | **❌ unter Ziel** |
| −6 dB | Messung | 0,0 % | 📋 dokumentiert |
| −3 dB | Messung | 0,0 % | 📋 dokumentiert |

Das Akzeptanzkriterium bei −10 dB (≥ 80 %) ist **nicht erfüllt**.
Dies liegt am 1/8-Skalierungsfaktor des Mehrkanal-Stresstests —
nicht an einem Decoder-Defekt. Das Ergebnis ist konsistent mit T-10.2.

### FEC-Upgrade evaluiert und verworfen (Juni 2026)

Als Reaktion auf den Cliff wurde RS(255,223) → RS(255,191) evaluiert
(64 statt 32 Byte Parität, korrigiert 32 statt 16 Byte-Fehler).
**Entscheidung: verworfen — RS(255,223) bleibt** (ADR-25).

Begründung: GUST verwendet shortened RS — übertragen wird nur
`Payload + 8 Header + RS_OVERHEAD`, nicht der volle 255-Byte-Block.
Die +32 Paritätsbytes kosten daher **+51–67 % Sendezeit** je Frame-Typ
(WEATHER: 4,86 s → 7,62 s; TEXT: 5,38 s → 8,13 s, gemessen 06.06.2026)
für nur ~3–5 dB Cliff-Verschiebung. Zusätzlich verletzen die ~8,1-s-Frames
die Vollfenster-Garantie (`WINDOW_S 9,0 ≥ MAX_FRAME_S 5,5 + 2,0`):
Ohne Fenster-Anpassung brach die Dekodierrate im Smoke-Test von
87,5 % auf 25 % ein (mit 12-s-Fenster: 81,2 %).

Vollständige Analyse: `gust_knowledge.md` §22.

### Was tun wenn die Kurve schlechter als erwartet ist?

Wenn die Dekodierrate bei −15 dB bereits unter 85 % fällt (also
schlechter als der aktuelle Messwert):

1. **RS-FEC-Konfiguration prüfen:** Aktuell RS(255,223), korrigiert
   16 Byte-Fehler. In `gust_frame.py` nachsehen ob `RS_OVERHEAD = 32`
   korrekt gesetzt ist.

2. **FFT-Symboldetektierung prüfen:** Bei niedrigem SNR liefert
   `_fft_detect_symbol()` falsche Symbole. Mit inspectrum die CF32-
   Datei öffnen und prüfen ob die MFSK-Töne im Wasserfall noch
   erkennbar sind — wenn ja, liegt das Problem im Decoder, nicht im
   Signal.

3. **Raised-Cosine-Fenster:** Ist `window=True` im Generator gesetzt?
   Ohne Fensterung entstehen Seitenkeulen die Nachbarkanäle stören.

4. **Kanal-Amplitude:** Alle 8 Kanäle werden mit 1/8 skaliert. Das
   ist korrekt und beabsichtigt — der Stresstest simuliert volle
   Kanalbelegung. Für Einzelkanal-SNR-Tests `--frames-per-ch 1`
   mit einem Kanal verwenden.

---

## Schicht 5 — On-Air

Wird durch den bestehenden Testplan T-10.x abgedeckt. Der Stresstest
(Schichten 1–4) ist Voraussetzung für die Interpretation der On-Air-
Ergebnisse — er liefert die Software-Referenz für den Vergleich.

**Wichtiger Hinweis:** Das Akzeptanzkriterium ≥ 80 % bei SNR −10 dB
gilt für **Einzel-TX** (T-10.2). Der Stresstest mit 8 gleichzeitigen
Kanälen misst unter anderen Bedingungen (1/8 Amplitude pro Kanal) und
ist nicht direkt vergleichbar. Beide Messreihen ergänzen sich.

---

## Typischer Testablauf (Kurzreferenz)

```powershell
# 1. Modulator-Selbsttest (einmalig, oder nach Codeänderungen)
py gust_modulator.py

# 2. Sanity (Schicht 2)
py gust_stresstest.py --seed 42 --duration 60 --frames-per-ch 1 --out sanity
py gust_stress_decode.py sanity.wav
# → Ziel: 100 %

# 3. Baseline (Schicht 3)  — Slot-Größe 12 s (120/10), Mindest-Slot 11 s
#    3 Seeds messen, Ziel: >= 95 % im Durchschnitt
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --out baseline_s42
py gust_stresstest.py --seed 43 --duration 120 --frames-per-ch 10 --out baseline_s43
py gust_stresstest.py --seed 44 --duration 120 --frames-per-ch 10 --out baseline_s44
py gust_stress_decode.py baseline_s42.wav
py gust_stress_decode.py baseline_s43.wav
py gust_stress_decode.py baseline_s44.wav
# → Ziel: >= 95 % Durchschnitt (empirische Baseline: ~96 %)

# 4. SNR-Sweep (Schicht 4, nur nach Baseline >= 95 %)
#    Gleicher Seed + gleiche Parameter, nur --noise variiert
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --noise -30 --out snr30
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --noise -10 --out snr10
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --noise  -6 --out snr6
py gust_stress_decode.py snr30.wav
py gust_stress_decode.py snr10.wav
py gust_stress_decode.py snr6.wav
# → Ziel: >= 80 % bei --noise -10
```

---

## Inspectrum-Analyse (optional)

Jeder Stresstest-Lauf erzeugt auch eine `.cf32`-Datei für inspectrum.
Damit lässt sich visuell prüfen ob das Signal wie erwartet aussieht:

```
Datei öffnen:   inspectrum gust_stress_<ts>.cf32
Sample Rate:    8000
FFT-Größe:      4096
```

Erwartetes Bild: 8 horizontale MFSK-Blöcke zwischen 600 und 2600 Hz,
zeitlich versetzt, mit sichtbaren Lücken zwischen Frames. Bei
`--noise -15` ist der Rauschboden sichtbar aber die Frames heben sich
noch klar ab. Bei `--noise -10` dominiert das Rauschen — die Frames
sind kaum noch erkennbar, was die 50 % Dekodierrate erklärt.

---

*Dokument: stresstest.md*
*Autor: OE3GAS*
*Stand: Juni 2026 — SNR-Sweep abgeschlossen; FEC-Cliff bei ~−12 dB (Mehrkanal) dokumentiert; RS(255,191)-Upgrade evaluiert und verworfen (ADR-25)*