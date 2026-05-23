#!/usr/bin/env python3
"""
GUST — Standalone Beacon (Bake)                              Phase 7
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Version : 1.0.0
Datum   : Mai 2026

Eigenstaendiges Beacon-Skript fuer beliebigen TRX mit Audio-PTT (z.B.
IC-7610 + hamlib). Im Gegensatz zur interaktiven Bake in
gust_tx_test.py --beacon werden hier alle Parameter ueber die
Kommandozeile gesetzt — geeignet fuer Dauerbetrieb, systemd-Service,
Autostart oder skriptgesteuerte Testreihen.

Die Payload-Erzeugung (Wetter/Position/Text/Notfall) wird aus
gust_tx_test.py wiederverwendet, ebenso der CSV-Logger.

TX-Pfad: gust_audio.AudioTransmitter + PTT (hamlib/gpio/null).
         Kein HackRF noetig.

Parameter (Kurzuebersicht)
──────────────────────────
  --call OE3GAS          Rufzeichen der Bake (Pflicht, sofern nicht
                         in gateway.json gesetzt)
  --types wpt            Frame-Typen: w=Wetter, p=Position, t=Text,
                         e=Notfall (Standard: wpt)
  --max-pause 15         Maximale (zufaellige) Pause in Sekunden
                         zwischen zwei Frames (Standard: 15)

Verwendung
──────────
  python gust_beacon.py --call OE3GAS
  python gust_beacon.py --call OE3GAS --types wp --max-pause 30
  python gust_beacon.py --call OE3GAS --types t --max-pause 5 --device 9
  python gust_beacon.py --call OE3GAS --dry-run         (kein TX, nur Test)
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

# Windows-Konsole nutzt oft cp1252 — Box-Zeichen wuerden crashen.
# UTF-8 erzwingen (Python 3.7+).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Payload-Helfer und CSV-Logger aus gust_tx_test wiederverwenden
from gust_tx_test import (
    TxLogger,
    _weather_payload,
    _position_payload,
    _text_payload,
    _emergency_payload,
    ts,
)


# ═══════════════════════════════════════════════════════════════════════
# STANDARD-PARAMETER
# ═══════════════════════════════════════════════════════════════════════

DEFAULT_TYPES       = "wpt"          # Wetter + Position + Text
DEFAULT_MAX_PAUSE_S = 15.0
DEFAULT_GATEWAY     = "gateway.json"
BEACON_LOG_FILE     = "beacon_log.csv"
VALID_CALLSIGN      = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/")

# Kuerzel  →  interner Frame-Typ-Name
TYPE_MAP = {
    "w": "WEATHER",
    "p": "POSITION",
    "t": "TEXT",
    "e": "EMERGENCY",
}


# ═══════════════════════════════════════════════════════════════════════
# HILFSFUNKTIONEN
# ═══════════════════════════════════════════════════════════════════════

def load_gateway_config(path: str) -> dict:
    """gateway.json lesen; fehlt sie, leeres Dict zurueck."""
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"{ts()}  WARNUNG: {path} konnte nicht gelesen werden: {e}",
              flush=True)
        return {}


def validate_callsign(call: str) -> str:
    """Rufzeichen normalisieren und plausibilisieren."""
    c = call.strip().upper()
    if not (3 <= len(c) <= 6) or not all(ch in VALID_CALLSIGN for ch in c):
        raise ValueError(
            f"Ungueltiges Rufzeichen '{call}' — 3–6 Zeichen, erlaubt: A–Z, 0–9, /"
        )
    return c


def parse_types(spec: str) -> list:
    """'wpt' / 'WP' / 'w,p,t' → ['WEATHER', 'POSITION', 'TEXT']."""
    raw = spec.lower().replace(",", "").replace(" ", "")
    seen = []
    for ch in raw:
        if ch not in TYPE_MAP:
            raise ValueError(
                f"Unbekanntes Frame-Kuerzel '{ch}' — erlaubt: w, p, t, e"
            )
        name = TYPE_MAP[ch]
        if name not in seen:
            seen.append(name)
    if not seen:
        raise ValueError("Mindestens ein Frame-Typ erforderlich (w/p/t/e)")
    return seen


def build_payload(frame_type_name: str):
    """Eine zufaellige Payload fuer den gewuenschten Typ erzeugen."""
    from gust_frame import FrameType
    if frame_type_name == "WEATHER":
        return FrameType.WEATHER,      "WEATHER",   _weather_payload()
    if frame_type_name == "POSITION":
        return FrameType.POSITION,     "POSITION",  _position_payload()
    if frame_type_name == "TEXT":
        return FrameType.TEXT,         "TEXT",      _text_payload()
    if frame_type_name == "EMERGENCY":
        return FrameType.EMERG_BEACON, "EMERGENCY", _emergency_payload()
    raise ValueError(f"Unbekannter Frame-Typ: {frame_type_name}")


def build_ptt(backend: str, host: str, port: int, full_cfg: dict | None = None):
    """
    PTT-Backend-Instanz erzeugen (hamlib / gpio / null / vox).

    Bei backend == 'hamlib' wird vor dem Verbindungsaufbau ueberprueft,
    ob rigctld laeuft. Falls nicht und full_cfg['rigctld'].auto_start
    == True, wird rigctld als Hintergrundprozess gestartet.
    """
    if backend == "hamlib":
        from gust_audio import HamlibPTT, ensure_rigctld_running
        ensure_rigctld_running(full_cfg or {}, host=host, port=port)
        return HamlibPTT(host=host, port=port)
    if backend == "gpio":
        from gust_audio import GPIOPTT
        return GPIOPTT()
    if backend in ("null", "vox", "none"):
        from gust_audio import NullPTT
        return NullPTT()
    raise ValueError(f"Unbekanntes PTT-Backend: {backend}")


def normalize_level(level) -> float:
    """gateway.json-Konvention: Werte > 1 werden als Prozent interpretiert."""
    try:
        v = float(level)
    except (TypeError, ValueError):
        return 0.8
    if v > 1.0:
        v = v / 100.0
    return max(0.0, min(1.0, v))


# ═══════════════════════════════════════════════════════════════════════
# HAUPTLOOP
# ═══════════════════════════════════════════════════════════════════════

def run_beacon(args, cfg: dict | None = None):
    from gust_modulator import transmit, SAMPLE_RATE
    from gust_frame import channel_frequency, assign_channel

    callsign     = validate_callsign(args.call)
    frame_types  = parse_types(args.types)
    max_pause_s  = float(args.max_pause)
    if max_pause_s < 0:
        raise ValueError("--max-pause darf nicht negativ sein")

    home_ch, time_offset = assign_channel(callsign)

    # ── Kopfzeile ─────────────────────────────────────────────────────
    print(flush=True)
    print("╔══════════════════════════════════════════════════════════╗", flush=True)
    print("║  GUST Beacon  v1.0 — Standalone-Bake                     ║", flush=True)
    print("╠══════════════════════════════════════════════════════════╣", flush=True)
    print(f"║  Rufzeichen  : {callsign:<43}║", flush=True)
    print(f"║  Heimatkanal : {home_ch}  ({channel_frequency(home_ch):.0f} Hz NF){'':<31}║", flush=True)
    print(f"║  Zeitversatz : {time_offset} s{'':<42}║", flush=True)
    print(f"║  Frame-Typen : {', '.join(frame_types):<43}║", flush=True)
    print(f"║  Max. Pause  : {max_pause_s:.0f} s (zufaellig 0…max){'':<26}║", flush=True)
    dev_str = str(args.device) if args.device is not None else "Standard"
    print(f"║  Audio-Geraet: {dev_str:<43}║", flush=True)
    print(f"║  Audio-Pegel : {args.level*100:.0f} %{'':<41}║", flush=True)
    print(f"║  PTT         : {args.ptt:<43}║", flush=True)
    print(f"║  Modus       : {'DRY-RUN (kein TX)' if args.dry_run else 'LIVE — TX aktiv':<43}║", flush=True)
    print("╠══════════════════════════════════════════════════════════╣", flush=True)
    print("║  Stoppen: Strg+C                                         ║", flush=True)
    print("╚══════════════════════════════════════════════════════════╝", flush=True)
    print(flush=True)

    # ── PTT- und Audio-Setup ──────────────────────────────────────────
    ptt = None
    if not args.dry_run:
        try:
            ptt = build_ptt(args.ptt, args.hamlib_host, args.hamlib_port, cfg)
        except Exception as e:
            print(f"{ts()}  FEHLER: PTT-Backend '{args.ptt}' nicht verfuegbar: {e}",
                  flush=True)
            print(f"{ts()}  Tipp: --dry-run fuer Test ohne Hardware", flush=True)
            sys.exit(1)

    logger   = TxLogger(args.log)
    tx_count = 0

    try:
        while True:
            # Frame-Typ zufaellig aus gewaehlten Typen
            ft_name_chosen = random.choice(frame_types)
            try:
                ft, ft_str, payload = build_payload(ft_name_chosen)
            except Exception as e:
                print(f"{ts()}  FEHLER Payload-Erzeugung: {e}", flush=True)
                time.sleep(1.0)
                continue

            # Modulation
            try:
                audio, channel, _ = transmit(
                    ft, callsign, payload,
                    channel=None,            # deterministisch via SHA-256
                    use_fec=True, window=True, add_silence_ms=150,
                )
            except Exception as e:
                print(f"{ts()}  FEHLER Frame-Modulation: {e}", flush=True)
                time.sleep(1.0)
                continue

            nf_hz  = channel_frequency(channel)
            dur_s  = len(audio) / SAMPLE_RATE
            tx_count += 1

            hdr = (
                f"{ts()}  BEACON #{tx_count:4d}  {callsign:<8}  "
                f"[{ft_str:<10}]  Kanal {channel}  "
                f"NF {nf_hz:.0f} Hz  {dur_s:.2f}s"
            )

            # ── Senden ───────────────────────────────────────────────
            if args.dry_run:
                print(f"{hdr}  [DRY-RUN]", flush=True)
                logger.write(
                    nr=tx_count, callsign=callsign, frame_type=ft_str,
                    channel=channel, gain_db=0,
                    nf_hz=nf_hz, rf_mhz=0.0,
                    duration_ms=round(dur_s * 1000), status="DRY-RUN",
                )
            else:
                from gust_audio import AudioTransmitter
                t0 = time.monotonic()
                try:
                    tx = AudioTransmitter(
                        ptt=ptt,
                        device=args.device,
                        level=args.level,
                    )
                    tx.transmit_audio(audio, sample_rate=SAMPLE_RATE)
                    elapsed = (time.monotonic() - t0) * 1000
                    print(f"{hdr}  {elapsed:.0f} ms  OK", flush=True)
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

            # ── Pause: zufaellig zwischen 0 und max ───────────────────
            pause = random.uniform(0.0, max_pause_s)
            print(f"{ts()}  Naechster Frame in {pause:.1f}s ...", flush=True)
            time.sleep(pause)

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
              f"CSV: {os.path.abspath(args.log)}", flush=True)


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    # gateway.json als Default-Quelle fuer Audio/PTT
    cfg        = load_gateway_config(DEFAULT_GATEWAY)
    cfg_audio  = cfg.get("audio", {}) if isinstance(cfg, dict) else {}
    cfg_call   = cfg.get("callsign") if isinstance(cfg, dict) else None

    def_device      = cfg_audio.get("device")
    def_ptt         = cfg_audio.get("ptt_backend", "hamlib")
    def_level       = normalize_level(cfg_audio.get("level", 0.8))
    def_hamlib_host = cfg_audio.get("hamlib_host", "localhost")
    def_hamlib_port = int(cfg_audio.get("hamlib_port", 4532))

    parser = argparse.ArgumentParser(
        prog="gust_beacon.py",
        description=(
            "GUST Beacon v1.0 — Eigenstaendige Bake fuer beliebigen TRX "
            "mit Audio-PTT (z.B. IC-7610 + hamlib).\n"
            "\n"
            "Sendet endlos zufaellig gewaehlte Frames vom angegebenen Rufzeichen, "
            "mit einer zufaelligen Pause zwischen 0 und --max-pause Sekunden.\n"
            "Defaults fuer Audio-Geraet, PTT-Backend und Pegel werden aus "
            "gateway.json gelesen, koennen aber per CLI ueberschrieben werden."
        ),
        epilog=(
            "Beispiele:\n"
            "  python gust_beacon.py --call OE3GAS\n"
            "      Bake mit Wetter+Position+Text, max. 15 s Pause, Audio aus gateway.json\n"
            "\n"
            "  python gust_beacon.py --call OE3GAS --types wp --max-pause 30\n"
            "      Nur Wetter + Position, bis zu 30 s Pause zwischen Frames\n"
            "\n"
            "  python gust_beacon.py --call OE3GAS --types t --max-pause 5 --device 9\n"
            "      Nur Text-Frames im 0–5 s-Takt, ueber Audio-Geraet ID 9\n"
            "\n"
            "  python gust_beacon.py --call OE3GAS --dry-run\n"
            "      Frame-Erzeugung und Timing ohne Hardware testen\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── Haupt-Parameter (vom Nutzer gewuenscht) ───────────────────────
    parser.add_argument(
        "--call", "--callsign", dest="call",
        default=cfg_call, metavar="RUFZ",
        help=(
            "Rufzeichen der Bake (3–6 Zeichen, A–Z, 0–9, /). "
            "Wird in jeden Frame als FROM-Feld eingebaut und bestimmt "
            "ueber SHA-256 deterministisch den NF-Kanal (0–9). "
            f"Standard: aus gateway.json (aktuell: {cfg_call or '— nicht gesetzt —'})"
        ),
    )
    parser.add_argument(
        "--types", "--frames", dest="types",
        default=DEFAULT_TYPES, metavar="WPTE",
        help=(
            "Frame-Typen, aus denen die Bake zufaellig waehlt. "
            "Kuerzel beliebig kombinieren: "
            "w=Wetter (0x01), p=Position (0x02), t=Text (0x40), "
            "e=Notfall-Beacon (0x20, NUR FUER ECHTE NOTFAELLE!). "
            f"Standard: '{DEFAULT_TYPES}' (Wetter + Position + Text)"
        ),
    )
    parser.add_argument(
        "--max-pause", dest="max_pause",
        type=float, default=DEFAULT_MAX_PAUSE_S, metavar="SEK",
        help=(
            "Maximale Pause in Sekunden zwischen zwei Frames. Die tatsaechliche "
            "Pause wird bei jedem Durchlauf zufaellig zwischen 0 und diesem Wert "
            "gewuerfelt — das reduziert die Kollisionswahrscheinlichkeit mit "
            "anderen Baken auf demselben Kanal. "
            f"Standard: {DEFAULT_MAX_PAUSE_S:.0f}"
        ),
    )

    # ── Hardware-/PTT-Parameter (mit Defaults aus gateway.json) ───────
    parser.add_argument(
        "--device", type=int, default=def_device, metavar="ID",
        help=(
            "Audio-Ausgabegeraet (Integer-ID, NICHT Name — siehe 'py gust.py "
            "devices'). Standard: aus gateway.json "
            f"(aktuell: {def_device if def_device is not None else 'System-Default'})"
        ),
    )
    parser.add_argument(
        "--level", type=float, default=def_level, metavar="PEGEL",
        help=(
            "Audio-Ausgangspegel als float 0.0–1.0 (z.B. 0.10 = 10 Prozent). "
            "Werte > 1 werden als Prozent interpretiert (10 = 0.10). "
            f"Standard aus gateway.json: {def_level:.2f}"
        ),
    )
    parser.add_argument(
        "--ptt", default=def_ptt, metavar="BACKEND",
        choices=["hamlib", "gpio", "null", "vox", "none"],
        help=(
            "PTT-Backend: 'hamlib' (rigctld, fuer IC-7610 u.a.), 'gpio' "
            "(Raspberry Pi GPIO), 'null' / 'vox' / 'none' (kein Hardware-PTT). "
            f"Standard aus gateway.json: {def_ptt}"
        ),
    )
    parser.add_argument(
        "--hamlib-host", default=def_hamlib_host, metavar="HOST",
        help=f"rigctld-Host (nur bei --ptt hamlib). Standard: {def_hamlib_host}",
    )
    parser.add_argument(
        "--hamlib-port", type=int, default=def_hamlib_port, metavar="PORT",
        help=f"rigctld-Port (nur bei --ptt hamlib). Standard: {def_hamlib_port}",
    )

    # ── Sonstiges ─────────────────────────────────────────────────────
    parser.add_argument(
        "--log", default=BEACON_LOG_FILE, metavar="DATEI",
        help=f"CSV-Logfile fuer alle gesendeten Frames. Standard: {BEACON_LOG_FILE}",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Kein TX — nur Frame-Erzeugung, Modulation und Timing pruefen "
             "(weder Audio noch PTT werden angefasst).",
    )

    args = parser.parse_args()

    if not args.call:
        parser.error(
            "Rufzeichen erforderlich — entweder per --call setzen oder "
            "'callsign' in gateway.json eintragen."
        )

    # Pegel-Konvention (gateway.json: Prozent > 1) auch fuer CLI anwenden
    args.level = normalize_level(args.level)

    try:
        run_beacon(args, cfg)
    except ValueError as e:
        parser.error(str(e))


if __name__ == "__main__":
    main()
