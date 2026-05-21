#!/usr/bin/env python3
"""
GUST — HackRF TX-Test                                     Phase 7
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Version : 1.1.0
Datum   : Mai 2026

Sendet zufällige GUST-Frames via HackRF One.
Neben WEATHER / POSITION / TEXT werden auch DUAL-Kanal-Emergency-Frames
gesendet: dasselbe Signal läuft gleichzeitig auf zwei NF-Kanälen.

Dual-Kanal-Prinzip
──────────────────
  audio_A  (Kanal A, volle Amplitude × 1/√2)
  audio_B  (Kanal B, selbe Nutzlast, volle Amplitude × 1/√2)
  mixed   = audio_A + audio_B   (normalisiert auf 0.9 Peak)

  Beide MFSK-8-Signale liegen in getrennten 250-Hz-Sub-Bändern des
  SSB-Passbands → keine spektrale Überlappung, kein gegenseitiges
  Stören. Der Empfänger kann jeden Kanal unabhängig dekodieren.

CSV-Log: tx_test_log.csv
  Spalten: timestamp, nr, callsign, frame_type, channel,
           channel_b, gain_db, nf_hz, rf_mhz, duration_ms, status

Verwendung
──────────
  python tx_test.py
  python tx_test.py --count 15 --min-gain 24 --max-gain 32
  python tx_test.py --dual-only     (nur Dual-Kanal-Frames)
  python tx_test.py --no-dual       (keine Dual-Kanal-Frames)
  python tx_test.py --dry-run       (kein HackRF TX)
"""

import argparse
import csv
import os
import random
import sys
import time
from datetime import datetime

import numpy as np

# ═══════════════════════════════════════════════════════════════════════
# STANDARD-PARAMETER
# ═══════════════════════════════════════════════════════════════════════

TX_FREQ_HZ   = 14_110_000.0
MIN_GAIN_DB  = 22
MAX_GAIN_DB  = 32
PAUSE_S      = 8.0
LOG_FILE     = "tx_test_log.csv"
DUAL_PROB    = 0.3   # 30% der Frames als Dual-Kanal

OE_PREFIXES  = ["OE1", "OE2", "OE3", "OE4", "OE5", "OE6", "OE7", "OE8", "OE9"]
LETTERS      = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


# ═══════════════════════════════════════════════════════════════════════
# HILFSFUNKTIONEN
# ═══════════════════════════════════════════════════════════════════════

def ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:12]


def random_callsign() -> str:
    return random.choice(OE_PREFIXES) + "".join(random.choices(LETTERS, k=3))


def _weather_payload():
    from gust_frame import encode_weather
    return encode_weather(
        temp_c       = round(random.uniform(-10, 40), 1),
        humidity_pct = random.randint(20, 99),
        pressure_hpa = round(random.uniform(980, 1040), 1),
        wind_kmh     = random.randint(0, 80),
        wind_deg     = random.randint(0, 359),
        rain_mm_h    = round(random.uniform(0, 20), 1),
        uv_index     = random.randint(0, 11),
    )


def _position_payload():
    from gust_frame import encode_position, POS_FLAG_GPS_FIX
    return encode_position(
        lat_deg     = round(random.uniform(47.5, 48.8), 4),
        lon_deg     = round(random.uniform(15.5, 17.2), 4),
        alt_m       = random.randint(150, 2000),
        speed_kmh   = random.randint(0, 120),
        heading_deg = random.randint(0, 359),
        timestamp   = 0,
        flags       = POS_FLAG_GPS_FIX,
    )


def _text_payload():
    from gust_frame import fragment_text
    texts = ["Test 73", "QSL?", "GUST Test", "CQ CQ CQ", "73 de OE3GAS"]
    frags = fragment_text(random.choice(texts),
                          dest_call=random_callsign(), seq_nr=0)
    return frags[0]


def _emergency_payload():
    from gust_frame import (
        encode_emergency_beacon, PRIO_URGENT,
        INJURY_UNKNOWN, INJURY_MINOR, INJURY_SERIOUS,
    )
    return encode_emergency_beacon(
        lat_deg        = round(random.uniform(47.5, 48.8), 4),
        lon_deg        = round(random.uniform(15.5, 17.2), 4),
        persons        = random.randint(1, 5),
        injury_code    = random.choice([INJURY_UNKNOWN, INJURY_MINOR, INJURY_SERIOUS]),
        resource_flags = 0,
        priority       = PRIO_URGENT,
        text_snippet   = random.choice(["HELP", "SOS ", "FIRE", "MEDIC"]),
    )


def random_frame():
    """Zufälliger Einzel-Kanal-Frame."""
    from gust_frame import FrameType
    callsign = random_callsign()
    choice = random.randint(0, 2)
    if choice == 0:
        return callsign, FrameType.WEATHER,      "WEATHER",   _weather_payload()
    elif choice == 1:
        return callsign, FrameType.POSITION,     "POSITION",  _position_payload()
    else:
        return callsign, FrameType.TEXT,         "TEXT",      _text_payload()


def make_dual_iq(callsign, frame_type, payload, ch_a, ch_b, use_fec=True, test_flag=True):
    """
    Erzeugt ein gemischtes IQ-Signal für zwei Kanäle gleichzeitig.

    Architektur:
      NF_A → IQ_A  ─┐
                     ├─ IQ_A + IQ_B → normalisiert → HackRF.transmit_iq()
      NF_B → IQ_B  ─┘

    WICHTIG: Diese Funktion wird mit BEREITS GEÖFFNETEM HackRF-Transmitter
    aufgerufen (damit das USB-Device während der Berechnung aktiv bleibt
    und nicht vom Windows USB-Power-Management suspendiert wird).

    Returns:
        (iq_mixed: np.complex64, used_a: int, used_b: int, duration_s: float)
    """
    from gust_modulator import transmit, SAMPLE_RATE
    from gust_hackrf import nf_to_iq_usb, HACKRF_SAMPLE_RATE

    # NF für beide Kanäle erzeugen
    print(f"  [DUAL] NF Kanal {ch_a} ...", flush=True)
    audio_a, used_a, _ = transmit(
        frame_type, callsign, payload,
        channel=ch_a, use_fec=use_fec, window=True, add_silence_ms=100,
        test=test_flag,
    )
    print(f"  [DUAL] NF Kanal {ch_b} ...", flush=True)
    audio_b, used_b, _ = transmit(
        frame_type, callsign, payload,
        channel=ch_b, use_fec=use_fec, window=True, add_silence_ms=100,
        test=test_flag,
    )

    print(f"  [DUAL] IQ-Konvertierung Kanal {used_a} ...", flush=True)
    iq_a = nf_to_iq_usb(audio_a, HACKRF_SAMPLE_RATE)
    print(f"  [DUAL] IQ-Konvertierung Kanal {used_b} ...", flush=True)
    iq_b = nf_to_iq_usb(audio_b, HACKRF_SAMPLE_RATE)

    # IQ auf gleiche Länge bringen und addieren
    max_len = max(len(iq_a), len(iq_b))
    mixed = np.zeros(max_len, dtype=np.complex64)
    mixed[:len(iq_a)] += iq_a
    mixed[:len(iq_b)] += iq_b

    # Normalisieren auf 0.7 Peak (HackRF Headroom)
    peak = float(np.max(np.abs(mixed)))
    if peak > 0:
        mixed = (mixed / peak * 0.7).astype(np.complex64)

    duration = max_len / HACKRF_SAMPLE_RATE
    return mixed, used_a, used_b, duration


# ═══════════════════════════════════════════════════════════════════════
# CSV-LOGGER
# ═══════════════════════════════════════════════════════════════════════

class TxLogger:
    FIELDS = ["timestamp", "nr", "callsign", "frame_type",
              "channel", "channel_b", "gain_db",
              "nf_hz", "nf_hz_b", "rf_mhz",
              "duration_ms", "status", "notes"]

    def __init__(self, path: str):
        self._file   = open(path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=self.FIELDS,
                                      extrasaction="ignore")
        self._writer.writeheader()
        self._file.flush()

    def write(self, **kwargs):
        kwargs.setdefault("timestamp",
                          datetime.now().isoformat(timespec="milliseconds"))
        self._writer.writerow(kwargs)
        self._file.flush()

    def close(self):
        self._file.close()


# ═══════════════════════════════════════════════════════════════════════
# HAUPTPROGRAMM
# ═══════════════════════════════════════════════════════════════════════

def run(args):
    from gust_modulator import transmit, SAMPLE_RATE
    from gust_frame import (
        FrameType, channel_frequency, CHANNEL_BW_HZ,
        encode_emergency_beacon, PRIO_URGENT, INJURY_UNKNOWN,
    )

    if not args.dry_run:
        try:
            from gust_hackrf import HackRFTransmitter
        except Exception as e:
            print(f"{ts()}  FEHLER: HackRF nicht verfügbar: {e}", flush=True)
            print(f"{ts()}  Tipp: --dry-run für Test ohne Hardware", flush=True)
            sys.exit(1)

    logger   = TxLogger(args.log)
    tx_count = 0

    print(flush=True)
    print("╔══════════════════════════════════════════════════════════╗", flush=True)
    print("║  GUST TX-Test  v1.1                                  ║", flush=True)
    print("╠══════════════════════════════════════════════════════════╣", flush=True)
    print(f"║  Frequenz  : {args.freq/1e6:.3f} MHz{'':>37}║", flush=True)
    if args.gains is not None:
        gain_str = "→".join(str(g) for g in args.gains) + " dB"
        print(f"║  Gain-Folge: {gain_str:<44}║", flush=True)
    else:
        print(f"║  Gain      : {args.min_gain}–{args.max_gain} dB{'':>40}║", flush=True)
    if args.fixed_channels:
        ch_str = "+".join(str(c) for c in args.fixed_channels)
        print(f"║  Kanäle    : fest {ch_str:<40}║", flush=True)
    print(f"║  Sendungen : {'endlos' if args.count==0 else args.count}{'':>46}║", flush=True)
    dual_mode = "JA (~30% der Frames)" if not args.no_dual else "NEIN"
    if args.dual_only: dual_mode = "NUR Dual-Kanal"
    print(f"║  Dual-Chan : {dual_mode:<44}║", flush=True)
    print(f"║  Modus     : {'DRY-RUN' if args.dry_run else 'LIVE — HackRF TX aktiv':<44}║", flush=True)
    print("╠══════════════════════════════════════════════════════════╣", flush=True)
    print("║  Stoppen   : Strg+C                                     ║", flush=True)
    print("╚══════════════════════════════════════════════════════════╝", flush=True)
    print(flush=True)

    try:
        while args.count == 0 or tx_count < args.count:

            # Gain: aus fester Folge oder zufällig
            if args.gains is not None:
                gain_db = args.gains[tx_count]
            else:
                gain_db = random.randint(args.min_gain, args.max_gain)
            tx_count += 1

            # ── Frame-Typ wählen ──────────────────────────────────────
            use_dual = (
                args.dual_only
                or (not args.no_dual and random.random() < DUAL_PROB)
            )

            if use_dual:
                # ── Dual-Kanal Emergency ──────────────────────────────
                callsign = random_callsign()
                payload  = _emergency_payload()
                ft       = FrameType.EMERG_BEACON
                ft_name  = "EMERGENCY"

                # Kanäle: fest vorgegeben oder zufällig (mind. 3 Kanäle Abstand)
                if args.fixed_channels and len(args.fixed_channels) == 2:
                    ch_a, ch_b = args.fixed_channels
                else:
                    ch_a = random.randint(0, 9)
                    ch_b = (ch_a + random.randint(3, 7)) % 10

                # IQ ZUERST berechnen (HackRF noch NICHT geöffnet):
                # → Device wird erst kurz vor dem Streamen geöffnet
                # → keine CPU-Last (FFT/Hilbert) während Device offen ist
                try:
                    iq_dual, used_a, used_b, duration = make_dual_iq(
                        callsign, ft, payload, ch_a, ch_b,
                        test_flag=not args.no_test,
                    )
                except Exception as e:
                    print(f"{ts()}  FEHLER Dual-IQ: {e}", flush=True)
                    tx_count -= 1
                    continue

                nf_a    = channel_frequency(used_a)
                nf_b    = channel_frequency(used_b)
                rf_mhz  = args.freq / 1e6 + nf_a / 1e6

                hdr = (
                    f"{ts()}  TX #{tx_count:3d}  {callsign:<8}  "
                    f"[{ft_name:<10}]  "
                    f"Kanal {used_a}+{used_b}  "
                    f"Gain {gain_db:2d} dB  "
                    f"NF {nf_a:.0f}+{nf_b:.0f} Hz  "
                    f"RF {rf_mhz:.6f} MHz  "
                    f"DUAL ★"
                )

            else:
                # ── Einzel-Kanal ──────────────────────────────────────
                callsign, ft, ft_name, payload = random_frame()
                fixed_ch = (args.fixed_channels[0]
                            if args.fixed_channels else None)

                try:
                    audio, channel, _ = transmit(
                        ft, callsign, payload, channel=fixed_ch,
                        use_fec=True, window=True, add_silence_ms=100,
                        test=(not args.no_test),
                    )
                except Exception as e:
                    print(f"{ts()}  FEHLER Frame: {e}", flush=True)
                    tx_count -= 1
                    continue

                nf_a   = channel_frequency(channel)
                nf_b   = None
                used_a = channel
                used_b = None
                rf_mhz = args.freq / 1e6 + nf_a / 1e6
                duration = len(audio) / SAMPLE_RATE

                hdr = (
                    f"{ts()}  TX #{tx_count:3d}  {callsign:<8}  "
                    f"[{ft_name:<10}]  "
                    f"Kanal {used_a}{'':>3}  "
                    f"Gain {gain_db:2d} dB  "
                    f"NF {nf_a:.0f} Hz{'':>8}  "
                    f"RF {rf_mhz:.6f} MHz"
                )

            # ── Senden ────────────────────────────────────────────────
            if args.dry_run:
                print(f"{hdr}  {duration:.2f}s  [DRY-RUN]", flush=True)
                logger.write(
                    nr=tx_count, callsign=callsign, frame_type=ft_name,
                    channel=used_a, channel_b=used_b, gain_db=gain_db,
                    nf_hz=nf_a, nf_hz_b=nf_b, rf_mhz=round(rf_mhz, 6),
                    duration_ms=round(duration * 1000), status="DRY-RUN",
                )
            else:
                t0 = time.monotonic()
                try:
                    # Beide Pfade identisch: IQ ist bereits berechnet,
                    # Device wird erst jetzt geöffnet (minimale Offen-Zeit).
                    tx = HackRFTransmitter(freq_hz=args.freq, gain_db=gain_db)
                    tx.open()
                    if use_dual:
                        tx.transmit_iq(iq_dual)
                    else:
                        tx.transmit(audio)
                    tx.close()
                    elapsed = (time.monotonic() - t0) * 1000
                    print(f"{hdr}  {elapsed:.0f} ms  ✓", flush=True)
                    logger.write(
                        nr=tx_count, callsign=callsign, frame_type=ft_name,
                        channel=used_a, channel_b=used_b, gain_db=gain_db,
                        nf_hz=nf_a, nf_hz_b=nf_b, rf_mhz=round(rf_mhz, 6),
                        duration_ms=round(elapsed), status="OK",
                    )
                except Exception as e:
                    elapsed = (time.monotonic() - t0) * 1000
                    try: tx.close()
                    except: pass
                    print(f"{hdr}  FEHLER: {e}", flush=True)
                    logger.write(
                        nr=tx_count, callsign=callsign, frame_type=ft_name,
                        channel=used_a, channel_b=used_b, gain_db=gain_db,
                        nf_hz=nf_a, nf_hz_b=nf_b, rf_mhz=round(rf_mhz, 6),
                        status="ERROR", notes=str(e),
                    )

            # Pause (außer nach letzter Sendung)
            if args.count == 0 or tx_count < args.count:
                _pause = random.uniform(1.0, args.pause)
                print(f"{ts()}  Pause {_pause:.1f}s ...", flush=True)
                time.sleep(_pause)

    except KeyboardInterrupt:
        print(f"\n{ts()}  Strg+C — beende ...", flush=True)
    finally:
        logger.close()
        print(f"\n{ts()}  Abgeschlossen: {tx_count} Sendungen  "
              f"CSV: {os.path.abspath(args.log)}", flush=True)


def main():
    parser = argparse.ArgumentParser(
        prog="tx_test.py",
        description="GUST Phase 7 — TX-Test mit Einzel- und Dual-Kanal-Frames",
    )
    parser.add_argument("--freq",      type=float, default=TX_FREQ_HZ,
                        help=f"TX-Frequenz Hz (Standard: {TX_FREQ_HZ:.0f})")
    parser.add_argument("--count",     type=int,   default=0,
                        help="Anzahl Sendungen (0=endlos)")
    parser.add_argument("--min-gain",  type=int,   default=MIN_GAIN_DB,
                        help=f"Min VGA Gain dB (Standard: {MIN_GAIN_DB})")
    parser.add_argument("--max-gain",  type=int,   default=MAX_GAIN_DB,
                        help=f"Max VGA Gain dB (Standard: {MAX_GAIN_DB})")
    parser.add_argument("--pause",     type=float, default=PAUSE_S,
                        help=f"Maximale Pause zwischen Sendungen in s — tatsächliche Pause zufällig 1..max (Standard: {PAUSE_S})")
    parser.add_argument("--log",       default=LOG_FILE,
                        help=f"CSV-Logfile (Standard: {LOG_FILE})")
    parser.add_argument("--dual-only", action="store_true",
                        help="Nur Dual-Kanal-Emergency-Frames senden")
    parser.add_argument("--no-dual",   action="store_true",
                        help="Keine Dual-Kanal-Frames")
    parser.add_argument("--channels",  default=None,
                        help="Feste Kanäle statt zufällig, z.B. '0,7' (Dual) "
                             "oder '3' (Einzel). Gilt für alle Sendungen.")
    parser.add_argument("--gain-sequence", default=None,
                        help="Exakte Gain-Folge statt zufällig, z.B. "
                             "'28,26,24,22,20'. Setzt --count automatisch.")
    parser.add_argument("--no-test",   action="store_true",
                        help="TEST-Flag NICHT setzen (Standard: alle Frames aus gust_tx_test.py sind Testframes)")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Kein HackRF TX — nur Frame-Erzeugung testen")
    args = parser.parse_args()

    # --channels parsen: feste Kanäle
    args.fixed_channels = None
    if args.channels:
        try:
            args.fixed_channels = [int(x) for x in args.channels.split(",")]
            assert all(0 <= c <= 9 for c in args.fixed_channels)
            assert 1 <= len(args.fixed_channels) <= 2
        except Exception:
            parser.error("--channels muss '0,7' (Dual) oder '3' (Einzel) sein, Kanäle 0–9")

    # --gain-sequence parsen: exakte Gain-Folge
    args.gains = None
    if args.gain_sequence:
        try:
            args.gains = [int(x) for x in args.gain_sequence.split(",")]
            assert all(0 <= g <= 47 for g in args.gains)
        except Exception:
            parser.error("--gain-sequence muss z.B. '28,26,24,22,20' sein (0–47 dB)")
        args.count = len(args.gains)   # Anzahl = Länge der Folge

    run(args)


if __name__ == "__main__":
    main()