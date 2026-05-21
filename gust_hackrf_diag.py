#!/usr/bin/env python3
"""
GUST — HackRF Dual-Kanal Diagnose                         Phase 7
═══════════════════════════════════════════════════════════════════════
Findet heraus WELCHER SoapySDR-Aufruf beim Dual-Kanal-TX hängt.

Jeder SoapySDR-Aufruf wird einzeln mit Timing protokolliert.
Ein Watchdog-Thread meldet wenn ein Aufruf länger als 10s dauert.

Testreihe:
  1. Einzel-Ton IQ (einfachstes Signal) → transmit_iq
  2. Dual-Kanal IQ (echtes GUST)     → transmit_iq

So sehen wir ob transmit_iq() generell hängt oder nur beim Dual-IQ.

Verwendung:
  python hackrf_diag.py
  python hackrf_diag.py --gain 30 --freq 14110000
"""

import argparse
import sys
import threading
import time

import numpy as np

try:
    import SoapySDR
    from SoapySDR import SOAPY_SDR_TX, SOAPY_SDR_CF32
except ImportError as e:
    print(f"SoapySDR nicht verfügbar: {e}")
    sys.exit(1)

HACKRF_SAMPLE_RATE = 2_000_000


def ts():
    return time.strftime("%H:%M:%S") + f".{int((time.time()%1)*1000):03d}"


class Watchdog:
    """Meldet wenn eine Operation zu lange dauert."""
    def __init__(self, name, timeout=10.0):
        self.name = name
        self.timeout = timeout
        self._timer = None

    def __enter__(self):
        def warn():
            print(f"\n{ts()}  ⚠⚠⚠ WATCHDOG: '{self.name}' hängt seit "
                  f"{self.timeout}s — DIES IST DIE HÄNGENDE OPERATION ⚠⚠⚠\n",
                  flush=True)
        self._timer = threading.Timer(self.timeout, warn)
        self._timer.daemon = True
        self._timer.start()
        self._t0 = time.time()
        print(f"{ts()}  → {self.name} ...", flush=True)
        return self

    def __exit__(self, *a):
        self._timer.cancel()
        dt = (time.time() - self._t0) * 1000
        print(f"{ts()}  ✓ {self.name}  ({dt:.0f} ms)", flush=True)


def make_single_tone_iq(freq_hz=900.0, duration_s=3.0):
    """Einfachstes Test-IQ: ein einzelner Ton im Basisband."""
    n = int(HACKRF_SAMPLE_RATE * duration_s)
    t = np.arange(n) / HACKRF_SAMPLE_RATE
    iq = 0.7 * np.exp(2j * np.pi * freq_hz * t)
    return iq.astype(np.complex64)


def make_dual_iq():
    """Echtes Dual-Kanal GUST-IQ (Kanal 2 + Kanal 7)."""
    from gust_modulator import transmit
    from gust_hackrf import nf_to_iq_usb
    from gust_frame import (
        FrameType, encode_emergency_beacon, PRIO_URGENT, INJURY_MINOR,
    )
    emg = encode_emergency_beacon(
        lat_deg=48.2, lon_deg=16.3, persons=2, injury_code=INJURY_MINOR,
        resource_flags=0, priority=PRIO_URGENT, text_snippet="HELP",
    )
    audio_a, _, _ = transmit(FrameType.EMERG_BEACON, "OE3GAS", emg,
                             channel=2, use_fec=True, window=True, add_silence_ms=100)
    audio_b, _, _ = transmit(FrameType.EMERG_BEACON, "OE3GAS", emg,
                             channel=7, use_fec=True, window=True, add_silence_ms=100)
    iq_a = nf_to_iq_usb(audio_a, HACKRF_SAMPLE_RATE)
    iq_b = nf_to_iq_usb(audio_b, HACKRF_SAMPLE_RATE)
    max_len = max(len(iq_a), len(iq_b))
    mixed = np.zeros(max_len, dtype=np.complex64)
    mixed[:len(iq_a)] += iq_a
    mixed[:len(iq_b)] += iq_b
    peak = float(np.max(np.abs(mixed)))
    if peak > 0:
        mixed = (mixed / peak * 0.7).astype(np.complex64)
    return mixed


def stream_iq_diagnostic(freq_hz, gain_db, iq, label, warmup_s=0.0):
    """Sendet IQ mit Einzel-Timing für jeden SoapySDR-Aufruf."""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  IQ: {len(iq)} Samples = {len(iq)/HACKRF_SAMPLE_RATE:.2f}s")
    print(f"  Warmup vor activateStream: {warmup_s:.1f}s")
    print(f"{'='*60}")

    with Watchdog("SoapySDR.Device(driver=hackrf)"):
        sdr = SoapySDR.Device(dict(driver="hackrf"))

    with Watchdog("setSampleRate"):
        sdr.setSampleRate(SOAPY_SDR_TX, 0, float(HACKRF_SAMPLE_RATE))
    with Watchdog("setFrequency"):
        sdr.setFrequency(SOAPY_SDR_TX, 0, float(freq_hz))
    with Watchdog("setGain VGA"):
        sdr.setGain(SOAPY_SDR_TX, 0, "VGA", float(gain_db))

    with Watchdog("setupStream"):
        tx_stream = sdr.setupStream(SOAPY_SDR_TX, SOAPY_SDR_CF32)

    # Aufwärm-Delay: HackRF Zeit geben PLL/LO zu stabilisieren
    if warmup_s > 0:
        with Watchdog(f"Warmup {warmup_s:.1f}s"):
            time.sleep(warmup_s)

    with Watchdog("activateStream"):
        sdr.activateStream(tx_stream)

    # Zero-Padding für sauberen Flush
    flush = int(HACKRF_SAMPLE_RATE * 0.1)
    iq_padded = np.concatenate([iq, np.zeros(flush, dtype=np.complex64)])

    BLOCK = 4096
    pos = 0
    iters = 0
    skipped = 0
    neg_rets = 0
    pos_rets = 0
    t_write0 = time.time()
    with Watchdog("write-loop (gesamt)", timeout=30.0):
        while pos < len(iq_padded):
            chunk = iq_padded[pos:pos + BLOCK]
            # DEFAULT-Timeout (kein timeoutUs!) — wie funktionierende transmit()
            sr = sdr.writeStream(tx_stream, [chunk], len(chunk))
            iters += 1
            if sr.ret > 0:
                pos += sr.ret
                pos_rets += 1
            else:
                # Original-Verhalten: Block überspringen bei ret<=0
                if sr.ret < 0:
                    neg_rets += 1
                pos += BLOCK
                skipped += 1
    dt_write = time.time() - t_write0
    written = pos_rets * BLOCK
    pct = 100.0 * written / len(iq_padded)
    print(f"{ts()}  Write-Loop: {iters} Iterationen, {dt_write:.2f}s", flush=True)
    print(f"{ts()}  Erfolgreiche Writes: {pos_rets}  "
          f"(~{written} Samples = {pct:.0f}% des Signals)", flush=True)
    print(f"{ts()}  Übersprungen (ret<=0): {skipped}  "
          f"(davon {neg_rets}× ret<0)", flush=True)
    if pct > 95:
        print(f"{ts()}  ✓✓ VOLLSTÄNDIG übertragen — Fix funktioniert!", flush=True)
    elif pct > 50:
        print(f"{ts()}  ⚠ TEILWEISE übertragen ({pct:.0f}%)", flush=True)
    else:
        print(f"{ts()}  ✗ NUR {pct:.0f}% übertragen — Underrun-Problem besteht", flush=True)

    with Watchdog("deactivateStream"):
        sdr.deactivateStream(tx_stream)
    with Watchdog("closeStream"):
        sdr.closeStream(tx_stream)
    with Watchdog("del sdr"):
        del sdr

    print(f"{ts()}  ✓✓✓ {label} ERFOLGREICH ABGESCHLOSSEN ✓✓✓")


def main():
    p = argparse.ArgumentParser(description="HackRF Dual-Kanal Diagnose")
    p.add_argument("--freq", type=float, default=14_110_000.0)
    p.add_argument("--gain", type=int, default=30)
    p.add_argument("--skip-single", action="store_true",
                   help="Einzel-Ton-Test überspringen")
    args = p.parse_args()

    print(f"\n{ts()}  HackRF Diagnose startet")
    print(f"{ts()}  Freq: {args.freq/1e6:.3f} MHz, Gain: {args.gain} dB\n")

    # ── Test 1: Einzel-Ton (einfachstes IQ) ───────────────────────────
    if not args.skip_single:
        print(f"{ts()}  Erzeuge Einzel-Ton IQ ...")
        iq_tone = make_single_tone_iq(freq_hz=900.0, duration_s=3.0)
        try:
            stream_iq_diagnostic(args.freq, args.gain, iq_tone,
                                 "TEST 1: EINZEL-TON (900 Hz Basisband)")
        except KeyboardInterrupt:
            print(f"\n{ts()}  Test 1 mit Strg+C abgebrochen")
            return
        except Exception as e:
            print(f"\n{ts()}  Test 1 FEHLER: {e}")
            import traceback; traceback.print_exc()
            return

        print(f"\n{ts()}  Warte 3s vor Test 2 ...")
        time.sleep(3)

    # ── Test 2: Dual-Kanal GUST ─────────────────────────────────────
    print(f"\n{ts()}  Erzeuge Dual-Kanal IQ (Kanal 2 + 7) ...")
    iq_dual = make_dual_iq()
    try:
        stream_iq_diagnostic(args.freq, args.gain, iq_dual,
                             "TEST 2: DUAL-KANAL GUST (Kanal 2 + 7)")
    except KeyboardInterrupt:
        print(f"\n{ts()}  Test 2 mit Strg+C abgebrochen")
        return
    except Exception as e:
        print(f"\n{ts()}  Test 2 FEHLER: {e}")
        import traceback; traceback.print_exc()
        return

    print(f"\n{ts()}  ════════════════════════════════════════")
    print(f"{ts()}  ALLE TESTS ERFOLGREICH — kein Hang!")
    print(f"{ts()}  ════════════════════════════════════════")


if __name__ == "__main__":
    main()
