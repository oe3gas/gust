# Claude Code Prompt: LDPC Etappe 2b — ldpc_loopback_test.py

## Voraussetzung

Etappe 2 (gust_ldpc.py) abgeschlossen und Selbsttest grün.

## Ziel

Neues Standalone-Script `ldpc_loopback_test.py`.
Kein Daemon, kein Audio, kein Modulator — rein algebraischer Test:
encode → Fehler injizieren → decode → Ergebnis.

Zusätzlich: **CPU-Laufzeitmessung** auf dem Zielsystem (RPi 4 oder PC).

---

## Datei erstellen: `ldpc_loopback_test.py`

```python
#!/usr/bin/env python3
"""
GUST — LDPC Isolierter Loopback-Test                       Etappe 2b
═══════════════════════════════════════════════════════════════════════
Testet gust_ldpc.py ohne Daemon, Audio oder Modulator:
  encode → [Fehler injizieren] → decode → Vergleich

Testet außerdem:
  - Overhead aller GUST Frame-Typen (LDPC vs. RS)
  - CPU-Laufzeit auf dem aktuellen System
  - Fehlerkorrektur-Grenze: ab wievielen Fehlern schlägt LDPC fehl?

Verwendung:
  py ldpc_loopback_test.py
  py ldpc_loopback_test.py --bench        # nur CPU-Benchmark
  py ldpc_loopback_test.py --sweep        # Fehlerquoten-Sweep
  py ldpc_loopback_test.py --all          # alle Tests

Erfolg: Exit-Code 0 wenn alle Pflicht-Tests bestehen.
"""

import argparse
import sys
import time
import random
import platform
import numpy as np
from typing import List, Tuple

# ── LDPC Backend laden ─────────────────────────────────────────────────
try:
    import gust_ldpc
    from gust_ldpc import LDPCFecBackend, LDPCDecodeError, _LDPC_LIB
    from gust_fec import get_fec_backend
    fec = LDPCFecBackend()
except ImportError as e:
    print(f"FEHLER: {e}")
    print("Bitte im GUST-Projektverzeichnis ausführen (gust_fec.py + gust_ldpc.py nötig).")
    sys.exit(2)

# RS zum Vergleich
try:
    from gust_frame import RS_OVERHEAD
    RS_AVAIL = True
except ImportError:
    RS_OVERHEAD = 32
    RS_AVAIL = False

# ══════════════════════════════════════════════════════════════════════
# TESTDATEN: alle GUST Frame-Typen
# ══════════════════════════════════════════════════════════════════════

FRAME_PAYLOADS = [
    ("CQ",           bytes([0x41]) + b"OE3GAS" + bytes(5 - 1)),
    ("EMERG_RSRC",   bytes([0x21]) + b"OE3GAS" + bytes(8 - 1)),
    ("STATION_TLM",  bytes([0x03]) + b"OE3GAS" + bytes(10 - 1)),
    ("WEATHER",      bytes([0x01]) + b"OE3GAS" + bytes(14 - 1)),
    ("POSITION",     bytes([0x02]) + b"OE3GAS" + bytes(18 - 1)),
    ("EMERG_BEACON", bytes([0x20]) + b"OE3GAS" + bytes(20 - 1)),
    ("TEXT",         bytes([0x40]) + b"OE3GAS" + bytes(20 - 1)),
]

# ══════════════════════════════════════════════════════════════════════
# TEST 1: ROUNDTRIP + OVERHEAD
# ══════════════════════════════════════════════════════════════════════

def test_roundtrip() -> Tuple[bool, List[dict]]:
    """Roundtrip-Test und Overhead-Vergleich für alle Frame-Typen."""
    print("\n── Test 1: Roundtrip + Overhead ────────────────────────")
    print(f"  {'Frame-Typ':<14} {'Payload':>7} {'RS':>6} {'LDPC':>6} "
          f"{'Diff':>8} {'Kürzer?':>8} {'OK':>4}")
    print(f"  {'─'*14} {'─'*7} {'─'*6} {'─'*6} {'─'*8} {'─'*8} {'─'*4}")

    results = []
    all_ok  = True

    for name, payload in FRAME_PAYLOADS:
        try:
            enc   = fec.encode(payload)
            dec   = fec.decode(enc)
            ok    = (dec == payload)

            rs_sz   = len(payload) + RS_OVERHEAD
            ldpc_sz = len(enc)
            diff    = ldpc_sz - rs_sz
            shorter = "✓" if diff < 0 else ("=" if diff == 0 else "✗")
            status  = "✓" if ok else "✗"

            if not ok:
                all_ok = False

            print(f"  {name:<14} {len(payload):>6}B {rs_sz:>5}B "
                  f"{ldpc_sz:>5}B {diff:>+7}B {shorter:>8} {status:>4}")

            results.append({
                "name": name, "payload": len(payload),
                "rs_size": rs_sz, "ldpc_size": ldpc_sz,
                "diff": diff, "ok": ok,
            })
        except Exception as e:
            print(f"  {name:<14} FEHLER: {e}")
            all_ok = False
            results.append({"name": name, "ok": False, "error": str(e)})

    ldpc_shorter = sum(1 for r in results if r.get("diff", 0) < 0)
    print(f"\n  LDPC kürzer als RS: {ldpc_shorter}/{len(FRAME_PAYLOADS)} Frame-Typen")
    return all_ok, results


# ══════════════════════════════════════════════════════════════════════
# TEST 2: CPU-LAUFZEITMESSUNG
# ══════════════════════════════════════════════════════════════════════

def test_benchmark(n_runs: int = 100) -> Tuple[bool, dict]:
    """
    CPU-Laufzeitmessung: encode + decode pro Frame-Typ, N Wiederholungen.
    Ziel: ≤ 50 ms/Frame auf RPi 4.
    """
    print(f"\n── Test 2: CPU-Benchmark ({n_runs} Wiederholungen) ──────────")
    print(f"  System: {platform.node()} / {platform.machine()} / "
          f"Python {platform.python_version()}")
    print(f"  LDPC-Bibliothek: {_LDPC_LIB or 'numpy-Fallback'}")
    print()
    print(f"  {'Frame-Typ':<14} {'encode ms':>10} {'decode ms':>10} "
          f"{'total ms':>10} {'Ziel ≤50ms':>10}")
    print(f"  {'─'*14} {'─'*10} {'─'*10} {'─'*10} {'─'*10}")

    TARGET_MS = 50.0
    results   = {}
    all_ok    = True

    for name, payload in FRAME_PAYLOADS:
        enc_times = []
        dec_times = []

        try:
            # Warm-up
            enc = fec.encode(payload)
            fec.decode(enc)

            # Messung
            for _ in range(n_runs):
                t0  = time.perf_counter()
                enc = fec.encode(payload)
                t1  = time.perf_counter()
                fec.decode(enc)
                t2  = time.perf_counter()
                enc_times.append((t1 - t0) * 1000)
                dec_times.append((t2 - t1) * 1000)

            enc_ms   = np.median(enc_times)
            dec_ms   = np.median(dec_times)
            total_ms = enc_ms + dec_ms
            ok       = total_ms <= TARGET_MS
            marker   = "✓" if ok else "✗ ÜBERSCHREITUNG"

            if not ok:
                all_ok = False

            print(f"  {name:<14} {enc_ms:>9.1f} {dec_ms:>9.1f} "
                  f"{total_ms:>9.1f} {marker:>10}")

            results[name] = {
                "encode_ms": enc_ms, "decode_ms": dec_ms,
                "total_ms": total_ms, "ok": ok,
            }
        except Exception as e:
            print(f"  {name:<14} FEHLER: {e}")
            all_ok = False

    # Worst-Case für Live+Deep-Decoder-Abschätzung
    if results:
        max_decode = max(r["decode_ms"] for r in results.values())
        max_total  = max(r["total_ms"] for r in results.values())

        # Live-Decoder: 8 Kanäle × (FFT ~35ms + LDPC decode)
        fft_ms     = 35.0
        live_per_s = 8 / 2.0   # 8 Kanäle, Schritt 2s → 4 receive()/s
        live_cpu   = live_per_s * (fft_ms + max_decode) / 1000 * 100

        # Deep-Decoder: 8 Kanäle / 8s Schritt
        deep_per_s = 8 / 8.0
        deep_cpu   = deep_per_s * (fft_ms + max_decode) / 1000 * 100

        print(f"\n  CPU-Abschätzung (Live + Deep Decoder parallel):")
        print(f"    LDPC decode worst-case:   {max_decode:.1f} ms/Frame")
        print(f"    FFT-Demodulation:         {fft_ms:.0f} ms/Fenster (geschätzt)")
        print(f"    Live-Decoder:            ~{live_cpu:.0f} % CPU")
        print(f"    Deep-Decoder:            ~{deep_cpu:.0f} % CPU")
        print(f"    Gesamt:                  ~{live_cpu + deep_cpu:.0f} % CPU")

        if live_cpu + deep_cpu > 70:
            print(f"    ⚠  Gesamtlast > 70 % — Deep Decoder Scan-Intervall")
            print(f"       auf 12 s erhöhen wenn LDPC aktiv (ADR-24)")
        else:
            print(f"    ✓  Gesamtlast im grünen Bereich")

    return all_ok, results


# ══════════════════════════════════════════════════════════════════════
# TEST 3: FEHLERQUOTEN-SWEEP
# ══════════════════════════════════════════════════════════════════════

def test_error_sweep(n_trials: int = 50) -> Tuple[bool, dict]:
    """
    Misst die Dekodierrate bei steigender Bit-Fehlerquote (BER).
    Zeigt den FEC-Cliff — ab wann schlägt LDPC fehl.
    """
    print(f"\n── Test 3: Fehlerquoten-Sweep ({n_trials} Versuche/Stufe) ──")
    print(f"  Payload: WEATHER-Frame (14 Byte)")
    print()
    print(f"  {'BER':>8} {'Fehler/Frame':>14} {'Dekodiert':>10} "
          f"{'Rate':>8} {'Bewertung':>12}")
    print(f"  {'─'*8} {'─'*14} {'─'*10} {'─'*8} {'─'*12}")

    _, payload = next(p for p in FRAME_PAYLOADS if p[0] == "WEATHER")
    rng        = random.Random(42)
    results    = {}

    # BER-Stufen: 0 → 30 %
    ber_levels = [0.0, 0.01, 0.02, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30]

    for ber in ber_levels:
        decoded_ok = 0
        for _ in range(n_trials):
            enc       = fec.encode(payload)
            corrupted = bytearray(enc)
            # Bit-Fehler gemäß BER einbringen
            for byte_idx in range(len(corrupted)):
                for bit in range(8):
                    if rng.random() < ber:
                        corrupted[byte_idx] ^= (1 << bit)
            try:
                dec = fec.decode(bytes(corrupted))
                if dec == payload:
                    decoded_ok += 1
            except (LDPCDecodeError, Exception):
                pass

        rate   = decoded_ok / n_trials
        n_err  = int(ber * len(fec.encode(payload)) * 8)
        marker = "✓ gut" if rate >= 0.95 else \
                 ("⚠ Grenzbereich" if rate >= 0.50 else "✗ Cliff")

        print(f"  {ber:>7.1%} {n_err:>14} {decoded_ok:>9}/{n_trials} "
              f"{rate:>7.1%} {marker:>12}")

        results[ber] = {"decoded": decoded_ok, "total": n_trials, "rate": rate}

    # Cliff-Punkt finden
    cliff = next(
        (ber for ber, r in results.items() if r["rate"] < 0.80),
        None
    )
    if cliff:
        print(f"\n  FEC-Cliff bei BER ≈ {cliff:.1%}")
    else:
        print(f"\n  Kein Cliff bis BER {max(ber_levels):.0%} — robust")

    return True, results


# ══════════════════════════════════════════════════════════════════════
# ZUSAMMENFASSUNG
# ══════════════════════════════════════════════════════════════════════

def print_summary(results: dict):
    """Abschlussbewertung für Etappe-2b-Kriterien."""
    print("\n══════════════════════════════════════════════════════")
    print("  Etappe 2b — Bewertung")
    print("══════════════════════════════════════════════════════")

    criteria = [
        ("Overhead LDPC < RS (alle Typen)",
         results.get("roundtrip_all_shorter", False)),
        ("Roundtrip-Genauigkeit 100 %",
         results.get("roundtrip_ok", False)),
        ("Laufzeit ≤ 50 ms/Frame",
         results.get("bench_ok", True)),
        ("CPU Live+Deep ≤ 70 %",
         results.get("cpu_ok", True)),
    ]

    all_pass = True
    for label, ok in criteria:
        status = "✓" if ok else "✗"
        if not ok:
            all_pass = False
        print(f"  {status}  {label}")

    print(f"{'─' * 54}")
    if all_pass:
        print(f"  ✓  Alle Kriterien erfüllt → Etappe 4 (Stresstest) starten")
    else:
        print(f"  ✗  Kriterien nicht erfüllt → Etappe 3 NICHT starten")
        print(f"     LDPC-Parameter oder Bibliothek prüfen")
    print(f"{'═' * 54}")


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description="GUST LDPC Isolierter Loopback-Test (Etappe 2b)")
    ap.add_argument("--bench",  action="store_true",
                    help="Nur CPU-Benchmark ausführen")
    ap.add_argument("--sweep",  action="store_true",
                    help="Nur Fehlerquoten-Sweep ausführen")
    ap.add_argument("--all",    action="store_true",
                    help="Alle Tests ausführen (Standard)")
    ap.add_argument("--runs",   type=int, default=100,
                    help="Wiederholungen für Benchmark (Standard: 100)")
    ap.add_argument("--trials", type=int, default=50,
                    help="Versuche pro BER-Stufe im Sweep (Standard: 50)")
    args = ap.parse_args()

    run_all      = args.all or (not args.bench and not args.sweep)
    run_bench    = args.bench or run_all
    run_sweep    = args.sweep or run_all
    run_roundtrip = run_all

    print("══════════════════════════════════════════════════════")
    print("  GUST LDPC Loopback-Test  (Etappe 2b)  OE3GAS")
    print("══════════════════════════════════════════════════════")
    print(f"  Backend:  {fec}")
    print(f"  Lib:      {_LDPC_LIB or 'numpy-Fallback'}")

    summary   = {}
    exit_code = 0

    if run_roundtrip:
        ok, rt_results = test_roundtrip()
        summary["roundtrip_ok"]          = ok
        summary["roundtrip_all_shorter"] = all(
            r.get("diff", 1) < 0 for r in rt_results if "diff" in r
        )
        if not ok:
            exit_code = 1

    if run_bench:
        ok, bench_results = test_benchmark(n_runs=args.runs)
        summary["bench_ok"] = ok
        if bench_results:
            max_total = max(r["total_ms"] for r in bench_results.values())
            fft_ms    = 35.0
            live_cpu  = (8 / 2.0) * (fft_ms + max_total) / 1000 * 100
            deep_cpu  = (8 / 8.0) * (fft_ms + max_total) / 1000 * 100
            summary["cpu_ok"] = (live_cpu + deep_cpu) <= 70.0
        if not ok:
            exit_code = 1

    if run_sweep:
        ok, _ = test_error_sweep(n_trials=args.trials)

    if run_all:
        print_summary(summary)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
```

---

## Verifikation

```powershell
# Alle Tests:
py ldpc_loopback_test.py --all

# Nur Benchmark (schnell, für RPi-Messung):
py ldpc_loopback_test.py --bench --runs 200

# Nur Fehlerquoten-Sweep:
py ldpc_loopback_test.py --sweep --trials 100
```

## Erfolgskriterien (müssen alle ✓ sein vor Etappe 4)

1. **Overhead LDPC < RS** für alle 7 Frame-Typen
2. **Roundtrip 100 %** — kein einziger Decode-Fehler ohne injizierten Fehler
3. **Laufzeit ≤ 50 ms/Frame** auf Zielsystem
4. **CPU Live+Deep ≤ 70 %** (geschätzt)

## Nur neue Datei erstellen — kein bestehender Code ändern.
