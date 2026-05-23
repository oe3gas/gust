#!/usr/bin/env python3
"""
GUST — Audio-Quellen Diagnose
══════════════════════════════════════════════════════════════
Testet alle verfügbaren Audio-Eingänge und zeigt welche
davon tatsächlich ein Signal liefern.

Ideal für die Diagnose der VAC/Audio-Repeater-Kette:
  Browser → Audio Repeater → Line 1 (VAC) → gust_rx

Verwendung:
    python gust_audio_diag.py            # alle Geräte testen
    python gust_audio_diag.py --device 3  # nur Gerät #3 testen
    python gust_audio_diag.py --seconds 5 # 5s aufnehmen (default: 3s)
    python gust_audio_diag.py --list       # nur auflisten, nicht testen
"""

import sys
import time
import argparse
import threading
import numpy as np

try:
    import sounddevice as sd
except ImportError:
    print("FEHLER: sounddevice nicht installiert.")
    print("  pip install sounddevice")
    sys.exit(1)


GUST_SAMPLE_RATE = 8000   # GUST arbeitet mit 8 kHz


# ───────────────────────────────────────────────────────────────────────
# Gerät-Liste
# ───────────────────────────────────────────────────────────────────────

def list_devices(filter_input=True):
    """Alle Audio-Geräte auflisten."""
    devs      = sd.query_devices()
    hostapis  = sd.query_hostapis()
    default_in, default_out = sd.default.device

    def api_name(idx):
        if 0 <= idx < len(hostapis):
            return hostapis[idx].get("name", f"API{idx}")
        return f"API{idx}"

    print()
    print("═" * 72)
    print("  VERFÜGBARE AUDIO-GERÄTE")
    print("═" * 72)
    print(f"  {'ID':>3}  {'Name':<36}  {'CH':>3}  {'kHz':>6}  API")
    print("─" * 72)

    input_ids = []
    for i, d in enumerate(devs):
        ch_in  = int(d.get("max_input_channels",  0))
        ch_out = int(d.get("max_output_channels", 0))
        sr     = float(d.get("default_samplerate", 0)) / 1000
        api    = api_name(int(d.get("hostapi", -1)))
        name   = str(d.get("name", "?")).strip()
        marker = ""
        if i == default_in:  marker += " ◄IN"
        if i == default_out: marker += " ◄OUT"

        if filter_input and ch_in == 0:
            continue

        flag = f"  {'*' if ch_in > 0 else ' '}"
        print(f"{flag} {i:>3}  {name:<36}  {ch_in:>3}  {sr:>6.1f}  {api}{marker}")
        if ch_in > 0:
            input_ids.append(i)

    print("─" * 72)
    print("  * = Eingang verfügbar")
    print()
    return input_ids


# ───────────────────────────────────────────────────────────────────────
# Einzel-Gerät testen
# ───────────────────────────────────────────────────────────────────────

def test_device(device_id, seconds=3.0, target_sr=GUST_SAMPLE_RATE):
    """
    Nimmt `seconds` Sekunden vom Gerät auf, berechnet Pegel und
    gibt ein Ergebnis-Dict zurück.
    """
    devs = sd.query_devices()
    if device_id >= len(devs):
        return {"ok": False, "error": f"Gerät {device_id} existiert nicht"}

    d     = devs[device_id]
    name  = str(d.get("name", "?")).strip()
    ch_in = int(d.get("max_input_channels", 0))
    native_sr = int(d.get("default_samplerate", 48000))

    if ch_in == 0:
        return {"ok": False, "device_id": device_id, "name": name,
                "error": "kein Eingang (max_input_channels=0)"}

    # Aufnahme-Puffer
    frames = []
    lock   = threading.Event()

    def callback(indata, frame_count, time_info, status):
        frames.append(indata[:, 0].copy() if indata.ndim > 1 else indata.ravel().copy())

    try:
        with sd.InputStream(
            samplerate = native_sr,
            channels   = min(ch_in, 2),
            dtype      = "float32",
            device     = device_id,
            callback   = callback,
            blocksize  = 1024,
        ):
            print(f"  Aufnahme {seconds:.0f}s von Gerät {device_id} "
                  f"({name[:32]}) @ {native_sr} Hz ...", end="", flush=True)
            time.sleep(seconds)

    except Exception as e:
        print(f"  FEHLER")
        return {"ok": False, "device_id": device_id, "name": name,
                "error": str(e)}

    if not frames:
        print(f"  KEINE DATEN")
        return {"ok": False, "device_id": device_id, "name": name,
                "error": "keine Daten empfangen"}

    audio = np.concatenate(frames)

    # Resampling auf 8 kHz (für GUST-Kompatibilitäts-Check)
    try:
        from scipy.signal import resample_poly
        from math import gcd
        g   = gcd(target_sr, native_sr)
        up  = target_sr // g
        dn  = native_sr // g
        audio_8k = resample_poly(audio, up, dn)
    except ImportError:
        audio_8k = audio   # scipy nicht installiert — kein Resampling

    # Pegel berechnen
    rms     = float(np.sqrt(np.mean(audio**2)))
    peak    = float(np.max(np.abs(audio)))
    rms_db  = 20 * np.log10(rms  + 1e-10)
    peak_db = 20 * np.log10(peak + 1e-10)

    # Stille-Erkennung: < -60 dBFS = praktisch kein Signal
    has_signal = rms_db > -60.0

    # Pegelbalken (20 Zeichen)
    bar_len = max(0, min(20, int((rms_db + 80) / 4)))
    bar     = "█" * bar_len + "░" * (20 - bar_len)

    status  = "✓ SIGNAL" if has_signal else "– STILLE"
    print(f"  {status}  RMS {rms_db:+6.1f} dBFS  Peak {peak_db:+6.1f} dBFS  [{bar}]")

    return {
        "ok":         True,
        "device_id":  device_id,
        "name":       name,
        "native_sr":  native_sr,
        "rms_db":     rms_db,
        "peak_db":    peak_db,
        "has_signal": has_signal,
        "samples":    len(audio),
        "samples_8k": len(audio_8k),
    }


# ───────────────────────────────────────────────────────────────────────
# VAC-Kette gezielt testen
# ───────────────────────────────────────────────────────────────────────

def test_vac_chain(seconds=3.0):
    """
    Sucht gezielt nach VAC-Geräten (Virtual Audio Cable / Line)
    und testet diese.
    """
    devs = sd.query_devices()
    vac_keywords = ["virtual audio cable", "line 1", "cable output",
                    "vac", "voicemeeter"]

    print()
    print("═" * 72)
    print("  VAC-KETTEN-TEST (Virtual Audio Cable / Line 1)")
    print("═" * 72)
    print("  Stelle sicher dass:")
    print("  1. KiwiSDR im Browser läuft und Audio hörbar ist")
    print("  2. Audio Repeater läuft (Wave In → Line 1 VAC)")
    print()

    vac_ids = []
    for i, d in enumerate(devs):
        name  = str(d.get("name", "")).lower()
        ch_in = int(d.get("max_input_channels", 0))
        if ch_in > 0 and any(kw in name for kw in vac_keywords):
            vac_ids.append(i)

    if not vac_ids:
        print("  ⚠ Keine VAC-Geräte gefunden (nach Name).")
        print("  Verwende --device <ID> um ein Gerät manuell zu testen.")
        return []

    results = []
    for dev_id in vac_ids:
        r = test_device(dev_id, seconds=seconds)
        results.append(r)

    return results


# ───────────────────────────────────────────────────────────────────────
# Alle Eingänge testen
# ───────────────────────────────────────────────────────────────────────

def test_all_inputs(seconds=3.0, skip_no_signal=False):
    """Alle Input-Geräte der Reihe nach testen."""
    input_ids = list_devices(filter_input=True)

    print("═" * 72)
    print(f"  TESTE ALLE {len(input_ids)} EINGÄNGE  ({seconds:.0f}s pro Gerät)")
    print("═" * 72)
    print("  → KiwiSDR im Browser starten, Audio Repeater aktiv lassen")
    print()

    results = []
    for dev_id in input_ids:
        r = test_device(dev_id, seconds=seconds)
        results.append(r)

    # Zusammenfassung
    print()
    print("═" * 72)
    print("  ZUSAMMENFASSUNG")
    print("═" * 72)
    with_signal = [r for r in results if r.get("has_signal")]
    silent      = [r for r in results if r.get("ok") and not r.get("has_signal")]
    errors      = [r for r in results if not r.get("ok")]

    if with_signal:
        print(f"  Geräte MIT Signal ({len(with_signal)}):")
        for r in sorted(with_signal, key=lambda x: x["rms_db"], reverse=True):
            print(f"    [{r['device_id']:>3}]  {r['name']:<36}  "
                  f"RMS {r['rms_db']:+6.1f} dBFS")
        print()
        best = with_signal[0]
        print(f"  → Empfehlung für gateway.json:")
        print(f'       "rx": {{"device": {best["device_id"]}}}')
        print(f'       # {best["name"]}')
    else:
        print("  ⚠ KEIN Gerät liefert ein Signal!")
        print("  Prüfe:")
        print("    1. Läuft KiwiSDR im Browser und ist Audio hörbar?")
        print("    2. Läuft Audio Repeater (grüne Balken)?")
        print("    3. Ist 'Wave Out' im Audio Repeater = 'Line 1 (VAC)'?")

    if silent:
        print(f"\n  Stille Geräte ({len(silent)}): "
              + ", ".join(f"{r['device_id']}={r['name'][:20]}" for r in silent))
    if errors:
        print(f"\n  Fehler ({len(errors)}): "
              + ", ".join(f"{r['device_id']}={r.get('error','?')[:30]}" for r in errors))

    print()
    return results


# ───────────────────────────────────────────────────────────────────────
# GUST-Kompatibilitäts-Check
# ───────────────────────────────────────────────────────────────────────

def check_gust_compat(device_id, seconds=6.0):
    """
    Simuliert exakt wie gust_rx.py das Gerät verwenden würde:
    - Aufnahme mit sounddevice InputStream
    - Resampling auf 8000 Hz
    - get_snapshot() Simulation
    """
    print()
    print("═" * 72)
    print(f"  GUST-KOMPATIBILITÄTS-CHECK  Gerät {device_id}")
    print("═" * 72)

    r = test_device(device_id, seconds=seconds)
    if not r.get("ok"):
        print(f"  FEHLER: {r.get('error')}")
        return

    print()
    print(f"  Gerät:        {r['name']}")
    print(f"  Native SR:    {r['native_sr']} Hz")
    print(f"  GUST SR:      {GUST_SAMPLE_RATE} Hz")
    resampling = r['native_sr'] != GUST_SAMPLE_RATE
    print(f"  Resampling:   {'JA (' + str(r['native_sr']) + ' → ' + str(GUST_SAMPLE_RATE) + ' Hz)' if resampling else 'NEIN (native = 8000 Hz)'}")
    print(f"  Samples (nat):{r['samples']:>8}")
    print(f"  Samples (8k): {r['samples_8k']:>8}")
    print(f"  Signal:       {'✓ JA' if r['has_signal'] else '✗ NEIN (Stille)'}")
    print(f"  RMS:          {r['rms_db']:+.1f} dBFS")
    print(f"  Peak:         {r['peak_db']:+.1f} dBFS")

    if r["has_signal"]:
        print()
        print("  ✓ Gerät ist GUST-kompatibel und liefert Audio!")
        print()
        print(f"  → In gateway.json eintragen:")
        print(f'    "rx": {{"device": {device_id}}}')
    else:
        print()
        print("  ⚠ Gerät liefert kein Signal — Audio Repeater läuft?")

    print()


# ───────────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="gust_audio_diag.py",
        description="GUST Audio-Quellen Diagnose — findet welches Gerät "
                    "den KiwiSDR/Browser-Sound über VAC liefert",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python gust_audio_diag.py              → alle Geräte testen (3s je)
  python gust_audio_diag.py --list       → nur auflisten
  python gust_audio_diag.py --vac        → nur VAC/Line-Geräte testen
  python gust_audio_diag.py --device 5   → Gerät 5 GUST-kompatibel prüfen
  python gust_audio_diag.py --seconds 5  → 5s Aufnahme (default: 3s)

Typische Kette:
  KiwiSDR Browser → Windows Audio → Audio Repeater
  → Line 1 (Virtual Audio Cable) → gust_rx.py
""",
    )
    parser.add_argument("--list",    action="store_true",
                        help="Nur Geräte auflisten, nicht testen")
    parser.add_argument("--vac",     action="store_true",
                        help="Nur VAC/Line-Geräte testen")
    parser.add_argument("--device",  type=int, default=None,
                        help="Einzelnes Gerät testen (GUST-Compat-Check)")
    parser.add_argument("--seconds", type=float, default=3.0,
                        help="Aufnahmedauer pro Gerät in Sekunden (default: 3)")
    args = parser.parse_args()

    print()
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║  GUST Audio-Diagnose                                OE3GAS       ║")
    print("╚══════════════════════════════════════════════════════════════════╝")

    if args.list:
        list_devices()
        return

    if args.device is not None:
        check_gust_compat(args.device, seconds=args.seconds)
        return

    if args.vac:
        test_vac_chain(seconds=args.seconds)
        return

    # Default: alle testen
    test_all_inputs(seconds=args.seconds)


if __name__ == "__main__":
    main()