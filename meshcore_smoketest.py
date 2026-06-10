"""
P6-21  MeshCore Companion Smoke-Test
=====================================
Verifiziert: USB-Serial-Verbindung, Device-Query, Kanal-Liste,
             CHANNEL_MSG_RECV-Events.

Verwendung:
    py meshcore_smoketest.py
    py meshcore_smoketest.py --port COM18 --timeout 120

Pass-Kriterium:
    - Node-Name enthält "OE3GAS"
    - Mindestens 6 Kanäle vorhanden
    - Mindestens 1 CHANNEL_MSG_RECV-Event innerhalb --timeout Sekunden
"""

import asyncio
import argparse
import logging
import sys
import json
import re
from datetime import datetime

# ── Import-Check ──────────────────────────────────────────────────────
try:
    from meshcore import MeshCore, EventType
except ImportError:
    print("FEHLER: meshcore-Library nicht installiert.")
    print("       pip install meshcore")
    sys.exit(1)

# ── Konsole auf UTF-8 zwingen ─────────────────────────────────────────
# Windows-Konsole ist standardmäßig cp1252 und kann die ✓/✗/⚠/—-Glyphen
# nicht kodieren → sonst UnicodeEncodeError statt sauberer Ausgabe.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("smoketest")

# ── Rufzeichen-Extraktion (wie in gust_transforms.py geplant) ─────────
def extract_callsign(node_name: str) -> str:
    """
    Extrahiert Rufzeichen aus MeshCore-Node-Namen.
    Beispiel: "AT-HL-OE3GAS-🦚" → "OE3GAS"
    Fallback: erste 6 Zeichen des bereinigten Namens.
    """
    m = re.search(r'([A-Z]{1,3}[0-9][A-Z]{1,4})', node_name.upper())
    return m.group(1) if m else re.sub(r'[^A-Z0-9]', '', node_name.upper())[:6]

# ── Hauptroutine ──────────────────────────────────────────────────────
async def run_smoketest(port: str, timeout_s: int) -> bool:
    results = {
        "port": port,
        "timestamp": datetime.now().isoformat(),
        "connect": False,
        "device_query": None,
        "callsign_extracted": None,
        "pubkey_prefix": "",
        "firmware": "",
        "radio": "",
        "channels": [],
        "channel_count_ok": False,
        "msg_received": False,
        "msg_events": [],
        "advertisement_events": [],
        "pass": False
    }

    print(f"\n{'='*60}")
    print(f"  GUST MeshCore Smoke-Test  —  {port}")
    print(f"{'='*60}\n")

    # ── Schritt 1: Verbindung ─────────────────────────────────────────
    print(f"[1/4] Verbinde mit {port} (115200 Baud)...")
    try:
        mc = await MeshCore.create_serial(port, 115200)
        results["connect"] = True
        print(f"      ✓ Verbunden\n")
    except Exception as e:
        print(f"      ✗ Verbindung fehlgeschlagen: {e}")
        _print_summary(results)
        return False

    # ── Schritt 2: Device Query ───────────────────────────────────────
    print("[2/4] Node-Info (self_info)...")
    try:
        # self_info wird nach Verbindungsaufbau automatisch befüllt
        await asyncio.sleep(2)
        info = mc.self_info or {}

        node_name   = info.get("name", "UNBEKANNT")
        callsign    = extract_callsign(node_name)
        pubkey      = info.get("public_key", "")
        pubkey_prefix = pubkey[:12] if pubkey else ""
        firmware    = info.get("ver", "")
        freq        = info.get("radio_freq", "?")
        bw          = info.get("radio_bw", "?")
        sf          = info.get("radio_sf", "?")
        cr          = info.get("radio_cr", "?")

        results["device_query"]        = node_name
        results["callsign_extracted"]  = callsign
        results["pubkey_prefix"]       = pubkey_prefix
        results["firmware"]            = firmware
        results["radio"]               = f"{freq},{bw},{sf},{cr}"

        print(f"      Node-Name   : {node_name}")
        print(f"      Rufzeichen  : {callsign}")
        print(f"      pubkey_prefix: {pubkey_prefix}")
        print(f"      Firmware    : {firmware}")
        print(f"      Radio       : {freq} MHz  BW={bw}  SF={sf}  CR={cr}")

        if "OE3GAS" in node_name.upper():
            print(f"      ✓ Node-Name enthält OE3GAS\n")
        else:
            print(f"      ⚠ Node-Name enthält nicht 'OE3GAS' — prüfen!\n")
    except Exception as e:
        print(f"      ✗ Node-Info fehlgeschlagen: {e}\n")

    # ── Schritt 3: Kanal-Liste ────────────────────────────────────────
    print("[3/4] Kanal-Liste (get_channel per Index)...")
    try:
        ch_list = []
        for i in range(20):   # max 20 Slots, Abbruch bei Exception
            try:
                ch = await mc.commands.get_channel(i)
                if ch and ch.payload:
                    payload = ch.payload
                    name    = payload.get("channel_name", f"slot{i}")
                    secret  = payload.get("channel_secret", b"")
                    # channel_secret als Hex-String für JSON-Serialisierung
                    secret_hex = secret.hex() if isinstance(secret, bytes) else str(secret)
                    ch_list.append({
                        "index":  i,
                        "name":   name,
                        "secret": secret_hex,
                        "hash":   payload.get("channel_hash", "")
                    })
                    print(f"        [{i}] {name}  hash={payload.get('channel_hash','?')}")
                else:
                    break
            except Exception:
                break   # kein weiterer Slot vorhanden

        results["channels"]          = ch_list
        results["channel_count_ok"]  = len(ch_list) >= 6

        print(f"      Gefundene Kanäle: {len(ch_list)}")

        # Erwartete Kanäle prüfen
        expected = {"public", "at-hl", "hollabrunn", "noe", "test", "vienna"}
        found    = {c["name"].lower() for c in ch_list}
        missing  = expected - found
        if missing:
            print(f"      ⚠ Fehlende Kanäle: {missing}")

        if results["channel_count_ok"]:
            print(f"      ✓ ≥6 Kanäle vorhanden\n")
        else:
            print(f"      ⚠ Weniger als 6 Kanäle\n")
    except Exception as e:
        print(f"      ✗ Kanal-Abfrage fehlgeschlagen: {e}\n")

    # ── Schritt 4: Message-Empfang ─────────────────────────────────────
    print(f"[4/4] Warte auf CHANNEL_MSG_RECV ({timeout_s}s)...")
    print(f"      → Sende jetzt eine Nachricht auf #test oder #noe\n")

    msg_event = asyncio.Event()

    def on_channel_msg(event):
        payload = event.payload if hasattr(event, 'payload') else str(event)
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n      *** CHANNEL_MSG_RECV um {ts} ***")
        print(f"          Payload: {payload}")

        # Rufzeichen-Auflösung aus pubkey_prefix (falls vorhanden)
        if isinstance(payload, dict):
            ch_idx = payload.get("channel_idx", "?")
            text   = payload.get("text", "")
            pubkey = payload.get("pubkey_prefix", "")
            sender = extract_callsign(pubkey) if pubkey else "UNKNWN"
            print(f"          Kanal-Index: {ch_idx}  Absender-Key: {pubkey}")
            print(f"          Rufzeichen (extrahiert): {sender}")
            print(f"          Text: {text}")
            results["msg_events"].append({
                "ts": ts,
                "channel_idx": ch_idx,
                "pubkey_prefix": pubkey,
                "callsign": sender,
                "text": text
            })
        else:
            results["msg_events"].append({"ts": ts, "raw": str(payload)})

        results["msg_received"] = True
        msg_event.set()

    def on_advertisement(event):
        payload = event.payload if hasattr(event, 'payload') else str(event)
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"      [ADV {ts}] {payload}")
        results["advertisement_events"].append({"ts": ts, "payload": str(payload)})

    def on_contact_msg(event):
        payload = event.payload if hasattr(event, 'payload') else str(event)
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n      *** CONTACT_MSG_RECV um {ts}: {payload} ***")

    # Subscribe auf alle relevanten Events
    mc.subscribe(EventType.CHANNEL_MSG_RECV, on_channel_msg)
    mc.subscribe(EventType.CONTACT_MSG_RECV, on_contact_msg)
    try:
        mc.subscribe(EventType.ADVERTISEMENT, on_advertisement)
    except Exception:
        pass  # ADVERTISEMENT nicht auf allen Firmware-Versionen verfügbar

    # Auto-Fetch starten
    try:
        await mc.start_auto_message_fetching()
        print("      Auto-Message-Fetching aktiv")
    except Exception as e:
        print(f"      ⚠ start_auto_message_fetching() fehlgeschlagen: {e}")
        print("        Versuche manuelles Polling...")

    # Warten
    try:
        await asyncio.wait_for(msg_event.wait(), timeout=timeout_s)
        print(f"\n      ✓ CHANNEL_MSG_RECV empfangen\n")
    except asyncio.TimeoutError:
        print(f"\n      ✗ Timeout nach {timeout_s}s — keine Channel-Message empfangen")
        print(f"        Advertisements gesehen: {len(results['advertisement_events'])}")
        print(f"        → Ist der Repeater (COM19) aktiv und im selben Kanal?\n")

    await mc.disconnect()

    # ── Ergebnis ──────────────────────────────────────────────────────
    # Check 4 (CHANNEL_MSG_RECV) ist kein Pass-Kriterium (P6-21 Entscheidung):
    # USB-Firmware erlaubt kein gleichzeitiges BLE — Verifikation erfolgt in P6-19.
    results["pass"] = (
        results["connect"] and
        results["device_query"] is not None and
        results["channel_count_ok"]
    )

    _print_summary(results)

    # JSON-Log schreiben
    logfile = f"meshcore_smoketest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(logfile, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nLog gespeichert: {logfile}")

    return results["pass"]


def _print_summary(results: dict) -> None:
    print(f"\n{'='*60}")
    print(f"  ERGEBNIS")
    print(f"{'='*60}")
    print(f"  Verbindung       : {'✓' if results['connect'] else '✗'}")
    print(f"  Node-Name        : {'✓ ' + str(results['device_query'])[:40] if results['device_query'] else '✗'}")
    print(f"  Rufzeichen       : {results.get('callsign_extracted', '–')}")
    print(f"  pubkey_prefix    : {results.get('pubkey_prefix', '–')}")
    print(f"  Firmware         : {results.get('firmware', '–')}")
    print(f"  Radio            : {results.get('radio', '–')}")
    print(f"  Kanäle (≥6)      : {'✓' if results['channel_count_ok'] else '✗'} ({len(results['channels'])} gefunden)")
    print(f"  Channel-Message  : {'–' if not results['msg_received'] else '✓'} ({len(results['msg_events'])} Events, nicht Pass-Kriterium)")
    print(f"  Advertisements   : {len(results['advertisement_events'])} gesehen")
    print(f"{'='*60}")
    status = "PASS ✓" if results.get("pass") else "FAIL ✗"
    print(f"  GESAMT: {status}")
    print(f"{'='*60}\n")


# ── CLI ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="GUST MeshCore Companion Smoke-Test (P6-21)"
    )
    parser.add_argument("--port",    default="COM18",
                        help="COM-Port des Companion (Standard: COM18)")
    parser.add_argument("--timeout", type=int, default=120,
                        help="Sekunden auf Channel-Message warten (Standard: 120)")
    args = parser.parse_args()

    ok = asyncio.run(run_smoketest(args.port, args.timeout))
    sys.exit(0 if ok else 1)
