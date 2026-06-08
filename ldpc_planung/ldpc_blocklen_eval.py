#!/usr/bin/env python3
"""
GUST — LDPC Blocklängen-Evaluation                         Etappe 2b
═══════════════════════════════════════════════════════════════════════
Beantwortet die in Etappe 2 aufgeworfene Designfrage:

   Ist die GUST-Blocklänge n=48 (Rate 3/4) zu kurz, und bringt eine
   größere Blocklänge genug Coding-Gain, um LDPC gegenüber RS(255,223)
   zu rechtfertigen?

Methodik (Monte-Carlo, reproduzierbar):
  - Kanal:        AWGN, BPSK (Bit 0 → +1), Soft-LLR.
  - Code:         regulärer (3, 12)-LDPC, Rate 3/4, exaktes Spaltengewicht 3,
                  balancierte Zeilen, keine Doppelkanten (eigener Konstruktor).
  - Decoder:      python-ldpc BpDecoder (min-sum, 50 Iter.).
  - Vergleich:    SOFT-Decision (volle LLR) vs. HARD-Decision (BSC aus
                  AWGN-Hard-Bits) — Letzteres entspricht dem heutigen
                  GUST-Pfad (Demodulator liefert Hard-Bytes, kein Soft-Output).
  - All-Zero-Codewort-Trick: für lineare Codes auf symmetrischen Kanälen
                  ist FER unabhängig vom gesendeten Codewort → kein Encoder
                  nötig, das Null-Wort ist immer gültig.
  - FER:          gemessen bis target_errs Frame-Fehler oder max_frames.

Wichtige Einordnung:
  AWGN/BPSK ist NICHT der reale GUST-Kanal (MFSK-8, nicht-kohärent). Für einen
  *relativen* Blocklängen-Vergleich ist AWGN aber der Standard und völlig
  ausreichend: er zeigt, ob und ab welcher Blocklänge BP überhaupt Gewinn
  liefert. Absolute dB-Werte sind nicht 1:1 auf GUST übertragbar.

Verwendung:
  py ldpc_blocklen_eval.py                # voller Lauf (soft+hard, Report)
  py ldpc_blocklen_eval.py --quick        # schnell (weniger Frames)
  py ldpc_blocklen_eval.py --csv out.csv  # Kurven zusätzlich als CSV
  py ldpc_blocklen_eval.py --blocklens 48,256,1024

Referenz: ADR-24, gust_knowledge.md, [[ldpc-n48-block-too-short]]
"""

import argparse
import sys
from math import erfc, sqrt, exp, comb

import numpy as np

try:
    import scipy.sparse as sp
    from ldpc import BpDecoder
except ImportError as e:
    print(f"FEHLER: benötigt scipy + python-ldpc ({e}). pip install ldpc scipy")
    sys.exit(2)


RATE = 0.75          # GUST-LDPC Code-Rate
DV   = 3             # Variable-Node-Grad (Spaltengewicht)


# ══════════════════════════════════════════════════════════════════════
# CODE-KONSTRUKTION  —  regulärer (dv, dc)-LDPC, Rate = 1 - dv/dc
# ══════════════════════════════════════════════════════════════════════

def make_regular_ldpc(n: int, rate: float = RATE, dv: int = DV,
                      seed: int = 7) -> np.ndarray:
    """
    Regulärer LDPC: exaktes Spaltengewicht dv, Zeilen so gleichmäßig wie
    möglich belegt (least-loaded zuerst), keine Doppelkanten.
    m = round(n*(1-rate)) Check-Nodes.
    """
    m   = int(round(n * (1 - rate)))
    rng = np.random.default_rng(seed)
    H   = np.zeros((m, n), dtype=np.uint8)
    row_deg = np.zeros(m, dtype=int)
    for j in range(n):
        # dv am wenigsten belegte Zeilen wählen, Gleichstände zufällig brechen
        order  = np.lexsort((rng.random(m), row_deg))
        chosen = order[:dv]
        H[chosen, j] = 1
        row_deg[chosen] += 1
    return H


# ══════════════════════════════════════════════════════════════════════
# KANAL + SIMULATION
# ══════════════════════════════════════════════════════════════════════

def _q(x: float) -> float:
    """Gaußsches Q(x) = 0.5*erfc(x/√2)."""
    return 0.5 * erfc(x / sqrt(2.0))


def simulate_point(H, rate, ebn0_db, mode,
                   target_errs=100, max_frames=20000, seed=1):
    """
    FER an einem Eb/N0-Punkt. mode: 'soft' (volle LLR) oder 'hard' (BSC).
    Gibt (fer, frames, ber) zurück.
    """
    m, n  = H.shape
    Hs    = sp.csr_matrix(H)
    rng   = np.random.default_rng(seed + int(round(ebn0_db * 13)))
    sigma = sqrt(1.0 / (2.0 * rate * 10 ** (ebn0_db / 10.0)))
    p_bsc = max(_q(1.0 / sigma), 1e-4)     # BSC-Übergangswahrscheinlichkeit

    # Decoder einmal bauen, Kanal je Frame aktualisieren (schnell)
    if mode == "hard":
        bpd = BpDecoder(Hs, max_iter=50, bp_method="minimum_sum",
                        input_vector_type="syndrome", error_rate=p_bsc)
    else:
        bpd = BpDecoder(Hs, max_iter=50, bp_method="minimum_sum",
                        input_vector_type="syndrome", error_rate=0.1)

    ferr = bit_err = frames = 0
    while frames < max_frames and ferr < target_errs:
        frames += 1
        y    = 1.0 + sigma * rng.standard_normal(n)   # All-Zero → +1
        hard = (y < 0).astype(np.uint8)
        if mode == "soft":
            llr = 2.0 * y / sigma ** 2
            probs = 1.0 / (1.0 + np.exp(np.abs(llr)))  # Bit-Fehlerwahrsch. aus |LLR|
            bpd.update_channel_probs(probs.tolist())
        syn = (H @ hard) % 2
        err = np.asarray(bpd.decode(syn.astype(np.uint8)), dtype=np.uint8)
        dec = (hard ^ err) % 2
        nb  = int(dec.sum())                  # All-Zero erwartet → Gewicht = Bitfehler
        if nb:
            ferr += 1
            bit_err += nb
    return ferr / frames, frames, bit_err / (frames * n)


def waterfall(H, rate, ebn0_list, mode, **kw):
    return [simulate_point(H, rate, e, mode, **kw) for e in ebn0_list]


def rs_fer_awgn(ebn0_db, N=255, K=223, m=8):
    """
    Analytische FER von RS(N,K) (t=(N-K)/2 Symbolfehler korrigierbar) auf
    hard-decision AWGN/BPSK. Eb/N0 auf Informationsbit normiert (Rate K/N).
    GUST nutzt diesen Code (shortened) als heutiges FEC — Referenzlinie.
    """
    R  = K / N
    pb = _q(sqrt(2 * R * 10 ** (ebn0_db / 10.0)))   # codierter Bitfehler (hard)
    ps = 1 - (1 - pb) ** m                          # Symbol-(Byte-)Fehler
    t  = (N - K) // 2                               # = 16
    cdf = sum(comb(N, i) * ps ** i * (1 - ps) ** (N - i) for i in range(t + 1))
    return 1 - cdf


def ebn0_at_fer(ebn0_list, fers, target=1e-2):
    """Lineare Interpolation in (Eb/N0, log10 FER) → Eb/N0 bei target-FER."""
    pts = [(e, f) for e, f in zip(ebn0_list, fers) if f > 0]
    logt = np.log10(target)
    for (e1, f1), (e2, f2) in zip(pts, pts[1:]):
        l1, l2 = np.log10(f1), np.log10(f2)
        if (l1 - logt) * (l2 - logt) <= 0 and l1 != l2:
            return e1 + (logt - l1) * (e2 - e1) / (l2 - l1)
    return None


# ══════════════════════════════════════════════════════════════════════
# GUST-PASSUNG: Blocklänge vs. reale Payload-Größen
# ══════════════════════════════════════════════════════════════════════

GUST_PAYLOADS = {       # Frame-Typ → Nutzdaten in Byte (inkl. TYPE/FROM)
    "CQ": 7, "EMERG_RSRC": 11, "STATION_TLM": 16,
    "WEATHER": 21, "POSITION": 25, "EMERG_BEACON": 27, "TEXT": 27,
}


def gust_fit_table(blocklens, rate=RATE):
    """Wieviel Nutzdaten fasst ein Codewort, und wie gut füllen GUST-Frames es?"""
    print("\n── GUST-Passung: Codewort-Kapazität vs. Frame-Größen ──────")
    print(f"  Rate {rate}: ein n-Bit-Codewort trägt k = {rate:.2f}·n Bit Nutzdaten\n")
    print(f"  {'n (Bit)':>8} {'k Daten':>9} {'= Byte':>8} "
          f"{'WEATHER-Füllung':>16} {'Frames/Codewort':>16}")
    print(f"  {'─'*8} {'─'*9} {'─'*8} {'─'*16} {'─'*16}")
    wx = GUST_PAYLOADS["WEATHER"]
    for n in blocklens:
        kbits  = int(rate * n)
        kbytes = kbits // 8
        fill   = wx / kbytes if kbytes else 0
        nframe = kbytes / wx if wx else 0
        note   = (f"{fill*100:4.0f} %" + (" (Padding!)" if fill < 0.5 else "")) \
                 if kbytes >= wx else f"{fill*100:4.0f} % (>1 Block)"
        print(f"  {n:>8} {kbits:>9} {kbytes:>7}B {note:>16} {nframe:>15.1f}")
    print("\n  → Große Blöcke fassen viele Bytes; GUST-Frames (7–27 B) füllen sie")
    print("    nur teilweise → Padding (Airtime-Verlust) ODER Frame-Aggregation")
    print("    (mehrere Frames je Codewort → höhere Latenz, Kopplung der Frames).")


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="GUST LDPC Blocklängen-Evaluation (2b)")
    ap.add_argument("--blocklens", default="48,128,256,512,1024",
                    help="Komma-Liste der Blocklängen n (Vielfache von 4)")
    ap.add_argument("--ebn0", default="1,2,3,4,5,6",
                    help="Komma-Liste Eb/N0 in dB")
    ap.add_argument("--target-errs", type=int, default=100,
                    help="Frame-Fehler je Punkt (Monte-Carlo-Genauigkeit)")
    ap.add_argument("--max-frames", type=int, default=20000)
    ap.add_argument("--quick", action="store_true",
                    help="Schnelllauf: weniger Frames (grobe Kurven)")
    ap.add_argument("--hard", action="store_true",
                    help="Auch HARD-Decision messen (heutiger GUST-Pfad)")
    ap.add_argument("--csv", default=None, help="Kurven zusätzlich als CSV speichern")
    args = ap.parse_args()

    blocklens = [int(x) for x in args.blocklens.split(",")]
    ebn0_list = [float(x) for x in args.ebn0.split(",")]
    te = 30 if args.quick else args.target_errs
    mf = 3000 if args.quick else args.max_frames
    do_hard = args.hard or not args.quick   # voller Lauf misst beides

    print("══════════════════════════════════════════════════════════════")
    print("  GUST — LDPC Blocklängen-Evaluation (Etappe 2b)        OE3GAS")
    print("══════════════════════════════════════════════════════════════")
    print(f"  Code:    regulärer (3,12)-LDPC, Rate {RATE}")
    print(f"  Kanal:   AWGN / BPSK, Decoder: python-ldpc min-sum, 50 Iter.")
    print(f"  MC:      bis {te} Frame-Fehler bzw. {mf} Frames je Punkt")
    print(f"  Hinweis: AWGN ≠ realer GUST-Kanal — relativer Vergleich, nicht absolut")

    csv_rows = [("n", "mode", "ebn0_db", "fer", "ber", "frames")]
    gain = {}   # (mode) -> list of (n, ebn0@1e-2)

    for mode in (["soft", "hard"] if do_hard else ["soft"]):
        title = ("SOFT-Decision (volle LLR — erfordert Soft-Output-Demodulator)"
                 if mode == "soft" else
                 "HARD-Decision (BSC aus Hard-Bits — heutiger GUST-Pfad)")
        print(f"\n── FER-Wasserfall, {title} ──")
        hdr = f"  {'n (Bit)':>8} " + " ".join(f"{e:>6.1f}dB" for e in ebn0_list) \
              + f" {'Eb/N0@1e-2':>11}"
        print(hdr)
        print("  " + "─" * (len(hdr) - 2))
        gain[mode] = []
        for n in blocklens:
            H    = make_regular_ldpc(n, RATE, DV, seed=7)
            pts  = waterfall(H, RATE, ebn0_list, mode,
                             target_errs=te, max_frames=mf)
            fers = [p[0] for p in pts]
            for e, (fer, frames, ber) in zip(ebn0_list, pts):
                csv_rows.append((n, mode, e, f"{fer:.5f}", f"{ber:.6f}", frames))
            thr = ebn0_at_fer(ebn0_list, fers, 1e-2)
            gain[mode].append((n, thr))
            cells = " ".join((f"{f:7.3f}" if f > 0 else "  <e-2 ") for f in fers)
            thr_s = f"{thr:6.2f} dB" if thr is not None else "   n/a "
            print(f"  {n:>8} {cells} {thr_s:>11}")

    # ── RS-Referenz (heutiges GUST-FEC, analytisch, hard-decision) ────
    rs_fers = [rs_fer_awgn(e) for e in ebn0_list]
    rs_thr  = ebn0_at_fer(ebn0_list, rs_fers, 1e-2)
    print("\n── Referenz: RS(255,223) hard-decision (heutiges GUST-FEC, analytisch) ──")
    print(f"  {'':>8} " + " ".join(f"{e:>6.1f}dB" for e in ebn0_list)
          + f" {'Eb/N0@1e-2':>11}")
    cells = " ".join((f"{f:7.3f}" if f >= 1e-3 else "  <e-3 ") for f in rs_fers)
    print(f"  {'RS':>8} {cells} {(f'{rs_thr:6.2f} dB' if rs_thr else 'n/a'):>11}")

    # ── Coding-Gain-Zusammenfassung ───────────────────────────────────
    print("\n── Coding-Gain vs. Blocklänge (Eb/N0 nötig für FER = 1e-2) ──")
    print("  Niedriger = besser. Δ = Gewinn gegenüber n=48.")
    if rs_thr:
        print(f"  Referenz RS(255,223) hard: {rs_thr:.2f} dB (Rate 0.875)")
    for mode in gain:
        print(f"\n  {mode.upper()}-Decision:")
        base = next((t for n0, t in gain[mode] if n0 == blocklens[0] and t), None)
        for n, thr in gain[mode]:
            if thr is None:
                print(f"    n={n:>5}:   FER 1e-2 nicht erreicht im Sweep-Bereich")
            else:
                d = f"{base - thr:+.2f} dB" if base else "—"
                print(f"    n={n:>5}:   {thr:5.2f} dB   (Δ vs n={blocklens[0]}: {d})")

    # ── GUST-Passung ─────────────────────────────────────────────────
    gust_fit_table(blocklens)

    # ── Fazit (datengetrieben formuliert, Interpretation im .md-Report) ─
    print("\n══════════════════════════════════════════════════════════════")
    print("  Kernaussagen")
    print("══════════════════════════════════════════════════════════════")
    print("  1. SOFT-Decision: Coding-Gain wächst mit der Blocklänge (steilerer")
    print("     Wasserfall). n=48 ist deutlich schwächer als n≥256.")
    print("  2. HARD-Decision (heutiger GUST-Pfad): bei Rate 3/4 für ALLE")
    print("     Blocklängen schwach — der Gewinn ist ohne Soft-Output nicht")
    print("     abrufbar. Das ist die eigentliche Einschränkung, nicht n=48 allein.")
    print("  3. Große Blöcke brauchen große Payloads; GUST-Frames (7–27 B) füllen")
    print("     sie nicht → Padding-Verlust oder Aggregations-Latenz.")
    print("  → Ausführliche Interpretation & Empfehlung: ldpc_blocklen_eval_ergebnis.md")

    if args.csv:
        import csv
        with open(args.csv, "w", newline="") as f:
            csv.writer(f).writerows(csv_rows)
        print(f"\n  CSV gespeichert: {args.csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
