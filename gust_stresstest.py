#!/usr/bin/env python3
"""
GUST — Parallel-Stresstest-Generator                        OE3GAS
═══════════════════════════════════════════════════════════════════════
Erzeugt eine WAV-Datei mit GLEICHZEITIG laufenden randomisierten Frames
auf allen 8 Kanälen. Frames starten zu zufälligen Zeitpunkten und
überlappen sich kanalübergreifend — wie auf einer echten KW-Frequenz.

Konzept:
  • Pro Kanal wird eine eigene Timeline erzeugt (Frames mit zufälligen
    Startzeitpunkten, keine Überlappung INNERHALB eines Kanals)
  • Alle 8 Kanal-Timelines werden zu EINEM NF-Signal addiert
  • Emergency-Frames (0x20) werden automatisch auf einem zweiten Kanal
    gespiegelt (Dual-Channel Diversity, maximaler Kanalabstand)

Kanal-Amplituden:
  Jeder Kanal ×(1/N_CHANNELS) → kein Clipping bei voller Belegung.

Verwendung:
  python gust_stresstest.py
  python gust_stresstest.py --duration 60 --frames-per-ch 5
  python gust_stresstest.py --duration 30 --seed 42
  python gust_stresstest.py --out meintest

Ausgabe:
  gust_stress_<timestamp>.wav   — 8 kHz PCM int16, normiert
  gust_stress_<timestamp>.cf32  — IQ-Datei für inspectrum
  gust_stress_<timestamp>.csv   — Timeline aller Frames
═══════════════════════════════════════════════════════════════════════
"""

import argparse
import csv
import json
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import numpy as np

# ── GUST-Module ────────────────────────────────────────────────────────
try:
    from gust_frame import (
        N_CHANNELS,
        FrameType,
        build_frame,
        auth_tag,          # HMAC-14 für AUTH-Frame
        encode_auth,       # AUTH-Payload (20 Byte)
        encode_weather,
        encode_position,
        encode_station_tlm,
        encode_emergency_beacon,
        encode_emerg_rsrc,
        encode_cq,
        fragment_text,
        frame_to_symbol_stream,
        POS_FLAG_GPS_FIX,
        PRIO_EMERGENCY, PRIO_URGENT, PRIO_WELFARE,
        INJR_UNKNOWN, INJR_MINOR, INJR_SERIOUS, INJR_CRITICAL,
        FLAG_GPS, FLAG_BAT, FLAG_RLY,
        RSRC_MEDICAL, RSRC_TRANSPORT,
        EVTYPE_FIRE, EVTYPE_FLOOD, EVTYPE_MEDICAL, EVTYPE_SAR,
        EVTYPE_QUAKE, EVTYPE_ACCIDENT, EVTYPE_STORM, EVTYPE_MCI,
    )
    from gust_modulator import (
        SAMPLE_RATE,
        modulate_channel,
    )
    from scipy.io import wavfile
except ImportError as e:
    print(f"FEHLER: Modul nicht gefunden: {e}")
    print("Bitte das Script im GUST-Projektverzeichnis ausfuehren.")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════
# KONSTANTEN
# ══════════════════════════════════════════════════════════════════════

MIN_SLOT_S = 11.0   # Mindest-Slot-Größe: 2 × MAX_FRAME_S (~5,5 s)
                    # Unterschreitung → zeitliche Kollisionen zwischen
                    # Kanälen → Dekodierrate sinkt ohne Rauschen

# ══════════════════════════════════════════════════════════════════════
# DATENSTRUKTUREN
# ══════════════════════════════════════════════════════════════════════

@dataclass
class FrameEvent:
    """Ein modulierter Frame auf der Zeitachse."""
    start_s:    float
    channel:    int
    channel_b:  Optional[int]   # nur bei EMERG_BEACON dual
    frame_type: str
    callsign:   str
    audio:      np.ndarray      # float32, ±1.0
    duration_s: float
    # Neu (nur für AUTH-Frames):
    auth_ref_nr:    Optional[int]   = None   # Nr. des Daten-Frames (1-basiert)
    auth_key_id:    Optional[int]   = None   # KEY_ID
    auth_timestamp: Optional[int]   = None   # Unix-Timestamp für Verifikation
    auth_type:      Optional[str]   = None   # "known" | "unknown" | None
    # Nur intern (nicht im CSV, nicht im Mixer relevant):
    _frame_body:    Optional[bytes] = None   # raw body für auth_tag()

# ══════════════════════════════════════════════════════════════════════
# CALLSIGN-POOL
# ══════════════════════════════════════════════════════════════════════

OE_CALLSIGNS = [
    "OE3GAS", "OE1XTU", "OE5XAR", "OE7XZR", "OE2XGR",
    "OE3XDA", "OE4XLC", "OE6XAR", "OE8XKK", "OE9XPI",
    "OE1XIS", "OE5XIM", "OE3XWW", "OE2XUM", "OE4XNB",
    "OE6XMF", "OE7XBT", "OE8XZQ", "OE1ABC", "OE3DEF",
    "OE5RFP", "OE1KFR", "OE3OMA", "OE2UKL", "OE7DBH",
]

def rand_call() -> str:
    return random.choice(OE_CALLSIGNS)

# ══════════════════════════════════════════════════════════════════════
# PAYLOAD-GENERATOREN
# ══════════════════════════════════════════════════════════════════════

def gen_weather() -> bytes:
    return encode_weather(
        temp_c       = round(random.uniform(-15.0, 38.0), 1),
        humidity_pct = random.randint(25, 98),
        pressure_hpa = round(random.uniform(960.0, 1050.0), 1),
        wind_kmh     = random.randint(0, 100),
        wind_deg     = random.randint(0, 359),
        rain_mm_h    = round(random.uniform(0.0, 25.0), 1),
        uv_index     = random.randint(0, 11),
        flags        = 0x03,
    )

def gen_position() -> bytes:
    return encode_position(
        lat_deg     = round(random.uniform(46.4, 49.0), 5),
        lon_deg     = round(random.uniform(9.5, 17.2), 5),
        alt_m       = random.randint(100, 3800),
        speed_kmh   = random.randint(0, 150),
        heading_deg = random.randint(0, 359),
        timestamp   = random.randint(0, 65535),
        flags       = POS_FLAG_GPS_FIX,
    )

def gen_station() -> bytes:
    return encode_station_tlm(
        voltage_mv  = random.randint(11500, 14200),
        current_ma  = random.randint(50, 2500),
        temp_c      = round(random.uniform(15.0, 65.0), 1),
        cpu_pct     = random.randint(1, 95),
        uptime_min  = random.randint(0, 50000),
        flags       = random.randint(0, 7),
    )

def gen_text() -> bytes:
    texts = [
        "73 de OE3GAS", "QSL?", "Test GUST v0.5", "SNR OK",
        "CQ KW", "Relais aktiv", "OE3Mode laeuft", "Hello HF",
        "Feld-Tag 2026", "Link up", "QRV?", "RST 599",
        "Wetter OK", "Batterie schwach", "Relais QRT",
    ]
    dest = rand_call()
    text = random.choice(texts)
    frags = fragment_text(text, dest_call=dest, seq_nr=random.randint(0, 255))
    return frags[0]

def gen_emerg_beacon() -> bytes:
    event_types = [EVTYPE_FIRE, EVTYPE_FLOOD, EVTYPE_MEDICAL, EVTYPE_SAR,
                   EVTYPE_QUAKE, EVTYPE_ACCIDENT, EVTYPE_STORM, EVTYPE_MCI]
    snippets    = ["HELP", "SOS", "HURT", "FIRE", "FLOD", "SRCH", "EVAC", "MCI"]
    status = FLAG_GPS \
           | (FLAG_BAT if random.random() > 0.3 else 0) \
           | (FLAG_RLY if random.random() > 0.5 else 0)
    return encode_emergency_beacon(
        lat_deg        = round(random.uniform(46.4, 49.0), 5),
        lon_deg        = round(random.uniform(9.5, 17.2), 5),
        persons        = random.randint(1, 50),
        event_type     = random.choice(event_types),
        priority       = random.choice([PRIO_EMERGENCY, PRIO_URGENT, PRIO_WELFARE]),
        injury         = random.choice([INJR_UNKNOWN, INJR_MINOR, INJR_SERIOUS, INJR_CRITICAL]),
        status_flags   = status,
        resource_flags = random.randint(1, 255),
        timestamp      = random.randint(0, 65535),
        text_snippet   = random.choice(snippets),
    )

def gen_emerg_rsrc() -> bytes:
    return encode_emerg_rsrc(
        event_type_echo  = random.randint(1, 15),
        avail_resources  = random.randint(1, 255),
        eta_minutes      = random.randint(0, 60),
        acked_callsign   = rand_call(),
        relay_confirm    = random.random() > 0.5,
    )

# ══════════════════════════════════════════════════════════════════════
# FRAME-TYPEN-POOL (name, FrameType-Konstante, gen_fn, Gewicht)
# ══════════════════════════════════════════════════════════════════════

FRAME_POOL = [
    ("WEATHER",      FrameType.WEATHER,     gen_weather,      5),
    ("POSITION",     FrameType.POSITION,    gen_position,     4),
    ("STATION_TLM",  FrameType.STATION_TLM, gen_station,      2),
    ("TEXT",         FrameType.TEXT,        gen_text,         3),
    ("CQ",           FrameType.CQ,          None,             2),
    ("EMERG_RSRC",   FrameType.EMERG_RSRC,  gen_emerg_rsrc,   1),
    ("EMERG_BEACON", FrameType.EMERG_BEACON,gen_emerg_beacon, 1),
]
_WEIGHTS = [p[3] for p in FRAME_POOL]

# ══════════════════════════════════════════════════════════════════════
# AUDIO-MODULATION
# ══════════════════════════════════════════════════════════════════════

def modulate_frame(frame_type_int: int, callsign: str,
                   payload: bytes, channel: int):
    """Payload -> (moduliertes float32-Audio, Frame-Body).

    Gibt zusätzlich den rohen Frame-Body (build_frame-Ausgabe) zurück,
    der für die AUTH-HMAC-Berechnung (auth_tag) gebraucht wird.
    """
    body    = build_frame(frame_type_int, callsign, payload, channel)
    symbols = frame_to_symbol_stream(body, use_fec=True)
    audio   = modulate_channel(symbols, channel, window=True)
    return audio, body

# ══════════════════════════════════════════════════════════════════════
# TIMELINE-GENERATOR PRO KANAL
# ══════════════════════════════════════════════════════════════════════

def build_channel_timeline(
    channel:        int,
    total_duration: float,
    frames_per_ch:  int,
    all_events:     List[FrameEvent],
    auth_config:    Optional[dict] = None,   # {key,key_id,ratio,pause_s} oder None
):
    """
    Erzeugt `frames_per_ch` FrameEvents fuer einen Kanal.

    Innerhalb des Kanals: Frames ueberlappen sich NICHT.
    Ueber Kanäle hinweg: volle zeitliche Ueberlappung.

    Emergency-Beacon-Frames werden zusaetzlich als Dual-Channel-Kopie
    auf dem Gegenkanal eingetragen (eigener FrameEvent, gleicher Start).
    """
    # Gesamtzeit in Slots aufteilen; Frame startet zufaellig im Slot
    slot_s = total_duration / frames_per_ch

    # Aktuelles Ende des letzten Frames auf diesem Kanal (kein Ueberlapp)
    cursor_s = 0.0

    for slot_idx in range(frames_per_ch):
        slot_center = slot_idx * slot_s + slot_s * 0.5

        # Frame-Typ waehlen
        entry = random.choices(FRAME_POOL, weights=_WEIGHTS, k=1)[0]
        name, ftype, fn, _ = entry
        callsign = rand_call()

        # Payload
        if name == "CQ":
            payload = encode_cq(callsign, flags=0)
        elif fn is not None:
            payload = fn()
        else:
            continue

        # Modulation
        try:
            audio, body = modulate_frame(ftype, callsign, payload, channel)
        except Exception as e:
            print(f"  WARNUNG ch{channel} {name}: {e}")
            continue

        dur_s = len(audio) / SAMPLE_RATE

        # Startzeitpunkt: zufaellig um Slot-Mitte, aber nie vor cursor_s
        # und so, dass Frame vollstaendig in [0, total_duration+1] liegt
        earliest = max(cursor_s, slot_center - slot_s * 0.4)
        latest   = min(total_duration - dur_s,
                       slot_center + slot_s * 0.3)
        if latest < earliest:
            latest = earliest
        start_s = random.uniform(earliest, latest)

        cursor_s = start_s + dur_s  # Kanal-Cursor vorschieben

        ch_b = None

        # ── Dual-Channel fuer Emergency-Beacon ──────────────────────
        if name == "EMERG_BEACON":
            ch_b = (channel + N_CHANNELS // 2) % N_CHANNELS
            try:
                audio_b, _ = modulate_frame(ftype, callsign, payload, ch_b)
                all_events.append(FrameEvent(
                    start_s    = start_s,
                    channel    = ch_b,
                    channel_b  = None,
                    frame_type = f"EMERG_BCN(dual<-ch{channel})",
                    callsign   = callsign,
                    audio      = audio_b,
                    duration_s = len(audio_b) / SAMPLE_RATE,
                ))
            except Exception as e:
                print(f"  WARNUNG Dual-Kanal ch{ch_b}: {e}")
                ch_b = None

        all_events.append(FrameEvent(
            start_s     = start_s,
            channel     = channel,
            channel_b   = ch_b,
            frame_type  = name,
            callsign    = callsign,
            audio       = audio,
            duration_s  = dur_s,
            _frame_body = body,   # für AUTH-HMAC
        ))

        # ── AUTH-Frame optional (Pfad B) ─────────────────────────────
        # Nach dem Daten-Frame ein AUTH-Frame (0x50) auf demselben Kanal.
        # Zwei Kategorien:
        #   known   — echtes Rufzeichen + echter Key aus gateway.json
        #             → der Daemon kann verifizieren.
        #   unknown — Rufzeichen NICHT in gateway.json + Zufalls-Key
        #             → der Daemon verwirft (key_id 99, nicht vereinbart).
        # Trägt ein „known"-AUTH ein anderes Rufzeichen als der Daten-Frame,
        # MUSS der Daten-Frame neu moduliert werden (Rufzeichen steckt
        # Base-40-kodiert im Frame-Body → auch im Audio).
        if auth_config and random.random() < auth_config["ratio"]:
            known_keys  = auth_config["known_keys"]
            ratio_known = auth_config["ratio_known"]
            ref_type    = int(ftype)   # FrameType ist int-Konstante

            if known_keys and random.random() < ratio_known:
                # ── KNOWN ─────────────────────────────────────────────
                key_entry     = random.choice(known_keys)
                auth_callsign = key_entry["callsign"]
                key           = key_entry["key"]
                key_id        = key_entry["key_id"]
                auth_type     = "known"
            else:
                # ── UNKNOWN ───────────────────────────────────────────
                # Rufzeichen wählen, das garantiert NICHT in gateway.json ist,
                # sonst würde der Daemon es (fälschlich) zu verifizieren versuchen.
                known_set     = {k["callsign"] for k in known_keys}
                auth_callsign = rand_call()
                _tries = 0
                while auth_callsign in known_set and _tries < 20:
                    auth_callsign = rand_call()
                    _tries += 1
                key       = os.urandom(32)   # unbekannter Zufalls-Key
                key_id    = 99               # nicht in gateway.json vereinbart
                auth_type = "unknown"

            # Daten-Frame trägt dasselbe Rufzeichen wie der AUTH-Frame →
            # neu modulieren (Rufzeichen ist Teil des Frame-Bodys/Audios).
            try:
                new_audio, data_body = modulate_frame(
                    ftype, auth_callsign, payload, channel)
                all_events[-1].callsign    = auth_callsign
                all_events[-1].audio       = new_audio
                all_events[-1].duration_s  = len(new_audio) / SAMPLE_RATE
                all_events[-1]._frame_body = data_body
            except Exception:
                data_body = all_events[-1]._frame_body   # Fallback: alter Body

            ref_nr   = len(all_events)                   # 1-basierter Index Daten-Frame
            ts       = int(time.time())                  # Timestamp für HMAC + Decoder
            hmac_tag = auth_tag(data_body, ts, key)
            auth_pl  = encode_auth(ts, ref_type, key_id, hmac_tag)

            auth_start = cursor_s + auth_config["pause_s"]   # nach Pause
            if auth_start + 5.8 <= total_duration:           # 5.8 s = AUTH-Frame-Dauer
                try:
                    auth_audio, _ = modulate_frame(
                        FrameType.AUTH, auth_callsign, auth_pl, channel)
                    auth_dur = len(auth_audio) / SAMPLE_RATE
                    all_events.append(FrameEvent(
                        start_s        = auth_start,
                        channel        = channel,
                        channel_b      = None,
                        frame_type     = f"AUTH({auth_type})",   # AUTH(known)/AUTH(unknown)
                        callsign       = auth_callsign,
                        audio          = auth_audio,
                        duration_s     = auth_dur,
                        auth_ref_nr    = ref_nr,
                        auth_key_id    = key_id,
                        auth_timestamp = ts,
                        auth_type      = auth_type,
                        _frame_body    = None,   # AUTH-Frames brauchen keinen body
                    ))
                    cursor_s = auth_start + auth_dur
                except Exception as e:
                    print(f"  WARNUNG AUTH-Frame ch{channel}: {e}")

# ══════════════════════════════════════════════════════════════════════
# MIXER: alle FrameEvents -> gemeinsamer Audio-Buffer
# ══════════════════════════════════════════════════════════════════════

def mix_all(events: List[FrameEvent], total_duration: float,
            noise_db: Optional[float] = None) -> np.ndarray:
    """
    Summiert alle FrameEvents in einen Float32-Puffer.

    Skalierung: jeder Frame x (1 / N_CHANNELS) → bei 8 gleichzeitigen
    Kanälen bleibt der Gesamtpegel unter 1.0.

    noise_db: Rauschpegel relativ zum Nutzsignal-Peak in dB.
              z.B. -20 → Rauschen 20 dB unter dem stärksten Frame-Signal.
              None → kein Rauschen.
    """
    n_samples = int((total_duration + 2.0) * SAMPLE_RATE)
    buf = np.zeros(n_samples, dtype=np.float32)
    scale = 1.0 / N_CHANNELS

    for ev in events:
        offset = int(ev.start_s * SAMPLE_RATE)
        end    = offset + len(ev.audio)
        if end > len(buf):
            extra = np.zeros(end - len(buf), dtype=np.float32)
            buf = np.concatenate([buf, extra])
        buf[offset:end] += ev.audio * scale

    if noise_db is not None:
        # Amplitude des Nutzsignals als Referenz
        sig_peak = float(np.max(np.abs(buf))) if np.any(buf != 0) else 1.0
        # Rausch-Amplitude: sig_peak × 10^(noise_db/20)
        noise_amp = sig_peak * 10.0 ** (noise_db / 20.0)
        noise = np.random.normal(0.0, noise_amp, len(buf)).astype(np.float32)
        buf += noise

    return buf

# ══════════════════════════════════════════════════════════════════════
# AUSGABE
# ══════════════════════════════════════════════════════════════════════

def write_wav(path: str, audio: np.ndarray):
    """float32 -> normiertes int16 WAV (90% FS)."""
    peak = float(np.max(np.abs(audio)))
    if peak > 0:
        audio = audio / peak * 0.90
    wavfile.write(path, SAMPLE_RATE, (audio * 32767).astype(np.int16))

def write_cf32(path: str, audio: np.ndarray):
    """Reales NF-Signal -> CF32 (I=Audio, Q=0) fuer inspectrum."""
    iq = np.zeros(len(audio) * 2, dtype=np.float32)
    iq[0::2] = audio
    iq.tofile(path)

def write_csv(path: str, events: List[FrameEvent]):
    fields = ["nr", "start_s", "end_s", "channel", "channel_b",
              "frame_type", "callsign", "duration_s",
              "auth_frame",    # "known" | "unknown" | ""
              "auth_ref_nr", "auth_key_id", "auth_timestamp"]
    rows = sorted(events, key=lambda e: e.start_s)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i, ev in enumerate(rows, 1):
            w.writerow({
                "nr":             i,
                "start_s":        f"{ev.start_s:.3f}",
                "end_s":          f"{ev.start_s + ev.duration_s:.3f}",
                "channel":        ev.channel,
                "channel_b":      ev.channel_b if ev.channel_b is not None else "",
                "frame_type":     ev.frame_type,
                "callsign":       ev.callsign,
                "duration_s":     f"{ev.duration_s:.3f}",
                "auth_frame":     ev.auth_type if ev.auth_type else "",
                "auth_ref_nr":    ev.auth_ref_nr    if ev.auth_ref_nr    is not None else "",
                "auth_key_id":    ev.auth_key_id    if ev.auth_key_id    is not None else "",
                "auth_timestamp": ev.auth_timestamp if ev.auth_timestamp is not None else "",
            })

# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description="GUST Parallel-Stresstest: alle Kanäle gleichzeitig, zufaellige Startzeitpunkte")
    ap.add_argument("--duration",      type=float, default=45.0,
                    help="Gesamtdauer in Sekunden (Standard: 45)")
    ap.add_argument("--frames-per-ch", type=int,   default=4,
                    help="Frames pro Kanal (Standard: 4)")
    ap.add_argument("--out",           type=str,   default="",
                    help="Basis-Dateiname ohne Extension")
    ap.add_argument("--seed",          type=int,   default=None,
                    help="Zufalls-Seed (fuer reproduzierbare Laeufe)")
    ap.add_argument("--noise",         type=float, default=None,
                    metavar="DB",
                    help="Weisses Rauschen relativ zum Signal in dB "
                         "(z.B. --noise -20 = SNR 20 dB, "
                         "--noise -6 = SNR 6 dB, kein Wert = kein Rauschen)")
    ap.add_argument("--auth", action="store_true",
                    help="AUTH-Frames (0x50) nach Daten-Frames einfuegen")
    ap.add_argument("--auth-ratio", type=float, default=1.0, metavar="R",
                    help="Anteil der Daten-Frames die authentifiziert werden "
                         "(0.0–1.0, Standard: 1.0 = alle)")
    ap.add_argument("--auth-pause", type=float, default=1.5, metavar="S",
                    help="Pause in Sekunden zwischen Daten-Frame und AUTH-Frame "
                         "(Standard: 1.5 s)")
    ap.add_argument("--config", type=str, default="gateway.json",
                    help="Pfad zur gateway.json (Auth-Keys, Standard: gateway.json)")
    ap.add_argument("--auth-ratio-known", type=float, default=0.5, metavar="R",
                    help="Anteil bekannter Keys an AUTH-Frames (0.0–1.0, "
                         "Standard: 0.5 = 50%% bekannt, 50%% unbekannt)")
    args = ap.parse_args()

    seed        = args.seed if args.seed is not None else int(time.time()) & 0xFFFF
    duration    = max(10.0, args.duration)
    frames_p_ch = max(1, args.frames_per_ch)

    # Slot-Größe prüfen und ggf. frames_p_ch reduzieren
    slot_s = duration / frames_p_ch
    if slot_s < MIN_SLOT_S:
        frames_p_ch_max = int(duration / MIN_SLOT_S)
        frames_p_ch_max = max(1, frames_p_ch_max)
        print(f"  WARNUNG: Slot-Groesse {slot_s:.1f} s < Minimum {MIN_SLOT_S:.0f} s")
        print(f"           frames-per-ch reduziert: {frames_p_ch} -> {frames_p_ch_max}")
        print(f"           (Tipp: --duration {int(frames_p_ch * MIN_SLOT_S)} fuer {frames_p_ch} Frames/Kanal)")
        frames_p_ch = frames_p_ch_max
        slot_s = duration / frames_p_ch

    random.seed(seed)
    np.random.seed(seed)

    # ── AUTH-Keys aus gateway.json laden (echte bilaterale Schlüssel) ─
    # Damit kann der Daemon „known"-Frames mit seinen echten Keys
    # verifizieren; „unknown"-Frames (Zufalls-Key) verwirft er.
    known_keys  = []   # list of {callsign, key_id, key}
    ratio_known = max(0.0, min(1.0, args.auth_ratio_known))
    if args.auth:
        cfg_path = args.config
        if not os.path.isfile(cfg_path):
            cfg_path = os.path.join(os.path.dirname(__file__),
                                    os.path.basename(args.config))
        try:
            with open(cfg_path, encoding="utf-8") as f:
                gw_cfg = json.load(f)
            for entry in gw_cfg.get("auth", {}).get("keys", []):
                try:
                    cs  = str(entry.get("callsign", "")).strip().upper()
                    key = bytes.fromhex(entry["key_hex"])
                    kid = int(entry.get("key_id", 1))
                    if cs and len(key) >= 16:
                        known_keys.append({"callsign": cs,
                                           "key_id":   kid,
                                           "key":      key})
                except Exception as e:
                    print(f"  WARNUNG: Auth-Key-Eintrag ungültig: {e}")
        except FileNotFoundError:
            print(f"  WARNUNG: {cfg_path} nicht gefunden — "
                  "nur 'unknown'-AUTH-Frames werden erzeugt")
        except Exception as e:
            print(f"  WARNUNG: gateway.json Lesefehler: {e}")

    base      = args.out or f"gust_stress_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    wav_path  = base + ".wav"
    cf32_path = base + ".cf32"
    csv_path  = base + ".csv"

    print(f"\n{'=':=<62}")
    print(f"  GUST Parallel-Stresstest   v0.5   OE3GAS")
    print(f"{'=':=<62}")
    print(f"  Dauer:          {duration:.0f} s")
    print(f"  Kanäle:         {N_CHANNELS}  (ch0 = 600 Hz ... ch{N_CHANNELS-1} = 2350 Hz)")
    print(f"  Frames/Kanal:   {frames_p_ch}")
    print(f"  Slot-Groesse:   {slot_s:.1f} s  (Minimum: {MIN_SLOT_S:.0f} s)")
    print(f"  Min. Frames:    {N_CHANNELS * frames_p_ch}  (ohne Dual-Kopien)")
    print(f"  Seed:           {seed}")
    if args.auth:
        print(f"  AUTH-Modus:     aktiv (ratio={args.auth_ratio:.0%}, "
              f"pause={args.auth_pause}s)")
        if known_keys:
            print(f"  AUTH-Keys:      {len(known_keys)} bekannte Stationen: "
                  + ", ".join(k["callsign"] for k in known_keys))
        else:
            print("  AUTH-Keys:      keine — alle AUTH-Frames werden 'unknown'")
        print(f"  AUTH-Ratio:     {ratio_known:.0%} bekannt / "
              f"{1-ratio_known:.0%} unbekannt")
    print(f"{'-'*62}\n")

    # ── AUTH-Konfiguration für build_channel_timeline ────────────────
    auth_cfg = {
        "known_keys":  known_keys,
        "ratio":       args.auth_ratio,
        "ratio_known": ratio_known,
        "pause_s":     args.auth_pause,
    } if args.auth else None

    # ── Timelines aller 8 Kanäle erzeugen ───────────────────────────
    all_events: List[FrameEvent] = []

    for ch in range(N_CHANNELS):
        freq_lo = 600 + ch * 250
        freq_hi = freq_lo + 250
        print(f"  Kanal {ch}  ({freq_lo}–{freq_hi} Hz):")
        n_before = len(all_events)
        build_channel_timeline(ch, duration, frames_p_ch, all_events, auth_cfg)
        # Nur Primär-Events dieses Kanals ausgeben
        primary = [e for e in all_events[n_before:]
                   if e.channel == ch and "dual" not in e.frame_type.lower()]
        for ev in sorted(primary, key=lambda e: e.start_s):
            dual_mark = " [+ Dual]" if ev.channel_b is not None else ""
            print(f"    @{ev.start_s:6.2f}s – {ev.start_s + ev.duration_s:.2f}s  "
                  f"{ev.frame_type:<14s}  {ev.callsign:<8s}{dual_mark}")

    n_primary = sum(1 for e in all_events
                    if "dual" not in e.frame_type.lower())
    n_dual    = sum(1 for e in all_events
                    if "dual" in e.frame_type.lower())

    print(f"\n  {'-'*58}")
    print(f"  Primär-Frames:    {n_primary}")
    print(f"  Dual-Kopien:      {n_dual}")
    print(f"  FrameEvents ges.: {len(all_events)}")

    if args.auth:
        known_auth = sum(1 for e in all_events if e.auth_type == "known")
        unk_auth   = sum(1 for e in all_events if e.auth_type == "unknown")
        print(f"  AUTH-Frames:      {known_auth} bekannt (-> Daemon verifiziert) + "
              f"{unk_auth} unbekannt (-> Daemon verwirft)")

    # ── Mixen ───────────────────────────────────────────────────────
    noise_db = args.noise
    if noise_db is not None:
        print(f"\n  Rauschen:       {noise_db:+.0f} dB  (SNR ~{abs(noise_db):.0f} dB)")
    else:
        print(f"\n  Rauschen:       keines  (--noise DB zum Aktivieren)")
    print(f"  Mische {len(all_events)} FrameEvents ...")
    audio    = mix_all(all_events, duration, noise_db=noise_db)
    real_dur = len(audio) / SAMPLE_RATE
    peak     = float(np.max(np.abs(audio)))
    print(f"  Buffer: {len(audio)} Samples = {real_dur:.1f} s  "
          f"| Peak: {peak:.4f} {'(OK)' if peak <= 1.0 else '(CLIP - wird normiert)'}")

    # ── Speichern ───────────────────────────────────────────────────
    print(f"\n  Ausgabedateien:")
    write_wav(wav_path, audio.copy())
    print(f"    WAV:  {wav_path}  "
          f"({os.path.getsize(wav_path)//1024} kB, {real_dur:.1f} s)")

    write_cf32(cf32_path, audio)
    print(f"    CF32: {cf32_path}  "
          f"({os.path.getsize(cf32_path)//1024} kB)  <- inspectrum {SAMPLE_RATE} Hz")

    write_csv(csv_path, all_events)
    print(f"    CSV:  {csv_path}  ({len(all_events)} Eintraege)")

    # ── Typ-Statistik ────────────────────────────────────────────────
    counts: dict = {}
    for ev in all_events:
        t = ev.frame_type.split("(")[0].strip()
        counts[t] = counts.get(t, 0) + 1

    print(f"\n  Frame-Typen-Verteilung:")
    for t, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"    {t:<22s} {n:3d}x")

    print(f"\n  inspectrum-Hinweis:")
    print(f"    Datei:       {cf32_path}")
    print(f"    Sample Rate: {SAMPLE_RATE}")
    print(f"    FFT-Groesse: 4096")
    print(f"    Kanalraster: ch0=600 Hz, ch1=850 Hz, ..., ch7=2350 Hz")
    print(f"\n  Fertig.  73 de OE3GAS\n")


if __name__ == "__main__":
    main()