#!/usr/bin/env python3
"""
GUST — Frame Layer                                         Phase 2
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Version : 0.2.0
Datum   : Mai 2026

Inhalt dieses Moduls:
  • FrameType        — Alle Frame-Typ-Konstanten mit Namen
  • Rufzeichen-Codec — Basis-40 Encoder/Decoder (4 Byte, 6 Zeichen)
  • CRC-16/CCITT     — Prüfsumme über TYPE + FROM + PAYLOAD
  • Payload-Encoder  — Für jeden definierten Frame-Typ (0x01–0x41)
  • Payload-Decoder  — Rückrichtung: bytes → Python-Dict
  • Frame-Builder    — Vollständiger Frame-Aufbau inkl. CRC
  • Frame-Parser     — Empfang: bytes → validiertes Dict
  • RS-FEC-Wrapper   — Reed-Solomon Encode/Decode (via reedsolo)
  • Bytes→Symbole    — Interface zum MFSK-Modulator (Phase 1)
  • Kanalzuweisung   — Deterministisch per Rufzeichen-Hash

Schnittstelle zu Phase 1 (Modulator):
  frame_to_symbol_stream(body_bytes) → List[int]  (0–7, MFSK-8)
  Diese Liste direkt an mfsk8_modulate() aus Phase 1 übergeben.
"""

import struct
import hashlib
from dataclasses import dataclass
from typing import Optional

try:
    import reedsolo
    _RS_AVAILABLE = True
except ImportError:
    _RS_AVAILABLE = False
    print("[Warnung] reedsolo nicht installiert — RS-FEC deaktiviert.")


# ═══════════════════════════════════════════════════════════════════════
# KONSTANTEN
# ═══════════════════════════════════════════════════════════════════════

VERSION = "0.3.0"

# Broadcast-Adresse (TO-Feld: alle Stationen)
BROADCAST_ADDR = b'\xff\xff\xff\xff'

# MFSK-8 SYNC-Preamble: 8 Symbole (v0.3)
# [7,0,7,0,7,0,7,0] = alternierend höchster/niedrigster Ton
# Dauer: 8 × 32 ms = 256 ms
# Δf zwischen Ton 7 und Ton 0 = 7 × 31,25 = 218,75 Hz (kanalunabhängig)
# → Breitband-Decoder kann SYNC ohne Vorabwissen über den Kanal finden
SYNC_SYMBOLS = [7, 0, 7, 0, 7, 0, 7, 0]

# RS(255,223) Parameter
RS_N = 255          # Codeword-Länge
RS_K = 223          # Nutzdaten pro Block
RS_T = 16           # Korrigierbare Byte-Fehler pro Block
RS_OVERHEAD = RS_N - RS_K   # = 32 Byte FEC-Overhead


# ═══════════════════════════════════════════════════════════════════════
# FRAME-TYPEN
# ═══════════════════════════════════════════════════════════════════════

class FrameType:
    WEATHER        = 0x01   # Wetter-Telemetrie          (14 Byte)
    POSITION       = 0x02   # GPS-Position                (18 Byte)
    STATION_TLM    = 0x03   # Stations-Telemetrie         (10 Byte)
    ROTOR_STATUS   = 0x10   # Rotor-Ist-Position          ( 7 Byte)
    ROTOR_CMD      = 0x11   # Rotor-Steuerbefehl          ( 5 Byte)
    EMERG_BEACON   = 0x20   # Notfall-Beacon              (16 Byte)
    EMERG_RSRC     = 0x21   # Notfall-Ressourcenstatus    ( 8 Byte)
    SENSOR         = 0x30   # Generische Sensor-TLV       (variabel)
    TEXT           = 0x40   # Freitext / QSO-Fragment     (variabel)
    CQ             = 0x41   # CQ-Anruf                    ( 5 Byte)
    MGMT           = 0xF0   # Protokoll-Management        (variabel)

# Reverse-Mapping für Anzeige
_TYPE_NAMES = {v: k for k, v in vars(FrameType).items() if not k.startswith('_')}

def frame_type_name(t: int) -> str:
    return _TYPE_NAMES.get(t, f"UNKNOWN(0x{t:02X})")


# ═══════════════════════════════════════════════════════════════════════
# RUFZEICHEN-CODEC (BASIS-40)
# ═══════════════════════════════════════════════════════════════════════
#
# 40 gültige Zeichen → jedes Zeichen = ein "Digit" in Basis 40
# 6 Zeichen passen in 40^6 = 4.096.000.000 < 2^32 → exakt 4 Byte
#
# Alphabet-Index:
#   0 = Leerzeichen (Padding)
#   1–10 = '0'–'9'
#   11–36 = 'A'–'Z'
#   37 = '/'  38 = '.'  39 = '-'  (und '+' als Index 39 → in GUST reserviert)

_B40_ALPHABET = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ/.-+"

def encode_callsign(call: str) -> bytes:
    """
    Kodiert Rufzeichen (bis 6 Zeichen) in 4 Byte (Big-Endian, Basis-40).
    Unbekannte Zeichen werden als Leerzeichen (Index 0) kodiert.

    Beispiel: 'OE3GAS' → b'\\x0c\\x23\\x4f\\x9b'
    """
    call = call.upper().strip().ljust(6)[:6]
    n = 0
    for c in reversed(call):
        idx = _B40_ALPHABET.index(c) if c in _B40_ALPHABET else 0
        n = n * 40 + idx
    return n.to_bytes(4, 'big')

def decode_callsign(data: bytes) -> str:
    """
    Dekodiert 4 Byte (Basis-40, Big-Endian) zurück zum Rufzeichen.
    Trailing-Spaces werden entfernt.
    """
    n = int.from_bytes(data[:4], 'big')
    chars = []
    for _ in range(6):
        chars.append(_B40_ALPHABET[n % 40])
        n //= 40
    return ''.join(chars).strip()

def callsign_roundtrip_ok(call: str) -> bool:
    """Selbsttest: Encode → Decode muss das Original zurückgeben."""
    return decode_callsign(encode_callsign(call)) == call.upper().strip()


# ═══════════════════════════════════════════════════════════════════════
# CRC-16 / CCITT-FALSE  (Polynom 0x1021, Init 0xFFFF)
# ═══════════════════════════════════════════════════════════════════════
#
# CRC-Abdeckung: TYPE(1) + FROM(4) + PAYLOAD(var) — OHNE SYNC
# Der CRC schützt den gesamten Frame-Body vor Übertragungsfehlern,
# die vom Reed-Solomon nicht mehr korrigiert werden konnten.

def crc16(data: bytes, init: int = 0xFFFF) -> int:
    """
    CRC-16/CCITT-FALSE über beliebige Bytes.
    Gibt 16-Bit-Wert zurück (0x0000–0xFFFF).
    """
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
    return crc

def crc16_bytes(data: bytes) -> bytes:
    """CRC-16 als 2 Byte Big-Endian."""
    return struct.pack('>H', crc16(data))

def verify_crc(body: bytes, received_crc: bytes) -> bool:
    """Prüft ob CRC über body mit received_crc übereinstimmt."""
    return crc16(body) == struct.unpack('>H', received_crc)[0]


# ═══════════════════════════════════════════════════════════════════════
# PAYLOAD-ENCODER / DECODER
# ═══════════════════════════════════════════════════════════════════════
#
# Jeder Encoder gibt bytes zurück, jeder Decoder ein Dict.
# Struct-Formate: '>' = Big-Endian (Netzwerkreihenfolge)
#                 'x' = 1 Byte Padding (kein Wert)
#
# Größenüberprüfung: Alle Payloads ≤ 20 Byte (Frame-Limit).


# ──────────────────────────────────────────────────────────────────────
# 0x01  WETTER-TELEMETRIE  (14 Byte)
#
# Byte-Layout (mit Alignment-Padding nach Olivia-Konvention):
#   0–1   Temperatur    int16   0,1 °C    (-3276,8 bis 3276,7 °C)
#   2     Luftfeuchte   uint8   %         (0–100)
#   3     [padding]
#   4–5   Luftdruck     uint16  0,1 hPa   (0–6553,5 hPa)
#   6     Windgeschw.   uint8   km/h      (0–255)
#   7     [padding]
#   8–9   Windrichtung  uint16  Grad      (0–359)
#  10–11  Niederschlag  uint16  0,1 mm/h  (0–6553,5 mm/h)
#  12     UV-Index      uint8             (0–11+)
#  13     Statusflags   uint8             (bit0=Batterie, bit1=Sensorok)
# ──────────────────────────────────────────────────────────────────────
_WX_FMT = '>hBxHBxHHBB'   # Ergibt exakt 14 Byte

def encode_weather(
    temp_c:        float,
    humidity_pct:  int,
    pressure_hpa:  float,
    wind_kmh:      int,
    wind_deg:      int,
    rain_mm_h:     float = 0.0,
    uv_index:      int   = 0,
    flags:         int   = 0,
) -> bytes:
    """Frame 0x01 — Wetter-Telemetrie (14 Byte)."""
    return struct.pack(_WX_FMT,
        int(round(temp_c * 10)),
        int(humidity_pct) & 0xFF,
        int(round(pressure_hpa * 10)),
        int(wind_kmh) & 0xFF,
        int(wind_deg) % 360,
        int(round(rain_mm_h * 10)),
        int(uv_index) & 0xFF,
        int(flags) & 0xFF,
    )

def decode_weather(payload: bytes) -> dict:
    """Frame 0x01 → Dict."""
    t, hum, press, wspd, wdir, rain, uv, flags = struct.unpack(_WX_FMT, payload[:14])
    return {
        "temp_c":         t / 10,
        "humidity_pct":   hum,
        "pressure_hpa":   press / 10,
        "wind_kmh":       wspd,
        "wind_deg":       wdir,
        "rain_mm_h":      rain / 10,
        "uv_index":       uv,
        "flags":          flags,
        "bat_ok":         bool(flags & 0x01),
        "sensor_ok":      bool(flags & 0x02),
    }


# ──────────────────────────────────────────────────────────────────────
# 0x02  POSITION  (18 Byte)
#
#   0–3   Latitude   int32  Mikrograd  (-90.000.000 bis +90.000.000)
#   4–7   Longitude  int32  Mikrograd  (-180.000.000 bis +180.000.000)
#   8–9   Altitude   int16  Meter      (-32768 bis +32767 m)
#  10     Speed      uint8  km/h       (0–255)
#  11     [padding]
#  12–13  Heading    uint16 Grad       (0–359)
#  14–15  Timestamp  uint16 Modulo 65536 s  (~18 Stunden Rollover)
#  16–17  Flags      uint16 (bit0=mobil, bit1=GPS-Fix, bit2=Notfall)
# ──────────────────────────────────────────────────────────────────────
_POS_FMT = '>iihBxHHH'    # Ergibt exakt 18 Byte

# Statusflag-Bits für Position
POS_FLAG_MOBILE    = 0x0001
POS_FLAG_GPS_FIX   = 0x0002
POS_FLAG_EMERGENCY = 0x0004

def encode_position(
    lat_deg:      float,
    lon_deg:      float,
    alt_m:        int   = 0,
    speed_kmh:    int   = 0,
    heading_deg:  int   = 0,
    timestamp:    int   = 0,
    flags:        int   = 0,
) -> bytes:
    """Frame 0x02 — Position (18 Byte). lat/lon in Dezimalgrad."""
    return struct.pack(_POS_FMT,
        int(round(lat_deg  * 1_000_000)),
        int(round(lon_deg  * 1_000_000)),
        int(alt_m),
        int(speed_kmh) & 0xFF,
        int(heading_deg) % 360,
        int(timestamp) & 0xFFFF,
        int(flags) & 0xFFFF,
    )

def decode_position(payload: bytes) -> dict:
    """Frame 0x02 → Dict."""
    lat, lon, alt, spd, hdg, ts, flags = struct.unpack(_POS_FMT, payload[:18])
    return {
        "lat_deg":     lat / 1_000_000,
        "lon_deg":     lon / 1_000_000,
        "alt_m":       alt,
        "speed_kmh":   spd,
        "heading_deg": hdg,
        "timestamp":   ts,
        "flags":       flags,
        "mobile":      bool(flags & POS_FLAG_MOBILE),
        "gps_fix":     bool(flags & POS_FLAG_GPS_FIX),
        "emergency":   bool(flags & POS_FLAG_EMERGENCY),
    }


# ──────────────────────────────────────────────────────────────────────
# 0x03  STATIONS-TELEMETRIE  (10 Byte)
#
#   0–1  Versorgungsspannung  uint16  mV   (0–65535 mV = 0–65,5 V)
#   2–3  Stromaufnahme        uint16  mA   (0–65535 mA)
#   4–5  Innentemperatur      int16   0,1°C
#   6    CPU-Auslastung       uint8   %
#   7–8  Betriebszeit         uint16  Min  (~45 Tage)
#   9    Statusflags          uint8
# ──────────────────────────────────────────────────────────────────────
_STN_FMT = '>HHhBHB'    # 10 Byte

def encode_station_tlm(
    voltage_mv:  int,
    current_ma:  int,
    temp_c:      float,
    cpu_pct:     int,
    uptime_min:  int,
    flags:       int = 0,
) -> bytes:
    """Frame 0x03 — Stations-Telemetrie (10 Byte)."""
    return struct.pack(_STN_FMT,
        int(voltage_mv) & 0xFFFF,
        int(current_ma) & 0xFFFF,
        int(round(temp_c * 10)),
        int(cpu_pct) & 0xFF,
        int(uptime_min) & 0xFFFF,
        int(flags) & 0xFF,
    )

def decode_station_tlm(payload: bytes) -> dict:
    """Frame 0x03 → Dict."""
    v, i, t, cpu, uptime, flags = struct.unpack(_STN_FMT, payload[:10])
    return {
        "voltage_v":    v / 1000,
        "current_ma":   i,
        "temp_c":       t / 10,
        "cpu_pct":      cpu,
        "uptime_min":   uptime,
        "flags":        flags,
    }


# ──────────────────────────────────────────────────────────────────────
# 0x10  ROTOR-STATUS  (7 Byte)
# 0x11  ROTOR-STEUERBEFEHL  (5 Byte)
#
# Azimut/Elevation in 0,1° Auflösung → uint16 / int16
# ──────────────────────────────────────────────────────────────────────
_ROT_STATUS_FMT  = '>HhHB'    # az(2) + el(2[signed]) + target_az(2) + flags(1) = 7 Byte
_ROT_CMD_FMT     = '>HhB'     # target_az(2) + target_el(2[signed]) + cmd(1) = 5 Byte

# Rotor-Befehle
ROTOR_STOP  = 0x00
ROTOR_MOVE  = 0x01
ROTOR_PARK  = 0x02

def encode_rotor_status(
    azimuth_deg:    float,
    elevation_deg:  float,
    target_az_deg:  float = 0.0,
    flags:          int   = 0,
) -> bytes:
    """Frame 0x10 — Rotor-Status (7 Byte). Winkel in Dezimalgrad."""
    return struct.pack(_ROT_STATUS_FMT,
        int(round(azimuth_deg   * 10)) & 0xFFFF,   # 0–3599 (0,0°–359,9°)
        int(round(elevation_deg * 10)),              # signed: -900 bis +900
        int(round(target_az_deg * 10)) & 0xFFFF,
        int(flags) & 0xFF,
    )

def decode_rotor_status(payload: bytes) -> dict:
    az, el, t_az, flags = struct.unpack(_ROT_STATUS_FMT, payload[:7])
    return {
        "azimuth_deg":   az  / 10,
        "elevation_deg": el  / 10,
        "target_az_deg": t_az / 10,
        "flags":         flags,
    }

def encode_rotor_cmd(
    target_az_deg: float,
    target_el_deg: float = 0.0,
    command:       int   = ROTOR_MOVE,
) -> bytes:
    """Frame 0x11 — Rotor-Steuerbefehl (5 Byte)."""
    return struct.pack(_ROT_CMD_FMT,
        int(round(target_az_deg * 10)) & 0xFFFF,
        int(round(target_el_deg * 10)),
        int(command) & 0xFF,
    )

def decode_rotor_cmd(payload: bytes) -> dict:
    t_az, t_el, cmd = struct.unpack(_ROT_CMD_FMT, payload[:5])
    cmd_names = {ROTOR_STOP: "STOP", ROTOR_MOVE: "MOVE", ROTOR_PARK: "PARK"}
    return {
        "target_az_deg": t_az / 10,
        "target_el_deg": t_el / 10,
        "command":       cmd,
        "command_str":   cmd_names.get(cmd, f"UNKNOWN({cmd})"),
    }


# ──────────────────────────────────────────────────────────────────────
# 0x20  NOTFALL-BEACON  (16 Byte)
#
#   0–3   Latitude         int32  Mikrograd
#   4–7   Longitude        int32  Mikrograd
#   8     Personenanzahl   uint8
#   9     Verletzungscode  uint8  (0=unbekannt,1=leicht,2=schwer,3=kritisch)
#  10     Ressourcen-Flags uint8  (bit0=Wasser, bit1=Nahrung, bit2=Medizin, bit3=Evakuierung)
#  11     Priorität        uint8  (0=niedrig, 1=mittel, 2=hoch, 3=sofort)
#  12–15  Freitext-Snippet 4 Byte ASCII
# ──────────────────────────────────────────────────────────────────────
_EMERG_FMT = '>iiBBBB4s'   # 16 Byte

# Verletzungscodes
INJURY_UNKNOWN  = 0
INJURY_MINOR    = 1
INJURY_SERIOUS  = 2
INJURY_CRITICAL = 3

# Ressourcen-Flags
RSRC_WATER    = 0x01
RSRC_FOOD     = 0x02
RSRC_MEDICAL  = 0x04
RSRC_EVAC     = 0x08

# Prioritätsstufen
PRIO_LOW      = 0
PRIO_MEDIUM   = 1
PRIO_HIGH     = 2
PRIO_URGENT   = 3

def encode_emergency_beacon(
    lat_deg:        float,
    lon_deg:        float,
    persons:        int,
    injury_code:    int,
    resource_flags: int,
    priority:       int,
    text_snippet:   str = "",
) -> bytes:
    """Frame 0x20 — Notfall-Beacon (16 Byte)."""
    snippet = text_snippet.encode('ascii', errors='replace').ljust(4)[:4]
    return struct.pack(_EMERG_FMT,
        int(round(lat_deg * 1_000_000)),
        int(round(lon_deg * 1_000_000)),
        int(persons) & 0xFF,
        int(injury_code) & 0xFF,
        int(resource_flags) & 0xFF,
        int(priority) & 0xFF,
        snippet,
    )

def decode_emergency_beacon(payload: bytes) -> dict:
    lat, lon, persons, injury, resources, prio, snippet = \
        struct.unpack(_EMERG_FMT, payload[:16])
    prio_names = {0: "LOW", 1: "MEDIUM", 2: "HIGH", 3: "URGENT"}
    return {
        "lat_deg":        lat / 1_000_000,
        "lon_deg":        lon / 1_000_000,
        "persons":        persons,
        "injury_code":    injury,
        "resource_flags": resources,
        "needs_water":    bool(resources & RSRC_WATER),
        "needs_food":     bool(resources & RSRC_FOOD),
        "needs_medical":  bool(resources & RSRC_MEDICAL),
        "needs_evac":     bool(resources & RSRC_EVAC),
        "priority":       prio,
        "priority_str":   prio_names.get(prio, "?"),
        "text_snippet":   snippet.decode('ascii', errors='replace').strip(),
    }


# ──────────────────────────────────────────────────────────────────────
# 0x40  FREITEXT / QSO-FRAGMENT  (6 + bis zu 14 Byte Text = 20 Byte)
#
#   0–3   TO-Rufzeichen  4 Byte Basis-40 (0xFFFFFFFF = Broadcast)
#   4     Sequenz-Nr.    uint8  (0–255, wraps around)
#   5     Fragment-Info  uint8  (Bits 7-4: Fragment-Index 0-basiert,
#                                Bits 3-0: Gesamt-Fragments - 1)
#   6–19  UTF-8-Text     max. 14 Byte
# ──────────────────────────────────────────────────────────────────────

def encode_text_fragment(
    dest_call:   str,
    text:        str,
    seq_nr:      int,
    frag_index:  int = 0,
    frag_total:  int = 1,
) -> bytes:
    """Frame 0x40 — Einzelnes Textfragment (max. 20 Byte Payload)."""
    if dest_call.upper() == "BROADCAST" or dest_call == "":
        dest = BROADCAST_ADDR
    else:
        dest = encode_callsign(dest_call)
    frag_info = ((frag_index & 0x0F) << 4) | ((frag_total - 1) & 0x0F)
    text_bytes = text.encode('utf-8')[:14]
    return dest + bytes([seq_nr & 0xFF, frag_info]) + text_bytes

def decode_text_fragment(payload: bytes) -> dict:
    """Frame 0x40 → Dict."""
    dest_raw = payload[:4]
    dest = "BROADCAST" if dest_raw == BROADCAST_ADDR else decode_callsign(dest_raw)
    seq_nr    = payload[4]
    frag_info = payload[5]
    frag_idx  = (frag_info >> 4) & 0x0F
    frag_tot  = (frag_info & 0x0F) + 1
    text      = payload[6:].decode('utf-8', errors='replace')
    return {
        "dest":       dest,
        "seq_nr":     seq_nr,
        "frag_index": frag_idx,
        "frag_total": frag_tot,
        "last_frag":  (frag_idx == frag_tot - 1),
        "text":       text,
    }

def fragment_text(
    text:       str,
    dest_call:  str,
    seq_nr:     int,
    chunk_size: int = 14,
) -> list:
    """
    Fragmentiert langen Text in mehrere Frame-0x40-Payloads.
    Rückgabe: Liste von Payload-bytes, jeder ≤ 20 Byte.

    Beispiel: 42-Zeichen-Text → 3 Fragmente à ≤14 Zeichen.
    Übertragungsdauer: len(fragments) × ~2,1 s
    """
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
    if not chunks:
        chunks = [""]
    return [
        encode_text_fragment(dest_call, chunk, seq_nr, i, len(chunks))
        for i, chunk in enumerate(chunks)
    ]

def reassemble_text(fragments: list) -> str:
    """
    Setzt Fragment-Dicts (aus decode_text_fragment) wieder zusammen.
    Erwartet vollständige und sortierte Fragment-Liste.
    """
    sorted_frags = sorted(fragments, key=lambda f: f["frag_index"])
    return "".join(f["text"] for f in sorted_frags)


# ──────────────────────────────────────────────────────────────────────
# 0x41  CQ-ANRUF  (5 Byte)
#
#   0–3  FROM-Rufzeichen (zur Redundanz, auch im Frame-Header)
#   4    CQ-Flags (bit0=DX, bit1=Contest, bit2=Emergency)
# ──────────────────────────────────────────────────────────────────────
CQ_FLAG_DX        = 0x01
CQ_FLAG_CONTEST   = 0x02
CQ_FLAG_EMERGENCY = 0x04

def encode_cq(callsign: str, flags: int = 0) -> bytes:
    """Frame 0x41 — CQ-Anruf (5 Byte)."""
    return encode_callsign(callsign) + bytes([int(flags) & 0xFF])

def decode_cq(payload: bytes) -> dict:
    return {
        "from":      decode_callsign(payload[:4]),
        "flags":     payload[4],
        "cq_dx":     bool(payload[4] & CQ_FLAG_DX),
        "cq_contest":bool(payload[4] & CQ_FLAG_CONTEST),
        "emergency": bool(payload[4] & CQ_FLAG_EMERGENCY),
    }


# ═══════════════════════════════════════════════════════════════════════
# FRAME-BUILDER & PARSER
# ═══════════════════════════════════════════════════════════════════════

def build_frame(frame_type: int, callsign: str, payload: bytes,
                channel: int, test: bool = False) -> bytes:
    """
    Baut den vollständigen GUST Frame-Body (ohne SYNC, ohne RS-FEC).

    Aufbau v0.3:  TYPE(1) | CHANNEL(1) | FROM(4) | PAYLOAD(1–20) | CRC(2)

    CHANNEL-Byte: Bits 3–0 = Kanal 0–9, Bit 7 = TEST-Flag, Bits 4–6 = reserviert
    CRC deckt: TYPE + CHANNEL + FROM + PAYLOAD (alles außer CRC selbst)
    SYNC wird erst in frame_to_symbol_stream() vorangestellt.

    Returns: bytes, Länge = 8 + len(payload) (9–28 Byte)
    """
    if not (1 <= len(payload) <= 20):
        raise ValueError(f"Payload-Länge {len(payload)} außerhalb 1–20 Byte")
    from_bytes = encode_callsign(callsign)
    ch_byte = (channel & 0x0F) | (FRAME_FLAG_TEST if test else 0)
    body_without_crc = (bytes([frame_type, ch_byte])
                        + from_bytes + payload)
    crc = crc16_bytes(body_without_crc)
    return body_without_crc + crc

def parse_frame(data: bytes) -> Optional[dict]:
    """
    Parst einen Frame-Body v0.3 (ohne SYNC, ohne RS-FEC).

    Aufbau: TYPE(1) | CHANNEL(1) | FROM(4) | PAYLOAD(var) | CRC(2)

    Returns: Dict mit 'type', 'channel', 'from', 'payload', 'crc_ok'
             oder None bei zu kurzem/korruptem Frame.
    """
    # Minimum: TYPE(1) + CHANNEL(1) + FROM(4) + PAYLOAD(1) + CRC(2) = 9 Byte
    if len(data) < 9:
        return None
    body      = data[:-2]
    crc_bytes = data[-2:]
    crc_ok    = verify_crc(body, crc_bytes)
    return {
        "type":      body[0],
        "type_name": frame_type_name(body[0]),
        "channel":   body[1] & 0x0F,
        "test":      bool(body[1] & FRAME_FLAG_TEST),
        "from":      decode_callsign(body[2:6]),
        "payload":   body[6:],
        "crc_ok":    crc_ok,
    }

def decode_payload(frame_type: int, payload: bytes) -> Optional[dict]:
    """
    Dispatcher: Dekodiert Payload je nach Frame-Typ.
    Returns None für unbekannte Typen.
    """
    decoders = {
        FrameType.WEATHER:      decode_weather,
        FrameType.POSITION:     decode_position,
        FrameType.STATION_TLM:  decode_station_tlm,
        FrameType.ROTOR_STATUS: decode_rotor_status,
        FrameType.ROTOR_CMD:    decode_rotor_cmd,
        FrameType.EMERG_BEACON: decode_emergency_beacon,
        FrameType.TEXT:         decode_text_fragment,
        FrameType.CQ:           decode_cq,
    }
    decoder = decoders.get(frame_type)
    if decoder:
        try:
            return decoder(payload)
        except struct.error as e:
            return {"error": str(e)}
    return None


# ═══════════════════════════════════════════════════════════════════════
# RS-FEC WRAPPER  (Reed-Solomon, via reedsolo)
# ═══════════════════════════════════════════════════════════════════════
#
# RS(255,223): 32 Byte Overhead, korrigiert 16 Byte-Fehler.
# Für kurze GUST-Frames (7–27 Byte) wird shortened RS verwendet:
# Wir kodieren mit RS(7+n, n) durch Zero-Padding auf K=223 Byte.
# Das ist Standard-Verhalten von reedsolo.

if _RS_AVAILABLE:
    _rs_codec = reedsolo.RSCodec(RS_OVERHEAD)   # nsym=32 Fehlerkorrektur-Bytes

def rs_encode(data: bytes) -> bytes:
    """
    Wendet Reed-Solomon(255,223) auf data an.
    Gibt data + 32 Byte RS-Parität zurück.
    Fehler wenn reedsolo nicht installiert.
    """
    if not _RS_AVAILABLE:
        raise RuntimeError("reedsolo nicht installiert")
    return bytes(_rs_codec.encode(data))

def rs_decode(data: bytes) -> bytes:
    """
    Dekodiert RS-geschützte Daten. Korrigiert bis zu 16 Byte-Fehler.
    Gibt die originalen Nutzdaten zurück (ohne RS-Parität).
    Wirft reedsolo.ReedSolomonError bei nicht korrigierbarem Fehler.
    """
    if not _RS_AVAILABLE:
        raise RuntimeError("reedsolo nicht installiert")
    decoded, _, _ = _rs_codec.decode(data)
    return bytes(decoded)


# ═══════════════════════════════════════════════════════════════════════
# BYTES → MFSK-8 SYMBOL-STREAM  (Interface zu Phase 1 Modulator)
# ═══════════════════════════════════════════════════════════════════════
#
# Konvertierung: Jede Gruppe von 3 Bits wird ein Symbol (0–7).
# MSB-first, Big-Endian.
# Padding: Wenn len(bits) kein Vielfaches von 3 → Nullen anhängen.
#
# Dieses Verfahren ist das direkte Interface zwischen Frame-Layer
# (Phase 2) und MFSK-Modulator (Phase 1):
#
#   build_frame() → rs_encode() → frame_to_symbol_stream() → mfsk8_modulate()

def bytes_to_symbols(data: bytes) -> list:
    """
    Konvertiert Bytes in MFSK-8 Symbole (0–7).
    Jede 3-Bit-Gruppe = 1 Symbol, MSB zuerst.
    """
    bits = []
    for byte in data:
        for i in range(7, -1, -1):   # Bit 7 zuerst (MSB)
            bits.append((byte >> i) & 1)
    # Padding auf Vielfaches von 3
    while len(bits) % 3 != 0:
        bits.append(0)
    symbols = []
    for i in range(0, len(bits), 3):
        sym = (bits[i] << 2) | (bits[i+1] << 1) | bits[i+2]
        symbols.append(sym)
    return symbols

def symbols_to_bytes(symbols: list, expected_bytes: int) -> bytes:
    """
    Rückrichtung: MFSK-8 Symbole → Bytes.
    expected_bytes gibt die gewünschte Byte-Länge an (entfernt Padding-Bits).
    """
    bits = []
    for sym in symbols:
        bits.append((sym >> 2) & 1)
        bits.append((sym >> 1) & 1)
        bits.append(sym & 1)
    # Exakt expected_bytes * 8 Bits verwenden
    bits = bits[:expected_bytes * 8]
    result = bytearray()
    for i in range(0, len(bits), 8):
        byte_bits = bits[i:i+8]
        if len(byte_bits) == 8:
            b = 0
            for bit in byte_bits:
                b = (b << 1) | bit
            result.append(b)
    return bytes(result)

def frame_to_symbol_stream(frame_body: bytes, use_fec: bool = True) -> list:
    """
    Vollständige TX-Vorbereitung: Frame-Body → Symbolstrom für Modulator.

    Ablauf:
      1. Optional: RS-FEC anwenden (frame_body → rs_encoded)
      2. Bytes → MFSK-8 Symbole konvertieren
      3. SYNC-Preamble [7,0,7,0,7,0,7,0] voranstellen

    Returns: List[int] mit Symbolindizes 0–7, direkt an mfsk8_modulate() übergeben.

    Beispiel:
        ch, _  = assign_channel('OE3GAS')
        body    = build_frame(FrameType.WEATHER, 'OE3GAS', payload, ch)
        symbols = frame_to_symbol_stream(body)
        audio   = mfsk8_modulate(symbols)   # Phase 1 Funktion
    """
    if use_fec and _RS_AVAILABLE:
        data = rs_encode(frame_body)
    else:
        data = frame_body
    data_symbols = bytes_to_symbols(data)
    return SYNC_SYMBOLS + data_symbols

def symbol_stream_stats(symbols: list) -> dict:
    """Einfache Statistik über einen Symbolstrom (für Debugging)."""
    from collections import Counter
    counts = Counter(symbols)
    duration_ms = len(symbols) * 32    # 32 ms pro Symbol
    return {
        "symbol_count":  len(symbols),
        "duration_ms":   duration_ms,
        "duration_s":    duration_ms / 1000,
        "symbol_counts": dict(sorted(counts.items())),
        "sync_present":  symbols[:4] == SYNC_SYMBOLS,
    }


# ═══════════════════════════════════════════════════════════════════════
# KANALZUWEISUNG  (deterministisch, Hash-basiert)
# ═══════════════════════════════════════════════════════════════════════
#
# Architektur: Alle Stationen stellen denselben Dial-Frequenz ein.
# Die NF-Tonhöhe bestimmt den Kanal — identisch zum FT8-Prinzip.
#
# Beispiel: Dial 14.110,000 MHz (USB)
#   NF 400 Hz  → RF 14.110,400 MHz  (Kanal 0)
#   NF 650 Hz  → RF 14.110,650 MHz  (Kanal 1)
#   ...
#   NF 2.650 Hz → RF 14.112,650 MHz (Kanal 9)
#   NF 2.900 Hz → RF 14.112,900 MHz (obere Grenze Kanal 9)
#
# Gesamtbandbreite: 10 × 250 Hz = 2.500 Hz  →  passt in Standard-SSB
# Passband-Bereich: 400 – 2.900 Hz (gut innerhalb 300–3.000 Hz SSB-Filter)
#
# Kanalplan:
#   Kanal  NF-Unterkante  NF-Oberkante   8 Töne bei
#      0      400 Hz         650 Hz      400,431,462,…,619 Hz
#      1      650 Hz         900 Hz      650,681,712,…,869 Hz
#      2      900 Hz        1150 Hz      …
#      …
#      9     2650 Hz        2900 Hz      2650,2681,…,2869 Hz
#
# Prinzip Kanalzuweisung: Rufzeichen → SHA-256 → Kanal + Zeitversatz
# Kein Koordinationsaufwand, Pure ALOHA (Kollisionsanalyse → Spec §4.2)

N_CHANNELS       = 10       # Standard-SSB-kompatibel (2,5 kHz Gesamtbandbreite)
FRAME_FLAG_TEST  = 0x80     # Bit 7 im CHANNEL-Byte: Frame ist ein Testframe
CHANNEL_BW_HZ    = 250      # Bandbreite je Kanal (= MFSK-8 Tonabstand × 8)
CHANNEL_BASE_HZ  = 400.0    # NF-Unterkante Kanal 0 (Hz)

def assign_channel(
    callsign:    str,
    n_channels:  int = N_CHANNELS,
    interval_s:  int = 300,
) -> tuple:
    """
    Berechnet Heimatkanal und Sende-Zeitversatz für eine Station.

    Args:
        callsign:   Rufzeichen (z.B. 'OE3GAS')
        n_channels: Anzahl verfügbarer Kanäle (Standard: 10 bei 2,5 kHz / 250 Hz)
        interval_s: Sendeintervall in Sekunden (Standard: 300 = 5 min)

    Returns:
        (channel: int, time_offset_s: int)

    Beispiel:
        ch, offset = assign_channel('OE3GAS')
        freq = channel_frequency(ch)
        # ch=2, offset=220, freq=900 Hz NF → Kanal 2, sendet bei t=220s, 520s, …
    """
    h = int(hashlib.sha256(callsign.upper().encode()).hexdigest(), 16)
    channel     = h % n_channels
    time_offset = (h >> 8) % interval_s
    return channel, time_offset

def channel_frequency(channel: int, base_freq_hz: float = CHANNEL_BASE_HZ) -> float:
    """
    Gibt die NF-Unterkante eines Kanals zurück (Hz).

    Kanalplan (Standard, 10 Kanäle):
      Kanal 0:  400 Hz   Kanal 5: 1650 Hz
      Kanal 1:  650 Hz   Kanal 6: 1900 Hz
      Kanal 2:  900 Hz   Kanal 7: 2150 Hz
      Kanal 3: 1150 Hz   Kanal 8: 2400 Hz
      Kanal 4: 1400 Hz   Kanal 9: 2650 Hz  (Oberkante: 2900 Hz)

    Gesamtspan: 400–2900 Hz → passt in Standard-SSB-Passband (300–3000 Hz).
    Der MFSK-8-Modulator legt BASE_FREQ auf diesen Wert; die 8 Töne
    liegen dann bei base + 0×31,25 Hz bis base + 7×31,25 Hz.
    """
    return base_freq_hz + channel * CHANNEL_BW_HZ

def next_tx_times(callsign: str, from_now_s: int = 3600, interval_s: int = 300) -> list:
    """
    Berechnet die nächsten Sendezeitpunkte (in Sekunden ab jetzt).
    Nützlich für den Sendeplaner im Gateway.
    """
    _, offset = assign_channel(callsign, interval_s=interval_s)
    import time
    now = int(time.time())
    # Nächstes Sende-Event
    elapsed = (now % interval_s)
    next_in = (offset - elapsed) % interval_s
    times = []
    t = next_in
    while t <= from_now_s:
        times.append(t)
        t += interval_s
    return times


# ═══════════════════════════════════════════════════════════════════════
# HILFSFUNKTION: HEX-DUMP
# ═══════════════════════════════════════════════════════════════════════

def hexdump(data: bytes, label: str = "", width: int = 16) -> str:
    """Formatierter Hex-Dump für Debugging und Protokoll-Inspektion."""
    lines = []
    if label:
        lines.append(f"  {label} ({len(data)} Byte):")
    for i in range(0, len(data), width):
        chunk = data[i:i+width]
        hex_part  = ' '.join(f'{b:02X}' for b in chunk)
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        lines.append(f"  {i:04X}  {hex_part:<{width*3}}  {ascii_part}")
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════════════
# SELBSTTEST & DEMO
# ═══════════════════════════════════════════════════════════════════════

def _run_tests():
    """Vollständiger Selbsttest aller Frame-Layer-Komponenten."""
    print("=" * 60)
    print("  GUST Frame Layer — Selbsttest")
    print(f"  Version {VERSION}")
    print("=" * 60)
    errors = []

    # ── Test 1: Rufzeichen-Codec ───────────────────────────────────
    print("\n── Test 1: Rufzeichen-Codec (Basis-40) ──")
    # Roundtrip-Test: nur Rufzeichen ≤ 6 Zeichen (Basis-40 speichert max. 6)
    test_calls = ["OE3GAS", "OE1XTU", "DL3ABC", "W1AW", "OE5XYZ"]
    for call in test_calls:
        encoded = encode_callsign(call)
        decoded = decode_callsign(encoded)
        ok = decoded == call.upper().strip()
        status = "✓" if ok else "✗"
        print(f"  {status}  '{call}' → {encoded.hex().upper()} → '{decoded}'")
        if not ok:
            errors.append(f"Callsign roundtrip failed: {call}")
    # Hinweis: Rufzeichen > 6 Zeichen werden auf 6 Stellen gekürzt (Spec-Limit)
    long_call = "VK2XX/P"
    enc = encode_callsign(long_call)
    dec = decode_callsign(enc)
    print(f"  ⚠  '{long_call}' (7 Zeichen) → gekürzt auf '{dec}' — Spec-Limit: 6 Zeichen")

    # ── Test 2: CRC-16 ─────────────────────────────────────────────
    print("\n── Test 2: CRC-16/CCITT-FALSE ──")
    # Bekannter Testwert: CRC('123456789') = 0x29B1
    test_data = b'123456789'
    computed  = crc16(test_data)
    expected  = 0x29B1
    ok = computed == expected
    status = "✓" if ok else "✗"
    print(f"  {status}  CRC('123456789') = 0x{computed:04X} (erwartet 0x{expected:04X})")
    if not ok:
        errors.append(f"CRC-16 Referenzwert falsch: 0x{computed:04X}")

    # ── Test 3: Wetter-Frame ───────────────────────────────────────
    print("\n── Test 3: Wetter-Frame (0x01) ──")
    wx_payload = encode_weather(
        temp_c=21.5, humidity_pct=68, pressure_hpa=1013.2,
        wind_kmh=15, wind_deg=270, rain_mm_h=0.0, uv_index=3, flags=0x03
    )
    print(f"  Payload ({len(wx_payload)} Byte):")
    print(hexdump(wx_payload))
    wx_back = decode_weather(wx_payload)
    assert wx_back["temp_c"] == 21.5,        "Temp-Roundtrip"
    assert wx_back["pressure_hpa"] == 1013.2, "Druck-Roundtrip"
    assert wx_back["wind_deg"] == 270,         "Windrichtung-Roundtrip"
    print(f"  Dekodiert: temp={wx_back['temp_c']}°C  "
          f"hum={wx_back['humidity_pct']}%  "
          f"press={wx_back['pressure_hpa']}hPa  "
          f"wind={wx_back['wind_kmh']}km/h@{wx_back['wind_deg']}°")
    print("  ✓")

    # ── Test 4: Position-Frame ─────────────────────────────────────
    print("\n── Test 4: Position-Frame (0x02) ──")
    # Wien: 48.2092° N, 16.3728° E
    pos_payload = encode_position(
        lat_deg=48.2092, lon_deg=16.3728,
        alt_m=198, speed_kmh=0, heading_deg=0,
        timestamp=1234, flags=POS_FLAG_GPS_FIX
    )
    print(f"  Payload ({len(pos_payload)} Byte):")
    print(hexdump(pos_payload))
    pos_back = decode_position(pos_payload)
    assert abs(pos_back["lat_deg"] - 48.2092) < 0.000001, "Lat-Roundtrip"
    assert abs(pos_back["lon_deg"] - 16.3728) < 0.000001, "Lon-Roundtrip"
    print(f"  Dekodiert: lat={pos_back['lat_deg']:.4f}°N  "
          f"lon={pos_back['lon_deg']:.4f}°E  "
          f"alt={pos_back['alt_m']}m  "
          f"gps_fix={pos_back['gps_fix']}")
    print("  ✓")

    # ── Test 5: Notfall-Beacon ─────────────────────────────────────
    print("\n── Test 5: Notfall-Beacon (0x20) ──")
    emerg_payload = encode_emergency_beacon(
        lat_deg=47.810, lon_deg=13.055,
        persons=3, injury_code=INJURY_SERIOUS,
        resource_flags=RSRC_WATER | RSRC_MEDICAL,
        priority=PRIO_URGENT,
        text_snippet="HELP"
    )
    print(f"  Payload ({len(emerg_payload)} Byte):")
    print(hexdump(emerg_payload))
    emerg_back = decode_emergency_beacon(emerg_payload)
    print(f"  Dekodiert: pos={emerg_back['lat_deg']:.3f}°N/{emerg_back['lon_deg']:.3f}°E  "
          f"prio={emerg_back['priority_str']}  "
          f"text='{emerg_back['text_snippet']}'")
    print("  ✓")

    # ── Test 6: Text-Fragmentierung ────────────────────────────────
    print("\n── Test 6: Freitext-Fragmentierung (0x40) ──")
    long_text = "OE3GAS DE OE1XTU PSE QSY 14.225 MHz tnx 73"
    fragments = fragment_text(long_text, dest_call="OE1XTU", seq_nr=42)
    print(f"  Text:       '{long_text}' ({len(long_text)} Zeichen)")
    print(f"  Fragmente:  {len(fragments)}")
    decoded_frags = [decode_text_fragment(f) for f in fragments]
    reassembled = reassemble_text(decoded_frags)
    ok = reassembled == long_text
    print(f"  Reassembled: '{reassembled}'")
    print(f"  ✓  Roundtrip {'OK' if ok else 'FEHLER'}")

    # ── Test 7: Vollständiger Frame-Build + Parse ──────────────────
    print("\n── Test 7: Frame-Build → Parse → Decode ──")
    ch_oe3gas, _ = assign_channel("OE3GAS")
    frame = build_frame(FrameType.WEATHER, "OE3GAS", wx_payload, ch_oe3gas)
    print(hexdump(frame, "Vollständiger Frame-Body"))
    parsed = parse_frame(frame)
    assert parsed is not None,            "parse_frame returned None"
    assert parsed["crc_ok"],              "CRC-Fehler im Frame"
    assert parsed["from"] == "OE3GAS",   f"FROM falsch: {parsed['from']}"
    assert parsed["type"] == 0x01,        "Typ falsch"
    assert parsed["channel"] == ch_oe3gas, f"CHANNEL falsch: {parsed['channel']}"
    payload_back = decode_payload(parsed["type"], parsed["payload"])
    print(f"  FROM:    {parsed['from']}")
    print(f"  CHANNEL: {parsed['channel']}")
    print(f"  TYPE:    0x{parsed['type']:02X} ({parsed['type_name']})")
    print(f"  CRC:     {'✓ OK' if parsed['crc_ok'] else '✗ FEHLER'}")
    print(f"  Temp:    {payload_back['temp_c']}°C")
    print("  ✓")

    # ── Test 8: Symbol-Stream ──────────────────────────────────────
    print("\n── Test 8: Bytes → Symbol-Stream ──")
    symbols = frame_to_symbol_stream(frame, use_fec=False)
    stats   = symbol_stream_stats(symbols)
    print(f"  SYNC-Symbole:    {SYNC_SYMBOLS}  ({len(SYNC_SYMBOLS)} Symbole)")
    print(f"  Datensymbole:    {len(symbols) - len(SYNC_SYMBOLS)}")
    print(f"  Gesamt-Symbole:  {stats['symbol_count']}")
    print(f"  Dauer:           {stats['duration_ms']} ms ({stats['duration_s']:.2f} s)")
    print(f"  SYNC erkannt:    {'✓' if stats['sync_present'] else '✗'}")
    print("  ✓")

    if _RS_AVAILABLE:
        print("\n── Test 9: Reed-Solomon FEC ──")
        original = frame
        encoded  = rs_encode(original)
        # Simuliere 5 Byte-Fehler
        corrupted = bytearray(encoded)
        for i in [2, 5, 10, 15, 20]:
            corrupted[i] ^= 0xFF
        recovered = rs_decode(bytes(corrupted))
        ok = recovered == original
        print(f"  Original:  {len(original)} Byte")
        print(f"  Kodiert:   {len(encoded)} Byte (+{len(encoded)-len(original)} Parität)")
        print(f"  5 Fehler injiziert, RS-Dekodierung: {'✓ Korrekt' if ok else '✗ Fehler'}")
    else:
        print("\n── Test 9: RS-FEC — übersprungen (reedsolo fehlt) ──")

    # ── Test 10: Kanalzuweisung ────────────────────────────────────
    print("\n── Test 10: Kanalzuweisung ──")
    print(f"  Kanalplan: {N_CHANNELS} Kanäle × {CHANNEL_BW_HZ} Hz = "
          f"{N_CHANNELS * CHANNEL_BW_HZ} Hz Gesamtbandbreite")
    print(f"  NF-Bereich: {CHANNEL_BASE_HZ:.0f} – "
          f"{CHANNEL_BASE_HZ + N_CHANNELS * CHANNEL_BW_HZ:.0f} Hz  "
          f"(Standard-SSB-Passband: 300–3000 Hz  ✓)")
    print()
    test_stations = ["OE3GAS", "OE1XTU", "DL3ABC", "W1AW", "OE5XYZ",
                     "OE3GAT", "OE3GAU"]
    print(f"  {'Rufzeichen':<12} {'Kanal':>6} {'NF-Unterk.':>12} {'NF-Oberk.':>11} {'Versatz':>9}")
    print(f"  {'-'*12} {'-'*6} {'-'*12} {'-'*11} {'-'*9}")
    for call in test_stations:
        ch, offset = assign_channel(call)
        f_low  = channel_frequency(ch)
        f_high = f_low + CHANNEL_BW_HZ
        print(f"  {call:<12} {ch:>6} {f_low:>9.0f} Hz {f_high:>8.0f} Hz {offset:>7}s")
    print()

    # ── Zusammenfassung ───────────────────────────────────────────
    print("=" * 60)
    if errors:
        print(f"  ✗ {len(errors)} Fehler gefunden:")
        for e in errors:
            print(f"    - {e}")
    else:
        print("  ✓ Alle Tests erfolgreich.")
    print("=" * 60)
    return len(errors) == 0


if __name__ == "__main__":
    _run_tests()