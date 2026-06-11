#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gust_keygen.py — GUST HMAC-Schluesselaustausch-Werkzeug (P8-11)

Tauscht bilaterale HMAC-Schluessel (Frame 0x50 AUTH) zwischen zwei GUST-
Stationen aus und traegt sie in gateway.json (auth.keys) ein.

Das Kernproblem: Beide Partner brauchen denselben Schluessel, aber jede
Station vergibt KEY_IDs unabhaengig. Der EMPFAENGER bestimmt, welche KEY_ID
in den AUTH-Frames stehen muss, die er verifizieren soll (gust_rx schlaegt
den Schluessel per KEY_ID nach). Darum laeuft der Austausch in drei Schritten:

    1) init    (OE3GAS)  Schluessel erzeugen, Block fuer Partner ausgeben
    2) accept  (OE1XTU)  Block eintragen, eigene KEY_ID zurueckmelden
    3) confirm (OE3GAS)  Eintrag mit der KEY_ID des Partners vervollstaendigen

Endzustand (Beispiel OE3GAS <-> OE1XTU, init-KEY_ID=4, accept-KEY_ID=35):
    OE3GAS gateway.json:  {key_id: 35, callsign: OE1XTU}  → verifiziert OE1XTU
    OE1XTU gateway.json:  {key_id:  4, callsign: OE3GAS}  → verifiziert OE3GAS
    (OE3GAS sendet an OE1XTU mit KEY_ID=4, OE1XTU sendet an OE3GAS mit KEY_ID=35)

Weitere Modi:  list (alle Schluessel zeigen),  revoke (Schluessel entfernen).

Nur stdlib (os, json, argparse, re) — keine externen Abhaengigkeiten.
"""

import argparse
import json
import os
import re
import sys
import tempfile

# Box-/Sonderzeichen auch auf cp1252-Konsolen (Windows) ausgeben koennen —
# Projektkonvention (vgl. gust.py, gust_stress_decode.py).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

W = 64   # Banner-Innenbreite


# ══════════════════════════════════════════════════════════════════════
# Hilfsfunktionen
# ══════════════════════════════════════════════════════════════════════

def _die(msg: str) -> None:
    """Fehlermeldung auf stderr ausgeben und mit Code 1 beenden."""
    print(f"FEHLER: {msg}", file=sys.stderr)
    sys.exit(1)


def _validate_callsign(callsign: str) -> str:
    """Rufzeichen pruefen (3–9 Zeichen, nur A–Z 0–9 /). Gibt Grossschreibung zurueck."""
    cs = (callsign or "").strip().upper()
    if not re.match(r"^[A-Z0-9/]{3,9}$", cs):
        _die(f"Ungueltiges Rufzeichen: {callsign!r} (erlaubt: 3–9 × [A-Z0-9/])")
    return cs


def _validate_key_hex(key_hex: str) -> str:
    """Schluessel-Hex pruefen (genau 64 Zeichen = 32 Byte). Gibt Kleinschreibung zurueck."""
    kh = (key_hex or "").strip().lower()
    if len(kh) != 64:
        _die(f"key-hex muss 64 Hex-Zeichen sein ({len(kh)} erhalten)")
    try:
        bytes.fromhex(kh)
    except ValueError:
        _die("key-hex enthaelt ungueltige Zeichen (nur 0–9 a–f)")
    return kh


def _next_free_key_id(existing_keys: list) -> int:
    """Kleinste positive Ganzzahl die noch nicht als key_id vergeben ist."""
    used = set()
    for k in existing_keys:
        try:
            used.add(int(k.get("key_id", 0)))
        except (TypeError, ValueError):
            continue
    i = 1
    while i in used:
        i += 1
    return i


def _resolve_config(path: str) -> str:
    """gateway.json finden: zuerst wie angegeben (CWD), dann Skript-Verzeichnis."""
    candidates = [
        path,
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     os.path.basename(path)),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return path   # existiert (noch) nicht — init darf neu anlegen


def _load_config(path: str) -> dict:
    """gateway.json laden. Nicht vorhanden → leeres Dict (init legt an)."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        _die(f"gateway.json ({path}) nicht lesbar: {e}")


def _save_config_atomic(cfg: dict, path: str) -> None:
    """
    gateway.json atomar schreiben: temporaere Datei im selben Verzeichnis,
    dann os.replace. Verhindert halbfertige/leere Konfigurationsdateien.
    (Gleiches Muster wie gust_web._save_config_atomic.)
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".gateway.", suffix=".json.tmp",
                                    dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def _auth_block(cfg: dict) -> dict:
    """auth-Block (mit keys-Liste) sicherstellen und zurueckgeben."""
    auth = cfg.setdefault("auth", {})
    auth.setdefault("enabled", False)
    if not isinstance(auth.get("keys"), list):
        auth["keys"] = []
    return auth


def _find_by_key_id(keys: list, key_id: int) -> dict | None:
    for k in keys:
        try:
            if int(k.get("key_id", -1)) == key_id:
                return k
        except (TypeError, ValueError):
            continue
    return None


def _banner(title_lines: list, body_lines: list) -> None:
    """Schlichter, kopierfreundlicher Rahmen (kein rechter Rand → lange Hex-Zeilen ok)."""
    bar = "═" * W
    print()
    print(bar)
    for t in title_lines:
        print(f"  {t}")
    print(bar)
    for b in body_lines:
        print(b if b == "" else f"  {b}")
    print(bar)
    print()


# ══════════════════════════════════════════════════════════════════════
# Subcommands
# ══════════════════════════════════════════════════════════════════════

def cmd_init(args, cfg: dict, path: str) -> None:
    partner = _validate_callsign(args.partner)
    me      = (cfg.get("callsign") or "?").upper()
    auth    = _auth_block(cfg)
    keys    = auth["keys"]

    # Laeuft bereits ein (unbestaetigter) Austausch mit diesem Partner?
    pending = next((k for k in keys
                    if str(k.get("callsign", "")).upper() == partner
                    and k.get("_pending")), None)
    if pending:
        _die(f"Austausch mit {partner} laeuft bereits "
             f"(KEY_ID={pending.get('key_id', '?')}) — erst 'confirm' "
             f"abschliessen oder 'revoke'")

    if args.key_id is not None:
        kid = args.key_id
        if kid < 1:
            _die("key-id muss eine positive Ganzzahl sein")
        dup = _find_by_key_id(keys, kid)
        if dup:
            _die(f"KEY_ID={kid} ist bereits vergeben fuer "
                 f"{dup.get('callsign', '?')}")
    else:
        kid = _next_free_key_id(keys)

    key_hex = os.urandom(32).hex()
    keys.append({
        "key_id":   kid,
        "callsign": partner,
        "key_hex":  key_hex,
        "_pending": True,   # wird von confirm aufgeloest
        "_comment": (f"Austausch initiiert {me}->{partner}, warte auf Antwort "
                     f"(KEY_ID die der Partner vergibt noch unbekannt)"),
    })
    auth["enabled"] = True
    _save_config_atomic(cfg, path)

    _banner(
        ["GUST Schluessel-Austausch — Initiierung",
         f"Von: {me}  →  An: {partner}"],
        [f"Schluessel erzeugt und in {os.path.basename(path)} eingetragen "
         f"(KEY_ID={kid}). ✓",
         "",
         f"Sende {partner} diesen Block fuer dessen gateway.json → auth.keys:",
         "",
         "{",
         f'  "key_id": {kid},',
         f'  "callsign": "{me}",',
         f'  "key_hex": "{key_hex}"',
         "}",
         "",
         f"{partner} antwortet dann mit (und nennt dir SEINE KEY_ID):",
         "",
         f"  py gust_keygen.py accept --from {me} \\",
         f"     --key-id {kid} --key-hex {key_hex}",
         "",
         f"Dessen KEY_ID kann eine andere Zahl als {kid} sein — sie kommt",
         f"im naechsten Schritt (confirm) zu dir zurueck."],
    )


def cmd_accept(args, cfg: dict, path: str) -> None:
    from_call = _validate_callsign(args.from_call)
    key_hex   = _validate_key_hex(args.key_hex)
    init_kid  = args.key_id
    me        = (cfg.get("callsign") or "?").upper()
    auth      = _auth_block(cfg)
    keys      = auth["keys"]

    if init_kid < 1:
        _die("key-id muss eine positive Ganzzahl sein")
    dup = _find_by_key_id(keys, init_kid)
    if dup:
        _die(f"KEY_ID={init_kid} ist bereits vergeben fuer "
             f"{dup.get('callsign', '?')} — Austausch nicht eindeutig")

    # Schluessel des Initiators eintragen: mit DESSEN KEY_ID (init_kid).
    # Damit verifiziere ich Frames von <from>, der genau diese KEY_ID sendet.
    keys.append({
        "key_id":   init_kid,
        "callsign": from_call,
        "key_hex":  key_hex,
        "_comment": f"Schluessel von {from_call}, KEY_ID={init_kid}",
    })
    auth["enabled"] = True

    # Meine KEY_ID fuer den Initiator: die Zahl, die ER in seinen AUTH-Frames
    # an mich verwenden soll (= meine Empfangs-KEY_ID, frei in MEINER Config).
    if args.my_key_id is not None:
        if args.my_key_id < 1:
            _die("my-key-id muss eine positive Ganzzahl sein")
        my_kid = args.my_key_id
        clash = _find_by_key_id(keys, my_kid)
        if clash and clash.get("callsign", "").upper() != from_call:
            _die(f"my-key-id={my_kid} kollidiert mit bestehendem Eintrag "
                 f"({clash.get('callsign', '?')}) — andere waehlen")
    else:
        my_kid = _next_free_key_id(keys)

    _save_config_atomic(cfg, path)

    _banner(
        ["GUST Schluessel-Austausch — Bestaetigung",
         f"Von: {me}  →  An: {from_call}"],
        [f"Schluessel von {from_call} eingetragen (KEY_ID={init_kid}). ✓",
         "",
         f"Teile {from_call} mit:",
         f"  Meine KEY_ID fuer dich: {my_kid}",
         f"  ({from_call} verwendet diese KEY_ID in AUTH-Frames an mich)",
         "",
         f"{from_call} schliesst den Austausch ab mit:",
         "",
         f"  py gust_keygen.py confirm --partner {me} --their-key-id {my_kid}"],
    )


def cmd_confirm(args, cfg: dict, path: str) -> None:
    partner   = _validate_callsign(args.partner)
    their_kid = args.their_key_id
    me        = (cfg.get("callsign") or "?").upper()
    auth      = _auth_block(cfg)
    keys      = auth["keys"]

    if their_kid < 1:
        _die("their-key-id muss eine positive Ganzzahl sein")

    # Genau den offenen (pending) Austausch-Eintrag dieses Partners treffen —
    # nicht einen bereits bestehenden, vollstaendigen Schluessel ueberschreiben.
    entry = next((k for k in keys
                  if str(k.get("callsign", "")).upper() == partner
                  and k.get("_pending")), None)
    if entry is None:
        # Fallback: kein pending, aber evtl. genau ein Eintrag → eindeutig?
        same = [k for k in keys
                if str(k.get("callsign", "")).upper() == partner]
        if len(same) == 1:
            entry = same[0]
        elif not same:
            _die(f"Kein Eintrag fuer {partner} gefunden — zuerst "
                 f"'init --partner {partner}' ausfuehren")
        else:
            _die(f"Mehrere Eintraege fuer {partner}, aber kein offener "
                 f"Austausch — nichts zu bestaetigen")

    # KEY_ID, die ICH beim Senden an den Partner einbette (= init-KEY_ID,
    # die der Partner zum Verifizieren meiner Frames nutzt).
    try:
        send_kid = int(entry.get("key_id", -1))
    except (TypeError, ValueError):
        send_kid = -1

    # Kollision pruefen: their_kid darf nicht schon einem ANDEREN Partner gehoeren.
    clash = next((k for k in keys
                  if k is not entry and _kid_eq(k, their_kid)), None)
    if clash:
        _die(f"KEY_ID={their_kid} ist bereits fuer "
             f"{clash.get('callsign', '?')} vergeben — Eintrag nicht aenderbar")

    # Eintrag auf die Empfangs-KEY_ID des Partners setzen: ab jetzt verifiziere
    # ich Frames von <partner>, der mit their_kid sendet.
    entry["key_id"]  = their_kid
    entry.pop("_pending", None)
    entry["_comment"] = (f"Austausch abgeschlossen: verifiziere {partner} mit "
                         f"KEY_ID={their_kid}; sende an {partner} mit "
                         f"KEY_ID={send_kid}")
    auth["enabled"] = True
    _save_config_atomic(cfg, path)

    _banner(
        ["GUST Schluessel-Austausch — Abgeschlossen"],
        [f"{me} → {partner}:",
         f"  {partner} verifiziert {me}-Frames mit KEY_ID={send_kid}",
         "",
         f"{partner} → {me}:",
         f"  {me} verifiziert {partner}-Frames mit KEY_ID={their_kid}",
         "",
         "Beide Richtungen konfiguriert. ✓"],
    )


def cmd_list(args, cfg: dict, path: str) -> None:
    auth = _auth_block(cfg)
    keys = auth["keys"]
    print()
    print(f"AUTH-Schluessel in {os.path.basename(path)}  "
          f"(auth.enabled = {auth.get('enabled')})")
    print(f"{'KEY-ID':>6}  {'Callsign':<9}  {'Schluessel':<13}  Kommentar")
    print(f"{'─'*6}  {'─'*9}  {'─'*13}  {'─'*30}")
    if not keys:
        print("  (keine Schluessel konfiguriert)")
    for k in keys:
        kid  = k.get("key_id", "?")
        cs   = str(k.get("callsign", "?"))
        kh   = str(k.get("key_hex", ""))
        snip = (kh[:8] + "…") if len(kh) >= 8 else (kh or "—")
        com  = str(k.get("_comment", ""))
        print(f"{str(kid):>6}  {cs:<9}  {snip:<13}  {com}")
    print()


def cmd_revoke(args, cfg: dict, path: str) -> None:
    kid  = args.key_id
    auth = _auth_block(cfg)
    keys = auth["keys"]
    entry = _find_by_key_id(keys, kid)
    if entry is None:
        _die(f"Kein Schluessel mit KEY_ID={kid} in {os.path.basename(path)}")
    cs = entry.get("callsign", "?")

    if not args.yes:
        try:
            resp = input(f"Schluessel KEY_ID={kid} ({cs}) wirklich entfernen? "
                         f"[j/N] ").strip().lower()
        except EOFError:
            resp = ""
        if resp not in ("j", "ja", "y", "yes"):
            print("Abgebrochen — nichts geaendert.")
            return

    auth["keys"] = [k for k in keys if not _kid_eq(k, kid)]
    _save_config_atomic(cfg, path)
    print(f"Schluessel KEY_ID={kid} ({cs}) entfernt.")


def _kid_eq(entry: dict, key_id: int) -> bool:
    try:
        return int(entry.get("key_id", -1)) == key_id
    except (TypeError, ValueError):
        return False


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gust_keygen.py",
        description="GUST HMAC-Schluesselaustausch-Werkzeug (Frame 0x50 AUTH)",
    )
    parser.add_argument("--config", default="gateway.json",
                        help="Pfad zu gateway.json (Standard: gateway.json)")

    sub = parser.add_subparsers(dest="cmd")

    p_init = sub.add_parser("init", help="Austausch initiieren")
    p_init.add_argument("--partner", required=True,
                        help="Rufzeichen des Partners (z.B. OE1XTU)")
    p_init.add_argument("--key-id", type=int, default=None,
                        help="Gewuenschte KEY_ID (Standard: naechste freie)")

    p_acc = sub.add_parser("accept", help="Austausch entgegennehmen")
    p_acc.add_argument("--from", dest="from_call", required=True,
                       help="Rufzeichen des Initiators")
    p_acc.add_argument("--key-id", type=int, required=True,
                       help="KEY_ID die der Initiator vergeben hat")
    p_acc.add_argument("--key-hex", required=True,
                       help="Schluessel (64 Hex-Zeichen)")
    p_acc.add_argument("--my-key-id", type=int, default=None,
                       help="Eigene KEY_ID fuer den Initiator (Standard: naechste freie)")

    p_con = sub.add_parser("confirm", help="Austausch abschliessen")
    p_con.add_argument("--partner", required=True,
                       help="Rufzeichen des Partners")
    p_con.add_argument("--their-key-id", type=int, required=True,
                       help="KEY_ID die der Partner fuer uns vergeben hat")

    sub.add_parser("list", help="Alle Schluessel anzeigen")

    p_rev = sub.add_parser("revoke", help="Schluessel entfernen")
    p_rev.add_argument("--key-id", type=int, required=True)
    p_rev.add_argument("--yes", action="store_true",
                       help="Bestaetigung ueberspringen")

    return parser


_DISPATCH = {
    "init":    cmd_init,
    "accept":  cmd_accept,
    "confirm": cmd_confirm,
    "list":    cmd_list,
    "revoke":  cmd_revoke,
}


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    if not args.cmd:
        build_parser().print_help()
        sys.exit(1)

    path = _resolve_config(args.config)
    cfg  = _load_config(path)
    _DISPATCH[args.cmd](args, cfg, path)


if __name__ == "__main__":
    main()
