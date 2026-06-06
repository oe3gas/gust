#!/usr/bin/env python3
"""
GUST — WAV-TX: WAV-Datei über konfigurierten Audio-Pfad mit PTT senden
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Version : 1.0.0
Datum   : Juni 2026

Sendet eine WAV-Datei über den in gateway.json konfigurierten TX-Audio-
Pfad mit PTT-Steuerung (Hamlib/rigctld). Gedacht für On-Air-Stresstests:
Stresstest-WAV lokal erzeugen → über IC-7610 aussenden → Remote-KiwiSDR
empfangen → mit gust_stress_decode.py auswerten.

Verwendung:
  py gust_wav_tx.py <datei.wav>
  py gust_wav_tx.py <datei.wav> --config gateway.json
  py gust_wav_tx.py <datei.wav> --dry-run
  py gust_wav_tx.py <datei.wav> --level 25 --repeat 3 --gap 5

Optionen:
  --config   Pfad zur gateway.json (Standard: gateway.json im CWD)
  --dry-run  PTT und Audio simulieren, nichts senden
  --level    TX-Pegel 1-100 (überschreibt gateway.json-Wert)
  --repeat   Wie oft die WAV gesendet wird (Standard: 1)
  --gap      Pause zwischen Wiederholungen in Sekunden (Standard: 10)
  --list     Audiogeräte anzeigen und beenden

Beispiel für Stresstest-Session:
  1. WAV erzeugen:
       py gust_stresstest.py --seed 43 --out baseline_s43.wav
  2. Aussenden (3× mit 10s Pause):
       py gust_wav_tx.py baseline_s43.wav --repeat 3 --gap 10
  3. KiwiSDR-Aufnahme als WAV speichern
  4. Auswerten:
       py gust_stress_decode.py kiwi_aufnahme.wav --csv baseline_s43.csv
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np


# ── Hilfsfunktionen aus GUST-Modulen ────────────────────────────────

def _load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def _get_tx_audio(cfg: dict) -> dict:
    """tx_audio (neu) oder audio (alt) — Rückwärtskompatibilität."""
    return cfg.get("tx_audio") or cfg.get("audio") or {}

def _coerce_level(raw) -> float:
    """Wert > 1 = Prozent (50 → 0.5), sonst direkt als Faktor."""
    v = float(raw)
    return max(0.01, min(1.0, v / 100.0)) if v > 1.0 else max(0.01, min(1.0, v))


# ── Hauptfunktion ────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="GUST WAV-TX — WAV-Datei mit PTT über IC-7610 senden",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Verwendung:")[0].strip(),
    )
    parser.add_argument("wav", nargs="?", help="WAV-Datei zum Senden")
    parser.add_argument("--config", default="gateway.json",
                        help="Pfad zur gateway.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulation — kein Audio, kein PTT")
    parser.add_argument("--level", type=float, default=None,
                        help="TX-Pegel 1-100 (überschreibt gateway.json)")
    parser.add_argument("--repeat", type=int, default=1,
                        help="Anzahl Sendedurchgänge (Standard: 1)")
    parser.add_argument("--gap", type=float, default=10.0,
                        help="Pause zwischen Wiederholungen in s (Standard: 10)")
    parser.add_argument("--list", action="store_true",
                        help="Audiogeräte anzeigen und beenden")
    args = parser.parse_args()

    # ── Geräteliste ──────────────────────────────────────────────────
    if args.list:
        from gust_audio import list_audio_devices
        list_audio_devices()
        return 0

    if not args.wav:
        parser.print_help()
        return 1

    # ── WAV laden ────────────────────────────────────────────────────
    wav_path = Path(args.wav)
    if not wav_path.exists():
        print(f"\n  ✗  Datei nicht gefunden: {wav_path}", file=sys.stderr)
        return 1

    try:
        from gust_modulator import load_wav, SAMPLE_RATE
        audio, sr = load_wav(str(wav_path))
        audio = audio.astype(np.float32)
    except Exception as e:
        print(f"\n  ✗  WAV laden fehlgeschlagen: {e}", file=sys.stderr)
        return 1

    duration_s = len(audio) / sr
    print(f"\n  WAV:        {wav_path.name}")
    print(f"  Dauer:      {duration_s:.1f} s")
    print(f"  Sample-Rate:{sr} Hz")
    print(f"  Samples:    {len(audio):,}")

    # ── Konfiguration laden ──────────────────────────────────────────
    try:
        cfg = _load_config(args.config)
    except Exception as e:
        print(f"\n  ✗  gateway.json laden fehlgeschlagen: {e}", file=sys.stderr)
        return 1

    tx_cfg = _get_tx_audio(cfg)
    rig_cfg = cfg.get("rigctld", {})

    # Pegel bestimmen
    raw_level = args.level if args.level is not None else tx_cfg.get("level", 30)
    level = _coerce_level(raw_level)

    ptt_delay_ms = tx_cfg.get("ptt_delay_ms", 250)
    ptt_delay_s  = ptt_delay_ms / 1000.0
    device       = tx_cfg.get("device")
    ptt_backend  = tx_cfg.get("ptt_backend", "null")

    print(f"\n  Konfiguration (aus {args.config}):")
    print(f"  PTT-Backend: {ptt_backend}")
    print(f"  PTT-Delay:   {ptt_delay_ms} ms")
    print(f"  Audio-Gerät: {device if device is not None else 'Standard'}")
    print(f"  TX-Pegel:    {level*100:.0f}%")
    print(f"  Wiederh.:    {args.repeat}×")
    if args.repeat > 1:
        print(f"  Pause:       {args.gap} s")
    if args.dry_run:
        print(f"\n  *** DRY-RUN — kein PTT, kein Audio ***")

    # ── PTT aufbauen ─────────────────────────────────────────────────
    if args.dry_run:
        from gust_audio import NullPTT
        ptt = NullPTT()
    else:
        try:
            from gust_audio import build_ptt
            ptt = build_ptt(tx_cfg, cfg)
        except Exception as e:
            print(f"\n  ✗  PTT-Initialisierung fehlgeschlagen: {e}",
                  file=sys.stderr)
            return 1

    # ── Senden ───────────────────────────────────────────────────────
    print()
    try:
        from gust_audio import AudioTransmitter

        with AudioTransmitter(
            ptt        = ptt,
            device     = device if not args.dry_run else None,
            level      = level,
            ptt_lead_s = ptt_delay_s,
            ptt_tail_s = ptt_delay_s,
        ) as tx:
            for i in range(args.repeat):
                if args.repeat > 1:
                    print(f"  ── Durchgang {i+1}/{args.repeat} ──")

                print(f"  PTT EIN  →  sende {duration_s:.1f}s Audio …")
                t0 = time.monotonic()

                if args.dry_run:
                    # Echte Dauer simulieren
                    time.sleep(ptt_delay_s + duration_s + ptt_delay_s)
                    print(f"  PTT AUS  (DRY-RUN, {time.monotonic()-t0:.1f}s)")
                else:
                    tx.transmit_audio(audio, sample_rate=sr)
                    elapsed = time.monotonic() - t0
                    print(f"  PTT AUS  ({elapsed:.1f}s gesamt)")

                # Pause zwischen Wiederholungen
                if i < args.repeat - 1:
                    print(f"  Warte {args.gap:.0f}s …")
                    time.sleep(args.gap)

        print(f"\n  ✓  {args.repeat} Durchgang/Durchgänge abgeschlossen.\n")
        return 0

    except KeyboardInterrupt:
        print(f"\n  ⚠  Abgebrochen (Strg+C) — PTT wird gelöst …")
        # AudioTransmitter.__exit__ löst PTT im finally-Block
        return 1
    except Exception as e:
        print(f"\n  ✗  TX-Fehler: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())