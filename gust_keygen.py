#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gust_keygen.py — GUST AUTH-Schlüsselverwaltung (P8-11, vereinfacht)

Verwaltet bilaterale HMAC-Schlüssel (Frame 0x50 AUTH) in gateway.json.
Lookup erfolgt per Rufzeichen — keine KEY_ID-Koordination nötig.

Verwendung:
  py gust_keygen.py add --partner OE1XTU [--key-hex <64hex>]
  py gust_keygen.py revoke --partner OE1XTU [--yes]
  py gust_keygen.py list
"""

import argparse
import json
import os
import re
import secrets
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

W = 64


def _die(msg):
    print(f"FEHLER: {msg}", file=sys.stderr)
    sys.exit(1)


def _validate_callsign(cs):
    cs = (cs or "").strip().upper()
    if not re.match(r"^[A-Z0-9/]{3,9}$", cs):
        _die(f"Ungültiges Rufzeichen: {cs!r} (erlaubt: 3–9 × [A-Z0-9/])")
    return cs


def _validate_key_hex(kh):
    kh = (kh or "").strip().lower()
    if len(kh) != 64:
        _die(f"key-hex muss 64 Hex-Zeichen sein ({len(kh)} erhalten)")
    try:
        bytes.fromhex(kh)
    except ValueError:
        _die("key-hex enthält ungültige Zeichen (nur 0–9 a–f)")
    return kh


def _resolve_config(path):
    candidates = [path,
                  os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               os.path.basename(path))]
    for p in candidates:
        if os.path.exists(p):
            return p
    return path


def _load_config(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        _die(f"gateway.json nicht lesbar: {e}")


def _save_config_atomic(cfg, path):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".gateway.", suffix=".json.tmp",
                               dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _auth_block(cfg):
    auth = cfg.setdefault("auth", {})
    auth.setdefault("enabled", False)
    if not isinstance(auth.get("keys"), list):
        auth["keys"] = []
    return auth


def _find_by_callsign(keys, callsign):
    for k in keys:
        if str(k.get("callsign", "")).upper() == callsign:
            return k
    return None


def _banner(title_lines, body_lines):
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


def cmd_add(args, cfg, path):
    partner = _validate_callsign(args.partner)
    auth    = _auth_block(cfg)
    keys    = auth["keys"]

    existing = _find_by_callsign(keys, partner)
    if existing and not args.force:
        _die(f"Schlüssel für {partner} existiert bereits — "
             f"'--force' zum Überschreiben oder 'revoke' zum Entfernen")

    key_hex = (_validate_key_hex(args.key_hex)
               if args.key_hex
               else secrets.token_hex(32))

    entry = {
        "callsign": partner,
        "key_hex":  key_hex,
        "_comment": f"Bilateraler Schlüssel mit {partner}",
    }

    if existing:
        idx = keys.index(existing)
        keys[idx] = entry
        action = "aktualisiert"
    else:
        keys.append(entry)
        action = "hinzugefügt"

    auth["enabled"] = True
    _save_config_atomic(cfg, path)

    _banner(
        [f"GUST AUTH-Schlüssel {action}: {partner}"],
        [f"Rufzeichen : {partner}",
         f"key_hex    : {key_hex}",
         "",
         "Diesen key_hex dem Partner mitteilen.",
         f"Partner trägt ein: py gust_keygen.py add --partner "
         f"{cfg.get('callsign','OE3GAS')} --key-hex {key_hex}",
         "",
         "Beide Seiten müssen denselben key_hex verwenden."],
    )


def cmd_revoke(args, cfg, path):
    partner = _validate_callsign(args.partner)
    auth    = _auth_block(cfg)
    keys    = auth["keys"]

    existing = _find_by_callsign(keys, partner)
    if not existing:
        _die(f"Kein Schlüssel für {partner} gefunden")

    if not args.yes:
        try:
            resp = input(f"Schlüssel für {partner} wirklich entfernen? [j/N] ").strip().lower()
        except EOFError:
            resp = ""
        if resp not in ("j", "ja", "y", "yes"):
            print("Abgebrochen — nichts geändert.")
            return

    auth["keys"] = [k for k in keys
                    if str(k.get("callsign", "")).upper() != partner]
    _save_config_atomic(cfg, path)
    print(f"Schlüssel für {partner} entfernt.")


def cmd_list(args, cfg, path):
    auth = _auth_block(cfg)
    keys = auth["keys"]
    enabled = auth.get("enabled", False)
    print(f"\nAUTH: {'aktiviert' if enabled else 'deaktiviert'}  "
          f"| {len(keys)} Schlüssel\n")
    if not keys:
        print("  (keine Schlüssel konfiguriert)")
    for k in keys:
        cs      = k.get("callsign", "?")
        preview = (k.get("key_hex") or "")[:16] + "…"
        comment = k.get("_comment", "")
        print(f"  {cs:<12}  key: {preview}  {comment}")
    print()


def build_parser():
    parser = argparse.ArgumentParser(
        prog="gust_keygen.py",
        description="GUST AUTH-Schlüsselverwaltung — Lookup per Rufzeichen",
    )
    parser.add_argument("--config", default="gateway.json")

    sub = parser.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add", help="Schlüssel hinzufügen oder aktualisieren")
    p_add.add_argument("--partner",  required=True)
    p_add.add_argument("--key-hex",  default=None,
                       help="64 Hex-Zeichen; fehlt → zufällig generiert")
    p_add.add_argument("--force",    action="store_true",
                       help="Bestehenden Eintrag überschreiben")

    p_rev = sub.add_parser("revoke", help="Schlüssel entfernen")
    p_rev.add_argument("--partner", required=True)
    p_rev.add_argument("--yes",     action="store_true")

    sub.add_parser("list", help="Alle Schlüssel anzeigen")

    return parser


def main():
    parser = build_parser()
    args   = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(0)

    path = _resolve_config(args.config)
    cfg  = _load_config(path)
    if not cfg:
        cfg = {}

    if args.cmd == "add":
        cmd_add(args, cfg, path)
    elif args.cmd == "revoke":
        cmd_revoke(args, cfg, path)
    elif args.cmd == "list":
        cmd_list(args, cfg, path)


if __name__ == "__main__":
    main()
