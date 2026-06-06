#!/usr/bin/env python3
"""
GUST — Simulations-Adapter                                 Phase 5
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Datum   : Mai 2026

Liefert simulierte Telemetrie-Frames (WEATHER, POSITION, TEXT, EMERG)
für den Daemon-Betrieb ohne echte Sensorhardware.

Schnittstelle (für gust.py cmd_daemon):
    adapter = SimAdapter(sim_cfg, callsign="OE3GAS")
    for frame in adapter.read_all_due():   # fällige Frames abfragen
        await bus.publish(make_rx_frame_event(frame))
    wait = adapter.next_due_in() or 1.0   # Zeit bis zum nächsten Frame
"""

import random
import time
from typing import Optional

from gust_frame import assign_channel, EVTYPE_OTHER

# ── Frame-Typ-Namen ─────────────────────────────────────────────────────────
_TYPE_NAMES = {
    0x01: "WEATHER",
    0x02: "POSITION",
    0x03: "STATION_TLM",
    0x20: "EMERG_BEACON",
    0x30: "SENSOR",
    0x40: "TEXT",
    0x41: "CQ",
}

# ── Beispiel-Rufzeichen für simulierte Gegenstationen ───────────────────────
_SIM_CALLS = [
    "OE1XTU", "OE2HRX", "OE3GAS", "OE3QOF", "OE4CMF",
    "OE5KCB", "OE6KPF", "OE7DOE", "OE8RSJ", "OE9MQR",
]

# ── Demo-Pool für Multi-Station-Betrieb (sim_cfg["multi_station"]) ──────────
DEMO_CALLSIGNS = [
    "OE3GAS",   # Heimatstation (immer dabei)
    "OE1XTU", "OE3GAT", "OE5RFP", "OE4XLC",
    "OE7DBH", "OE2XGR", "OE9XPI", "OE1KFR",
]

# Stationen ohne Positions-Frames (reine Telemetrie-Stationen)
_NO_POSITION_CALLS = {"OE1KFR"}


def _make_weather(callsign: str, channel: int) -> dict:
    return {
        "frame_type": 0x01,
        "type_name":  "WEATHER",
        "from":       callsign,
        "channel":    channel,
        "snr_db":     round(random.uniform(8.0, 22.0), 1),
        "freq_offset_hz": round(random.uniform(-20.0, 20.0), 1),
        "data": {
            "temp_c":       round(random.uniform(-5.0, 35.0), 1),
            "humidity_pct": random.randint(30, 98),
            "pressure_hpa": round(random.uniform(980.0, 1030.0), 1),
            "wind_kmh":     random.randint(0, 80),
            "wind_deg":     random.randint(0, 359),
            "rain_mm_h":    round(random.uniform(0.0, 15.0), 1),
            "uv_index":     random.randint(0, 11),
            "bat_ok":       True,
            "sensor_ok":    True,
        },
    }


def _make_position(callsign: str, channel: int,
                   lat: float, lon: float, alt_m: int,
                   drift: bool) -> dict:
    if drift:
        lat += random.uniform(-0.002, 0.002)
        lon += random.uniform(-0.002, 0.002)
    return {
        "frame_type": 0x02,
        "type_name":  "POSITION",
        "from":       callsign,
        "channel":    channel,
        "snr_db":     round(random.uniform(8.0, 22.0), 1),
        "freq_offset_hz": round(random.uniform(-20.0, 20.0), 1),
        "data": {
            "lat_deg":    round(lat, 5),
            "lon_deg":    round(lon, 5),
            "alt_m":      alt_m + random.randint(-5, 5),
            "speed_kmh":  random.randint(0, 5),
            "heading_deg": random.randint(0, 359),
            "timestamp":  0,
            "mobile":     False,
            "gps_fix":    True,
            "emergency":  False,
        },
    }


def _make_text(callsign: str, channel: int) -> dict:
    msgs = [
        "73 de " + callsign,
        "GUST Test",
        "CQ CQ CQ",
        "QTH Wien JN88HF",
        "Test 73",
    ]
    return {
        "frame_type": 0x40,
        "type_name":  "TEXT",
        "from":       callsign,
        "channel":    channel,
        "snr_db":     round(random.uniform(8.0, 22.0), 1),
        "freq_offset_hz": round(random.uniform(-20.0, 20.0), 1),
        "data": {
            "dest":       random.choice(_SIM_CALLS),
            "seq_nr":     0,
            "frag_index": 0,
            "frag_total": 1,
            "last_frag":  True,
            "text":       random.choice(msgs),
        },
    }


def _make_station_tlm(callsign: str, channel: int) -> dict:
    # Feldnamen identisch zu gust_frame.decode_station_tlm() (Frame 0x03)
    return {
        "frame_type": 0x03,
        "type_name":  "STATION_TLM",
        "from":       callsign,
        "channel":    channel,
        "snr_db":     round(random.uniform(8.0, 22.0), 1),
        "freq_offset_hz": round(random.uniform(-20.0, 20.0), 1),
        "data": {
            "voltage_v":  round(random.uniform(11.8, 14.2), 2),
            "current_ma": random.randint(150, 900),
            "temp_c":     round(random.uniform(15.0, 55.0), 1),
            "cpu_pct":    random.randint(3, 60),
            "uptime_min": random.randint(10, 9999),
            "flags":      0,
        },
    }


def _make_emergency(callsign: str, channel: int,
                    lat: float, lon: float) -> dict:
    return {
        "frame_type": 0x20,
        "type_name":  "EMERG_BEACON",
        "from":       callsign,
        "channel":    channel,
        "snr_db":     round(random.uniform(10.0, 20.0), 1),
        "freq_offset_hz": round(random.uniform(-20.0, 20.0), 1),
        "data": {
            "lat_deg":          round(lat + random.uniform(-0.01, 0.01), 5),
            "lon_deg":          round(lon + random.uniform(-0.01, 0.01), 5),
            "persons":          random.randint(1, 5),
            "event_type":       random.randint(1, 5),
            "priority":         3,
            "priority_str":     "EMERGENCY",
            "injury":           random.randint(0, 3),
            "injury_str":       random.choice(["UNKNOWN","MINOR","SERIOUS","CRITICAL"]),
            "gps_fix":          True,
            "battery_ok":       True,
            "relay_request":    False,
            "resource_flags":   0,
            "needs_medical":    random.choice([True, False]),
            "needs_fire":       False,
            "needs_rescue":     False,
            "needs_water":      False,
            "needs_shelter":    False,
            "needs_comms":      False,
            "needs_transport":  False,
            "needs_hazmat":     False,
            "timestamp_s":      0,
            "text_snippet":     random.choice(["SOS", "HELP", "MEDI", "FIRE"]),
        },
    }


class SimAdapter:
    """
    Simulations-Adapter: erzeugt periodisch Telemetrie-Frames.

    Konfigurationsschlüssel (sim_cfg):
        weather_interval_s   (Standard: 300)
        position_interval_s  (Standard: 300)
        text_interval_s      (Standard: 120)
        emergency_enabled    (Standard: False)
        lat, lon, alt_m      (Standard: Wien)
        drift                (Standard: False)
        multi_station        (Standard: False) — mehrere fiktive Stationen
                             auf ihren deterministischen Kanälen, zufällige
                             Intervalle 20–60 s, gemischte Frame-Typen
        callsigns            Liste der Stationen für multi_station
                             (Standard: DEMO_CALLSIGNS); aktiviert
                             multi_station implizit
    """

    def __init__(self, sim_cfg: dict, callsign: str = "OE3GAS") -> None:
        self._callsign = callsign
        _ch_info       = assign_channel(callsign)
        # assign_channel gibt (channel, offset_s) oder nur int zurück
        self._channel  = _ch_info[0] if isinstance(_ch_info, (tuple, list)) else int(_ch_info)
        self._lat      = sim_cfg.get("lat",   48.2082)
        self._lon      = sim_cfg.get("lon",   16.3738)
        self._alt_m    = int(sim_cfg.get("alt_m", 180))
        self._drift    = bool(sim_cfg.get("drift", False))
        self._emerg    = bool(sim_cfg.get("emergency_enabled", False))

        frames_cfg = sim_cfg.get("frames", ["weather", "position", "text"])
        self._weather_iv  = sim_cfg.get("weather_interval_s",  300.0) if "weather"  in frames_cfg else None
        self._position_iv = sim_cfg.get("position_interval_s", 300.0) if "position" in frames_cfg else None
        self._text_iv     = sim_cfg.get("text_interval_s",     120.0) if "text"     in frames_cfg else None
        self._emerg_iv    = sim_cfg.get("emergency_interval_s", 600.0) if self._emerg else None

        now = time.monotonic()

        # ── Multi-Station-Demo (belebtes Netz: alle Kanäle aktiv) ─────
        cs_list = sim_cfg.get("callsigns")
        self._multi = bool(sim_cfg.get("multi_station")) \
            or isinstance(cs_list, list)
        self._stations: list = []
        if self._multi:
            calls = (list(cs_list) if isinstance(cs_list, list) and cs_list
                     else list(DEMO_CALLSIGNS))
            if callsign not in calls:
                calls.insert(0, callsign)   # Heimatstation immer dabei
            # Genau eine Station darf Emergency senden (wenn aktiviert)
            emerg_station = calls[-1] if self._emerg else None
            for c in calls:
                ci = assign_channel(c)
                ch = ci[0] if isinstance(ci, (tuple, list)) else int(ci)
                self._stations.append({
                    "call":     c,
                    "channel":  ch,
                    "next_due": now + random.uniform(2.0, 25.0),
                    "emerg":    (c == emerg_station),
                })

        # Beim Start sofort den ersten Frame fällig machen
        # (Einzelstation-Modus — bei multi_station übernimmt _stations)
        self._next: dict[str, float] = {}
        if not self._multi:
            if self._weather_iv:
                self._next["weather"]  = now + 5.0
            if self._position_iv:
                self._next["position"] = now + 10.0
            if self._text_iv:
                self._next["text"]     = now + 20.0
            if self._emerg_iv:
                self._next["emerg"]    = now + 30.0

    def _make_station_frame(self, st: dict) -> dict:
        """Zufälligen Frame-Typ für eine Multi-Station-Demo-Station wählen."""
        c, ch = st["call"], st["channel"]
        choices = [("weather", 40)]
        if c not in _NO_POSITION_CALLS:
            choices.append(("position", 30))
        choices.append(("text", 15))
        choices.append(("tlm", 10))
        if st["emerg"]:
            choices.append(("emerg", 5))
        kinds, weights = zip(*choices)
        kind = random.choices(kinds, weights=weights, k=1)[0]
        if kind == "weather":
            return _make_weather(c, ch)
        if kind == "position":
            return _make_position(c, ch, self._lat, self._lon,
                                  self._alt_m, True)
        if kind == "text":
            return _make_text(c, ch)
        if kind == "tlm":
            return _make_station_tlm(c, ch)
        return _make_emergency(c, ch, self._lat, self._lon)

    def read_all_due(self) -> list:
        """Gibt alle jetzt fälligen Frames zurück und plant den nächsten Zeitpunkt."""
        now    = time.monotonic()
        frames = []

        # ── Multi-Station-Modus ───────────────────────────────────────
        if self._multi:
            for st in self._stations:
                if now < st["next_due"]:
                    continue
                frames.append(self._make_station_frame(st))
                st["next_due"] = now + random.uniform(20.0, 60.0)
            return frames

        if "weather" in self._next and now >= self._next["weather"]:
            frames.append(_make_weather(self._callsign, self._channel))
            self._next["weather"] = now + (self._weather_iv or 300.0)

        if "position" in self._next and now >= self._next["position"]:
            frames.append(_make_position(
                self._callsign, self._channel,
                self._lat, self._lon, self._alt_m, self._drift
            ))
            self._next["position"] = now + (self._position_iv or 300.0)

        if "text" in self._next and now >= self._next["text"]:
            frames.append(_make_text(self._callsign, self._channel))
            self._next["text"] = now + (self._text_iv or 120.0)

        if "emerg" in self._next and now >= self._next["emerg"]:
            frames.append(_make_emergency(
                self._callsign, self._channel, self._lat, self._lon
            ))
            self._next["emerg"] = now + (self._emerg_iv or 600.0)

        return frames

    def next_due_in(self) -> Optional[float]:
        """Sekunden bis zum nächsten fälligen Frame (None wenn nichts geplant)."""
        pending = list(self._next.values()) \
                + [st["next_due"] for st in self._stations]
        if not pending:
            return None
        now = time.monotonic()
        soonest = min(pending) - now
        return max(0.0, soonest)


def create_adapter(cfg: dict, callsign: str = "OE3GAS") -> SimAdapter:
    """
    Erzeugt den passenden Adapter laut gateway.json.
    Aktuell nur SimAdapter implementiert; andere Adapter (BME280, Meshtastic)
    werden in späteren Phasen ergänzt.
    """
    sim_cfg = cfg.get("source", {}).get("sim", {})
    return SimAdapter(sim_cfg, callsign=callsign)