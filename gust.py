#!/usr/bin/env python3
"""
GUST — CLI-Einstiegspunkt                                  Phase 5
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Version : 0.1.0
Datum   : Mai 2026

Verwendung:
  py gust.py daemon                    Vollbetrieb (TX + RX + Web)
  py gust.py daemon --sim              Daemon mit Simulator (kein HW)
  py gust.py daemon --dry-run          Kein TX, nur Web + Sim
  py gust.py rx                        Monitor-Modus (nur RX + Web)
  py gust.py tx weather                Wetter-Frame einmalig senden
  py gust.py tx position               Position einmalig senden
  py gust.py tx text "Test 73" OE1XTU  Freitext einmalig senden
  py gust.py tx emergency              Notfall-Frame (sofort, Prio 1)
  py gust.py info OE3GAS               Kanalinfo für Rufzeichen
  py gust.py info                      Kanalinfo für eigenes Rufzeichen
  py gust.py devices                   Verfügbare Audiogeräte anzeigen

Globale Optionen (vor dem Subcommand):
  --callsign OE3GAS       Eigenes Rufzeichen (überschreibt gateway.json)
  --config gateway.json   Konfigurationsdatei
  --dry-run               Kein TX, Audio-Ausgabe deaktiviert
  --sim                   Simulator als Datenquelle (kein Hardware-Adapter)
  -v / --verbose          Debug-Logging

Daemon-Modus startet:
  • EventBus (asyncio Fan-out)
  • SimAdapter oder Hardware-Adapter als TX-Quelle
  • WebServer (aiohttp, Port 8080)
  • Status-Publisher (alle 60 s an EventBus)
  • Python-Logging → EventBus → /ws/log im Browser

Konfigurationsdatei gateway.json:
  Falls nicht vorhanden → Standardwerte (kein Fehler).
  Rufzeichen via --callsign überschreibt gateway.json.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Optional

# ── Eigene Module ─────────────────────────────────────────────────────
from gust_eventbus import (
    EventBus, EventBusLogHandler,
    make_rx_frame_event, make_status_event,
)
from gust_web import WebServer

# SimAdapter: unterstützt alten (gust_weather.py) und neuen Dateinamen
try:
    from gust_msg_simulator import SimAdapter, create_adapter
except ImportError:
    from gust_weather import SimAdapter, create_adapter   # type: ignore

# ── ANSI-Farben ────────────────────────────────────────────────────────
_ANSI_RESET  = "\033[0m"
_ANSI_GREY   = "\033[38;5;242m"
_ANSI_GREEN  = "\033[32m"
_ANSI_YELLOW = "\033[33m"
_ANSI_BLUE   = "\033[34m"
_ANSI_CYAN   = "\033[36m"
_ANSI_RED    = "\033[31m"
_ANSI_ORANGE = "\033[38;5;208m"
_ANSI_BOLD   = "\033[1m"

# ── Überschreibende Statuszeile ────────────────────────────────────────
class _StatusLine:
    """
    Verwaltet eine einzelne überschreibbare Zeile am Ende der Terminal-Ausgabe.
    Wird für periodische RX-Scan-Meldungen verwendet (\r ohne \n).
    """
    _active: str = ""
    _enabled: bool = True

    @classmethod
    def write(cls, text: str) -> None:
        """Statuszeile überschreibend ausgeben (kein Newline)."""
        if not cls._enabled or not sys.stderr.isatty():
            return
        padded = ("  " + text).ljust(78)
        sys.stderr.write("\r" + padded)
        sys.stderr.flush()
        cls._active = text

    @classmethod
    def clear(cls) -> None:
        """Statuszeile löschen bevor eine normale Log-Zeile ausgegeben wird."""
        if not cls._enabled or not sys.stderr.isatty():
            return
        if cls._active:
            sys.stderr.write("\r" + " " * 80 + "\r")
            sys.stderr.flush()
            cls._active = ""


# ── Farbiger Formatter ─────────────────────────────────────────────────
class GustFormatter(logging.Formatter):
    """
    Farbiger CLI-Formatter für GUST.
    - INFO-Meldungen: grün
    - WARNING: orange, ERROR/CRITICAL: rot
    - TX-Gateway-Meldungen: gelb mit TX ▶ Label
    - RX-Frame-Meldungen:   blau  mit RX ◀ Label
    - aiohttp.access: vollständig unterdrückt (WARNING gesetzt in setup_logging)
    - Periodische RX-Scan-Heartbeats: überschreibende Statuszeile
    """

    _LEVEL_COLORS = {
        logging.DEBUG:    _ANSI_GREY,
        logging.INFO:     _ANSI_GREEN,
        logging.WARNING:  _ANSI_ORANGE,
        logging.ERROR:    _ANSI_RED,
        logging.CRITICAL: _ANSI_RED + _ANSI_BOLD,
    }

    def format(self, record: logging.LogRecord) -> str:
        ts    = self.formatTime(record, "%H:%M:%S")
        msg   = record.getMessage()
        name  = record.name

        # TX-Gateway-Ereignisse → gelb, kompaktes Label
        if name == "gust.gateway" and "gesendet auf Kanal" in msg:
            _StatusLine.clear()
            label = f"{_ANSI_YELLOW}TX ▶   {_ANSI_RESET}"
            # Komprimieren: 'TX-Gateway: WEATHER gesendet auf Kanal 2  (4.9s, Prio 4)'
            compact = msg.replace("TX-Gateway: ", "").replace(" gesendet auf Kanal ", "  Kanal ")
            return f"{_ANSI_GREY}{ts}{_ANSI_RESET}  {label}{_ANSI_YELLOW}{compact}{_ANSI_RESET}"

        # RX-Frame-Ereignisse → blau, kompaktes Label
        if "[RX] ✓ Frame" in msg:
            _StatusLine.clear()
            label = f"{_ANSI_BLUE}RX ◀   {_ANSI_RESET}"
            # '[RX] ✓ Frame #3  von OE3GAT    [WEATHER   ]  Kanal 0  ...' → kürzen
            compact = msg.replace("[RX] ✓ ", "")
            return f"{_ANSI_GREY}{ts}{_ANSI_RESET}  {label}{_ANSI_BLUE}{compact}{_ANSI_RESET}"

        # Periodischer RX-Heartbeat → Statuszeile (überschreibend, kein Newline)
        if "[RX]" in msg and "Scans ohne Frame" in msg:
            _StatusLine.write(f"{_ANSI_GREY}{ts}  ▸ {msg}{_ANSI_RESET}")
            return ""   # leerer String → kein normaler Log-Output

        # DRY-RUN TX → cyan
        if "DRY-RUN" in msg:
            _StatusLine.clear()
            return (f"{_ANSI_GREY}{ts}{_ANSI_RESET}  "
                    f"{_ANSI_CYAN}DRY    {_ANSI_RESET}{_ANSI_CYAN}{msg}{_ANSI_RESET}")

        # Standard: INFO grün, WARNING orange, ERROR rot
        _StatusLine.clear()
        color = self._LEVEL_COLORS.get(record.levelno, "")
        level = f"{color}{record.levelname:<7}{_ANSI_RESET}"
        # Logger-Name: nur letzten Teil ('gust.web' → 'web', 'gust' → 'gust')
        short_name = name.split(".")[-1] if "." in name else name
        return (f"{_ANSI_GREY}{ts}{_ANSI_RESET}  {level} "
                f"{_ANSI_GREY}{short_name:<12}{_ANSI_RESET} {msg}")


# ── Gefilterte Handler-Klasse ──────────────────────────────────────────
class _SkipEmptyFilter(logging.Filter):
    """Unterdrückt Log-Records mit leerem source-Message-String (belt-and-suspenders)."""
    def filter(self, record: logging.LogRecord) -> bool:
        return bool(record.getMessage())


class _GustStreamHandler(logging.StreamHandler):
    """
    StreamHandler der nichts ausgibt wenn der Formatter einen leeren String
    zurückgibt. Notwendig für den RX-Heartbeat: GustFormatter.format() gibt
    dort "" zurück (Seiteneffekt: _StatusLine.write() malt die \r-Zeile),
    aber StreamHandler.emit() würde sonst "" + "\n" schreiben — eine
    Leerzeile nach dem \r, die die überschreibende Statuszeile zerstört.
    """
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if not msg:
                return
            stream = self.stream
            stream.write(msg + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)


log = logging.getLogger("gust")

VERSION = "0.1.0"

# ─── Standard-Konfiguration (wird mit gateway.json zusammengeführt) ───
_DEFAULT_CONFIG = {
    "callsign": "OE3GAS",
    "web": {
        "host":    "0.0.0.0",
        "port":    8080,
        "api_key": "",
    },
    "gateway": {
        "interval_s":    300,
        "min_tx_gap_s":   10,
    },
    "source": {
        "adapter": "sim",
        "sim": {
            "frames":               ["weather", "position", "text"],
            "weather_interval_s":   300,
            "position_interval_s":  300,
            "text_interval_s":      120,
            "emergency_enabled":    False,
            "lat":   48.2082,
            "lon":   16.3738,
            "alt_m": 180,
            "drift": False,
        },
    },
    "audio": {
        "device":       None,
        "ptt_backend":  "null",
        "ptt_delay_ms": 250,
    },
    "rx": {
        "enabled":          True,    # False → RX-Loop nicht starten
        "device":           None,    # None = selbes Gerät wie TX (oder Standard)
        "scan_interval_s":  2.0,     # Sekunden zwischen Scan-Versuchen
        "window_s":         9.0,     # Audiohistorie pro Versuch (Vollfenster-Garantie)
        "dedup_ttl_s":      30,      # Sekunden bis Frame wieder dekodiert wird
    },
}


# ═══════════════════════════════════════════════════════════════════════
# KONFIGURATION
# ═══════════════════════════════════════════════════════════════════════

def load_config(path: Optional[str], callsign: Optional[str]) -> dict:
    """
    Konfiguration laden und mit Standardwerten zusammenführen.
    Fehlende gateway.json → Standardwerte ohne Fehlermeldung.
    --callsign überschreibt immer den Wert aus der Datei.
    """
    cfg = _deep_merge({}, _DEFAULT_CONFIG)

    if path:
        cfg_path = Path(path)
        if cfg_path.exists():
            try:
                with open(cfg_path, encoding="utf-8") as f:
                    file_cfg = json.load(f)
                cfg = _deep_merge(cfg, file_cfg)
                log.info("Konfiguration geladen: %s", cfg_path)
            except Exception as e:
                log.warning("gateway.json Fehler: %s — Standardwerte verwenden.", e)
        else:
            log.info("Keine gateway.json gefunden — Standardwerte aktiv.")

    if callsign:
        cfg["callsign"] = callsign.upper()

    return cfg


def _deep_merge(base: dict, override: dict) -> dict:
    """Verschachtelte Dicts zusammenführen (override hat Vorrang)."""
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# ═══════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════

def setup_logging(verbose: bool, bus: Optional[EventBus] = None) -> None:
    """
    Python-Logging konfigurieren.
    - Farbiger GustFormatter für Terminal-Ausgabe
    - aiohttp.access auf WARNING → kein HTTP-Poll-Spam
    - aiohttp.server auf WARNING → kein Connection-Spam
    - Optionaler EventBus-Handler für Web-GUI /ws/log
    """
    level = logging.DEBUG if verbose else logging.INFO

    # Root-Logger: GustFormatter auf stderr
    root = logging.getLogger()
    root.setLevel(level)

    if not root.handlers:
        handler = _GustStreamHandler(sys.stderr)
        handler.setFormatter(GustFormatter())
        handler.addFilter(_SkipEmptyFilter())
        root.addHandler(handler)
    else:
        # basicConfig wurde bereits aufgerufen — Handler ersetzen
        root.handlers.clear()
        handler = _GustStreamHandler(sys.stderr)
        handler.setFormatter(GustFormatter())
        handler.addFilter(_SkipEmptyFilter())
        root.addHandler(handler)

    # aiohttp-Spam unterdrücken
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.server").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.web").setLevel(logging.WARNING)

    if bus is not None:
        loop    = asyncio.get_event_loop()
        eb_handler = EventBusLogHandler(bus)
        eb_handler.set_loop(loop)
        eb_handler.setLevel(logging.INFO)
        logging.getLogger("gust").addHandler(eb_handler)
        logging.getLogger("demo").addHandler(eb_handler)


# ═══════════════════════════════════════════════════════════════════════
# HILFSFUNKTIONEN
# ═══════════════════════════════════════════════════════════════════════

def _channel_info(callsign: str, n_channels: int = 10,
                  interval: int = 300) -> dict:
    """Kanal und Zeitversatz für ein Rufzeichen berechnen."""
    h      = int(hashlib.sha256(callsign.upper().encode()).hexdigest(), 16)
    ch     = h % n_channels
    offset = (h >> 8) % interval
    nf_lo  = 400 + ch * 250
    nf_hi  = nf_lo + 250
    return {
        "callsign":  callsign.upper(),
        "channel":   ch,
        "offset_s":  offset,
        "interval_s": interval,
        "nf_lo":     nf_lo,
        "nf_hi":     nf_hi,
    }


def _print_banner(cfg: dict, mode: str) -> None:
    cs       = cfg["callsign"]
    ci       = _channel_info(cs, interval=cfg["gateway"]["interval_s"])
    om, os_  = divmod(ci["offset_s"], 60)
    im       = ci["interval_s"] // 60
    port     = cfg["web"]["port"]
    freq_str = f"{ci['nf_lo']}–{ci['nf_hi']} Hz NF"
    off_str  = f"+{om}m {os_:02d}s  (Schedule: {im} min)"
    url_str  = f"http://localhost:{port}"

    W  = 76   # Textfeld-Breite: 80 gesamt − 2×║ − 2 Leerzeichen Einzug = 76 (Box-Rand bündig)
    HR = '╠' + '═' * 78 + '╣'
    def row(text): return f'║  {text:<{W}}║'

    print(f"""
╔{'═'*78}╗
{row(f'GUST  v{VERSION}   [{mode}]')}
{HR}
{row(f'Rufzeichen : {cs}')}
{row(f'Kanal      : {ci["channel"]}  ({freq_str})')}
{row(f'TX-Offset  : {off_str}')}
{row(f'Web-UI     : {url_str}')}
{row('Stoppen    : Strg+C')}
{HR}
{row('Aufrufe:')}
{row('')}
{row('  gust.py daemon')}
{row('    TX + RX Vollbetrieb mit echter Hardware (Transceiver + Audio)')}
{row('')}
{row('  gust.py daemon --sim')}
{row('    Wie daemon, aber SimAdapter speist synthetische Frames ein —')}
{row('    kein echtes HF-Signal nötig, ideal zum Testen der Web-GUI')}
{row('')}
{row('  gust.py daemon --dry-run')}
{row('    TX-Pipeline vollständig aktiv, aber kein Audio/PTT-Ausgang —')}
{row('    Frame-Erzeugung und Scheduling testbar ohne Sender')}
{row('')}
{row('  gust.py daemon --sim --dry-run')}
{row('    Kombination: SimAdapter + kein TX — reine Software-Simulation')}
{row('')}
{row('  gust.py rx')}
{row('    Nur Empfang + Web-UI, kein TX-Gateway gestartet')}
{row('')}
{row('  gust.py tx weather / position / text / emergency')}
{row('    Einmaliger One-Shot TX, danach beenden')}
{row('')}
{row('  gust.py info [RUFZEICHEN]')}
{row('    Kanal + TX-Offset für ein Rufzeichen anzeigen')}
{row('')}
{row('  gust.py devices')}
{row('    Verfügbare Audiogeräte mit IDs auflisten')}
╚{'═'*78}╝
""")


# ═══════════════════════════════════════════════════════════════════════
# SUBCOMMAND: daemon
# ═══════════════════════════════════════════════════════════════════════

async def cmd_daemon(cfg: dict, dry_run: bool, use_sim: bool) -> None:
    """
    Vollbetrieb: SimAdapter → EventBus → WebServer + TX-Gateway.
    TX-Gateway (gust_gateway.TxGateway) verbindet die Web-/REST-„Senden"-
    Buttons mit der Audio-TX-Pipeline. Im DRY-RUN nimmt es Frames an,
    sendet aber nicht.
    """
    bus = EventBus()
    setup_logging(cfg.get("_verbose", False), bus)

    # ── Datenquelle ───────────────────────────────────────────────────
    if use_sim:
        # Explizit --sim: Simulations-Frames im Web-Feed (kein HF-Pfad)
        sim_cfg = cfg["source"].get("sim", {})
        sim_cfg.setdefault("lat",   cfg["source"].get("lat",   48.2082))
        sim_cfg.setdefault("lon",   cfg["source"].get("lon",   16.3738))
        adapter = SimAdapter(sim_cfg, callsign=cfg["callsign"])
        source_label = "SimAdapter"
    else:
        # Standard (auch --device, --dry-run): kein Sim, nur echter RX-Pfad.
        # NullAdapter liefert nie Frames → Web-Feed zeigt ausschließlich
        # echte über gust_rx.py dekodierte Frames.
        class _NullAdapter:
            def read_all_due(self): return []
            def next_due_in(self): return 1.0
        adapter = _NullAdapter()
        source_label = "RX-only (kein Sim)"

    if dry_run:
        log.info("DRY-RUN aktiv — kein TX, kein Audio.")

    # ── TX-Gateway ────────────────────────────────────────────────────
    # Verbindet die Web-/REST-„Senden"-Buttons mit der Audio-TX-Pipeline.
    # Im DRY-RUN werden Frames angenommen und geloggt, aber nicht gesendet.
    from gust_gateway import TxGateway
    gateway = TxGateway(cfg, event_bus=bus, dry_run=dry_run)

    # ── WebServer ─────────────────────────────────────────────────────
    server = WebServer(cfg, event_bus=bus, gateway=gateway,
                       config_path=cfg.get("_config_path"))
    await server.start()
    await gateway.start()
    await asyncio.sleep(0.1)   # EventBus-Reader Zeit zum Subscriben geben

    _mode_badges = ["DAEMON"]
    if use_sim:  _mode_badges.append("SIM")
    if dry_run:  _mode_badges.append("DRY-RUN")
    _print_banner(cfg, " · ".join(_mode_badges))
    log.info("Daemon gestartet. Modus: %s", " · ".join(_mode_badges))

    # ── Haupt-Loop ────────────────────────────────────────────────────
    start_time  = time.time()
    last_status = 0.0

    # ── RX-Loop starten ───────────────────────────────────────────────
    from gust_rx import build_rx_loop
    rx = build_rx_loop(cfg, bus)
    rx_task = None
    if rx is not None and not dry_run:
        rx_task = asyncio.create_task(rx.run(), name="rx_loop")
        log.info(
            "RX-Loop aktiv  |  Gerät: %s  |  Intervall: %.1fs  |  Fenster: %.1fs",
            cfg["rx"].get("device") or "Standard",
            cfg["rx"].get("scan_interval_s", 2.0),
            cfg["rx"].get("window_s", 8.0),
        )

    try:
        while True:
            # Fällige Frames vom Adapter holen und in den Bus publishen
            for frame in adapter.read_all_due():
                event = make_rx_frame_event(frame)
                await bus.publish(event)
                log.info("SIM-TX: [P%d] %s von %s",
                         frame.get("priority", 0), frame.get("type_name", "?"), frame.get("from", "?"))

            # Periodischer Status-Event (alle 60 s)
            now = time.time()
            if now - last_status >= 60:
                last_status = now
                ci = _channel_info(cfg["callsign"],
                                   interval=cfg["gateway"]["interval_s"])
                await bus.publish(make_status_event(
                    callsign    = cfg["callsign"],
                    uptime_s    = now - start_time,
                    home_channel= ci["channel"],
                    audio_device= cfg["audio"].get("device") or "–",
                    ptt_backend = cfg["audio"].get("ptt_backend", "null"),
                ))

            wait = min(adapter.next_due_in() or 1.0, 0.5)
            await asyncio.sleep(wait)

    except asyncio.CancelledError:
        pass
    finally:
        log.info("Daemon wird beendet …")
        if rx_task and not rx_task.done():
            rx_task.cancel()
            try:
                await rx_task
            except asyncio.CancelledError:
                pass
        await gateway.stop()
        await server.stop()


# ═══════════════════════════════════════════════════════════════════════
# SUBCOMMAND: rx
# ═══════════════════════════════════════════════════════════════════════

async def cmd_rx(cfg: dict) -> None:
    """
    Monitor-Modus: WebServer + EventBus + kontinuierlicher RX-Loop.
    Kein TX — reine Empfangsstation.
    """
    from gust_rx import build_rx_loop

    bus = EventBus()
    setup_logging(cfg.get("_verbose", False), bus)

    server = WebServer(cfg, event_bus=bus, config_path=cfg.get("_config_path"))
    await server.start()
    await asyncio.sleep(0.1)

    _print_banner(cfg, "RX-MONITOR")
    log.info("Monitor-Modus aktiv.")

    # ── RX-Loop starten ───────────────────────────────────────────────
    rx = build_rx_loop(cfg, bus)
    rx_task = None
    if rx is not None:
        rx_task = asyncio.create_task(rx.run(), name="rx_loop")
        log.info(
            "RX-Loop aktiv  |  Gerät: %s  |  Intervall: %.1fs  |  Fenster: %.1fs",
            cfg["rx"].get("device") or "Standard",
            cfg["rx"].get("scan_interval_s", 2.0),
            cfg["rx"].get("window_s", 8.0),
        )
    else:
        log.info("RX-Loop deaktiviert (rx.enabled=false)")

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        if rx_task and not rx_task.done():
            rx_task.cancel()
            try:
                await rx_task
            except asyncio.CancelledError:
                pass
        await server.stop()


# ═══════════════════════════════════════════════════════════════════════
# TX-HILFSFUNKTIONEN  (Phase 7)
# ═══════════════════════════════════════════════════════════════════════

def _build_ptt(audio_cfg: dict, full_cfg: dict | None = None):
    """
    PTT-Backend aus Konfiguration erzeugen.

    audio.ptt_backend:
      "null"   → NullPTT (Simulation, kein Hardware — Standard)
      "hamlib" → HamlibPTT via rigctld (IC-7610 etc.)
      "gpio"   → GPIUPTT via Raspberry Pi GPIO

    Zusätzliche Felder in audio_cfg:
      hamlib_host  (Standard: "localhost")
      hamlib_port  (Standard: 4532)
      gpio_pin     (Standard: 17)

    Bei backend == "hamlib" wird vor dem Verbindungsaufbau geprüft, ob
    rigctld läuft. Falls nicht und cfg['rigctld'].auto_start == true,
    wird rigctld als Hintergrundprozess gestartet (siehe
    gust_audio.ensure_rigctld_running).
    """
    from gust_audio import NullPTT, HamlibPTT, GPIUPTT, ensure_rigctld_running
    backend = audio_cfg.get("ptt_backend", "null").lower()

    if backend == "hamlib":
        host = audio_cfg.get("hamlib_host", "localhost")
        port = int(audio_cfg.get("hamlib_port", 4532))
        # rigctld ggf. starten (oder klare Fehlermeldung)
        try:
            ensure_rigctld_running(full_cfg or {}, host=host, port=port)
        except RuntimeError as e:
            log.error("rigctld-Vorbereitung fehlgeschlagen:\n%s", e)
            raise
        log.info("PTT-Backend: HamlibPTT @ %s:%d", host, port)
        return HamlibPTT(host=host, port=port)

    elif backend == "gpio":
        pin = int(audio_cfg.get("gpio_pin", 17))
        log.info("PTT-Backend: GPIUPTT Pin %d", pin)
        return GPIUPTT(pin=pin)

    else:
        log.info("PTT-Backend: NullPTT (Simulation)")
        return NullPTT()


def _build_payload(frame_type_str: str, tx_args: dict) -> tuple:
    """
    CLI-Argumente → (frame_type_int, payload_bytes).

    Direkte Kodierung aus CLI-Werten — keine Umwege über SimAdapter.
    Für TEXT: nur das erste Fragment wird kodiert (One-Shot TX).

    Returns:
        (FrameType-Konstante, bytes)

    Raises:
        ValueError: Unbekannter Frame-Typ
        ImportError: gust_frame nicht verfügbar
    """
    from gust_frame import (
        FrameType,
        encode_weather,
        encode_position,
        encode_emergency_beacon,
        fragment_text,
        INJURY_UNKNOWN,
        POS_FLAG_MOBILE, POS_FLAG_GPS_FIX,
        PRIO_URGENT,
    )

    t = frame_type_str.upper()

    if t == "WEATHER":
        payload = encode_weather(
            temp_c       = tx_args.get("temp_c",       20.0),
            humidity_pct = tx_args.get("humidity_pct", 65),
            pressure_hpa = tx_args.get("pressure_hpa", 1013.2),
            wind_kmh     = tx_args.get("wind_kmh",     0),
            wind_deg     = tx_args.get("wind_deg",     270),
            rain_mm_h    = tx_args.get("rain_mm_h",    0.0),
            uv_index     = tx_args.get("uv_index",     3),
            flags        = 0x03,   # bat_ok + sensor_ok
        )
        return FrameType.WEATHER, payload

    elif t == "POSITION":
        mobile = tx_args.get("mobile", False)
        flags  = POS_FLAG_GPS_FIX | (POS_FLAG_MOBILE if mobile else 0)
        payload = encode_position(
            lat_deg     = tx_args.get("lat",         48.2082),
            lon_deg     = tx_args.get("lon",         16.3738),
            alt_m       = tx_args.get("alt_m",       180),
            speed_kmh   = tx_args.get("speed_kmh",   0),
            heading_deg = tx_args.get("heading_deg", 0),
            timestamp   = 0,
            flags       = flags,
        )
        return FrameType.POSITION, payload

    elif t == "EMERGENCY":
        text = tx_args.get("emg_text", "HELP")[:4].upper()
        payload = encode_emergency_beacon(
            lat_deg        = tx_args.get("lat",     48.2082),
            lon_deg        = tx_args.get("lon",     16.3738),
            persons        = tx_args.get("persons", 1),
            injury_code    = tx_args.get("injury",  INJURY_UNKNOWN),
            resource_flags = 0,
            priority       = PRIO_URGENT,
            text_snippet   = text,
        )
        return FrameType.EMERG_BEACON, payload

    elif t == "TEXT":
        messages = tx_args.get("messages", ["Test 73"])
        dest     = tx_args.get("dest",     "CQCQCQ")
        text     = messages[0] if messages else "Test 73"
        frags    = fragment_text(text, dest_call=dest, seq_nr=0)
        # One-Shot TX: erstes Fragment senden
        # Mehrteiliger Text → mehrere tx-Aufrufe nötig (Phase 8)
        if len(frags) > 1:
            log.warning("Text zu lang für einen Frame — nur Fragment 1/%d wird gesendet.",
                        len(frags))
        return FrameType.TEXT, frags[0]

    else:
        raise ValueError(f"Unbekannter Frame-Typ: '{frame_type_str}'")


# ═══════════════════════════════════════════════════════════════════════
# SUBCOMMAND: tx
# ═══════════════════════════════════════════════════════════════════════

async def cmd_tx(cfg: dict, frame_type: str,
                 dry_run: bool, tx_args: dict) -> None:
    """
    One-Shot TX: einen einzelnen Frame erzeugen und senden.

    Ablauf:
        1. Frame-Anzeige via SimAdapter (Werte aus CLI-Args)
        2. --dry-run → Abbruch nach Anzeige
        3. Payload direkt aus CLI-Args kodieren (_build_payload)
        4. PTT-Backend aus Konfiguration instanziieren (_build_ptt)
        5. AudioTransmitter.transmit_frame() im Thread-Executor aufrufen
           (blockierender sounddevice-Aufruf bleibt außerhalb des Event-Loops)
    """
    cs = cfg["callsign"]

    # ── Frame-Anzeige via SimAdapter ──────────────────────────────────
    sim_cfg = dict(cfg["source"].get("sim", {}))
    sim_cfg.update({
        "base_temp_c": tx_args.get("temp_c",   20.0),
        "lat":         tx_args.get("lat",       48.2082),
        "lon":         tx_args.get("lon",       16.3738),
        "alt_m":       tx_args.get("alt_m",     180),
        "drift":       tx_args.get("mobile",    False),
        "messages":    tx_args.get("messages",  ["Test 73"]),
        "emg_text":    tx_args.get("emg_text",  "HELP"),
    })
    sim_cfg["emergency_enabled"] = (frame_type == "emergency")

    adapter = SimAdapter(sim_cfg, callsign=cs)
    frame   = adapter.trigger(frame_type)

    if frame is None:
        print(f"✗  Fehler: Frame-Typ '{frame_type}' konnte nicht erzeugt werden.",
              file=sys.stderr)
        return

    d    = frame["data"]
    typ  = frame["type_name"]
    prio = frame["priority"]

    print(f"\n  Frame erzeugt: [{typ}]  Prio {prio}  von {frame['from']}")
    print(f"  {'─'*50}")
    for k, v in d.items():
        print(f"    {k:<20} = {v}")

    if dry_run:
        print(f"\n  DRY-RUN — Frame wird nicht gesendet.\n")
        return

    # ── Payload kodieren ──────────────────────────────────────────────
    try:
        frame_type_int, payload = _build_payload(frame_type, tx_args)
    except Exception as e:
        print(f"\n  ✗  Payload-Fehler: {e}", file=sys.stderr)
        return

    # ── TX-Pfad wählen: SDR (SoapySDR) oder NF-Audio ─────────────────
    sdr_cfg = cfg.get("sdr_tx") or {}
    if sdr_cfg.get("enabled"):
        await _tx_via_sdr(cs, frame_type_int, payload, sdr_cfg)
        return

    try:
        from gust_audio import AudioTransmitter, AUDIO_LEVEL
        from gust_frame  import channel_frequency, CHANNEL_BW_HZ

        ptt = _build_ptt(cfg["audio"], cfg)

        # Level aus Config: Wert > 1 wird als Prozent interpretiert (50 → 0.5)
        raw_level = cfg["audio"].get("level", AUDIO_LEVEL * 100)
        level = max(0.01, min(1.0, raw_level / 100.0)) if raw_level > 1.0 else float(raw_level)

        print(f"\n  TX startet …")
        print(f"  PTT:       {ptt.__class__.__name__}")
        print(f"  PTT-Delay: {cfg['audio'].get('ptt_delay_ms', 250)} ms (Lead + Tail)")
        print(f"  Gerät:     {cfg['audio'].get('device') or 'Standard'}")
        print(f"  Kanal:     wird aus SHA-256({cs}) bestimmt")
        print(f"  RC-Fenster: aktiv (window=True)")

        loop = asyncio.get_event_loop()

        _ptt_delay_s = cfg["audio"].get("ptt_delay_ms", 250) / 1000.0
        with AudioTransmitter(
            ptt        = ptt,
            device     = cfg["audio"].get("device"),
            level      = level,
            ptt_lead_s = _ptt_delay_s,
            ptt_tail_s = _ptt_delay_s,
        ) as tx:
            # transmit_frame() ist blockierend (sounddevice + time.sleep)
            # → im Thread-Executor ausführen, Event-Loop bleibt reaktiv
            used_ch = await loop.run_in_executor(
                None,
                lambda: tx.transmit_frame(
                    frame_type_int, cs, payload,
                    channel  = None,   # automatisch aus SHA-256(callsign)
                    use_fec  = True,
                    window   = True,   # Raised Cosine — P7-03
                )
            )

        f_lo = channel_frequency(used_ch)
        print(f"\n  ✓  Gesendet auf Kanal {used_ch}  "
              f"({f_lo:.0f}–{f_lo + CHANNEL_BW_HZ:.0f} Hz NF)\n")

    except RuntimeError as e:
        # sounddevice nicht installiert, rigctld nicht erreichbar etc.
        print(f"\n  ✗  TX-Fehler: {e}", file=sys.stderr)
    except Exception as e:
        print(f"\n  ✗  Unerwarteter Fehler beim TX: {e}", file=sys.stderr)


async def _tx_via_sdr(callsign: str, frame_type_int: int, payload: bytes,
                       sdr_cfg: dict) -> None:
    """
    TX via generischen SoapySDR-Pfad (P7-04 / ADR-16). Wird aus cmd_tx()
    aufgerufen, wenn `sdr_tx.enabled` in gateway.json gesetzt ist.

    Frame-Layer + Modulator bleiben unverändert — `transmit()` liefert das
    fertige NF-Audio, das wir hier in IQ konvertieren und über
    `SoapyTxBackend.transmit_iq()` ausgeben.
    """
    try:
        from gust_modulator import transmit, SAMPLE_RATE
        from gust_frame      import channel_frequency, CHANNEL_BW_HZ
        from gust_hackrf     import nf_to_iq_usb
        from gust_soapy_tx   import SoapyTxBackend, soapy_available

        if not soapy_available():
            print("\n  ✗  SDR-TX aktiv, aber SoapySDR-Bindings fehlen.\n"
                  "      Entweder Python 3.9 + PothosSDR verwenden oder "
                  "`sdr_tx.enabled: false` in gateway.json setzen.",
                  file=sys.stderr)
            return

        device_args = sdr_cfg.get("device_args") or {}
        if not device_args.get("driver"):
            print("\n  ✗  sdr_tx.device_args.driver fehlt — bitte im Web-UI "
                  "ein Gerät auswählen oder Konfig anpassen.", file=sys.stderr)
            return

        sample_rate = int(sdr_cfg.get("sample_rate", 2_000_000))
        freq_hz     = float(sdr_cfg.get("freq_hz",     14_110_000))
        antenna     = sdr_cfg.get("antenna")    or None
        gain        = sdr_cfg.get("gain")       or {"normalized": 0.5}
        tx_channel  = int(sdr_cfg.get("tx_channel", 0))

        # NF-Audio + Kanal genau wie im Audio-Pfad erzeugen
        audio, used_ch, frame_body = transmit(
            frame_type_int, callsign, payload,
            channel=None, use_fec=True, window=True, add_silence_ms=100,
        )

        print(f"\n  TX startet (SDR via SoapySDR) …")
        print(f"  Gerät:       {sdr_cfg.get('label') or device_args}")
        print(f"  Sample-Rate: {sample_rate/1e6:.3f} MSps")
        print(f"  Frequenz:    {freq_hz/1e6:.6f} MHz (USB-Dial)")
        print(f"  Antenne:     {antenna or '(Default)'}")
        print(f"  Gain:        {gain}")
        print(f"  Kanal:       {used_ch}  ({channel_frequency(used_ch):.0f} Hz NF)")

        loop = asyncio.get_event_loop()
        # NF→IQ und das blockierende setupStream/writeStream im Executor
        def _do_tx():
            iq = nf_to_iq_usb(audio, sample_rate)
            with SoapyTxBackend(
                device_args=device_args,
                freq_hz=freq_hz,
                sample_rate=sample_rate,
                channel=tx_channel,
                antenna=antenna,
                gain=gain,
            ) as tx:
                tx.transmit_iq(iq)
        await loop.run_in_executor(None, _do_tx)

        f_lo = channel_frequency(used_ch)
        print(f"\n  ✓  Gesendet via SDR auf Kanal {used_ch}  "
              f"({f_lo:.0f}–{f_lo + CHANNEL_BW_HZ:.0f} Hz NF)\n")

    except RuntimeError as e:
        print(f"\n  ✗  SDR-TX-Fehler: {e}", file=sys.stderr)
    except Exception as e:
        print(f"\n  ✗  Unerwarteter SDR-TX-Fehler: {e}", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════════
# SUBCOMMAND: info
# ═══════════════════════════════════════════════════════════════════════

def cmd_info(callsign: str, interval: int = 300) -> None:
    """Kanalzuweisung und TX-Timing für ein Rufzeichen anzeigen."""
    ci  = _channel_info(callsign, interval=interval)
    nf  = ci["nf_lo"]
    off = ci["offset_s"]
    om, os_ = divmod(off, 60)
    im  = interval // 60

    print(f"""
  Rufzeichen:   {ci['callsign']}
  ─────────────────────────────────────
  Heimatkanal:  {ci['channel']}
  NF-Bereich:   {ci['nf_lo']}–{ci['nf_hi']} Hz
  RF-Offset:    +{nf - 400:.0f} Hz über Dial-Frequenz

  TX-Offset:    +{om}m {os_:02d}s  (= {off} s im {im}-min-Schedule)
  TX-Schedule:  alle {im} min
  ─────────────────────────────────────
  Formel: SHA-256("{ci['callsign']}") % 10  → Kanal {ci['channel']}
""")


# ═══════════════════════════════════════════════════════════════════════
# SUBCOMMAND: devices
# ═══════════════════════════════════════════════════════════════════════

def cmd_devices() -> None:
    """Verfügbare Audiogeräte auflisten."""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        default_in, default_out = sd.default.device

        print(f"\n  {'ID':<4}  {'Name':<40}  {'In':<4}  {'Out':<4}")
        print(f"  {'─'*60}")
        for i, d in enumerate(devices):
            di  = "●" if i == default_in  else ("" if d["max_input_channels"]  > 0 else "")
            do  = "●" if i == default_out else ("" if d["max_output_channels"] > 0 else "")
            name = d["name"][:38]
            ic   = str(d["max_input_channels"])  if d["max_input_channels"]  > 0 else "–"
            oc   = str(d["max_output_channels"]) if d["max_output_channels"] > 0 else "–"
            marker = "◄" if i == default_in or i == default_out else " "
            print(f"  {i:<4}  {name:<40}  {ic:<4}  {oc:<4}  {marker}")

        print(f"\n  Standard-Eingang:  #{default_in}")
        print(f"  Standard-Ausgang:  #{default_out}")
        print(f"\n  Verwendung in gateway.json:")
        print(f'    "audio": {{"device": {default_out}}}')
        print()
    except ImportError:
        print("\n  sounddevice nicht installiert.")
        print("  Installation:  pip install sounddevice\n")


# ═══════════════════════════════════════════════════════════════════════
# ARGUMENT-PARSER
# ═══════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gust",
        description="GUST — HF Telemetrie & Gateway System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  py gust.py daemon --sim               Vollbetrieb mit Simulator
  py gust.py daemon --dry-run           Kein TX, Web-UI aktiv
  py gust.py rx                         Nur Monitor-Modus
  py gust.py tx weather --dry-run       Wetter-Frame anzeigen
  py gust.py tx text "Hallo" OE1XTU    Text senden
  py gust.py info OE3GAS               Kanalinfo anzeigen
  py gust.py info                       Eigener Kanal
  py gust.py devices                    Audiogeräte auflisten
        """,
    )

    # ── Globale Optionen ─────────────────────────────────────────────
    parser.add_argument("--callsign", "-c", metavar="RUFZEICHEN",
        help="Eigenes Rufzeichen (überschreibt gateway.json)")
    parser.add_argument("--config", metavar="DATEI", default="gateway.json",
        help="Konfigurationsdatei (default: gateway.json)")
    parser.add_argument("--dry-run", action="store_true",
        help="Kein TX, kein Audio — nur Anzeige/Web")
    parser.add_argument("--sim", action="store_true",
        help="Simulator als Datenquelle erzwingen")
    parser.add_argument("--verbose", "-v", action="store_true",
        help="Debug-Logging aktivieren")
    parser.add_argument("--version", action="version", version=f"GUST {VERSION}")

    sub = parser.add_subparsers(dest="cmd", metavar="SUBCOMMAND")
    sub.required = True

    # Hilfs-Funktion: --device/--level zu einem Subparser hinzufügen
    def _add_audio_args(p):
        p.add_argument("--device", metavar="ID",
                       help="Audio-Gerät: Nummer (z.B. 9) oder Name "
                            "(überschreibt gateway.json — siehe py gust.py devices)")
        p.add_argument("--level", type=float, default=None, metavar="%",
                       help="Audio-Ausgangspegel 1–100%% "
                            "(überschreibt gateway.json)")

    # ── daemon ────────────────────────────────────────────────────────
    p_daemon = sub.add_parser("daemon",
        help="Vollbetrieb: TX + RX + Web-Server")
    p_daemon.add_argument("--port",     type=int,
        help="Web-Server Port (überschreibt gateway.json)")
    p_daemon.add_argument("--sim",      action="store_true",
        help="Simulator als Datenquelle (auch nach dem Subcommand)")
    p_daemon.add_argument("--frames",   nargs="+",
        choices=["weather","position","text","emergency","all"],
        help="Simulator-Frame-Typen")
    p_daemon.add_argument("--interval", type=int,
        help="Simulator-Intervall in Sekunden")
    _add_audio_args(p_daemon)

    # ── rx ────────────────────────────────────────────────────────────
    p_rx = sub.add_parser("rx",
        help="Monitor-Modus (nur Empfang + Web-Server)")
    _add_audio_args(p_rx)

    # ── tx ────────────────────────────────────────────────────────────
    p_tx = sub.add_parser("tx",
        help="Einzelnen Frame senden",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Typen:  weather · position · text · emergency\n\n"
            "Beispiele:\n"
            "  py gust.py tx weather --temp 22 --dry-run\n"
            "  py gust.py tx text 'Test 73' OE1XTU\n"
            "  py gust.py tx position --lat 47.8 --lon 13.0\n"
            "  py gust.py tx emergency --confirm\n"
        ),
    )
    p_tx.add_argument("tx_type",
        choices=["weather", "position", "text", "emergency"],
        metavar="TYP",
        help="weather | position | text | emergency")
    # Gemeinsame Felder (Positionsangaben für position + emergency)
    p_tx.add_argument("message", nargs="?", default="OE3GAS de OE3GAS Test 73",
                      help="Text-Nachricht (nur für tx text)")
    p_tx.add_argument("dest",    nargs="?", default="CQCQCQ",
                      metavar="ZIEL", help="Ziel-Rufzeichen (nur für tx text)")
    # Wetter
    p_tx.add_argument("--temp",     type=float, default=20.0,   metavar="°C")
    p_tx.add_argument("--humidity", type=int,   default=65,     metavar="%")
    p_tx.add_argument("--pressure", type=float, default=1013.2, metavar="hPa")
    p_tx.add_argument("--wind",     type=int,   default=10,     metavar="km/h")
    p_tx.add_argument("--wind-dir", type=int,   default=270,    metavar="°")
    p_tx.add_argument("--rain",     type=float, default=0.0,    metavar="mm/h")
    p_tx.add_argument("--uv",       type=int,   default=3)
    # Position
    p_tx.add_argument("--lat",      type=float, default=48.2082)
    p_tx.add_argument("--lon",      type=float, default=16.3738)
    p_tx.add_argument("--alt",      type=int,   default=180,    metavar="m")
    p_tx.add_argument("--speed",    type=int,   default=0,      metavar="km/h")
    p_tx.add_argument("--heading",  type=int,   default=0,      metavar="°")
    p_tx.add_argument("--mobile",   action="store_true")
    # Notfall
    p_tx.add_argument("--persons",  type=int,   default=1)
    p_tx.add_argument("--injury",   type=int,   default=0,
                      choices=[0,1,2,3],
                      help="0=unbekannt 1=leicht 2=schwer 3=kritisch")
    p_tx.add_argument("--emg-text", default="HELP", metavar="4-ZEICHEN")
    p_tx.add_argument("--confirm",  action="store_true",
                      help="Sicherheitsbestätigung für emergency (ohne --dry-run Pflicht)")
    # Komfort-Optionen: auch nach dem Subcommand erlaubt
    p_tx.add_argument("--dry-run",  action="store_true",
                      help="Frame anzeigen ohne zu senden")
    p_tx.add_argument("--callsign", "-c", metavar="RUFZEICHEN",
                      help="Rufzeichen (überschreibt gateway.json)")
    _add_audio_args(p_tx)

    # ── info ──────────────────────────────────────────────────────────
    p_info = sub.add_parser("info",
        help="Kanalzuweisung für Rufzeichen anzeigen")
    p_info.add_argument("target_callsign", nargs="?",
        metavar="RUFZEICHEN",
        help="Rufzeichen (optional, Standard: eigenes)")

    # ── devices ───────────────────────────────────────────────────────
    sub.add_parser("devices",
        help="Verfügbare Audiogeräte auflisten")

    return parser


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = build_parser()

    # No-Args-Hint — vor parse_args(), damit Sub-Required nicht zuerst greift
    if len(sys.argv) == 1:
        print("Verwendung: python gust.py -h  oder  --help  für Parameterübersicht")
        sys.exit(0)

    args   = parser.parse_args()

    # Logging früh initialisieren (ohne Bus, wird bei daemon/rx neu gesetzt)
    setup_logging(args.verbose)

    # ── Subcommands ohne asyncio ──────────────────────────────────────
    if args.cmd == "info":
        cfg    = load_config(args.config, args.callsign)
        target = (getattr(args, "target_callsign", None)
                  or args.callsign
                  or cfg["callsign"])
        cmd_info(target, interval=cfg["gateway"]["interval_s"])
        return

    if args.cmd == "devices":
        cmd_devices()
        return

    # ── Konfiguration laden ───────────────────────────────────────────
    cfg = load_config(args.config, args.callsign)
    cfg["_verbose"]     = args.verbose
    cfg["_config_path"] = args.config   # für WebServer (Audio-Settings speichern)

    # Port-Override für daemon
    if args.cmd == "daemon" and hasattr(args, "port") and args.port:
        cfg["web"]["port"] = args.port

    # Audio-Overrides für alle Subcommands die --device/--level kennen
    _apply_audio_overrides(args, cfg)

    # ── Windows asyncio Policy ────────────────────────────────────────
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # ── asyncio Subcommands ───────────────────────────────────────────
    if args.cmd == "daemon":
        # --sim kann global (vor 'daemon') ODER lokal (nach 'daemon') stehen
        use_sim = args.sim or getattr(args, "sim", False) or args.dry_run
        if use_sim or args.dry_run:
            cfg["source"]["adapter"] = "sim"
        if hasattr(args, "frames") and args.frames:
            frames = args.frames
            if "all" in frames:
                frames = ["weather", "position", "text"]
            cfg["source"]["sim"]["frames"] = frames
        if hasattr(args, "interval") and args.interval:
            for ftype in ["weather", "position", "text", "emergency"]:
                cfg["source"]["sim"][f"{ftype}_interval_s"] = args.interval

        _run_async(cmd_daemon(cfg, args.dry_run, use_sim))

    elif args.cmd == "rx":
        _run_async(cmd_rx(cfg))

    elif args.cmd == "tx":
        # --dry-run und --callsign gelten global (vor tx) ODER lokal (nach tx weather)
        dry_run  = args.dry_run or getattr(args, "dry_run", False)
        callsign = getattr(args, "callsign", None) or args.callsign
        if callsign:
            cfg["callsign"] = callsign.upper()

        # --device / --level: bereits via _apply_audio_overrides() angewendet

        if args.tx_type == "emergency":
            if not dry_run and not args.confirm:
                print("\n  ⚠  tx emergency erfordert --confirm oder --dry-run!\n",
                      file=sys.stderr)
                sys.exit(1)

        tx_args = {
            # Wetter
            "temp_c":       args.temp,
            "humidity_pct": args.humidity,
            "pressure_hpa": args.pressure,
            "wind_kmh":     args.wind,
            "wind_deg":     args.wind_dir,
            "rain_mm_h":    args.rain,
            "uv_index":     args.uv,
            # Position
            "lat":          args.lat,
            "lon":          args.lon,
            "alt_m":        args.alt,
            "speed_kmh":    args.speed,
            "heading_deg":  args.heading,
            "mobile":       args.mobile,
            # Text
            "messages":     [args.message],
            "dest":         args.dest,
            # Notfall
            "persons":      args.persons,
            "injury":       args.injury,
            "emg_text":     args.emg_text,
        }
        asyncio.run(cmd_tx(cfg, args.tx_type, dry_run, tx_args))


def _apply_audio_overrides(args, cfg: dict) -> None:
    """CLI-Overrides für audio.device / audio.level anwenden (alle Subcommands).

    --device akzeptiert Ganzzahl (ID) oder Name (String) — Integer wird bevorzugt
    geparst, da Windows MME mehrere Geräte mit gleichem Namen meldet.
    --level wird von Prozent (1–100) in float (0.01–1.0) umgerechnet.
    """
    device_arg = getattr(args, "device", None)
    if device_arg is not None:
        try:
            cfg["audio"]["device"] = int(device_arg)
        except ValueError:
            cfg["audio"]["device"] = device_arg

    level_arg = getattr(args, "level", None)
    if level_arg is not None:
        cfg["audio"]["level"] = max(0.01, min(1.0, level_arg / 100.0))


def _run_async(coro) -> None:
    """Asyncio-Loop mit SIGINT/SIGTERM-Behandlung starten."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(coro)

    def _shutdown(sig_name: str) -> None:
        log.info("Signal %s empfangen — beende …", sig_name)
        task.cancel()

    # SIGTERM/SIGINT nur auf Unix (Windows kennt kein SIGTERM im asyncio)
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                sig, lambda s=sig.name: _shutdown(s))

    try:
        loop.run_until_complete(task)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        pending = asyncio.all_tasks(loop)
        for t in pending:
            t.cancel()
        if pending:
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True))
        loop.close()


if __name__ == "__main__":
    main()