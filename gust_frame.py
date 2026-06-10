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
import hmac
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

# MFSK-8 SYNC-Preamble: 8 Symbole (v0.5)
# Costas-Array Ordnung 8 (maschinell verifiziert — alle 28 Differenzvektoren eindeutig)
# Ersetzt [7,0,7,0,7,0,7,0]: alle 8 Töne je einmal → Equalizer-Basis + optimale Autokorrelation
# Dauer: 8 × 32 ms = 256 ms
# → Breitband-Decoder kann SYNC ohne Vorabwissen über den Kanal finden
SYNC_SYMBOLS = [2, 0, 6, 7, 1, 4, 3, 5]

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
    EMERG_BEACON   = 0x20   # Notfall-Beacon              (20 Byte)
    EMERG_RSRC     = 0x21   # Notfall-Ressourcenstatus    ( 8 Byte)
    SENSOR         = 0x30   # Generische Sensor-TLV       (variabel)
    TEXT           = 0x40   # Freitext / QSO-Fragment     (variabel)
    CQ             = 0x41   # CQ-Anruf                    ( 5 Byte)
    AUTH           = 0x50   # HMAC-SHA256 Authentifizierung (bilateral, 20 Byte)
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
# HMAC-AUTHENTIFIZIERUNG  (Frame 0x50 — AUTH, GUST-S, Spec §3.5)
# ═══════════════════════════════════════════════════════════════════════
#
# Payload (20 Byte): REF_SEQ(2) | REF_TYPE(1) | KEY_ID(1) | HMAC(16)
# HMAC-SHA256 über (Daten-Frame-Body + REF_SEQ), truncated auf 16 Byte.
# stdlib hmac/hashlib — keine externen Abhängigkeiten.

def auth_tag(frame_body: bytes, ref_seq: int, key: bytes) -> bytes:
    """
    Berechnet den 16-Byte HMAC-SHA256 Tag für einen AUTH-Frame.

    Args:
        frame_body: Vollständiger Frame-Body des referenzierten Daten-Frames
                    (TYPE + CHANNEL + FROM + PAYLOAD, ohne SYNC und ohne RS-Parität)
        ref_seq:    Sequenznummer des Daten-Frames (uint16, big-endian)
        key:        Gemeinsamer HMAC-Schlüssel (32 Byte empfohlen)

    Returns:
        bytes: 16 Byte HMAC-SHA256 (truncated)
    """
    msg = frame_body + struct.pack(">H", ref_seq)
    return hmac.new(key, msg, hashlib.sha256).digest()[:16]


def verify_auth(frame_body: bytes, ref_seq: int,
                tag: bytes, key: bytes) -> bool:
    """
    Prüft einen 16-Byte HMAC-SHA256 Tag.
    Verwendet hmac.compare_digest() gegen Timing-Angriffe.

    Returns:
        True wenn Tag korrekt, False sonst.
    """
    expected = auth_tag(frame_body, ref_seq, key)
    return hmac.compare_digest(expected, tag)


def encode_auth(ref_seq: int, ref_type: int,
                key_id: int, hmac_tag: bytes) -> bytes:
    """
    Kodiert den AUTH-Frame Payload (0x50), 20 Byte.

    Aufbau: REF_SEQ(2) | REF_TYPE(1) | KEY_ID(1) | HMAC(16)

    Args:
        ref_seq:   Sequenznummer des authentifizierten Daten-Frames (0–65535)
        ref_type:  Frame-Typ des Daten-Frames (z.B. 0x01 für WEATHER)
        key_id:    Schlüssel-Identifier (0–255, bilateral vereinbart)
        hmac_tag:  16-Byte HMAC-SHA256 (Ausgabe von auth_tag())

    Returns:
        bytes: 20 Byte Payload
    """
    if len(hmac_tag) != 16:
        raise ValueError(f"hmac_tag muss 16 Byte sein, erhalten: {len(hmac_tag)}")
    return struct.pack(">HBB", ref_seq, ref_type, key_id) + hmac_tag


def decode_auth(payload: bytes) -> dict:
    """
    Dekodiert den AUTH-Frame Payload (0x50).

    Returns:
        dict mit: ref_seq, ref_type, key_id, hmac_tag
    """
    if len(payload) < 20:
        raise ValueError(f"AUTH-Payload zu kurz: {len(payload)} < 20 Byte")
    ref_seq, ref_type, key_id = struct.unpack(">HBB", payload[:4])
    return {
        "ref_seq":  ref_seq,
        "ref_type": ref_type,
        "key_id":   key_id,
        "hmac_tag": payload[4:20],
    }


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


# Prioritätsstufen (Bits 7–6 des Flags-Byte) — ICS-213 / ARRL NTS konform
PRIO_ROUTINE   = 0b00   # Routine
PRIO_WELFARE   = 0b01   # Wohlbefinden / Info
PRIO_URGENT    = 0b10   # Dringend
PRIO_EMERGENCY = 0b11   # Notfall — unmittelbare Lebensgefahr

# Aliases für Rückwärtskompatibilität (deprecated, werden in v0.4 entfernt)
PRIO_LOW    = PRIO_ROUTINE
PRIO_MEDIUM = PRIO_WELFARE
PRIO_HIGH   = PRIO_URGENT
# PRIO_URGENT already defined above — no alias needed

# Verletzungsgrad (Bits 5–4 des Flags-Byte)
INJR_UNKNOWN  = 0b00
INJR_MINOR    = 0b01
INJR_SERIOUS  = 0b10
INJR_CRITICAL = 0b11

# Rückwärtskompatibilität: alte Namen INJURY_* → INJR_* (deprecated, v0.4 entfernt).
# Konservativ ergänzt: externe Module (gust.py, gust_gateway.py, gust_tx_test.py,
# gust_hackrf_diag.py) sowie der Legacy-Smoke-Test importieren noch INJURY_*.
# Diese Aliase halten sie lauffähig, ohne die anderen Dateien zu ändern.
INJURY_UNKNOWN  = INJR_UNKNOWN
INJURY_MINOR    = INJR_MINOR
INJURY_SERIOUS  = INJR_SERIOUS
INJURY_CRITICAL = INJR_CRITICAL

# Status-Bits (Bits 3–0 des Flags-Byte)
FLAG_GPS = 0x08   # Bit 3: GPS-Fix gültig
FLAG_BAT = 0x04   # Bit 2: Batterie OK
FLAG_RLY = 0x02   # Bit 1: Relay-Request

# Ressourcen-Flags (Offset 11) — alle 8 Bit definiert
# Nach ICS Resource Management (FEMA) und OCHA Humanitarian Glossary
RSRC_MEDICAL   = 0x01   # Bit 0: Sanitäter / Arzt
RSRC_FIRE      = 0x02   # Bit 1: Feuerwehr
RSRC_RESCUE    = 0x04   # Bit 2: Technische Rettung
RSRC_WATER     = 0x08   # Bit 3: Wasser / Lebensmittel
RSRC_SHELTER   = 0x10   # Bit 4: Unterkunft
RSRC_COMMS     = 0x20   # Bit 5: Kommunikationsunterstützung
RSRC_TRANSPORT = 0x40   # Bit 6: Transport / Fahrzeuge
RSRC_HAZMAT    = 0x80   # Bit 7: HAZMAT-Team

# Rückwärtskompatibilität: alter Name RSRC_FOOD → RSRC_WATER (gleiche Semantik)
RSRC_FOOD = RSRC_WATER
# Rückwärtskompatibilität: alter Name RSRC_EVAC → RSRC_TRANSPORT
RSRC_EVAC = RSRC_TRANSPORT

# Ereignistyp-Kodes (Offset 9) — abgeleitet aus OASIS CAP v1.2
EVTYPE_FIRE      = 0x01
EVTYPE_FLOOD     = 0x02
EVTYPE_MEDICAL   = 0x03
EVTYPE_SAR       = 0x04   # Search and Rescue
EVTYPE_INFRA     = 0x05   # Infrastrukturschaden
EVTYPE_QUAKE     = 0x06
EVTYPE_MISSING   = 0x07   # Vermisste Person(en)
EVTYPE_UNREST    = 0x08   # Civil Unrest
EVTYPE_ACCIDENT  = 0x09
EVTYPE_COLLAPSE  = 0x0A   # Gebäudeeinsturz
EVTYPE_STORM     = 0x0B
EVTYPE_LANDSLIDE = 0x0C
EVTYPE_POWEROUT  = 0x0D
EVTYPE_COMMSOUT  = 0x0E
EVTYPE_EVAC      = 0x0F   # Evakuierung erforderlich
EVTYPE_CBRNE     = 0x10
EVTYPE_MCI       = 0x11   # Mass Casualty Incident
EVTYPE_WILDFIRE  = 0x13
EVTYPE_OTHER     = 0xFF

# ──────────────────────────────────────────────────────────────────────
# 0x20  NOTFALL-BEACON  (20 Byte)
#
#   0– 3  Latitude        int32   Mikrograd
#   4– 7  Longitude       int32   Mikrograd
#   8     Personenanzahl  uint8   (0x00=unbekannt, 0xFF=MCI)
#   9     Ereignistyp     uint8   (EVTYPE_*, 0x00=ungültig)
#  10     Flags-Byte      uint8   bits7-6=PRIO, bits5-4=INJR,
#                                 bit3=GPS, bit2=BAT, bit1=RLY, bit0=res
#  11     Ressourcen      uint8   Bitmask RSRC_* (angefordert)
#  12–13  Zeitstempel     uint16  Sekunden mod 65536
#  14–19  Freitext        6 Byte  ASCII, NUL-aufgefüllt
#
# struct-Format: '>iiB B B B H 6s'  = 20 Byte
# ──────────────────────────────────────────────────────────────────────
_EMERG_FMT = '>iiB B B B H 6s'   # 20 Byte

_PRIO_NAMES = {0: "ROUTINE", 1: "WELFARE", 2: "URGENT", 3: "EMERGENCY"}
_INJR_NAMES = {0: "UNKNOWN", 1: "MINOR",   2: "SERIOUS", 3: "CRITICAL"}

def encode_emergency_beacon(
    lat_deg:        float,
    lon_deg:        float,
    persons:        int,
    event_type:     int,
    priority:       int   = PRIO_EMERGENCY,
    injury:         int   = INJR_UNKNOWN,
    status_flags:   int   = 0,
    resource_flags: int   = 0,
    timestamp:      int   = 0,
    text_snippet:   str   = "",
    # Legacy parameter kept for call-site compatibility — ignored
    injury_code:    int   = None,
) -> bytes:
    """Frame 0x20 — Notfall-Beacon (20 Byte, Protokoll v0.3+).

    BREAKING CHANGE (v0.3 → 20-Byte-Layout):
      Die POSITIONELLE Signatur hat sich gegenüber dem alten 16-Byte-Frame
      geändert. Alt war (lat, lon, persons, injury_code, resource_flags,
      priority, text_snippet); neu ist (lat, lon, persons, event_type,
      priority, injury, status_flags, resource_flags, timestamp,
      text_snippet). Alte positionelle Aufrufe brechen daher; Aufrufer
      müssen auf Keyword-Argumente umgestellt werden. Das Legacy-Kwarg
      `injury_code=` wird weiterhin akzeptiert (auf `injury` gemappt).

    Flags-Byte (Offset 10):
      bits 7-6 = priority (PRIO_ROUTINE … PRIO_EMERGENCY)
      bits 5-4 = injury   (INJR_UNKNOWN … INJR_CRITICAL)
      bit  3   = GPS-Fix gültig  (FLAG_GPS)
      bit  2   = Batterie OK     (FLAG_BAT)
      bit  1   = Relay-Request   (FLAG_RLY)
      bit  0   = reserviert, immer 0
    """
    # Legacy: if old-style injury_code kwarg was passed, use it
    if injury_code is not None:
        injury = injury_code
    flags_byte = ((priority & 0x03) << 6) \
               | ((injury   & 0x03) << 4) \
               | (status_flags & 0x0F)
    snippet = text_snippet.encode('ascii', errors='replace')[:6]
    snippet = snippet.ljust(6, b'\x00')
    return struct.pack(_EMERG_FMT,
        int(round(lat_deg * 1_000_000)),
        int(round(lon_deg * 1_000_000)),
        int(persons)       & 0xFF,
        int(event_type)    & 0xFF,
        flags_byte,
        int(resource_flags) & 0xFF,
        int(timestamp)      & 0xFFFF,
        snippet,
    )

def decode_emergency_beacon(payload: bytes) -> dict:
    """Frame 0x20 → Dict."""
    lat, lon, per, evt, flg, rsrc, ts, snip = \
        struct.unpack(_EMERG_FMT, payload[:20])
    prio = (flg >> 6) & 0x03
    injr = (flg >> 4) & 0x03
    return {
        "lat_deg":        lat  / 1_000_000,
        "lon_deg":        lon  / 1_000_000,
        "persons":        per,
        "event_type":     evt,
        "priority":       prio,
        "priority_str":   _PRIO_NAMES.get(prio, "?"),
        "injury":         injr,
        "injury_str":     _INJR_NAMES.get(injr, "?"),
        "gps_fix":        bool(flg & FLAG_GPS),
        "battery_ok":     bool(flg & FLAG_BAT),
        "relay_request":  bool(flg & FLAG_RLY),
        "resource_flags": rsrc,
        "needs_medical":  bool(rsrc & RSRC_MEDICAL),
        "needs_fire":     bool(rsrc & RSRC_FIRE),
        "needs_rescue":   bool(rsrc & RSRC_RESCUE),
        "needs_water":    bool(rsrc & RSRC_WATER),
        "needs_shelter":  bool(rsrc & RSRC_SHELTER),
        "needs_comms":    bool(rsrc & RSRC_COMMS),
        "needs_transport":bool(rsrc & RSRC_TRANSPORT),
        "needs_hazmat":   bool(rsrc & RSRC_HAZMAT),
        "timestamp_s":    ts,
        "text_snippet":   snip.rstrip(b'\x00 ').decode('ascii', 'replace'),
        # Legacy keys for call-site compatibility
        "resource_flags": rsrc,
        "priority_str":   _PRIO_NAMES.get(prio, "?"),
    }


# ──────────────────────────────────────────────────────────────────────
# 0x21  NOTFALL-RESSOURCENSTATUS  (8 Byte)
#
# Quittierung + Ressourcenrückmeldung für Frame 0x20.
# Wird directed gesendet (FROM = quittierungssender, Payload Byte4-7 =
# gequittierte Station).
#
#   0    Ereignistyp-Echo  uint8  (spiegelt 0x20 Offset 9)
#   1    Verfügbare Ressourcen  uint8  (gleiche Bitmask wie 0x20 Offset 11)
#   2    ETA in Minuten    uint8  (0x00=sofort, 0xFF=unbekannt)
#   3    Flags             uint8  (bit0=ACK, bit1=RLC, bits7-2=reserviert)
#   4–7  Gequittiertes Rufzeichen  4 Byte Basis-40
#
# struct-Format: '>BBBB4s'  = 8 Byte
# ──────────────────────────────────────────────────────────────────────
_EMERG_RSRC_FMT = '>BBBB4s'   # 8 Byte

# Flags für Frame 0x21
ACK_FLAG = 0x01   # Bit 0: immer 1 in gültigem EMERG_RSRC
RLC_FLAG = 0x02   # Bit 1: Relay-Confirm (Station leitet Meldung weiter)

def encode_emerg_rsrc(
    event_type_echo:  int,
    avail_resources:  int,
    eta_minutes:      int,
    acked_callsign:   str,
    relay_confirm:    bool = False,
) -> bytes:
    """Frame 0x21 — Notfall-Ressourcenstatus / ACK (8 Byte).

    event_type_echo : EVTYPE_* aus dem empfangenen Frame 0x20
    avail_resources : RSRC_* Bitmask — verfügbare (nicht angeforderte!) Ressourcen
    eta_minutes     : 0=sofort, 1-254=Minuten, 255=unbekannt
    acked_callsign  : Rufzeichen der Station, deren 0x20 quittiert wird
    relay_confirm   : True wenn diese Station die Meldung aktiv weiterleitet
    """
    flags = ACK_FLAG
    if relay_confirm:
        flags |= RLC_FLAG
    return struct.pack(_EMERG_RSRC_FMT,
        int(event_type_echo) & 0xFF,
        int(avail_resources) & 0xFF,
        int(eta_minutes)     & 0xFF,
        flags,
        encode_callsign(acked_callsign),
    )

def decode_emerg_rsrc(payload: bytes) -> dict:
    """Frame 0x21 → Dict."""
    evt, rsrc, eta, flags, call_raw = \
        struct.unpack(_EMERG_RSRC_FMT, payload[:8])
    return {
        "event_type_echo":  evt,
        "avail_resources":  rsrc,
        "avail_medical":    bool(rsrc & RSRC_MEDICAL),
        "avail_fire":       bool(rsrc & RSRC_FIRE),
        "avail_rescue":     bool(rsrc & RSRC_RESCUE),
        "avail_water":      bool(rsrc & RSRC_WATER),
        "avail_shelter":    bool(rsrc & RSRC_SHELTER),
        "avail_comms":      bool(rsrc & RSRC_COMMS),
        "avail_transport":  bool(rsrc & RSRC_TRANSPORT),
        "avail_hazmat":     bool(rsrc & RSRC_HAZMAT),
        "eta_minutes":      eta,
        "ack_valid":        bool(flags & ACK_FLAG),
        "relay_confirm":    bool(flags & RLC_FLAG),
        "acked_callsign":   decode_callsign(call_raw),
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
        FrameType.EMERG_RSRC:   decode_emerg_rsrc,
        FrameType.TEXT:         decode_text_fragment,
        FrameType.CQ:           decode_cq,
        FrameType.AUTH:         decode_auth,
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

def _rs_encode_raw(data: bytes) -> bytes:
    """
    Rohe Reed-Solomon(255,223)-Kodierung — immer RS, KEIN Backend-Dispatch.
    Gibt data + 32 Byte RS-Parität zurück.

    Wird vom RS-FEC-Backend (gust_fec.ReedSolomonFEC) intern verwendet.
    Getrennt von rs_encode(), weil rs_encode() an das aktive Backend
    weiterleitet — würde das RS-Backend rs_encode() aufrufen, entstünde
    eine Endlosrekursion.
    """
    if not _RS_AVAILABLE:
        raise RuntimeError("reedsolo nicht installiert")
    return bytes(_rs_codec.encode(data))

def _rs_decode_raw(data: bytes) -> bytes:
    """
    Rohe Reed-Solomon-Dekodierung — immer RS, KEIN Backend-Dispatch.
    Korrigiert bis zu 16 Byte-Fehler, gibt die Nutzdaten zurück.
    Wirft reedsolo.ReedSolomonError bei nicht korrigierbarem Fehler.
    """
    if not _RS_AVAILABLE:
        raise RuntimeError("reedsolo nicht installiert")
    decoded, _, _ = _rs_codec.decode(data)
    return bytes(decoded)


# ── FEC-Backend (ADR-24) ──────────────────────────────────────────────
# Standard: RS (produktiv). LDPC: opt-in per set_fec_backend().
# Wird von gust.py beim Start gesetzt wenn gateway.json "fec": "ldpc" enthält.
#
# rs_encode()/rs_decode() leiten an das aktive Backend weiter (Default RS),
# damit die Schnittstelle für gust_modulator.py unverändert bleibt. Das
# RS-Backend (gust_fec.ReedSolomonFEC) ruft INTERN _rs_encode_raw/_rs_decode_raw
# auf — niemals rs_encode/rs_decode — sonst entstünde eine Endlosrekursion.
_fec_backend = None   # None = lazy init beim ersten Aufruf → RS

def set_fec_backend(name: str) -> None:
    """
    FEC-Backend global setzen.
    Wird einmalig beim Daemon-Start aufgerufen (aus gust.py).

    Args:
        name: "rs" (Standard) oder "ldpc" (experimentell)
    """
    global _fec_backend
    from gust_fec import get_fec_backend
    _fec_backend = get_fec_backend(name)
    import logging
    logging.getLogger("gust.frame").info(
        "[FEC] Backend gewechselt: %s  (overhead=%d B)",
        name, _fec_backend.overhead
    )

def _get_fec():
    """Lazy-Init: FEC-Backend holen, RS als Default."""
    global _fec_backend
    if _fec_backend is None:
        from gust_fec import get_fec_backend
        _fec_backend = get_fec_backend("rs")
    return _fec_backend

def get_fec_overhead() -> int:
    """
    Aktuellen FEC-Overhead in Bytes zurückgeben.
    Für RS: RS_OVERHEAD (32). Für LDPC: abhängig von der Blockgröße
    (variabel/shortened, erst nach dem ersten encode() bekannt → bis dahin
    Fallback auf RS_OVERHEAD).
    """
    fec = _get_fec()
    return fec.overhead if fec.overhead > 0 else RS_OVERHEAD

def rs_encode(data: bytes) -> bytes:
    """
    FEC-Kodierung (Backend per set_fec_backend() wählbar, Standard: RS).
    Gibt data + Paritätsbytes zurück.
    """
    return _get_fec().encode(data)

def rs_decode(data: bytes) -> bytes:
    """
    FEC-Dekodierung (Backend per set_fec_backend() wählbar, Standard: RS).
    Gibt die originalen Nutzdaten zurück.
    Wirft backend-spezifische Exception bei nicht korrigierbaren Fehlern.
    """
    return _get_fec().decode(data)


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
#   NF 600 Hz   → RF 14.110,600 MHz  (Kanal 0)
#   NF 850 Hz   → RF 14.110,850 MHz  (Kanal 1)
#   ...
#   NF 2.350 Hz → RF 14.112,350 MHz  (Kanal 7)
#   NF 2.600 Hz → RF 14.112,600 MHz  (obere Grenze Kanal 7)
#
# Gesamtbandbreite: 8 × 250 Hz = 2.000 Hz  →  600–2600 Hz (SSB-Plateau ±0,5 dB)
# Randkanäle 0+9 alt (400 Hz / 2650–2900 Hz) entfernt — lagen im SSB-Filterrolloff.
#
# Kanalplan v0.5:
#   Kanal  NF-Unterkante  NF-Oberkante   8 Töne bei
#      0      600 Hz         850 Hz      600,631,...,819 Hz
#      1      850 Hz        1100 Hz      850,881,...,1069 Hz
#      2     1100 Hz        1350 Hz      ...
#      ...
#      7     2350 Hz        2600 Hz      2350,2381,...,2569 Hz
#
# Prinzip Kanalzuweisung: Rufzeichen → SHA-256 → Kanal + Zeitversatz
# Kein Koordinationsaufwand, Pure ALOHA (Kollisionsanalyse → Spec §4.2)

N_CHANNELS       = 8        # Reduziert von 10 — Kanäle 0+9 alt lagen im SSB-Rolloff
FRAME_FLAG_TEST  = 0x80     # Bit 7 im CHANNEL-Byte: Frame ist ein Testframe
CHANNEL_BW_HZ    = 250      # Bandbreite je Kanal (= MFSK-8 Tonabstand × 8)
CHANNEL_BASE_HZ  = 600.0    # War 400.0 — neuer Span 600–2600 Hz, SSB-Plateau ±0,5 dB

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

    Kanalplan (Standard, 8 Kanäle, v0.5):
      Kanal 0:  600 Hz   Kanal 4: 1600 Hz
      Kanal 1:  850 Hz   Kanal 5: 1850 Hz
      Kanal 2: 1100 Hz   Kanal 6: 2100 Hz
      Kanal 3: 1350 Hz   Kanal 7: 2350 Hz  (Oberkante: 2600 Hz)

    Gesamtspan: 600–2600 Hz → SSB-Plateau ±0,5 dB (innerhalb 300–3000 Hz Passband).
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

    # ── Test 5: Notfall-Beacon 0x20 v2 (20 Byte) ───────────────────────
    print("\n── Test 5: Notfall-Beacon 0x20 v2 (20 Byte) ──")
    emerg_payload = encode_emergency_beacon(
        lat_deg=47.810, lon_deg=13.055,
        persons=5,
        event_type=EVTYPE_FLOOD,
        priority=PRIO_EMERGENCY,
        injury=INJR_SERIOUS,
        status_flags=FLAG_GPS | FLAG_BAT | FLAG_RLY,
        resource_flags=RSRC_MEDICAL | RSRC_TRANSPORT,
        timestamp=3600,
        text_snippet="FLOOD",
    )
    assert len(emerg_payload) == 20, \
        f"0x20 Payload falsche Länge: {len(emerg_payload)} (erwartet 20)"
    print(f"  Payload ({len(emerg_payload)} Byte):")
    print(hexdump(emerg_payload))
    eb = decode_emergency_beacon(emerg_payload)
    assert eb["priority_str"]  == "EMERGENCY",  f"Prio: {eb['priority_str']}"
    assert eb["injury_str"]    == "SERIOUS",    f"Injr: {eb['injury_str']}"
    assert eb["event_type"]    == EVTYPE_FLOOD, f"EvType: {eb['event_type']}"
    assert eb["gps_fix"]       == True,         "GPS-Flag"
    assert eb["relay_request"] == True,         "RLY-Flag"
    assert eb["needs_medical"] == True,         "RSRC_MEDICAL"
    assert eb["needs_transport"]== True,        "RSRC_TRANSPORT"
    assert eb["timestamp_s"]   == 3600,         f"TS: {eb['timestamp_s']}"
    assert eb["text_snippet"]  == "FLOOD",      f"Snippet: {eb['text_snippet']}"
    print(f"  Prio={eb['priority_str']}  Injr={eb['injury_str']}  "
          f"EvType=0x{eb['event_type']:02X}  GPS={eb['gps_fix']}  "
          f"Snippet='{eb['text_snippet']}'")
    print("  ✓")

    # ── Test 11: Notfall-Ressourcenstatus 0x21 (8 Byte) ────────────────
    print("\n── Test 11: EMERG_RSRC 0x21 (8 Byte) ──")
    rsrc_payload = encode_emerg_rsrc(
        event_type_echo=EVTYPE_FLOOD,
        avail_resources=RSRC_MEDICAL | RSRC_TRANSPORT,
        eta_minutes=20,
        acked_callsign="OE3GAS",
        relay_confirm=False,
    )
    assert len(rsrc_payload) == 8, \
        f"0x21 Payload falsche Länge: {len(rsrc_payload)} (erwartet 8)"
    print(f"  Payload ({len(rsrc_payload)} Byte):")
    print(hexdump(rsrc_payload))
    rd = decode_emerg_rsrc(rsrc_payload)
    assert rd["event_type_echo"] == EVTYPE_FLOOD,  f"Echo: {rd['event_type_echo']}"
    assert rd["eta_minutes"]     == 20,            f"ETA: {rd['eta_minutes']}"
    assert rd["ack_valid"]       == True,          "ACK-Flag"
    assert rd["relay_confirm"]   == False,         "RLC-Flag"
    assert rd["acked_callsign"]  == "OE3GAS",      f"Call: {rd['acked_callsign']}"
    assert rd["avail_medical"]   == True,          "avail_medical"
    assert rd["avail_transport"] == True,          "avail_transport"
    assert rd["avail_fire"]      == False,         "avail_fire should be False"
    print(f"  ACK={rd['ack_valid']}  ETA={rd['eta_minutes']}min  "
          f"Caller={rd['acked_callsign']}  "
          f"Avail=0x{rd['avail_resources']:02X}")
    print("  ✓")

    # ── Test 12: 0x20/0x21 Ressourcenabgleich ──────────────────────────
    print("\n── Test 12: 0x20/0x21 Ressourcenabgleich ──")
    requested = decode_emergency_beacon(emerg_payload)["resource_flags"]
    available = decode_emerg_rsrc(rsrc_payload)["avail_resources"]
    covered   = requested & available
    missing   = requested & (~available & 0xFF)
    assert covered == (RSRC_MEDICAL | RSRC_TRANSPORT), \
        f"Covered falsch: 0b{covered:08b}"
    assert missing == 0, f"Missing sollte 0 sein, ist: 0b{missing:08b}"
    print(f"  Requested: 0b{requested:08b}")
    print(f"  Available: 0b{available:08b}")
    print(f"  Covered:   0b{covered:08b}  Missing: 0b{missing:08b}")
    print("  ✓  Alle angeforderten Ressourcen gedeckt")

    # ── Test 13: 0x21 via Frame-Build + dispatch ────────────────────────
    print("\n── Test 13: 0x21 Frame-Build → Parse → decode_payload ──")
    ch, _ = assign_channel("OE1XTU")
    frame_21 = build_frame(FrameType.EMERG_RSRC, "OE1XTU", rsrc_payload, ch)
    parsed_21 = parse_frame(frame_21)
    assert parsed_21 is not None,          "parse_frame returned None"
    assert parsed_21["crc_ok"],            "CRC-Fehler Frame 0x21"
    assert parsed_21["type"] == 0x21,      f"Typ: {parsed_21['type']}"
    assert parsed_21["from"] == "OE1XTU",  f"FROM: {parsed_21['from']}"
    decoded_21 = decode_payload(FrameType.EMERG_RSRC, parsed_21["payload"])
    assert decoded_21 is not None,         "decode_payload lieferte None für 0x21"
    assert decoded_21["ack_valid"],        "ACK-Flag nach Frame-Roundtrip"
    print(f"  FROM={parsed_21['from']}  TYPE=0x{parsed_21['type']:02X}  "
          f"CRC={'✓' if parsed_21['crc_ok'] else '✗'}")
    print(f"  Decoded: ACK={decoded_21['ack_valid']}  "
          f"ETA={decoded_21['eta_minutes']}min  "
          f"Acked={decoded_21['acked_callsign']}")
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