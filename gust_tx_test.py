#!/usr/bin/env python3
"""
GUST — HackRF TX-Test                                     Phase 7
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Version : 1.2.0
Datum   : Mai 2026

Sendet zufällige GUST-Frames via HackRF One.
Neben WEATHER / POSITION / TEXT werden auch DUAL-Kanal-Emergency-Frames
gesendet: dasselbe Signal läuft gleichzeitig auf zwei NF-Kanälen.

Dual-Kanal-Prinzip
──────────────────
  audio_A  (Kanal A, volle Amplitude × 1/√2)
  audio_B  (Kanal B, selbe Nutzlast, volle Amplitude × 1/√2)
  mixed   = audio_A + audio_B   (normalisiert auf 0.9 Peak)

  Beide MFSK-8-Signale liegen in getrennten 250-Hz-Sub-Bändern des
  SSB-Passbands → keine spektrale Überlappung, kein gegenseitiges
  Stören. Der Empfänger kann jeden Kanal unabhängig dekodieren.

Freitext-Modus (--text-only)
─────────────────────────────
  Sendet mehrteilige deutsche Freitext-Nachrichten (Frame 0x40).
  Jede Nachricht besteht aus 1–4 Fragmenten à max. 14 Byte (UTF-8).
  Die Fragmente werden als separate Frames mit kurzer Pause gesendet.

  Fragment-Format (Payload Byte 5, frag_info):
    Bits 7–4: Fragment-Index (0-basiert)
    Bits 3–0: Gesamt-Fragmente − 1
  Maximale Nachrichtenlänge: 4 × 14 = 56 Zeichen.

  Beispiel: 3-teilige Nachricht "Wir treffen uns morgen auf 14 MHz"
    Frag 0/3: "Wir treffen uns"   frag_info = 0x02
    Frag 1/3: " morgen auf 14 "  frag_info = 0x12
    Frag 2/3: "MHz"               frag_info = 0x22

CSV-Log: tx_test_log.csv
  Spalten: timestamp, nr, callsign, frame_type, channel,
           channel_b, gain_db, nf_hz, rf_mhz, duration_ms, status

Verwendung
──────────
  python tx_test.py
  python tx_test.py --count 15 --min-gain 24 --max-gain 32
  python tx_test.py --dual-only       (nur Dual-Kanal-Frames)
  python tx_test.py --no-dual         (keine Dual-Kanal-Frames)
  python tx_test.py --text-only       (nur mehrteilige Freitext-Nachrichten)
  python tx_test.py --text-only --text-parts 3  (genau 3 Fragmente)
  python tx_test.py --device hackrf   (HackRF statt IC-7610)
  python tx_test.py --dry-run         (kein TX — nur Frame-Erzeugung)
"""

import argparse
import csv
import json
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
INTER_FRAG_PAUSE_S = 0.5   # Pause zwischen Fragmenten einer Freitext-Nachricht
DEFAULT_DEVICE = "7610"    # "7610" = IC-7610 USB-Audio | "hackrf" = HackRF One
GATEWAY_JSON   = "gateway.json"

OE_PREFIXES  = ["OE1", "OE2", "OE3", "OE4", "OE5", "OE6", "OE7", "OE8", "OE9"]
LETTERS      = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


# ═══════════════════════════════════════════════════════════════════════
# GATEWAY-KONFIGURATION
# ═══════════════════════════════════════════════════════════════════════

def load_gateway_config(path: str = GATEWAY_JSON) -> dict:
    """
    Liest gateway.json und gibt die relevanten TX-Defaults zurück.

    Fehlende Felder werden mit sicheren Fallback-Werten belegt,
    sodass das Skript auch ohne gateway.json lauffähig bleibt.

    Erwartete Struktur (Auszug):
        {
            "callsign": "OE3GAS",
            "audio": {
                "device":       9,           ← sounddevice-Index
                "ptt_backend":  "hamlib",    ← "null" | "hamlib"
                "level":        30,          ← Ausgangspegel in %
                "hamlib_host":  "localhost",
                "hamlib_port":  4532
            }
        }
    """
    defaults = {
        "callsign":     None,
        "audio_device": None,
        "ptt":          "null",
        "audio_level":  0.80,
        "hamlib_host":  "localhost",
        "hamlib_port":  4532,
    }

    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return defaults   # kein gateway.json → stille Fallbacks
    except Exception as e:
        print(f"[Warnung] {path} konnte nicht gelesen werden: {e}", flush=True)
        return defaults

    audio = cfg.get("tx_audio") or cfg.get("audio") or {}

    if cfg.get("callsign"):
        defaults["callsign"] = str(cfg["callsign"]).upper().strip()

    if audio.get("device") is not None:
        try:
            defaults["audio_device"] = int(audio["device"])
        except (TypeError, ValueError):
            defaults["audio_device"] = audio["device"]   # Name als String

    backend = str(audio.get("ptt_backend", "null")).lower()
    defaults["ptt"] = "hamlib" if backend == "hamlib" else "null"

    if audio.get("level") is not None:
        raw = float(audio["level"])
        # Werte > 1 werden als Prozent interpretiert (30 → 0.30)
        defaults["audio_level"] = raw / 100.0 if raw > 1.0 else raw

    if audio.get("hamlib_host"):
        defaults["hamlib_host"] = str(audio["hamlib_host"])
    if audio.get("hamlib_port"):
        defaults["hamlib_port"] = int(audio["hamlib_port"])

    # rigctld-Block als ganzes Dict weitergeben — für ensure_rigctld_running()
    defaults["rigctld"] = cfg.get("rigctld") or {}

    return defaults


# ═══════════════════════════════════════════════════════════════════════
# DEUTSCHE FREITEXT-POOL (ASCII-kompatibel, Umlaute als ae/oe/ue/ss)
# ═══════════════════════════════════════════════════════════════════════
#
# Kategorisiert nach Anzahl der Fragmente (je 14 Byte pro Fragment).
# Alle Texte verwenden reines ASCII — das entspricht Amateurfunk-Usus
# auf digitalen Kurzwellen-Betriebsarten (kein UTF-8 auf der Luft).
#
# Grenzen: 1 Teil ≤ 14 Zeichen │ 2 Teile 15–28 │ 3 Teile 29–42 │ 4 Teile 43–56

_GERMAN_TEXTS = {
    1: [   # ≤ 14 Zeichen (1 Fragment)
        "Hallo Wien!",
        "73 de OE3GAS",
        "PSE QSY 80m",
        "CQ CQ CQ DX",
        "Gut empfangen",
        "Signal stark!",
        "QSL ok tnx",
        "GUST v1 Test",
        "FB Modulation",
        "Kein QRM hier",
    ],
    2: [   # 15–28 Zeichen (2 Fragmente)
        "QSO laeuft gut, 73!",
        "Frequenz frei kommen",
        "Signal ist sehr stark",
        "Guten Abend aus Wien!",
        "Auf 40m sehr viel QRM",
        "OE3GAS ruft auf 80m!",
        "20m Band offen nach NA",
        "Wetter heute bewoelkt",
        "73 und bis zum naechsten",
        "GUST Frame ankommt gut!",
    ],
    3: [   # 29–42 Zeichen (3 Fragmente)
        "Wir treffen uns morgen auf 14 MHz",
        "GUST Protokoll im Feldtest aktiv!",
        "Antenne nach Norden ausgerichtet.",
        "Funke laeuft stabil und sauber!",
        "OE3GAS testet digitale Betriebsart",
        "Ausbreitung heute Nacht sehr gut!!",
        "Kurzwelle macht heute viel Freude!",
        "Modulation sauber, kein Splatter!",
    ],
    4: [   # 43–56 Zeichen (4 Fragmente)
        "Heute Nacht gute Ausbreitung auf 20m Europa!",
        "GUST Freitext Test laeuft stabil und sauber!",
        "Digital Funk Test von OE3GAS aus Niederoest!",
        "QSO morgen 9 Uhr auf 14200 kHz bitte melden!",
        "MFSK8 Uebertragung ohne Fehler und QRM frei!",
        "Phase 7 Feldtest: alle Frames korrekt dekodiert",
    ],
}


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


def _text_multipart_fragments(num_parts: int = 0, dest_call: str = ""):
    """
    Erzeugt eine mehrteilige Freitext-Nachricht als Fragment-Liste.

    Parameters
    ----------
    num_parts : int
        Gewünschte Anzahl Fragmente (1–4).
        0 = zufällig aus 1–4 wählen.
    dest_call : str
        Ziel-Rufzeichen. Leer oder fehlend → zufällig generiert.

    Returns
    -------
    text : str
        Der vollständige Nachrichtentext.
    payloads : list[bytes]
        Liste der kodierten Fragment-Payloads (je ≤ 20 Byte).
    dest_call : str
        Tatsächlich verwendetes Ziel-Rufzeichen.
    seq_nr : int
        Sequenznummer (zufällig 0–255).
    """
    from gust_frame import fragment_text

    if num_parts < 1 or num_parts > 4:
        num_parts = random.randint(1, 4)

    text   = random.choice(_GERMAN_TEXTS[num_parts])
    dest   = dest_call.strip().upper() if dest_call.strip() else random_callsign()
    seq_nr = random.randint(0, 255)
    payloads = fragment_text(text, dest_call=dest, seq_nr=seq_nr)

    # Sicherheitsnetz: fragment_text kann bei sehr kurzen Texten eine andere
    # Anzahl liefern als erwartet — wir akzeptieren das kommentarlos.
    return text, payloads, dest, seq_nr


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
        FrameType, channel_frequency, assign_channel, CHANNEL_BW_HZ,
        encode_emergency_beacon, PRIO_URGENT, INJURY_UNKNOWN,
        fragment_text,
    )

    # ── Gerät wählen und initialisieren ──────────────────────────────────
    ptt = None   # nur für 7610 (AudioTransmitter) verwendet
    device_label = ""

    if args.device == "7610":
        dev_str = str(args.audio_device) if args.audio_device is not None else "Standard"
        device_label = (f"IC-7610 USB-Audio  "
                        f"(Gerät: {dev_str}, PTT: {args.ptt})")
        if not args.dry_run:
            try:
                from gust_audio import AudioTransmitter, NullPTT, HamlibPTT
                if args.ptt == "hamlib":
                    from gust_audio import ensure_rigctld_running
                    # Vollständige Config für ensure_rigctld_running() zusammenbauen.
                    # (gw ist in run() nicht im Scope → gateway.json hier laden.)
                    _gw = load_gateway_config(args.config)
                    _rig_cfg = {
                        "audio":   {
                            "ptt_backend":  "hamlib",
                            "hamlib_host":  args.hamlib_host,
                            "hamlib_port":  args.hamlib_port,
                        },
                        "rigctld": _gw.get("rigctld") or {},
                    }
                    try:
                        ensure_rigctld_running(_rig_cfg,
                                               host=args.hamlib_host,
                                               port=args.hamlib_port)
                    except RuntimeError as e:
                        print(f"{ts()}  FEHLER: {e}", flush=True)
                        print(f"{ts()}  Tipp: --dry-run für Test ohne Hardware",
                              flush=True)
                        sys.exit(1)
                    import time as _time
                    ptt = None
                    for _attempt in range(10):
                        _time.sleep(0.3)
                        try:
                            ptt = HamlibPTT(host=args.hamlib_host,
                                            port=args.hamlib_port)
                            break
                        except RuntimeError as _ptt_err:
                            print(f"[DEBUG] Versuch {_attempt+1}/10: {_ptt_err!s:.60}", flush=True)
                    if ptt is None:
                        raise RuntimeError(
                            f"rigctld nicht erreichbar nach 10 Versuchen "
                            f"({args.hamlib_host}:{args.hamlib_port})"
                        )
                else:
                    ptt = NullPTT()
            except Exception as e:
                print(f"{ts()}  FEHLER: IC-7610 Audio-Init fehlgeschlagen: {e}",
                      flush=True)
                print(f"{ts()}  Tipp: --dry-run für Test ohne Hardware", flush=True)
                sys.exit(1)
    else:
        device_label = "HackRF One  (SoapySDR)"
        if not args.dry_run:
            try:
                from gust_hackrf import HackRFTransmitter
            except Exception as e:
                print(f"{ts()}  FEHLER: HackRF nicht verfügbar: {e}", flush=True)
                print(f"{ts()}  Tipp: --dry-run für Test ohne Hardware", flush=True)
                sys.exit(1)

    # IC-7610 unterstützt kein transmit_iq() → Dual-Kanal nicht möglich
    if args.device == "7610" and (args.dual_only or not args.no_dual):
        if args.dual_only:
            print(f"{ts()}  [Hinweis] --dual-only nicht verfügbar mit IC-7610. "
                  f"Wechsle zu Einzel-Kanal.", flush=True)
            args.dual_only = False
        args.no_dual = True

    logger   = TxLogger(args.log)
    tx_count = 0

    # ── AUTH-Schlüssel laden (für --auth Flag, P8-11) ────────────────────
    # load_gateway_config() reicht den auth-Block NICHT durch → gateway.json
    # hier direkt lesen (CWD, dann Skript-Verzeichnis).
    auth_keys = {}   # Rufzeichen (str) → bytes
    auth_cfg  = {}   # auth-Block aus gateway.json (für Rufzeichen-Zuordnung)
    if args.auth:
        for _p in (args.config,
                   os.path.join(os.path.dirname(__file__),
                                os.path.basename(args.config))):
            try:
                with open(_p, encoding="utf-8") as _f:
                    auth_cfg = json.load(_f).get("auth", {})
                break
            except Exception:
                continue
        if not auth_cfg.get("enabled", False):
            print("  ⚠  --auth gesetzt, aber 'auth.enabled' fehlt/false in gateway.json")
            print("     AUTH-Frames werden trotzdem erzeugt (für Testzwecke)")
        for entry in auth_cfg.get("keys", []):
            try:
                cs  = str(entry.get("callsign", "")).strip().upper()
                key = bytes.fromhex(entry["key_hex"])
                if cs:
                    auth_keys[cs] = key
                    print(f"  AUTH-Schluessel: {cs}")
            except Exception as e:
                print(f"  ⚠  AUTH-Schluessel ungueltig: {e}")
        if not auth_keys:
            print("  ⚠  Keine gueltigen AUTH-Schluessel in gateway.json — "
                  "--auth ohne Wirkung")

    def send_auth(data_callsign, data_ft_int, frame_body, channel, gain_db,
                  auth_key=None):
        """
        Sendet einen AUTH-Frame (0x50) für den zuletzt gesendeten Daten-Frame.
        TIMESTAMP + HMAC-SHA256-14 über (frame_body + TIMESTAMP). Gleicher
        TX-Pfad (Audio/HackRF) wie der Daten-Frame; Dry-Run-fähig.

        Lookup beim Empfänger erfolgt per Rufzeichen; die KEY_ID im Payload ist
        nur noch Wire-Format-Konstante (0). auth_key: explizit zu verwendender
        Schlüssel; None = Schlüssel des Daten-Rufzeichens. Für den realistischen
        Test wird bewusst auch ein zufälliges Rufzeichen ohne Schlüssel
        gesendet, das der Empfänger nicht verifizieren kann.
        """
        if not (args.auth and auth_keys):
            return
        from gust_frame import FrameType as _FT, auth_tag, encode_auth
        key = auth_key if auth_key is not None \
            else auth_keys.get(str(data_callsign).strip().upper())
        if key is None:
            key = os.urandom(32)   # unbekannte Station → Empfänger verwirft
        ts_val       = int(time.time())
        tag          = auth_tag(frame_body, ts_val, key)
        auth_payload = encode_auth(ts_val, data_ft_int, tag)
        auth_hdr = (f"{ts()}  AUTH #{tx_count:3d}  {data_callsign:<8}  "
                    f"[AUTH      ]  Kanal {channel}  TS={ts_val}")
        if args.dry_run:
            print(f"{auth_hdr}  [DRY-RUN]", flush=True)
            logger.write(nr=tx_count, callsign=data_callsign, frame_type="AUTH",
                         channel=channel, status="DRY-RUN",
                         notes=f"REF_TYPE=0x{data_ft_int:02X}")
            return
        time.sleep(args.auth_pause)
        try:
            auth_audio, auth_ch, _ = transmit(
                _FT.AUTH, data_callsign, auth_payload,
                channel=channel, use_fec=True, window=True, add_silence_ms=100)
        except Exception as e:
            print(f"{auth_hdr}  Frame-Erzeugung fehlgeschlagen: {e}", flush=True)
            return
        t0 = time.monotonic()
        try:
            if args.device == "7610":
                from gust_audio import AudioTransmitter
                tx = AudioTransmitter(ptt=ptt, device=args.audio_device,
                                      level=args.audio_level)
                tx.transmit_audio(auth_audio, sample_rate=SAMPLE_RATE)
            else:
                from gust_hackrf import HackRFTransmitter
                tx = HackRFTransmitter(freq_hz=args.freq, gain_db=gain_db)
                tx.open(); tx.transmit(auth_audio); tx.close()
            elapsed = (time.monotonic() - t0) * 1000
            print(f"{auth_hdr}  {elapsed:.0f} ms  ✓", flush=True)
            logger.write(nr=tx_count, callsign=data_callsign, frame_type="AUTH",
                         channel=auth_ch, duration_ms=round(elapsed), status="OK",
                         notes=f"REF_TYPE=0x{data_ft_int:02X}")
        except Exception as e:
            try: tx.close()
            except Exception: pass
            print(f"{auth_hdr}  FEHLER: {e}", flush=True)
            logger.write(nr=tx_count, callsign=data_callsign, frame_type="AUTH",
                         channel=channel, status="ERROR", notes=str(e))

    print(flush=True)
    print("╔══════════════════════════════════════════════════════════╗", flush=True)
    print("║  GUST TX-Test  v1.3                                  ║", flush=True)
    print("╠══════════════════════════════════════════════════════════╣", flush=True)
    print(f"║  Gerät     : {device_label:<44}║", flush=True)
    if args.device == "7610":
        lvl_str = f"{args.audio_level*100:.0f}%"
        print(f"║  Pegel     : {lvl_str:<44}║", flush=True)
    print(f"║  Frequenz  : {args.freq/1e6:.3f} MHz{'':>37}║", flush=True)
    if args.device == "hackrf":
        if args.gains is not None:
            gain_str = "→".join(str(g) for g in args.gains) + " dB"
            print(f"║  Gain-Folge: {gain_str:<44}║", flush=True)
        else:
            print(f"║  Gain      : {args.min_gain}–{args.max_gain} dB{'':>40}║", flush=True)
    if args.fixed_channels:
        ch_str = "+".join(str(c) for c in args.fixed_channels)
        print(f"║  Kanäle    : fest {ch_str:<40}║", flush=True)
    print(f"║  Sendungen : {'endlos' if args.count==0 else args.count}{'':>46}║", flush=True)
    if args.text_only:
        parts_str = (f"genau {args.text_parts} Fragmente"
                     if args.text_parts > 0 else "1–4 Fragmente zufällig")
        print(f"║  Modus     : NUR Freitext ({parts_str}){'':>{max(0,18-len(parts_str))}}║", flush=True)
        print(f"║  Frag-Pause: {args.inter_frag_pause:.1f}s{'':>45}║", flush=True)
    else:
        dual_mode = "JA (~30% der Frames)" if not args.no_dual else "NEIN"
        if args.dual_only: dual_mode = "NUR Dual-Kanal"
        print(f"║  Dual-Chan : {dual_mode:<44}║", flush=True)
    print(f"║  TX-Modus  : {'DRY-RUN — kein Signal' if args.dry_run else 'LIVE — Sender aktiv':<44}║", flush=True)
    print("╠══════════════════════════════════════════════════════════╣", flush=True)
    print("║  Stoppen   : Strg+C                                     ║", flush=True)
    print("╚══════════════════════════════════════════════════════════╝", flush=True)
    if args.auth:
        if auth_keys:
            print(f"  AUTH-Modus  : aktiv ({len(auth_keys)} Schluessel), "
                  f"Pause {args.auth_pause:.1f}s, Einzel-Kanal erzwungen", flush=True)
            _known = [e.get("callsign", "?") for e in auth_cfg.get("keys", [])]
            print(f"  AUTH-Bekannt: {', '.join(_known)} → verifiziert", flush=True)
            print(f"  AUTH-Fremd  : zufaellige Rufzeichen ohne Schluessel "
                  f"→ schlaegt fehl", flush=True)
        else:
            print("  AUTH-Modus  : --auth gesetzt, aber keine Schluessel geladen",
                  flush=True)
    print(flush=True)

    try:
        while args.count == 0 or tx_count < args.count:

            # Gain: aus fester Folge oder zufällig
            if args.gains is not None:
                gain_db = args.gains[tx_count]
            else:
                gain_db = random.randint(args.min_gain, args.max_gain)
            tx_count += 1

            # ── AUTH-Test-Identität pro Sendung (P8-11) ───────────────
            # Abwechselnd: bekannte Station (Rufzeichen aus gateway.json →
            # Empfänger verifiziert ✓) und fremde Station (zufälliges
            # Rufzeichen ohne Schlüssel → Empfänger verwirft ✗). Lookup
            # erfolgt per Rufzeichen. auth_force_callsign = None → random.
            auth_force_callsign = None
            auth_send_key       = None
            if args.auth and auth_keys:
                if tx_count % 2 == 1:
                    # Bekannte Station — durch konfigurierte Rufzeichen rotieren
                    _calls = list(auth_keys.keys())
                    auth_force_callsign = _calls[(tx_count // 2) % len(_calls)]
                    auth_send_key       = auth_keys.get(auth_force_callsign)
                else:
                    # Fremde Station — zufälliges Rufzeichen, beliebiger Key
                    auth_send_key       = os.urandom(32)

            # ── Frame-Typ wählen ──────────────────────────────────────
            if args.text_only:
                # ══════════════════════════════════════════════════════
                # FREITEXT-MODUS: mehrteilige deutsche Nachricht senden
                # ══════════════════════════════════════════════════════
                callsign = auth_force_callsign or random_callsign()
                fixed_ch = (args.fixed_channels[0]
                            if args.fixed_channels else None)

                text, payloads, dest, seq_nr = _text_multipart_fragments(
                    args.text_parts,
                    dest_call=args.text_dest or "",
                )
                total_frags = len(payloads)

                print(
                    f"{ts()}  MSG #{tx_count:3d}  {callsign:<8}  "
                    f"[TEXT {total_frags}-teilig]  "
                    f"Seq {seq_nr:3d}  An: {dest}",
                    flush=True,
                )
                print(
                    f"          Text: \"{text}\" ({len(text)} Z.)",
                    flush=True,
                )

                msg_ok = True
                for frag_idx, frag_payload in enumerate(payloads):
                    frag_label = f"TEXT_{frag_idx+1}of{total_frags}"

                    try:
                        audio, channel, frame_body = transmit(
                            FrameType.TEXT, callsign, frag_payload,
                            channel=fixed_ch,
                            use_fec=True, window=True, add_silence_ms=100,
                        )
                    except Exception as e:
                        print(
                            f"  {ts()}  FEHLER Fragment {frag_idx+1}: {e}",
                            flush=True,
                        )
                        msg_ok = False
                        break

                    nf_a   = channel_frequency(channel)
                    rf_mhz = args.freq / 1e6 + nf_a / 1e6
                    duration = len(audio) / SAMPLE_RATE

                    frag_hdr = (
                        f"  {ts()}  Frag {frag_idx+1}/{total_frags}  "
                        f"Kanal {channel}  "
                        f"Gain {gain_db:2d} dB  "
                        f"NF {nf_a:.0f} Hz  "
                        f"RF {rf_mhz:.6f} MHz"
                    )

                    if args.dry_run:
                        print(
                            f"{frag_hdr}  {duration:.2f}s  [DRY-RUN]",
                            flush=True,
                        )
                        logger.write(
                            nr=tx_count, callsign=callsign,
                            frame_type=frag_label,
                            channel=channel, channel_b=None,
                            gain_db=gain_db, nf_hz=nf_a, nf_hz_b=None,
                            rf_mhz=round(rf_mhz, 6),
                            duration_ms=round(duration * 1000),
                            status="DRY-RUN",
                            notes=f"seq={seq_nr} dest={dest} text={text!r}",
                        )
                    else:
                        t0 = time.monotonic()
                        try:
                            if args.device == "7610":
                                tx = AudioTransmitter(
                                    ptt=ptt, device=args.audio_device,
                                    level=args.audio_level)
                                tx.transmit_audio(audio,
                                                  sample_rate=SAMPLE_RATE)
                            else:
                                tx = HackRFTransmitter(
                                    freq_hz=args.freq, gain_db=gain_db)
                                tx.open()
                                tx.transmit(audio)
                                tx.close()
                            elapsed = (time.monotonic() - t0) * 1000
                            print(
                                f"{frag_hdr}  {elapsed:.0f} ms  ✓",
                                flush=True,
                            )
                            logger.write(
                                nr=tx_count, callsign=callsign,
                                frame_type=frag_label,
                                channel=channel, channel_b=None,
                                gain_db=gain_db, nf_hz=nf_a, nf_hz_b=None,
                                rf_mhz=round(rf_mhz, 6),
                                duration_ms=round(elapsed), status="OK",
                                notes=f"seq={seq_nr} dest={dest} text={text!r}",
                            )
                        except Exception as e:
                            elapsed = (time.monotonic() - t0) * 1000
                            try: tx.close()
                            except: pass
                            print(
                                f"{frag_hdr}  FEHLER: {e}",
                                flush=True,
                            )
                            logger.write(
                                nr=tx_count, callsign=callsign,
                                frame_type=frag_label,
                                channel=channel, channel_b=None,
                                gain_db=gain_db, nf_hz=nf_a, nf_hz_b=None,
                                rf_mhz=round(rf_mhz, 6),
                                status="ERROR", notes=str(e),
                            )
                            msg_ok = False
                            break

                    # Kurze Pause zwischen Fragmenten (nicht nach dem letzten)
                    if frag_idx < total_frags - 1:
                        time.sleep(args.inter_frag_pause)

                status_sym = "✓" if msg_ok else "✗"
                print(
                    f"          {status_sym} Nachricht abgeschlossen "
                    f"({total_frags} Fragmente)",
                    flush=True,
                )

                # AUTH-Frame nach der Freitext-Nachricht (referenziert das
                # zuletzt gesendete Fragment als Daten-Frame, P8-11).
                if args.auth and auth_keys and msg_ok and total_frags > 0:
                    send_auth(callsign, FrameType.TEXT, frame_body, channel, gain_db,
                              auth_key=auth_send_key)

            else:
                # ══════════════════════════════════════════════════════
                # NORMALER MODUS: Einzel- oder Dual-Kanal-Frame
                # ══════════════════════════════════════════════════════
                use_dual = (
                    args.dual_only
                    or (not args.no_dual and random.random() < DUAL_PROB)
                )

                if use_dual:
                    # ── Dual-Kanal Emergency ──────────────────────────
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
                    # ── Einzel-Kanal ──────────────────────────────────
                    callsign, ft, ft_name, payload = random_frame()
                    if auth_force_callsign:
                        callsign = auth_force_callsign   # bekannte AUTH-Station
                    fixed_ch = (args.fixed_channels[0]
                                if args.fixed_channels else None)

                    try:
                        audio, channel, frame_body = transmit(
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

                # ── Senden ────────────────────────────────────────────
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
                        if args.device == "7610":
                            tx = AudioTransmitter(
                                ptt=ptt, device=args.audio_device,
                                level=args.audio_level)
                            tx.transmit_audio(audio, sample_rate=SAMPLE_RATE)
                        else:
                            # Beide HackRF-Pfade: IQ bereits berechnet,
                            # Device erst jetzt öffnen (minimale Offen-Zeit).
                            tx = HackRFTransmitter(
                                freq_hz=args.freq, gain_db=gain_db)
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

                # AUTH-Frame nach dem Einzel-Kanal-Daten-Frame (P8-11).
                # Dual-Kanal ist unter --auth deaktiviert → frame_body eindeutig.
                if args.auth and auth_keys and not use_dual:
                    send_auth(callsign, ft, frame_body, used_a, gain_db,
                              auth_key=auth_send_key)

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


def main():
    # ── Gateway-Konfiguration laden (vor argparse, als Defaults) ─────────
    # Suche gateway.json zuerst im aktuellen Verzeichnis, dann im
    # Skript-Verzeichnis (für den Fall dass das Skript von woanders gerufen wird).
    _cfg_path = GATEWAY_JSON
    if not os.path.exists(_cfg_path):
        _cfg_path = os.path.join(os.path.dirname(__file__), GATEWAY_JSON)
    gw = load_gateway_config(_cfg_path)

    parser = argparse.ArgumentParser(
        prog="gust_tx_test.py",
        description="GUST Phase 7 — TX-Test  (Defaults aus gateway.json)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiel-Aufrufe:
  Endlos-TX via IC-7610 (Audio + hamlib PTT):
    py gust_tx_test.py --device 7610 --ptt hamlib

  Einzelner Wetter-Frame, kein TX (Dry-Run):
    py gust_tx_test.py --dry-run --count 1

  SNR-Baseline: HackRF Gain-Stepping 28→1 dB, CSV-Log:
    py gust_tx_test.py --device hackrf --gain-sequence 28,26,24,22,20,18,16,14,12,10,8,6,4,2,1

  HackRF Dual-Kanal auf Kanal 2+7, 5 Sendungen:
    py gust_tx_test.py --device hackrf --channels 0,7 --dual-only --count 5

  Nur Freitext-Fragmente (0x40), 3 Teile, Ziel OE1XTU:
    py gust_tx_test.py --text-only --text-parts 3 --text-dest OE1XTU

  Einzelner Frame auf festem Kanal 2, lange Pause:
    py gust_tx_test.py --channels 2 --count 1 --pause 10

  Andere gateway.json verwenden:
    py gust_tx_test.py --config pfad/zu/gateway.json --device 7610

Hinweise:
  gust.py daemon sollte NICHT gleichzeitig laufen — beide Programme
  teilen denselben rigctld-Prozess und würden sich gegenseitig bei
  PTT stören (TX bleibt hängen oder wird vorzeitig beendet).
  Workflow: daemon stoppen → gust_tx_test.py starten → daemon neu starten.
  rigctld bleibt nach dem daemon-Stop aktiv und wird von gust_tx_test.py
  automatisch wiederverwendet (bzw. neu gestartet falls nötig).
""",
    )
    parser.add_argument("--config",     default=GATEWAY_JSON,
                        help="Pfad zu gateway.json")
    parser.add_argument("--freq",       type=float, default=TX_FREQ_HZ,
                        help="TX-Frequenz Hz (nur HackRF)")
    parser.add_argument("--device",     default=DEFAULT_DEVICE,
                        choices=["7610", "hackrf"],
                        help="TX-Gerät")
    parser.add_argument("--audio-device", type=int,
                        default=gw["audio_device"],
                        dest="audio_device",
                        help="sounddevice-Index IC-7610 USB-Audio "
                             "(Liste: python -m sounddevice)")
    parser.add_argument("--ptt",        default=gw["ptt"],
                        choices=["null", "hamlib"],
                        help="PTT-Backend für IC-7610")
    parser.add_argument("--hamlib-host", default=gw["hamlib_host"],
                        dest="hamlib_host",
                        help="rigctld Hostname")
    parser.add_argument("--hamlib-port", type=int, default=gw["hamlib_port"],
                        dest="hamlib_port",
                        help="rigctld Port")
    parser.add_argument("--count",      type=int,   default=0,
                        help="Anzahl Sendungen (0=endlos)")
    parser.add_argument("--min-gain",   type=int,   default=MIN_GAIN_DB,
                        help="Min VGA Gain dB (nur HackRF)")
    parser.add_argument("--max-gain",   type=int,   default=MAX_GAIN_DB,
                        help="Max VGA Gain dB (nur HackRF)")
    parser.add_argument("--pause",      type=float, default=PAUSE_S,
                        help="Pause zwischen Sendungen in s")
    parser.add_argument("--log",        default=LOG_FILE,
                        help="CSV-Logfile")
    parser.add_argument("--dual-only",  action="store_true",
                        help="Nur Dual-Kanal-Emergency-Frames (nur HackRF)")
    parser.add_argument("--no-dual",    action="store_true",
                        help="Keine Dual-Kanal-Frames")
    parser.add_argument("--text-only",  action="store_true",
                        help="Nur mehrteilige Freitext-Nachrichten (Frame 0x40)")
    parser.add_argument("--text-parts", type=int, default=0,
                        help="Fragmente pro Nachricht: 1–4 (0=zufällig)")
    parser.add_argument("--text-dest",  default="",
                        help="Ziel-Rufzeichen für Freitext, leer=zufällig")
    parser.add_argument("--inter-frag-pause", type=float,
                        default=INTER_FRAG_PAUSE_S,
                        dest="inter_frag_pause",
                        help="Pause zwischen Fragmenten in s")
    parser.add_argument("--channels",   default=None,
                        help="Feste Kanäle, z.B. '0,7' (Dual) oder '3' (Einzel)")
    parser.add_argument("--gain-sequence", default=None,
                        help="Exakte Gain-Folge, z.B. '28,26,24,22,20'")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Kein TX — nur Frame-Erzeugung testen")
    parser.add_argument("--auth",       action="store_true",
                        help="Nach jedem Daten-Frame einen AUTH-Frame (0x50) senden "
                             "(erfordert 'auth'-Block in gateway.json)")
    parser.add_argument("--auth-pause", type=float, default=1.5,
                        dest="auth_pause",
                        help="Pause zwischen Daten-Frame und AUTH-Frame in Sekunden "
                             "(Standard: 1.5)")
    args = parser.parse_args()

    # Falls --config explizit angegeben wurde und von GATEWAY_JSON abweicht,
    # Config neu laden und Argumente überschreiben die noch auf Defaults stehen.
    if args.config != GATEWAY_JSON:
        gw = load_gateway_config(args.config)
        if args.audio_device is None:
            args.audio_device = gw["audio_device"]
        if args.ptt == "null":
            args.ptt = gw["ptt"]
        if args.hamlib_host == "localhost":
            args.hamlib_host = gw["hamlib_host"]
        if args.hamlib_port == 4532:
            args.hamlib_port = gw["hamlib_port"]

    # Audio-Level aus gateway.json (kein CLI-Override, da selten geändert)
    args.audio_level = gw["audio_level"]

    # --channels parsen
    args.fixed_channels = None
    if args.channels:
        try:
            args.fixed_channels = [int(x) for x in args.channels.split(",")]
            assert all(0 <= c <= 9 for c in args.fixed_channels)
            assert 1 <= len(args.fixed_channels) <= 2
        except Exception:
            parser.error("--channels muss '0,7' (Dual) oder '3' (Einzel) sein, Kanäle 0–9")

    # --gain-sequence parsen
    args.gains = None
    if args.gain_sequence:
        try:
            args.gains = [int(x) for x in args.gain_sequence.split(",")]
            assert all(0 <= g <= 47 for g in args.gains)
        except Exception:
            parser.error("--gain-sequence muss z.B. '28,26,24,22,20' sein (0–47 dB)")
        args.count = len(args.gains)

    # --text-parts validieren
    if args.text_parts not in range(0, 5):
        parser.error("--text-parts muss 0 (zufällig) oder 1–4 sein")

    # --auth referenziert genau EINEN Daten-Frame → Einzel-Kanal erzwingen
    # (Dual-Kanal mischt zwei Frames im IQ → kein eindeutiger frame_body für HMAC).
    if args.auth:
        args.no_dual   = True
        args.dual_only = False

    run(args)


if __name__ == "__main__":
    main()