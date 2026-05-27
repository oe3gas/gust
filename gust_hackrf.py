#!/usr/bin/env python3
"""
GUST — HackRF / SoapySDR TX-Pfad                          Phase 3
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Version : 1.0.0
Datum   : Mai 2026

Dieses Modul implementiert den direkten RF-Sendepfad über SoapySDR.
Unterstützte Hardware:
  • HackRF One           → SoapySDR-Treiber: "hackrf"
  • ICOM IC-7610         → Soapy7610 (OE3GAS eigene Entwicklung)
  • SDRplay RSPdx2       → SoapySDR-Treiber: "sdrplay" (nur RX)
  • Alle SoapySDR-fähigen Geräte

── Voraussetzungen ────────────────────────────────────────────────────
  apt install python3-soapysdr libsoapysdr-dev soapysdr-module-hackrf
  oder: conda install -c conda-forge soapysdr

  Installiert prüfen:
    python3 -c "import SoapySDR; print(SoapySDR.getAPIVersion())"
    SoapySDRUtil --probe=driver=hackrf    # HackRF erkennen
    SoapySDRUtil --probe=driver=IC7610 # IC-7610 erkennen

── Labortest-Szenario (Phase 3) ──────────────────────────────────────
  HackRF TX → Koaxialkabel (Dämpfungsglied) → SDRplay / IC-7610 RX
  → Empfangen und in GQRX/SDR# ansehen
  → Parallel WAV-Datei aufnehmen und mit gust_decode.py dekodieren

── Sicherheitshinweis ────────────────────────────────────────────────
  Das HackRF sendet direkt auf der eingestellten HF-Frequenz.
  • Nur mit angeschlossener Antenne und Abschluss arbeiten!
  • Kein Senden auf Amateurfunk-Bänder ohne gültige Lizenz!
  • Für Labortests: Dummy Load oder 30+ dB Dämpfungsglied verwenden.
  • TX-Leistung HackRF: 1–10 mW typisch — kein Vergleich zum IC-7610.

── Frequenzarchitektur ────────────────────────────────────────────────
  Das GUST-Signal ist ein NF-Signal (400–2900 Hz).
  Für HF-Übertragung: NF auf eine Trägerfrequenz aufmodulieren.

  Option A — NF über Soundkarte an IC-7610 (empfohlen für On-Air):
    IC-7610 nimmt das NF-Signal und setzt es in SSB-DSB um.
    → Keine direkten HF-Kenntnisse in Python nötig.
    → Verwende gust_audio.py + HamlibPTT.

  Option B — IQ direkt via HackRF (Labortest):
    Python erzeugt das SSB-IQ-Signal selbst.
    → Mehr Kontrolle, Labortest ohne IC-7610.
    → HackRF als IF-Transceiver oder direkt auf HF.

  Dieses Modul implementiert Option B.

── SSB-Modulation in Python ──────────────────────────────────────────
  USB (Upper Sideband) SSB-IQ-Erzeugung via Hilbert-Transform:
    analytic = hilbert(audio_nf)           # analytisches NF-Signal
    iq = analytic * exp(j*2*pi*f_nf*t)    # auf NF zentriert → Basisband IQ
  → HackRF sendet dieses Basisband IQ-Signal direkt auf der gewählten
    HF-Frequenz (z.B. 14.110 MHz für 40m/20m-Band Test)
"""

import numpy as np
import sys
import time

# SoapySDR (optional — nur wenn installiert)
try:
    import SoapySDR
    from SoapySDR import SOAPY_SDR_TX, SOAPY_SDR_CF32
    _SOAPY_AVAILABLE = True
except ImportError:
    _SOAPY_AVAILABLE = False

from gust_modulator import transmit, SAMPLE_RATE
from gust_frame import (
    FrameType, encode_weather, assign_channel,
    channel_frequency, CHANNEL_BW_HZ,
)


# ═══════════════════════════════════════════════════════════════════════
# KONSTANTEN
# ═══════════════════════════════════════════════════════════════════════

# Standard-Labortest-Frequenz (20m-Band, GUST Pilotfrequenz)
LAB_TX_FREQ_HZ      = 14_110_000.0    # 14.110 MHz USB Dial

# HackRF TX-Parameter
HACKRF_TX_GAIN_DB    = 14     # VGA Gain (0–47 dB, 7 dB Schritte)
HACKRF_TX_AMP_ENABLE = False  # Interner 14 dB Verstärker (VORSICHT!)
HACKRF_SAMPLE_RATE   = 2_000_000  # 2 MHz (HackRF Minimum = 2 MHz)

# Soapy7610 TX-Parameter (IC-7610)
SOAPY7610_SAMPLE_RATE = 192_000   # 192 kHz (IC-7610 native)


# ═══════════════════════════════════════════════════════════════════════
# NF → IQ KONVERTIERUNG (SSB-Modulation)
# ═══════════════════════════════════════════════════════════════════════

def nf_to_iq_usb(audio_nf: np.ndarray, output_sample_rate: int) -> np.ndarray:
    """
    Konvertiert GUST NF-Audiosignal in USB SSB Basisband-IQ.

    Das resultierende IQ-Signal ist zentriert bei 0 Hz (Basisband).
    Der HackRF verschiebt es auf die gewählte HF-Frequenz.

    Mathematik (USB = Upper Sideband):
        1. Audiosignal aufabtasten auf output_sample_rate
        2. Analytisches Signal via Hilbert-Transformation
        3. Ergebnis ist IQ-Basisband (positive Frequenzen = USB)

    Args:
        audio_nf:           NF-Audio von mfsk8_modulate() (8000 Hz)
        output_sample_rate: Ziel-Samplerate für den SDR

    Returns:
        Complex64 IQ-Samples für SoapySDR

    Beispiel:
        audio, _, _ = transmit(FrameType.WEATHER, 'OE3GAS', payload)
        iq = nf_to_iq_usb(audio, HACKRF_SAMPLE_RATE)
        # → iq ist ein Complex64-Array mit 2 Mio Samples/s
        # → Enthält MFSK-8 Signal als USB-moduliertes IQ
    """
    from scipy.signal import resample_poly, hilbert
    from math import gcd

    # ── 1. Aufabtasten: 8 kHz → output_sample_rate ────────────────
    g   = gcd(SAMPLE_RATE, output_sample_rate)
    up  = output_sample_rate // g
    dn  = SAMPLE_RATE // g
    resampled = resample_poly(audio_nf.astype(np.float64), up, dn)

    # ── 2. Hilbert-Transformation → analytisches Signal ───────────
    analytic = hilbert(resampled)

    # ── 3. Normalisieren und als Complex64 zurückgeben ─────────────
    peak = np.max(np.abs(analytic))
    if peak > 0:
        analytic = analytic / peak * 0.7   # 70% — etwas Headroom

    return analytic.astype(np.complex64)


# ═══════════════════════════════════════════════════════════════════════
# HACKRF TX
# ═══════════════════════════════════════════════════════════════════════

class HackRFTransmitter:
    """
    GUST TX via HackRF One + SoapySDR.

    Sendet das MFSK-8-Signal direkt als SSB-IQ auf einer HF-Frequenz.
    Für Labortests: Dummy Load oder Dämpfungsglied an die HackRF-Antenne!

    Args:
        freq_hz:    TX-Mittenfrequenz in Hz (z.B. 14_110_000)
        gain_db:    VGA Gain 0–47 dB (Standard: 14 dB = moderater Pegel)
        amp_enable: Internen 14 dB PA einschalten (VORSICHT: hohe Leistung)
        device_args: SoapySDR Geräteargumente (z.B. "serial=0000...")
    """

    def __init__(
        self,
        freq_hz:     float = LAB_TX_FREQ_HZ,
        gain_db:     int   = HACKRF_TX_GAIN_DB,
        amp_enable:  bool  = HACKRF_TX_AMP_ENABLE,
        device_args: str   = "driver=hackrf",
    ):
        if not _SOAPY_AVAILABLE:
            raise RuntimeError(
                "SoapySDR nicht installiert.\n"
                "Installation: apt install python3-soapysdr soapysdr-module-hackrf\n"
                "Prüfen: python3 -c \"import SoapySDR; print('OK')\""
            )
        self.freq_hz     = freq_hz
        self.gain_db     = gain_db
        self.amp_enable  = amp_enable
        self.device_args = device_args
        self._sdr        = None

    def open(self):
        """HackRF initialisieren und TX konfigurieren."""
        print(f"[HackRF] Initialisiere Gerät: {self.device_args}")
        self._sdr = SoapySDR.Device(dict(driver="hackrf"))

        self._sdr.setSampleRate(SOAPY_SDR_TX, 0, float(HACKRF_SAMPLE_RATE))
        self._sdr.setFrequency(SOAPY_SDR_TX, 0, self.freq_hz)
        self._sdr.setGain(SOAPY_SDR_TX, 0, "VGA", float(self.gain_db))
        if self.amp_enable:
            self._sdr.setGain(SOAPY_SDR_TX, 0, "AMP", 14.0)
            print("[HackRF] ⚠  Interner 14 dB Verstärker AKTIV — Leistung hoch!")

        freq_mhz = self.freq_hz / 1e6
        print(f"[HackRF] Frequenz:    {freq_mhz:.3f} MHz")
        print(f"[HackRF] Sample Rate: {HACKRF_SAMPLE_RATE/1e6:.1f} MSps")
        print(f"[HackRF] VGA Gain:    {self.gain_db} dB")

    def transmit(self, audio_nf: np.ndarray) -> None:
        """
        NF-Signal als SSB-IQ via HackRF senden.

        Args:
            audio_nf: GUST NF-Audio (8000 Hz float32)
        """
        if self._sdr is None:
            self.open()

        # NF → IQ
        iq = nf_to_iq_usb(audio_nf, HACKRF_SAMPLE_RATE)
        duration = len(audio_nf) / SAMPLE_RATE

        print(f"[HackRF] TX: {duration:.2f}s NF → "
              f"{len(iq)} IQ-Samples @ {HACKRF_SAMPLE_RATE/1e6:.1f} MSps")

        tx_stream = self._sdr.setupStream(SOAPY_SDR_TX, SOAPY_SDR_CF32)
        self._sdr.activateStream(tx_stream)

        BLOCK = 4096
        pos   = 0
        while pos < len(iq):
            block = iq[pos:pos + BLOCK]
            sr    = self._sdr.writeStream(tx_stream, [block], len(block))
            pos  += sr.ret if sr.ret > 0 else BLOCK

        self._sdr.deactivateStream(tx_stream)
        self._sdr.closeStream(tx_stream)
        print("[HackRF] TX abgeschlossen")

    def transmit_iq(self, iq: np.ndarray) -> None:
        """
        Sendet ein vorgefertigtes IQ-Array direkt (überspringt nf_to_iq_usb).
        Verwendet dasselbe Device-Handle und EXAKT dieselbe Write-Loop wie
        transmit() — Default-Timeout, kein langer Timeout (sonst TX-Underrun!).

        Args:
            iq: Complex64-Array (bereits auf 2 MSps skaliert, Peak ≤ 0.9)
        """
        if self._sdr is None:
            self.open()

        # Null-Padding anhängen: leert den HackRF-Puffer sauber am Ende
        flush_samples = int(HACKRF_SAMPLE_RATE * 0.1)
        iq = np.concatenate([
            iq.astype(np.complex64),
            np.zeros(flush_samples, dtype=np.complex64),
        ])

        duration = len(iq) / HACKRF_SAMPLE_RATE
        print(f"[HackRF] TX IQ: {duration:.2f}s  "
              f"{len(iq)} Samples @ {HACKRF_SAMPLE_RATE/1e6:.1f} MSps", flush=True)

        tx_stream = self._sdr.setupStream(SOAPY_SDR_TX, SOAPY_SDR_CF32)
        self._sdr.activateStream(tx_stream)

        # EXAKT wie transmit() — Default-Timeout, pos += BLOCK bei ret<=0
        BLOCK = 4096
        pos   = 0
        while pos < len(iq):
            block = iq[pos:pos + BLOCK]
            sr    = self._sdr.writeStream(tx_stream, [block], len(block))
            pos  += sr.ret if sr.ret > 0 else BLOCK

        self._sdr.deactivateStream(tx_stream)
        self._sdr.closeStream(tx_stream)
        print("[HackRF] TX IQ abgeschlossen", flush=True)

    def transmit_frame(
        self,
        frame_type: int,
        callsign:   str,
        payload:    bytes,
        channel:    int  = None,
        window:     bool = True,
    ) -> int:
        """GUST Frame erzeugen und via HackRF senden."""
        audio, used_channel, frame_body = transmit(
            frame_type, callsign, payload,
            channel=channel, use_fec=True, window=window, add_silence_ms=100
        )
        base = channel_frequency(used_channel)
        print(f"[HackRF] Frame: {len(frame_body)} Byte  |  "
              f"Kanal {used_channel} ({base:.0f} Hz NF)  |  "
              f"HF: {self.freq_hz/1e6:.3f} MHz USB")
        self.transmit(audio)
        return used_channel

    def close(self):
        if self._sdr:
            del self._sdr
            self._sdr = None
            print("[HackRF] Gerät geschlossen")

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()


# ═══════════════════════════════════════════════════════════════════════
# SOAPY7610 TX (IC-7610)
# ═══════════════════════════════════════════════════════════════════════

class Soapy7610Transmitter:
    """
    GUST TX via Soapy7610 (ICOM IC-7610 USB-Audio + CAT).

    Soapy7610 ist OE3GAS eigene Entwicklung: es macht den IC-7610
    als SoapySDR-Gerät verfügbar. Die USB-Soundkarte des IC-7610
    liefert IQ-Streams mit bis zu 192 kHz Bandbreite.

    Installationsvoraussetzungen:
        - IC7610 compiliert und in SoapySDR-Modulpfad installiert
        - IC-7610 per USB verbunden, CI-V aktiviert
        - Verify: SoapySDRUtil --probe=driver=IC7610

    Dieser TX-Pfad umgeht die Soundkarte und steuert den IC-7610
    direkt als SDR. Vorteil: volle 100W Ausgangsleistung des IC-7610.

    Wichtig: IC-7610 muss auf USB-D Modus gestellt sein (nicht USB),
    damit das externe Audio-Signal unverstärkt auf Sendung geht.
    """

    def __init__(
        self,
        freq_hz:     float = LAB_TX_FREQ_HZ,
        device_args: str   = "driver=IC7610",
    ):
        if not _SOAPY_AVAILABLE:
            raise RuntimeError("SoapySDR nicht installiert.")
        self.freq_hz     = freq_hz
        self.device_args = device_args
        self._sdr        = None

    def open(self):
        """IC-7610 via Soapy7610 initialisieren."""
        print(f"[Soapy7610] Initialisiere IC-7610...")
        self._sdr = SoapySDR.Device({"driver": "IC7610"})

        self._sdr.setSampleRate(SOAPY_SDR_TX, 0, float(SOAPY7610_SAMPLE_RATE))
        self._sdr.setFrequency(SOAPY_SDR_TX, 0, self.freq_hz)

        print(f"[Soapy7610] Frequenz:    {self.freq_hz/1e6:.3f} MHz")
        print(f"[Soapy7610] Sample Rate: {SOAPY7610_SAMPLE_RATE/1e3:.0f} kSps")
        print(f"[Soapy7610] Modus:       USB-D (Digital) — muss am IC-7610 gesetzt sein")

    def transmit(self, audio_nf: np.ndarray) -> None:
        """NF-Signal als USB-IQ via IC-7610 senden."""
        if self._sdr is None:
            self.open()

        iq = nf_to_iq_usb(audio_nf, SOAPY7610_SAMPLE_RATE)
        duration = len(audio_nf) / SAMPLE_RATE

        print(f"[Soapy7610] TX: {duration:.2f}s → {len(iq)} IQ-Samples")

        tx_stream = self._sdr.setupStream(SOAPY_SDR_TX, SOAPY_SDR_CF32)
        self._sdr.activateStream(tx_stream)

        BLOCK = 1024
        pos   = 0
        while pos < len(iq):
            block = iq[pos:pos + BLOCK]
            sr    = self._sdr.writeStream(tx_stream, [block], len(block))
            pos  += sr.ret if sr.ret > 0 else BLOCK

        self._sdr.deactivateStream(tx_stream)
        self._sdr.closeStream(tx_stream)
        print("[Soapy7610] TX abgeschlossen")

    def close(self):
        if self._sdr:
            del self._sdr
            self._sdr = None


# ═══════════════════════════════════════════════════════════════════════
# LABORTEST-SKRIPT
# ═══════════════════════════════════════════════════════════════════════

def run_labortest(
    freq_hz:     float = LAB_TX_FREQ_HZ,
    callsign:    str   = "OE3GAS",
    channel:     int   = None,
    use_hackrf:  bool  = True,
    save_cf32:   bool  = True,
):
    """
    Phase 3 Labortest: GUST Frame via HackRF → SDRplay/IC-7610 RX.

    Testaufbau:
        PC/RPi → HackRF One → Dämpfungsglied 30+ dB → SDRplay RSPdx2
                                                      oder IC-7610 RX

    Ablauf:
        1. Wetter-Frame für OE3GAS erzeugen
        2. Als USB-SSB-IQ-Signal konvertieren
        3. Über HackRF senden
        4. Optional: WAV und CF32 der NF speichern (für gust_decode.py)

    Empfang verifizieren:
        - SDRplay/GQRX: Wasserfall bei 14.110 MHz USB beobachten
        - MFSK-Kammstruktur bei NF 900–1150 Hz sichtbar?
        - WAV aufnehmen und mit: python3 gust_decode.py aufnahme.wav --scan
    """
    from gust_modulator import save_wav, save_cf32 as _save_cf32

    print("=" * 60)
    print("  GUST Phase 3 — Labortest")
    print("=" * 60)

    # Frame erzeugen
    payload = encode_weather(
        temp_c=21.5, humidity_pct=68, pressure_hpa=1013.2,
        wind_kmh=15, wind_deg=270, rain_mm_h=0.2, uv_index=3
    )
    audio, used_ch, frame_body = transmit(
        FrameType.WEATHER, callsign, payload,
        channel=channel, use_fec=True, window=True, add_silence_ms=100
    )
    base = channel_frequency(used_ch)

    print(f"\nFrame-Info:")
    print(f"  Rufzeichen:  {callsign}")
    print(f"  Frame-Body:  {len(frame_body)} Byte")
    print(f"  NF-Kanal:    {used_ch}  ({base:.0f}–{base+CHANNEL_BW_HZ:.0f} Hz)")
    print(f"  HF-Frequenz: {freq_hz/1e6:.3f} MHz (USB Dial)")
    print(f"  RF-Signal:   {freq_hz/1e6 + base/1e6:.6f}–{freq_hz/1e6 + (base+CHANNEL_BW_HZ)/1e6:.6f} MHz")
    print(f"  Audiodauer:  {len(audio)/SAMPLE_RATE:.2f}s")

    # WAV und CF32 für Verifikation speichern
    if save_cf32:
        nf_wav  = f"gust_lab_ch{used_ch}_nf.wav"
        nf_cf32 = f"gust_lab_ch{used_ch}_nf.cf32"
        save_wav(nf_wav, audio)
        _save_cf32(nf_cf32, audio)
        print(f"\nNF-Dateien gespeichert:")
        print(f"  {nf_wav}   → Audacity: NF-Spektrum prüfen")
        print(f"  {nf_cf32}  → inspectrum (Sample Rate 8000 Hz)")
        print(f"\nDekodieren mit:")
        print(f"  python3 gust_decode.py {nf_wav} --scan")

        # IQ-Datei für HF-Inspektion
        iq = nf_to_iq_usb(audio, HACKRF_SAMPLE_RATE)
        iq_path = f"gust_lab_{freq_hz/1e6:.3f}MHz_hackrf.cf32"
        iq.tofile(iq_path)
        print(f"\n  {iq_path}  → inspectrum (Sample Rate {HACKRF_SAMPLE_RATE/1e6:.0f} MSps)")
        print(f"     Zeigt das SSB-IQ-Signal wie der HackRF es sendet")

    # HackRF TX (nur wenn SoapySDR vorhanden)
    if use_hackrf:
        if not _SOAPY_AVAILABLE:
            print("\n⚠  SoapySDR nicht verfügbar — HackRF TX übersprungen.")
            print("   Installation: apt install python3-soapysdr soapysdr-module-hackrf")
            print(f"\n   NF-WAV kann manuell über GQRX/fldigi oder Soundkarte gesendet werden.")
            print(f"   Datei: {nf_wav}")
        else:
            print(f"\nHackRF TX auf {freq_hz/1e6:.3f} MHz...")
            with HackRFTransmitter(freq_hz=freq_hz) as hackrf:
                hackrf.transmit(audio)

    print("\n" + "=" * 60)
    print("Labortest abgeschlossen.")
    print("\nNächste Schritte:")
    print("  1. WAV-Datei in Audacity öffnen → Spektrum prüfen")
    print("     (MFSK-Kammstruktur bei 900–1150 Hz)")
    print("  2. CF32 in inspectrum öffnen (Sample Rate: 8000 Hz)")
    print("  3. WAV dekodieren:")
    print(f"     python3 gust_decode.py gust_lab_ch{used_ch}_nf.wav --scan")
    print("  4. Auf-Air-Test: HackRF → Dämpfungsglied → SDRplay empfangen")
    print("=" * 60)


# ═══════════════════════════════════════════════════════════════════════
# CLI / MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="gust_hackrf.py",
        description=(
            "GUST HackRF — Direkter TX-Schnelltest\n"
            "\n"
            "Sendet einen einzelnen GUST-Testframe via HackRF One.\n"
            "Hauptverwendung: Modul-Import durch gust_tx_test.py und gust_hackrf_diag.py.\n"
            "Direktaufruf für schnelle Hardware-Verifikation.\n"
            "Erfordert Python 3.9 + PothosSDR (PYTHONPATH setzen)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--freq", type=float, default=LAB_TX_FREQ_HZ, metavar="HZ",
        help=f"TX-Trägerfrequenz in Hz "
             f"(Standard: {LAB_TX_FREQ_HZ:.0f} = 14.110 MHz)"
    )
    parser.add_argument(
        "--callsign", default="OE3GAS", metavar="RUFZEICHEN",
        help="Eigenes Rufzeichen für den Testframe (Standard: OE3GAS)"
    )
    parser.add_argument(
        "--channel", type=int, default=None, metavar="0-9",
        help="NF-Kanal 0–9 (Standard: automatisch aus SHA-256-Hash des Rufzeichens)"
    )
    parser.add_argument(
        "--no-hackrf", action="store_true",
        help="NF-Dateien erzeugen ohne HackRF TX (SoapySDR nicht nötig)"
    )
    parser.add_argument(
        "--probe", action="store_true",
        help="SoapySDR Geräte auflisten (entspricht SoapySDRUtil --probe)"
    )

    # No-Args-Hint — vor parse_args()
    if len(sys.argv) == 1:
        print("Verwendung: python gust_hackrf.py -h  oder  --help  für Parameterübersicht")
        sys.exit(0)

    args = parser.parse_args()

    if args.probe:
        if _SOAPY_AVAILABLE:
            results = SoapySDR.Device.enumerate()
            print(f"\nSoapySDR Geräte ({len(results)} gefunden):")
            for i, r in enumerate(results):
                print(f"  [{i}] {dict(r)}")
            if not results:
                print("  Keine Geräte gefunden.")
        else:
            print("SoapySDR nicht installiert.")
        return

    run_labortest(
        freq_hz    = args.freq,
        callsign   = args.callsign,
        channel    = args.channel,
        use_hackrf = not args.no_hackrf,
        save_cf32  = True,
    )


if __name__ == "__main__":
    main()
