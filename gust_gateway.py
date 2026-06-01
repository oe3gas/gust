#!/usr/bin/env python3
"""
GUST — TX-Gateway                                          Phase 7
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Version : 1.0.0
Datum   : Mai 2026

Sende-Gateway zwischen Web-/REST-Schicht (gust_web.py) und der
Audio-TX-Pipeline (gust_audio.AudioTransmitter). Schließt die bisherige
Lücke, dass die „Senden"-Buttons im Web-GUI nur eine Erfolgsmeldung
vortäuschten, aber nie wirklich sendeten.

── Funktionsweise ─────────────────────────────────────────────────────
  • enqueue(frame_dict, priority)  reiht einen Frame in eine
    asyncio-PriorityQueue ein (P1 = höchste Priorität, P4 = niedrigste).
  • Ein einzelner Worker-Task sendet immer genau einen Frame zur Zeit —
    es gibt nur einen Sender, paralleles TX ist physikalisch unmöglich.
  • Cooldown: Zwischen Nicht-Notfall-Frames wird ein Mindestabstand
    (gateway.min_tx_gap_s, Default 10 s) eingehalten. Wartende Frames
    bleiben in der Queue.
  • Notfall (Priorität 1) überspringt den Cooldown und überholt alle
    wartenden Frames (PriorityQueue + sofortiges Senden).
  • Der blockierende sounddevice-/PTT-Aufruf läuft im Thread-Executor,
    damit der asyncio-Event-Loop (Web-Server, RX-Loop) reaktiv bleibt.

── Schnittstelle für gust_web.WebServer (Duck-Typing) ──────────────────
  gateway.enqueue(frame_dict, priority=4)   # Frame einreihen
  gateway.get_status() -> dict              # queue_depth, last_tx, ...

  frame_dict (von gust_web._enqueue_tx erzeugt):
    {
      "frame_type": "weather"|"position"|"text"|"emergency",
      "data":       { ... Felder aus dem Web-Formular ... },
      "from":       "OE3GAS",
      "priority":   int,
      "ts":         float,
    }

── Lebenszyklus ───────────────────────────────────────────────────────
  gw = TxGateway(cfg, event_bus=bus, dry_run=False)
  await gw.start()          # Worker-Task starten
  ...                       # WebServer ruft gw.enqueue(...)
  await gw.stop()           # Worker geordnet beenden
"""

import asyncio
import hashlib
import itertools
import logging
import time
from typing import Optional

from gust_eventbus import make_tx_done_event

log = logging.getLogger("gust.gateway")

# Frames mit Priorität ≤ diesem Wert überspringen den Cooldown (Notfall, sofort).
EMERGENCY_PRIORITY = 1
# Frames mit Priorität ≥ diesem Wert werden auf den Stations-Slot terminiert
# (P3 Position / P4 Telemetrie). P1 Notfall = sofort, P2 Freitext = ≤ 30 s.
SLOT_MIN_PRIORITY = 3
# Grobe TX-Dauer eines Frames (s) — nur für die ETA-Vorschau der Queue.
APPROX_TX_S = 5.0
# Worst-Case-Reaktionszeit des Workers (s): er schläft höchstens so lange am
# Stück, damit ein neu eingereihter Notfall auch bei verpasstem Weck-Signal
# spätestens nach dieser Zeit drankommt (statt erst zum Slot in u.U. Minuten).
WORKER_POLL_S = 1.0


# ═══════════════════════════════════════════════════════════════════════
# PAYLOAD-KODIERUNG  (Web-Formularfelder → Frame-Payload)
# ═══════════════════════════════════════════════════════════════════════

def _encode_payload(frame_type_str: str, data: dict) -> tuple:
    """
    Web-Formulardaten in (FrameType-Konstante, [payload_bytes, …]) übersetzen.

    Die Rückgabe ist immer eine *Liste* von Payloads — bei Freitext kann ein
    langer Text in mehrere 0x40-Fragmente zerfallen, die nacheinander (ohne
    zusätzlichen Cooldown) gesendet werden. Alle anderen Typen liefern genau
    ein Payload.

    Die Feldnamen entsprechen den Schlüsseln, die das Web-UI in
    gust_web.sendTx() sendet (z.B. 'humidity', 'wind_dir', 'rain_mmh').

    Raises:
        ValueError: Unbekannter Frame-Typ.
    """
    from gust_frame import (
        FrameType,
        encode_weather, encode_position, encode_emergency_beacon,
        fragment_text,
        EVTYPE_OTHER,
        INJURY_UNKNOWN,
        POS_FLAG_MOBILE, POS_FLAG_GPS_FIX,
        PRIO_URGENT,
    )

    t = (frame_type_str or "").lower()

    if t == "weather":
        payload = encode_weather(
            temp_c       = float(data.get("temp_c",        20.0)),
            humidity_pct = int(  data.get("humidity",      65)),
            pressure_hpa = float(data.get("pressure_hpa",  1013.2)),
            wind_kmh     = int(  data.get("wind_kmh",      0)),
            wind_deg     = int(  data.get("wind_dir",      0)),
            rain_mm_h    = float(data.get("rain_mmh",      0.0)),
            uv_index     = int(  data.get("uv_index",      0)),
            flags        = 0x03,   # bat_ok + sensor_ok
        )
        return FrameType.WEATHER, [payload]

    if t == "position":
        mobile = bool(data.get("mobile", False))
        flags  = POS_FLAG_GPS_FIX | (POS_FLAG_MOBILE if mobile else 0)
        payload = encode_position(
            lat_deg     = float(data.get("lat",      48.2082)),
            lon_deg     = float(data.get("lon",      16.3738)),
            alt_m       = int(  data.get("alt_m",    0)),
            speed_kmh   = int(  data.get("speed_kmh", 0)),
            heading_deg = int(  data.get("heading",  0)),
            timestamp   = 0,
            flags       = flags,
        )
        return FrameType.POSITION, [payload]

    if t == "emergency":
        snippet = str(data.get("text_snippet", "HELP"))[:8].upper()
        payload = encode_emergency_beacon(
            lat_deg        = float(data.get("lat",        48.2082)),
            lon_deg        = float(data.get("lon",        16.3738)),
            persons        = int(  data.get("persons",    1)),
            event_type     = int(  data.get("event_type", EVTYPE_OTHER)),
            injury_code    = int(  data.get("injury",     INJURY_UNKNOWN)),
            resource_flags = int(  data.get("resources",  0)),
            priority       = int(  data.get("priority",   PRIO_URGENT)),
            text_snippet   = snippet,
        )
        return FrameType.EMERG_BEACON, [payload]

    if t == "text":
        text = str(data.get("text", ""))
        dest = (str(data.get("to", "")) or "CQCQCQ").upper()
        # Langer Text → mehrere 0x40-Fragmente (werden back-to-back gesendet)
        payloads = fragment_text(text, dest_call=dest, seq_nr=0)
        return FrameType.TEXT, payloads

    if t == "text_fragment":
        # EIN vorberechnetes 0x40-Fragment (Schedule-getaktet vom Web-UI).
        # Es wird NICHT erneut fragmentiert — Index/Total/Seq kommen vom Client,
        # sodass der Empfänger die Teile später wieder zusammensetzen kann.
        from gust_frame import encode_text_fragment
        chunk = str(data.get("text_chunk", ""))
        dest  = (str(data.get("to", "")) or "CQCQCQ").upper()
        seq   = int(data.get("seq_nr",     0)) & 0xFF
        idx   = int(data.get("frag_index", 0))
        total = max(1, int(data.get("frag_total", 1)))
        payload = encode_text_fragment(dest, chunk, seq, idx, total)
        return FrameType.TEXT, [payload]

    raise ValueError(f"Unbekannter Frame-Typ: {frame_type_str!r}")


# ═══════════════════════════════════════════════════════════════════════
# TX-GATEWAY
# ═══════════════════════════════════════════════════════════════════════

class TxGateway:
    """
    Prioritäts-gesteuertes Sende-Gateway (ein Worker, ein Sender).

    Args:
        cfg:        Vollständige Konfiguration (gateway.json + Defaults).
        event_bus:  Optional gust_eventbus.EventBus — für tx_done-Events.
        dry_run:    True → Frames werden angenommen und geloggt, aber NICHT
                    gesendet (entspricht 'daemon --dry-run', kein TX/Audio).
    """

    def __init__(self, cfg: dict, event_bus=None, dry_run: bool = False):
        self._cfg        = cfg
        self._event_bus  = event_bus
        self._dry_run    = dry_run

        self._callsign   = cfg.get("callsign", "OE3GAS")
        self._audio_cfg  = cfg.get("audio", {})

        gw_cfg           = cfg.get("gateway", {})
        self._min_tx_gap = float(gw_cfg.get("min_tx_gap_s", 10))

        # Stations-Slot (deterministisch, wie in gust_frame.assign_channel):
        # Sendezyklus interval_s, Zeitversatz offset = (SHA-256 >> 8) % interval.
        # Slot-Zeitpunkte: alle t mit (unixzeit mod interval) == offset.
        self._interval_s = int(gw_cfg.get("interval_s", 300))
        h = int(hashlib.sha256(self._callsign.upper().encode()).hexdigest(), 16)
        self._tx_offset  = (h >> 8) % self._interval_s

        # Sendepegel aus gateway.json: Wert > 1 wird als Prozent interpretiert.
        from gust_audio import AUDIO_LEVEL
        raw_level = self._audio_cfg.get("level", AUDIO_LEVEL * 100)
        self._level = (max(0.01, min(1.0, raw_level / 100.0))
                       if raw_level > 1.0 else float(raw_level))

        # Warteschlange: Liste von Items
        #   {"priority": int, "seq": int, "frame": dict, "eligible": mono_time}
        # 'eligible' = früheste Sendezeit (monotonic): Slot für P3/P4, jetzt für
        # P1/P2. Die Auswahl des nächsten Frames erfolgt nach
        # (eligible, priority, seq) — frühere Fälligkeit und höhere Priorität
        # zuerst, seq als FIFO-Tiebreaker.
        self._pending: list = []
        self._seq   = itertools.count()
        # Wird bei jedem enqueue() gesetzt — weckt den Worker, damit ein neu
        # eingereihter (z.B. früher fälliger oder Notfall-)Frame sofort
        # berücksichtigt wird.
        self._wake  = asyncio.Event()

        self._last_tx_mono: Optional[float] = None   # für Cooldown (monotonic)
        self._last_tx_ts:   Optional[float] = None   # für Status (Unix-Zeit)
        self._busy       = False
        self._tx_count   = 0

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._worker_task: Optional[asyncio.Task] = None

        # Popen-Handle eines von GUST (über build_ptt im TX-Pfad) gestarteten
        # rigctld. Wird der Web-GUI über self._gateway zugänglich gemacht, damit
        # sie ihren eigenen rigctld als solchen erkennt (Port-Konflikt-Dialog).
        self._rigctld_proc = None

    # ── ÖFFENTLICHE SCHNITTSTELLE (für WebServer) ─────────────────────

    def enqueue(self, frame_dict: dict, priority: int = 4) -> None:
        """Frame einreihen und auf seinen Sendezeitpunkt terminieren.

        P3/P4 werden auf den nächsten Stations-Slot gelegt, P1/P2 sind sofort
        fällig. Aus dem asyncio-Loop aufrufen (WebServer-Handler)."""
        priority = int(priority)
        now = time.monotonic()
        if priority >= SLOT_MIN_PRIORITY:
            eligible = now + self._next_slot_delay()   # auf Stations-Slot legen
        else:
            eligible = now                             # Notfall / Freitext: sofort
        self._pending.append({
            "priority": priority,
            "seq":      next(self._seq),
            "frame":    frame_dict,
            "eligible": eligible,
        })
        self._wake.set()
        log.info("TX-Gateway: eingereiht — %s (Prio %d), fällig in %.0fs, "
                 "Queue-Tiefe %d", frame_dict.get("frame_type", "?"), priority,
                 max(0.0, eligible - now), len(self._pending))

    def get_status(self) -> dict:
        """Status-Felder für /api/status (wird in den WebServer-Status gemischt)."""
        return {
            "queue_depth": len(self._pending) + (1 if self._busy else 0),
            "last_tx":     self._last_tx_ts,
            "tx_count":    self._tx_count,
        }

    def get_queue(self) -> list:
        """
        Vorschau der ausstehenden Frames mit geschätztem Sendezeitpunkt.

        Für jeden wartenden Frame wird simuliert, wann er voraussichtlich
        gesendet wird — unter Berücksichtigung von Fälligkeit (Slot),
        Cooldown-Abstand und serieller Abarbeitung (back-to-back ab Slot).

        Rückgabe: Liste in Sende-Reihenfolge, je Frame:
            { frame_type, from, priority, eta_s, eta_ts }
        eta_s  = Sekunden bis zum voraussichtlichen Senden
        eta_ts = absoluter Unix-Zeitstempel (für lokalen Countdown im Browser)
        """
        now_mono = time.monotonic()
        now_wall = time.time()
        items = sorted(self._pending,
                       key=lambda it: (it["eligible"], it["priority"], it["seq"]))

        # sim_done = mono-Zeit, zu der der zuletzt (simuliert) gesendete Frame
        # fertig ist. Startwert: Ende des letzten echten TX bzw. ~jetzt falls
        # gerade gesendet wird.
        sim_done = self._last_tx_mono
        if self._busy:
            sim_done = max(sim_done or now_mono, now_mono)

        result = []
        for it in items:
            send_at = it["eligible"]
            if it["priority"] > EMERGENCY_PRIORITY and sim_done is not None:
                send_at = max(send_at, sim_done + self._min_tx_gap)
            if sim_done is not None:
                send_at = max(send_at, sim_done)   # seriell nach vorigem TX
            send_at = max(send_at, now_mono)
            eta_s = send_at - now_mono
            result.append({
                "frame_type": it["frame"].get("frame_type", "?"),
                "from":       it["frame"].get("from", self._callsign),
                "priority":   it["priority"],
                "eta_s":      round(eta_s, 1),
                "eta_ts":     round(now_wall + eta_s, 3),
            })
            sim_done = send_at + APPROX_TX_S
        return result

    def clear_queue(self) -> int:
        """Alle ausstehenden Frames aus der Warteschlange entfernen.
        Gibt die Anzahl der gelöschten Frames zurück.
        Frames die gerade gesendet werden (_busy=True) sind nicht betroffen.
        """
        n = len(self._pending)
        self._pending.clear()
        log.info("TX-Queue gelöscht (%d Frame(s) entfernt).", n)
        return n

    def _next_slot_delay(self) -> float:
        """Sekunden bis zum nächsten Stations-Slot ((unixzeit mod interval) == offset)."""
        iv = self._interval_s
        now_in_cycle = int(time.time()) % iv
        delta = self._tx_offset - now_in_cycle
        if delta <= 0:
            delta += iv
        return float(delta)

    # ── LEBENSZYKLUS ───────────────────────────────────────────────────

    async def start(self) -> None:
        """Worker-Task starten."""
        self._loop = asyncio.get_event_loop()
        self._worker_task = asyncio.create_task(self._worker(), name="tx_gateway")
        log.info("TX-Gateway gestartet  |  Cooldown: %.1fs  |  Pegel: %.0f%%  |  %s",
                 self._min_tx_gap, self._level * 100,
                 "DRY-RUN (kein TX)" if self._dry_run else "TX aktiv")

    async def stop(self) -> None:
        """Worker geordnet beenden."""
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        log.info("TX-Gateway gestoppt (gesendet: %d Frame(s)).", self._tx_count)

    # ── INTERNER WORKER ────────────────────────────────────────────────

    async def _worker(self) -> None:
        """
        Sende-Schleife: wählt den nächsten fälligen Frame und sendet ihn.

        Auswahl nach (eligible, priority, seq): der am frühesten fällige Frame
        zuerst, bei Gleichstand die höhere Priorität. Slot-gebundene Frames
        (P3/P4) werden bis zu ihrem Slot zurückgehalten; ab dem Slot werden
        wartende Frames seriell mit Cooldown-Abstand gesendet (back-to-back).
        Notfall (P1) überspringt den Cooldown.
        """
        while True:
            if not self._pending:
                self._wake.clear()
                await self._wake.wait()
                continue

            item = min(self._pending,
                       key=lambda it: (it["eligible"], it["priority"], it["seq"]))

            now = time.monotonic()
            send_at = item["eligible"]
            # Cooldown-Abstand nur für Nicht-Notfall-Frames.
            if item["priority"] > EMERGENCY_PRIORITY and self._last_tx_mono is not None:
                send_at = max(send_at, self._last_tx_mono + self._min_tx_gap)

            wait = send_at - now
            if wait > 0:
                # Unterbrechbar warten: ein neues enqueue() weckt uns sofort,
                # sodass z.B. ein Notfall vorgezogen wird. Der Schlaf wird auf
                # WORKER_POLL_S gedeckelt, damit selbst ein (sehr seltener)
                # verpasster Weckruf die Notfall-Reaktion auf ≤ 1 s begrenzt.
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(),
                                           timeout=min(wait, WORKER_POLL_S))
                except asyncio.TimeoutError:
                    pass
                continue

            # Fällig → senden
            self._pending.remove(item)
            self._busy = True
            try:
                await self._transmit(item["priority"], item["frame"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:   # noqa: BLE001 — Worker darf nie sterben
                log.error("TX-Gateway: Sendefehler (%s): %s",
                          item["frame"].get("frame_type", "?"), exc)
            finally:
                self._busy = False

    async def _transmit(self, priority: int, frame: dict) -> None:
        """Einen Frame kodieren und (sofern nicht dry-run) senden."""
        from gust_frame import FrameType

        frame_type_str = frame.get("frame_type", "weather")
        data           = frame.get("data", {}) or {}
        callsign       = (frame.get("from") or self._callsign).upper()

        # Payload kodieren (kann ValueError werfen → vom Worker geloggt)
        frame_type_int, payloads = _encode_payload(frame_type_str, data)

        # Notfall-Frames werden per Parallelkanal-Diversity (ADR-12) auf
        # ZWEI Kanälen gleichzeitig gesendet. dual_channels=None → Einzel-Kanal.
        dual_channels = (self._emergency_channels(callsign)
                         if frame_type_int == FrameType.EMERG_BEACON else None)

        if self._dry_run:
            if dual_channels is not None:
                log.info("TX-Gateway DRY-RUN: würde NOTFALL dual auf Kanal "
                         "%d+%d senden — kein TX.", *dual_channels)
            else:
                log.info("TX-Gateway DRY-RUN: würde %s senden (%d Frame[s]) "
                         "— kein TX.", frame_type_str, len(payloads))
            # Cooldown-Stempel auch im DRY-RUN setzen, damit die zeitliche
            # Abfolge (Slot + Back-to-back-Cooldown) realistisch simuliert wird.
            self._last_tx_mono = time.monotonic()
            self._last_tx_ts   = time.time()
            return

        t0 = time.monotonic()
        used = await self._loop.run_in_executor(
            None, self._blocking_transmit,
            frame_type_int, callsign, payloads, dual_channels)
        duration = time.monotonic() - t0

        # Cooldown-/Status-Stempel erst nach erfolgreichem TX setzen
        self._last_tx_mono = time.monotonic()
        self._last_tx_ts   = time.time()
        self._tx_count    += 1

        # used ist (ch_a, ch_b) bei Dual-Kanal, sonst ein einzelner Kanal-int
        if isinstance(used, tuple):
            primary_ch = used[0]
            chan_desc  = f"{used[0]}+{used[1]} (Dual-Kanal)"
        else:
            primary_ch = used
            chan_desc  = str(used)

        log.info("TX-Gateway: %s gesendet auf Kanal %s  (%.1fs, Prio %d)",
                 frame_type_str, chan_desc, duration, priority)

        # tx_done an den Event-Bus → Web-UI (/ws/rx) zeigt „TX abgeschlossen"
        if self._event_bus is not None:
            ev_frame = {
                "frame_type": frame_type_int,
                "type_name":  frame_type_str.upper(),
                "from":       callsign,
                "to":         data.get("to") or data.get("dest") or "*",
                "priority":   priority,
                "data":       data,
            }
            if isinstance(used, tuple):
                ev_frame["channels"] = list(used)   # beide Diversity-Kanäle
            await self._event_bus.publish(make_tx_done_event(
                ev_frame, channel=primary_ch, duration_s=duration))

    def _emergency_channels(self, callsign: str) -> tuple:
        """
        Die beiden Kanäle für einen Notfall-Frame (Parallelkanal-Diversity).

        Kanal A = Heimatkanal der Station (SHA-256-Zuweisung), Kanal B liegt
        deterministisch mit maximalem Abstand gegenüber (home + N/2 mod N,
        also 5 Kanäle bei 10 Kanälen) — gut gespreizt für QRM-Schutz und
        immer ein gültiger, verschiedener Kanal.
        """
        from gust_frame import assign_channel, N_CHANNELS
        home, _ = assign_channel(callsign)
        second  = (home + N_CHANNELS // 2) % N_CHANNELS
        return home, second

    def _blocking_transmit(self, frame_type_int: int, callsign: str,
                           payloads: list,
                           dual_channels: Optional[tuple] = None):
        """
        Blockierender TX-Aufruf — läuft im Thread-Executor.

        Baut PTT + AudioTransmitter und sendet:
          • dual_channels=(a, b)  → den Frame parallel auf zwei Kanälen
            (gemischtes NF-Signal, ein PTT-Zyklus). Rückgabe: (a, b).
          • sonst                 → alle Payloads des Frames nacheinander
            (Freitext-Fragmente back-to-back). Rückgabe: verwendeter Kanal.
        """
        from gust_audio import AudioTransmitter, build_ptt

        ptt = build_ptt(self._audio_cfg, self._cfg)
        # Hat build_ptt rigctld gestartet, Handle merken (None = lief bereits;
        # dann vorhandenes Handle nicht überschreiben).
        _proc = getattr(ptt, "_rigctld_proc", None)
        if _proc is not None:
            self._rigctld_proc = _proc
        used = None
        with AudioTransmitter(
            ptt    = ptt,
            device = self._audio_cfg.get("device"),
            level  = self._level,
        ) as tx:
            if dual_channels is not None:
                ch_a, ch_b = dual_channels
                used = tx.transmit_frame_dual(
                    frame_type_int, callsign, payloads[0],
                    channel_a = ch_a, channel_b = ch_b,
                    use_fec   = True,
                    window    = True,
                )
            else:
                for payload in payloads:
                    used = tx.transmit_frame(
                        frame_type_int, callsign, payload,
                        channel = None,    # automatisch aus SHA-256(callsign)
                        use_fec = True,
                        window  = True,    # Raised Cosine — On-Air-Betrieb
                    )
        return used
