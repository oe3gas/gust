# Claude Code Prompt: LDPC Etappe 4 — Stresstest RS vs. LDPC (isoliert, ohne Etappe 3)

## Voraussetzung

- Etappe 2b (ldpc_loopback_test.py) vollständig grün
- Alle Erfolgskriterien erfüllt

## Ziel

Zwei neue Standalone-Scripts:

1. `ldpc_stress_gen.py`   — erzeugt LDPC-kodierte Stresstest-WAV
2. `ldpc_stress_decode.py` — dekodiert die WAV mit LDPC, vergleicht mit CSV

**Kein bestehender Code wird geändert.**
`gust_stresstest.py` und `gust_stress_decode.py` bleiben unverändert
(RS-Betrieb). Die neuen Scripts sind eigene Dateien die gust_ldpc.py
direkt einbinden.

Der vollständige Vergleich am Ende:

```
RS-Stresstest:    py gust_stresstest.py    → wav/csv
                  py gust_stress_decode.py → Dekodierrate

LDPC-Stresstest:  py ldpc_stress_gen.py    → wav/csv
                  py ldpc_stress_decode.py → Dekodierrate

Vergleich: gleicher Seed, gleiche Frames, nur FEC unterschiedlich
```

---

## Script 1: `ldpc_stress_gen.py`

**Kopie von `gust_stresstest.py`** mit folgenden Änderungen:

### Änderungen gegenüber gust_stresstest.py

1. **LDPC-Backend setzen** — am Anfang von `main()`, vor der Frame-Erzeugung:

```python
# LDPC-Backend aktivieren
import gust_ldpc   # registriert sich
from gust_frame import set_fec_backend
set_fec_backend("ldpc")
print("  FEC-Backend:    LDPC (Rate 3/4, experimentell)")
```

2. **Banner-Titel** ändern:
```
GUST LDPC-Stresstest-Generator  v0.5  OE3GAS
```

3. **Default-Ausgabedateiname** mit `ldpc_`-Präfix:
```python
base = args.out or f"ldpc_stress_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
```

4. **Hinweis in Ausgabe** ergänzen:
```
  ⚠  LDPC-kodiert — nur mit ldpc_stress_decode.py dekodierbar
```

Alles andere (Frame-Generierung, Kanal-Mixing, Noise, CF32-Ausgabe)
bleibt **identisch** zu `gust_stresstest.py` — damit sind die
Bedingungen für RS und LDPC exakt vergleichbar.

---

## Script 2: `ldpc_stress_decode.py`

**Kopie von `gust_stress_decode.py`** mit folgenden Änderungen:

1. **LDPC-Backend setzen** — am Anfang von `main()`, vor dem Scan:

```python
# LDPC-Backend aktivieren
import gust_ldpc
from gust_frame import set_fec_backend
set_fec_backend("ldpc")
```

2. **Banner-Titel** ändern:
```
GUST LDPC Stresstest-Auswertung
```

3. **Fehlermeldung** wenn RS-WAV als Eingabe:
```python
# Warnung ausgeben wenn Dateiname kein "ldpc_" enthält
if "ldpc_" not in Path(args.wav).name:
    print("  ⚠  WARNUNG: Dateiname enthält kein 'ldpc_' — falsche WAV?")
    print("     RS-WAVs mit gust_stress_decode.py auswerten.")
```

Alles andere (Sliding Window, Matching, CSV-Ausgabe)
bleibt **identisch** zu `gust_stress_decode.py`.

---

## Vergleichs-Workflow

```powershell
# ── Baseline ohne Rauschen ────────────────────────────────────────────
# RS (bestehend):
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --out cmp_rs_base
py gust_stress_decode.py cmp_rs_base.wav

# LDPC (neu):
py ldpc_stress_gen.py --seed 42 --duration 120 --frames-per-ch 10 --out cmp_ldpc_base
py ldpc_stress_decode.py cmp_ldpc_base.wav

# ── SNR-Sweep ─────────────────────────────────────────────────────────
# RS:
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --noise -15 --out cmp_rs_snr15
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --noise -10 --out cmp_rs_snr10
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --noise  -6 --out cmp_rs_snr6

py gust_stress_decode.py cmp_rs_snr15.wav
py gust_stress_decode.py cmp_rs_snr10.wav
py gust_stress_decode.py cmp_rs_snr6.wav

# LDPC:
py ldpc_stress_gen.py --seed 42 --duration 120 --frames-per-ch 10 --noise -15 --out cmp_ldpc_snr15
py ldpc_stress_gen.py --seed 42 --duration 120 --frames-per-ch 10 --noise -10 --out cmp_ldpc_snr10
py ldpc_stress_gen.py --seed 42 --duration 120 --frames-per-ch 10 --noise  -6 --out cmp_ldpc_snr6

py ldpc_stress_decode.py cmp_ldpc_snr15.wav
py ldpc_stress_decode.py cmp_ldpc_snr10.wav
py ldpc_stress_decode.py cmp_ldpc_snr6.wav
```

## Ergebnistabelle (zum Ausfüllen nach dem Test)

| SNR | RS | LDPC | Gewinn | Bewertung |
|---|---|---|---|---|
| kein | ~96 % | | | Baseline |
| −15 dB | ~92 % | | | |
| −10 dB | ~50 % | | | **Ziel: ≥ 80 %** |
| −6 dB | ~0 % | | | |

**Erfolgskriterium für LDPC-Integration (Etappe 3):**
- Baseline ≥ 96 % (= RS, keine Regression)
- −10 dB ≥ 80 % (RS: 50 % → klarer Gewinn)
- −6 dB > 0 % (RS: 0 % → LDPC dekodiert noch)

Wenn diese drei Kriterien erfüllt sind → Etappe 3 (Integration in
gust_frame.py) freigegeben.

---

## Verifikation

```powershell
# Syntax-Check
python -m py_compile ldpc_stress_gen.py
python -m py_compile ldpc_stress_decode.py

# Smoke-Test (kurz, kein Rauschen):
py ldpc_stress_gen.py --seed 42 --duration 30 --frames-per-ch 3 --out ldpc_smoke
py ldpc_stress_decode.py ldpc_smoke.wav
# Erwartung: ≥ 95 % Dekodierrate (= RS-Baseline, kein Rauschen)

# Kreuztest (muss WARNUNG ausgeben):
py ldpc_stress_decode.py cmp_rs_base.wav
# Erwartung: ⚠ WARNUNG + 0 % Dekodierrate (RS≠LDPC)
```

## Wichtig

- Beide Scripts sind **Kopien mit minimalen Änderungen** —
  kein neuer Algorithmus, keine neue Logik
- Kein bestehender Code wird geändert
- RS-WAVs und LDPC-WAVs sind **nicht kreuzkompatibel**
- Etappe 3 (gust_frame.py Integration) erst nach
  bestandenem Stresstest-Vergleich starten
