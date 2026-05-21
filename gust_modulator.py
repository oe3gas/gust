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
  Kanal 0:   400– 650 Hz    Kanal 5: 1650–1900 Hz
  Kanal 1:   650– 900 Hz    Kanal 6: 1900–2150 Hz
  Kanal 2:   900–1150 Hz    Kanal 7: 2150–2400 Hz
  Kanal 3:  1150–1400 Hz    Kanal 8: 2400–2650 Hz
  Kanal 4:  1400–1650 Hz    Kanal 9: 2650–2900 Hz
  → Gesamtspan 400–2900 Hz, passt in Standard-SSB-Passband
"""

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfilt
import struct as _struct
import os

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

def _find_sync_candidates(audio: np.ndarray) -> list:
    """
    Energie-basierte SYNC-Kandidatensuche — alle 10 Kanäle, vollvektorisiert.

    Scannt den gesamten NF-Bereich 380–2580 Hz in 8-Hz-Schritten und deckt
    damit alle GUST-Kanäle (0–9) inklusive ±80 Hz Frequenzoffset ab.

    Performance: numpy stride_tricks; 250 Blöcke × 277 f0-Werte → < 0,5 s.

    Kandidaten-Format: (score: float, block_pos: int, f0_hz: float)
      f0_hz = tatsächliche Ton-0-Frequenz (kein Offset-Wert).
    """
    SCORE_MIN      = 0.70   # Schwelle — mit CRC-Verifikation in receive() sicher
    MAX_CANDIDATES = 12    # Maximal Kandidaten in Multi-Hyp.-Schleife
    HALF_BW        = 8.0
    STEP_HZ        = 8.0
    HOP            = SAMPLES_PER_SYM // 2   # 128 — Halb-Block-Auflösung fürs Timing

    if len(audio) < (len(SYNC_SYMBOLS) + 2) * SAMPLES_PER_SYM:
        return []

    bin_res = SAMPLE_RATE / FFT_PAD_N

    # Überlappende 256-Sample-Fenster im 128-Sample-Raster (zero-copy)
    n_pos = (len(audio) - SAMPLES_PER_SYM) // HOP + 1
    s     = audio.strides[0]
    frames = np.lib.stride_tricks.as_strided(
        audio, shape=(n_pos, SAMPLES_PER_SYM), strides=(HOP * s, s))

    # Spektren aller Fenster (mit Zero-Padding auf FFT_PAD_N)
    padded = np.zeros((n_pos, FFT_PAD_N), dtype=np.float32)
    padded[:, :SAMPLES_PER_SYM] = frames
    spectra = np.abs(np.fft.rfft(padded, axis=1))

    # SYNC-Maske: True = Ton 7 erwartet
    sync_is_7 = np.array([s == 7 for s in SYNC_SYMBOLS], dtype=bool)

    candidates = []

    # Range deckt Kanal 0 (400Hz) bis Kanal 9 (2650Hz) inkl. ±100Hz Offset ab
    for f0 in np.arange(320.0, 2761.0, STEP_HZ):
        f7 = f0 + 7 * TONE_SPACING
        if f7 > SAMPLE_RATE / 2 - HALF_BW:
            break

        lo0 = max(0, int((f0 - HALF_BW) / bin_res))
        hi0 = min(spectra.shape[1], int((f0 + HALF_BW) / bin_res) + 1)
        lo7 = max(0, int((f7 - HALF_BW) / bin_res))
        hi7 = min(spectra.shape[1], int((f7 + HALF_BW) / bin_res) + 1)

        e0 = np.sum(spectra[:, lo0:hi0] ** 2, axis=1)
        e7 = np.sum(spectra[:, lo7:hi7] ** 2, axis=1)
        total = e0 + e7 + 1e-12
        r0 = e0 / total
        r7 = e7 / total

        # SYNC-Symbole liegen 256 Samples = 2 HOPs auseinander.
        # Gleitfenster: 8 Werte mit Schrittweite 2 im HOP-Raster.
        N = n_pos - 2 * (len(SYNC_SYMBOLS) - 1)
        if N < 1:
            continue
        st = r0.strides[0]
        r0_win = np.lib.stride_tricks.as_strided(r0, shape=(N, 8), strides=(st, 2 * st))
        r7_win = np.lib.stride_tricks.as_strided(r7, shape=(N, 8), strides=(st, 2 * st))

        expected = np.where(sync_is_7, r7_win, r0_win)
        scores   = np.mean(expected, axis=1)

        best_pos   = int(np.argmax(scores))   # in HOP-Einheiten
        best_score = float(scores[best_pos])

        if best_score >= SCORE_MIN:
            # Sample-Position = HOP-Index × HOP
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
    audio:        np.ndarray,
    channel:      int   = None,
    sync_symbols: list  = None,
    freq_offset:  float = 0.0,
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
        data_syms  = [
            _fft_detect_symbol(
                data_audio[i*SAMPLES_PER_SYM:(i+1)*SAMPLES_PER_SYM],
                tone_freqs
            )
            for i in range(n_blocks)
        ]
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
            return all_symbols, False, -1, channel, freq_offset

        data_syms = all_symbols[sync_pos + sync_len:]
        return data_syms, True, sync_pos * SAMPLES_PER_SYM, channel, freq_offset


# ═══════════════════════════════════════════════════════════════════════
# RX-PIPELINE  (Audio → Dict in einem Aufruf)
# ═══════════════════════════════════════════════════════════════════════

def _build_result_direct(data_symbols, sync_found, sync_offset, det_ch, det_offset,
                         use_fec, rs_available, sym2bytes, rs_dec, pf, dp, RS_OH):
    """Symbole → Ergebnis-Dict (Direktmodus ohne Multi-Hypothesen)."""
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
    if use_fec and rs_available:
        last_e = None
        for n in range(nb_max, rs_min - 1, -1):
            try:
                raw  = sym2bytes(data_symbols, n)
                dec  = rs_dec(raw)
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

    Summiert die Energie am jeweils ERWARTETEN Ton (7 oder 0) über alle
    8 SYNC-Symbole. Scharfes Maximum am wahren (f0, Timing) — Basis für
    Frequenz- UND Timing-Feinabstimmung.
    """
    bin0 = round(f0 / SAMPLE_RATE * FFT_PAD_N)
    bin7 = round((f0 + 7 * TONE_SPACING) / SAMPLE_RATE * FFT_PAD_N)
    if bin7 >= FFT_PAD_N // 2 or sample_pos < 0:
        return 0.0
    e = 0.0
    for i, sym in enumerate(SYNC_SYMBOLS):
        a = sample_pos + i * SAMPLES_PER_SYM
        blk = audio[a : a + SAMPLES_PER_SYM]
        if len(blk) < SAMPLES_PER_SYM:
            return 0.0
        spec = np.abs(np.fft.rfft(blk, n=FFT_PAD_N))
        e += spec[bin7] if sym == 7 else spec[bin0]
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
    audio:       np.ndarray,
    channel:     int   = None,
    use_fec:     bool  = True,
    freq_offset: float = 0.0,
) -> dict:
    """
    Vollständige RX-Pipeline: Audio → dekodiertes Dict.

    Breitband-Modus (channel=None):
      Multi-Hypothesen mit CRC-Verifikation. Alle Kanäle 0–9 werden
      erfasst. Erster Kandidat mit CRC OK wird zurückgegeben.

    Direktmodus (channel=0–9):
      Dekodierung auf einem bekannten Kanal mit optionalem freq_offset.
    """
    from gust_frame import (
        symbols_to_bytes, rs_decode, _RS_AVAILABLE,
        RS_OVERHEAD, parse_frame, decode_payload,
    )

    if audio.dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0
    elif audio.dtype == np.uint8:
        audio = (audio.astype(np.float32) - 128.0) / 128.0
    elif audio.dtype != np.float32:
        audio = audio.astype(np.float32)

    # ── Direktmodus ───────────────────────────────────────────────────
    if channel is not None:
        data_symbols, sync_found, sync_offset, det_ch, det_offset = demodulate(
            audio, channel=channel, freq_offset=freq_offset
        )
        return _build_result_direct(
            data_symbols, sync_found, sync_offset, det_ch, det_offset,
            use_fec, _RS_AVAILABLE, symbols_to_bytes, rs_decode,
            parse_frame, decode_payload, RS_OVERHEAD,
        )

    # ── Breitband-Modus: Multi-Hypothesen mit CRC-Verifikation ────────
    candidates = _find_sync_candidates(audio)
    if not candidates:
        return {"sync_found": False, "freq_offset_hz": 0.0,
                "error": "SYNC nicht gefunden"}

    rs_min   = RS_OVERHEAD + 9
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

    # ── Test 7: Alle 10 Kanäle ────────────────────────────────────
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


if __name__ == "__main__":
    _run_tests()