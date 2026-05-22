#!/usr/bin/env python3
"""
GUST — TX-Test + Beacon-Modus                              Phase 7
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Version : 1.2.0
Datum   : Mai 2026

Zwei Betriebsmodi:

1) HackRF TX-Test (Standard)
   Sendet zufällige GUST-Frames via HackRF One (SNR-Messungen, Dual-Kanal).
   Neben WEATHER / POSITION / TEXT werden auch DUAL-Kanal-Emergency-Frames
   gesendet: dasselbe Signal läuft gleichzeitig auf zwei NF-Kanälen.

   Dual-Kanal-Prinzip:
     audio_A + audio_B → gemischt, normalisiert → HackRF.transmit_iq()
     Beide Signale in getrennten 250-Hz-Sub-Bändern — keine Überlappung.

2) Beacon-Modus  (--beacon)
   Interaktive Bake für beliebigen TRX mit Audio-PTT und hamlib.
   Kein HackRF nötig. Fragt beim Start nach Rufzeichen, Frame-Typen,
   Audio-Gerät und PTT-Backend. Sendet alle 30 Sekunden einen zufälligen
   Frame vom eigenen Rufzeichen — endlos bis Strg+C.
   Ziel: jeder Teilnehmer kann eine eigene Bake in Betrieb nehmen.

CSV-Logs:
  tx_test_log.csv  — HackRF-Test
  beacon_log.csv   — Beacon-Modus

Verwendung
──────────
  python gust_tx_test.py                          HackRF-Test (zufällig)
  python gust_tx_test.py --count 15 --min-gain 24 --max-gain 32
  python gust_tx_test.py --dual-only              nur Dual-Kanal
  python gust_tx_test.py --gain-sequence 28,24,20,16,12,8,4,1
  python gust_tx_test.py --dry-run                kein TX, nur Test
  python gust_tx_test.py --beacon                 Beacon-Modus (interaktiv)
  python gust_tx_test.py --beacon --dry-run       Beacon ohne Hardware
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


def make_dual_iq(callsign, frame_type, payload, ch_a, ch_b, use_fec=True):
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
    )
    print(f"  [DUAL] NF Kanal {ch_b} ...", flush=True)
    audio_b, used_b, _ = transmit(
        frame_type, callsign, payload,
        channel=ch_b, use_fec=use_fec, window=True, add_silence_ms=100,
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
                print(f"{ts()}  Pause {args.pause:.0f}s ...", flush=True)
                time.sleep(args.pause)

    except KeyboardInterrupt:
        print(f"\n{ts()}  Strg+C — beende ...", flush=True)
    finally:
        logger.close()
        print(f"\n{ts()}  Abgeschlossen: {tx_count} Sendungen  "
              f"CSV: {os.path.abspath(args.log)}", flush=True)


# ═══════════════════════════════════════════════════════════════════════
# BEACON-MODUS — Interaktive Bake für beliebigen TRX via Audio + hamlib
# ═══════════════════════════════════════════════════════════════════════

BEACON_INTERVAL_S = 30.0          # Abstand zwischen Frames (fest, nicht konfigurierbar)
BEACON_LOG_FILE   = "beacon_log.csv"
VALID_CALLSIGN    = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/")

BEACON_FRAME_MENU = [
    ("W", "WEATHER",   "Wetter-Telemetrie   (0x01)"),
    ("P", "POSITION",  "Position / GPS      (0x02)"),
    ("T", "TEXT",      "Freitext / QSO      (0x40)"),
    ("E", "EMERGENCY", "Notfall-Beacon      (0x20)  ⚠ nur für echte Notfälle!"),
]


def _ask_callsign() -> str:
    """Rufzeichen interaktiv abfragen und validieren."""
    while True:
        raw = input("  Rufzeichen (z.B. OE3GAS): ").strip().upper()
        if 3 <= len(raw) <= 6 and all(c in VALID_CALLSIGN for c in raw):
            return raw
        print(f"  ✗ Ungültig — 3–6 Zeichen, erlaubt: A–Z, 0–9, /")


def _ask_frame_types() -> list:
    """Frame-Typen interaktiv auswählen (Mehrfachauswahl)."""
    print()
    print("  Welche Frame-Typen soll die Bake senden?")
    print("  (Mehrfachauswahl: Buchstaben eingeben, z.B. 'WP' für Wetter + Position)")
    print()
    for key, _, label in BEACON_FRAME_MENU:
        print(f"    [{key}]  {label}")
    print()
    while True:
        raw = input("  Auswahl: ").strip().upper()
        selected = []
        for key, name, _ in BEACON_FRAME_MENU:
            if key in raw:
                selected.append(name)
        if selected:
            return selected
        print("  ✗ Mindestens einen Typ auswählen (W / P / T / E)")


def _ask_audio_device() -> int | None:
    """Audio-Gerät interaktiv abfragen."""
    try:
        from gust_audio import list_audio_devices
        print()
        print("  Verfügbare Audio-Ausgabegeräte:")
        list_audio_devices()
    except Exception:
        pass
    print()
    raw = input("  Audio-Gerät ID (leer = Standard): ").strip()
    if raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        print("  ✗ Ungültig — Standard-Gerät wird verwendet")
        return None


def _ask_ptt_backend() -> tuple[str, str, int]:
    """PTT-Backend interaktiv auswählen."""
    print()
    print("  PTT-Steuerung:")
    print("    [1]  hamlib   — rigctld (IC-7610 und andere CAT-fähige TRX)")
    print("    [2]  gpio     — Raspberry Pi GPIO")
    print("    [3]  null     — kein PTT (Dry-Run / VOX)")
    print()
    while True:
        raw = input("  Auswahl [1/2/3]: ").strip()
        if raw == "1":
            host = input("  hamlib Host [localhost]: ").strip() or "localhost"
            port_raw = input("  hamlib Port [4532]: ").strip()
            port = int(port_raw) if port_raw.isdigit() else 4532
            return "hamlib", host, port
        elif raw == "2":
            return "gpio", "", 0
        elif raw == "3":
            return "null", "", 0
        print("  ✗ Bitte 1, 2 oder 3 eingeben")


def _build_beacon_frame(callsign: str, frame_type_name: str):
    """Einen einzelnen Beacon-Frame erzeugen."""
    from gust_frame import (
        FrameType,
        encode_emergency_beacon, PRIO_URGENT,
        INJURY_UNKNOWN, INJURY_MINOR,
    )
    if frame_type_name == "WEATHER":
        return FrameType.WEATHER, "WEATHER", _weather_payload()
    elif frame_type_name == "POSITION":
        return FrameType.POSITION, "POSITION", _position_payload()
    elif frame_type_name == "TEXT":
        return FrameType.TEXT, "TEXT", _text_payload()
    elif frame_type_name == "EMERGENCY":
        payload = encode_emergency_beacon(
            lat_deg        = round(random.uniform(47.5, 48.8), 4),
            lon_deg        = round(random.uniform(15.5, 17.2), 4),
            persons        = 1,
            injury_code    = INJURY_UNKNOWN,
            resource_flags = 0,
            priority       = PRIO_URGENT,
            text_snippet   = "TEST",
        )
        return FrameType.EMERG_BEACON, "EMERGENCY", payload
    raise ValueError(f"Unbekannter Frame-Typ: {frame_type_name}")


def run_beacon(dry_run: bool = False):
    """
    Interaktiver Beacon-Modus.

    Fragt beim Start nach Rufzeichen, Frame-Typen, Audio-Gerät und PTT-Backend.
    Sendet dann alle 30 Sekunden einen zufällig gewählten Frame vom eingegebenen
    Rufzeichen — endlos bis Strg+C.

    TX-Pfad: gust_audio.py (AudioTransmitter + PTT) — kein HackRF nötig.
    Damit kann jeder Teilnehmer mit beliebigem TRX eine eigene Bake betreiben.
    """
    from gust_modulator import transmit, SAMPLE_RATE
    from gust_frame import channel_frequency, assign_channel

    print(flush=True)
    print("╔══════════════════════════════════════════════════════════╗", flush=True)
    print("║  GUST Beacon-Modus — Interaktive Bake                   ║", flush=True)
    print("╠══════════════════════════════════════════════════════════╣", flush=True)
    print("║  Sendet zufällige Frames mit deinem Rufzeichen          ║", flush=True)
    print(f"║  Abstand: {BEACON_INTERVAL_S:.0f}s  |  Stoppen: Strg+C{'':<22}║", flush=True)
    print("╚══════════════════════════════════════════════════════════╝", flush=True)
    print(flush=True)

    # ── Interaktive Konfiguration ─────────────────────────────────────
    print("── Konfiguration ──────────────────────────────────────────")
    print()
    print("  Rufzeichen:")
    callsign     = _ask_callsign()
    frame_types  = _ask_frame_types()
    audio_device = _ask_audio_device()
    ptt_backend, hamlib_host, hamlib_port = _ask_ptt_backend()

    # Kanalinfo für dieses Rufzeichen
    home_ch, time_offset = assign_channel(callsign)

    # ── Zusammenfassung ───────────────────────────────────────────────
    print()
    print("╔══════════════════════════════════════════════════════════╗", flush=True)
    print("║  Beacon-Konfiguration                                    ║", flush=True)
    print("╠══════════════════════════════════════════════════════════╣", flush=True)
    print(f"║  Rufzeichen  : {callsign:<43}║", flush=True)
    print(f"║  Heimatkanal : {home_ch}  ({channel_frequency(home_ch):.0f} Hz NF){'':<31}║", flush=True)
    print(f"║  Zeitversatz : {time_offset} s{'':<42}║", flush=True)
    print(f"║  Frame-Typen : {', '.join(frame_types):<43}║", flush=True)
    dev_str = str(audio_device) if audio_device is not None else "Standard"
    print(f"║  Audio-Gerät : {dev_str:<43}║", flush=True)
    print(f"║  PTT         : {ptt_backend:<43}║", flush=True)
    print(f"║  Intervall   : {BEACON_INTERVAL_S:.0f} s (fest){'':<36}║", flush=True)
    print(f"║  Modus       : {'DRY-RUN — kein TX' if dry_run else 'LIVE — TX aktiv':<43}║", flush=True)
    print("╠══════════════════════════════════════════════════════════╣", flush=True)
    print("║  Stoppen: Strg+C                                         ║", flush=True)
    print("╚══════════════════════════════════════════════════════════╝", flush=True)
    print(flush=True)

    # ── PTT-Backend aufbauen ──────────────────────────────────────────
    ptt = None
    if not dry_run:
        try:
            
            from gust_audio import AudioTransmitter
            if ptt_backend == "hamlib":
                from gust_audio import HamlibPTT
                ptt = HamlibPTT(host=hamlib_host, port=hamlib_port)
            elif ptt_backend == "gpio":
                from gust_audio import GPIOPTT
                ptt = GPIOPTT()
            else:
                from gust_audio import NullPTT
                ptt = NullPTT()
                                
        except Exception as e:
            print(f"{ts()}  FEHLER: Audio/PTT nicht verfügbar: {e}", flush=True)
            print(f"{ts()}  Tipp: --beacon --dry-run für Test ohne Hardware", flush=True)
            sys.exit(1)

    # ── CSV-Logger ────────────────────────────────────────────────────
    logger   = TxLogger(BEACON_LOG_FILE)
    tx_count = 0
    next_tick = time.monotonic()

    try:
        while True:
            # Frame-Typ zufällig aus gewählten Typen
            ft_name_chosen = random.choice(frame_types)
            ft, ft_str, payload = _build_beacon_frame(callsign, ft_name_chosen)

            try:
                audio, channel, _ = transmit(
                    ft, callsign, payload,
                    channel=None,        # deterministisch via SHA-256
                    use_fec=True, window=True, add_silence_ms=150,
                )
            except Exception as e:
                print(f"{ts()}  FEHLER Frame-Erzeugung: {e}", flush=True)
                next_tick += BEACON_INTERVAL_S
                time.sleep(max(0, next_tick - time.monotonic()))
                continue

            nf_hz  = channel_frequency(channel)
            dur_s  = len(audio) / SAMPLE_RATE
            tx_count += 1

            hdr = (
                f"{ts()}  BEACON #{tx_count:4d}  {callsign:<8}  "
                f"[{ft_str:<10}]  Kanal {channel}  "
                f"NF {nf_hz:.0f} Hz  {dur_s:.2f}s"
            )

            if dry_run:
                print(f"{hdr}  [DRY-RUN]", flush=True)
                logger.write(
                    nr=tx_count, callsign=callsign, frame_type=ft_str,
                    channel=channel, gain_db=0,
                    nf_hz=nf_hz, rf_mhz=0.0,
                    duration_ms=round(dur_s * 1000), status="DRY-RUN",
                )
            else:
                t0 = time.monotonic()
                try:
                    tx = AudioTransmitter(
                        ptt=ptt,
                        device=audio_device,
                        level=1.0,
                    )
                    tx.transmit_audio(audio, sample_rate=SAMPLE_RATE)
                    elapsed = (time.monotonic() - t0) * 1000
                    print(f"{hdr}  {elapsed:.0f} ms  ✓", flush=True)
                    logger.write(
                        nr=tx_count, callsign=callsign, frame_type=ft_str,
                        channel=channel, gain_db=0,
                        nf_hz=nf_hz, rf_mhz=0.0,
                        duration_ms=round(elapsed), status="OK",
                    )
                except Exception as e:
                    print(f"{hdr}  FEHLER: {e}", flush=True)
                    logger.write(
                        nr=tx_count, callsign=callsign, frame_type=ft_str,
                        channel=channel, gain_db=0,
                        nf_hz=nf_hz, rf_mhz=0.0,
                        status="ERROR", notes=str(e),
                    )

            # Fixed-Cadence: nächster Slot unabhängig von TX-Dauer
            next_tick += BEACON_INTERVAL_S
            wait = next_tick - time.monotonic()
            if wait > 0:
                print(f"{ts()}  Nächster Frame in {wait:.0f}s ...", flush=True)
                time.sleep(wait)
            else:
                # TX hat länger gedauert als Intervall → sofort weiter, resync
                next_tick = time.monotonic()

    except KeyboardInterrupt:
        print(f"\n{ts()}  Strg+C — Bake gestoppt.", flush=True)
    finally:
        if ptt:
            try:
                ptt.release()
            except Exception:
                pass
        logger.close()
        print(f"{ts()}  Beacon beendet: {tx_count} Frames gesendet  "
              f"CSV: {os.path.abspath(BEACON_LOG_FILE)}", flush=True)


def main():
    parser = argparse.ArgumentParser(
        prog="gust_tx_test.py",
        description=(
            "GUST TX-Test v1.2 — HackRF TX-Test und Beacon-Modus\n"
            "\n"
            "Zwei Betriebsmodi:\n"
            "\n"
            "  Standard-Modus  Sendet zufällige Frames via HackRF One (SNR-Messungen,\n"
            "                  Einzel- und Dual-Kanal). Erfordert HackRF + Python 3.9 +\n"
            "                  PothosSDR (PYTHONPATH setzen).\n"
            "\n"
            "  Beacon-Modus    Interaktive Bake via Audio-PTT und hamlib.\n"
            "  (--beacon)      Kein HackRF nötig — für alle TRX mit USB-Audio geeignet.\n"
            "                  Fragt Rufzeichen, Frame-Typen, Gerät und PTT interaktiv ab.\n"
            "                  Sendet alle 30 Sekunden einen zufälligen Frame."
        ),
        epilog=(
            "Beispiele:\n"
            "  python gust_tx_test.py --beacon                 Interaktive Bake (Audio-PTT)\n"
            "  python gust_tx_test.py --beacon --dry-run       Bake ohne Hardware (Trockentest)\n"
            "  python gust_tx_test.py                          HackRF-Test, zufälliger Gain\n"
            "  python gust_tx_test.py --count 15 --min-gain 24 --max-gain 32\n"
            "  python gust_tx_test.py --dual-only              Nur Dual-Kanal-Emergency\n"
            "  python gust_tx_test.py --gain-sequence 28,24,20,16,12,8,4,1\n"
            "  python gust_tx_test.py --dry-run                HackRF-Test ohne TX\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--beacon", action="store_true",
                        help="Beacon-Modus: interaktive Bake via Audio + hamlib PTT. "
                             "Kein HackRF nötig — für alle TRX mit Audio-PTT geeignet.")
    parser.add_argument("--freq", type=float, default=TX_FREQ_HZ, metavar="HZ",
                        help=f"TX-Trägerfrequenz in Hz "
                             f"(Standard: {TX_FREQ_HZ:.0f} = "
                             f"{TX_FREQ_HZ/1e6:.3f} MHz)")
    parser.add_argument("--count", type=int, default=0, metavar="N",
                        help="Anzahl Sendungen, 0 = endlos (Standard: 0)")
    parser.add_argument("--min-gain", type=int, default=MIN_GAIN_DB, metavar="DB",
                        help=f"Min HackRF VGA-Gain in dB für Zufallsbereich "
                             f"(Standard: {MIN_GAIN_DB})")
    parser.add_argument("--max-gain", type=int, default=MAX_GAIN_DB, metavar="DB",
                        help=f"Max HackRF VGA-Gain in dB für Zufallsbereich "
                             f"(Standard: {MAX_GAIN_DB})")
    parser.add_argument("--pause", type=float, default=PAUSE_S, metavar="SEK",
                        help=f"Pause zwischen Sendungen in s (Standard: {PAUSE_S})")
    parser.add_argument("--log", default=LOG_FILE, metavar="DATEI",
                        help=f"CSV-Logfile für TX-Statistik (Standard: {LOG_FILE})")
    parser.add_argument("--dual-only", action="store_true",
                        help="Nur Dual-Kanal-Emergency-Frames senden")
    parser.add_argument("--no-dual", action="store_true",
                        help="Keine Dual-Kanal-Frames (nur Einzel-Kanal)")
    parser.add_argument("--channels", default=None, metavar="KAN",
                        help="Feste Kanäle statt zufällig: '3' (Einzel) oder '2,7' "
                             "(Dual). Gilt für alle Sendungen.")
    parser.add_argument("--gain-sequence", default=None, metavar="LISTE",
                        help="Exakte Gain-Folge für SNR-Messungen, z.B. "
                             "'28,24,20,16,12,8,4,1'. Setzt --count automatisch.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Kein TX — Frame-Erzeugung und Timing ohne Hardware testen")

    # No-Args-Hint — vor parse_args()
    if len(sys.argv) == 1:
        print("GUST TX-Test — HackRF TX-Test und interaktive Bake.")
        print()
        print("Standard-Modus  : HackRF One mit Gain-Sweep / Dual-Kanal-Frames")
        print("Beacon-Modus    : interaktive Bake via Audio-PTT (--beacon)")
        print()
        print("Verwendung: python gust_tx_test.py -h  oder  --help  für Parameterübersicht")
        sys.exit(0)

    args = parser.parse_args()

    # Beacon-Modus: eigener Pfad, kein HackRF erforderlich
    if args.beacon:
        run_beacon(dry_run=args.dry_run)
        return

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