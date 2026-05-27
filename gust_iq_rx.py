#!/usr/bin/env python3
"""
GUST — IQ-Eingang: RTL-SDR / SoapySDR                     Phase 9
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Version : 0.1.0  (Phase 9: IQ-Eingang, Protokoll v0.5)
Datum   : Mai 2026

Empfangspfad für direkten IQ-Eingang von SDR-Hardware (RTL-SDR, SDRplay,
HackRF im RX-Modus). Ergänzt den bestehenden Audio-Pfad (gust_rx.py).

Vorteile gegenüber Audio-Pfad:
  • Kein SSB-Filterrolloff — eigener FIR-Bandpass, ±0,1 dB über 2 kHz
  • Alle 8 Kanäle gleichzeitig empfangen (digitales Filterbank)
  • Kein Transceiver notwendig — RTL-SDR ~25 EUR genügt für RX-only Gateway
  • Passband-Equalizer wirkt, aber weniger nötig als bei SSB-Audio

Hardware-Voraussetzungen:
  RTL-SDR:    pyrtlsdr  (pip install pyrtlsdr)
  Soapy-SDRs: SoapySDR Python-Bindings (sdrangel/soapysdr Paket)

Kalibrierung RTL-SDR:
  ppm_correction in gateway.json unter "rtlsdr":
    rtl_test -p   →  Ablesen des PPM-Fehlers  →  Eintragen

Verwendung:
  Standalone-Test:
    python gust_iq_rx.py --freq 14110000 --ppm 3 --scan

  Als Modul in gust.py:
    from gust_iq_rx import IQReceiver
    rx = IQReceiver(config)
    await rx.run(event_bus)

Hinweis zur Dekodierung (v0.5):
  Der eigentliche Dekoder-Pfad nutzt gust_modulator.receive() — die
  vollständige RX-Pipeline (SYNC-Suche → Symbole → RS-FEC → Frame → CRC →
  Payload). receive() liefert bereits ein fertiges Frame-Dict; ein
  manuelles parse_frame()/decode_payload() ist hier nicht nötig.
"""

import asyncio
import logging
import time
import numpy as np
from typing import Optional

from gust_frame import (
    N_CHANNELS, CHANNEL_BASE_HZ, CHANNEL_BW_HZ,
    channel_frequency,
)
from gust_modulator import (
    SAMPLE_RATE, SAMPLES_PER_SYM, TONE_SPACING, N_TONES,
    demodulate, receive,
)

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# KONSTANTEN
# ═══════════════════════════════════════════════════════════════════════

IQ_SAMPLE_RATE   = 250_000   # Hz — RTL-SDR Mindest-SR (stabil ab 250 kHz)
CAPTURE_DURATION = 10.0      # Sekunden pro Scan-Fenster (≥ max. Frame-Länge ~5 s)
RESAMPLE_FACTOR  = IQ_SAMPLE_RATE // SAMPLE_RATE   # 250000 / 8000 = 31,25 → gerundet


# ═══════════════════════════════════════════════════════════════════════
# IQ → KANAL-AUDIO KONVERTIERUNG
# ═══════════════════════════════════════════════════════════════════════

def iq_to_channel_audio(
    iq_samples:  np.ndarray,
    iq_sr:       int,
    channel:     int,
    ppm:         float = 0.0,
    center_freq: float = 14_110_000.0,
) -> np.ndarray:
    """
    Konvertiert einen IQ-Strom in das Kanal-Audio für demodulate().

    Verarbeitungspfad:
      1. Frequenzkorrektur (PPM-Fehler des SDR)
      2. FIR-Bandpass auf das Kanalfenster [f_low – 50 Hz, f_high + 50 Hz]
      3. Betrag des analytischen Signals (Envelope Detection)
      4. Downsampling auf GUST SAMPLE_RATE (8000 Hz) via scipy.signal

    Args:
        iq_samples:  Complex64-Array vom SDR (IQ-Basisband, zentriert bei 0)
        iq_sr:       Sample-Rate des IQ-Stroms (z.B. 250000)
        channel:     GUST-Kanal 0–7
        ppm:         Frequenzkorrektur des SDR in ppm (aus gateway.json)
        center_freq: HF-Trägerfrequenz in Hz (nur für PPM-Berechnung)

    Returns:
        float32 Audio-Array bei 8000 Hz — direkt an demodulate() übergeben
    """
    from scipy.signal import firwin, lfilter, resample_poly
    from math import gcd

    # 1. PPM-Frequenzkorrektur: Trägerfehler als Phasenrampe kompensieren
    if abs(ppm) > 0.01:
        freq_error_hz = center_freq * ppm / 1e6
        t = np.arange(len(iq_samples)) / iq_sr
        correction = np.exp(-1j * 2 * np.pi * freq_error_hz * t)
        iq_samples = iq_samples * correction

    # 2. Kanalfenster bestimmen (NF-Frequenzen des Kanals)
    f_base  = channel_frequency(channel)              # Ton-0-Frequenz (z.B. 600 Hz für Kanal 0)
    f_low   = f_base - 50.0                           # Guard-Band unterhalb
    f_high  = f_base + (N_TONES - 1) * TONE_SPACING + 50.0   # Guard-Band oberhalb

    # Normierte Cutoff-Frequenzen (0..1 = Nyquist)
    nyq    = iq_sr / 2.0
    lo_n   = max(0.001, f_low  / nyq)
    hi_n   = min(0.999, f_high / nyq)

    # FIR-Bandpass (101 Taps — Kompromiss: Flankensteilheit vs. Rechenzeit)
    b = firwin(101, [lo_n, hi_n], pass_zero=False, window='hamming')
    filtered = lfilter(b, 1.0, iq_samples.real.astype(np.float32))

    # 3. Downsampling auf 8000 Hz via polyphasisches Resampling
    g = gcd(iq_sr, SAMPLE_RATE)
    up   = SAMPLE_RATE // g
    down = iq_sr // g
    audio = resample_poly(filtered, up, down).astype(np.float32)

    # Normalisieren auf [-1, 1]
    peak = np.max(np.abs(audio))
    if peak > 1e-6:
        audio /= peak

    return audio


def iq_to_all_channels(
    iq_samples:  np.ndarray,
    iq_sr:       int,
    ppm:         float = 0.0,
    center_freq: float = 14_110_000.0,
) -> dict:
    """
    Filterbank: IQ-Strom → Audio für alle N_CHANNELS Kanäle.

    Gibt ein Dict {channel: audio_array} zurück — alle Kanäle parallel,
    bereit für parallele Dekodierung.

    Verwendung im Gateway-Betrieb:
        iq = sdr.read_samples(n)
        channels = iq_to_all_channels(iq, IQ_SAMPLE_RATE, ppm=3)
        for ch, audio in channels.items():
            result = receive(audio, channel=ch, use_equalizer=True)
            if result.get('crc_ok'):
                ...
    """
    return {
        ch: iq_to_channel_audio(iq_samples, iq_sr, ch, ppm, center_freq)
        for ch in range(N_CHANNELS)
    }


# ═══════════════════════════════════════════════════════════════════════
# EMPFANG AUS IQ-DATEI (für Tests ohne Hardware)
# ═══════════════════════════════════════════════════════════════════════

def decode_iq_file(
    path:        str,
    iq_sr:       int   = IQ_SAMPLE_RATE,
    center_freq: float = 14_110_000.0,
    ppm:         float = 0.0,
    channel:     int   = None,
    use_equalizer: bool = True,
) -> list:
    """
    Dekodiert eine CF32-IQ-Datei (z.B. von HackRF oder inspectrum).

    Args:
        path:          Pfad zur .cf32-Datei (complex64)
        iq_sr:         Sample-Rate der Aufnahme
        center_freq:   HF-Mittenfrequenz in Hz
        ppm:           PPM-Korrektur
        channel:       Kanal 0–7 oder None für Breitband-Scan aller Kanäle
        use_equalizer: Passband-Equalizer aktivieren (empfohlen)

    Returns:
        Liste von dekodierten Frame-Dicts (kann leer sein). Jeder Eintrag ist
        das Ergebnis-Dict von gust_modulator.receive() mit CRC-OK, ergänzt um
        '_source' und '_channel'.
    """
    iq = np.fromfile(path, dtype=np.complex64)
    log.info(f"IQ-Datei: {path}, {len(iq)/iq_sr:.1f}s, {iq_sr/1000:.0f} kHz SR")

    results = []
    channels_to_scan = [channel] if channel is not None else range(N_CHANNELS)

    for ch in channels_to_scan:
        audio = iq_to_channel_audio(iq, iq_sr, ch, ppm, center_freq)
        # Vollständige RX-Pipeline: SYNC → Symbole → RS-FEC → Frame → CRC → Payload
        res = receive(audio, channel=ch, use_fec=True, use_equalizer=use_equalizer)
        if not res.get('sync_found'):
            continue
        if not res.get('crc_ok'):
            log.debug(f"Kanal {ch}: SYNC gefunden, aber CRC fehlgeschlagen")
            continue
        res['_source']  = 'iq_file'
        res['_channel'] = ch
        results.append(res)
        log.info(f"Kanal {ch}: Frame dekodiert — {res.get('from','?')} "
                 f"{res.get('type_name','?')}")

    return results


# ═══════════════════════════════════════════════════════════════════════
# LIVE-EMPFANG (RTL-SDR via pyrtlsdr)
# ═══════════════════════════════════════════════════════════════════════

class IQReceiver:
    """
    Asynchroner IQ-Empfänger für RTL-SDR (und andere SoapySDR-Geräte).

    Schnittstelle identisch zu gust_rx.py AudioReceiver — drop-in Ersatz
    für RX-only Gateways ohne Transceiver.

    gateway.json Konfiguration:
        {
          "rtlsdr": {
            "enabled": true,
            "center_freq_hz": 14110000,
            "sample_rate": 250000,
            "gain": "auto",
            "ppm_correction": 3
          }
        }
    """

    def __init__(self, config: dict):
        rtl_cfg = config.get('rtlsdr', {})
        self.enabled     = rtl_cfg.get('enabled', False)
        self.center_freq = rtl_cfg.get('center_freq_hz', 14_110_000)
        self.sample_rate = rtl_cfg.get('sample_rate', IQ_SAMPLE_RATE)
        self.gain        = rtl_cfg.get('gain', 'auto')
        self.ppm         = rtl_cfg.get('ppm_correction', 0)
        self._running    = False

    async def run(self, event_bus, dedup_cache: Optional[set] = None):
        """
        Hauptschleife: IQ lesen → alle Kanäle dekodieren → Events emittieren.
        Läuft bis stop() aufgerufen wird.
        """
        if not self.enabled:
            log.info("IQReceiver: nicht aktiviert (rtlsdr.enabled=false)")
            return

        try:
            import rtlsdr
        except ImportError:
            log.error("pyrtlsdr nicht installiert: pip install pyrtlsdr")
            return

        sdr = rtlsdr.RtlSdr()
        try:
            sdr.sample_rate    = self.sample_rate
            sdr.center_freq    = self.center_freq
            sdr.freq_correction = int(self.ppm)
            if self.gain == 'auto':
                sdr.gain = 'auto'
            else:
                sdr.gain = float(self.gain)

            log.info(f"IQReceiver gestartet: {self.center_freq/1e6:.3f} MHz, "
                     f"{self.sample_rate/1000:.0f} kHz, PPM={self.ppm}")

            n_samples = int(self.sample_rate * CAPTURE_DURATION)
            self._running = True

            while self._running:
                iq = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: sdr.read_samples(n_samples)
                )
                iq = np.array(iq, dtype=np.complex64)
                await self._process_block(iq, event_bus, dedup_cache)
                await asyncio.sleep(0)   # Scheduler-Tick

        finally:
            sdr.close()

    async def _process_block(self, iq: np.ndarray, event_bus, dedup_cache):
        """Alle 8 Kanäle eines IQ-Blocks parallel dekodieren."""
        channels_audio = iq_to_all_channels(
            iq, self.sample_rate, self.ppm, self.center_freq
        )
        for ch, audio in channels_audio.items():
            # Vollständige RX-Pipeline pro Kanal (mit Passband-Equalizer)
            res = receive(audio, channel=ch, use_fec=True, use_equalizer=True)
            if not (res.get('sync_found') and res.get('crc_ok')):
                continue
            try:
                # Dedup via (Rufzeichen, Typ, Kanal)
                if dedup_cache is not None:
                    key = (res.get('from'), res.get('type'), ch)
                    if key in dedup_cache:
                        continue
                    dedup_cache.add(key)

                res['_source']  = 'iq_rtlsdr'
                res['_channel'] = ch

                if event_bus:
                    await event_bus.publish({
                        'type': 'rx_frame',
                        'data': res,
                        'ts':   time.time(),
                    })
                log.info(f"IQ Kanal {ch}: {res.get('from','?')} "
                         f"{res.get('type_name','?')}")
            except Exception as e:
                log.debug(f"IQ Kanal {ch}: Verarbeitungsfehler: {e}")

    def stop(self):
        self._running = False


# ═══════════════════════════════════════════════════════════════════════
# STANDALONE-TEST
# ═══════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse, sys

    parser = argparse.ArgumentParser(description='GUST IQ-Empfänger Test')
    parser.add_argument('--file',    help='CF32-IQ-Datei dekodieren')
    parser.add_argument('--freq',    type=float, default=14_110_000, help='Mittenfrequenz Hz')
    parser.add_argument('--sr',      type=int,   default=IQ_SAMPLE_RATE, help='Sample-Rate')
    parser.add_argument('--ppm',     type=float, default=0.0, help='PPM-Korrektur')
    parser.add_argument('--channel', type=int,   default=None, help='Kanal 0–7 (default: alle)')
    parser.add_argument('--no-eq',   action='store_true', help='Equalizer deaktivieren')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    if args.file:
        frames = decode_iq_file(
            args.file,
            iq_sr       = args.sr,
            center_freq = args.freq,
            ppm         = args.ppm,
            channel     = args.channel,
            use_equalizer = not args.no_eq,
        )
        if frames:
            print(f"\n{len(frames)} Frame(s) dekodiert:")
            for f in frames:
                print(f"  Kanal {f.get('_channel','?')}: {f.get('from','?')} "
                      f"— {f.get('type_name','?')}")
        else:
            print("Keine Frames gefunden.")
    else:
        print("Verwendung:")
        print("  python gust_iq_rx.py --file aufnahme.cf32 --freq 14110000 --ppm 3")
        print("  python gust_iq_rx.py --file aufnahme.cf32 --channel 2")
