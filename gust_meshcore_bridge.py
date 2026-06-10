"""
P6-19  GUST MeshCore Bridge
============================
Verbindet MeshCore Companion-Node (USB-Serial) mit dem GUST-Daemon.

Eingehende MeshCore Channel-Messages werden als synthetische RX_FRAME-Events
auf den GUST-EventBus publiziert — sichtbar im WebGUI ohne HF-Hardware.

Konfiguration: meshcore.json
Verwendung:
    py gust_meshcore_bridge.py
    py gust_meshcore_bridge.py --config meshcore.json --verbose

TX-Richtung (GUST → MeshCore): zurückgestellt, siehe P6-20.

──────────────────────────────────────────────────────────────────────────
Abweichungen von der ursprünglichen P6-19-Skizze (nach Verifikation der
echten APIs in gust_frame.py / gust_eventbus.py und der meshcore-Library):

  • TYPE_TEXT existiert nicht → gust_frame.FrameType.TEXT (0x40) wird genutzt.
  • Fragmentierung: lokales UTF-8-sicheres fragment_text() (BUG-MC-03) chunkt
    auf Byte-Grenzen; die Chunks werden per encode_text_fragment() in GUST-
    0x40-Payloads verpackt. gust_frame.fragment_text chunkt nach Zeichen und
    trunkiert dann auf 14 Byte → zerreißt Emoji/Multibyte (deshalb nicht genutzt).
  • RX_FRAME-Events haben im GUST-EventBus die Form {"type":"rx_frame",
    "data": <Frame-Dict>} und werden im Web-Server via json.dumps serialisiert.
    Deshalb publizieren wir KEINE rohen bytes, sondern ein JSON-fähiges
    Frame-Dict mit "payload_decoded" — exakt das Format, das gust_rx.py /
    gust_modulator.py erzeugen und das die Web-UI rendert (frame.payload_decoded).
    Erzeugt über das vorhandene gust_eventbus.make_rx_frame_event().
  • MeshCore Channel-Messages tragen KEINE Sender-Identität (kein pubkey_prefix
    im Wire-Format — nur channel_idx, sender_timestamp, text). Die Auflösung
    fällt daher auf Rufzeichen-Extraktion aus dem Text bzw. auf das eigene
    Gateway-Rufzeichen zurück (siehe resolve_sender / _process_channel_msg).
──────────────────────────────────────────────────────────────────────────
"""

import asyncio
import argparse
import collections
import json
import logging
import re
import sys
from pathlib import Path

# ── Import-Check meshcore ─────────────────────────────────────────────
try:
    from meshcore import MeshCore, EventType as MCEventType
except ImportError:
    print("FEHLER: meshcore-Library nicht installiert. pip install meshcore")
    sys.exit(1)

# ── GUST-Imports ──────────────────────────────────────────────────────
# Hinweis: TYPE_TEXT gibt es nicht — der Frame-Typ steckt in FrameType.TEXT.
try:
    from gust_eventbus import EventBus, EventType as GustEventType, make_rx_frame_event
    from gust_frame import (
        FrameType,
        frame_type_name,
        encode_text_fragment,
        decode_text_fragment,
    )
except ImportError as e:
    print(f"FEHLER: GUST-Module nicht gefunden: {e}")
    print("       Bridge muss im GUST-Projektverzeichnis gestartet werden.")
    sys.exit(1)

TYPE_TEXT = FrameType.TEXT   # 0x40 — Alias für Lesbarkeit

log = logging.getLogger("meshcore_bridge")

# ── Konstanten ────────────────────────────────────────────────────────
RECONNECT_DELAY_S = 30
MAX_CHANNEL_SLOTS = 20
MESHCORE_JSON_DEFAULT = "meshcore.json"
DEDUP_HISTORY = 512          # zuletzt gesehene Channel-Messages (Refetch-Schutz)


# ═══════════════════════════════════════════════════════════════════════
# Hilfsfunktionen
# ═══════════════════════════════════════════════════════════════════════

def load_config(path: str) -> dict:
    """Liest meshcore.json und gibt das Dict zurück."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"meshcore.json nicht gefunden: {p.absolute()}\n"
            "  → Vorlage: meshcore.json.example kopieren und anpassen."
        )
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def extract_callsign(text: str) -> str:
    """
    Versucht ein Amateurfunk-Rufzeichen aus einem String zu extrahieren.
    Beispiele:
        "AT-HL-OE3GAS-🦚"   → "OE3GAS"   (Node-Name)
        "OE1XTU: Hallo"     → "OE1XTU"   (Nachrichten-Präfix)
        "Hallo zusammen"    → ""          (kein Rufzeichen erkennbar)

    Gibt "" zurück wenn kein Rufzeichen-Muster gefunden wird — der Aufrufer
    entscheidet dann über einen Fallback (anders als eine naive Kürzung,
    die aus "Hallo" ein Pseudo-Rufzeichen machen würde).
    """
    m = re.search(r'([A-Z]{1,2}[0-9][A-Z]{1,4})', text.upper())
    return m.group(1) if m else ""


def resolve_sender(pubkey_prefix: str, text: str, contacts: list,
                   own_callsign: str) -> str:
    """
    Bestimmt das GUST-FROM-Rufzeichen für eine eingehende MeshCore-Message.

    Auflösungsreihenfolge:
      1. pubkey_prefix → bekannter Kontakt aus meshcore.json (falls vorhanden;
         Channel-Messages tragen i.d.R. KEINEN pubkey_prefix)
      2. Rufzeichen aus dem Nachrichtentext extrahieren
      3. eigenes Gateway-Rufzeichen (OE3GAS relayt die Mesh-Nachricht auf HF)
    """
    if pubkey_prefix:
        for c in contacts:
            if c.get("pubkey_prefix", "").lower() == pubkey_prefix.lower():
                cs = c.get("callsign", "")
                if cs:
                    return cs.upper()[:6]
    guess = extract_callsign(text)
    if guess:
        return guess[:6]
    return (own_callsign or "OE3GAS").upper()[:6]


def fragment_text(text: str, chunk_size: int = 14) -> list[bytes]:
    """
    Zerlegt Text in Chunks à chunk_size Bytes, schneidet aber immer
    auf UTF-8-Zeichengrenzen — kein Emoji/Multibyte-Zeichen wird zerrissen.
    MeshCore-Limit: 130 Byte. GUST-Frame-Payload-Limit: 14 Byte Text.
    BUG-MC-03 Fix.

    Ersetzt gust_frame.fragment_text (chunkt nach Zeichen + trunkiert auf
    14 Byte → zerreißt Multibyte). Die Byte-Chunks werden im Aufrufer per
    encode_text_fragment() in GUST-0x40-Payloads verpackt.
    """
    encoded = text.encode("utf-8")
    chunks = []
    i = 0
    while i < len(encoded):
        end = i + chunk_size
        if end >= len(encoded):
            chunks.append(encoded[i:])
            break
        # Zurück bis zur nächsten UTF-8-Zeichengrenze
        # Continuation-Bytes haben Muster 10xxxxxx (0x80–0xBF)
        while end > i and (encoded[end] & 0xC0) == 0x80:
            end -= 1
        chunks.append(encoded[i:end])
        i = end
    return chunks if chunks else [b""]


# ═══════════════════════════════════════════════════════════════════════
# MeshCore Bridge
# ═══════════════════════════════════════════════════════════════════════

class MeshCoreBridge:
    """
    Verbindet MeshCore Companion (USB) mit dem GUST-EventBus.
    Inbound only (P6-19). TX kommt in P6-20.
    """

    def __init__(self, config: dict, bus: EventBus):
        self.cfg      = config
        self.bus      = bus
        self.mc: MeshCore | None = None
        self._running = False

        # Konfiguration auslesen
        conn              = config.get("connection", {})
        self.port         = conn.get("port", "COM18")
        self.baudrate     = conn.get("baudrate", 115200)
        self.auto_reconnect = conn.get("auto_reconnect", True)
        self.reconnect_delay = conn.get("reconnect_delay_s", RECONNECT_DELAY_S)

        bridge            = config.get("bridge", {})
        self.fetch_interval = bridge.get("auto_fetch_interval_s", 5)
        self.fwd_public   = bridge.get("forward_public_channel", False)
        self.unknown_policy = bridge.get("unknown_sender_policy", "pubkey_prefix")

        self.contacts     = config.get("contacts", {}).get("known", [])
        self.own_callsign = config.get("node", {}).get("callsign", "OE3GAS")
        self.own_pubkey   = config.get("node", {}).get("pubkey_prefix", "")
        self.cfg_firmware = config.get("node", {}).get("firmware", "–")

        # Kanal-Index → Slot-Dict aus meshcore.json (Name + gust_forward)
        self.channel_map: dict[int, dict] = {}
        for slot in config.get("channels", {}).get("slots", []):
            idx = slot.get("index")
            if idx is not None:
                self.channel_map[idx] = dict(slot)

        # Laufzeit-Status
        self._seq = 0                                    # GUST-Text-Sequenznummer
        self._seen: set = set()                          # Refetch-Dedup
        self._seen_order: collections.deque = collections.deque(maxlen=DEDUP_HISTORY)

    # ── Lebenszyklus ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Startet die Bridge — reconnect-Loop wenn auto_reconnect."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_run()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("Bridge-Fehler: %s", e)
                if not self.auto_reconnect or not self._running:
                    break
                log.info("Reconnect in %ds ...", self.reconnect_delay)
                await asyncio.sleep(self.reconnect_delay)

    async def stop(self) -> None:
        """Stoppt die Bridge sauber."""
        self._running = False
        if self.mc:
            try:
                await self.mc.disconnect()
            except Exception:
                pass
            self.mc = None

    async def _connect_and_run(self) -> None:
        """Verbindet und läuft bis Fehler oder Stop."""
        log.info("Verbinde mit %s (%d Baud)...", self.port, self.baudrate)
        self.mc = await MeshCore.create_serial(self.port, self.baudrate)

        # self_info wird asynchron nach dem Connect befüllt — kurz warten
        await asyncio.sleep(2)
        info = self.mc.self_info or {}
        node_name = info.get("name", "UNBEKANNT")
        firmware  = info.get("ver") or self.cfg_firmware
        log.info("Verbunden: %s  Firmware: %s", node_name, firmware)

        # Eigene pubkey_prefix aktualisieren falls in der Config noch leer
        pubkey = info.get("public_key", "")
        if pubkey and not self.own_pubkey:
            self.own_pubkey = pubkey[:12]
            log.info("pubkey_prefix: %s", self.own_pubkey)

        # Kanal-Namen vom Node laden (ergänzt meshcore.json)
        await self._load_channels()

        # Event-Subscribe — meshcore akzeptiert Coroutine-Callbacks direkt
        # (Dispatcher prüft iscoroutinefunction und awaitet sie).
        self.mc.subscribe(MCEventType.CHANNEL_MSG_RECV, self._on_channel_msg)
        try:
            self.mc.subscribe(MCEventType.ADVERTISEMENT, self._on_advertisement)
        except Exception:
            pass  # nicht alle Firmware-Versionen liefern diesen Event

        # Auto-Fetch starten (pollt wartende Nachrichten vom Companion)
        try:
            await self.mc.start_auto_message_fetching()
            log.info("Auto-Message-Fetching aktiv (Intervall ~%ds)",
                     self.fetch_interval)
        except Exception as e:
            log.warning("start_auto_message_fetching() fehlgeschlagen: %s", e)

        log.info("Bridge aktiv — warte auf MeshCore-Nachrichten...")

        # Laufschleife — bis Verbindungsverlust oder stop()
        while self._running:
            if not self.mc.is_connected:
                raise ConnectionError("Verbindung zum Companion verloren")
            await asyncio.sleep(1.0)

    async def _load_channels(self) -> None:
        """Lädt Kanal-Namen vom Node und ergänzt self.channel_map."""
        for i in range(MAX_CHANNEL_SLOTS):
            try:
                ch = await self.mc.commands.get_channel(i)
                payload = getattr(ch, "payload", None)
                if not payload:
                    break
                name = payload.get("channel_name", "")
                if not name:
                    break  # leerer Slot → Ende
                if i not in self.channel_map:
                    self.channel_map[i] = {"index": i, "name": name,
                                           "gust_forward": True}
                else:
                    # Config-Name behalten falls gesetzt, sonst Node-Name
                    self.channel_map[i].setdefault("name", name)
            except Exception:
                break
        log.info("Kanäle geladen: %s",
                 {idx: v.get("name", "?") for idx, v in sorted(self.channel_map.items())})

    # ── Eingehende Channel-Messages ────────────────────────────────────

    async def _on_channel_msg(self, event) -> None:
        """Callback für CHANNEL_MSG_RECV (von meshcore als Coroutine awaited)."""
        await self._process_channel_msg(event)

    async def _process_channel_msg(self, event) -> None:
        """Verarbeitet eine eingehende Channel-Message und publiziert RX_FRAME."""
        try:
            payload = getattr(event, "payload", {})
            if not isinstance(payload, dict):
                log.debug("Unbekanntes Event-Format: %s", type(payload))
                return

            ch_idx    = payload.get("channel_idx", 0)
            text      = payload.get("text", "")
            pubkey    = payload.get("pubkey_prefix", "")   # bei Channel-Msgs leer
            timestamp = payload.get("sender_timestamp", 0)

            if not text:
                log.debug("Leerer Text-Payload ignoriert")
                return

            # Refetch-Dedup: dieselbe Nachricht kann nach einem Reconnect
            # erneut geliefert werden. Schlüssel = Kanal + Zeitstempel + Text.
            dkey = (ch_idx, timestamp, text)
            if dkey in self._seen:
                log.debug("Duplikat (Refetch) ignoriert: ch=%d ts=%s", ch_idx, timestamp)
                return
            self._remember(dkey)

            # Eigene Nachrichten nicht zurückspielen (nur möglich falls die
            # Firmware doch einen pubkey_prefix mitliefert — defensiv).
            if pubkey and self.own_pubkey and \
               pubkey.lower() == self.own_pubkey.lower():
                log.debug("Eigene Nachricht ignoriert")
                return

            # Kanal-Konfiguration prüfen
            ch_info = self.channel_map.get(ch_idx, {})
            ch_name = ch_info.get("name", f"ch{ch_idx}")
            gust_forward = ch_info.get("gust_forward", True)

            # Public-Kanal-Filter (ch 0)
            if ch_idx == 0 and not self.fwd_public:
                log.debug("Public-Kanal ignoriert (forward_public_channel=false)")
                return
            if not gust_forward:
                log.debug("Kanal %s nicht für GUST konfiguriert", ch_name)
                return

            # Absender auflösen (Channel-Msgs: aus Text bzw. eigenes Rufzeichen)
            from_call = resolve_sender(pubkey, text, self.contacts, self.own_callsign)

            # Mesh-Kontext in den Text packen (Kanal + Absender bleiben sichtbar)
            full_text = f"{ch_name}/{from_call}: {text}"

            log.info("MeshCore MSG  ch=%s  from=%s  text=%r",
                     ch_name, from_call, text[:60])

            # GUST-Text-Fragmente erzeugen (BROADCAST-Ziel, gemeinsame Seq-Nr)
            # UTF-8-sichere Byte-Chunks (BUG-MC-03) → GUST-0x40-Payloads
            seq = self._next_seq()
            text_chunks = fragment_text(full_text, chunk_size=14)
            total = len(text_chunks)
            fragments = [
                encode_text_fragment("BROADCAST", chunk.decode("utf-8"),
                                     seq, i, total)
                for i, chunk in enumerate(text_chunks)
            ]

            for frag_payload in fragments:
                # Zurückdekodieren → JSON-fähiges payload_decoded fürs WebGUI
                decoded = decode_text_fragment(frag_payload)
                frame = {
                    "frame_type":      TYPE_TEXT,
                    "type":            TYPE_TEXT,
                    "type_name":       frame_type_name(TYPE_TEXT),
                    "from":            from_call,
                    "channel":         ch_idx,   # MeshCore-Slot, KEIN HF-Kanal
                    "test":            False,
                    "crc_ok":          True,
                    "payload_decoded": decoded,
                    "payload_hex":     frag_payload.hex(),
                    "synthetic":       True,
                    "source":          "meshcore",
                    "meta": {
                        "channel_idx":   ch_idx,
                        "channel_name":  ch_name,
                        "pubkey_prefix": pubkey,
                        "meshcore_ts":   timestamp,
                    },
                }
                await self.bus.publish(make_rx_frame_event(frame))
                log.debug("RX_FRAME publiziert: from=%s  frag %d/%d  %dB",
                          from_call, decoded.get("frag_index", 0) + 1,
                          decoded.get("frag_total", 1), len(frag_payload))

        except Exception as e:
            log.exception("Fehler beim Verarbeiten der Channel-Message: %s", e)

    def _on_advertisement(self, event) -> None:
        """Loggt ADVERTISEMENT-Events (Nodes die sich melden)."""
        payload = getattr(event, "payload", {})
        if isinstance(payload, dict):
            name   = payload.get("adv_name") or payload.get("name", "?")
            pubkey = payload.get("public_key", "")[:12]
            cs     = extract_callsign(name)
            log.debug("ADV: %s  key=%s  rufz=%s", name, pubkey, cs or "—")

    # ── interne Helfer ─────────────────────────────────────────────────

    def _next_seq(self) -> int:
        """Fortlaufende GUST-Text-Sequenznummer (1 Byte)."""
        seq = self._seq
        self._seq = (self._seq + 1) & 0xFF
        return seq

    def _remember(self, key) -> None:
        """Merkt eine Nachricht für den Refetch-Dedup (bounded)."""
        if len(self._seen_order) == self._seen_order.maxlen:
            old = self._seen_order[0]   # wird durch append automatisch verdrängt
            self._seen.discard(old)
        self._seen_order.append(key)
        self._seen.add(key)


# ═══════════════════════════════════════════════════════════════════════
# Standalone-Betrieb (ohne laufenden Daemon)
# ═══════════════════════════════════════════════════════════════════════

class StandaloneEventBus:
    """
    Minimaler EventBus-Ersatz für Standalone-Betrieb.
    Loggt empfangene Events statt sie weiterzuleiten.
    Wird ersetzt sobald Bridge in den Daemon integriert ist (separater Prompt).
    """
    async def publish(self, event: dict) -> None:
        data       = event.get("data", {})
        frame_type = data.get("frame_type", data.get("type", 0))
        from_call  = data.get("from", "?")
        ch_name    = data.get("meta", {}).get("channel_name", "?")
        decoded    = data.get("payload_decoded", {})
        text       = decoded.get("text", "")
        log.info(
            "[STANDALONE] RX_FRAME  type=0x%02x  from=%-6s  ch=%-12s  "
            "frag=%d/%d  text=%r  synthetic=%s",
            frame_type if isinstance(frame_type, int) else 0,
            from_call, ch_name,
            decoded.get("frag_index", 0) + 1, decoded.get("frag_total", 1),
            text, data.get("synthetic", False),
        )


async def run_standalone(config_path: str, verbose: bool) -> None:
    """Startet Bridge im Standalone-Modus ohne GUST-Daemon."""
    # UTF-8 Konsole (Windows) — vor dem Logging konfigurieren
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    log.info("GUST MeshCore Bridge  —  Standalone-Modus")
    log.info("Konfiguration: %s", config_path)

    config = load_config(config_path)
    bus    = StandaloneEventBus()
    bridge = MeshCoreBridge(config, bus)

    try:
        await bridge.start()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Bridge gestoppt")
    finally:
        await bridge.stop()


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="GUST MeshCore Bridge (P6-19) — Standalone-Modus"
    )
    parser.add_argument("--config",  default=MESHCORE_JSON_DEFAULT,
                        help=f"Pfad zu meshcore.json (Standard: {MESHCORE_JSON_DEFAULT})")
    parser.add_argument("--verbose", action="store_true",
                        help="Debug-Logging aktivieren")
    args = parser.parse_args()

    try:
        asyncio.run(run_standalone(args.config, args.verbose))
    except KeyboardInterrupt:
        pass
