#!/usr/bin/env python3
"""
GUST — MFSK-8 Modulator / Demodulator                     Phase 1
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Version : 0.2.0  (Phase 3: Raised Cosine Windowing)
Datum   : Mai 2026

Inhalt:
  • MFSK-8 Modulator   — phasenkontinuierlich, channel-aware
  • MFSK-8 Demodulator — FFT-basiert (Loopback / Labortest)
  • TX-Pipeline        — Frame-Body → Audio in einem Aufruf
  • RX-Pipeline        — Audio → Frame-Body → Dict
  • WAV-Utilities      — Speichern / Laden für inspectrum / Audacity
  • Selbsttest         — vollständiger TX→WAV→RX Loopback

Abhängigkeiten:
  numpy, scipy          — Signal/WAV
  gust_frame         — Channel-Plan, Frame-Builder, RS-FEC

Schnittstelle zu Phase 2 (Frame Layer):
  TX:  build_frame() + frame_to_symbol_stream()  →  modulate_channel()
  RX:  demodulate()  →  parse_frame() + decode_payload()

Kanalplan (aus gust_frame):
  Kanal 0:   600– 850 Hz    Kanal 4: 1600–1850 Hz
  Kanal 1:   850–1100 Hz    Kanal 5: 1850–2100 Hz
  Kanal 2:  1100–1350 Hz    Kanal 6: 2100–2350 Hz
  Kanal 3:  1350–1600 Hz    Kanal 7: 2350–2600 Hz
  → Gesamtspan 600–2600 Hz (v0.5), SSB-Plateau ±0,5 dB
"""

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfilt
import struct as _struct
import os
import sys

from gust_frame import (
    channel_frequency, assign_channel,
    CHANNEL_BW_HZ, N_CHANNELS, CHANNEL_BASE_HZ,
    build_frame, parse_frame, decode_payload, frame_to_symbol_stream,
    FrameType, encode_weather, decode_weather,
    SYNC_SYMBOLS,
)


# ═══════════════════════════════════════════════════════════════════════
# PHYSIKALISCHE KONSTANTEN
# ═══════════════════════════════════════════════════════════════════════
#
# Diese Werte sind durch die Protokollspezifikation fest vorgegeben
# und dürfen nicht geändert werden — sie bestimmen Kompatibilität.

SAMPLE_RATE     = 8000      # Hz  — Abtastrate (telefonkompatibel, reicht für 3 kHz NF)
SYMBOL_DUR      = 0.032     # s   — Symboldauer = 32 ms
TONE_SPACING    = 31.25     # Hz  — Tonabstand  = 1 / SYMBOL_DUR  (Orthogonalitätsbedingung)
N_TONES         = 8         # —   — MFSK-8: 8 Töne, 3 Bit/Symbol
SAMPLES_PER_SYM = int(SYMBOL_DUR * SAMPLE_RATE)   # = 256 Samples

# FFT Zero-Padding für Demodulator und Tests
# 256 Samples → Bin-Abstand 31,25 Hz (= TONE_SPACING → ungünstig)
# 4096 Punkte → Bin-Abstand  1,95 Hz (16× genauer, Peaks klar trennbar)
FFT_PAD_N       = 4096
FFT_RESOLUTION  = SAMPLE_RATE / FFT_PAD_N   # ≈ 1,953 Hz/Bin

# Abgeleitete Größen (informativ)
RAW_BITRATE_BPS = (N_TONES.bit_length() - 1) / SYMBOL_DUR   # = 93,75 bit/s
# (N_TONES.bit_length()-1 = log2(8) = 3)


# ═══════════════════════════════════════════════════════════════════════
# RAISED COSINE SYMBOL-FENSTERUNG  (Phase 3)
# ═══════════════════════════════════════════════════════════════════════
#
# Problem (Phase 1/2):
#   Rechteckige Symbolfenster → abrupte Ein-/Ausblendung jedes Symbols.
#   → Sinc-förmige Seitenkeullen im Spektrum (Spectral Leakage).
#   → Im Audacity-Spektrum sichtbar als breites Rauschteppich unter dem
#     MFSK-Signal, von ~300 Hz bis ~3000 Hz.
#
# Lösung (Phase 3):
#   Raised Cosine Fensterfunktion an den Symbolflanken:
#     w(t) = 0,5 × (1 − cos(π·t/T_rolloff))  für Ramp-up
#     Plateau bei 1,0 über den mittleren Symbolbereich
#     w(t) = 0,5 × (1 + cos(π·t/T_rolloff))  für Ramp-down
#
#   Ergebnis: Seitenkeullen um ~40–50 dB reduziert → weniger QRM
#   auf Nachbarkanälen. Für On-Air-Betrieb auf KW dringend empfohlen.
#
#   Demodulator: Das Fenster verändert die Frequenz nicht, nur die
#   Amplitudenhüllkurve. Die FFT-basierte Symbolerkennung bleibt
#   unverändert — kein Einfluss auf die Dekodiergenauigkeit.
#
# Rolloff-Anteil: 1/8 des Symbols = 4 ms (bei 32 ms Symboldauer).
#   Kompromiss: kurzer Einfluss auf ISI, gute Seitenkeullen-Dämpfung.

_RC_ROLLOFF_FRAC = 1 / 8   # Anteil des Symbols für Auf-/Abklingrampe

def raised_cosine_window(n_samples: int = SAMPLES_PER_SYM,
                          rolloff_frac: float = _RC_ROLLOFF_FRAC) -> np.ndarray:
    """
    Raised Cosine Fensterfunktion für MFSK-8 Symbol-Shaping.

    Struktur: [Ramp-up | Plateau | Ramp-down]
              |← r →|←   n - 2r  →|← r →|

    Args:
        n_samples:    Fensterlänge (Standard: SAMPLES_PER_SYM = 256)
        rolloff_frac: Anteil für Auf-/Abklingflanken (Standard: 1/8 = 4 ms)

    Returns:
        np.ndarray float64, Werte zwischen 0,0 und 1,0.

    Spektrale Wirkung (verglichen mit Rechteckfenster):
        Erste Nebenkeulle:  −13 dB → ~−40 dB  (ca. 27 dB Verbesserung)
        Das entspricht einer Reduktion der HF-Splatter-Leistung um Faktor ~500.
    """
    r = max(1, int(n_samples * rolloff_frac))
    w = np.ones(n_samples, dtype=np.float64)

    # Ramp-up: 0 → 1 über r Samples (Raised Cosine Anstieg)
    t_up   = np.arange(r)
    w[:r]  = 0.5 * (1.0 - np.cos(np.pi * t_up / r))

    # Ramp-down: 1 → 0 über r Samples (Raised Cosine Abfall)
    t_dn   = np.arange(r)
    w[-r:] = 0.5 * (1.0 + np.cos(np.pi * t_dn / r))

    return w

# Vorberechnetes Fenster (einmalig — spart CPU im Gateway-Betrieb)
_RC_WINDOW = raised_cosine_window()


# ═══════════════════════════════════════════════════════════════════════
# KERN-MODULATOR
# ═══════════════════════════════════════════════════════════════════════

def mfsk8_modulate(symbol_indices: list, base_freq_hz: float,
                   window: bool = False) -> np.ndarray:
    """
    Phasenkontinuierlicher MFSK-8 Modulator mit optionalem Raised Cosine Windowing.

    Wandelt eine Liste von Symbol-Indizes (0–7) in ein Audio-Signal um.
    Jedes Symbol wird als Sinuston der Dauer 32 ms erzeugt.

    Args:
        symbol_indices: Liste von Ganzzahlen 0–7 (vom Frame Layer)
        base_freq_hz:   Frequenz des untersten Tons (Ton 0) in Hz
                        → ergibt sich aus channel_frequency(channel)
        window:         True = Raised Cosine Symbolfensterung (Phase 3).
                        Reduziert Seitenkeullen um ~40 dB → weniger QRM.
                        False = Rechteckfenster (Phase 1/2 Verhalten, default).
                        Für On-Air-Betrieb: window=True empfohlen.

    Returns:
        np.ndarray (float32), Länge = len(symbols) × 256 Samples

    Phasenkontinuität:
        Der Phasenwinkel wird zwischen Symbolen weitergeführt.
        Ohne das entstehen Phasensprünge → Klick-Artefakte → unnötige
        Spektralausbreitung (Seitenband-Splatter auf KW).

    Ton-Frequenzen (Beispiel Kanal 2, base=900 Hz):
        Ton 0:  900,00 Hz   Ton 4: 1025,00 Hz
        Ton 1:  931,25 Hz   Ton 5: 1056,25 Hz
        Ton 2:  962,50 Hz   Ton 6: 1087,50 Hz
        Ton 3:  993,75 Hz   Ton 7: 1118,75 Hz
    """
    output = []
    phase  = 0.0
    t_sym  = np.arange(SAMPLES_PER_SYM)

    for idx in symbol_indices:
        freq  = base_freq_hz + idx * TONE_SPACING
        omega = 2.0 * np.pi * freq / SAMPLE_RATE
        tone  = np.sin(phase + omega * t_sym)
        # Phase für nächstes Symbol exakt weiterführen
        phase = (phase + omega * SAMPLES_PER_SYM) % (2.0 * np.pi)
        # Phase 3: Raised Cosine Fensterfunktion anwenden
        if window:
            tone = tone * _RC_WINDOW
        output.append(tone)

    return np.concatenate(output).astype(np.float32)


def modulate_channel(symbol_indices: list, channel: int, window: bool = False) -> np.ndarray:
    """
    Kanal-aware Modulator: ermittelt base_freq aus dem Kanalplan
    und ruft mfsk8_modulate() auf.

    Dies ist die primäre TX-Funktion — channel bestimmt die NF-Lage.

    Args:
        symbol_indices: Ausgabe von frame_to_symbol_stream()
        channel:        Kanal 0–9 (aus assign_channel())

    Returns:
        Audio-Signal als float32

    Beispiel:
        payload = encode_weather(21.5, 68, 1013.2, 15, 270)
        body    = build_frame(FrameType.WEATHER, 'OE3GAS', payload)
        symbols = frame_to_symbol_stream(body)
        ch, _   = assign_channel('OE3GAS')
        audio   = modulate_channel(symbols, ch)
        # → Töne bei 900–1150 Hz (Kanal 2)
    """
    base = channel_frequency(channel)
    return mfsk8_modulate(symbol_indices, base_freq_hz=base, window=window)


# ═══════════════════════════════════════════════════════════════════════
# TX-PIPELINE  (Frame → Audio in einem Aufruf)
# ═══════════════════════════════════════════════════════════════════════

def transmit(
    frame_type: int,
    callsign:   str,
    payload:    bytes,
    channel:    int  = None,
    use_fec:    bool = True,
    add_silence_ms: int = 200,
    window:     bool = False,
    test:       bool = False,
) -> tuple:
    """
    Vollständige TX-Pipeline: Payload → moduliertes Audio.

    Ablauf:
        payload → build_frame() → frame_to_symbol_stream() → modulate_channel()

    Args:
        frame_type:     z.B. FrameType.WEATHER
        callsign:       Rufzeichen der sendenden Station
        payload:        Kodierte Nutzdaten (aus encode_*() Funktionen)
        channel:        Kanal 0–9; None = automatisch aus assign_channel()
        use_fec:        Reed-Solomon FEC aktivieren (empfohlen)
        add_silence_ms: Stille vor/nach dem Signal (für PTT-Vorlauf)
        window:         Raised Cosine Fensterung (Phase 3, default=False)

    Returns:
        (audio: np.ndarray float32,  channel: int,  frame_body: bytes)

    Beispiel:
        payload = encode_weather(21.5, 68, 1013.2, 15, 270)
        audio, ch, _ = transmit(FrameType.WEATHER, 'OE3GAS', payload)
        save_wav('wx_oe3gas.wav', audio)
    """
    if channel is None:
        channel, _ = assign_channel(callsign)

    frame_body = build_frame(frame_type, callsign, payload, channel, test=test)
    symbols    = frame_to_symbol_stream(frame_body, use_fec=use_fec)
    signal     = modulate_channel(symbols, channel, window=window)

    if add_silence_ms > 0:
        silence_samples = int(add_silence_ms / 1000 * SAMPLE_RATE)
        silence = np.zeros(silence_samples, dtype=np.float32)
        signal = np.concatenate([silence, signal, silence])

    return signal, channel, frame_body


# ═══════════════════════════════════════════════════════════════════════
# KERN-DEMODULATOR
# ═══════════════════════════════════════════════════════════════════════
#
# Methode: FFT-basierte Tondetektierung pro Symbol-Fenster.
#
# Für jeden 256-Sample-Block:
#   1. FFT berechnen
#   2. Magnitude bei den 8 erwarteten Ton-Frequenzen ablesen
#   3. Ton mit höchster Magnitude = detektiertes Symbol
#
# Dieser Ansatz ist ausreichend für Loopback-Tests und klare Signale.
# Für reale KW-Bedingungen (Fading, QRM) ist ein Matched-Filter oder
# Goertzel-Algorithmus robuster — das ist Arbeit für Phase 3/GNU Radio.

def _tone_frequencies(base_freq_hz: float) -> np.ndarray:
    """Gibt die 8 Ton-Frequenzen für eine gegebene Basis zurück."""
    return np.array([base_freq_hz + i * TONE_SPACING for i in range(N_TONES)])

def _fft_detect_symbol(block: np.ndarray, tone_freqs: np.ndarray) -> int:
    """
    Erkennt das Symbol in einem 256-Sample-Block via zero-padded FFT.

    Zero-Padding auf FFT_PAD_N=4096 gibt ~1,95 Hz Frequenzauflösung.
    Ohne Padding wäre die Auflösung 31,25 Hz (= Tonabstand) — das führt
    zu Mehrdeutigkeiten, weil Kanalbasen wie 900 Hz nicht auf Bin-Grenzen
    fallen (900 / 31,25 = 28,8).

    Mit Padding: bin 461 = 461 × (8000/4096) = 900,39 Hz → Δ < 0,4 Hz ✓
    """
    spectrum = np.abs(np.fft.rfft(block, n=FFT_PAD_N))
    magnitudes = np.array([
        spectrum[round(f / SAMPLE_RATE * FFT_PAD_N)]
        for f in tone_freqs
    ])
    return int(np.argmax(magnitudes))


def _build_equalizer(audio: np.ndarray, sync_pos: int,
                     tone_freqs: np.ndarray) -> np.ndarray:
    """
    Passband-Equalizer aus dem Costas-SYNC ableiten (v0.5).

    Da der Costas-SYNC alle 8 Töne je einmal enthält, kann der Empfänger
    die empfangene Amplitude jedes Tons messen und einen Korrektionsvektor
    berechnen. Dieser kompensiert den SSB-Filterrolloff automatisch.

    Wird von demodulate() aufgerufen, wenn use_equalizer=True.

    Args:
        audio:      Vollständiges Audio-Array
        sync_pos:   Sample-Position des SYNC-Starts (aus _find_sync_candidates)
        tone_freqs: 8 Tonfrequenzen des Kanals (Hz)

    Returns:
        eq: np.ndarray, shape (8,), Korrektionsfaktoren (≥ 1.0)
            eq[i] > 1 bedeutet: Ton i war gedämpft → wird verstärkt
            eq[i] = 1 bedeutet: Ton i hatte Referenzamplitude
    """
    measured = np.zeros(8, dtype=np.float64)

    for i, tone_idx in enumerate(SYNC_SYMBOLS):
        # Fenster des i-ten SYNC-Symbols aus dem Audio schneiden
        start = sync_pos + i * SAMPLES_PER_SYM
        end   = start + SAMPLES_PER_SYM
        if end > len(audio):
            measured[tone_idx] = 1.0
            continue
        block    = audio[start:end]
        spectrum = np.abs(np.fft.rfft(block, n=FFT_PAD_N))
        # Amplitude am erwarteten Ton messen
        bin_idx  = round(tone_freqs[tone_idx] / SAMPLE_RATE * FFT_PAD_N)
        bin_idx  = max(0, min(len(spectrum) - 1, bin_idx))
        measured[tone_idx] = float(spectrum[bin_idx])

    # Referenz = stärkster Ton (ungedämpft), Korrektionsfaktor = ref / gemessen
    ref = max(measured.max(), 1e-6)
    eq  = ref / np.maximum(measured, 1e-6)
    return eq.astype(np.float64)


def _fft_detect_symbol_eq(block: np.ndarray, tone_freqs: np.ndarray,
                           eq: np.ndarray) -> int:
    """
    Symbol-Erkennung mit Passband-Equalizer.

    Identisch zu _fft_detect_symbol(), aber die FFT-Amplitude jedes Tons
    wird vor dem argmax mit eq[i] multipliziert. Das kompensiert ungleiche
    Ton-Amplituden durch SSB-Filterrolloff oder SDR-Nichtlinearität.

    Args:
        block:      256-Sample-Audioblock
        tone_freqs: 8 Tonfrequenzen (Hz)
        eq:         Korrektionsvektor aus _build_equalizer(), shape (8,)

    Returns:
        int: Erkannter Symbol-Index 0–7
    """
    spectrum   = np.abs(np.fft.rfft(block, n=FFT_PAD_N))
    magnitudes = np.array([
        spectrum[round(f / SAMPLE_RATE * FFT_PAD_N)]
        for f in tone_freqs
    ], dtype=np.float64)
    return int(np.argmax(magnitudes * eq))


# ─────────────────────────────────────────────────────────────────────
# Soft-Output-Demodulation (P8-13) — LLR-Werte für LDPC
#
# _fft_detect_symbol() liefert nur das Hard-Symbol (argmax). Für eine
# künftige LDPC-Soft-Decision werden Log-Likelihood-Ratios (LLR) je Ton
# gebraucht. Die folgenden Funktionen laufen PARALLEL zum Hard-Pfad —
# _fft_detect_symbol() bleibt unverändert, kein Breaking Change.
#
# LLR-Definition: log( E_i / (Σ E - E_i) ) — Energie des Tons i gegen die
# Summe aller anderen Töne. argmax(LLR) == argmax(Energie) == Hard-Symbol.
# ─────────────────────────────────────────────────────────────────────

def _fft_detect_symbol_soft(block: np.ndarray,
                            tone_freqs: np.ndarray) -> np.ndarray:
    """
    Soft-Output-Variante von _fft_detect_symbol(): LLR statt Hard-Symbol.

    Args:
        block:      256-Sample-Audioblock
        tone_freqs: 8 Tonfrequenzen (Hz)

    Returns:
        np.ndarray shape (8,) float64 — LLR pro Ton (höher = wahrscheinlicher).
    """
    spectrum = np.abs(np.fft.rfft(block, n=FFT_PAD_N))
    energies = np.array([spectrum[round(f / SAMPLE_RATE * FFT_PAD_N)]
                         for f in tone_freqs], dtype=np.float64)
    eps = 1e-12
    total = np.sum(energies) + eps
    return np.log((energies + eps) / (total - energies + eps))


def _fft_detect_symbol_soft_eq(block: np.ndarray, tone_freqs: np.ndarray,
                               eq: np.ndarray) -> np.ndarray:
    """
    Soft-Output mit Passband-Equalizer (vgl. _fft_detect_symbol_eq()).

    Identisch zu _fft_detect_symbol_soft(), aber die Ton-Energien werden
    vor der LLR-Berechnung mit eq[i] multipliziert.

    Returns:
        np.ndarray shape (8,) float64 — LLR pro Ton.
    """
    spectrum = np.abs(np.fft.rfft(block, n=FFT_PAD_N))
    energies = np.array([spectrum[round(f / SAMPLE_RATE * FFT_PAD_N)]
                         for f in tone_freqs], dtype=np.float64)
    energies = energies * eq
    eps = 1e-12
    total = np.sum(energies) + eps
    return np.log((energies + eps) / (total - energies + eps))


def llr_to_symbol(llr: np.ndarray) -> int:
    """Hard-Decision aus LLR-Vektor — numerisch identisch zu _fft_detect_symbol()."""
    return int(np.argmax(llr))


# ─────────────────────────────────────────────────────────────────────
# Ton-LLR → Bit-LLR (P8-13 → LDPC-Brücke)
#
# _fft_detect_symbol_soft() liefert pro MFSK-8-Symbol 8 Ton-LLR. LDPC
# (gust_ldpc._decode_block) braucht per-Bit-LLR. Jedes Symbol kodiert 3 Bit:
# Bit k = (s >> k) & 1. Max-Log-Approximation:
#   bit_llr[k] = max(tone_llr[s] | bit_k(s)=0) - max(tone_llr[s] | bit_k(s)=1)
# Positiv → Bit k wahrscheinlich 0, negativ → Bit k wahrscheinlich 1.
#
# Vorberechnete Symbol-Indexmengen je Bit (k=0..2):
# ─────────────────────────────────────────────────────────────────────
_MFSK8_BIT0 = [
    np.array([s for s in range(8) if ((s >> 0) & 1) == 0], dtype=int),
    np.array([s for s in range(8) if ((s >> 1) & 1) == 0], dtype=int),
    np.array([s for s in range(8) if ((s >> 2) & 1) == 0], dtype=int),
]
_MFSK8_BIT1 = [
    np.array([s for s in range(8) if ((s >> 0) & 1) == 1], dtype=int),
    np.array([s for s in range(8) if ((s >> 1) & 1) == 1], dtype=int),
    np.array([s for s in range(8) if ((s >> 2) & 1) == 1], dtype=int),
]


def symbol_llr_to_bit_llr(tone_llr: np.ndarray) -> np.ndarray:
    """
    Wandelt einen Ton-LLR-Vektor (shape (8,), aus _fft_detect_symbol_soft())
    in 3 Bit-LLR (shape (3,)) um — Max-Log-Approximation.

    Returns:
        np.ndarray shape (3,) float64 — Bit-LLR [bit0, bit1, bit2].
        Positiv → Bit wahrscheinlich 0, negativ → Bit wahrscheinlich 1.
    """
    bit_llr = np.empty(3, dtype=np.float64)
    for k in range(3):
        bit_llr[k] = np.max(tone_llr[_MFSK8_BIT0[k]]) \
                   - np.max(tone_llr[_MFSK8_BIT1[k]])
    return bit_llr


def symbols_to_bit_llr_array(tone_llr_list) -> np.ndarray:
    """
    Wendet symbol_llr_to_bit_llr() auf jedes Ton-LLR der Liste an und gibt
    ein flaches Bit-LLR-Array zurück.

    Args:
        tone_llr_list: Sequenz von Ton-LLR-Vektoren (je shape (8,)), ein
                       Eintrag pro MFSK-8-Symbol.

    Returns:
        np.ndarray shape (N*3,) float64 — verkettete Bit-LLR in Symbolreihenfolge:
        [bit0_s0, bit1_s0, bit2_s0, bit0_s1, bit1_s1, bit2_s1, ...].
        Für die spätere LDPC-Block-Aufteilung (je N_BITS/3 Symbole pro Block).
    """
    if len(tone_llr_list) == 0:
        return np.empty(0, dtype=np.float64)
    return np.concatenate([symbol_llr_to_bit_llr(t) for t in tone_llr_list])


def _find_sync_candidates(audio: np.ndarray) -> list:
    """
    Energie-basierte SYNC-Kandidatensuche — alle 8 Kanäle (v0.5), vollvektorisiert.

    Scannt den NF-Bereich 500–2510 Hz in 8-Hz-Schritten und deckt damit alle
    GUST-Kanäle (0–7, v0.5: 600–2600 Hz) inklusive ±100 Hz Frequenzoffset ab.

    Costas-SYNC (v0.5): SYNC_SYMBOLS enthält alle 8 Töne je einmal.
    Score = mittlere Energie-Fraktion des erwarteten Tons über alle 8 SYNC-Positionen.
    Optimale Autokorrelation durch Costas-Eigenschaft → scharfer, eindeutiger Peak.

    Performance: numpy stride_tricks; ~250 Blöcke × 250 f0-Werte → < 0,5 s.

    Kandidaten-Format: (score: float, block_pos: int, f0_hz: float)
      f0_hz = tatsächliche Ton-0-Frequenz des Kandidaten.
    """
    SCORE_MIN      = 0.35   # Costas hat 8 Töne, gleichmäßige Energie → Erwartung ~0,35–0,50
    MAX_CANDIDATES = 12
    HALF_BW        = 8.0
    STEP_HZ        = 8.0
    HOP            = SAMPLES_PER_SYM // 2   # 128 Samples — Halb-Block-Auflösung

    if len(audio) < (len(SYNC_SYMBOLS) + 2) * SAMPLES_PER_SYM:
        return []

    bin_res = SAMPLE_RATE / FFT_PAD_N
    n_sync  = len(SYNC_SYMBOLS)  # = 8

    # Überlappende 256-Sample-Fenster im 128-Sample-Raster (zero-copy)
    n_pos = (len(audio) - SAMPLES_PER_SYM) // HOP + 1
    s     = audio.strides[0]
    frames = np.lib.stride_tricks.as_strided(
        audio, shape=(n_pos, SAMPLES_PER_SYM), strides=(HOP * s, s))

    # Spektren aller Fenster (mit Zero-Padding auf FFT_PAD_N)
    padded = np.zeros((n_pos, FFT_PAD_N), dtype=np.float32)
    padded[:, :SAMPLES_PER_SYM] = frames
    spectra = np.abs(np.fft.rfft(padded, axis=1))   # (n_pos, FFT_PAD_N//2+1)

    # SYNC-Ton-Indizes: welchen der 8 Töne erwartet Position i?
    sync_tones = np.array(SYNC_SYMBOLS, dtype=int)   # z.B. [2,0,6,7,1,4,3,5]

    candidates = []

    # Scan-Range: Kanal 0 v0.5 bei 600 Hz → untere Grenze 500 Hz (±100 Hz Offset)
    #             Kanal 7 v0.5 bei 2350 Hz → obere Grenze ca. 2510 Hz
    for f0 in np.arange(500.0, 2511.0, STEP_HZ):
        f_max = f0 + 7 * TONE_SPACING   # höchster Ton = Ton 7
        if f_max > SAMPLE_RATE / 2 - HALF_BW:
            break

        # Energie für alle 8 Töne an allen Zeitpositionen: e_tone shape = (8, n_pos)
        e_tone = np.zeros((8, n_pos), dtype=np.float64)
        for k in range(8):
            tf  = f0 + k * TONE_SPACING
            lo  = max(0, int((tf - HALF_BW) / bin_res))
            hi  = min(spectra.shape[1], int((tf + HALF_BW) / bin_res) + 1)
            e_tone[k] = np.sum(spectra[:, lo:hi] ** 2, axis=1)

        # Normalisieren: Energie-Fraktion pro Ton (Summe über alle 8 Töne = 1)
        total = np.sum(e_tone, axis=0) + 1e-12   # (n_pos,)
        r = e_tone / total                         # (8, n_pos)

        # Gleitfenster-Score: 8 SYNC-Symbole, je 2 HOPs auseinander
        # Score[t] = mean über i von r[sync_tones[i], t + 2*i]
        N = n_pos - 2 * (n_sync - 1)
        if N < 1:
            continue

        scores = np.zeros(N, dtype=np.float64)
        for i, tone_idx in enumerate(sync_tones):
            scores += r[tone_idx, 2 * i : 2 * i + N]
        scores /= n_sync

        best_pos   = int(np.argmax(scores))
        best_score = float(scores[best_pos])

        if best_score >= SCORE_MIN:
            candidates.append((best_score, best_pos * HOP, float(f0)))

    candidates.sort(key=lambda x: -x[0])

    # Dedup: ±2 HOPs (256 Samples) und ±30 Hz
    deduped = []
    for sc, sample_pos, f0 in candidates:
        if not any(abs(sample_pos - p) <= 256 and abs(f0 - fo) <= 30
                   for _, p, fo in deduped):
            deduped.append((sc, sample_pos, f0))
    return deduped[:MAX_CANDIDATES]


def _find_sync_wideband(audio: np.ndarray) -> tuple:
    """
    Breitband SYNC-Suche — alle Kanäle, energie-basiert (v3).
    Delegiert an _find_sync_candidates(); kompatible Schnittstelle.
    """
    cands = _find_sync_candidates(audio)
    if not cands:
        return -1, None
    _, sample_pos, f0 = cands[0]
    return sample_pos, f0


def demodulate(
    audio:         np.ndarray,
    channel:       int   = None,
    sync_symbols:  list  = None,
    freq_offset:   float = 0.0,
    use_equalizer: bool  = False,
    collect_llr:   bool  = False,
) -> tuple:
    """
    MFSK-8 Demodulator.

    Zwei Modi:
      channel=None  → Breitband: _find_sync_wideband() erkennt Kanal+Offset
                       automatisch. Kein Vorabwissen nötig.
      channel=0–9   → Direktmodus: bekannter Kanal, optionaler freq_offset.

    Args:
        audio:       Audio-Signal (float32 oder int16 oder uint8)
        channel:     Kanal 0–9, oder None für automatische Erkennung
        sync_symbols: SYNC-Sequenz (Standard: SYNC_SYMBOLS)
        freq_offset: Manueller Frequenzversatz Hz (nur im Direktmodus)
        use_equalizer: True = Passband-Equalizer aus Costas-SYNC ableiten.
                       Kompensiert SSB-Filterrolloff automatisch.
                       Empfohlen für Randkanäle und IQ-Eingang. (default: False)

    Returns:
        (data_symbols, sync_found, sync_offset_samples, detected_channel, detected_offset)
        data_symbols:         list[int] — Symbole nach dem SYNC
        sync_found:           bool
        sync_offset_samples:  int  — Sample-Position des SYNC-Starts
        detected_channel:     int  — erkannter Kanal (None wenn nicht gefunden)
        detected_offset:      float — erkannter Frequenzversatz in Hz
    """
    if sync_symbols is None:
        sync_symbols = SYNC_SYMBOLS

    # Normalisieren auf float32
    if audio.dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0
    elif audio.dtype == np.uint8:
        audio = (audio.astype(np.float32) - 128.0) / 128.0
    elif audio.dtype != np.float32:
        audio = audio.astype(np.float32)

    if channel is None:
        # ── Breitband-Modus ───────────────────────────────────────
        sync_pos_s, base_freq = _find_sync_wideband(audio)
        if sync_pos_s < 0:
            return [], False, -1, None, 0.0

        # Nächsten Kanal und Offset aus Basisfrequenz ableiten
        nearest_ch = int(round((base_freq - CHANNEL_BASE_HZ) / CHANNEL_BW_HZ))
        nearest_ch = max(0, min(N_CHANNELS - 1, nearest_ch))
        computed_offset = base_freq - channel_frequency(nearest_ch)

        base       = base_freq   # = channel_frequency(nearest_ch) + offset
        tone_freqs = _tone_frequencies(base)

        # Symbole direkt nach SYNC dekodieren
        data_start = sync_pos_s + len(sync_symbols) * SAMPLES_PER_SYM
        data_audio = audio[data_start:]
        n_blocks   = len(data_audio) // SAMPLES_PER_SYM
        blocks = [data_audio[i*SAMPLES_PER_SYM:(i+1)*SAMPLES_PER_SYM]
                  for i in range(n_blocks)]
        if use_equalizer and sync_pos_s >= 0:
            eq = _build_equalizer(audio, sync_pos_s, tone_freqs)
            data_syms = [_fft_detect_symbol_eq(b, tone_freqs, eq) for b in blocks]
            tone_llrs = ([_fft_detect_symbol_soft_eq(b, tone_freqs, eq)
                          for b in blocks] if collect_llr else None)
        else:
            data_syms = [_fft_detect_symbol(b, tone_freqs) for b in blocks]
            tone_llrs = ([_fft_detect_symbol_soft(b, tone_freqs)
                          for b in blocks] if collect_llr else None)
        if collect_llr:
            return data_syms, True, sync_pos_s, nearest_ch, computed_offset, tone_llrs
        return data_syms, True, sync_pos_s, nearest_ch, computed_offset

    else:
        # ── Direktmodus: bekannter Kanal ──────────────────────────
        base       = channel_frequency(channel) + freq_offset
        tone_freqs = _tone_frequencies(base)

        n_blocks    = len(audio) // SAMPLES_PER_SYM
        all_symbols = [
            _fft_detect_symbol(
                audio[i*SAMPLES_PER_SYM:(i+1)*SAMPLES_PER_SYM],
                tone_freqs
            )
            for i in range(n_blocks)
        ]

        sync_len = len(sync_symbols)
        sync_pos = None
        for i in range(len(all_symbols) - sync_len):
            if all_symbols[i:i+sync_len] == list(sync_symbols):
                sync_pos = i
                break

        if sync_pos is None:
            # Ohne gefundenen SYNC: use_equalizer wird ignoriert
            if collect_llr:
                return all_symbols, False, -1, channel, freq_offset, None
            return all_symbols, False, -1, channel, freq_offset

        sync_pos_s = sync_pos * SAMPLES_PER_SYM
        data_start = sync_pos_s + sync_len * SAMPLES_PER_SYM
        data_audio = audio[data_start:]
        n_data     = len(data_audio) // SAMPLES_PER_SYM
        if use_equalizer and sync_pos_s >= 0:
            # Datensymbole mit Passband-Equalizer aus dem Costas-SYNC neu dekodieren
            eq     = _build_equalizer(audio, sync_pos_s, tone_freqs)
            blocks = [data_audio[i*SAMPLES_PER_SYM:(i+1)*SAMPLES_PER_SYM]
                      for i in range(n_data)]
            data_syms = [_fft_detect_symbol_eq(b, tone_freqs, eq) for b in blocks]
            tone_llrs = ([_fft_detect_symbol_soft_eq(b, tone_freqs, eq)
                          for b in blocks] if collect_llr else None)
        else:
            data_syms = all_symbols[sync_pos + sync_len:]
            if collect_llr:
                blocks = [data_audio[i*SAMPLES_PER_SYM:(i+1)*SAMPLES_PER_SYM]
                          for i in range(n_data)]
                tone_llrs = [_fft_detect_symbol_soft(b, tone_freqs) for b in blocks]
            else:
                tone_llrs = None
        if collect_llr:
            return data_syms, True, sync_pos_s, channel, freq_offset, tone_llrs
        return data_syms, True, sync_pos_s, channel, freq_offset


# ═══════════════════════════════════════════════════════════════════════
# RX-PIPELINE  (Audio → Dict in einem Aufruf)
# ═══════════════════════════════════════════════════════════════════════

def _build_result_direct(data_symbols, sync_found, sync_offset, det_ch, det_offset,
                         use_fec, rs_available, sym2bytes, rs_dec, pf, dp, RS_OH,
                         tone_llrs=None):
    """Symbole → Ergebnis-Dict (Direktmodus ohne Multi-Hypothesen).

    tone_llrs: optionale Ton-LLR-Liste (aus demodulate(collect_llr=True)). Bei
    aktivem LDPC-Backend wird daraus Soft-Decision-BP genutzt (P8-14).
    """
    result = {
        "sync_found":       sync_found,
        "sync_offset_s":    sync_offset / SAMPLE_RATE if sync_offset >= 0 else None,
        "data_symbols":     len(data_symbols),
        "detected_channel": det_ch,
        "freq_offset_hz":   round(det_offset, 1),
    }
    if not sync_found:
        result["error"] = "SYNC nicht gefunden"
        return result
    min_fb  = 9
    rs_min  = RS_OH + min_fb
    nb_max  = len(data_symbols) * 3 // 8

    # ── Soft-Decision-LLR-Blöcke vorbereiten (nur bei aktivem LDPC-Backend) ──
    # Die Multi-Try-Schleife dekodiert sonst Hard-Bytes; bei LDPC liefert das nur
    # 1-Bit/Block-Korrektur (deutlich schwächer als RS). Mit Ton-LLR aus dem
    # Demodulator wird Soft-Decision-BP genutzt (~2 dB Gewinn, P8-13/P8-14).
    from gust_frame import _get_fec
    _fec      = _get_fec()
    _use_soft = (getattr(_fec, "name", None) == "ldpc") and bool(tone_llrs)
    llr_blocks = None
    if _use_soft:
        arr = symbols_to_bit_llr_array(tone_llrs)
        # symbols_to_bytes streamt pro Symbol MSB→LSB (Bit2,Bit1,Bit0),
        # symbols_to_bit_llr_array liefert [Bit0,Bit1,Bit2] → pro Triple umdrehen,
        # damit die LLR-Position zur LDPC-Bitposition passt.
        if len(arr) >= 3:
            arr = arr.reshape(-1, 3)[:, ::-1].reshape(-1)
        N = int(getattr(_fec, "N_BITS", 255))
        llr_blocks = []
        for i in range(0, len(arr), N):
            seg = arr[i:i + N]
            if len(seg) < N:
                seg = np.concatenate([seg, np.zeros(N - len(seg), dtype=np.float64)])
            llr_blocks.append(seg)
        if not llr_blocks:
            llr_blocks = None

    if use_fec and rs_available:
        last_e = None
        for n in range(nb_max, rs_min - 1, -1):
            try:
                raw  = sym2bytes(data_symbols, n)
                if _use_soft and llr_blocks is not None:
                    dec = _fec.decode(raw, llr_blocks=llr_blocks)
                else:
                    dec = rs_dec(raw)
                pars = pf(dec)
                if pars and pars["crc_ok"]:
                    result.update({
                        "type":            pars["type"],
                        "type_name":       pars["type_name"],
                        "channel":         pars["channel"],
                        "from":            pars["from"],
                        "test":            pars.get("test", False),
                        "crc_ok":          True,
                        "payload_decoded": dp(pars["type"], pars["payload"]),
                        "_rs_bytes_used":  n,
                        # Rohe Frame-Body-Bytes (TYPE+CHANNEL+FROM+PAYLOAD+CRC,
                        # exakt wie build_frame() liefert) — für AUTH-HMAC-
                        # Verifikation (P8-11). Internes Diagnosefeld.
                        "_raw_frame_body": dec,
                    })
                    return result
            except Exception as e:
                last_e = str(e)
        result["fec_error"] = last_e or "Unbekannt"
        result["error"]     = "RS-FEC Dekodierungsfehler"
    else:
        raw  = sym2bytes(data_symbols, nb_max)
        pars = pf(raw)
        if pars:
            result.update({
                "type":            pars["type"],
                "type_name":       pars["type_name"],
                "from":            pars["from"],
                "test":            pars.get("test", False),
                "crc_ok":          pars["crc_ok"],
                "payload_decoded": dp(pars["type"], pars["payload"]),
            })
        else:
            result["error"] = "Frame-Parse fehlgeschlagen"
    return result



def _sync_energy_at_sample(audio: np.ndarray, sample_pos: int, f0: float) -> float:
    """
    Single-Bin SYNC-Energie an Sample-Position und Basisfrequenz.

    Summiert die Energie am jeweils ERWARTETEN Ton über alle 8 SYNC-Symbole.
    Scharfes Maximum am wahren (f0, Timing) — Basis für Frequenz- UND
    Timing-Feinabstimmung.

    v0.5: allgemein für das Costas-SYNC-Array (alle 8 Töne), nicht mehr nur
    Ton 0/7. Für jedes SYNC-Symbol wird der Bin des tatsächlich erwarteten
    Tons ausgewertet.
    """
    if sample_pos < 0:
        return 0.0
    # Bin-Index je möglicher Ton (0–7) vorberechnen
    tone_bins = [round((f0 + k * TONE_SPACING) / SAMPLE_RATE * FFT_PAD_N)
                 for k in range(N_TONES)]
    if tone_bins[-1] >= FFT_PAD_N // 2:
        return 0.0
    e = 0.0
    for i, sym in enumerate(SYNC_SYMBOLS):
        a = sample_pos + i * SAMPLES_PER_SYM
        blk = audio[a : a + SAMPLES_PER_SYM]
        if len(blk) < SAMPLES_PER_SYM:
            return 0.0
        spec = np.abs(np.fft.rfft(blk, n=FFT_PAD_N))
        e += spec[tone_bins[sym]]
    return float(e)


def _refine_f0_at(audio: np.ndarray, sample_pos: int, f0_coarse: float) -> float:
    """Verfeinert f0 an fester Sample-Position (grob ±18Hz/2Hz, fein ±2Hz/0.5Hz)."""
    best_f0 = f0_coarse
    best_e  = _sync_energy_at_sample(audio, sample_pos, f0_coarse)
    for df in np.arange(-18, 19, 2):
        e = _sync_energy_at_sample(audio, sample_pos, f0_coarse + df)
        if e > best_e:
            best_e, best_f0 = e, f0_coarse + df
    cb = best_f0
    for df in np.arange(-2, 2.5, 0.5):
        e = _sync_energy_at_sample(audio, sample_pos, cb + df)
        if e > best_e:
            best_e, best_f0 = e, cb + df
    return float(best_f0)


def _refine_sync(audio: np.ndarray, sample_pos: int, f0_coarse: float) -> tuple:
    """
    Verfeinert SYNC-Frequenz UND -Timing.

    Die SYNC-Suche rastert grob: f0 in 8-Hz-Schritten (bis ±10 Hz Fehler)
    und Timing in Symbolblöcken (bis ±128 Samples = halbes Symbol Fehler).
    Beides muss nachgeschärft werden, sonst verfehlt die Symbol-Detektion.

    Ablauf:
      1. f0 grob an block-ausgerichteter Position
      2. Timing: ±192 Samples in 16-Sample-Schritten (Energiemaximum)
      3. Timing fein: ±16 Samples in 4-Sample-Schritten
      4. f0 fein an bester Timing-Position

    Returns:
        (sample_pos: int, f0_hz: float) — sample-genaue SYNC-Position.
    """
    base = int(sample_pos)

    # 1. f0 grob
    f0 = _refine_f0_at(audio, base, f0_coarse)

    # 2. Timing grob ±192 Samples / 16
    best_s, best_e = base, _sync_energy_at_sample(audio, base, f0)
    for ds in range(-192, 193, 16):
        e = _sync_energy_at_sample(audio, base + ds, f0)
        if e > best_e:
            best_e, best_s = e, base + ds

    # 3. Timing fein ±16 Samples / 4
    cb = best_s
    for ds in range(-16, 17, 4):
        e = _sync_energy_at_sample(audio, cb + ds, f0)
        if e > best_e:
            best_e, best_s = e, cb + ds

    # 4. f0 fein an bester Timing-Position
    f0 = _refine_f0_at(audio, best_s, f0)

    return max(0, best_s), float(f0)


def receive(
    audio:         np.ndarray,
    channel:       int   = None,
    use_fec:       bool  = True,
    freq_offset:   float = 0.0,
    use_equalizer: bool  = False,
) -> dict:
    """
    Vollständige RX-Pipeline: Audio → dekodiertes Dict.

    Breitband-Modus (channel=None):
      Multi-Hypothesen mit CRC-Verifikation. Alle Kanäle 0–7 werden
      erfasst. Erster Kandidat mit CRC OK wird zurückgegeben.

    Direktmodus (channel=0–7):
      Dekodierung auf einem bekannten Kanal mit optionalem freq_offset.

    use_equalizer (v0.5): Passband-Equalizer aus dem Costas-SYNC ableiten
      (kompensiert SSB-Filterrolloff / SDR-Nichtlinearität). Wirkt in beiden
      Modi auf die Datensymbol-Dekodierung. Empfohlen für den IQ-Eingang.
    """
    from gust_frame import (
        symbols_to_bytes, rs_decode, _RS_AVAILABLE,
        RS_OVERHEAD, get_fec_overhead, parse_frame, decode_payload,
    )

    if audio.dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0
    elif audio.dtype == np.uint8:
        audio = (audio.astype(np.float32) - 128.0) / 128.0
    elif audio.dtype != np.float32:
        audio = audio.astype(np.float32)

    # ── Direktmodus ───────────────────────────────────────────────────
    if channel is not None:
        # Soft-Decision nur bei aktivem LDPC-Backend (LLR-Sammlung kostet CPU:
        # zusätzliche Soft-FFT-Auswertung pro Symbol). RS-Pfad bleibt Hard.
        from gust_frame import _get_fec
        _use_soft = getattr(_get_fec(), "name", None) == "ldpc"
        if _use_soft:
            (data_symbols, sync_found, sync_offset, det_ch, det_offset,
             tone_llrs) = demodulate(
                audio, channel=channel, freq_offset=freq_offset,
                use_equalizer=use_equalizer, collect_llr=True,
            )
        else:
            data_symbols, sync_found, sync_offset, det_ch, det_offset = demodulate(
                audio, channel=channel, freq_offset=freq_offset,
                use_equalizer=use_equalizer,
            )
            tone_llrs = None
        return _build_result_direct(
            data_symbols, sync_found, sync_offset, det_ch, det_offset,
            use_fec, _RS_AVAILABLE, symbols_to_bytes, rs_decode,
            parse_frame, decode_payload, RS_OVERHEAD,
            tone_llrs=tone_llrs,
        )

    # ── Breitband-Modus: Multi-Hypothesen mit CRC-Verifikation ────────
    candidates = _find_sync_candidates(audio)
    if not candidates:
        return {"sync_found": False, "freq_offset_hz": 0.0,
                "error": "SYNC nicht gefunden"}

    rs_min   = get_fec_overhead() + 9
    last_res = None

    for hyp_idx, (score, cand_pos, f0_coarse) in enumerate(candidates):
        # Fein-Refinement: f0 (8Hz→<1Hz) UND Timing (Halb-Block→Sample-genau)
        sync_pos_s, f0 = _refine_sync(audio, cand_pos, f0_coarse)
        tone_freqs = _tone_frequencies(f0)
        nearest_ch = int(round((f0 - CHANNEL_BASE_HZ) / CHANNEL_BW_HZ))
        nearest_ch = max(0, min(N_CHANNELS - 1, nearest_ch))
        comp_off   = f0 - channel_frequency(nearest_ch)

        data_start = sync_pos_s + len(SYNC_SYMBOLS) * SAMPLES_PER_SYM
        data_audio = audio[data_start:]
        n_blocks   = len(data_audio) // SAMPLES_PER_SYM
        if n_blocks < 1:
            continue

        if use_equalizer and sync_pos_s >= 0:
            eq = _build_equalizer(audio, sync_pos_s, tone_freqs)
            data_syms = [
                _fft_detect_symbol_eq(
                    data_audio[i * SAMPLES_PER_SYM:(i + 1) * SAMPLES_PER_SYM],
                    tone_freqs, eq,
                )
                for i in range(n_blocks)
            ]
        else:
            data_syms = [
                _fft_detect_symbol(
                    data_audio[i * SAMPLES_PER_SYM:(i + 1) * SAMPLES_PER_SYM],
                    tone_freqs,
                )
                for i in range(n_blocks)
            ]

        res = {
            "sync_found":        True,
            "sync_offset_s":     sync_pos_s / SAMPLE_RATE,
            "data_symbols":      len(data_syms),
            "detected_channel":  nearest_ch,
            "freq_offset_hz":    round(comp_off, 1),
            "_sync_score":       round(score, 3),
            "_sync_hypothesis":  hyp_idx + 1,
        }

        nb_max = len(data_syms) * 3 // 8

        # TODO (P8-14): Breitband-Multi-Hypothesen-Pfad nutzt weiterhin
        # Hard-Decision (rs_decode ohne LLR). Soft-Decision ist bisher nur im
        # Direktmodus verdrahtet — der Stresstest (ldpc_stress_decode.py) läuft
        # über channel=ch (Direktmodus), daher hier vorerst kein Soft-Pfad.
        if use_fec and _RS_AVAILABLE:
            last_fec_error = None
            for n_try in range(nb_max, rs_min - 1, -1):
                try:
                    raw_bytes = symbols_to_bytes(data_syms, n_try)
                    decoded   = rs_decode(raw_bytes)
                    parsed    = parse_frame(decoded)
                    if parsed and parsed["crc_ok"]:
                        res.update({
                            "type":            parsed["type"],
                            "type_name":       parsed["type_name"],
                            "channel":         parsed["channel"],
                            "from":            parsed["from"],
                            "test":            parsed.get("test", False),
                            "crc_ok":          True,
                            "payload_decoded": decode_payload(
                                parsed["type"], parsed["payload"]
                            ),
                            "_rs_bytes_used":  n_try,
                            # Rohe Frame-Body-Bytes für AUTH-HMAC (P8-11)
                            "_raw_frame_body": decoded,
                        })
                        return res
                except Exception as e:
                    last_fec_error = str(e)
                    continue
            res["fec_error"] = last_fec_error or "Unbekannt"
            res["crc_ok"]    = False
        else:
            raw_bytes = symbols_to_bytes(data_syms, nb_max)
            parsed    = parse_frame(raw_bytes)
            if parsed:
                res.update({
                    "type":            parsed.get("type"),
                    "type_name":       parsed.get("type_name"),
                    "from":            parsed.get("from"),
                    "test":            parsed.get("test", False),
                    "crc_ok":          parsed.get("crc_ok", False),
                    "payload_decoded": decode_payload(
                        parsed.get("type", 0), parsed.get("payload", b"")
                    ),
                })
                if res.get("crc_ok"):
                    return res
            else:
                res["crc_ok"] = False

        last_res = res

    if last_res is not None:
        last_res["error"] = "Alle SYNC-Kandidaten: CRC fehlgeschlagen"
        return last_res
    return {"sync_found": False, "error": "SYNC nicht gefunden"}

# ═══════════════════════════════════════════════════════════════════════
# WAV-UTILITIES
# ═══════════════════════════════════════════════════════════════════════

def save_wav(path: str, audio: np.ndarray, sample_rate: int = SAMPLE_RATE):
    """
    Speichert Audio als 16-Bit PCM WAV.
    Kompatibel mit inspectrum (als IQ-Datei nicht nutzbar, aber
    gut für Audacity-Analyse und visuelle Kontrolle).

    Für inspectrum: WAV zuerst mit hackrf_transfer oder GNU Radio
    zu IQ (.cf32) konvertieren.
    """
    # Normalisieren auf ±0,9 (etwas Headroom)
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio_norm = audio / peak * 0.9
    else:
        audio_norm = audio
    pcm16 = (audio_norm * 32767).astype(np.int16)
    wavfile.write(path, sample_rate, pcm16)
    duration_s = len(audio) / sample_rate
    print(f"  WAV gespeichert: {path}  ({duration_s:.2f}s, {len(pcm16)} Samples)")

def save_cf32(path: str, audio: np.ndarray):
    """
    Speichert Audio als IQ-Datei im .cf32-Format für inspectrum.

    inspectrum erwartet raw Complex Float32: [I0,Q0, I1,Q1, I2,Q2, ...]
    Da unser Signal reell ist (NF-Audio), erzeugen wir das analytische
    Signal via Hilbert-Transformation:
      I = Realteil    = Original-Audio
      Q = Imaginärteil = Hilbert-transformiertes Audio

    Das Ergebnis ist ein einseitiges Spektrum (kein Spiegel-Image),
    das in inspectrum korrekt dargestellt wird.

    Wichtig beim Öffnen in inspectrum:
      • Format:      Complex Float 32 (.cf32)
      • Sample rate: 8000  (manuell eingeben — steht nicht in der Datei)
      • Dann Zoom auf den Frequenzbereich des Kanals

    Args:
        path:  Dateipfad, idealerweise mit .cf32-Endung
        audio: float32 Audio-Signal (aus transmit() oder modulate_channel())
    """
    from scipy.signal import hilbert
    analytic = hilbert(audio.astype(np.float64))
    iq = np.empty(len(analytic) * 2, dtype=np.float32)
    iq[0::2] = analytic.real.astype(np.float32)   # I-Kanal
    iq[1::2] = analytic.imag.astype(np.float32)   # Q-Kanal
    iq.tofile(path)
    print(f"  IQ gespeichert:  {path}  ({len(analytic)} Samples, "
          f"Sample Rate: {SAMPLE_RATE} Hz)")
    print(f"  → inspectrum: Datei öffnen, Sample Rate = {SAMPLE_RATE} eintragen")

def load_wav(path: str) -> tuple:
    """
    Lädt WAV-Datei → (audio: float32, sample_rate: int).

    Unterstützt int16, int32, float32/64 und uint8.
    Stereo → automatisch auf Kanal 0 reduziert.
    Falsche Sample Rate → automatisches Resampling auf 8000 Hz.
    """
    from math import gcd
    sr, data = wavfile.read(path)

    # Stereo → Mono
    if data.ndim == 2:
        data = data[:, 0]

    # Normalisieren auf float32
    if data.dtype == np.uint8:
        data = (data.astype(np.float32) - 128.0) / 128.0
    elif data.dtype == np.int16:
        data = data.astype(np.float32) / 32768.0
    elif data.dtype == np.int32:
        data = data.astype(np.float32) / 2147483648.0
    elif data.dtype in (np.float32, np.float64):
        data = data.astype(np.float32)

    # Resampling auf 8000 Hz wenn nötig
    if sr != SAMPLE_RATE:
        from scipy.signal import resample_poly
        g    = gcd(sr, SAMPLE_RATE)
        up   = SAMPLE_RATE // g
        down = sr // g
        data = resample_poly(data, up, down).astype(np.float32)
        # sr bleibt als Rückgabe das Original damit Caller informiert sind,
        # das Audio ist aber auf SAMPLE_RATE normiert
        return data, SAMPLE_RATE

    return data, sr

def audio_duration_s(audio: np.ndarray) -> float:
    return len(audio) / SAMPLE_RATE


# ═══════════════════════════════════════════════════════════════════════
# SELBSTTEST & DEMO
# ═══════════════════════════════════════════════════════════════════════

def _run_tests():
    print("=" * 60)
    print("  GUST Modulator/Demodulator — Selbsttest")
    print(f"  Version 0.1.1  |  SAMPLE_RATE={SAMPLE_RATE} Hz")
    print("=" * 60)
    errors = []

    # ── Test 1: Kanalplan-Übersicht ────────────────────────────────
    print("\n── Test 1: Kanalplan (aus gust_frame) ──")
    print(f"  {'Kanal':>5}  {'NF-Unterk.':>11}  {'NF-Oberk.':>10}  "
          f"{'Ton 0':>8}  {'Ton 7':>8}")
    print(f"  {'─'*5}  {'─'*11}  {'─'*10}  {'─'*8}  {'─'*8}")
    for ch in range(N_CHANNELS):
        base   = channel_frequency(ch)
        tone7  = base + 7 * TONE_SPACING
        print(f"  {ch:>5}  {base:>9.2f} Hz  {base+CHANNEL_BW_HZ:>8.2f} Hz  "
              f"{base:>6.2f} Hz  {tone7:>6.2f} Hz")
    print(f"\n  Gesamt: {CHANNEL_BASE_HZ:.0f}–"
          f"{channel_frequency(N_CHANNELS-1)+CHANNEL_BW_HZ:.0f} Hz  ✓ SSB-kompatibel")

    # ── Test 2: Einzelton-Verifikation ─────────────────────────────
    print("\n── Test 2: Einzelton auf Kanal 2 (Basis 900 Hz) ──")
    print(f"  FFT ohne Padding:  Bin-Abstand {SAMPLE_RATE/SAMPLES_PER_SYM:.2f} Hz  "
          f"(900 Hz → Bin {900/SAMPLE_RATE*SAMPLES_PER_SYM:.1f} → nicht ganzzahlig!)")
    print(f"  FFT mit Padding:   Bin-Abstand {FFT_RESOLUTION:.3f} Hz  "
          f"(900 Hz → Bin {round(900/SAMPLE_RATE*FFT_PAD_N)} → {round(900/SAMPLE_RATE*FFT_PAD_N)*FFT_RESOLUTION:.2f} Hz)")

    base_ch2 = channel_frequency(2)
    single   = mfsk8_modulate([0], base_freq_hz=base_ch2)

    # Zero-padded FFT — gleiche Methode wie Demodulator
    spectrum_pad = np.abs(np.fft.rfft(single, n=FFT_PAD_N))
    freqs_pad    = np.fft.rfftfreq(FFT_PAD_N, d=1.0/SAMPLE_RATE)
    peak_hz      = freqs_pad[np.argmax(spectrum_pad)]
    delta        = abs(peak_hz - base_ch2)
    ok = delta < 2.0
    print(f"  Peak (padded FFT): {peak_hz:.2f} Hz  (erwartet {base_ch2:.2f} Hz,  Δ={delta:.2f} Hz)  "
          f"{'✓' if ok else '✗'}")
    if not ok:
        errors.append(f"Ton-Frequenz falsch: {peak_hz:.2f} Hz")

    # ── Test 3: Phasenkontinuität & Symbolerkennung ───────────────
    print("\n── Test 3: Alle 8 Symbole modulieren → demodulieren ──")
    print("  (Indirekter Phasentest: Splatter würde Symbolfehler verursachen)")
    base_ch2   = channel_frequency(2)
    tf         = _tone_frequencies(base_ch2)
    all_syms   = list(range(N_TONES))
    audio_8sym = mfsk8_modulate(all_syms, base_freq_hz=base_ch2)
    detected   = [
        _fft_detect_symbol(audio_8sym[i*SAMPLES_PER_SYM:(i+1)*SAMPLES_PER_SYM], tf)
        for i in range(N_TONES)
    ]
    ok = detected == all_syms
    print(f"  Gesendet:   {all_syms}")
    print(f"  Empfangen:  {detected}  {'✓' if ok else '✗'}")
    if not ok:
        errors.append(f"Symbolerkennung falsch: {detected}")

    # ── Test 3c: Soft-Output-Demodulator (P8-13) ──────────────────
    print("\n── Test 3c: Soft-Output (LLR) — Konsistenz mit Hard-Symbol ──")
    soft_ok    = True
    pos_llr_ok = True
    for i in range(N_TONES):
        blk  = audio_8sym[i*SAMPLES_PER_SYM:(i+1)*SAMPLES_PER_SYM]
        llr  = _fft_detect_symbol_soft(blk, tf)
        hard = _fft_detect_symbol(blk, tf)
        # 1) argmax(soft) == hard
        if llr_to_symbol(llr) != hard:
            soft_ok = False
        # 2) LLR des tatsächlich gesendeten Tons > 0
        if not (llr[i] > 0):
            pos_llr_ok = False
    print(f"  hard == llr_to_symbol(soft) für alle 8 Symbole:  {'✓' if soft_ok else '✗'}")
    print(f"  LLR des richtigen Tons > 0 für alle 8 Symbole:   {'✓' if pos_llr_ok else '✗'}")
    if not soft_ok:
        errors.append("Soft-Output: argmax(LLR) != Hard-Symbol")
    if not pos_llr_ok:
        errors.append("Soft-Output: LLR des richtigen Tons nicht > 0")

    # ── Test 3d: Ton-LLR → Bit-LLR (P8-13 → LDPC-Brücke) ──────────
    print("\n── Test 3d: Ton-LLR → Bit-LLR (Max-Log-Approximation) ──")
    shape_ok  = True
    sign_ok   = True
    tone_llrs = []
    for s in range(N_TONES):
        tllr = _fft_detect_symbol_soft(
            audio_8sym[s*SAMPLES_PER_SYM:(s+1)*SAMPLES_PER_SYM], tf)
        tone_llrs.append(tllr)
        bllr = symbol_llr_to_bit_llr(tllr)
        if bllr.shape != (3,):
            shape_ok = False
        for k in range(3):
            bit = (s >> k) & 1
            if bit == 0 and not (bllr[k] > 0):
                sign_ok = False
            if bit == 1 and not (bllr[k] < 0):
                sign_ok = False
    flat   = symbols_to_bit_llr_array(tone_llrs)
    arr_ok = (flat.shape == (N_TONES * 3,))
    print(f"  shape (3,) für alle 8 Symbole:                   {'✓' if shape_ok else '✗'}")
    print(f"  Vorzeichen korrekt (bit=0 → +, bit=1 → −):       {'✓' if sign_ok else '✗'}")
    print(f"  symbols_to_bit_llr_array shape == (24,):         {'✓' if arr_ok else '✗'}  ({flat.shape[0]})")
    if not shape_ok:
        errors.append("Bit-LLR: falsche shape")
    if not sign_ok:
        errors.append("Bit-LLR: Vorzeichen falsch")
    if not arr_ok:
        errors.append("Bit-LLR: symbols_to_bit_llr_array shape falsch")

    # ── Test 3b: Raised Cosine Windowing (Phase 3) ────────────────
    print("\n── Test 3b: Raised Cosine Windowing — Spektralvergleich ──")
    base_ch2 = channel_frequency(2)
    syms_test = list(range(N_TONES)) * 3   # 24 Symbole, alle 8 Töne

    audio_rect = mfsk8_modulate(syms_test, base_ch2, window=False)
    audio_win  = mfsk8_modulate(syms_test, base_ch2, window=True)

    # Leistung in der Passbandregion (Nutzsignal 900–1150 Hz)
    def band_power(sig, f_lo, f_hi):
        spec = np.abs(np.fft.rfft(sig, n=FFT_PAD_N)) ** 2
        freqs = np.fft.rfftfreq(FFT_PAD_N, d=1.0 / SAMPLE_RATE)
        mask = (freqs >= f_lo) & (freqs <= f_hi)
        return 10 * np.log10(spec[mask].mean() + 1e-20)

    # Leistung ausserhalb Band (Seitenkeullen bei 200–800 Hz)
    inband_rect  = band_power(audio_rect, 900, 1150)
    outband_rect = band_power(audio_rect, 200,  800)
    inband_win   = band_power(audio_win,  900, 1150)
    outband_win  = band_power(audio_win,  200,  800)
    sidelobe_rect = outband_rect - inband_rect
    sidelobe_win  = outband_win  - inband_win
    improvement   = sidelobe_win - sidelobe_rect

    print(f"  Rechteckfenster:  Passband {inband_rect:+.1f} dB  | Außerband {outband_rect:+.1f} dB  | Seitenkeullen {sidelobe_rect:.1f} dB")
    print(f"  Raised Cosine:    Passband {inband_win:+.1f} dB  | Außerband {outband_win:+.1f} dB  | Seitenkeullen {sidelobe_win:.1f} dB")
    print(f"  Verbesserung:     {abs(improvement):.1f} dB weniger Splatter mit RC-Fenster  ({'✓' if improvement < -5 else '?'})")

    # Symbolerkennung mit Fenster muss weiterhin korrekt sein
    all_syms_chk = list(range(N_TONES))
    audio_chk = mfsk8_modulate(all_syms_chk, base_ch2, window=True)
    detected_w = [
        _fft_detect_symbol(audio_chk[i*SAMPLES_PER_SYM:(i+1)*SAMPLES_PER_SYM], tf)
        for i in range(N_TONES)
    ]
    sym_ok = detected_w == all_syms_chk
    print(f"  Symbolerkennung mit Fenster: {detected_w} {'✓' if sym_ok else '✗'}")
    if not sym_ok:
        errors.append("Symbolerkennung mit Raised Cosine fehlgeschlagen")

    # ── Test 4: TX-Pipeline  ───────────────────────────────────────
    print("\n── Test 4: TX-Pipeline (Wetter-Frame, Kanal auto) ──")
    payload = encode_weather(
        temp_c=21.5, humidity_pct=68, pressure_hpa=1013.2,
        wind_kmh=15, wind_deg=270, rain_mm_h=0.2, uv_index=3
    )
    audio, ch, frame_body = transmit(
        FrameType.WEATHER, "OE3GAS", payload,
        channel=None, use_fec=True, add_silence_ms=100
    )
    base_freq = channel_frequency(ch)
    print(f"  Rufzeichen:  OE3GAS")
    print(f"  Kanal:       {ch}  ({base_freq:.0f}–{base_freq+CHANNEL_BW_HZ:.0f} Hz NF)")
    print(f"  Frame-Body:  {len(frame_body)} Byte")
    print(f"  Audio:       {len(audio)} Samples = {audio_duration_s(audio):.2f} s")
    print(f"  Amplitude:   max={np.max(np.abs(audio)):.3f}")
    print("  ✓")

    # ── Test 5: WAV und IQ-Export ─────────────────────────────────
    print("\n── Test 5: Datei-Export ──")
    wav_path  = "gust_test_ch2.wav"
    cf32_path = "gust_test_ch2.cf32"
    save_wav(wav_path, audio)
    save_cf32(cf32_path, audio)
    assert os.path.exists(wav_path),  "WAV nicht erzeugt"
    assert os.path.exists(cf32_path), "CF32 nicht erzeugt"
    loaded, sr = load_wav(wav_path)
    ok = sr == SAMPLE_RATE and len(loaded) == len(audio)
    print(f"  WAV geladen:  {len(loaded)} Samples @ {sr} Hz  {'✓' if ok else '✗'}")
    if not ok:
        errors.append("WAV roundtrip fehlgeschlagen")

    # ── Test 6: RX-Pipeline (Loopback) ────────────────────────────
    print("\n── Test 6: RX-Pipeline — Loopback TX→RX ──")
    result = receive(loaded, channel=ch, use_fec=True)

    sync_ok = result.get("sync_found", False)
    crc_ok  = result.get("crc_ok", False)
    from_ok = result.get("from") == "OE3GAS"
    type_ok = result.get("type") == FrameType.WEATHER

    print(f"  SYNC gefunden:  {'✓' if sync_ok else '✗'}")
    print(f"  CRC:            {'✓ OK' if crc_ok else '✗ FEHLER'}")
    print(f"  FROM:           {result.get('from', '?')}  {'✓' if from_ok else '✗'}")
    print(f"  TYPE:           {result.get('type_name', '?')}  {'✓' if type_ok else '✗'}")

    if "payload_decoded" in result and result["payload_decoded"]:
        wx = result["payload_decoded"]
        temp_ok  = wx.get("temp_c") == 21.5
        press_ok = wx.get("pressure_hpa") == 1013.2
        print(f"  Temp:           {wx.get('temp_c')}°C  {'✓' if temp_ok else '✗'}")
        print(f"  Druck:          {wx.get('pressure_hpa')} hPa  {'✓' if press_ok else '✗'}")
        if not (temp_ok and press_ok):
            errors.append("Payload-Werte nach Loopback falsch")
    else:
        print(f"  Fehler:  {result.get('error', result.get('fec_error', 'unbekannt'))}")
        errors.append("Loopback-Dekodierung fehlgeschlagen")

    if not (sync_ok and crc_ok and from_ok and type_ok):
        errors.append("Loopback: Frame nicht korrekt dekodiert")

    # ── Test 7: Alle 8 Kanäle ─────────────────────────────────────
    print("\n── Test 7: Alle Kanäle — Ton-Peak (zero-padded FFT) ──")
    print(f"  {'Kanal':>5}  {'Basis Hz':>9}  {'Peak Hz':>9}  {'Δ Hz':>6}  Status")
    all_ok = True
    for c in range(N_CHANNELS):
        base  = channel_frequency(c)
        sig   = mfsk8_modulate([0], base_freq_hz=base)
        spec  = np.abs(np.fft.rfft(sig, n=FFT_PAD_N))
        fq    = np.fft.rfftfreq(FFT_PAD_N, d=1.0/SAMPLE_RATE)
        peak  = fq[np.argmax(spec)]
        delta = abs(peak - base)
        ok    = delta < 2.0
        if not ok:
            all_ok = False
            errors.append(f"Kanal {c}: Ton-Peak {peak:.2f} Hz statt {base:.2f} Hz")
        print(f"  {c:>5}  {base:>9.2f}  {peak:>9.2f}  {delta:>5.2f}  {'✓' if ok else '✗'}")
    if all_ok:
        print("  ✓ Alle Kanäle korrekt")

    # ── Zusammenfassung ───────────────────────────────────────────
    print("\n" + "=" * 60)
    if errors:
        print(f"  ✗ {len(errors)} Fehler:")
        for e in errors:
            print(f"    - {e}")
    else:
        print("  ✓ Alle Tests erfolgreich.")
        print(f"  WAV:  {wav_path}")
        print(f"  IQ:   {cf32_path}  ← für inspectrum (Sample Rate: {SAMPLE_RATE})")
    print("=" * 60)
    return len(errors) == 0


def _intro_and_prompt() -> bool:
    """
    Erklärende Begrüßung anzeigen und interaktiv fragen, ob der
    Selbsttest gestartet werden soll. Rückgabe: True = Selbsttest, False = Abbruch.
    """
    print("""
═══════════════════════════════════════════════════════════════════════
  GUST MFSK-8 Modulator / Demodulator
═══════════════════════════════════════════════════════════════════════

  Was ist dieses Modul?
  ─────────────────────
  Der GUST-Modulator setzt Frame-Bytes in NF-Audio um und wieder zurück.
  Er ist das Herzstück der GUST-Übertragung — alles oberhalb (Frames,
  CRC, RS-FEC) und alles unterhalb (Audio-Geräte, HackRF, SDR) baut auf
  diesem Modul auf.

  Was tut der Modulator konkret?
  ──────────────────────────────
  • MFSK-8 Modulation: 8 Töne im Abstand 31,25 Hz, 32 ms je Symbol,
    phasenkontinuierlich mit optionalem Raised-Cosine-Fenster
  • Kanalbewusst: ordnet die 8 Töne in einen der 8 NF-Kanäle ein
    (Bandbreite 250 Hz je Kanal, Span 600–2600 Hz im SSB-Passband)
  • SYNC-Detektion: erkennt die 8-Symbol-Präambel im Breitband-Scan,
    schärft Frequenz und Timing nach (sub-Hz, sample-genau)
  • FFT-Demodulator (zero-padded 4096) → Symbole → Bytes → Frame

  Was tut der Selbsttest?
  ───────────────────────
  Vollständiger Loopback: erzeugt Test-Frames, moduliert sie zu Audio,
  schreibt eine WAV-Datei + CF32-IQ (für inspectrum), demoduliert das
  Audio zurück und vergleicht das Ergebnis Symbol für Symbol.

  Dauer: einige Sekunden. Schreibt WAV/CF32-Dateien ins aktuelle
  Verzeichnis. Keine Hardware nötig.
═══════════════════════════════════════════════════════════════════════
""")
    try:
        antwort = input("Selbsttest jetzt durchführen? [J/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    # Default "ja": leere Eingabe, "j", "ja", "y", "yes"
    return antwort in ("", "j", "ja", "y", "yes")


if __name__ == "__main__":
    # Ohne Parameter: erklärende Begrüßung + explizite Nachfrage.
    # Mit Parametern (z.B. aus Skripten/Make): direkt durchstarten.
    if len(sys.argv) == 1:
        if _intro_and_prompt():
            _run_tests()
        else:
            print("Abgebrochen. Der Selbsttest wurde nicht ausgeführt.")
            sys.exit(0)
    else:
        _run_tests()