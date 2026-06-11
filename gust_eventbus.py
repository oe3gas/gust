#!/usr/bin/env python3
"""
GUST — Event-Bus                                           Phase 5
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Version : 0.1.0
Datum   : Mai 2026

Inhalt dieses Moduls:
  • EventType       — Konstanten für alle Event-Typen
  • SubscriberQueue — asyncio.Queue mit TTL-Filterung und Statistik
  • EventBus        — Fan-out Publisher: ein Event → alle Subscriber
  • make_*_event()  — Hilfsfunktionen zum Erzeugen typgerechter Events

Architektur (ADR-02):
  Ein Publisher (z.B. RX-Decoder) veröffentlicht einen Event.
  Jeder Subscriber bekommt eine eigene SubscriberQueue.
  Langsame Subscriber verlieren Frames — kein Blocking des Publishers.
  Thread-sicheres Einliefern für MQTT/Meshtastic-Callbacks.

  Publisher:   RX-Decoder, TX-Scheduler, Status-Reporter
  Subscriber:  WebSocket-Handler, Logfile-Writer, MQTTBridge (optional)

Verwendungsbeispiel:
  bus = EventBus()

  # Subscriber registrieren
  ws_queue  = bus.subscribe("websocket", maxsize=64)
  log_queue = bus.subscribe("logfile",   maxsize=512, ttl_s=None)

  # Event veröffentlichen (aus asyncio-Kontext)
  await bus.publish(make_rx_frame_event(decoded_frame))

  # Event empfangen (im WebSocket-Handler)
  event = await ws_queue.get_event()   # blockiert bis Event da ist
  # oder mit Timeout:
  event = await ws_queue.get_event(timeout=5.0)

  # Subscriber austragen (beim Shutdown)
  bus.unsubscribe(ws_queue)

Thread-sicheres Einliefern (aus paho-MQTT / Meshtastic-Callbacks):
  bus.publish_threadsafe(event, loop=asyncio.get_event_loop())

Statistik:
  bus.stats()   → {"published": N, "dropped": N, "subscribers": [...]}
"""

import asyncio
import logging
import time
import threading
from typing import Optional

log = logging.getLogger("gust.eventbus")


# ═══════════════════════════════════════════════════════════════════════
# EVENT-TYPEN
# ═══════════════════════════════════════════════════════════════════════

class EventType:
    """
    Alle definierten Event-Typen im GUST-System.
    Wert ist immer ein String → direkt im JSON-Serializer verwendbar.
    """
    RX_FRAME       = "rx_frame"        # Dekodierter Empfangsframe vom RX-Decoder
    RX_AUDIO_LEVEL = "rx_audio_level"  # RMS/Peak des RX-Audios (Diagnose-Pegelmeter)
    TX_DONE        = "tx_done"         # Gesendeter Frame wurde übertragen
    STATUS         = "status"          # Periodischer System-Status (alle 60 s)
    LOG            = "log"             # Systemlog-Eintrag (Level + Nachricht)

    # Zukünftige Typen (Phase 6+)
    MQTT_RX  = "mqtt_rx"    # Frame via MQTT eingelangt (MQTTBridge)
    CONFIG   = "config"     # Konfiguration wurde geändert


# ═══════════════════════════════════════════════════════════════════════
# SUBSCRIBER-QUEUE
# ═══════════════════════════════════════════════════════════════════════

class SubscriberQueue(asyncio.Queue):
    """
    asyncio.Queue-Erweiterung für den GUST Event-Bus.

    Zusätzliche Funktionen gegenüber asyncio.Queue:
      • get_event()   — Blockierendes Lesen mit TTL-Filterung
      • ttl_s         — Events älter als ttl_s Sekunden werden übersprungen
                        None = kein TTL (niemals verwerfen)
      • stats()       — Statistik: Empfangen, Verworfen (voll + abgelaufen)

    TTL-Mechanismus:
      Jeder Event trägt ein ts_mono-Feld (time.monotonic() bei publish()).
      Beim Lesen wird geprüft: age = now_mono - event["ts_mono"]
      Ist age > ttl_s, wird der Event übersprungen (kein Blocking).
      Anwendungsfall: WebSocket-Subscriber fällt zurück — alte Frames
      haben keinen Wert mehr für die Echtzeit-Anzeige.

    Dropped-Zähler:
      _dropped_full  — Verworfen weil Queue voll war (beim put_nowait)
      _dropped_ttl   — Verworfen weil Event abgelaufen (beim get_event)
    """

    def __init__(self, name: str, maxsize: int = 64,
                 ttl_s: Optional[float] = 30.0):
        """
        Args:
            name:     Bezeichner für Logging und Statistik
            maxsize:  Maximale Queue-Tiefe (Standard: 64 Frames)
            ttl_s:    Time-to-live in Sekunden, None = unbegrenzt
        """
        super().__init__(maxsize=maxsize)
        self.name   = name
        self.ttl_s  = ttl_s

        self._received:     int = 0   # Erfolgreich von Subscriber gelesen
        self._dropped_full: int = 0   # Verworfen: Queue war voll
        self._dropped_ttl:  int = 0   # Verworfen: TTL abgelaufen

    async def get_event(self,
                        timeout: Optional[float] = None) -> Optional[dict]:
        """
        Nächstes gültiges Event lesen — mit TTL-Filterung.

        Args:
            timeout: Maximale Wartezeit in Sekunden.
                     None = unbegrenzt warten.
                     Bei Ablauf: asyncio.TimeoutError wird weitergegeben.

        Returns:
            Event-Dict (nie None, da TimeoutError bei Timeout).

        Raises:
            asyncio.TimeoutError: wenn timeout abläuft ohne gültiges Event.
        """
        deadline = (time.monotonic() + timeout) if timeout is not None else None

        while True:
            # Verbleibende Zeit bis Deadline berechnen
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError(
                        f"SubscriberQueue '{self.name}': Timeout nach {timeout}s"
                    )
                event = await asyncio.wait_for(super().get(), timeout=remaining)
            else:
                event = await super().get()

            # TTL-Prüfung
            if self.ttl_s is not None:
                age = time.monotonic() - event.get("ts_mono", time.monotonic())
                if age > self.ttl_s:
                    self._dropped_ttl += 1
                    log.debug("TTL-Drop in '%s': %.1f s alt (TTL: %.1f s)",
                              self.name, age, self.ttl_s)
                    continue   # nächstes Event versuchen

            self._received += 1
            return event

    def stats(self) -> dict:
        """Statistik-Snapshot dieser Queue."""
        total_dropped = self._dropped_full + self._dropped_ttl
        return {
            "name":          self.name,
            "qsize":         self.qsize(),
            "maxsize":       self.maxsize,
            "ttl_s":         self.ttl_s,
            "received":      self._received,
            "dropped_full":  self._dropped_full,
            "dropped_ttl":   self._dropped_ttl,
            "dropped_total": total_dropped,
        }

    def __repr__(self) -> str:
        s = self.stats()
        return (f"SubscriberQueue(name='{self.name}', "
                f"qsize={s['qsize']}/{s['maxsize']}, "
                f"received={s['received']}, dropped={s['dropped_total']})")


# ═══════════════════════════════════════════════════════════════════════
# EVENT-BUS
# ═══════════════════════════════════════════════════════════════════════

class EventBus:
    """
    Interner asyncio Fan-out Event-Bus für GUST.

    Ein Event geht an alle registrierten Subscriber gleichzeitig.
    Langsame Subscriber blockieren den Publisher nicht — volle Queues
    führen zum Verwerfen des Events für diesen Subscriber.

    Thread-Safety:
      Die Subscriber-Liste wird mit einem threading.Lock geschützt.
      publish() darf nur aus dem asyncio-Event-Loop aufgerufen werden.
      publish_threadsafe() darf aus beliebigen Threads aufgerufen werden.

    Lebenszyklus:
      bus = EventBus()
      q   = bus.subscribe("ws_handler")
      await bus.publish(event)
      bus.unsubscribe(q)
      bus.reset_stats()
    """

    def __init__(self):
        # threading.Lock statt asyncio.Lock — wird auch aus Threads aufgerufen
        self._lock: threading.Lock   = threading.Lock()
        self._subscribers: list[SubscriberQueue] = []

        # Statistik
        self._published:     int = 0
        self._total_dropped: int = 0

    # ── SUBSCRIBER MANAGEMENT ─────────────────────────────────────────

    def subscribe(self, name: Optional[str] = None,
                  maxsize: int = 64,
                  ttl_s: Optional[float] = 30.0) -> SubscriberQueue:
        """
        Neuen Subscriber registrieren.

        Args:
            name:    Bezeichner (für Logging/Debugging). Auto-generiert wenn None.
            maxsize: Queue-Tiefe (Standard: 64 Frames).
                     Erhöhen für Subscriber die keine Frames verlieren dürfen
                     (z.B. Logfile-Writer: maxsize=512, ttl_s=None).
            ttl_s:   TTL in Sekunden (Standard: 30 s).
                     None für kritische Subscriber ohne Ablaufzeit.

        Returns:
            SubscriberQueue — die Queue dieses Subscribers.

        Beispiele:
            ws_q  = bus.subscribe("websocket_rx",  maxsize=64,  ttl_s=30.0)
            log_q = bus.subscribe("logfile_writer", maxsize=512, ttl_s=None)
            mqtt_q= bus.subscribe("mqtt_bridge",    maxsize=128, ttl_s=60.0)
        """
        with self._lock:
            auto_name = name or f"subscriber_{len(self._subscribers) + 1}"
            q = SubscriberQueue(auto_name, maxsize=maxsize, ttl_s=ttl_s)
            self._subscribers.append(q)
        log.info("EventBus: Subscriber '%s' registriert (maxsize=%d, ttl=%s s)",
                 auto_name, maxsize, ttl_s)
        return q

    def unsubscribe(self, queue: SubscriberQueue) -> None:
        """
        Subscriber wieder austragen.

        Sicher bei doppeltem Aufruf (kein Fehler wenn nicht vorhanden).
        Wird beim Shutdown oder beim Trennen einer WebSocket-Verbindung
        aufgerufen.
        """
        with self._lock:
            try:
                self._subscribers.remove(queue)
                log.info("EventBus: Subscriber '%s' ausgetragen (stats: %s)",
                         queue.name, queue.stats())
            except ValueError:
                log.debug("EventBus: unsubscribe('%s') — nicht gefunden.",
                          queue.name)

    @property
    def subscriber_count(self) -> int:
        """Anzahl der aktuell registrierten Subscriber."""
        with self._lock:
            return len(self._subscribers)

    # ── PUBLISH ───────────────────────────────────────────────────────

    async def publish(self, event: dict) -> None:
        """
        Event an alle Subscriber veröffentlichen (asyncio-Kontext).

        Das Event wird mit ts (Unix-Zeit) und ts_mono (Monotonic-Zeit)
        ergänzt, falls diese Felder fehlen. Monotonic wird für die
        TTL-Berechnung genutzt.

        Wichtig: Diese Methode ist non-blocking — ein voller Subscriber
        verliert den Frame (QueueFull), blockiert aber nicht.

        Args:
            event: Dict mit mindestens {"type": str, "data": dict}.

        Raises:
            Keine — Fehler werden geloggt, nie propagiert.
        """
        # Zeitstempel eintragen (nicht überschreiben falls schon gesetzt)
        now = time.time()
        now_mono = time.monotonic()
        event.setdefault("ts",      now)
        event.setdefault("ts_mono", now_mono)

        with self._lock:
            subscribers = list(self._subscribers)   # Snapshot für Lock-freiheit

        self._published += 1
        dropped_this = 0

        for q in subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                q._dropped_full += 1
                dropped_this    += 1
                self._total_dropped += 1
                log.debug("EventBus: QueueFull für Subscriber '%s' "
                          "(type=%s) — Frame verworfen.",
                          q.name, event.get("type", "?"))

        if dropped_this:
            log.warning("EventBus: %d Subscriber(s) haben Frame verworfen "
                        "(type=%s, total_dropped=%d).",
                        dropped_this, event.get("type", "?"),
                        self._total_dropped)

    def publish_threadsafe(self, event: dict,
                           loop: asyncio.AbstractEventLoop) -> None:
        """
        Thread-sicheres Veröffentlichen aus einem Fremd-Thread.

        Wird von MQTT-Callbacks (paho läuft in eigenem Thread) und
        Meshtastic-Callbacks aufgerufen.

        Args:
            event: Event-Dict (wie bei publish()).
            loop:  Der laufende asyncio-Event-Loop des Hauptprogramms.
                   Beschaffen via: asyncio.get_event_loop() im Hauptthread.

        Beispiel (paho MQTT on_message):
            def on_message(client, userdata, msg):
                event = make_log_event("INFO", f"MQTT: {msg.topic}")
                bus.publish_threadsafe(event, loop)
        """
        asyncio.run_coroutine_threadsafe(self.publish(event), loop)

    # ── STATISTIK ─────────────────────────────────────────────────────

    def stats(self) -> dict:
        """
        Statistik-Snapshot des gesamten Event-Bus.

        Returns:
            Dict mit Gesamtstatistik und Liste der Subscriber-Stats.
        """
        with self._lock:
            sub_stats = [q.stats() for q in self._subscribers]

        return {
            "published":     self._published,
            "total_dropped": self._total_dropped,
            "subscriber_count": len(sub_stats),
            "subscribers":   sub_stats,
        }

    def reset_stats(self) -> None:
        """Statistik-Zähler zurücksetzen (für Tests / Betriebsübergabe)."""
        with self._lock:
            self._published     = 0
            self._total_dropped = 0
            for q in self._subscribers:
                q._received     = 0
                q._dropped_full = 0
                q._dropped_ttl  = 0

    def __repr__(self) -> str:
        s = self.stats()
        return (f"EventBus(subscribers={s['subscriber_count']}, "
                f"published={s['published']}, "
                f"dropped={s['total_dropped']})")


# ═══════════════════════════════════════════════════════════════════════
# HILFSFUNKTIONEN — Event-Konstruktoren
# ═══════════════════════════════════════════════════════════════════════

def make_rx_frame_event(frame: dict,
                        channel: Optional[int] = None) -> dict:
    """
    Event für einen dekodiert empfangenen Frame.

    Args:
        frame:   Dekodierter Frame als Dict (aus gust_frame.parse_frame())
        channel: Empfangskanal (0–9), falls nicht schon in frame enthalten.

    Event-Struktur:
        {
          "type":    "rx_frame",
          "ts":      float,            # Unix-Zeitstempel
          "ts_mono": float,            # Monotonic (für TTL)
          "data": {
              "frame_type":  int,      # 0x01, 0x02, ...
              "type_name":   str,      # "WEATHER", "POSITION", ...
              "from":        str,      # Rufzeichen
              "channel":     int,      # Kanal 0–9
              "snr_db":      float,    # optional
              "rs_errors":   int,      # optional
              "data":        dict,     # decodierte Nutzdaten
          }
        }
    """
    data = dict(frame)
    if channel is not None and "channel" not in data:
        data["channel"] = channel
    # Interne RX-Diagnosefelder nicht auf den Bus legen: _raw_frame_body
    # sind rohe bytes (für die AUTH-HMAC-Verifikation in gust_rx._verify_auth)
    # und nicht JSON-serialisierbar — sie würden den WebSocket-Broadcast,
    # /api/log und den Session-Export sprengen.
    data.pop("_raw_frame_body", None)
    return {
        "type": EventType.RX_FRAME,
        "data": data,
    }


def make_tx_done_event(frame: dict,
                       channel: Optional[int] = None,
                       duration_s: Optional[float] = None) -> dict:
    """
    Event nach erfolgreicher Übertragung eines Frames.

    Args:
        frame:      Gesendeter Frame (aus TX-Queue)
        channel:    Verwendeter Kanal
        duration_s: Sendezeit in Sekunden
    """
    data = dict(frame)
    if channel is not None:
        data["channel"] = channel
    if duration_s is not None:
        data["duration_s"] = round(duration_s, 3)
    return {
        "type": EventType.TX_DONE,
        "data": data,
    }


def make_audio_level_event(rms: float, peak: float,
                            device: Optional[str] = None) -> dict:
    """
    Diagnose-Event für den RX-Audiopegel.

    Wird periodisch (typisch alle 250 ms) vom AudioRXLoop publiziert,
    damit das Web-UI sehen kann ob überhaupt Audio vom Eingang kommt.
    Ohne dieses Signal dekodiert GUST entweder Stille oder Fremdsignale
    auf dem falschen Gerät.

    Args:
        rms:    Quadratischer Mittelwert des Audio-Slices (0.0–1.0)
        peak:   Spitzenwert |max| (0.0–1.0); >0.98 = Clipping
        device: Optionale Gerätekennung (für UI-Anzeige)

    dB-Werte (20·log10) werden hier mitberechnet, damit Empfänger sie
    nicht selbst umrechnen müssen. -100 dB als Floor für Stille.
    """
    import math
    def _to_db(x: float) -> float:
        return round(20.0 * math.log10(x), 1) if x > 1e-5 else -100.0

    data = {
        "rms":      round(float(rms),  6),
        "peak":     round(float(peak), 6),
        "rms_db":   _to_db(rms),
        "peak_db":  _to_db(peak),
        "clipping": bool(peak > 0.98),
    }
    if device is not None:
        data["device"] = device
    return {
        "type": EventType.RX_AUDIO_LEVEL,
        "data": data,
    }


def make_status_event(callsign: str,
                      uptime_s: float,
                      queue_depth: int = 0,
                      home_channel: Optional[int] = None,
                      **extra) -> dict:
    """
    Periodischer System-Status-Event (typisch alle 60 s).

    Args:
        callsign:     Eigenes Rufzeichen
        uptime_s:     Betriebszeit in Sekunden
        queue_depth:  TX-Queue-Füllstand
        home_channel: Heimatkanal (0–9)
        **extra:      Weitere Felder (audio_device, ptt_backend, etc.)
    """
    data = {
        "callsign":     callsign,
        "uptime_s":     round(uptime_s, 1),
        "queue_depth":  queue_depth,
        "home_channel": home_channel,
        **extra,
    }
    return {
        "type": EventType.STATUS,
        "data": data,
    }


def make_log_event(level: str, message: str,
                   module: Optional[str] = None) -> dict:
    """
    Systemlog-Eintrag als Event (für /ws/log WebSocket-Stream).

    Args:
        level:   "DEBUG", "INFO", "WARNING", "ERROR"
        message: Log-Text
        module:  Quellenmodul (optional, z.B. "gateway", "rx_decoder")
    """
    data: dict = {"level": level.upper(), "msg": message}
    if module:
        data["module"] = module
    return {
        "type": EventType.LOG,
        "data": data,
    }


# ═══════════════════════════════════════════════════════════════════════
# LOGGING-HANDLER — Python-Logging → Event-Bus
# ═══════════════════════════════════════════════════════════════════════

class EventBusLogHandler(logging.Handler):
    """
    Python-Logging-Handler der Log-Einträge als Events in den Bus einspeist.

    Einrichten:
        bus = EventBus()
        handler = EventBusLogHandler(bus)
        handler.setLevel(logging.INFO)
        logging.getLogger("gust").addHandler(handler)

    Dann landen alle INFO+-Logs auch in den /ws/log WebSocket-Streams.
    """

    def __init__(self, bus: EventBus):
        super().__init__()
        self._bus  = bus
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Event-Loop setzen (nach asyncio.run() / loop.run_forever())."""
        self._loop = loop

    def emit(self, record: logging.LogRecord) -> None:
        """Wird von Python-Logging aufgerufen — thread-safe."""
        if self._loop is None or self._loop.is_closed():
            return
        try:
            event = make_log_event(
                level   = record.levelname,
                message = self.format(record),
                module  = record.name,
            )
            self._bus.publish_threadsafe(event, self._loop)
        except Exception:
            self.handleError(record)


# ═══════════════════════════════════════════════════════════════════════
# STANDALONE SELBSTTEST
# ═══════════════════════════════════════════════════════════════════════

async def _run_tests() -> None:
    """Schnelle Selbsttests — decken Testplan T-5.1 und T-5.2 ab."""
    import sys

    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        sym = "✅" if cond else "❌"
        print(f"  {sym}  {name}")
        if not cond:
            ok = False

    print("\nGUST EventBus — Selbsttest\n" + "─" * 40)

    # ── T-5.1: Fan-out an mehrere Subscriber ──────────────────────────
    print("\nT-5.1  Fan-out")
    bus = EventBus()
    q1  = bus.subscribe("sub_a", maxsize=10, ttl_s=None)
    q2  = bus.subscribe("sub_b", maxsize=10, ttl_s=None)

    event = {"type": EventType.RX_FRAME, "data": {"value": 42}}
    await bus.publish(event)

    e1 = await q1.get_event()
    e2 = await q2.get_event()
    check("Beide Subscriber erhalten den Event",
          e1["data"]["value"] == 42 and e2["data"]["value"] == 42)
    check("Events sind identisch", e1["data"] == e2["data"])
    check("ts wurde automatisch gesetzt", "ts" in e1 and "ts_mono" in e1)
    check("subscriber_count == 2", bus.subscriber_count == 2)

    # ── T-5.2: Volle Queue blockiert nicht ───────────────────────────
    print("\nT-5.2  QueueFull — non-blocking")
    bus2 = EventBus()
    q_slow = bus2.subscribe("slow",  maxsize=8,  ttl_s=None)
    q_fast = bus2.subscribe("fast",  maxsize=64, ttl_s=None)

    for i in range(12):
        await bus2.publish({"type": "test", "data": {"i": i}})

    check("Schneller Subscriber hat 12 Events", q_fast.qsize() == 12)
    check("Langsamer Subscriber hat maxsize=8", q_slow.qsize() == 8)
    check("dropped_full für slow == 4", q_slow._dropped_full == 4)
    check("bus2 zählt total_dropped == 4", bus2.stats()["total_dropped"] == 4)
    check("publisher wurde nicht blockiert", True)   # hätte sonst nicht weitergemacht

    # ── T-5.3: TTL-Filterung ─────────────────────────────────────────
    print("\nT-5.3  TTL-Filterung")
    bus3 = EventBus()
    q_ttl = bus3.subscribe("ttl_q", maxsize=10, ttl_s=5.0)

    # Altes Event manuell erzeugen (ts_mono 20 s in der Vergangenheit)
    old_event = {
        "type":    "test",
        "data":    {"msg": "veraltet"},
        "ts":      time.time() - 20,
        "ts_mono": time.monotonic() - 20,
    }
    q_ttl.put_nowait(old_event)

    # Frisches Event
    fresh_event = {
        "type":    "test",
        "data":    {"msg": "frisch"},
        "ts":      time.time(),
        "ts_mono": time.monotonic(),
    }
    q_ttl.put_nowait(fresh_event)

    result = await asyncio.wait_for(q_ttl.get_event(), timeout=1.0)
    check("Veraltetes Event übersprungen", result["data"]["msg"] == "frisch")
    check("dropped_ttl == 1", q_ttl._dropped_ttl == 1)

    # ── T-5.4: unsubscribe ────────────────────────────────────────────
    print("\nT-5.4  Unsubscribe")
    bus4 = EventBus()
    q_tmp = bus4.subscribe("temp")
    check("Subscriber registriert", bus4.subscriber_count == 1)
    bus4.unsubscribe(q_tmp)
    check("Subscriber ausgetragen", bus4.subscriber_count == 0)
    # Doppeltes unsubscribe darf nicht fehlerwerfen
    bus4.unsubscribe(q_tmp)
    check("Doppeltes unsubscribe kein Fehler", True)

    # ── T-5.5: make_*_event Hilfsfunktionen ──────────────────────────
    print("\nT-5.5  Event-Konstruktoren")
    rx  = make_rx_frame_event({"frame_type": 0x01, "from": "OE3GAS"}, channel=2)
    tx  = make_tx_done_event({"frame_type": 0x02}, channel=3, duration_s=1.92)
    st  = make_status_event("OE3GAS", 3600.0, queue_depth=2, home_channel=2)
    lg  = make_log_event("WARNING", "Testwarnung", module="test")

    check("rx_frame type korrekt",  rx["type"] == EventType.RX_FRAME)
    check("tx_done duration",       tx["data"]["duration_s"] == 1.92)
    check("status callsign",        st["data"]["callsign"] == "OE3GAS")
    check("log level",              lg["data"]["level"] == "WARNING")

    # ── T-5.6: Stats und reset ────────────────────────────────────────
    print("\nT-5.6  Statistik")
    stats = bus2.stats()
    check("stats.published > 0",    stats["published"] > 0)
    check("stats.subscribers vorhanden", len(stats["subscribers"]) == 2)
    bus2.reset_stats()
    check("Nach reset: published == 0", bus2.stats()["published"] == 0)

    # ── T-5.7: EventBusLogHandler ─────────────────────────────────────
    print("\nT-5.7  EventBusLogHandler")
    bus5 = EventBus()
    q_log = bus5.subscribe("log_q", maxsize=32, ttl_s=None)
    handler = EventBusLogHandler(bus5)
    handler.set_loop(asyncio.get_event_loop())
    test_logger = logging.getLogger("gust.test_handler")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)
    test_logger.info("Testmeldung über Handler")
    await asyncio.sleep(0.05)   # kurz warten damit der Thread einliefert
    check("Log-Eintrag via Handler in Queue", q_log.qsize() > 0)

    # ── ERGEBNIS ─────────────────────────────────────────────────────
    print("\n" + "─" * 40)
    if ok:
        print("✅  Alle Tests bestanden.\n")
    else:
        print("❌  Einige Tests fehlgeschlagen — Details oben.\n")
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s  %(levelname)-7s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(_run_tests())