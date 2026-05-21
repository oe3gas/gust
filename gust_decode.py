#!/usr/bin/env python3
"""
GUST — Standalone Decoder                                  Phase 2/3
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Version : 0.1.0
Datum   : Mai 2026

Verwendung:
  python3 gust_decode.py <datei.wav> <kanal>       # Einzelkanal
  python3 gust_decode.py <datei.wav> --scan        # Alle 10 Kanäle
  python3 gust_decode.py --list-devices            # Audiogeräte anzeigen
  python3 gust_decode.py --help

Beispiele:
  python3 gust_decode.py aufnahme.wav 2
  python3 gust_decode.py aufnahme.wav --scan
  python3 gust_decode.py gust_test_ch2.wav --scan
"""

import sys
import argparse
import os

import numpy as np

from gust_frame import (
    N_CHANNELS, channel_frequency, CHANNEL_BW_HZ,
    FrameType, decode_payload, frame_type_name,
    hexdump,
)
from gust_modulator import (
    receive, load_wav, SAMPLE_RATE,
)


# ───────────────────────────────────────────────────────────────────────
# ANZEIGE-HELPER
# ───────────────────────────────────────────────────────────────────────

def _fmt_result(result: dict, channel: int) -> str:
    """Formatiert ein RX-Ergebnis als lesbaren Multi-Line-String."""
    base = channel_frequency(channel)
    lines = []
    lines.append(f"╔══ Kanal {channel}  ({base:.0f}–{base+CHANNEL_BW_HZ:.0f} Hz) "
                 + "═" * (44 - len(f"Kanal {channel}  ({base:.0f}–{base+CHANNEL_BW_HZ:.0f} Hz)")) + "╗")

    if not result.get("sync_found"):
        lines.append("║  ✗  SYNC nicht gefunden")
        lines.append("╚" + "═" * 55 + "╝")
        return "\n".join(lines)

    crc   = result.get("crc_ok", False)
    from_ = result.get("from", "?")
    typ   = result.get("type", 0)
    tname = result.get("type_name", "?")
    hdr_ch = result.get("channel", "?")
    offset = result.get("freq_offset_hz", 0.0)

    lines.append(f"║  SYNC:    ✓  (Offset {result.get('sync_offset_s', 0):.3f}s)")
    lines.append(f"║  Freq.off:{offset:+.1f} Hz")
    lines.append(f"║  CRC:     {'✓ OK' if crc else '✗ FEHLER'}")
    lines.append(f"║  VON:     {from_}")
    lines.append(f"║  KANAL:   {hdr_ch}  (Header)  /  {channel}  (erkannt)")
    lines.append(f"║  TYP:     0x{typ:02X}  {tname}  ({result.get('data_symbols','?')} Datensymbole)")

    payload = result.get("payload_decoded")
    if isinstance(payload, dict) and "error" not in payload:
        lines.append("║  ── Nutzlast ──────────────────────────────────────────")
        for k, v in payload.items():
            if k in ("flags",):
                continue
            if isinstance(v, float):
                lines.append(f"║    {k:20s} = {v:.4g}")
            elif isinstance(v, bool):
                lines.append(f"║    {k:20s} = {'ja' if v else 'nein'}")
            else:
                lines.append(f"║    {k:20s} = {v}")
    elif "error" in result:
        lines.append(f"║  FEHLER: {result['error']}")

    lines.append("╚" + "═" * 55 + "╝")
    return "\n".join(lines)


def _short_summary(result: dict, channel: int) -> str:
    """Einzeilige Zusammenfassung für Channel-Scan."""
    base = channel_frequency(channel)
    if not result.get("sync_found"):
        return f"  Kanal {channel} ({base:4.0f} Hz):  kein Signal"
    ok     = "✓" if result.get("crc_ok") else "✗"
    typ    = result.get("type_name", "?")
    frm    = result.get("from", "?")
    offset = result.get("freq_offset_hz", 0.0)
    return f"  Kanal {channel} ({base:4.0f} Hz):  {ok}  {frm}  [{typ}]  ({offset:+.1f} Hz)"


# ───────────────────────────────────────────────────────────────────────
# SINGLE-CHANNEL DECODE
# ───────────────────────────────────────────────────────────────────────

def decode_file_channel(wav_path: str, channel: int,
                        verbose: bool = True,
                        freq_offset: float = 0.0) -> dict:
    """Lädt WAV-Datei und decodiert einen Kanal."""
    if not os.path.exists(wav_path):
        print(f"✗  Datei nicht gefunden: {wav_path}", file=sys.stderr)
        sys.exit(1)

    audio, sr = load_wav(wav_path)

    if sr != SAMPLE_RATE:
        print(f"⚠  Warnung: Sample Rate ist {sr} Hz (erwartet {SAMPLE_RATE} Hz)")

    duration = len(audio) / SAMPLE_RATE
    if verbose:
        print(f"\nDatei:     {wav_path}")
        print(f"Dauer:     {duration:.2f}s  ({len(audio)} Samples @ {sr} Hz)")
        print(f"Kanal:     {channel}  ({channel_frequency(channel):.0f}–"
              f"{channel_frequency(channel)+CHANNEL_BW_HZ:.0f} Hz NF)")
        if freq_offset != 0:
            print(f"Offset:    {freq_offset:+.0f} Hz")
        print()

    result = receive(audio, channel=channel, use_fec=True,
                     freq_offset=freq_offset)

    if verbose:
        print(_fmt_result(result, channel))

    return result


# ───────────────────────────────────────────────────────────────────────
# CHANNEL-SCAN (alle 10 Kanäle)
# ───────────────────────────────────────────────────────────────────────

def scan_file(wav_path: str, freq_offset: float = 0.0) -> list:
    """
    Scannt eine WAV-Datei nach GUST-Frames.

    Standardmodus: Breitband-Erkennung (channel=None) — findet Kanal
    und Frequenzversatz automatisch. Kein Vorabwissen über Kanal nötig.

    freq_offset: optionaler manueller Versatz (überschreibt Auto-Erkennung
                 wenn angegeben; für Tests mit bekanntem Offset).
    """
    if not os.path.exists(wav_path):
        print(f"✗  Datei nicht gefunden: {wav_path}", file=sys.stderr)
        sys.exit(1)

    audio, sr = load_wav(wav_path)
    duration  = len(audio) / SAMPLE_RATE

    wideband = (freq_offset == 0.0)

    print(f"\nChannel-Scan")
    print(f"═" * 60)
    print(f"Datei:   {wav_path}")
    print(f"Dauer:   {duration:.2f}s  |  Sample Rate: {sr} Hz")
    if wideband:
        print(f"Modus:   Breitband (automatische Kanal- und Offseterkennung)")
    else:
        print(f"Modus:   Direktscan  |  Offset: {freq_offset:+.0f} Hz")
    print()

    found = []

    if wideband:
        # Breitband: ein einziger receive()-Aufruf mit channel=None
        result = receive(audio, channel=None, use_fec=True)
        if result.get("crc_ok"):
            det_ch = result.get("detected_channel", 0)
            found.append((det_ch, result))
            base = channel_frequency(det_ch)
            offset = result.get("freq_offset_hz", 0.0)
            print(f"  ✓  Kanal {det_ch} ({base:.0f} Hz)  "
                  f"Offset {offset:+.1f} Hz  "
                  f"→ {result.get('from','?')} [{result.get('type_name','?')}]")
        elif result.get("sync_found"):
            print(f"  ✗  SYNC gefunden aber Dekodierung fehlgeschlagen  "
                  f"(Kanal {result.get('detected_channel','?')}, "
                  f"Offset {result.get('freq_offset_hz',0):+.1f} Hz)")
        else:
            print(f"  Kein Signal gefunden.")
            print(f"  Tipp: SDRplay auf exakt 14.110,000 MHz (USB) einstellen.")
    else:
        # Direktscan: alle 10 Kanäle mit festem Offset
        for ch in range(N_CHANNELS):
            result = receive(audio, channel=ch, use_fec=True,
                             freq_offset=freq_offset)
            print(_short_summary(result, ch))
            if result.get("crc_ok"):
                found.append((ch, result))

    print(f"\n{'─'*60}")
    print(f"Ergebnis: {len(found)} Frame(s) dekodiert\n")
    for ch, result in found:
        print(_fmt_result(result, ch))

    return found


def autoscan_file(wav_path: str) -> list:
    """
    Automatische Frequenzsuche: probiert Offsets von −250 bis +250 Hz
    in 31-Hz-Schritten (= 1 Tonabstand). Nützlich wenn TX- und RX-
    Kalibrierung leicht abweichen.
    """
    if not os.path.exists(wav_path):
        print(f"✗  Datei nicht gefunden: {wav_path}", file=sys.stderr)
        sys.exit(1)

    audio, sr = load_wav(wav_path)
    duration  = len(audio) / SAMPLE_RATE

    print(f"\nAuto-Scan (Frequenzsuche ±250 Hz)")
    print(f"═" * 60)
    print(f"Datei:   {wav_path}")
    print(f"Dauer:   {duration:.2f}s  |  Sample Rate: {sr} Hz")
    print(f"Suche:   Offset −250…+250 Hz, Schritt 10 Hz\n")

    best = []
    for offset in range(-250, 251, 10):
        for ch in range(N_CHANNELS):
            result = receive(audio, channel=ch, use_fec=True,
                             freq_offset=float(offset))
            if result.get("crc_ok"):
                best.append((ch, offset, result))
                print(f"  ✓  Kanal {ch}  Offset {offset:+d} Hz  "
                      f"→ {result.get('from','?')} [{result.get('type_name','?')}]")

    print(f"\n{'─'*60}")
    if not best:
        print(f"  Kein Frame gefunden.")
        print(f"  Tipp: Prüfe ob SDRplay auf exakt 14.110,000 MHz (USB) stand.")
        print(f"        Mit --offset <Hz> manuell korrigieren (z.B. --offset -100).")
    else:
        print(f"  {len(best)} Frame(s) gefunden.")
        print(f"  → Für künftige Aufnahmen empfohlen: --offset {best[0][1]:+d}")

    return best


# ───────────────────────────────────────────────────────────────────────
# ARGPARSE / CLI
# ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="gust_decode.py",
        description="GUST MFSK-8 Decoder — WAV-Datei → Frame-Inhalt",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python3 gust_decode.py aufnahme.wav 2
      → Kanal 2 (900–1150 Hz) dekodieren

  python3 gust_decode.py aufnahme.wav --scan
      → Alle 10 Kanäle durchsuchen

  python3 gust_decode.py gust_test_ch2.wav --scan
      → Testdatei aus gust_modulator.py dekodieren

Kanalplan (10 Kanäle × 250 Hz = 2,5 kHz):
  Kanal 0:   400– 650 Hz    Kanal 5: 1650–1900 Hz
  Kanal 1:   650– 900 Hz    Kanal 6: 1900–2150 Hz
  Kanal 2:   900–1150 Hz    Kanal 7: 2150–2400 Hz
  Kanal 3:  1150–1400 Hz    Kanal 8: 2400–2650 Hz
  Kanal 4:  1400–1650 Hz    Kanal 9: 2650–2900 Hz
""",
    )

    parser.add_argument(
        "wav_file",
        nargs="?",
        help="WAV-Datei (8000 Hz Mono, 16-Bit PCM)",
    )
    parser.add_argument(
        "channel",
        nargs="?",
        type=int,
        help="Kanal 0–9 (optional, ohne: --scan verwenden)",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Alle 10 Kanäle nach Frames durchsuchen",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Verfügbare Audiogeräte auflisten (für Phase 3 Echtzeit-RX)",
    )
    parser.add_argument(
        "--no-fec",
        action="store_true",
        help="Reed-Solomon FEC deaktivieren (Debugging)",
    )
    parser.add_argument(
        "--offset", type=float, default=0.0, metavar="HZ",
        help="Frequenzversatz in Hz (z.B. --offset -100). "
             "Kompensiert Kalibrierfehler zwischen TX und RX.",
    )
    parser.add_argument(
        "--autoscan", action="store_true",
        help="Automatische Frequenzsuche ±250 Hz (findet Offset automatisch)",
    )

    args = parser.parse_args()

    # ── Geräteliste ───────────────────────────────────────────────
    if args.list_devices:
        try:
            import sounddevice as sd
            print("\nVerfügbare Audiogeräte:")
            print("─" * 60)
            print(sd.query_devices())
            print(f"\nStandard-Eingang:  {sd.default.device[0]}")
            print(f"Standard-Ausgang:  {sd.default.device[1]}")
        except ImportError:
            print("sounddevice nicht installiert. Installation: pip install sounddevice")
        return

    # ── WAV-Datei erforderlich ─────────────────────────────────────
    if not args.wav_file:
        parser.print_help()
        sys.exit(0)

    # ── Channel-Scan ──────────────────────────────────────────────
    if args.autoscan:
        autoscan_file(args.wav_file)
        return

    if args.scan or args.channel is None:
        scan_file(args.wav_file, freq_offset=args.offset)
        return

    # ── Einzelkanal ───────────────────────────────────────────────
    if not (0 <= args.channel <= N_CHANNELS - 1):
        print(f"✗  Kanal muss zwischen 0 und {N_CHANNELS - 1} liegen", file=sys.stderr)
        sys.exit(1)

    decode_file_channel(args.wav_file, args.channel,
                        verbose=True, freq_offset=args.offset)


if __name__ == "__main__":
    main()