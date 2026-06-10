# Claude Code Prompt: LDPC Etappe 4 — Stresstest-Vergleich RS vs. LDPC

## Voraussetzung

Etappen 1–3 abgeschlossen:
- `gust_fec.py` ✓
- `gust_ldpc.py` ✓
- Integration in gust_frame.py / gust.py ✓
- Verifikation Etappe 3 vollständig grün ✓

## Ziel dieser Etappe

`gust_stresstest.py` und `gust_stress_decode.py` um einen
`--fec`-Parameter erweitern damit RS und LDPC unter identischen
Bedingungen verglichen werden können.

---

## Änderung 1: gust_stresstest.py — --fec Parameter

### Argparse ergänzen

```python
# In main(), im argparse-Block:
ap.add_argument("--fec", type=str, default="rs",
                choices=["rs", "ldpc"],
                help="FEC-Backend: 'rs' (Standard) oder 'ldpc' (experimentell)")
```

### FEC-Backend vor der Generierung setzen

In `main()`, nach dem Seed-Setzen und vor dem Kanalschleife:

```python
fec_name = args.fec.lower()
if fec_name != "rs":
    try:
        import gust_ldpc   # registriert sich
        from gust_frame import set_fec_backend
        set_fec_backend(fec_name)
        print(f"  FEC-Backend:    {fec_name.upper()}")
    except Exception as e:
        print(f"  FEHLER FEC-Backend '{fec_name}': {e}")
        sys.exit(1)
else:
    print(f"  FEC-Backend:    RS (Standard)")
```

### Im Banner ausgeben

```
  FEC-Backend:    RS (Standard)   ← oder LDPC
```

### Ausgabe-Dateiname FEC-Backend einbeziehen

Wenn `--out` nicht angegeben, Dateiname um FEC-Suffix erweitern:

```python
# ALT:
base = args.out or f"gust_stress_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

# NEU:
if args.out:
    base = args.out
else:
    fec_suffix = "" if fec_name == "rs" else f"_{fec_name}"
    base = f"gust_stress{fec_suffix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
```

---

## Änderung 2: gust_stress_decode.py — --fec Parameter

### Argparse ergänzen

```python
ap.add_argument("--fec", type=str, default="rs",
                choices=["rs", "ldpc"],
                help="FEC-Backend das beim Dekodieren verwendet wurde: "
                     "'rs' (Standard) oder 'ldpc'")
```

### FEC-Backend vor dem Scan setzen

In `main()`, vor dem Sliding-Window-Scan:

```python
fec_name = args.fec.lower()
if fec_name != "rs":
    try:
        import gust_ldpc
        from gust_frame import set_fec_backend
        set_fec_backend(fec_name)
    except Exception as e:
        print(f"FEHLER FEC-Backend '{fec_name}': {e}")
        sys.exit(1)
```

### Im Scan-Banner ausgeben

```
  FEC-Backend:  rs    ← oder ldpc
```

---

## Vergleichs-Workflow

Nach Abschluss dieser Etappe läuft der vollständige Vergleich so:

```powershell
# ── Baseline ohne Rauschen ────────────────────────────────────────────
# RS (Standard):
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --fec rs --out cmp_rs_base
py gust_stress_decode.py cmp_rs_base.wav --fec rs

# LDPC:
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --fec ldpc --out cmp_ldpc_base
py gust_stress_decode.py cmp_ldpc_base.wav --fec ldpc

# ── SNR-Sweep ─────────────────────────────────────────────────────────
# RS:
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --fec rs --noise -15 --out cmp_rs_snr15
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --fec rs --noise -10 --out cmp_rs_snr10
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --fec rs --noise  -6 --out cmp_rs_snr6

# LDPC:
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --fec ldpc --noise -15 --out cmp_ldpc_snr15
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --fec ldpc --noise -10 --out cmp_ldpc_snr10
py gust_stresstest.py --seed 42 --duration 120 --frames-per-ch 10 --fec ldpc --noise  -6 --out cmp_ldpc_snr6

py gust_stress_decode.py cmp_rs_snr15.wav   --fec rs
py gust_stress_decode.py cmp_rs_snr10.wav   --fec rs
py gust_stress_decode.py cmp_rs_snr6.wav    --fec rs
py gust_stress_decode.py cmp_ldpc_snr15.wav --fec ldpc
py gust_stress_decode.py cmp_ldpc_snr10.wav --fec ldpc
py gust_stress_decode.py cmp_ldpc_snr6.wav  --fec ldpc
```

Erwartete Ergebnistabelle (zum Ausfüllen nach dem Test):

| SNR   | RS Dekodierrate | LDPC Dekodierrate | LDPC Gewinn |
|-------|-----------------|-------------------|-------------|
| kein  | ~96 %           |                   |             |
| −15 dB| ~92 %           |                   |             |
| −10 dB| ~50 %           |                   |             |
| −6 dB | ~0 %            |                   |             |

---

## Verifikation

```powershell
# Syntax-Check
python -m py_compile gust_stresstest.py
python -m py_compile gust_stress_decode.py

# Smoke-Test RS (kein LDPC nötig):
py gust_stresstest.py --seed 42 --duration 30 --frames-per-ch 3 --fec rs --out smoke_rs
py gust_stress_decode.py smoke_rs.wav --fec rs
# Erwartung: ~95 % Dekodierrate

# Smoke-Test LDPC:
py gust_stresstest.py --seed 42 --duration 30 --frames-per-ch 3 --fec ldpc --out smoke_ldpc
py gust_stress_decode.py smoke_ldpc.wav --fec ldpc
# Erwartung: ~95 % Dekodierrate (kein Rauschen → gleich wie RS)
```

---

## Wichtig

- Nur `gust_stresstest.py` und `gust_stress_decode.py` ändern
- `gust_frame.py`, `gust_modulator.py`, `gust_fec.py`, `gust_ldpc.py`
  **nicht** anfassen — die FEC-Umschaltung passiert über `set_fec_backend()`
- RS bleibt der Default wenn `--fec` nicht angegeben wird
- WAV-Dateien von RS und LDPC sind **nicht kreuzkompatibel** —
  immer mit demselben `--fec` kodieren und dekodieren
