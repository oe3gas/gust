#!/usr/bin/env python3
"""
GUST — Web-Server                                          Phase 5
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Version : 0.1.0
Datum   : Mai 2026

Inhalt dieses Moduls:
  • WebServer     — aiohttp AppRunner, Port 8080, bind 0.0.0.0
  • REST API      — /api/status  /api/config  /api/tx/*  /api/log
  • WebSocket     — /ws/rx (RX-Frames Echtzeit)
                    /ws/log (Systemlog Echtzeit)
  • Static UI     — Eingebettetes HTML+Vanilla-JS Dashboard
                    (Tabs: Monitor | Senden | Status | Log)
  • Auth          — Bearer-Token / X-API-Key Middleware (optional)

Erwartete Schnittstellen (Duck-Typing):
  event_bus.subscribe()   -> asyncio.Queue   # Fan-out, jeder Sub bekommt eigene Queue
  event_bus.unsubscribe(q)                   # Queue wieder austragen
  Events sind dicts: {"type": str, "data": dict, "ts": float}
  Relevante Typen: "rx_frame", "tx_done", "status"

  gateway.enqueue(frame_dict, priority=4)    # Frame in TX-Queue einreihen
  gateway.get_status() -> dict               # Felder: queue_depth, last_tx, ...

Schnittstelle zu Phase 6 (MQTT):
  WebServer hat keine direkte Abhängigkeit zu MQTT.
  MQTTBridge ist ein weiterer EventBus-Subscriber — keine Änderung nötig.

Standalone-Test:
  python gust_web.py --port 8080
  → Öffnet Dashboard auf http://localhost:8080 mit Mock-Daten
"""

import asyncio
import json
import logging
import time
import hashlib
from pathlib import Path
from collections import deque
from datetime import datetime, timezone
from typing import Optional, Set

from aiohttp import web, WSMsgType
import aiohttp

# ═══════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════

log = logging.getLogger("gust.web")


class _BytesEncoder(json.JSONEncoder):
    """JSON-Encoder der bytes/bytearray als Hex-String serialisiert.

    Schutznetz für den RX-Frame-Broadcast: dekodierte Payloads können
    bytes-Felder enthalten (z.B. AUTH hmac_tag), die json.dumps sonst
    mit TypeError abbrechen lassen würden → _event_bus_reader stürzt ab.
    """
    def default(self, obj):
        if isinstance(obj, (bytes, bytearray)):
            return obj.hex()
        return super().default(obj)


# ═══════════════════════════════════════════════════════════════════════
# KANALPLAN (für UI-Anzeige)
# ═══════════════════════════════════════════════════════════════════════

CHANNEL_PLAN = [
    {"ch": 0, "nf_lo": 400,  "nf_hi": 650,  "tone0":  400.00},
    {"ch": 1, "nf_lo": 650,  "nf_hi": 900,  "tone0":  650.00},
    {"ch": 2, "nf_lo": 900,  "nf_hi": 1150, "tone0":  900.00},
    {"ch": 3, "nf_lo": 1150, "nf_hi": 1400, "tone0": 1150.00},
    {"ch": 4, "nf_lo": 1400, "nf_hi": 1650, "tone0": 1400.00},
    {"ch": 5, "nf_lo": 1650, "nf_hi": 1900, "tone0": 1650.00},
    {"ch": 6, "nf_lo": 1900, "nf_hi": 2150, "tone0": 1900.00},
    {"ch": 7, "nf_lo": 2150, "nf_hi": 2400, "tone0": 2150.00},
    {"ch": 8, "nf_lo": 2400, "nf_hi": 2650, "tone0": 2400.00},
    {"ch": 9, "nf_lo": 2650, "nf_hi": 2900, "tone0": 2650.00},
]


def _callsign_to_channel(callsign: str) -> int:
    """Deterministischer Heimatkanal per SHA-256 (wie in gust_frame.py)."""
    h = int(hashlib.sha256(callsign.upper().encode()).hexdigest(), 16)
    return h % 10


# ═══════════════════════════════════════════════════════════════════════
# EINGEBETTETES HTML/CSS/JS DASHBOARD
# ═══════════════════════════════════════════════════════════════════════

_HTML_UI = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GUST Dashboard</title>
<style>
/* ── UI-SCHRIFT & GRÖSSENSTUFEN (per JS überschreibbar) ──── */
:root {
  --ui-font:    'Courier New', monospace;
  --fs-xxs:  10px;
  --fs-xs:   11px;
  --fs-sm:   12px;
  --fs-base: 13px;
  --fs-lg:   16px;
}
/* ── DARK AMBER (Standard) ───────────────────────────────── */
:root {
  --bg:      #0d1117;
  --bg2:     #161b22;
  --bg3:     #21262d;
  --border:  #30363d;
  --text:    #c9d1d9;
  --text2:   #8b949e;
  --accent:  #e6a817;  /* Amber — Operator/Telegrafie-Feeling */
  --green:   #3fb950;
  --red:     #f85149;
  --blue:    #79c0ff;  /* Rufzeichen-Farbe */
  --purple:  #d2a8ff;  /* alternativ für Callsigns */
  --orange:  #ffa657;
  --shadow:  rgba(0,0,0,0);  /* kein Glow im Dark-Mode nötig */
}

/* ── LIGHT CLEAN ─────────────────────────────────────────── */
[data-theme="light"] {
  --bg:      #ffffff;
  --bg2:     #f6f8fa;
  --bg3:     #eaeef2;
  --border:  #d0d7de;
  --text:    #1f2328;
  --text2:   #636c76;
  --accent:  #0969da;  /* GitHub-Blau — klar, professionell */
  --green:   #1a7f37;
  --red:     #cf222e;
  --blue:    #8250df;  /* Rufzeichen lila — hebt sich vom Akzent ab */
  --purple:  #6639ba;
  --orange:  #bc4c00;
  --shadow:  rgba(0,0,0,0);
}

/* Glow nur im Dark-Mode sinnvoll */
[data-theme="light"] #ws-indicator.connected { box-shadow: none; }
[data-theme="light"] #ws-indicator.error     { box-shadow: none; }
/* Light-Theme: bold für bessere Lesbarkeit auf hellem Hintergrund */
[data-theme="light"] body { font-weight: bold; }
/* Light-Theme: gefüllte (primäre) Buttons brauchen weißen Text auf Akzentblau */
[data-theme="light"] .btn:not(.secondary):not(.btn-secondary) {
  color: #ffffff !important;
}
[data-theme="light"] .tx-prio-info,
[data-theme="light"] .ch-freq,
[data-theme="light"] .ch-info,
[data-theme="light"] .frame-row .ts,
[data-theme="light"] .log-line,
[data-theme="light"] .field-row .unit { font-weight: normal; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: var(--ui-font);
       font-size: var(--fs-base); min-height: 100vh;
       transition: background .2s, color .2s; }

/* ── HEADER ── */
header { background: var(--bg2); border-bottom: 1px solid var(--border);
         padding: 10px 16px; display: flex; align-items: center; gap: 16px; }
header h1 { font-size: var(--fs-lg); color: var(--accent); letter-spacing: 2px; flex: 1; }
header h1 span { color: var(--text2); font-size: var(--fs-xs); font-weight: normal;
                  margin-left: 8px; letter-spacing: 0; }
#ws-indicator { width: 10px; height: 10px; border-radius: 50%;
                background: var(--text2); transition: background .3s; }
#ws-indicator.connected { background: var(--green); box-shadow: 0 0 6px var(--green); }
#ws-indicator.error     { background: var(--red);   box-shadow: 0 0 6px var(--red); }

/* ── DAEMON HEARTBEAT ─────────────────────────────────── */
#daemon-hb {
  display: flex; align-items: center; gap: 5px;
  padding: 3px 9px; border-radius: 4px;
  font-size: 11px; font-weight: bold; letter-spacing: 0.05em;
  border: 1px solid var(--border);
  background: var(--bg3);
  cursor: default; user-select: none;
  transition: background .3s, border-color .3s;
}
.hb-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--text2); flex-shrink: 0;
  transition: background .3s;
}
.hb-label { color: var(--text2); transition: color .3s; }

#daemon-hb.hb-alive  .hb-dot   { background: var(--green); animation: hb-pulse 2s ease-in-out infinite; }
#daemon-hb.hb-alive  .hb-label { color: var(--green); }
#daemon-hb.hb-warn   .hb-dot   { background: var(--orange); animation: hb-blink 0.7s step-end infinite; }
#daemon-hb.hb-warn   .hb-label { color: var(--orange); }
#daemon-hb.hb-dead   .hb-dot   { background: var(--red); animation: none; }
#daemon-hb.hb-dead   .hb-label { color: var(--red); }

@keyframes hb-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%       { opacity: 0.4; transform: scale(0.85); }
}
@keyframes hb-blink {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0; }
}

/* ── OFFLINE BANNER ───────────────────────────────────── */
#daemon-offline-banner {
  display: none;
  background: var(--red); color: #fff;
  padding: 7px 16px; text-align: center;
  font-size: 12px; font-weight: bold;
  border-bottom: 2px solid rgba(0,0,0,0.25);
  letter-spacing: 0.03em;
}
#daemon-offline-banner.visible { display: block; }
#onair-banner {
  display: none;
  background: var(--red);
  color: #fff;
  padding: 8px 16px;
  text-align: center;
  font-size: var(--fs-sm);
  font-weight: bold;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  border-bottom: 2px solid rgba(0,0,0,0.3);
  animation: onair-pulse 1s ease-in-out infinite alternate;
}
#onair-banner.visible { display: block; }
@keyframes onair-pulse {
  from { opacity: 1.0; }
  to   { opacity: 0.6; }
}
#theme-btn { background: none; border: 1px solid var(--border); color: var(--text2);
             padding: 3px 8px; border-radius: 4px; cursor: pointer; font-size: var(--fs-sm); }
#theme-btn:hover { border-color: var(--accent); color: var(--accent); }
#callsign-badge { background: var(--bg3); border: 1px solid var(--border);
                  padding: 3px 10px; border-radius: 12px; font-size: var(--fs-sm);
                  color: var(--accent); font-weight: bold; }

/* ── TABS ── */
nav { background: var(--bg2); border-bottom: 1px solid var(--border); display: flex; gap: 2px; padding: 0 8px; }
nav button { background: none; border: none; color: var(--text2); padding: 10px 16px;
             cursor: pointer; font-family: inherit; font-size: var(--fs-base); border-bottom: 2px solid transparent; }
nav button:hover  { color: var(--text); }
nav button.active { color: var(--accent); border-bottom-color: var(--accent); }

/* ── MAIN ── */
main { padding: 16px; max-width: 1200px; }
.tab-panel { display: none; }
.tab-panel.active { display: block; }

/* ── AUDIO-METER (RX-Eingangs-Diagnose) ── */
#audio-meter { background: var(--bg2); border: 1px solid var(--border);
               border-radius: 6px; padding: 8px 12px; margin-bottom: 12px; }
#audio-meter .am-row { display: flex; align-items: center; gap: 10px;
                        font-size: var(--fs-xs); color: var(--text2); margin: 3px 0; }
#audio-meter .am-label { width: 38px; flex-shrink: 0; color: var(--text2); }
#audio-meter .am-bar   { flex: 1; height: 10px; background: var(--bg3);
                          border: 1px solid var(--border); border-radius: 3px;
                          overflow: hidden; position: relative; }
#audio-meter .am-fill  { height: 100%; background: var(--green);
                          width: 0%; transition: width .15s linear; }
#audio-meter .am-fill.warn { background: var(--orange); }
#audio-meter .am-fill.clip { background: var(--red); }
#audio-meter .am-val   { width: 70px; flex-shrink: 0; text-align: right;
                          font-family: var(--ui-font);
                          color: var(--text); font-size: var(--fs-xs); }
#audio-meter .am-hdr   { display: flex; justify-content: space-between;
                          align-items: center; font-size: var(--fs-xs);
                          color: var(--text2); margin-bottom: 4px; }
#audio-meter .am-status { color: var(--text); font-weight: bold; }
#audio-meter.silent  .am-status { color: var(--text2); }
#audio-meter.ok      .am-status { color: var(--green); }
#audio-meter.weak    .am-status { color: var(--orange); }
#audio-meter.clip    .am-status { color: var(--red); }
#audio-meter.nosig   .am-status { color: var(--red); }

/* ── CHANNEL GRID ── */
#channel-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 16px; }
.ch-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
           padding: 8px 10px; cursor: pointer; transition: border-color .15s, box-shadow .15s; }
.ch-card:hover { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(74,144,217,.25); }
.ch-card.home    { border-color: var(--accent); }
.ch-card.emerg-active { border: 2px solid #e24b4a !important; background: rgba(226,75,74,.06); }
.ch-card.active  { border-color: var(--green); background: rgba(63,185,80,.08); }
.ch-card .ch-num { font-size: var(--fs-lg); font-weight: bold; color: var(--accent); }
.ch-card .ch-freq { font-size: var(--fs-xs); color: var(--text2); margin-top: 2px; }
.ch-card .ch-last { font-size: var(--fs-xs); color: var(--text); margin-top: 6px; min-height: 14px;
                    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ch-card .ch-info { font-size: var(--fs-xxs); color: var(--text2); margin-top: 2px; }

/* ── FRAME FEED ── */
#rx-feed { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
           height: 320px; overflow-y: auto; padding: 8px; }
.frame-row { padding: 4px 6px; border-bottom: 1px solid var(--border); display: flex;
             gap: 10px; align-items: baseline; font-size: var(--fs-sm); }
.frame-row:last-child { border-bottom: none; }
.frame-row .ts   { color: var(--text2); white-space: nowrap; }
.frame-row .ch   { color: var(--accent); width: 20px; text-align: center; }
.frame-row .from { color: var(--blue); font-weight: bold; width: 85px; }
.frame-row .type { color: var(--green); width: 90px; }
.frame-row .snr  { width: 58px; text-align: right; font-weight: bold; font-size: var(--fs-xs); white-space: nowrap; }
.frame-row .off  { color: var(--text2); width: 52px; text-align: right; font-size: var(--fs-xs); white-space: nowrap; }
.frame-row .data { color: var(--text); flex: 1; }
.frame-row.emergency .type { color: var(--red); font-weight: bold; }
.frame-row.emergency      { background: rgba(248,81,73,.08); }
.frame-row.testframe      { border-left: 3px solid #1f6feb; }
.test-pill { display:inline-block; background:#1f6feb; color:#fff !important;
  font-size:var(--fs-xxs); font-weight:bold; padding:1px 6px; border-radius:3px;
  letter-spacing:.5px; white-space:nowrap; vertical-align:middle; flex-shrink:0; }
/* AUTH-Badge: nur Emoji 🔑, kein Hintergrund (wie Deep-Badge 🔍) */
.auth-pill { font-size:0.9em; margin-left:2px; vertical-align:middle; cursor:default; }
.frame-row { cursor: pointer; }
.frame-row:hover { background: rgba(255,255,255,.04); }

/* ── MODAL ── */
#frame-modal { display:none; position:fixed; inset:0; z-index:1000;
  background:rgba(0,0,0,.82); align-items:center; justify-content:center; }
#frame-modal.open { display:flex; }
#frame-modal-box { background:var(--bg3); border:2px solid var(--accent);
  border-radius:8px; min-width:360px; max-width:560px;
  width:90vw; max-height:80vh; overflow-y:auto; position:relative; }
#frame-modal-box h3 { font-size:var(--fs-sm); font-weight:bold; margin:0; padding:10px 40px 10px 16px;
  color:var(--bg); background:var(--accent); border-radius:5px 5px 0 0;
  letter-spacing:.5px; }
#frame-modal-body { padding:14px 16px 16px; }
#frame-modal-close { position:absolute; top:8px; right:12px; background:none;
  border:none; color:var(--bg); font-size:var(--fs-lg); cursor:pointer; line-height:1;
  opacity:.8; }
#frame-modal-close:hover { opacity:1; }
.modal-row { display:flex; gap:8px; padding:4px 0;
  border-bottom:1px solid var(--border); font-size:var(--fs-sm); }
.modal-row:last-child { border-bottom:none; }
.modal-key { color:var(--text2); width:140px; flex-shrink:0; }
.modal-val { color:var(--text); word-break:break-all; font-weight:bold; }
.modal-map { display:inline-block; margin-top:10px; font-size:var(--fs-xs);
  color:var(--accent); text-decoration:none; }
.modal-map:hover { text-decoration:underline; }
[data-theme="light"] #frame-modal-box { background:#f6f8fa; }

/* ── AUDIO TOGGLES ── */
.audio-toggles { display:flex; gap:16px; align-items:center;
  margin-bottom:8px; flex-wrap:wrap; }
.toggle-sw { display:flex; align-items:center; gap:7px;
  cursor:pointer; font-size:var(--fs-xs); color:var(--text2); user-select:none; }
.toggle-sw input[type=checkbox] { accent-color:var(--accent);
  width:14px; height:14px; cursor:pointer; }
.toggle-sw:hover { color:var(--text); }
.snr-hi  { color: #3fb950; }   /* > 15 dB  — stark */
.test-badge { background: #1f6feb; color: #fff; font-size: var(--fs-xxs); font-weight: bold;
  padding: 1px 5px; border-radius: 3px; letter-spacing: .5px; vertical-align: middle;
  margin-left: 4px; }
.snr-mid { color: #e3b341; }   /* 8–15 dB  — ok    */
.snr-lo  { color: #f85149; }   /* < 8 dB   — schwach */

/* ── SECTION TITLES ── */
h2 { font-size: var(--fs-base); color: var(--text2); text-transform: uppercase;
     letter-spacing: 1px; margin-bottom: 8px; margin-top: 16px; }
h2:first-child { margin-top: 0; }

/* ── TX FORMS ── */
.tx-form { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
           padding: 16px; max-width: 520px; }
.tx-form.hidden { display: none; }
.tx-form.form-p4 { border-color: var(--green); }
.tx-form.form-p3 { border-color: var(--blue);  }
.tx-form.form-p2 { border-color: var(--orange);}
.tx-form.form-p1 { border-color: var(--red);   }
.tx-unavailable { background: rgba(255,166,87,.12); border: 1px solid var(--orange);
  border-radius: 6px; padding: 10px 12px; margin-bottom: 14px; color: var(--orange);
  font-size: var(--fs-sm); }
.tx-unavailable.hidden { display: none; }
.hidden { display: none; }
.tx-unavailable code { background: var(--bg3); color: var(--text); padding: 1px 6px;
  border-radius: 3px; font-family: var(--ui-font); }
/* Senden-Tab im Monitor-Modus: Bedienelemente sichtbar deaktiviert */
.tab-panel.tx-disabled .tx-btn,
.tab-panel.tx-disabled .btn { opacity: .4; pointer-events: none; }
.field-row { display: flex; gap: 8px; margin-bottom: 8px; align-items: center; flex-wrap: wrap; }
.field-row label { color: var(--text2); width: 120px; flex-shrink: 0; font-size: var(--fs-sm); }
.field-row input, .field-row select {
  background: var(--bg3); border: 1px solid var(--border); color: var(--text);
  padding: 5px 8px; border-radius: 4px; font-family: inherit; font-size: var(--fs-sm);
  flex: 1; min-width: 0; }
.field-row input:focus, .field-row select:focus {
  outline: none; border-color: var(--accent); }
.field-row .unit { color: var(--text2); font-size: var(--fs-xs); white-space: nowrap; }
.btn { background: var(--accent); color: #000; border: none; padding: 7px 20px;
       border-radius: 4px; cursor: pointer; font-family: inherit; font-size: var(--fs-base);
       font-weight: bold; margin-top: 8px; }
.btn:hover { filter: brightness(1.1); }
.btn.danger { background: var(--red); color: #fff; }
.btn.secondary { background: var(--bg3); color: var(--text); border: 1px solid var(--border); font-weight: normal; }
.btn.secondary:hover { border-color: var(--accent); }
#tx-result { margin-top: 8px; font-size: var(--fs-sm); padding: 6px 10px; border-radius: 4px; display: none; }
#tx-result.ok  { background: rgba(63,185,80,.15); color: var(--green); }
#tx-result.err { background: rgba(248,81,73,.15); color: var(--red); }
/* ── TX GRUPPEN-SELEKTOR ── */
.tx-groups { margin-bottom: 14px; display: flex; flex-direction: column; gap: 16px; }
.tx-group { background: var(--bg2); border: 1px solid var(--border);
  border-radius: 6px; padding: 10px 12px; }
.tx-group.p4-group { border-left: 3px solid var(--green); }
.tx-group.p3-group { border-left: 3px solid var(--blue); }
.tx-group.p2-group { border-left: 3px solid var(--orange); }
.tx-group.p1-group { border-left: 3px solid var(--red); }
.tx-group-hdr { display: flex; align-items: center; gap: 7px; margin-bottom: 5px; }
.tx-prio-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.tx-prio-name { font-size: var(--fs-xxs); text-transform: uppercase; letter-spacing: 1.2px; font-weight: bold; }
.tx-prio-info { font-size: var(--fs-xs); color: var(--text2); }
.tx-prio-info .cd { color: var(--text); font-weight: bold; }
.tx-btn-row { display: flex; gap: 6px; flex-wrap: wrap; }
.tx-btn { background: var(--bg3); border: 1px solid var(--border); color: var(--text2);
  padding: 5px 12px; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: var(--fs-sm); }
.tx-btn:hover:not(:disabled) { color: var(--text); border-color: var(--text2); }
.tx-btn.active   { border-color: var(--accent); color: var(--accent); }
.tx-btn.p1-btn.active { border-color: var(--red);    color: var(--red); }
.p4-col { color: var(--green); }   .p4-dot { background: var(--green); }
.p3-col { color: var(--blue); }    .p3-dot { background: var(--blue); }
.p2-col { color: var(--orange); }  .p2-dot { background: var(--orange); }
.p1-col { color: var(--red); }     .p1-dot { background: var(--red); }

/* ── TX-WARTESCHLANGE ── */
#tx-queue { background: var(--bg2); border: 1px solid var(--border);
  border-radius: 6px; padding: 6px 8px; max-width: 640px; }
.txq-empty { color: var(--text2); font-size: var(--fs-sm); padding: 6px 4px; }
.txq-row { display: flex; align-items: center; gap: 10px; padding: 7px 6px;
  border-bottom: 1px solid var(--border); font-size: var(--fs-sm); }
.txq-row:last-child { border-bottom: none; }
.txq-row.next { background: rgba(230,168,23,.08); border-radius: 4px; }
.txq-row .tx-prio-dot { width: 8px; height: 8px; }
.txq-type { flex: 1; color: var(--text); font-weight: bold; min-width: 0;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.txq-prio { font-size: var(--fs-xxs); font-weight: bold; letter-spacing: .5px;
  width: 22px; text-align: center; flex-shrink: 0; }
.txq-cd { color: var(--accent); font-weight: bold; width: 92px;
  text-align: right; flex-shrink: 0; white-space: nowrap; }
.txq-cd.now { color: var(--green); }
.txq-at { color: var(--text2); font-size: var(--fs-xs); width: 78px;
  text-align: right; flex-shrink: 0; white-space: nowrap; }
@media (max-width: 640px) { .txq-at { display: none; } }

/* ── STATUS CARDS ── */
#status-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.stat-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px; padding: 12px 14px; }
.stat-card .key { color: var(--text2); font-size: var(--fs-xs); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }
.stat-card .val { color: var(--text); font-size: var(--fs-lg); font-weight: bold; }
.stat-card .val.accent { color: var(--accent); }
.stat-card .val.green  { color: var(--green); }

/* ── LOG ── */
#log-feed { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
            height: 420px; overflow-y: auto; padding: 8px; font-size: var(--fs-xs); }
.log-line { padding: 2px 4px; border-radius: 3px; }
.log-line.WARNING { color: #e3b341; }
.log-line.ERROR   { color: var(--red); }
.log-line.DEBUG   { color: var(--text2); }
.log-line.INFO    { color: var(--text); }
#log-controls { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }
#log-controls select { background: var(--bg3); border: 1px solid var(--border); color: var(--text);
  padding: 4px 8px; border-radius: 4px; font-family: inherit; font-size: var(--fs-sm); }
#autoscroll-toggle { display: flex; align-items: center; gap: 6px; cursor: pointer; color: var(--text2); font-size: var(--fs-sm); }
#autoscroll-toggle input { accent-color: var(--accent); }

/* ── SPLIT CARD (Heimatkanal / TX-Offset) ── */
.stat-card-split { display: flex; padding: 0; overflow: hidden; }
.split-half      { flex: 1; padding: 12px 12px; }
.split-divider   { width: 1px; background: var(--border); flex-shrink: 0; align-self: stretch; }
.split-sub       { font-size: var(--fs-xxs); color: var(--text2); margin-top: 2px; font-weight: normal; }

/* ── AUDIO-EINSTELLUNGEN ─────────────────────────────────────────── */
.audio-cfg-card { background: var(--bg2); border: 1px solid var(--border);
                  border-radius: 6px; padding: 14px 16px; max-width: 640px; }
.audio-cfg-card h3 { font-size: var(--fs-sm); color: var(--text2);
                     text-transform: uppercase; letter-spacing: 1px;
                     margin-bottom: 10px; }
.audio-cfg-row { display: flex; gap: 10px; margin-bottom: 10px;
                 align-items: center; flex-wrap: wrap; }
.audio-cfg-row label { width: 130px; font-size: var(--fs-sm); color: var(--text2);
                       flex-shrink: 0; }
.audio-cfg-row select { flex: 1; min-width: 240px; background: var(--bg3);
                        border: 1px solid var(--border); color: var(--text);
                        padding: 6px 10px; border-radius: 4px;
                        font-family: inherit; font-size: var(--fs-sm); }
.audio-cfg-row select:focus { outline: none; border-color: var(--accent); }
.audio-cfg-note { font-size: var(--fs-xs); color: var(--text2); margin: 6px 0 12px;
                  padding: 8px 10px; background: var(--bg3);
                  border-left: 2px solid var(--accent); border-radius: 3px; }
.audio-cfg-actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
#audio-cfg-result { margin-top: 10px; font-size: var(--fs-sm); padding: 8px 10px;
                    border-radius: 4px; display: none; }
#audio-cfg-result.ok  { background: rgba(63,185,80,.12);  color: var(--green); }
#audio-cfg-result.err { background: rgba(248,81,73,.12);  color: var(--red); }
#audio-cfg-result.warn{ background: rgba(255,166,87,.12); color: var(--orange); }

/* ── STATUS & CONFIG — Abschnitts-Überschriften ──────────────────── */
#tab-status > h3, #tab-inbox > h3 {
  font-size: var(--fs-base); color: var(--text2); text-transform: uppercase;
  letter-spacing: 1px; margin: 22px 0 10px; }
#tab-status > h3:first-child { margin-top: 0; }
#cfg-audio-status { margin-top: 10px; font-size: var(--fs-sm); min-height: 14px; }
.status-cfg-row { display: flex; align-items: center; gap: 12px;
                  margin-bottom: 8px; }
.cfg-label { color: var(--text2); font-size: var(--fs-sm);
             width: 110px; flex-shrink: 0; }
.status-cfg-row select { background: var(--bg3); border: 1px solid var(--border);
                         color: var(--text); padding: 4px 8px; border-radius: 4px;
                         font-family: var(--ui-font); font-size: var(--fs-sm);
                         min-width: 180px; }

/* ── EMPFANGEN (INBOX) ───────────────────────────────────────────── */
.inbox-badge { display: inline-block; min-width: 16px; height: 16px;
  padding: 0 4px; border-radius: 8px; background: var(--red); color: #fff;
  font-size: var(--fs-xxs); font-weight: bold; line-height: 16px; text-align: center;
  vertical-align: middle; }
.inbox-badge.hidden { display: none; }
#inbox-list { display: flex; flex-direction: column; gap: 6px; }
.inbox-item { background: var(--bg); border: 1px solid var(--border);
  border-radius: 6px; padding: 8px 10px; cursor: pointer; display: flex;
  gap: 10px; align-items: baseline; font-size: var(--fs-sm);
  transition: border-color .2s; }
.inbox-item:hover { border-color: var(--accent); }
.inbox-item.inbox-unread { background: var(--bg2);
  border-left: 3px solid var(--accent); }
.inbox-item .ib-ts   { color: var(--text2); white-space: nowrap; }
.inbox-item .ib-from { color: var(--blue); font-weight: bold;
  white-space: nowrap; }
.inbox-item .ib-type { font-size: var(--fs-xxs); font-weight: bold; padding: 1px 6px;
  border-radius: 3px; background: var(--bg3); border: 1px solid var(--border);
  color: var(--text2); letter-spacing: .5px; white-space: nowrap;
  flex-shrink: 0; }
.inbox-item .ib-type.multi { color: var(--accent); border-color: var(--accent); }
.inbox-item .ib-preview { color: var(--text); flex: 1; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }

/* ── GENERISCHES MODAL (Inbox-Detail) ────────────────────────────── */
.modal-overlay { display: none; position: fixed; inset: 0; z-index: 1000;
  background: rgba(0,0,0,.82); align-items: center; justify-content: center; }
.modal-overlay.open { display: flex; }
.modal-box { background: var(--bg3); border: 2px solid var(--accent);
  border-radius: 8px; min-width: 340px; max-width: 620px; width: 90vw;
  max-height: 80vh; overflow-y: auto; position: relative; padding: 16px 18px; }
[data-theme="light"] .modal-box { background: #f6f8fa; }
.modal-box h3 { font-size: var(--fs-base); color: var(--accent); margin-bottom: 4px; }
.modal-close { position: absolute; top: 8px; right: 12px; background: none;
  border: none; color: var(--text2); font-size: var(--fs-lg); cursor: pointer;
  line-height: 1; }
.modal-close:hover { color: var(--accent); }
.seq-table { width: 100%; border-collapse: collapse; font-size: var(--fs-sm);
  margin-top: 8px; }
.seq-table th, .seq-table td { text-align: left; padding: 4px 8px;
  border-bottom: 1px solid var(--border); }
.seq-table th { color: var(--text2); font-weight: normal; }

/* ── Inbox Antwort-Bereich ───────────────────────── */
.inbox-reply-box {
  margin-top: 14px;
  border-top: 1px solid var(--border);
  padding-top: 12px;
}
.inbox-reply-box label {
  display: block;
  color: var(--text2);
  font-size: var(--fs-sm);
  margin-bottom: 4px;
}
.inbox-reply-box textarea {
  width: 100%;
  min-height: 72px;
  background: var(--bg2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 6px 8px;
  font-family: inherit;
  font-size: var(--fs-sm);
  resize: vertical;
  box-sizing: border-box;
}
.inbox-reply-box textarea:focus {
  outline: none;
  border-color: var(--accent);
}
.inbox-reply-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}
.inbox-reply-counter {
  color: var(--text2);
  font-size: var(--fs-xs);
  flex: 1;
}
.inbox-reply-counter.warn { color: var(--orange, #f0a050); }
.inbox-reply-status {
  font-size: var(--fs-xs);
  min-height: 1.2em;
  margin-top: 4px;
}
.inbox-reply-status.ok  { color: var(--green, #3fb950); }
.inbox-reply-status.err { color: var(--red,   #f85149); }

/* ── MOBILE / RESPONSIVE ─────────────────────────────────────────── */
@media (max-width: 640px) {
  header { padding: 8px 10px; gap: 8px; flex-wrap: wrap; }
  header h1 span { display: none; }                /* Untertitel auf kleinen Screens weglassen */
  main { padding: 10px 8px; }
  nav { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  nav button { padding: 8px 10px; font-size: var(--fs-xs); white-space: nowrap; }
  #channel-grid { grid-template-columns: repeat(2, 1fr); }  /* 4→2 Spalten */
  #status-grid  { grid-template-columns: repeat(2, 1fr); }  /* 3→2 Spalten */
  .stat-card-split { flex-direction: column; }               /* Split-Card untereinander */
  .split-divider   { width: auto; height: 1px; }
  .field-row label { width: 80px; font-size: var(--fs-xs); }
  .tx-form { max-width: 100%; }
  .tx-group-hdr { flex-wrap: wrap; }
  .tx-prio-info { width: 100%; padding-left: 15px; margin-top: 2px; }
  #rx-feed  { height: 200px; }
  #log-feed { height: 260px; }
}

/* ── Config Sub-Tabs ─────────────────────────────────────── */
.cfg-subnav {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 0;
  padding: 0;
}
.cfg-subnav button {
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 6px 16px;
  color: var(--text2);
  cursor: pointer;
  font-size: var(--fs-sm);
  font-family: inherit;
  transition: color .15s, background .15s, border-color .15s;
  white-space: nowrap;
}
.cfg-subnav button:hover {
  color: var(--text);
  border-color: var(--accent);
  background: var(--bg2);
}
.cfg-subnav button.active {
  color: var(--accent);
  border-color: var(--accent);
  background: var(--bg2);
  font-weight: bold;
}
.cfg-subpanel { display: none; }
.cfg-subpanel.active { display: block; }

/* ── Hamlib-Sektion ──────────────────────────────────────── */
.hamlib-status-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  font-size: var(--fs-sm);
}
.hamlib-status-dot {
  width: 10px; height: 10px;
  border-radius: 50%;
  background: var(--text2);
  flex-shrink: 0;
}
.hamlib-status-dot.ok  { background: var(--green); }
.hamlib-status-dot.err { background: var(--red); }

/* ── Fragment-Collapsing ─────────────────────────────────── */
.frag-row { display: none; }
.frag-row.expanded { display: flex; }
.frag-toggle {
  cursor: pointer;
  font-size: 0.7em;
  color: var(--text2);
  margin-right: 4px;
  user-select: none;
  flex-shrink: 0;
  width: 12px;
  display: inline-block;
  text-align: center;
}
.assembled-row { cursor: pointer; }
.assembled-row:hover { background: var(--bg3); }

/* ── MeshCore Bridge Badge ───────────────────────────────── */
.mc-badge {
  background: #5b4fcf;
  color: #fff;
  font-size: 0.65em;
  padding: 1px 5px;
  border-radius: 3px;
  vertical-align: middle;
  margin-right: 4px;
  font-weight: bold;
  letter-spacing: 0.5px;
  font-family: monospace;
}
.frame-row.meshcore {
  border-left: 3px solid #5b4fcf;
  padding-left: 4px;
}
.hb-warn .hb-dot { background: var(--yellow, #f5a623) !important; }
</style>
</head>
<body>

<header>
  <h1>GUST <span>Generic Universal Shortwave Telemetry</span></h1>
  <span id="callsign-badge">–</span>
  <span id="ws-indicator" title="WebSocket Status"></span>
  <div id="daemon-hb" class="hb-unknown" title="GUST Daemon Status">
    <span class="hb-dot"></span>
    <span class="hb-label">DAEMON</span>
  </div>
  <div id="mc-bridge-status" class="hb-unknown" title="MeshCore Bridge" style="display:none">
    <span class="hb-dot"></span>
    <span class="hb-label">MC</span>
  </div>
  <button id="theme-btn" onclick="toggleTheme()" title="Theme wechseln">🌙 Light</button>
</header>
<div id="daemon-offline-banner"></div>
<div id="onair-banner">📡 ON AIR</div>

<nav>
  <button class="active" onclick="switchTab('monitor',this)" data-i18n="nav.monitor">📡 Monitor</button>
  <button onclick="switchTab('tx',this)" data-i18n="nav.send">📤 Senden</button>
  <button onclick="switchTab('inbox',this)"><span data-i18n="nav.inbox">💬 Kommunikation</span> <span id="inbox-badge" class="inbox-badge hidden">0</span></button>
  <button onclick="switchTab('status',this)" data-i18n="nav.status">⚙ Status &amp; Config</button>
  <button onclick="switchTab('log',this)" data-i18n="nav.log">🗒 Log</button>
  <button onclick="switchTab('stresstest',this)" data-i18n="nav.stresstest">🧪 Stresstest</button>
</nav>

<main>

<!-- ══════════════════════════════════════════════════════ TAB: MONITOR -->
<div id="tab-monitor" class="tab-panel active">

  <!-- Sub-Tab-Leiste: Liste / Swimlane -->
  <div id="monitor-subtabs"
       style="display:flex;gap:6px;margin-bottom:14px;">
    <button id="mon-btn-list"
            class="btn active"
            onclick="monSwitchTab('list')">📋 Liste</button>
    <button id="mon-btn-swimlane"
            class="btn secondary"
            onclick="monSwitchTab('swimlane')">📡 Swimlane</button>
  </div>

  <!-- ── Sub-Panel: Listen-Ansicht (bisheriger Monitor-Inhalt) ── -->
  <div id="mon-panel-list">
  <h2 data-i18n="monitor.audio_in">Audio-Eingang (RX)</h2>
  <div id="audio-meter" class="nosig">
    <div class="am-hdr">
      <span>🎤 <span id="am-device">–</span></span>
      <span class="am-status" id="am-status">Kein Audio-Signal</span>
    </div>
    <div class="am-row">
      <span class="am-label">RMS</span>
      <div class="am-bar"><div class="am-fill" id="am-rms-fill"></div></div>
      <span class="am-val" id="am-rms-val">–</span>
    </div>
    <div class="am-row">
      <span class="am-label">Peak</span>
      <div class="am-bar"><div class="am-fill" id="am-peak-fill"></div></div>
      <span class="am-val" id="am-peak-val">–</span>
    </div>
  </div>

  <h2 data-i18n="monitor.channels">Kanalübersicht — 8 Kanäle (600–2600 Hz NF)</h2>
  <div id="channel-grid"></div>

  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;flex-wrap:wrap;gap:8px;">
    <h2 style="margin:0;" data-i18n="monitor.feed.title">Live RX-Feed</h2>
    <div style="display:flex;gap:14px;align-items:center;">
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer;color:var(--text2);font-size:var(--fs-sm);"
             title="Verhindert Dekodierung des eigenen Sendesignals (TRX-Monitor-Schutz)">
        <input type="checkbox" id="ignore-rx-while-tx" style="accent-color:var(--accent);">
        Ignore Decodes while Sending
      </label>
      <label id="autoscroll-toggle" data-i18n="monitor.autoscroll"><input type="checkbox" id="autoscroll" checked> Auto-Scroll</label>
      <select id="feed-height-sel" onchange="setFeedHeight(this.value)"
              title="Anzeigehöhe des Live-Feeds"
              style="background:var(--bg2);color:var(--text);border:1px solid var(--border);border-radius:4px;font-size:var(--fs-sm);padding:2px 4px;">
        <option value="200">Klein</option>
        <option value="320" selected>Mittel</option>
        <option value="550">Groß</option>
        <option value="800">Sehr groß</option>
      </select>
    </div>
  </div>
  <div class="audio-toggles">
    <label class="toggle-sw">
      <input type="checkbox" id="toggle-audio-emerg">
      🔔 <span data-i18n="monitor.sound_emerg">Ton bei Emergency</span>
    </label>
    <label class="toggle-sw">
      <input type="checkbox" id="toggle-audio-mine">
      🔔 <span data-i18n="monitor.sound_msg">Ton bei Nachricht für mich</span>
    </label>
  </div>
  <div id="rx-feed">
    <div style="color:var(--text2);padding:8px;" data-i18n="monitor.feed.empty">Warte auf RX-Frames …</div>
  </div>
  </div><!-- /mon-panel-list -->

  <!-- ── Sub-Panel: Swimlane (Live-Visualisierung) ── -->
  <div id="mon-panel-swimlane" style="display:none">

    <!-- Toolbar -->
    <div style="display:flex;gap:12px;align-items:center;
                flex-wrap:wrap;margin-bottom:10px">

      <!-- Zoom (Zeitfenster ist fix 600s) -->
      <label style="color:var(--text2);font-size:var(--fs-sm)">
        Zoom:
        <select id="sl-zoom-select"
                onchange="slSetZoom(this.value)"
                style="margin-left:4px;background:var(--bg2);
                       color:var(--text);
                       border:1px solid var(--border);
                       border-radius:4px;padding:2px 6px">
          <option value="6" selected>Übersicht  (6 px/s)</option>
          <option value="10">Detail     (10 px/s)</option>
        </select>
      </label>
      <span style="color:var(--text2);font-size:var(--fs-xs)">
        600 s</span>

      <!-- Pause/Resume -->
      <button id="sl-pause-btn" class="btn"
              onclick="slTogglePause()"
              style="min-width:90px">⏸ Pause</button>

      <!-- PNG Export -->
      <button class="btn secondary"
              onclick="slExport()">⬇ PNG</button>

      <!-- Frame-Zähler -->
      <span id="sl-counter"
            style="color:var(--text2);font-size:var(--fs-xs);
                   margin-left:auto">0 Frames</span>
    </div>

    <!-- Canvas-Container: Scroll ist Canvas-intern (scrollOffsetPx) -->
    <div id="sl-container"
         style="overflow-x:auto;overflow-y:hidden;
                background:#0D1117;border-radius:6px;
                border:1px solid var(--border)">
      <canvas id="sl-canvas"
              style="display:block;outline:none"
              tabindex="0"
              onclick="this.focus()"></canvas>
    </div>

    <!-- Legende -->
    <div id="sl-legend"
         style="display:flex;flex-wrap:wrap;gap:8px;
                margin-top:8px;font-size:var(--fs-xs)"></div>

  </div><!-- /mon-panel-swimlane -->
</div>

<!-- ══════════════════════════════════════════════════════ TAB: SENDEN -->
<div id="tab-tx" class="tab-panel">
  <div id="tx-unavailable" class="tx-unavailable hidden">
    📡 <b>Monitor-Modus</b> — diese Station kann nicht senden (kein TX-Gateway aktiv).
    Starte GUST als Daemon, um zu senden:
    <code>py gust.py daemon</code>
  </div>
  <h2>One-Shot TX</h2>
<div class="tx-groups">

  <div class="tx-group p4-group">
    <div class="tx-group-hdr">
      <span class="tx-prio-dot p4-dot"></span>
      <span class="tx-prio-name p4-col" data-i18n="send.group.telemetry">Telemetrie</span>
      <span class="tx-prio-info">P4 · <span data-i18n="send.p4.schedule">Schedule: alle</span> <span id="p4-interval">5 min</span> <span data-i18n="send.p4.next">— nächster Schedule in</span> <span class="cd" id="p4-next">–</span></span>
    </div>
    <div class="tx-btn-row">
      <button class="tx-btn active" onclick="selectTxType('weather',this)" data-i18n="tab.send.weather">🌤 Wetter</button>
    </div>
  </div>

  <div class="tx-group p3-group">
    <div class="tx-group-hdr">
      <span class="tx-prio-dot p3-dot"></span>
      <span class="tx-prio-name p3-col" data-i18n="send.group.navigation">Navigation</span>
      <span class="tx-prio-info">P3 · <span data-i18n="send.p3.next">nächster Schedule in</span> <span class="cd" id="p3-next">–</span></span>
    </div>
    <div class="tx-btn-row">
      <button class="tx-btn" onclick="selectTxType('position',this)" data-i18n="tab.send.position">📍 Position</button>
    </div>
  </div>

  <div class="tx-group p2-group">
    <div class="tx-group-hdr">
      <span class="tx-prio-dot p2-dot"></span>
      <span class="tx-prio-name p2-col" data-i18n="send.group.communication">Kommunikation</span>
      <span class="tx-prio-info" data-i18n="send.p2.label">P2 · Sendung ≤ 30 s nach Einreihung</span>
    </div>
    <div class="tx-btn-row">
      <button class="tx-btn" onclick="selectTxType('text',this)" data-i18n="tab.send.text">💬 Freitext</button>
    </div>
  </div>

  <div class="tx-group p1-group">
    <div class="tx-group-hdr">
      <span class="tx-prio-dot p1-dot"></span>
      <span class="tx-prio-name p1-col" data-i18n="send.group.emergency">Notfall</span>
      <span class="tx-prio-info" data-i18n="send.p1.label">P1 · sofort — überspringt Cooldown</span>
    </div>
    <div class="tx-btn-row">
      <button class="tx-btn p1-btn" onclick="selectTxType('emergency',this)" data-i18n="tab.send.emergency">🆘 Notfall-Beacon</button>
    </div>
  </div>

</div>

  <!-- Wetter-Formular -->
  <div id="form-weather" class="tx-form form-p4">
    <div class="field-row"><label data-i18n="send.weather.temp">Temperatur</label>
      <input type="number" id="w-temp" value="20.0" step="0.1"><span class="unit">°C</span></div>
    <div class="field-row"><label data-i18n="send.weather.hum">Luftfeuchte</label>
      <input type="number" id="w-hum" value="65" min="0" max="100"><span class="unit">%</span></div>
    <div class="field-row"><label data-i18n="send.weather.pressure">Luftdruck</label>
      <input type="number" id="w-pres" value="1013.2" step="0.1"><span class="unit">hPa</span></div>
    <div class="field-row"><label data-i18n="send.weather.wind">Windgeschw.</label>
      <input type="number" id="w-wind" value="15" min="0"><span class="unit">km/h</span></div>
    <div class="field-row"><label data-i18n="send.weather.wdir">Windrichtung</label>
      <input type="number" id="w-wdir" value="270" min="0" max="359"><span class="unit">°</span></div>
    <div class="field-row"><label data-i18n="send.weather.rain">Niederschlag</label>
      <input type="number" id="w-rain" value="0.0" step="0.1"><span class="unit">mm/h</span></div>
    <div class="field-row"><label data-i18n="send.weather.uv">UV-Index</label>
      <input type="number" id="w-uv" value="3" min="0" max="15"></div>
    <div style="display:flex;gap:8px;align-items:center;">
      <button class="btn" onclick="sendTx('weather')" data-i18n="send.btn.submit.weather">Wetter senden</button>
      <button class="btn secondary" type="button" onclick="clearForm('weather')" data-i18n="send.btn.clear">Löschen</button>
    </div>
  </div>

  <!-- Position-Formular -->
  <div id="form-position" class="tx-form form-p3 hidden">
    <div class="field-row"><label>Latitude</label>
      <input type="number" id="p-lat" value="48.2082" step="0.0001"><span class="unit">°</span></div>
    <div class="field-row"><label>Longitude</label>
      <input type="number" id="p-lon" value="16.3738" step="0.0001"><span class="unit">°</span></div>
    <div class="field-row"><label>Altitude</label>
      <input type="number" id="p-alt" value="180"><span class="unit">m</span></div>
    <div class="field-row"><label>Speed</label>
      <input type="number" id="p-speed" value="0"><span class="unit">km/h</span></div>
    <div class="field-row"><label>Heading</label>
      <input type="number" id="p-hdg" value="0" min="0" max="359"><span class="unit">°</span></div>
    <div class="field-row"><label>Mobil</label>
      <select id="p-mobile"><option value="0" data-i18n="send.position.no">Nein (Bake)</option><option value="1" data-i18n="send.position.yes">Ja (mobil)</option></select></div>
    <div style="display:flex;gap:8px;align-items:center;">
      <button class="btn" onclick="sendTx('position')" data-i18n="send.btn.submit.position">Position senden</button>
      <button class="btn secondary" type="button" onclick="clearForm('position')" data-i18n="send.btn.clear">Löschen</button>
    </div>
  </div>

  <!-- Text-Formular -->
  <div id="form-text" class="tx-form form-p2 hidden">
    <div class="field-row"><label data-i18n="send.text.to">An (Rufzeichen)</label>
      <input type="text" id="t-to" value="" maxlength="6" style="text-transform:uppercase" placeholder="z.B. OE1XTU"></div>
    <div class="field-row"><label data-i18n="send.text.message">Nachricht</label>
      <input type="text" id="t-msg" value="" maxlength="56"
             placeholder="Nachricht (max. 56 Byte / 4 Frames)"
             oninput="updateTextCounter()"></div>
    <div id="text-counter-row" style="font-size:var(--fs-xs);color:var(--text2);margin-bottom:8px;
         display:flex;gap:16px;align-items:center;">
      <span id="text-byte-count">0 / 56 Byte</span>
      <span id="text-frame-count">1 Frame</span>
      <span id="text-remaining"></span>
    </div>
    <div id="qso-mode-row" style="display:flex;align-items:center;gap:10px;margin-bottom:10px;
         padding:7px 10px;border-radius:4px;border:1px solid var(--border);background:var(--bg2);
         font-size:var(--fs-sm);">
      <label class="toggle-label" style="display:flex;align-items:center;gap:8px;cursor:pointer;margin:0;">
        <input type="checkbox" id="qso-mode-toggle" onchange="toggleQsoMode(this.checked)">
        <span>⚡ <span data-i18n="send.text.qso_mode">QSO-Modus (60 s / Fragment)</span></span>
      </label>
      <span id="qso-mode-hint" style="color:var(--text2);font-size:var(--fs-xs);">
        <span data-i18n="send.text.schedule">Schedule-Intervall:</span> <span id="qso-interval-display">300 s</span>
      </span>
    </div>
    <div id="qso-mode-warning" class="hidden" style="font-size:var(--fs-xs);color:var(--orange,#d97706);
         margin-bottom:8px;padding:4px 8px;border-radius:3px;border:1px solid var(--orange,#d97706);"
         data-i18n="send.text.qso_warning">
      ⚠ QSO-Modus aktiv — nur bei interaktivem Betrieb verwenden. Für automatischen Betrieb deaktivieren.
    </div>
    <div style="display:flex;gap:8px;align-items:center;">
      <button class="btn" onclick="sendTx('text')" data-i18n="send.btn.submit.text">Text senden</button>
      <button class="btn secondary" type="button" onclick="clearForm('text')" data-i18n="send.btn.clear">Löschen</button>
    </div>
  </div>

  <!-- Notfall-Formular -->
  <div id="form-emergency" class="tx-form form-p1 hidden">
    <div style="background:rgba(248,81,73,.12);border:1px solid var(--red);border-radius:4px;
                padding:8px;margin-bottom:12px;color:var(--red);font-size:var(--fs-sm);"
         data-i18n="send.emergency.warning">
      ⚠ Notfall-Frames erhalten Priorität 1 — sofortige Übertragung ohne Cooldown
    </div>
    <div class="field-row"><label>Latitude</label>
      <input type="number" id="e-lat" value="48.2082" step="0.0001"><span class="unit">°</span></div>
    <div class="field-row"><label>Longitude</label>
      <input type="number" id="e-lon" value="16.3738" step="0.0001"><span class="unit">°</span></div>
    <div class="field-row"><label data-i18n="send.emergency.persons">Personen</label>
      <input type="number" id="e-persons" value="1" min="1"></div>
    <div class="field-row"><label data-i18n="send.emergency.injury">Verletzung</label>
      <select id="e-injury">
        <option value="0" data-i18n="send.emergency.unknown">Unbekannt</option><option value="1">Leicht</option>
        <option value="2">Schwer</option><option value="3">Kritisch</option>
      </select></div>
    <div class="field-row"><label data-i18n="send.emergency.prio">Priorität</label>
      <select id="e-prio">
        <option value="1">Mittel</option><option value="2" data-i18n="send.emergency.urgent">Hoch</option>
        <option value="3" selected data-i18n="send.emergency.immediate">Sofort</option>
      </select></div>
    <div class="field-row"><label data-i18n="send.emergency.shorttext">Kurztext (8 Z.)</label>
      <input type="text" id="e-text" value="" maxlength="8" style="text-transform:uppercase" placeholder="z.B. TRAPPED" data-i18n-placeholder="send.emergency.placeholder"></div>
    <div style="display:flex;gap:8px;align-items:center;">
      <button class="btn danger" onclick="sendTx('emergency')" data-i18n="send.emergency.btn">🆘 NOTFALL senden</button>
      <button class="btn secondary" type="button" onclick="clearForm('emergency')" data-i18n="send.btn.clear">Löschen</button>
    </div>
  </div>

  <div id="tx-result"></div>

  <div style="display:flex;align-items:center;justify-content:space-between;margin-top:20px;margin-bottom:6px;">
    <h2 style="margin:0;" data-i18n="send.queue.title">TX-Warteschlange</h2>
    <button id="btn-clear-queue" class="btn secondary" style="font-size:var(--fs-xs);padding:4px 10px;"
            onclick="clearTxQueue()" title="Alle ausstehenden Frames löschen">
      ✕ <span data-i18n="send.queue.clear">Warteschlange löschen</span>
    </button>
  </div>
  <div id="tx-queue">
    <div class="txq-empty" data-i18n="send.queue.empty">Warteschlange leer — keine ausstehenden Frames</div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════ TAB: EMPFANGEN -->
<div id="tab-inbox" class="tab-panel">
  <!-- Sub-Navigation -->
  <div style="display:flex;gap:8px;margin-bottom:14px;border-bottom:1px solid var(--border);padding-bottom:8px;">
    <button id="comm-tab-rx" class="btn active" onclick="switchCommTab('rx')"
            style="font-size:var(--fs-sm);" data-i18n="tab.inbox.rx">📨 Empfangen</button>
    <button id="comm-tab-tx" class="btn secondary" onclick="switchCommTab('tx')"
            style="font-size:var(--fs-sm);" data-i18n="tab.inbox.tx">📤 Gesendet</button>
  </div>

  <!-- Empfangen -->
  <div id="comm-panel-rx">
    <div id="inbox-empty" style="color:var(--text2);padding:8px;" data-i18n="inbox.empty">Keine Nachrichten empfangen.</div>
    <div id="inbox-list"></div>
  </div>

  <!-- Gesendet -->
  <div id="comm-panel-tx" style="display:none;">
    <div id="sent-empty" style="color:var(--text2);padding:8px;" data-i18n="inbox.sent_empty">Noch nichts gesendet.</div>
    <div id="sent-list"></div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════ TAB: STATUS & CONFIG -->
<div id="tab-status" class="tab-panel">
  <h3 data-i18n="status.title">System-Status</h3>
  <div id="status-grid">
    <div class="stat-card"><div class="key" data-i18n="status.callsign">Rufzeichen</div><div class="val accent" id="s-call">–</div></div>
    <div class="stat-card stat-card-split">
      <div class="split-half">
        <div class="key" data-i18n="status.homechannel">Heimatkanal</div>
        <div class="val accent" id="s-ch">–</div>
      </div>
      <div class="split-divider"></div>
      <div class="split-half">
        <div class="key" data-i18n="status.tx_offset">TX-Offset</div>
        <div class="val accent" id="s-ch-offset">–</div>
        <div class="split-sub" id="s-ch-cycle"></div>
      </div>
    </div>
    <div class="stat-card"><div class="key" data-i18n="status.uptime">Uptime</div><div class="val" id="s-uptime">–</div></div>
    <div class="stat-card"><div class="key" data-i18n="status.tx_queue">TX-Queue</div><div class="val" id="s-queue">–</div></div>
    <div class="stat-card"><div class="key" data-i18n="status.last_tx">Letzter TX</div><div class="val" id="s-last-tx">–</div></div>
    <div class="stat-card"><div class="key" data-i18n="status.last_rx">Letzter RX</div><div class="val green" id="s-last-rx">–</div></div>
    <div class="stat-card"><div class="key" data-i18n="status.audio_device">Audio-Gerät</div><div class="val" id="s-audio">–</div></div>
    <div class="stat-card"><div class="key" data-i18n="status.ptt_backend">PTT-Backend</div><div class="val" id="s-ptt">–</div></div>
    <div class="stat-card"><div class="key" data-i18n="status.rx_count">RX-Frames (Session)</div><div class="val green" id="s-rx-count">0</div></div>
  </div>

  <!-- Konfiguration — Trennbereich mit Header -->
  <div style="margin-top:24px;margin-bottom:12px;
              border-top:1px solid var(--border);padding-top:16px;
              display:flex;align-items:center;gap:12px;">
    <h3 style="margin:0;font-size:var(--fs-sm);text-transform:uppercase;
               letter-spacing:1px;color:var(--text2);white-space:nowrap;">
      ⚙ <span data-i18n="cfg.title">Konfiguration</span>
    </h3>
    <div style="flex:1;height:1px;background:var(--border);"></div>
  </div>
  <nav class="cfg-subnav">
    <button class="active" onclick="switchCfgTab('cfg-audio',this)" data-i18n="cfg.tab.audio">🔊 Audio &amp; PTT</button>
    <button onclick="switchCfgTab('cfg-hamlib',this)" data-i18n="cfg.tab.hamlib">📻 Transceiver (Hamlib)</button>
    <button onclick="switchCfgTab('cfg-sdr',this)" data-i18n="cfg.tab.sdr">📡 SDR-TX (SoapySDR)</button>
    <button onclick="switchCfgTab('cfg-display',this)" data-i18n="cfg.tab.display">🎨 Darstellung</button>
  </nav>

  <!-- ── Unterseite: Audio & PTT ── -->
  <div id="cfg-audio" class="cfg-subpanel active">
    <div class="audio-cfg-card">
      <div class="audio-cfg-row">
        <label data-i18n="cfg.audio.rx">Audio-Eingang (RX)</label>
        <select id="cfg-audio-in"><option value="">– Standard / wie TX –</option></select>
      </div>
      <div class="audio-cfg-row">
        <label data-i18n="cfg.audio.tx">Audio-Ausgang (TX)</label>
        <select id="cfg-audio-out"><option value="">– Standard –</option></select>
      </div>
      <div class="audio-cfg-row">
        <label data-i18n="cfg.audio.ptt">PTT-Backend</label>
        <select id="cfg-ptt">
          <option value="null">null (kein PTT)</option>
          <option value="gpio">gpio</option>
          <option value="hamlib">hamlib</option>
        </select>
      </div>
      <div class="audio-cfg-row">
        <label>Deep Decoding</label>
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
          <input type="checkbox" id="cfg-deep-decode" data-saved="0"
                 style="accent-color:var(--accent)">
          <span style="color:var(--text2);font-size:var(--fs-xs)">
            Größere Fenster — liefert verpasste Frames mit ~15s Verzögerung nach
          </span>
        </label>
      </div>
      <div class="audio-cfg-note" data-i18n-html="cfg.audio.info.html">
        <b>TX-Wechsel</b> wirkt sofort beim nächsten Sendevorgang.
        <b>RX-Wechsel</b> erfordert einen Neustart des Daemons —
        der RX-Loop hält das Gerät beim Start fest.
      </div>
      <div class="audio-cfg-actions">
        <button class="btn" onclick="saveAudioConfig()" data-i18n="btn.save">💾 Speichern</button>
        <button class="btn secondary" onclick="loadAudioConfig()" data-i18n="btn.reload">↻ Neu laden</button>
      </div>
      <div id="cfg-audio-status"></div>
    </div>

    <div style="margin-top:16px;background:var(--bg2);border:1px solid var(--border);
         border-radius:6px;padding:14px;width:fit-content;max-width:100%;">
      <h2 style="margin-top:0;margin-bottom:10px;" data-i18n="cfg.audio.ptt_timing">PTT-Timing</h2>
      <div style="display:flex;gap:8px;align-items:center;">
        <label style="color:var(--text2);font-size:var(--fs-sm);width:140px;flex-shrink:0;"
               title="Lead (vor Audio) = Tail (nach Audio) — symmetrisch"
               data-i18n="cfg.audio.ptt_lead">
          PTT Lead/Tail
        </label>
        <input type="number" id="cfg-ptt-delay" value="250" min="0" max="2000" step="10"
               style="background:var(--bg3);border:1px solid var(--border);color:var(--text);
                      padding:5px 8px;border-radius:4px;font-family:inherit;font-size:var(--fs-sm);
                      width:80px;flex-shrink:0;"
               title="PTT Lead- und Tail-Verzögerung in Millisekunden (Lead = vor Audio, Tail = nach Audio)">
        <span style="color:var(--text2);font-size:var(--fs-xs);white-space:nowrap;flex-shrink:0;">ms</span>
        <button class="btn secondary" style="margin-top:0;padding:5px 12px;flex-shrink:0;"
                onclick="savePttDelay()">Speichern</button>
      </div>
      <div id="cfg-save-result" style="font-size:var(--fs-xs);margin-top:6px;display:none;"></div>
    </div>
  </div>

  <!-- ── Unterseite: Transceiver (Hamlib) ── -->
  <div id="cfg-hamlib" class="cfg-subpanel">
    <div class="audio-cfg-card">
      <!-- TRX-Profil-Auswahl — nur sichtbar wenn trx_profiles vorhanden -->
      <div id="trx-profile-row" class="audio-cfg-row hidden" style="margin-bottom:12px;">
        <label data-i18n="cfg.hamlib.profile">TRX-Profil</label>
        <select id="trx-profile-select" onchange="onTrxProfileChange(this.value)"
                style="flex:1;">
        </select>
        <button class="btn" style="flex-shrink:0;padding:5px 12px;"
                onclick="activateTrxProfile()" data-i18n="btn.activate">✓ Aktivieren</button>
      </div>
      <div id="trx-profile-active" style="font-size:var(--fs-xs);color:var(--text2);
           margin-bottom:10px;display:none;">
      </div>

      <!-- ── Profil bearbeiten / neu anlegen ── -->
      <div style="margin-top:16px;border-top:1px solid var(--border);
                  padding-top:14px;margin-bottom:14px">
        <div style="display:flex;justify-content:space-between;
                    align-items:center;margin-bottom:10px">
          <span style="font-weight:600;color:var(--text)">
            Profil bearbeiten / Neu anlegen
          </span>
          <button class="btn secondary" onclick="trxNewProfile()"
                  style="font-size:var(--fs-sm)">+ Neu</button>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;
                    gap:8px 16px">

          <label style="color:var(--text2);font-size:var(--fs-sm)">
            Profilname
            <input id="trx-edit-name" type="text" placeholder="z.B. IC-7610"
                   style="display:block;width:100%;margin-top:3px;
                          background:var(--bg2);color:var(--text);
                          border:1px solid var(--border);border-radius:4px;
                          padding:4px 8px">
          </label>

          <label style="color:var(--text2);font-size:var(--fs-sm)">
            Rig-Modell (Hamlib-ID)
            <input id="trx-edit-rigmodel" type="number" placeholder="3078"
                   style="display:block;width:100%;margin-top:3px;
                          background:var(--bg2);color:var(--text);
                          border:1px solid var(--border);border-radius:4px;
                          padding:4px 8px">
          </label>

          <label style="color:var(--text2);font-size:var(--fs-sm)">
            COM-Port
            <input id="trx-edit-device" type="text" placeholder="COM11"
                   style="display:block;width:100%;margin-top:3px;
                          background:var(--bg2);color:var(--text);
                          border:1px solid var(--border);border-radius:4px;
                          padding:4px 8px">
          </label>

          <label style="color:var(--text2);font-size:var(--fs-sm)">
            Baudrate
            <select id="trx-edit-baud"
                    style="display:block;width:100%;margin-top:3px;
                           background:var(--bg2);color:var(--text);
                           border:1px solid var(--border);border-radius:4px;
                           padding:4px 8px">
              <option value="4800">4800</option>
              <option value="9600">9600</option>
              <option value="19200" selected>19200</option>
              <option value="38400">38400</option>
              <option value="57600">57600</option>
            </select>
          </label>

          <label style="color:var(--text2);font-size:var(--fs-sm)">
            TX-Audiogerät (ID)
            <input id="trx-edit-tx-dev" type="number" placeholder="14"
                   style="display:block;width:100%;margin-top:3px;
                          background:var(--bg2);color:var(--text);
                          border:1px solid var(--border);border-radius:4px;
                          padding:4px 8px">
          </label>

          <label style="color:var(--text2);font-size:var(--fs-sm)">
            RX-Audiogerät (ID, leer = wie TX)
            <input id="trx-edit-rx-dev" type="number" placeholder="2"
                   style="display:block;width:100%;margin-top:3px;
                          background:var(--bg2);color:var(--text);
                          border:1px solid var(--border);border-radius:4px;
                          padding:4px 8px">
          </label>

          <label style="color:var(--text2);font-size:var(--fs-sm)">
            PTT-Delay ms
            <input id="trx-edit-ptt-delay" type="number"
                   value="250" min="0" max="2000"
                   style="display:block;width:100%;margin-top:3px;
                          background:var(--bg2);color:var(--text);
                          border:1px solid var(--border);border-radius:4px;
                          padding:4px 8px">
          </label>

          <label style="color:var(--text2);font-size:var(--fs-sm)">
            TX-Pegel (1–100)
            <input id="trx-edit-level" type="number"
                   value="30" min="1" max="100"
                   style="display:block;width:100%;margin-top:3px;
                          background:var(--bg2);color:var(--text);
                          border:1px solid var(--border);border-radius:4px;
                          padding:4px 8px">
          </label>

          <label style="color:var(--text2);font-size:var(--fs-sm);
                        grid-column:span 2">
            Notiz / Kommentar (optional)
            <input id="trx-edit-comment" type="text"
                   placeholder="z.B. ACC-Eingang, Pegel 40%"
                   style="display:block;width:100%;margin-top:3px;
                          background:var(--bg2);color:var(--text);
                          border:1px solid var(--border);border-radius:4px;
                          padding:4px 8px">
          </label>
        </div>

        <div style="display:flex;gap:8px;margin-top:10px">
          <button class="btn" onclick="trxSaveProfile()"
                  style="flex:1">💾 Profil speichern</button>
          <button class="btn secondary" onclick="trxDeleteProfile()"
                  id="trx-delete-btn"
                  style="color:var(--red)">🗑 Löschen</button>
        </div>
        <div id="trx-edit-msg"
             style="font-size:var(--fs-sm);margin-top:6px;
                    color:var(--text2);min-height:1.2em"></div>
      </div>
      <div class="hamlib-status-row" id="hamlib-status-row" style="display:none;margin-bottom:12px;">
        <div class="hamlib-status-dot" id="hamlib-status-dot"></div>
        <span id="hamlib-status-text"></span>
      </div>
      <div id="cfg-hamlib-status" style="margin-bottom:6px;"></div>
      <div class="audio-cfg-row">
        <label data-i18n="cfg.hamlib.port">Serieller Port</label>
        <select id="hamlib-port"><option value="">– (Rescan) –</option></select>
        <button class="btn secondary" style="margin-top:0;padding:5px 12px;flex-shrink:0;"
                onclick="rescanHamlibPorts()" data-i18n="btn.rescan">↻ Rescan</button>
      </div>
      <div class="audio-cfg-row">
        <label data-i18n="cfg.hamlib.model">Gewähltes Modell</label>
        <span id="hamlib-model-selected" style="color:var(--accent);font-size:var(--fs-sm);">–</span>
        <input type="hidden" id="hamlib-model-id" value="">
      </div>
      <div class="audio-cfg-row">
        <label data-i18n="cfg.hamlib.search">Rig-Modell suchen</label>
        <input type="text" id="hamlib-model-search" placeholder="Suche (z.B. TS-790)" data-i18n-placeholder="cfg.hamlib.search_ph"
               oninput="searchHamlibModels(this.value)"
               style="flex:1;background:var(--bg3);border:1px solid var(--border);
                      color:var(--text);padding:6px 10px;border-radius:4px;
                      font-family:inherit;font-size:var(--fs-sm);">
      </div>
      <div class="audio-cfg-row" style="align-items:flex-start;">
        <label style="padding-top:4px;"> </label>
        <select id="hamlib-model-list" size="5"
                style="flex:1;background:var(--bg3);border:1px solid var(--border);
                       color:var(--text);padding:4px;border-radius:4px;
                       font-family:inherit;font-size:var(--fs-sm);min-height:90px;"
                onchange="onHamlibModelSelect(this)">
          <option value="">– Suche oben eingeben –</option>
        </select>
      </div>
      <div class="audio-cfg-row">
        <label data-i18n="cfg.hamlib.baud">Baudrate</label>
        <select id="hamlib-baud">
          <option value="1200">1200</option>
          <option value="4800">4800</option>
          <option value="9600" selected>9600</option>
          <option value="19200">19200</option>
          <option value="38400">38400</option>
          <option value="57600">57600</option>
          <option value="115200">115200</option>
        </select>
      </div>
      <div class="audio-cfg-row">
        <label class="toggle-sw" style="width:auto;cursor:pointer;">
          <input type="checkbox" id="hamlib-autostart" checked>
          <span data-i18n="cfg.hamlib.autostart">rigctld automatisch starten (auto_start)</span>
        </label>
      </div>
      <div class="audio-cfg-note" data-i18n-html="cfg.hamlib.info.html">
        GUST startet rigctld beim Hochfahren automatisch mit den eingetragenen
        Parametern. PTT-Backend wird auf <b>hamlib</b> gesetzt. Extern gestartete
        rigctld-Instanzen werden nicht beendet.
      </div>
      <div class="audio-cfg-actions">
        <button class="btn" onclick="saveHamlibConfig()" data-i18n="btn.save">💾 Speichern</button>
        <button class="btn secondary" onclick="loadHamlibConfig()" data-i18n="btn.reload">↻ Neu laden</button>
        <button class="btn secondary" onclick="testHamlibConnection()" data-i18n="btn.connect_test">🔌 Verbinden &amp; Testen</button>
        <button class="btn secondary" id="tune-btn" onclick="toggleTune()"
                style="border-color:var(--orange);color:var(--orange);" data-i18n="btn.tune">
          📡 Tune
        </button>
      </div>
    </div>
  </div>

  <!-- ── Unterseite: SDR-TX ── -->
  <div id="cfg-sdr" class="cfg-subpanel">
    <div class="audio-cfg-card sdr-cfg-card">

      <!-- ── SDR-Profile ── -->
      <div style="margin-bottom:16px;padding-bottom:14px;
                  border-bottom:1px solid var(--border)">
        <div style="display:flex;justify-content:space-between;
                    align-items:center;margin-bottom:10px">
          <span style="font-weight:600;color:var(--text)">
            SDR-Geräte &amp; Profile
          </span>
          <button class="btn secondary" onclick="sdrScan()"
                  id="sdr-scan-btn">🔍 Geräte scannen</button>
        </div>

        <!-- Gefundene Geräte -->
        <div id="sdr-devices-list"
             style="font-size:var(--fs-sm);color:var(--text2);
                    margin-bottom:10px">
          Noch kein Scan durchgeführt.
        </div>

        <!-- Aktive Profile -->
        <div style="display:grid;grid-template-columns:1fr 1fr;
                    gap:8px 16px;margin-bottom:10px">
          <label style="color:var(--text2);font-size:var(--fs-sm)">
            Aktives RX-Profil
            <select id="sdr-active-rx"
                    onchange="sdrActivate('rx',this.value)"
                    style="display:block;width:100%;margin-top:3px;
                           background:var(--bg2);color:var(--text);
                           border:1px solid var(--border);
                           border-radius:4px;padding:4px 8px">
              <option value="">(keines — Audio/VAC)</option>
            </select>
          </label>
          <label style="color:var(--text2);font-size:var(--fs-sm)">
            Aktives TX-Profil
            <select id="sdr-active-tx"
                    onchange="sdrActivate('tx',this.value)"
                    style="display:block;width:100%;margin-top:3px;
                           background:var(--bg2);color:var(--text);
                           border:1px solid var(--border);
                           border-radius:4px;padding:4px 8px">
              <option value="">(keines — Audio-TX-Pfad)</option>
            </select>
          </label>
        </div>

        <!-- Profil bearbeiten -->
        <details style="margin-top:8px">
          <summary style="cursor:pointer;color:var(--text2);
                          font-size:var(--fs-sm);user-select:none">
            ▸ Profil bearbeiten / Neu anlegen
          </summary>
          <div style="margin-top:10px;display:grid;
                      grid-template-columns:1fr 1fr;gap:8px 16px">
            <label style="color:var(--text2);font-size:var(--fs-sm)">
              Profilname
              <input id="sdr-edit-name" type="text"
                     placeholder="z.B. HackRF"
                     style="display:block;width:100%;margin-top:3px;
                            background:var(--bg2);color:var(--text);
                            border:1px solid var(--border);
                            border-radius:4px;padding:4px 8px">
            </label>
            <label style="color:var(--text2);font-size:var(--fs-sm)">
              Typ (automatisch)
              <input id="sdr-edit-type" type="text" readonly
                     placeholder="aus Scan"
                     style="display:block;width:100%;margin-top:3px;
                            background:var(--bg2);color:var(--text2);
                            border:1px solid var(--border);
                            border-radius:4px;padding:4px 8px;
                            opacity:0.7">
            </label>
            <label style="color:var(--text2);font-size:var(--fs-sm)">
              Treiber
              <input id="sdr-edit-driver" type="text"
                     placeholder="hackrf / rtlsdr / sdrplay"
                     style="display:block;width:100%;margin-top:3px;
                            background:var(--bg2);color:var(--text);
                            border:1px solid var(--border);
                            border-radius:4px;padding:4px 8px">
            </label>
            <label style="color:var(--text2);font-size:var(--fs-sm)">
              Serial (leer = erstes Gerät)
              <input id="sdr-edit-serial" type="text"
                     placeholder="optional"
                     style="display:block;width:100%;margin-top:3px;
                            background:var(--bg2);color:var(--text);
                            border:1px solid var(--border);
                            border-radius:4px;padding:4px 8px">
            </label>
            <label style="color:var(--text2);font-size:var(--fs-sm);
                          grid-column:span 2">
              Notiz
              <input id="sdr-edit-comment" type="text"
                     placeholder="optional"
                     style="display:block;width:100%;margin-top:3px;
                            background:var(--bg2);color:var(--text);
                            border:1px solid var(--border);
                            border-radius:4px;padding:4px 8px">
            </label>
          </div>
          <div style="display:flex;gap:8px;margin-top:8px">
            <button class="btn" onclick="sdrSaveProfile()"
                    style="flex:1">💾 Profil speichern</button>
            <button class="btn secondary"
                    onclick="sdrDeleteProfile()"
                    style="color:var(--red)">
              🗑 Löschen</button>
          </div>
          <div id="sdr-edit-msg"
               style="font-size:var(--fs-sm);margin-top:6px;
                      color:var(--text2);min-height:1.2em"></div>
        </details>
      </div>

      <div class="audio-cfg-row">
        <label class="toggle-sw" style="width:auto;cursor:pointer;">
          <input type="checkbox" id="sdr-enabled">
          <span data-i18n="cfg.sdr.active">Aktiv — TX über SDR statt NF-Audio</span>
        </label>
      </div>
      <div class="audio-cfg-row">
        <label data-i18n="cfg.sdr.device">TX-Gerät</label>
        <select id="sdr-device"><option value="">– (Rescan starten) –</option></select>
        <button class="btn secondary" style="margin-top:0;padding:5px 12px;flex-shrink:0;"
                onclick="rescanSdrDevices()" title="SoapySDR.Device.enumerate() neu aufrufen"
                data-i18n="btn.rescan">
          ↻ Rescan
        </button>
      </div>
      <div class="audio-cfg-row">
        <label data-i18n="cfg.sdr.freq">Frequenz</label>
        <input type="number" id="sdr-freq" value="14110000" min="1000" step="1000"
               style="flex:1;min-width:140px;background:var(--bg3);border:1px solid var(--border);
                      color:var(--text);padding:6px 10px;border-radius:4px;
                      font-family:inherit;font-size:var(--fs-sm);">
        <span class="unit" style="color:var(--text2);font-size:var(--fs-xs);" data-i18n="cfg.sdr.freq_unit">Hz (USB-Dial)</span>
      </div>
      <div class="audio-cfg-row">
        <label data-i18n="cfg.sdr.samplerate">Sample-Rate</label>
        <select id="sdr-sr"><option value="2000000">2 000 000 Hz</option></select>
      </div>
      <div class="audio-cfg-row">
        <label data-i18n="cfg.sdr.antenna">Antenne</label>
        <select id="sdr-antenna"><option value="">– Default –</option></select>
      </div>
      <div class="audio-cfg-row">
        <label data-i18n="cfg.sdr.gain">Gain (normalisiert)</label>
        <input type="range" id="sdr-gain" min="0" max="1" step="0.01" value="0.5"
               style="flex:1;accent-color:var(--accent);">
        <span class="unit" id="sdr-gain-val"
              style="width:90px;text-align:right;color:var(--text);font-size:var(--fs-xs);">0.50</span>
      </div>
      <div id="sdr-gain-elements" style="display:none;font-size:var(--fs-xs);
           color:var(--text2);margin:4px 0 8px;padding:6px 10px;background:var(--bg3);
           border-radius:3px;"></div>
      <div class="audio-cfg-note" id="sdr-note" data-i18n-html="cfg.sdr.info.html">
        <b>Discovery-only</b> (ADR-16) — Geräte kommen aus
        <code>SoapySDR.Device.enumerate()</code>. RX-only-Geräte (z.B. RTL-SDR)
        sind ausgegraut. Gespeichert werden Treiber + Seriennummer, nicht der
        Listenindex.
      </div>
      <div class="audio-cfg-actions">
        <button class="btn" onclick="saveSdrConfig()" data-i18n="btn.save">💾 Speichern</button>
        <button class="btn secondary" onclick="loadSdrConfig()" data-i18n="btn.reload">↻ Neu laden</button>
      </div>
      <div id="sdr-cfg-status"></div>
      <details id="sdr-modules-details" style="margin-top:10px;font-size:var(--fs-xs);">
        <summary style="cursor:pointer;color:var(--text2);" data-i18n="cfg.sdr.diag">
          Geladene SoapySDR-Module (Diagnose)
        </summary>
        <pre id="sdr-modules-list" style="margin-top:6px;padding:6px 10px;
          background:var(--bg3);border-radius:3px;color:var(--text2);
          font-family:var(--ui-font);white-space:pre-wrap;
          max-height:160px;overflow-y:auto;">–</pre>
      </details>
    </div>
  </div>

  <!-- ── Unterseite: Darstellung ── -->
  <div id="cfg-display" class="cfg-subpanel">
    <h3 style="margin-top:0;margin-bottom:8px;font-size:var(--fs-sm);
               text-transform:uppercase;letter-spacing:1px;color:var(--text2);"
        data-i18n="cfg.display.title">
      Darstellung
    </h3>
    <div class="status-cfg-row">
      <label class="cfg-label" data-i18n="cfg.display.theme">Theme</label>
      <select id="cfg-theme" onchange="applyTheme(this.value)">
        <option value="dark">Dark Amber</option>
        <option value="light">Light Clean</option>
      </select>
    </div>
    <div class="status-cfg-row">
      <label class="cfg-label" data-i18n="cfg.display.lang">Sprache / Language</label>
      <select id="cfg-lang" onchange="loadLang(this.value)">
        <option value="de">🇩🇪 Deutsch</option>
        <option value="en">🇬🇧 English</option>
      </select>
    </div>
    <div class="status-cfg-row">
      <label class="cfg-label" data-i18n="cfg.display.font">Schriftart</label>
      <select id="cfg-font" onchange="applyFont(this.value)">
        <option value="mono">Monospace (Standard)</option>
        <option value="system">System UI (Segoe / Helvetica)</option>
        <option value="sans">Sans-serif (Calibri / Helvetica Neue)</option>
        <option value="serif">Serif (Georgia)</option>
      </select>
    </div>
    <div class="status-cfg-row">
      <label class="cfg-label" data-i18n="font.size_label">Schriftgröße</label>
      <select id="cfg-fontsize" onchange="applyFontSize(this.value)">
        <option value="12">12 px</option>
        <option value="13">13 px (Standard)</option>
        <option value="14">14 px</option>
        <option value="15">15 px</option>
        <option value="16">16 px</option>
        <option value="18">18 px</option>
        <option value="20">20 px</option>
      </select>
    </div>

    <div style="margin-top:16px;">
      <button class="btn secondary" onclick="loadStatus()" data-i18n="status.refresh">↻ Aktualisieren</button>
    </div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════ TAB: LOG -->
<div id="tab-log" class="tab-panel">

  <!-- ── Aktivitätslog ── -->
  <div style="margin-bottom:18px;">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
      <h3 style="margin:0;font-size:var(--fs-sm);text-transform:uppercase;
                 letter-spacing:1px;color:var(--text2);" data-i18n="log.activity">📡 Aktivitätslog</h3>
      <button class="btn secondary" style="padding:3px 10px;font-size:var(--fs-xs);"
              onclick="clearActivityLog()" data-i18n="log.clear">Leeren</button>
    </div>
    <div id="activity-feed"
         style="background:var(--bg2);border:1px solid var(--border);border-radius:6px;
                padding:8px;max-height:200px;overflow-y:auto;font-size:var(--fs-sm);">
      <div style="color:var(--text2);" data-i18n="log.activity.empty">Noch keine Aktivität.</div>
    </div>
  </div>

  <!-- ── Systemlog ── -->
  <div>
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
      <h3 style="margin:0;font-size:var(--fs-sm);text-transform:uppercase;
                 letter-spacing:1px;color:var(--text2);">🗒 <span data-i18n="log.systemlog">Systemlog</span></h3>
      <span style="color:var(--text2);font-size:var(--fs-sm);" data-i18n="log.level">Ebene:</span>
      <select id="log-level-filter" onchange="filterLogLevel()">
        <option value="ALL" data-i18n="log.filter.all">Alle</option>
        <option value="INFO">INFO+</option>
        <option value="WARNING">WARNING+</option>
        <option value="ERROR">ERROR</option>
      </select>
      <label id="autoscroll-toggle">
        <input type="checkbox" id="log-autoscroll" checked> <span data-i18n="monitor.autoscroll">Auto-Scroll</span>
      </label>
      <button class="btn secondary" style="padding:3px 10px;font-size:var(--fs-xs);"
              onclick="clearLog()">Leeren</button>
    </div>
    <div id="log-feed"></div>
  </div>

</div>

<!-- ═══════════════ TAB: STRESSTEST (Session-Recorder) ═══════════════ -->
<div id="tab-stresstest" class="tab-panel">
  <h3 style="margin-bottom:6px;font-size:var(--fs-sm);text-transform:uppercase;
             letter-spacing:1px;color:var(--text2);" data-i18n="stresstest.title">🧪 Stresstest-Session</h3>
  <p style="color:var(--text2);font-size:var(--fs-sm);margin-bottom:14px;" data-i18n="stresstest.desc">
    WAV-Datei via VAC abspielen → Session aufzeichnen → Ground-Truth-CSV auswerten.
  </p>

  <!-- Status-Anzeige -->
  <div id="st-status-bar" style="
      padding:8px 14px;border-radius:6px;margin-bottom:14px;
      background:var(--bg2);font-size:var(--fs-sm);
      border-left:4px solid var(--accent)">
    <span id="st-state-label">Bereit</span> &nbsp;·&nbsp;
    <span id="st-frame-count">0 Frames</span>
    <span id="st-duration" style="margin-left:8px;color:var(--text2)"></span>
  </div>

  <!-- Steuerung -->
  <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px">
    <button id="st-btn-start" class="btn" onclick="stSession('start')">
      ▶ Session starten
    </button>
    <button id="st-btn-stop" class="btn secondary" onclick="stSession('stop')" disabled>
      ■ Session stoppen
    </button>
  </div>

  <!-- CSV-Upload + Auswertung -->
  <div id="st-evaluate-box" style="display:none">
    <h4 style="margin-bottom:8px">Ground-Truth-CSV auswerten</h4>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <input type="file" id="st-csv-input" accept=".csv"
             style="flex:1;min-width:200px">
      <button class="btn" onclick="stEvaluate()">Auswerten</button>
    </div>
  </div>

  <!-- Report -->
  <div id="st-report-box" style="display:none;margin-top:18px">
    <h4>Auswertungsbericht</h4>
    <div id="st-report-summary" style="
        background:var(--bg2);border:1px solid var(--border);border-radius:8px;
        padding:14px;margin:8px 0 12px 0;font-size:var(--fs-sm)">
    </div>
    <div id="st-missed-box" style="display:none">
      <h4 style="margin-bottom:6px;color:var(--orange)">Fehlende Frames</h4>
      <table id="st-missed-table" style="width:100%;font-size:var(--fs-xs);border-collapse:collapse">
        <thead><tr style="border-bottom:1px solid var(--border)">
          <th style="text-align:left;padding:4px 8px">Kanal</th>
          <th style="text-align:left;padding:4px 8px">Typ</th>
          <th style="text-align:left;padding:4px 8px">Rufzeichen</th>
          <th style="text-align:left;padding:4px 8px">@Zeit</th>
        </tr></thead>
        <tbody id="st-missed-tbody"></tbody>
      </table>
    </div>
    <div style="display:flex;gap:10px;margin-top:12px;flex-wrap:wrap">
      <button class="btn secondary" onclick="stDownload('json')">⬇ JSON</button>
      <button class="btn secondary" onclick="stDownload('csv')">⬇ CSV</button>
    </div>
  </div>
</div>

</main><!-- /main -->

<script>
// ═══════════════════════════ STATE ════════════════════════════
const state = {
  callsign:   '–',
  homeChannel: null,
  channelLast: {},
  rxCount:    0,
  wsRx:       null,
  wsLog:      null,
  lang:       {},
  currentLang: localStorage.getItem('gust_lang') || 'de',
  wsRetryTimer: null,
  txInterval: 300,    // TX-Schedule-Intervall in Sekunden (aus /api/status)
  qsoMode:    false,  // QSO-Modus: 60 s Fragment-Intervall statt txInterval
  txOffset:   0,      // Zeitversatz dieses Rufzeichens innerhalb des TX-Schedules
  txQueue:    [],     // ausstehende TX-Frames (aus /api/tx/queue) — wird alle 5s überschrieben!
  isSending:  false,  // true während eines laufenden TX (TRX-Monitor-Schutz)
  fragCache:  {},     // RX-Reassembly: 'call:seq' → {total, frags, ts, ch, frm, dest, t0}
  txFragQueue: [],    // TX: wartende Einzelfragmente (eigener Name — NICHT txQueue!)
  txFragActive: false,// true = Fragment-Sende-Loop läuft gerade
  _txDoneResolve: null,// Resolver-Callback für _waitForTxDone()
  inbox:       [],    // Empfangene Freitext-Nachrichten (an mich adressiert)
  inboxUnread: 0,     // Anzahl ungelesener Nachrichten
  sent:        [],    // Gesendete Frames (aus tx_done-Events)
  sentFragCache: {},  // TX-Reassembly: 'seq_nr' → {total, frags, ts, to, t0}
  uptimeBase:  null,  // uptime_s vom letzten loadStatus()
  uptimeBaseTs: 0,    // Date.now() beim Setzen von uptimeBase
  lastHeartbeat:  null,   // Date.now() beim letzten Heartbeat-Empfang
  daemonAlive:    false,  // true nach erstem Heartbeat
};

// ═══════════════════════════ i18n ════════════════════════════
function t(key) {
  return state.lang[key] ?? key;
}

async function loadLang(code) {
  try {
    const r = await fetch('/api/lang/' + code);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    state.lang = await r.json();
    state.currentLang = code;
    localStorage.setItem('gust_lang', code);
    applyI18n();
  } catch(e) {
    console.warn('i18n load failed:', e);
  }
}

function applyI18n() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    const val = t(key);
    // Buttons und Spans: innerHTML nur wenn kein Kind-Element vorhanden
    if (el.children.length === 0) el.textContent = val;
  });
  document.querySelectorAll('[data-i18n-html]').forEach(el => {
    el.innerHTML = t(el.getAttribute('data-i18n-html'));
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    el.placeholder = t(el.getAttribute('data-i18n-placeholder'));
  });
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    el.title = t(el.getAttribute('data-i18n-title'));
  });
}

// Freitext-Frame-Typ (0x40 — siehe gust_frame.py FrameType.TEXT)
function _setOnAir(active) {
  const b = document.getElementById('onair-banner');
  if (!b) return;
  if (active) {
    b.classList.add('visible');
  } else {
    b.classList.remove('visible');
  }
}

const TEXT_FRAME_TYPE = 0x40;

// ═══════════════════════════ THEME ════════════════════════════
// Dark Amber = Standard (kein data-theme Attribut)
// Light Clean = data-theme="light"
// Gespeichert in localStorage, beim Reload wiederhergestellt.

const THEMES = {
  dark:  { attr: null,    btn: '🌙 Light',  label: 'Dark Amber'  },
  light: { attr: 'light', btn: '☀ Dark',   label: 'Light Clean' },
};

function toggleTheme() {
  const isLight = document.documentElement.getAttribute('data-theme') === 'light';
  applyTheme(isLight ? 'dark' : 'light');
}

function applyTheme(name) {
  const t = THEMES[name];
  if (t.attr) {
    document.documentElement.setAttribute('data-theme', t.attr);
  } else {
    document.documentElement.removeAttribute('data-theme');
  }
  document.getElementById('theme-btn').textContent = t.btn;
  localStorage.setItem('gust-theme', name);
  // Dropdown im Status & Config-Tab synchron halten
  const sel = document.getElementById('cfg-theme');
  if (sel) sel.value = name;
}

// ── SCHRIFTART / SCHRIFTGRÖSSE (Appearance) ──
// Wirken über CSS Custom Properties auf :root, damit auch Regeln mit
// expliziter font-size (var(--fs-*)) zuverlässig mitskalieren.
const FONTS = {
  mono:   "'Courier New', 'Lucida Console', monospace",
  system: "system-ui, -apple-system, 'Segoe UI', sans-serif",
  sans:   "'Calibri', 'Helvetica Neue', 'DejaVu Sans', Arial, sans-serif",
  serif:  "Georgia, 'Times New Roman', serif",
};

// Basisgrößen-Stufen in px (korrespondierend zu --fs-base)
const FONT_SIZES = { '12': 12, '13': 13, '14': 14, '15': 15, '16': 16, '18': 18, '20': 20 };

function applyFont(key) {
  const family = FONTS[key] || FONTS.mono;
  const root = document.documentElement;
  root.style.setProperty('--ui-font', family);
  localStorage.setItem('gust-font', key);
  // Dropdown synchronisieren (falls vorhanden)
  const sel = document.getElementById('cfg-font');
  if (sel) sel.value = key;
}

function applyFontSize(baseStr) {
  const base = parseInt(baseStr, 10);
  if (isNaN(base) || base < 10 || base > 20) return;
  const root = document.documentElement;
  // Alle Größenstufen proportional skalieren — Basis ist 13px
  const scale = base / 13;
  root.style.setProperty('--fs-base', base + 'px');
  root.style.setProperty('--fs-sm',   Math.round(12 * scale) + 'px');
  root.style.setProperty('--fs-xs',   Math.round(11 * scale) + 'px');
  root.style.setProperty('--fs-xxs',  Math.round(10 * scale) + 'px');
  root.style.setProperty('--fs-lg',   Math.round(16 * scale) + 'px');
  localStorage.setItem('gust-fontsize', baseStr);
  // Dropdown synchronisieren (falls vorhanden)
  const sel = document.getElementById('cfg-fontsize');
  if (sel) sel.value = baseStr;
}

// Beim Laden: gespeichertes Theme / Schrift wiederherstellen
(function() {
  // Theme
  const savedTheme = localStorage.getItem('gust-theme') || 'dark';
  applyTheme(savedTheme);
  // Font — VOR dem ersten Paint anwenden, damit kein FOUT entsteht
  const savedFont = localStorage.getItem('gust-font') || 'mono';
  applyFont(savedFont);
  // Fontsize
  const savedSize = localStorage.getItem('gust-fontsize') || '13';
  applyFontSize(savedSize);
})();

// ═══════════════════════════ TABS ═════════════════════════════
function switchTab(name, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
  if (name === 'status') {
    loadStatus(); loadAudioConfig(); loadSdrConfig();
    // Sub-Tab-Zustand wiederherstellen wenn Config-Tab geöffnet wird
    const savedSub = localStorage.getItem('gust_cfg_subtab') || 'cfg-audio';
    const subBtn = document.querySelector(`.cfg-subnav button[onclick*="'${savedSub}'"]`);
    switchCfgTab(savedSub, subBtn || document.querySelector('.cfg-subnav button'));
  }
  if (name === 'tx')     fetchTxQueue();
  if (name === 'stresstest') stUpdateStatus();
  if (name === 'inbox') {
    // Tab geöffnet → alles als gelesen markieren, Badge zurücksetzen
    state.inbox.forEach(m => m.read = true);
    state.inboxUnread = 0;
    updateInboxBadge();
    renderInbox();
  }
}

// ═══════════════════════════ TX FORMS ═════════════════════════
function selectTxType(type, btn) {
  document.querySelectorAll('.tx-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tx-form').forEach(f => f.classList.add('hidden'));
  btn.classList.add('active');
  document.getElementById('form-' + type).classList.remove('hidden');
}

function updateTextCounter() {
  const msg    = document.getElementById('t-msg')?.value || '';
  const bytes  = new TextEncoder().encode(msg).length;
  const frames = Math.max(1, Math.ceil(bytes / 14));
  const remain = 56 - bytes;

  const bc = document.getElementById('text-byte-count');
  const fc = document.getElementById('text-frame-count');
  const rc = document.getElementById('text-remaining');
  if (!bc) return;

  bc.textContent = bytes + ' / 56 Byte';
  bc.style.color = bytes > 56 ? 'var(--red)' : bytes > 42 ? 'var(--orange)' : 'var(--text2)';

  fc.textContent = frames + (frames === 1 ? ' Frame' : ' Frames');
  fc.style.color = frames >= 4 ? 'var(--orange)' : 'var(--green)';

  if (remain >= 0) {
    rc.textContent = remain + ' Byte frei';
    rc.style.color = remain < 14 ? 'var(--orange)' : 'var(--text2)';
  } else {
    rc.textContent = 'Limit überschritten!';
    rc.style.color = 'var(--red)';
  }
}

async function sendTx(type) {
  // Monitor-Modus: gar nicht erst senden — sonst 503 vom Server.
  if (state.txAvailable === false) {
    const el = document.getElementById('tx-result');
    el.className = 'err'; el.style.display = 'block';
    el.textContent = '✗ Senden nicht möglich — Monitor-Modus (kein TX-Gateway). '
                   + 'Starte GUST als Daemon: py gust.py daemon';
    setTimeout(() => el.style.display = 'none', 5000);
    return;
  }
  let payload = {};
  if (type === 'weather') {
    payload = {
      temp_c:    parseFloat(document.getElementById('w-temp').value),
      humidity:  parseInt(document.getElementById('w-hum').value),
      pressure_hpa: parseFloat(document.getElementById('w-pres').value),
      wind_kmh:  parseInt(document.getElementById('w-wind').value),
      wind_dir:  parseInt(document.getElementById('w-wdir').value),
      rain_mmh:  parseFloat(document.getElementById('w-rain').value),
      uv_index:  parseInt(document.getElementById('w-uv').value),
    };
  } else if (type === 'position') {
    payload = {
      lat:    parseFloat(document.getElementById('p-lat').value),
      lon:    parseFloat(document.getElementById('p-lon').value),
      alt_m:  parseInt(document.getElementById('p-alt').value),
      speed_kmh: parseInt(document.getElementById('p-speed').value),
      heading:   parseInt(document.getElementById('p-hdg').value),
      mobile: parseInt(document.getElementById('p-mobile').value) === 1,
    };
  } else if (type === 'text') {
    const msgVal   = document.getElementById('t-msg').value;
    const toCall   = (document.getElementById('t-to').value || '').toUpperCase() || 'CQCQCQ';
    const encoder  = new TextEncoder();
    const msgBytes = encoder.encode(msgVal).length;
    const txEl     = document.getElementById('tx-result');

    if (msgBytes === 0) {
      txEl.className = 'err'; txEl.style.display = 'block';
      txEl.textContent = '✗ Nachricht ist leer.';
      setTimeout(() => txEl.style.display = 'none', 3000);
      return;
    }
    if (msgBytes > 56) {
      alert('Nachricht zu lang: ' + msgBytes + ' Byte (max. 56 Byte / 4 Frames).');
      return;
    }

    // Byte-korrekt in 14-Byte-Chunks aufteilen (UTF-8, nicht zeichenbasiert)
    const CHUNK = 14;
    const chunks = [];
    let remaining = msgVal;
    while (remaining.length > 0) {
      let lo = 0, hi = remaining.length;
      while (lo < hi) {
        const mid = Math.ceil((lo + hi) / 2);
        if (encoder.encode(remaining.slice(0, mid)).length <= CHUNK) lo = mid;
        else hi = mid - 1;
      }
      if (lo === 0) lo = 1;   // Schutz gegen Endlosschleife bei Mehrbyte-Zeichen
      chunks.push(remaining.slice(0, lo));
      remaining = remaining.slice(lo);
    }
    if (chunks.length === 0) chunks.push('');

    const nFrames = chunks.length;
    const seqNr   = Math.floor(Math.random() * 256);   // gemeinsame Sequenznummer

    if (nFrames > 1) {
      const ivSec = state.qsoMode ? 60 : (state.txInterval || 300);
      const mins  = Math.round((nFrames * ivSec) / 60);
      const modeStr = state.qsoMode ? ' (QSO-Modus, 60 s/Fragment)' : ' (Standard-Schedule)';
      const ok = confirm(
        'Diese Nachricht wird mit ' + nFrames + ' Frames gesendet.\n' +
        'Intervall' + modeStr + ': ca. ' + mins + ' Minuten.\n\n' +
        'Die Frames werden einzeln je Schedule-Slot gesendet.\nTrotzdem senden?'
      );
      if (!ok) return;
    }

    // Fragmente in die (eigene) TX-Fragment-Queue legen und Loop starten
    for (let i = 0; i < chunks.length; i++) {
      state.txFragQueue.push({
        to: toCall, text_chunk: chunks[i], seq_nr: seqNr,
        frag_index: i, frag_total: chunks.length,
      });
    }
    _startTxQueue();
    return;   // kein gemeinsamer POST — der Queue-Loop übernimmt das Senden
  } else if (type === 'emergency') {
    payload = {
      lat:       parseFloat(document.getElementById('e-lat').value),
      lon:       parseFloat(document.getElementById('e-lon').value),
      persons:   parseInt(document.getElementById('e-persons').value),
      injury:    parseInt(document.getElementById('e-injury').value),
      priority:  parseInt(document.getElementById('e-prio').value),
      text_snippet: document.getElementById('e-text').value.toUpperCase().padEnd(8,' ').slice(0,8),
    };
  }

  const el = document.getElementById('tx-result');
  state.isSending = true; _setOnAir(true);
  el.style.display = 'none';
  try {
    const r = await apiFetch('/api/tx/' + type, { method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    el.className = 'ok'; el.style.display = 'block';
    el.textContent = '✓ ' + (r.message || t('tx.queued'));
    fetchTxQueue();   // Warteschlange sofort aktualisieren
  } catch(e) {
    el.className = 'err'; el.style.display = 'block';
    el.textContent = t('tx.error') + ' ' + e.message;
  }
  setTimeout(() => el.style.display = 'none', 4000);
}

// ═══════════════════════════ STATUS ═══════════════════════════
async function loadStatus() {
  try {
    const s = await apiFetch('/api/status');
    document.getElementById('s-call').textContent = s.callsign || '–';
    document.getElementById('s-ch').textContent   = t('unit.channel') + ' ' + (s.home_channel ?? '–');
    if (s.tx_time_offset_s != null && s.tx_interval_s) {
      const off  = s.tx_time_offset_s;
      const itvl = s.tx_interval_s;
      const om   = Math.floor(off / 60), os = off % 60;
      const im   = Math.floor(itvl / 60);
      document.getElementById('s-ch-offset').textContent =
        om > 0 ? `+${om}m ${String(os).padStart(2,'0')}s` : `+${os}s`;
      document.getElementById('s-ch-cycle').textContent  =
        `Schedule: ${im} min`;
    }
    if (s.uptime_s != null) {
      state.uptimeBase   = s.uptime_s;
      state.uptimeBaseTs = Date.now();
    }
    document.getElementById('s-uptime').textContent = formatUptime(s.uptime_s);
    document.getElementById('s-queue').textContent  = s.queue_depth ?? '–';
    document.getElementById('s-last-tx').textContent = s.last_tx ? fmtTs(s.last_tx) : '–';
    document.getElementById('s-last-rx').textContent = s.last_rx ? fmtTs(s.last_rx) : '–';
    document.getElementById('s-audio').textContent  = s.audio_device || '–';
    document.getElementById('s-ptt').textContent    = s.ptt_backend  || t('status.unknown');
    document.getElementById('s-rx-count').textContent = state.rxCount;
    applyStatusPush(s);   // Interval + Offset für Countdown übernehmen
  } catch(e) { /* ignore */ }
}

async function savePttDelay() {
  const ms  = parseInt(document.getElementById('cfg-ptt-delay')?.value || '250');
  const res = document.getElementById('cfg-save-result');
  try {
    await apiFetch('/api/config', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({audio: {ptt_delay_ms: ms}}),
    });
    res.style.display = 'block';
    res.style.color   = 'var(--green)';
    res.textContent   = '✓ Gespeichert (' + ms + ' ms) — wirkt ab nächstem TX';
  } catch(e) {
    res.style.display = 'block';
    res.style.color   = 'var(--red)';
    res.textContent   = '✗ Fehler: ' + e.message;
  }
  setTimeout(() => { if(res) res.style.display='none'; }, 4000);
}

// ═══════════════════════════ QSO-MODUS ════════════════════════
// QSO-Modus verkürzt das Fragment-Intervall auf 60 s (statt txInterval).
// DESIGNENTSCHEIDUNG (gust_spec.md §3.4 / §6.2):
//   Nur über das Web-UI aktivierbar — bewusst nicht in gateway.json oder API.
//   Für automatischen Betrieb (Baken, Telemetrie) ist dieser Modus nicht
//   vorgesehen; er dient ausschließlich der interaktiven Ham-Kommunikation.
function toggleQsoMode(enabled) {
  state.qsoMode = enabled;
  const warning  = document.getElementById('qso-mode-warning');
  const display  = document.getElementById('qso-interval-display');
  const ivActive = enabled ? 60 : state.txInterval;
  if (warning) warning.classList.toggle('hidden', !enabled);
  if (display) display.textContent = enabled ? '60 s ⚡' : (state.txInterval + ' s');
  _tickTxCountdown();
}

// ═══════════════════════ TX-FRAGMENT-SCHEDULING ═══════════════
// Sendet mehrteilige Freitext-Nachrichten Fragment für Fragment,
// jeweils ein Fragment pro Schedule-Slot (statt alle back-to-back).
async function _startTxQueue() {
  if (state.txFragActive || state.txFragQueue.length === 0) return;
  state.txFragActive = true;
  const el = document.getElementById('tx-result');
  try {
    while (state.txFragQueue.length > 0) {
      const frag = state.txFragQueue.shift();
      if (el) {
        el.className = 'ok'; el.style.display = 'block';
        el.textContent = `⏳ Sende Fragment ${frag.frag_index + 1}/${frag.frag_total} …`;
      }
      fetchTxQueue();   // Warteschlange nach jedem Fragment aktualisieren
      state.isSending = true; _setOnAir(true);
      try {
        await apiFetch('/api/tx/text_fragment', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(frag),
        });
        await _waitForTxDone(30000);   // auf die tatsächliche Übertragung warten
      } catch(e) {
        if (el) { el.className = 'err';
          el.textContent = `✗ Fehler bei Fragment ${frag.frag_index + 1}: ${e.message}`; }
        state.txFragQueue = [];       // bei Fehler: Rest verwerfen
        break;
      } finally {
        state.isSending = false; _setOnAir(false);
      }

      if (state.txFragQueue.length > 0) {
        // Bis zum nächsten Schedule-Slot warten — mit Live-Countdown in der Anzeige
        const label = `✓ Fragment ${frag.frag_index + 1}/${frag.frag_total} gesendet — nächstes in `;
        await _countdownWait(Math.max(1, _nextCycleSecs()), (rem) => {
          if (el) el.textContent = label + _fmtCountdown(rem);
        });
      } else if (el) {
        el.textContent = `✓ Alle ${frag.frag_total} Fragmente gesendet.`;
        setTimeout(() => { if (el) el.style.display = 'none'; }, 5000);
      }
    }
  } finally {
    state.txFragActive = false;
  }
}

// Wartet auf das nächste 'tx_done'-WebSocket-Event (oder Timeout-Fallback).
// Der WS-onmessage-Handler ruft state._txDoneResolve() auf, sobald tx_done kommt.
function _waitForTxDone(timeoutMs) {
  return new Promise((resolve) => {
    if (!state.wsRx) { resolve('no-ws'); return; }
    const timer = setTimeout(() => { state._txDoneResolve = null; resolve('timeout'); }, timeoutMs);
    state._txDoneResolve = () => { clearTimeout(timer); resolve('done'); };
  });
}

// Wartet `secs` Sekunden und ruft onTick(verbleibende Sekunden) einmal pro Sekunde auf.
function _countdownWait(secs, onTick) {
  return new Promise((resolve) => {
    let remaining = Math.ceil(secs);
    onTick(remaining);
    const iv = setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) { clearInterval(iv); resolve(); }
      else onTick(remaining);
    }, 1000);
  });
}

// Lösch-Button der Sende-Formulare: alle Eingaben des Formulars zurücksetzen.
function clearForm(type) {
  const form = document.getElementById('form-' + type);
  if (!form) return;
  form.querySelectorAll('input').forEach(inp => { inp.value = ''; });
  form.querySelectorAll('select').forEach(sel => { sel.selectedIndex = 0; });
  if (type === 'text' && typeof updateTextCounter === 'function') updateTextCounter();
  const tr = document.getElementById('tx-result');
  if (tr) tr.style.display = 'none';
}

// ═══════════════════════════ AUDIO CONFIG ═════════════════════
// Lädt verfügbare Audio-Geräte + aktuelle Auswahl in die Dropdowns des
// Status & Config-Tabs. "Standard" = leerer Wert = sounddevice-Default.
// RX = leerer Wert bedeutet "wie TX" (gust_rx.py fällt automatisch zurück).
async function loadAudioConfig() {
  const statusEl = document.getElementById('cfg-audio-status');
  if (statusEl) { statusEl.textContent = ''; statusEl.style.color = 'var(--text2)'; }
  try {
    const [devs, cur] = await Promise.all([
      apiFetch('/api/audio/devices'),
      apiFetch('/api/audio/config'),
    ]);

    function _fill(selectId, items, currentId, placeholder) {
      const sel = document.getElementById(selectId);
      if (!sel) return;
      sel.innerHTML = `<option value="">${placeholder}</option>`;
      // Nach Host-API gruppieren (MME, WASAPI, … bzw. ALSA, JACK unter Linux),
      // Reihenfolge des ersten Auftretens beibehalten.
      const groups = new Map();
      for (const d of (items || [])) {
        const api = d.host_api_name || `API ${d.host_api}`;
        if (!groups.has(api)) groups.set(api, []);
        groups.get(api).push(d);
      }
      for (const [api, list] of groups) {
        const og = document.createElement('optgroup');
        og.label = api;
        for (const d of list) {
          const opt = document.createElement('option');
          opt.value = String(d.id);
          const def = d.is_default ? ' ★' : '';
          opt.textContent = `[${d.id}] ${d.name} (${d.channels}ch)${def}`;
          // Markiere aktuell konfiguriertes Gerät
          if (currentId !== null && currentId !== undefined &&
              String(currentId) === String(d.id)) {
            opt.selected = true;
          }
          og.appendChild(opt);
        }
        sel.appendChild(og);
      }
    }

    _fill('cfg-audio-in',  devs.input,  cur.rx_device, '– Standard / wie TX –');
    _fill('cfg-audio-out', devs.output, cur.tx_device, '– Standard –');

    const pttSel = document.getElementById('cfg-ptt');
    if (pttSel && cur.ptt_backend) pttSel.value = cur.ptt_backend;

    const deepCb = document.getElementById('cfg-deep-decode');
    if (deepCb) {
      deepCb.checked = !!cur.deep_decode;
      deepCb.dataset.saved = cur.deep_decode ? '1' : '0';
    }

    if (statusEl && cur.writable === false) {
      statusEl.style.color = 'var(--orange)';
      statusEl.textContent = '⚠ Schreiben deaktiviert — kein config_path bekannt.';
    }
  } catch (e) {
    if (statusEl) {
      statusEl.style.color = 'var(--red)';
      statusEl.textContent = '✗ Geräte konnten nicht geladen werden: ' + e.message;
    }
  }
}

async function saveAudioConfig() {
  const inSel  = document.getElementById('cfg-audio-in')?.value  ?? '';
  const outSel = document.getElementById('cfg-audio-out')?.value ?? '';
  const ptt    = document.getElementById('cfg-ptt')?.value || 'null';
  const deepCb = document.getElementById('cfg-deep-decode');
  const deepChanged = deepCb
    && (deepCb.checked ? '1' : '0') !== deepCb.dataset.saved;
  // Leer = null (= Standard / wie TX bei RX)
  const body = {
    audio: {
      input:  inSel  === '' ? null : parseInt(inSel, 10),
      output: outSel === '' ? null : parseInt(outSel, 10),
    },
    ptt: { backend: ptt },
    rx:  { deep_decode: !!(deepCb && deepCb.checked) },
  };

  const statusEl = document.getElementById('cfg-audio-status');
  try {
    const r = await apiFetch('/api/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (statusEl) {
      if (deepChanged) {
        statusEl.style.color = 'var(--orange)';
        statusEl.textContent = '✓ Gespeichert — Neustart erforderlich um '
          + 'Deep Decoding zu ' + (deepCb.checked ? 'aktivieren.' : 'deaktivieren.');
      } else {
        statusEl.style.color = 'var(--green)';
        statusEl.textContent = '✓ ' + (r.message || 'Konfiguration gespeichert');
      }
    }
    if (deepCb) deepCb.dataset.saved = deepCb.checked ? '1' : '0';
    loadStatus();   // Status-Karten (Audio-Gerät, PTT) auffrischen
  } catch (e) {
    if (statusEl) {
      statusEl.style.color = 'var(--red)';
      statusEl.textContent = '✗ Fehler: ' + e.message;
    }
  }
}

// ═══════════════════════════ SDR-TX (SoapySDR) ════════════════
// Discovery-only (ADR-16): Geräte kommen ausschließlich aus
// /api/sdr/devices (= SoapySDR.Device.enumerate()). Es gibt KEIN
// manuelles Args-/Plugin-Pfad-Feld. Recovery = Rescan-Button.
const sdrState = {
  devices: [],   // [{args, label, driver, serial, tx_capable, ...}]
  current: null, // ausgewählter Eintrag aus sdrState.devices
  caps:    null, // {gain_overall, sample_rates, antennas, …}
  saved:   null, // gespeicherter sdr_tx-Block (vom Server)
};

// ── SDR-PROFILE (Schritt 3 — analog zu TRX-Profilen) ────────────────

let _sdrScanDevices = [];   // Cache des letzten /api/sdr/scan

async function sdrScan() {
  const btn  = document.getElementById('sdr-scan-btn');
  const list = document.getElementById('sdr-devices-list');
  if (btn) btn.disabled = true;
  list.textContent = 'Suche läuft…';
  try {
    const res = await apiFetch('/api/sdr/scan');
    if (!res.available) {
      list.textContent = 'SoapySDR nicht installiert.';
      return;
    }
    _sdrScanDevices = res.devices || [];
    if (_sdrScanDevices.length === 0) {
      list.textContent = 'Keine SDR-Geräte gefunden.';
    } else {
      list.innerHTML = _sdrScanDevices.map((d, i) => {
        const caps = [];
        if (d.rx_capable) caps.push('RX');
        if (d.tx_capable) caps.push('TX');
        const badge = caps.length
          ? `<span style="color:var(--green)">${caps.join('+')}</span>`
          : '<span style="color:var(--text2)">?</span>';
        const err = d.probe_error
          ? ` <span style="color:var(--red)">⚠ ${_esc(d.probe_error)}</span>`
          : '';
        return `<div style="margin-bottom:4px">`
          + `<b>${_esc(d.label)}</b> ${badge}`
          + (d.serial ? ` · ${_esc(d.serial)}` : '')
          + err
          + ` <button class="btn secondary" `
          + `style="font-size:var(--fs-xs);padding:1px 6px;margin-left:6px" `
          + `onclick="sdrFillFromDevice(${i})">Übernehmen</button></div>`;
      }).join('');
    }
    // Dropdowns aktualisieren
    sdrUpdateDropdowns(res.profiles, res.active_rx, res.active_tx);
  } catch(e) {
    list.textContent = 'Fehler: ' + (e.message || e);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function sdrFillFromDevice(idx) {
  // Formular mit gescanntem Gerät befüllen (Index in _sdrScanDevices)
  const d = _sdrScanDevices[idx];
  if (!d) return;
  const n = document.getElementById('sdr-edit-name');
  if (n && !n.value) n.value = d.label || d.driver || '';
  const t = document.getElementById('sdr-edit-type');
  if (t) t.value = d.type || '';
  const dr = document.getElementById('sdr-edit-driver');
  if (dr) dr.value = d.driver || '';
  const s = document.getElementById('sdr-edit-serial');
  if (s) s.value = d.serial || '';
}

function sdrUpdateDropdowns(profiles, activeRx, activeTx) {
  ['rx','tx'].forEach(dir => {
    const sel = document.getElementById('sdr-active-' + dir);
    if (!sel) return;
    // Bestehende Optionen außer der leeren entfernen
    while (sel.options.length > 1) sel.remove(1);
    (profiles || []).forEach(p => {
      const ok = dir === 'rx'
        ? ['rx','trx'].includes(p.type)
        : ['tx','trx'].includes(p.type);
      if (!ok) return;
      const opt = document.createElement('option');
      opt.value = p.name;
      opt.textContent = p.name
        + (p._comment ? ' — ' + p._comment : '');
      sel.appendChild(opt);
    });
    sel.value = (dir === 'rx' ? activeRx : activeTx) || '';
  });
}

async function sdrActivate(dir, name) {
  try {
    await apiFetch('/api/sdr/profile/activate/' + dir, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ name: name || null })
    });
  } catch(e) {
    alert('Fehler: ' + (e.message || e));
  }
}

async function sdrSaveProfile() {
  const msg  = document.getElementById('sdr-edit-msg');
  const name = document.getElementById('sdr-edit-name').value.trim();
  if (!name) { msg.textContent = 'Profilname fehlt.'; return; }
  const body = {
    name,
    type:    document.getElementById('sdr-edit-type').value.trim()
             || 'rx',
    driver:  document.getElementById('sdr-edit-driver').value.trim(),
    serial:  document.getElementById('sdr-edit-serial').value.trim()
             || undefined,
    _comment: document.getElementById('sdr-edit-comment').value.trim()
             || undefined,
  };
  try {
    const res = await apiFetch('/api/sdr/profile/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    msg.textContent = res.message || 'Gespeichert.';
    msg.style.color = 'var(--green)';
    await sdrScan();
  } catch(e) {
    msg.textContent = e.message || 'Fehler.';
    msg.style.color = 'var(--red)';
  }
}

async function sdrDeleteProfile() {
  const name = document.getElementById('sdr-edit-name').value.trim();
  if (!name) return;
  if (!confirm('SDR-Profil "' + name + '" löschen?')) return;
  const msg = document.getElementById('sdr-edit-msg');
  try {
    const res = await apiFetch(
      '/api/sdr/profile?name=' + encodeURIComponent(name),
      { method: 'DELETE' });
    msg.textContent = res.message || 'Gelöscht.';
    msg.style.color = 'var(--green)';
    document.getElementById('sdr-edit-name').value = '';
    await sdrScan();
  } catch(e) {
    msg.textContent = e.message || 'Fehler.';
    msg.style.color = 'var(--red)';
  }
}

function _sdrArgsKey(args) {
  // Stabiler Schlüssel für ein <option value>: driver + identity-Feld.
  // Wir sortieren die Keys, damit dieselben Args denselben Key ergeben,
  // egal wie die GUI sie aufschreibt.
  if (!args || typeof args !== 'object') return '';
  const keys = Object.keys(args).sort();
  return keys.map(k => `${k}=${args[k]}`).join('|');
}

async function loadSdrDevices() {
  const sel    = document.getElementById('sdr-device');
  const modBox = document.getElementById('sdr-modules-list');
  if (!sel) return;
  try {
    const r = await apiFetch('/api/sdr/devices');
    sdrState.devices = r.devices || [];
    // Modul-Diagnose ausgeben
    if (modBox) {
      if (!r.available) {
        modBox.textContent = 'SoapySDR-Bindings nicht installiert.';
      } else if ((r.modules || []).length === 0) {
        modBox.textContent = 'Keine Module geladen.';
      } else {
        modBox.textContent = r.modules.map(m =>
          (m.version ? `[${m.version}] ` : '') + m.path).join('\n');
      }
    }
    // Dropdown befüllen — RX-only ausgrauen
    const opts = ['<option value="">' + t('cfg.sdr.no_device') + '</option>'];
    sdrState.devices.forEach(d => {
      const k = _sdrArgsKey(d.args);
      const lbl = `${d.label || d.driver}` +
                  (d.serial ? `  ·  ${d.serial.slice(-8)}` : '') +
                  (d.tx_capable ? '' : '  · RX-only');
      opts.push(
        `<option value="${k}"${d.tx_capable ? '' : ' disabled'}>${lbl}</option>`
      );
    });
    sel.innerHTML = opts.join('');
    // Aktuell konfigurierte Auswahl re-selektieren
    const want = _sdrArgsKey(r.selected || (sdrState.saved && sdrState.saved.device_args));
    if (want) {
      sel.value = want;
      await onSdrDeviceChange();
    }
  } catch (e) {
    sel.innerHTML = `<option>Fehler: ${e.message}</option>`;
  }
}

async function rescanSdrDevices() {
  const sel = document.getElementById('sdr-device');
  if (sel) sel.innerHTML = '<option>Suche Geräte …</option>';
  await loadSdrDevices();
}

async function onSdrDeviceChange() {
  const sel = document.getElementById('sdr-device');
  if (!sel) return;
  const k = sel.value;
  sdrState.current = sdrState.devices.find(d => _sdrArgsKey(d.args) === k) || null;
  if (!sdrState.current) { sdrState.caps = null; return; }

  // Caps frisch vom Server holen (Gain/SR/Antenne sind treiberabhängig)
  const qs = new URLSearchParams(sdrState.current.args).toString();
  try {
    const r = await apiFetch('/api/sdr/caps?' + qs);
    sdrState.caps = r.caps || null;
  } catch (e) {
    sdrState.caps = null;
    // Selbst ohne Caps soll die UI nicht hängen — wir behalten Default-Felder
  }
  _renderSdrCaps();
}

function _renderSdrCaps() {
  const caps = sdrState.caps || {};
  // Antennen
  const aSel = document.getElementById('sdr-antenna');
  if (aSel) {
    const ants = caps.antennas || [];
    const opts = ['<option value="">– Default –</option>']
      .concat(ants.map(a => `<option value="${a}">${a}</option>`));
    aSel.innerHTML = opts.join('');
    const want = (sdrState.saved && sdrState.saved.antenna) || '';
    if (want && ants.includes(want)) aSel.value = want;
  }
  // Sample-Rates — diskrete Liste hat Vorrang, sonst Range als Hinweis
  const sSel = document.getElementById('sdr-sr');
  if (sSel) {
    const list = caps.sample_rates || [];
    let opts;
    if (list.length > 0) {
      opts = list.map(r =>
        `<option value="${r}">${Math.round(r).toLocaleString('de-AT')} Hz</option>`);
    } else if ((caps.sample_rate_ranges || []).length) {
      const r0 = caps.sample_rate_ranges[0];
      opts = [`<option value="${r0.min}">${Math.round(r0.min).toLocaleString('de-AT')} Hz (min)</option>`];
      // Übliche HackRF-Stufe als zweite Wahl
      if (r0.max >= 2_000_000) opts.push('<option value="2000000">2 000 000 Hz</option>');
    } else {
      opts = ['<option value="2000000">2 000 000 Hz</option>'];
    }
    sSel.innerHTML = opts.join('');
    const want = String((sdrState.saved && sdrState.saved.sample_rate) || '');
    if (want) {
      // Wenn der gespeicherte Wert nicht in der Liste ist, als Option ergänzen
      if (!Array.from(sSel.options).some(o => o.value === want)) {
        const o = document.createElement('option');
        o.value = want; o.textContent = `${Math.round(want).toLocaleString('de-AT')} Hz (gespeichert)`;
        sSel.insertBefore(o, sSel.firstChild);
      }
      sSel.value = want;
    }
  }
  // Gain-Elemente diagnostisch zeigen (treiberabhängig — z.B. HackRF AMP/VGA)
  const gBox = document.getElementById('sdr-gain-elements');
  if (gBox) {
    const el = caps.gain_elements || [];
    if (el.length) {
      gBox.style.display = '';
      gBox.textContent = 'Gain-Elemente: ' +
        el.map(e => `${e.name} (${e.min}…${e.max} dB)`).join(', ');
    } else {
      gBox.style.display = 'none';
    }
  }
}

async function loadSdrConfig() {
  const statusEl = document.getElementById('sdr-cfg-status');
  try {
    const r = await apiFetch('/api/sdr/config');
    const sdr = r.sdr_tx || {};
    sdrState.saved = sdr;

    document.getElementById('sdr-enabled').checked = !!sdr.enabled;
    document.getElementById('sdr-freq').value     = sdr.freq_hz     || 14110000;
    const gainVal = (sdr.gain && sdr.gain.normalized != null)
      ? Number(sdr.gain.normalized) : 0.5;
    document.getElementById('sdr-gain').value     = gainVal;
    document.getElementById('sdr-gain-val').textContent = gainVal.toFixed(2);

    // Devices laden — _select_ erfolgt darin anhand sdrState.saved
    await loadSdrDevices();

    if (statusEl) { statusEl.style.display = 'none'; statusEl.textContent = ''; }
  } catch (e) {
    if (statusEl) {
      statusEl.style.display = '';
      statusEl.style.color = 'var(--red)';
      statusEl.textContent = '✗ Konfig konnte nicht geladen werden: ' + e.message;
    }
  }
}

async function saveSdrConfig() {
  const enabled = document.getElementById('sdr-enabled').checked;
  const sel     = document.getElementById('sdr-device');
  const dev     = sdrState.devices.find(d => _sdrArgsKey(d.args) === sel.value);
  const sr      = parseFloat(document.getElementById('sdr-sr').value || '0');
  const freq    = parseFloat(document.getElementById('sdr-freq').value || '0');
  const ant     = document.getElementById('sdr-antenna').value || '';
  const gain    = parseFloat(document.getElementById('sdr-gain').value || '0');

  const body = {
    enabled:     enabled,
    device_args: dev ? dev.args : {},
    label:       dev ? (dev.label || '') : '',
    sample_rate: sr,
    freq_hz:     freq,
    antenna:     ant,
    gain:        { normalized: gain },
    tx_channel:  0,
  };

  const statusEl = document.getElementById('sdr-cfg-status');
  if (statusEl) statusEl.style.display = 'none';
  try {
    const r = await apiFetch('/api/sdr/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    sdrState.saved = r.sdr_tx;
    if (statusEl) {
      statusEl.className = 'ok'; statusEl.style.display = 'block';
      statusEl.style.color = 'var(--green)';
      statusEl.textContent = '✓ ' + (r.message || 'SDR-TX-Konfiguration gespeichert');
    }
  } catch (e) {
    if (statusEl) {
      statusEl.className = 'err'; statusEl.style.display = 'block';
      statusEl.style.color = 'var(--red)';
      statusEl.textContent = '✗ ' + e.message;
    }
  }
}

// Range-Slider live anzeigen
document.addEventListener('DOMContentLoaded', () => {
  const gEl = document.getElementById('sdr-gain');
  const gV  = document.getElementById('sdr-gain-val');
  if (gEl && gV) {
    gEl.addEventListener('input', () => { gV.textContent = parseFloat(gEl.value).toFixed(2); });
  }
  const dSel = document.getElementById('sdr-device');
  if (dSel) dSel.addEventListener('change', onSdrDeviceChange);
});

// ═══════════════════════════ Config Sub-Tab-Navigation ════════════════

function switchCfgTab(id, btn) {
  // Alle Sub-Panels deaktivieren
  document.querySelectorAll('.cfg-subpanel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.cfg-subnav button').forEach(b => b.classList.remove('active'));
  // Gewähltes Panel + Button aktivieren
  document.getElementById(id)?.classList.add('active');
  btn?.classList.add('active');
  // Aktiven Sub-Tab in localStorage merken
  localStorage.setItem('gust_cfg_subtab', id);
}

// Beim Laden: gespeicherten Sub-Tab wiederherstellen + Hamlib-Felder befüllen
document.addEventListener('DOMContentLoaded', function restoreCfgSubTab() {
  const saved = localStorage.getItem('gust_cfg_subtab') || 'cfg-audio';
  const panel = document.getElementById(saved);
  if (!panel) return;
  const btn = document.querySelector(`.cfg-subnav button[onclick*="'${saved}'"]`);
  // Immer explizit aufrufen — setzt active-Klasse auf Button + Panel
  switchCfgTab(saved, btn || document.querySelector('.cfg-subnav button'));
  // Hamlib-Felder immer beim Seitenladen befüllen (nicht nur bei Tab-Klick)
  rescanHamlibPorts().then(() => loadHamlibConfig());
  // rigctld-Status beim Seitenladen automatisch prüfen und anzeigen
  testHamlibConnection();
});

// ═══════════════════════════ Hamlib / rigctld ════════════════════════

async function rescanHamlibPorts() {
  const sel = document.getElementById('hamlib-port');
  sel.innerHTML = '<option value="">Scanne …</option>';
  try {
    const r = await apiFetch('/api/hamlib/ports');
    sel.innerHTML = '<option value="">– Port wählen –</option>';
    (r.ports || []).forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.device;
      opt.textContent = `${p.device}  ${p.description ? '— ' + p.description : ''}`.trim();
      sel.appendChild(opt);
    });
    if ((r.ports || []).length === 0)
      sel.innerHTML = '<option value="">Keine seriellen Ports gefunden</option>';
  } catch(e) {
    sel.innerHTML = `<option value="">Fehler: ${e.message}</option>`;
  }
}

let _hamlibModelTimer = null;
function searchHamlibModels(q) {
  clearTimeout(_hamlibModelTimer);
  _hamlibModelTimer = setTimeout(async () => {
    const list = document.getElementById('hamlib-model-list');
    list.innerHTML = '<option value="">Suche …</option>';
    try {
      const r = await apiFetch('/api/hamlib/models?q=' + encodeURIComponent(q));
      list.innerHTML = '';
      if (!r.models || r.models.length === 0) {
        list.innerHTML = '<option value="">Keine Treffer</option>';
        return;
      }
      r.models.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = `[${m.id}]  ${m.label}`;
        list.appendChild(opt);
      });
    } catch(e) {
      list.innerHTML = `<option value="">Fehler: ${e.message}</option>`;
    }
  }, 300);  // 300 ms Debounce
}

function onHamlibModelSelect(sel) {
  const opt = sel.options[sel.selectedIndex];
  if (!opt || !opt.value) return;
  document.getElementById('hamlib-model-id').value = opt.value;
  document.getElementById('hamlib-model-selected').textContent = opt.textContent.trim();
}

async function loadHamlibConfig() {
  // Liest aktuellen rigctld-Block aus gateway.json via /api/config
  try {
    const cfg = await apiFetch('/api/config');
    const rig = cfg.rigctld || {};
    // Port vorbelegen
    const portSel = document.getElementById('hamlib-port');
    if (rig.device) {
      // Existierenden Wert als Option eintragen falls noch nicht vorhanden
      if (![...portSel.options].some(o => o.value === rig.device)) {
        const opt = document.createElement('option');
        opt.value = rig.device;
        opt.textContent = rig.device + '  (aus Konfiguration)';
        portSel.appendChild(opt);
      }
      portSel.value = rig.device;
    }
    // Modell — ID setzen und Label via Suche auflösen
    if (rig.rig_model) {
      document.getElementById('hamlib-model-id').value = rig.rig_model;
      // Label via ID-Suche auflösen und ersten passenden Eintrag selektieren
      searchHamlibModels(String(rig.rig_model));
      document.getElementById('hamlib-model-selected').textContent =
        `Modell ${rig.rig_model}  (wird aufgelöst …)`;
      // Nach Debounce (350ms) + Netzwerk: passenden Eintrag in Listbox selektieren
      setTimeout(() => {
        const list = document.getElementById('hamlib-model-list');
        if (!list) return;
        // Eintrag mit exakt dieser ID suchen
        const match = [...list.options].find(o => parseInt(o.value, 10) === rig.rig_model);
        if (match) {
          match.selected = true;
          onHamlibModelSelect(list);
        }
      }, 800);
    }
    // Baudrate
    if (rig.baud) document.getElementById('hamlib-baud').value = String(rig.baud);
    // Auto-Start
    document.getElementById('hamlib-autostart').checked = rig.auto_start !== false;
  } catch(e) {
    document.getElementById('cfg-hamlib-status').textContent = '✗ ' + e.message;
  }
  loadTrxProfiles();
}

async function saveHamlibConfig() {
  const statusEl = document.getElementById('cfg-hamlib-status');
  const modelId  = parseInt(document.getElementById('hamlib-model-id').value, 10);
  const device   = document.getElementById('hamlib-port').value;
  const baud     = parseInt(document.getElementById('hamlib-baud').value, 10);
  const autoStart = document.getElementById('hamlib-autostart').checked;

  if (!modelId) {
    statusEl.style.color = 'var(--red)';
    statusEl.textContent = '✗ Bitte ein Rig-Modell aus der Suche wählen.';
    return;
  }
  if (!device) {
    statusEl.style.color = 'var(--red)';
    statusEl.textContent = '✗ Bitte einen seriellen Port wählen.';
    return;
  }
  try {
    const r = await apiFetch('/api/hamlib/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ rig_model: modelId, device, baud, auto_start: autoStart }),
    });

    // Port-Konflikt — User fragen
    if (!r.ok && r.conflict) {
      statusEl.style.color = 'var(--orange)';
      statusEl.textContent = '⚠ ' + r.message;
      // Konflikt-Dialog anzeigen
      _showHamlibConflictDialog(r.pid, r.proc_name, r.port);
      return;
    }

    statusEl.style.color = r.ok ? 'var(--green)' : 'var(--red)';
    statusEl.textContent = (r.ok ? '✓ ' : '✗ ') + (r.message || r.error || '');
    if (r.ok) { loadStatus(); _testHamlibDelayed(2000); loadTrxProfiles(); }
  } catch(e) {
    statusEl.style.color = 'var(--red)';
    statusEl.textContent = '✗ ' + e.message;
  }
}

// ═══════════════════════ TRX-PROFILE ══════════════════════
// Lädt trx_profiles aus /api/config und befüllt das Dropdown.
// Wird beim Laden des Hamlib-Tabs aufgerufen.
let _trxProfiles = [];  // Cache für Profile — befüllt von loadTrxProfiles()
function loadTrxProfiles() {
  apiFetch('/api/config').then(cfg => {
    const profiles = cfg.trx_profiles || [];
    _trxProfiles = profiles;  // Cache aktualisieren
    const active   = cfg.active_trx_profile || '';
    const row      = document.getElementById('trx-profile-row');
    const sel      = document.getElementById('trx-profile-select');
    const info     = document.getElementById('trx-profile-active');
    if (!profiles.length) {
      if (row)  row.classList.add('hidden');
      if (info) info.style.display = 'none';
      return;
    }
    if (row) row.classList.remove('hidden');
    if (sel) {
      sel.innerHTML = profiles.map(p =>
        `<option value="${_esc(p.name)}" ${p.name === active ? 'selected' : ''}>
           ${_esc(p.name)}
         </option>`
      ).join('');
    }
    if (info && active) {
      info.style.display = 'block';
      info.textContent   = `Aktives Profil: ${active}`;
    }
    // Bearbeitungsformular mit der aktuellen Auswahl befüllen
    const current = profiles.find(p => p.name === (sel?.value || active))
                    || profiles[0];
    if (current) trxFillEditForm(current);
  }).catch(() => {});
}

function onTrxProfileChange(name) {
  const info = document.getElementById('trx-profile-active');
  if (info) info.textContent = `Auswahl: ${name} — noch nicht aktiviert`;
  // Profilwerte sofort in Formular anzeigen (Vorschau vor Aktivieren)
  const p = _trxProfiles.find(x => x.name === name);
  if (!p) return;
  trxFillEditForm(p);   // Bearbeitungsformular mitziehen
  // Serieller Port
  const portSel = document.getElementById('hamlib-port');
  if (portSel && p.device) {
    if (![...portSel.options].some(o => o.value === p.device)) {
      const opt = document.createElement('option');
      opt.value = p.device;
      opt.textContent = p.device + '  (aus Profil)';
      portSel.appendChild(opt);
    }
    portSel.value = p.device;
  }
  // Rig-Modell
  if (p.rig_model) {
    document.getElementById('hamlib-model-id').value = p.rig_model;
    document.getElementById('hamlib-model-selected').textContent = `Modell ${p.rig_model}  (wird aufgelöst …)`;
    searchHamlibModels(String(p.rig_model));
    setTimeout(() => {
      const list = document.getElementById('hamlib-model-list');
      if (!list) return;
      const match = [...list.options].find(o => parseInt(o.value, 10) === p.rig_model);
      if (match) { match.selected = true; onHamlibModelSelect(list); }
    }, 800);
  }
  // Baudrate
  if (p.baud) document.getElementById('hamlib-baud').value = String(p.baud);
}

// ── TRX-Profil-Verwaltung (Anlegen / Bearbeiten / Löschen) ──────────

function trxFillEditForm(profile) {
  // Formular mit Profildaten füllen
  document.getElementById('trx-edit-name').value =
    profile.name || '';
  document.getElementById('trx-edit-rigmodel').value =
    profile.rig_model || '';
  document.getElementById('trx-edit-device').value =
    profile.device || '';
  const baudSel = document.getElementById('trx-edit-baud');
  if (baudSel) baudSel.value = String(profile.baud || 19200);
  document.getElementById('trx-edit-tx-dev').value =
    profile.audio_device_tx ?? '';
  document.getElementById('trx-edit-rx-dev').value =
    profile.audio_device_rx ?? '';
  document.getElementById('trx-edit-ptt-delay').value =
    profile.ptt_delay_ms ?? 250;
  document.getElementById('trx-edit-level').value =
    profile.level ?? 30;
  document.getElementById('trx-edit-comment').value =
    profile._comment || '';
}

function trxNewProfile() {
  // Formular leeren für ein neues Profil
  trxFillEditForm({});
  document.getElementById('trx-edit-name').focus();
  document.getElementById('trx-edit-msg').textContent = '';
}

async function trxSaveProfile() {
  const msg = document.getElementById('trx-edit-msg');
  const name = document.getElementById('trx-edit-name').value.trim();
  if (!name) { msg.textContent = 'Profilname fehlt.'; return; }

  const body = {
    name,
    rig_model:      parseInt(document.getElementById('trx-edit-rigmodel').value) || 0,
    device:         document.getElementById('trx-edit-device').value.trim(),
    baud:           parseInt(document.getElementById('trx-edit-baud').value) || 19200,
    audio_device_tx: document.getElementById('trx-edit-tx-dev').value !== ''
                     ? parseInt(document.getElementById('trx-edit-tx-dev').value)
                     : null,
    audio_device_rx: document.getElementById('trx-edit-rx-dev').value !== ''
                     ? parseInt(document.getElementById('trx-edit-rx-dev').value)
                     : null,
    ptt_backend:    'hamlib',
    auto_start:     true,
    ptt_delay_ms:   parseInt(document.getElementById('trx-edit-ptt-delay').value) || 250,
    level:          parseInt(document.getElementById('trx-edit-level').value) || 30,
    _comment:       document.getElementById('trx-edit-comment').value.trim(),
  };

  try {
    const res = await apiFetch('/api/trx/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    msg.textContent = res.message || 'Gespeichert.';
    msg.style.color = 'var(--green)';
    // Dropdown aktualisieren
    loadTrxProfiles();
  } catch(e) {
    msg.textContent = e.message || 'Fehler beim Speichern.';
    msg.style.color = 'var(--red)';
  }
}

async function trxDeleteProfile() {
  const name = document.getElementById('trx-edit-name').value.trim();
  if (!name) return;
  if (!confirm('Profil "' + name + '" wirklich löschen?')) return;
  const msg = document.getElementById('trx-edit-msg');
  try {
    const res = await apiFetch(
      '/api/trx/profile?name=' + encodeURIComponent(name),
      { method: 'DELETE' }
    );
    if (res.ok === false) {
      msg.textContent = res.error || 'Löschen nicht möglich.';
      msg.style.color = 'var(--red)';
      return;
    }
    msg.textContent = res.message || 'Gelöscht.';
    msg.style.color = 'var(--green)';
    trxNewProfile();
    loadTrxProfiles();
  } catch(e) {
    msg.textContent = e.message || 'Fehler beim Löschen.';
    msg.style.color = 'var(--red)';
  }
}

async function activateTrxProfile() {
  const sel      = document.getElementById('trx-profile-select');
  const statusEl = document.getElementById('cfg-hamlib-status');
  const name     = sel?.value;
  if (!name) return;
  try {
    const r = await apiFetch('/api/trx/activate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ name }),
    });

    // Port-Konflikt — gleiche Behandlung wie saveHamlibConfig (Kill-Dialog)
    if (!r.ok && r.conflict) {
      if (statusEl) { statusEl.style.color = 'var(--orange)'; statusEl.textContent = '⚠ ' + r.message; }
      _showHamlibConflictDialog(r.pid, r.proc_name, r.port);
      return;
    }

    if (statusEl) {
      statusEl.style.color   = r.ok ? 'var(--green)' : 'var(--red)';
      statusEl.textContent   = (r.ok ? '✓ ' : '✗ ') + (r.message || r.error || '');
    }
    // Formular immer aktualisieren wenn kein Konflikt — Config wurde gespeichert,
    // auch wenn rigctld-Start scheiterte (z.B. TRX nicht angeschlossen).
    if (!r.conflict) {
      const info = document.getElementById('trx-profile-active');
      if (info) { info.style.display = 'block'; info.textContent = `Aktives Profil: ${name}`; }
      rescanHamlibPorts().then(() => loadHamlibConfig()); // Ports neu einlesen, dann Felder befüllen
      if (r.ok) _testHamlibDelayed(2000);  // rigctld-Status nur prüfen wenn erfolgreich gestartet
    }
  } catch(e) {
    if (statusEl) { statusEl.style.color = 'var(--red)'; statusEl.textContent = '✗ ' + e.message; }
  }
}

function _showHamlibConflictDialog(pid, procName, port) {
  // Bestehendes Dialog entfernen falls vorhanden
  document.getElementById('hamlib-conflict-dlg')?.remove();

  const dlg = document.createElement('div');
  dlg.id = 'hamlib-conflict-dlg';
  dlg.style.cssText = [
    'position:fixed', 'inset:0', 'background:rgba(0,0,0,0.6)',
    'display:flex', 'align-items:center', 'justify-content:center',
    'z-index:9999', 'font-family:inherit'
  ].join(';');

  dlg.innerHTML = `
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;
                padding:24px;max-width:480px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,0.5);">
      <h3 style="margin:0 0 12px;color:var(--orange);">⚠ Port-Konflikt</h3>
      <p style="margin:0 0 16px;color:var(--text);line-height:1.5;">
        Um den TRx über Hamlib zu steuern, muss rigctld auf Port
        <strong>${port}</strong> gestartet werden.<br><br>
        Dieser Port wird aktuell von
        <strong style="color:var(--accent);">${_esc(procName)}</strong>
        (PID ${pid}) belegt.<br><br>
        Soll dieser Prozess beendet und rigctld gestartet werden?
      </p>
      <div style="display:flex;gap:10px;justify-content:flex-end;">
        <button class="btn secondary" onclick="document.getElementById('hamlib-conflict-dlg').remove()">
          ✗ Abbrechen
        </button>
        <button class="btn" style="background:var(--orange);border-color:var(--orange);"
                onclick="_hamlibForceRestart(${pid})">
          ✓ Beenden &amp; rigctld starten
        </button>
      </div>
    </div>
  `;
  document.body.appendChild(dlg);
}

async function _hamlibForceRestart(pid) {
  document.getElementById('hamlib-conflict-dlg')?.remove();
  const statusEl = document.getElementById('cfg-hamlib-status');
  statusEl.style.color = 'var(--text2)';
  statusEl.textContent = '⟳ Prozess wird beendet, rigctld startet …';
  try {
    const r = await apiFetch('/api/hamlib/force_restart', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ pid }),
    });
    statusEl.style.color = r.ok ? 'var(--green)' : 'var(--red)';
    statusEl.textContent = (r.ok ? '✓ ' : '✗ ') + (r.message || r.error || '');
    if (r.ok) { loadStatus(); _testHamlibDelayed(2000); }
  } catch(e) {
    statusEl.style.color = 'var(--red)';
    statusEl.textContent = '✗ ' + e.message;
  }
}

async function testHamlibConnection() {
  const dotEl  = document.getElementById('hamlib-status-dot');
  const txtEl  = document.getElementById('hamlib-status-text');
  const rowEl  = document.getElementById('hamlib-status-row');
  rowEl.style.display = 'flex';
  dotEl.className = 'hamlib-status-dot';
  txtEl.textContent = 'Prüfe rigctld …';
  try {
    // 1) Status prüfen — läuft rigctld bereits?
    let status = await apiFetch('/api/hamlib/status');
    if (!status.running) {
      // 2) Nicht erreichbar → starten
      txtEl.textContent = 'Starte rigctld …';
      const startResult = await apiFetch('/api/hamlib/start', { method: 'POST' });
      if (!startResult.ok) {
        dotEl.classList.add('err');
        txtEl.textContent = '✗ Start fehlgeschlagen: ' + (startResult.error || '');
        return;
      }
      // 3) Nach Start erneut Status abfragen
      status = await apiFetch('/api/hamlib/status');
    }
    if (status.running) {
      dotEl.classList.add('ok');
      const freq = status.freq_hz
        ? (status.freq_hz / 1e6).toFixed(6) + ' MHz'
        : '(Frequenz unbekannt)';
      txtEl.textContent = t('cfg.hamlib.status_running') + ' ' + freq;
      startHamlibPolling();
    } else {
      dotEl.classList.add('err');
      txtEl.textContent = 'rigctld nicht erreichbar' + (status.error ? ': ' + status.error : '');
    }
  } catch(e) {
    dotEl.classList.add('err');
    txtEl.textContent = 'Fehler: ' + e.message;
  }
}

function _testHamlibDelayed(delayMs) {
  // Wartet delayMs Millisekunden bevor testHamlibConnection() aufgerufen
  // wird — gibt rigctld nach einem Neustart Zeit zum Hochfahren.
  // Standard-Delay: 2000 ms (rigctld braucht ~1-2 s auf Windows/RPi).
  // Tune-Button während des Delays deaktivieren — verhindert Tune-Klick
  // bevor rigctld mit neuen Parametern erreichbar ist.
  const tuneBtn = document.getElementById('tune-btn');
  if (tuneBtn) {
    tuneBtn.disabled = true;
    tuneBtn.title    = 'Warte auf rigctld …';
  }
  setTimeout(() => {
    testHamlibConnection().finally(() => {
      if (tuneBtn) {
        tuneBtn.disabled = false;
        tuneBtn.title    = '';
      }
    });
  }, delayMs || 2000);
}

// ── Hamlib Frequenz-Polling (alle 5 s) ──────────────────────
let _hamlibPollTimer = null;

function startHamlibPolling() {
  stopHamlibPolling();
  _hamlibPollTimer = setInterval(async () => {
    const dotEl = document.getElementById('hamlib-status-dot');
    const txtEl = document.getElementById('hamlib-status-text');
    const rowEl = document.getElementById('hamlib-status-row');
    if (!dotEl || !txtEl || !rowEl) return;
    // Nicht pollen während GUST sendet — verhindert Kollision mit HamlibPTT._cmd()
    if (state.isSending) return;
    try {
      const status = await apiFetch('/api/hamlib/status');
      rowEl.style.display = 'flex';
      if (status.running) {
        dotEl.className = 'hamlib-status-dot ok';
        const freq = status.freq_hz
          ? (status.freq_hz / 1e6).toFixed(6) + ' MHz  (Update alle 5 s)'
          : '(Frequenz unbekannt)';
        txtEl.textContent = t('cfg.hamlib.status_running') + ' ' + freq;
      } else {
        dotEl.className = 'hamlib-status-dot err';
        txtEl.textContent = 'rigctld nicht erreichbar' + (status.error ? ': ' + status.error : '');
        stopHamlibPolling();
      }
    } catch(e) {
      // Netzwerkfehler — Polling nicht stoppen, nächster Versuch in 5 s
    }
  }, 5000);
}

function stopHamlibPolling() {
  if (_hamlibPollTimer !== null) {
    clearInterval(_hamlibPollTimer);
    _hamlibPollTimer = null;
  }
}

// ── Tune Toggle ───────────────────────────────────────────────
let _tuneTimer = null;

async function toggleTune() {
  const btn = document.getElementById('tune-btn');
  if (!btn) return;
  if (btn.dataset.tuning === '1') {
    // Zweiter Klick → stoppen
    await _tuneStop();
  } else {
    // Erster Klick → starten
    stopHamlibPolling();   // Polling VOR dem API-Call stoppen — rigctld akzeptiert
                           // auf Windows nur eine TCP-Verbindung gleichzeitig
    try {
      const r = await apiFetch('/api/tx/tune', { method: 'POST' });
      if (!r.ok) {
        document.getElementById('cfg-hamlib-status').textContent =
          '✗ Tune fehlgeschlagen: ' + (r.error || '');
        startHamlibPolling();   // Polling wieder aktivieren nach Fehler
        return;
      }
    } catch(e) {
      document.getElementById('cfg-hamlib-status').textContent =
        '✗ Tune Fehler: ' + e.message;
      startHamlibPolling();   // Polling wieder aktivieren nach Fehler
      return;
    }
    // Button auf aktiv schalten
    btn.dataset.tuning    = '1';
    btn.style.borderColor = 'var(--red)';
    btn.style.color       = 'var(--red)';
    _setOnAir(true);
    let remaining = 15;
    btn.textContent = '⏹ Tune (' + remaining + ' s)';
    _tuneTimer = setInterval(() => {
      remaining--;
      if (remaining <= 0) {
        _tuneReset();
        startHamlibPolling();
      } else {
        btn.textContent = '⏹ Tune (' + remaining + ' s)';
      }
    }, 1000);
  }
}

async function _tuneStop() {
  try { await apiFetch('/api/tx/tune_stop', { method: 'POST' }); }
  catch(e) { /* ignorieren */ }
  _tuneReset();
  startHamlibPolling();
}

function _tuneReset() {
  _setOnAir(false);   // ON-AIR-Banner löschen (beide Tune-Enden: Stop + 15 s-Ablauf)
  if (_tuneTimer) { clearInterval(_tuneTimer); _tuneTimer = null; }
  const btn = document.getElementById('tune-btn');
  if (!btn) return;
  btn.dataset.tuning    = '0';
  btn.style.borderColor = 'var(--orange)';
  btn.style.color       = 'var(--orange)';
  btn.textContent       = t('btn.tune');
}

// Hamlib-Unterseite beim Öffnen automatisch befüllen (lazy):
// Beim Aktivieren der Hamlib-Sektion Ports + gespeicherte Konfiguration laden.
document.querySelector('.cfg-subnav button[onclick*="cfg-hamlib"]')
  ?.addEventListener('click', () => {
    rescanHamlibPorts().then(() => loadHamlibConfig());
    testHamlibConnection();
  });

// ═══════════════════════════ CHANNEL GRID ═════════════════════
function buildChannelGrid(homeChannel) {
  const grid = document.getElementById('channel-grid');
  const plan = [
    [0,'600–850'],[1,'850–1100'],[2,'1100–1350'],[3,'1350–1600'],
    [4,'1600–1850'],[5,'1850–2100'],[6,'2100–2350'],[7,'2350–2600']
  ];
  grid.innerHTML = plan.map(([ch, freq]) => `
    <div class="ch-card ${ch === homeChannel ? 'home' : ''}" id="ch-card-${ch}">
      <div class="ch-num">${ch}${ch === homeChannel ? ' ★' : ''}</div>
      <div class="ch-freq">${freq} Hz</div>
      <div class="ch-last" id="ch-last-${ch}">–</div>
      <div id="ch-test-${ch}" style="margin-top:3px;min-height:14px"></div>
      <div class="ch-info" id="ch-info-${ch}"></div>
    </div>`).join('');
  // Klick auf Kachel → Detail-Modal des zuletzt empfangenen Frames.
  // Handler EINMALIG hier setzen (nicht bei jedem Update → kein Stapeln).
  plan.forEach(([ch]) => {
    const card = document.getElementById('ch-card-' + ch);
    if (!card) return;
    card.addEventListener('click', () => {
      if (card._lastFrame) openFrameModal(card._lastFrame);
    });
  });
}

function snrClass(snr) {
  if (snr == null) return '';
  return snr > 15 ? 'snr-hi' : snr >= 8 ? 'snr-mid' : 'snr-lo';
}
function snrLabel(snr) {
  if (snr == null) return '';
  return (snr > 0 ? '+' : '') + snr.toFixed(1) + ' dB';
}

function updateChannelCard(ch, from, typeName, tsStr, snr, isEmerg, isTest, frameData) {
  const lastEl = document.getElementById('ch-last-' + ch);
  const infoEl = document.getElementById('ch-info-' + ch);
  const card   = document.getElementById('ch-card-' + ch);
  if (!lastEl) return;
  if (card && frameData) card._lastFrame = frameData;   // für Klick → Modal
  lastEl.textContent = from + ' · ' + typeName;
  const testEl = document.getElementById('ch-test-' + ch);
  if (testEl) testEl.innerHTML  = isTest ? '<span class="test-pill" style="font-size:var(--fs-xxs)">TEST</span>' : '';
  if (infoEl) {
    // SNR und Zeit in EINER Zeile, getrennt durch " · ". SNR behält
    // seine Farb-Klasse (snr-hi/mid/lo); ohne SNR nur die Zeit.
    const snrStr  = snr != null ? snrLabel(snr) : '';
    const snrPart = snrStr
      ? `<span class="${snrClass(snr)}">${snrStr}</span> · `
      : '';
    infoEl.innerHTML = (snrStr || tsStr) ? (snrPart + (tsStr || '')) : '–';
  }
  if (isEmerg) {
    card.classList.remove('active');
    card.classList.add('emerg-active');
  } else {
    card.classList.remove('emerg-active');   // Notfall-Rot zurücksetzen
    card.classList.add('active');
    setTimeout(() => card.classList.remove('active'), 8000);
  }
}

// ═══════════════════════════ RX FEED ══════════════════════════
// Retroaktives 🔑-Badge: ein AUTH-Frame (0x50) hat einen zuvor empfangenen
// Daten-Frame per HMAC verifiziert. Der jüngste passende Daten-Frame
// (gleiches Rufzeichen + REF_TYPE) bekommt nachträglich das Schlüssel-Badge.
function markFrameAuthenticated(d) {
  const from    = d.from     ?? '';
  const refType = d.ref_type ?? -1;
  const feed    = document.getElementById('rx-feed');
  if (!feed) return;
  const rows = Array.from(feed.querySelectorAll('.frame-row'));
  for (let i = rows.length - 1; i >= 0; i--) {
    const row = rows[i];
    if (row.dataset.from === from && parseInt(row.dataset.type) === refType) {
      if (!row.querySelector('.auth-pill')) {
        const badge = document.createElement('span');
        badge.className = 'auth-pill';
        badge.title = `HMAC verifiziert (KEY_ID=${d.key_id ?? '?'})`;
        badge.textContent = '🔑';
        const container = row.querySelector('.badge-container');
        if (container) {
          // Reihenfolge: TEST → 🔑 → 🔍, also vor das Deep-Badge einfügen
          const deep = container.querySelector('[title*="Deep"]');
          if (deep) container.insertBefore(badge, deep);
          else      container.appendChild(badge);
        } else {
          // Fallback für ältere Zeilen ohne Container: nach der .type-Span
          const typeSpan = row.querySelector('.type');
          if (typeSpan && typeSpan.parentNode)
            typeSpan.parentNode.insertBefore(badge, typeSpan.nextSibling);
          else
            row.appendChild(badge);
        }
      }
      break;  // nur den jüngsten passenden Frame markieren
    }
  }
  // Swimlane-Frame ebenfalls markieren → 🔑 im Block (slDraw)
  if (typeof sl !== 'undefined' && sl.frames) {
    for (let i = sl.frames.length - 1; i >= 0; i--) {
      const f = sl.frames[i];
      const ftype = f.raw?.type ?? f.raw?.frame_type ?? -1;
      if (f.callsign === from && ftype === refType) {
        f.authenticated = true;
        break;
      }
    }
  }
}

// Live-Feed-Anzeigehöhe (Dropdown). #rx-feed nutzt CSS height (nicht
// max-height) → hier ebenfalls style.height setzen, sonst wächst der Feed nicht.
function setFeedHeight(px) {
  const feed = document.getElementById('rx-feed');
  if (feed) feed.style.height = px + 'px';
  try { localStorage.setItem('gust_feed_height', px); } catch(e) {}
}
document.addEventListener('DOMContentLoaded', () => {
  try {
    const saved = localStorage.getItem('gust_feed_height');
    if (saved) {
      setFeedHeight(saved);
      const sel = document.getElementById('feed-height-sel');
      if (sel) sel.value = saved;
    }
  } catch(e) {}
});
function appendRxFrame(frame) {
  if (state.isSending && document.getElementById('ignore-rx-while-tx')?.checked) return;
  state.rxCount++;
  document.getElementById('s-rx-count').textContent = state.rxCount;

  const feed = document.getElementById('rx-feed');
  // Leere Platzhalter entfernen
  const placeholder = feed.querySelector('[style*="color:var(--text2)"]');
  if (placeholder) placeholder.remove();

  const ts  = new Date(frame.ts * 1000).toLocaleTimeString('de-AT');
  const ch  = frame.channel ?? frame.detected_channel ?? '?';
  const isMC = frame.source === 'meshcore';
  const frm  = _esc(frame.from ?? '?');
  const typ = frame.type_name ?? '?';
  const dat = frameDataSummary(frame);
  const _ftype  = frame.frame_type ?? frame.type ?? 0;
  const isEmerg = (_ftype === 0x20 || _ftype === 0x21
               || frame.type_name === 'EMERG_BEACON'
               || frame.type_name === 'EMERG_RSRC');

  const snr  = frame.snr_db  ?? frame._snr_db  ?? null;
  // Aktivitätslog-Eintrag für jeden empfangenen Frame
  const _actExtra = [
    ch !== '?' ? 'Kanal ' + ch : null,
    snr != null ? 'SNR ' + snr.toFixed(1) + ' dB' : null,
  ].filter(Boolean).join(' · ');
  activityLog('rx', typ, frm, _actExtra);
  const off  = frame.freq_offset_hz ?? frame.offset_hz ?? null;
  const offStr  = off  != null ? (off > 0 ? '+' : '') + off.toFixed(0) + ' Hz' : '';
  const snrCls  = snrClass(snr);

  const row = document.createElement('div');
  const isTest = !!frame.test;
  // AUTH-Badge: gesetzt wenn ein AUTH-Frame (0x50) diesen Daten-Frame
  // per HMAC verifiziert hat (gust_rx._verify_auth, P8-11).
  const authBadge = frame.authenticated
      ? '<span class="auth-pill" title="HMAC-authentifiziert — Herkunft verifiziert">🔑</span>'
      : '';
  // Deep-Decode-Fund (vom parallelen 20s-Decoder nachgeliefert)
  const deepBadge = frame.deep
      ? '<span title="Deep-Decode-Fund" style="font-size:0.9em;margin-left:2px;'
        + 'vertical-align:middle;cursor:default">🔍</span>'
      : '';
  row.className = 'frame-row' + (isEmerg ? ' emergency' : '') + (isTest ? ' testframe' : '') + (isMC ? ' meshcore' : '');
  // Matching-Attribute für das retroaktive AUTH-Badge (frame_authenticated):
  row.dataset.from = frame.from ?? '';
  row.dataset.type = String(_ftype);
  const mcBadge = isMC ? '<span class="mc-badge">MC</span>' : '';
  row.innerHTML = `<span class="ts">${ts}</span>
    <span class="ch">${ch}</span>
    <span class="from">${mcBadge}${frm}</span>
    <span style="display:flex;align-items:center;gap:5px;width:122px;flex-shrink:0"><span class="type" style="width:auto">${typ}</span><span class="badge-container" style="display:flex;align-items:center;gap:3px">${isTest ? '<span class="test-pill">TEST</span>' : ''}${authBadge}${deepBadge}</span></span>
    <span class="snr ${snrCls}">${snr != null ? snrLabel(snr) : '–'}</span>
    <span class="off">${offStr}</span>
    <span class="data">${dat}</span>`;
  row.addEventListener('click', () => openFrameModal(frame));
  feed.appendChild(row);

  // Multi-Fragment-TEXT: Teile sammeln und bei Vollständigkeit als Klartext zeigen
  if (_ftype === 0x40 || frame.type_name === 'TEXT') {
    const fd    = frame.payload_decoded || frame.data || {};
    const total = fd.frag_total ?? 1;
    if (total > 1) {
      const idx    = fd.frag_index ?? 0;
      const seqKey = `${frm}:${fd.seq_nr ?? 0}`;
      const dest   = fd.dest || fd.to || '?';

      // Einzelframe als kollabierbare Zeile markieren
      row.classList.add('frag-row');
      row.dataset.seqKey = seqKey;

      if (!state.fragCache[seqKey])
        state.fragCache[seqKey] = {
          total, frags: {}, ts, ch, frm, dest,
          t0: Date.now(), rows: [], assembledRow: null
        };
      state.fragCache[seqKey].frags[idx] = fd.text || '';
      state.fragCache[seqKey].rows.push(row);

      const cached   = state.fragCache[seqKey];
      const received = Object.keys(cached.frags).length;

      if (received >= total) {
        // Alle Teile da → reassemblieren
        const assembled = Object.keys(cached.frags)
          .sort((a, b) => Number(a) - Number(b))
          .map(k => cached.frags[k]).join('');

        // Synthetisches Frame-Objekt für das Detail-Modal
        const assembledFrame = {
          type_name:   'TEXT',
          frame_type:  0x40,
          from:        cached.frm,
          source:      frame.source || null,
          channel:     cached.ch,
          ts:          cached.t0 / 1000,
          snr_db:      null,
          freq_offset_hz: null,
          _assembled:  true,
          _frag_total: total,
          _frag_count: received,
          data: {
            dest:       cached.dest,
            text:       assembled,
            seq_nr:     fd.seq_nr ?? 0,
            frag_total: total,
            frag_index: total - 1,
          },
          payload_decoded: {
            dest:       cached.dest,
            text:       assembled,
            seq_nr:     fd.seq_nr ?? 0,
            frag_total: total,
            frag_index: total - 1,
          },
        };

        // Zusammenfassungszeile bauen
        const isMCassembled = (frame.source === 'meshcore');
        const mcBadgeA = isMCassembled ? '<span class="mc-badge">MC</span>' : '';
        const arow = document.createElement('div');
        arow.className = 'frame-row assembled-row' + (isMCassembled ? ' meshcore' : '');
        arow.dataset.seqKey = seqKey;

        // Toggle-Button + Inhalt
        const toggleBtn = document.createElement('span');
        toggleBtn.className = 'frag-toggle';
        toggleBtn.textContent = '▶';
        toggleBtn.title = 'Einzelframes ein-/ausklappen';

        arow.innerHTML = `<span class="ts">${cached.ts}</span>
          <span class="ch">${cached.ch}</span>
          <span class="from">${mcBadgeA}${_esc(cached.frm)}</span>
          <span style="display:flex;align-items:center;gap:5px;width:122px;flex-shrink:0">
            <span class="type" style="width:auto;color:var(--green)">TEXT ✓</span>
          </span>
          <span class="snr">–</span>
          <span class="off"></span>
          <span class="data">→ ${_esc(cached.dest)}  "${_esc(assembled)}"
            <span style="color:var(--text2);font-size:var(--fs-xxs)">[${total}/${total} Frg. ✓]</span>
          </span>`;

        // Toggle-Button VOR dem ersten span einfügen
        arow.insertBefore(toggleBtn, arow.firstChild);

        // Toggle-Klick: Einzelframes ein-/ausklappen
        toggleBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          const fragRows = feed.querySelectorAll(`.frag-row[data-seq-key="${seqKey}"]`);
          const expanded = toggleBtn.textContent === '▼';
          fragRows.forEach(r => r.classList.toggle('expanded', !expanded));
          toggleBtn.textContent = expanded ? '▶' : '▼';
        });

        // Zeilen-Klick: Detail-Modal öffnen
        arow.addEventListener('click', () => openFrameModal(assembledFrame));

        cached.assembledRow = arow;
        feed.appendChild(arow);

        delete state.fragCache[seqKey];
      }

      // Verwaiste Cache-Einträge (> 120 s) verwerfen
      const now = Date.now();
      for (const k of Object.keys(state.fragCache))
        if (now - (state.fragCache[k].t0 || now) > 120000) delete state.fragCache[k];
    }
  }

  // Maximal 100 Zeilen im DOM
  while (feed.children.length > 100) feed.removeChild(feed.firstChild);

  if (document.getElementById('autoscroll').checked)
    feed.scrollTop = feed.scrollHeight;

  updateChannelCard(ch, frm, typ, ts, snr, isEmerg, isTest, frame);

  // Audio-Alerts
  if (isEmerg && document.getElementById('toggle-audio-emerg')?.checked)
    playAlarm();
  const myCall = state.callsign?.toUpperCase();
  if (myCall && (frame.type_name === 'TEXT') &&
      (frame.payload_decoded?.dest || frame.data?.dest || '')
        .toUpperCase() === myCall &&
      document.getElementById('toggle-audio-mine')?.checked)
    playNotify();

  // Inbox: an mich adressierte Freitext-Nachrichten sammeln
  const _ftInbox = frame.frame_type ?? frame.type;
  const _fdInbox = frame.payload_decoded || frame.data || {};
  const _destInbox = (frame.to ?? _fdInbox.dest ?? _fdInbox.to ?? '')
    .toString().toUpperCase();
  if ((_ftInbox === TEXT_FRAME_TYPE || frame.type_name === 'TEXT') &&
      myCall && _destInbox === myCall) {
    inboxAddFrame(frame);
  }
}

// ═══════════════════════════ INBOX (EMPFANGEN) ════════════════
// Sammelt an das eigene Rufzeichen adressierte Freitext-Frames (0x40),
// gruppiert mehrteilige Sequenzen und rendert sie im Empfangen-Tab.
function _esc(s) {
  return String(s ?? '').replace(/[&<>"]/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

function _fragOf(f) { return f.payload_decoded || f.data || {}; }

function inboxAddFrame(frame) {
  const fd    = _fragOf(frame);
  const frm   = frame.from ?? '?';
  const to    = (frame.to ?? fd.dest ?? fd.to ?? state.callsign ?? '?');
  const seqId = fd.seq_nr ?? frame.seq_id ?? frame.sequence_id ?? null;
  const total = fd.frag_total ?? frame.seq_total ?? 1;
  const index = fd.frag_index ?? frame.seq_index ?? 0;
  const text  = fd.text ?? '';
  const ts    = frame.ts ?? (Date.now() / 1000);

  let msg;
  if (!seqId || total <= 1) {
    // ── Einzelframe ──
    msg = {
      id: `single:${ts}:${frm}`, type: 'single', ts, from: frm, to,
      text, frames: [frame], complete: true, total: 1, received: 1, read: false,
    };
    state.inbox.unshift(msg);
  } else {
    // ── Multi-Frame ──
    const seqKey = `seq:${frm}:${seqId}`;
    msg = state.inbox.find(m => m.type === 'multi' && m.id === seqKey);
    if (!msg) {
      msg = { id: seqKey, type: 'multi', ts, from: frm, to, text: '',
              frames: [], complete: false, total, received: 0, read: false };
      state.inbox.unshift(msg);
    }
    // Doppelte Fragmente (gleicher Index) ignorieren
    if (!msg.frames.some(f => (_fragOf(f).frag_index ?? 0) === index)) {
      msg.frames.push(frame);
    }
    msg.total    = Math.max(msg.total, total);
    msg.received = msg.frames.length;
    msg.complete = msg.received >= msg.total;
    // Volltext neu aus allen Fragmenten zusammensetzen (sortiert nach Index)
    msg.text = msg.frames.slice()
      .sort((a, b) => (_fragOf(a).frag_index ?? 0) - (_fragOf(b).frag_index ?? 0))
      .map(f => _fragOf(f).text ?? '').join('');
  }

  // Bei aktivem Inbox-Tab direkt als gelesen markieren
  const inboxActive = document.getElementById('tab-inbox')?.classList.contains('active');
  if (inboxActive) msg.read = true;

  renderInbox();
  updateInboxBadge();
}

// ═══════════════════════════ KOMMUNIKATION ════════════════════

function switchCommTab(which) {
  const isRx = which === 'rx';
  document.getElementById('comm-panel-rx').style.display = isRx ? '' : 'none';
  document.getElementById('comm-panel-tx').style.display = isRx ? 'none' : '';
  document.getElementById('comm-tab-rx').className = isRx ? 'btn active' : 'btn secondary';
  document.getElementById('comm-tab-tx').className = isRx ? 'btn secondary' : 'btn active';
}

// Gesendeten Frame aus tx_done-Event aufnehmen
function appendTxDone(data) {
  const ts       = Date.now() / 1000;
  const typeName = data.type_name || '?';
  const to       = data.to || '*';
  const frameData = data.data || {};

  // Text-Fragmente reassemblieren
  if (typeName === 'TEXT_FRAGMENT' || (data.frame_type === 0x40 || data.frame_type === 64)) {
    const seqNr  = frameData.seq_nr ?? 0;
    const idx    = frameData.frag_index ?? 0;
    const total  = frameData.frag_total ?? 1;
    const chunk  = frameData.text_chunk || frameData.text || '';
    const key    = String(seqNr);

    if (!state.sentFragCache[key])
      state.sentFragCache[key] = { total, frags: {}, ts, to, t0: Date.now() };
    state.sentFragCache[key].frags[idx] = chunk;

    const cached   = state.sentFragCache[key];
    const received = Object.keys(cached.frags).length;

    if (received >= total) {
      const assembled = Object.keys(cached.frags)
        .sort((a, b) => Number(a) - Number(b))
        .map(k => cached.frags[k]).join('');
      delete state.sentFragCache[key];
      // Vollständige Nachricht als sent-Eintrag speichern
      const entry = {
        ts:       cached.ts,
        type:     'text',
        typeName: 'TEXT',
        to:       cached.to,
        text:     assembled,
        total,
        channel:  data.channel,
        frames:   total,
      };
      state.sent.unshift(entry);
      renderSent();
      activityLog('tx', 'TEXT', cached.to,
        cached.total + ' Fr. · Kanal ' + (data.channel ?? '?'));
    }
    // Einzelfragmente nicht separat anzeigen
    return;
  }

  // Alle anderen Frame-Typen direkt aufnehmen
  const entry = {
    ts:       ts,
    type:     typeName.toLowerCase(),
    typeName: typeName,
    to:       to,
    data:     frameData,
    channel:  data.channel,
    frames:   1,
  };
  state.sent.unshift(entry);
  if (state.sent.length > 100) state.sent.pop();
  renderSent();
  activityLog('tx', entry.typeName, entry.to,
    entry.channel != null ? 'Kanal ' + entry.channel : '');
}

function renderSent() {
  const list  = document.getElementById('sent-list');
  const empty = document.getElementById('sent-empty');
  if (!list) return;
  if (!state.sent.length) {
    list.innerHTML = '';
    if (empty) empty.style.display = '';
    return;
  }
  if (empty) empty.style.display = 'none';
  list.innerHTML = '';
  state.sent.forEach(m => {
    const row  = document.createElement('div');
    row.className = 'inbox-item';
    const ts   = new Date(m.ts * 1000).toLocaleTimeString('de-AT');
    const icons = { text:'💬', weather:'🌤', position:'📍', emergency:'🆘' };
    const icon  = icons[m.type] || '📦';
    const ch    = m.channel != null ? ` · Kanal ${m.channel}` : '';

    let preview = '';
    if (m.type === 'text') {
      preview = m.text ? ('"' + m.text.slice(0, 80) + (m.text.length > 80 ? '…' : '') + '"') : '(kein Text)';
    } else if (m.type === 'weather') {
      const d = m.data || {};
      preview = [
        d.temp_c    != null ? d.temp_c + '°C'       : null,
        d.humidity_pct != null ? d.humidity_pct + '%' : null,
        d.pressure_hpa != null ? d.pressure_hpa + ' hPa' : null,
      ].filter(Boolean).join('  ');
    } else if (m.type === 'position') {
      const d = m.data || {};
      preview = d.lat != null ? d.lat.toFixed(4) + ', ' + d.lon.toFixed(4) : '';
    } else if (m.type === 'emergency') {
      preview = '🆘 NOTFALL';
    } else {
      preview = JSON.stringify(m.data || {}).slice(0, 60);
    }

    row.innerHTML =
      '<span class="ib-ts">' + ts + '</span>' +
      '<span class="ib-from" style="color:var(--accent);">' + icon + ' → ' + _esc(m.to) + '</span>' +
      '<span class="ib-type">' + m.typeName + (m.frames > 1 ? ' (' + m.frames + ' Fr.)' : '') + ch + '</span>' +
      '<span class="ib-preview">' + _esc(preview) + '</span>';
    list.appendChild(row);
  });
}

function updateInboxBadge() {
  const unread = state.inbox.filter(m => !m.read).length;
  state.inboxUnread = unread;
  const b = document.getElementById('inbox-badge');
  if (!b) return;
  if (unread > 0) { b.textContent = unread; b.classList.remove('hidden'); }
  else            { b.classList.add('hidden'); }
}

function renderInbox() {
  const list  = document.getElementById('inbox-list');
  const empty = document.getElementById('inbox-empty');
  if (!list) return;
  if (!state.inbox.length) {
    list.innerHTML = '';
    if (empty) empty.style.display = '';
    return;
  }
  if (empty) empty.style.display = 'none';
  list.innerHTML = '';
  state.inbox.forEach(m => {
    const row = document.createElement('div');
    row.className = 'inbox-item' + (m.read ? '' : ' inbox-unread');
    const ts = new Date(m.ts * 1000).toLocaleTimeString('de-AT');
    const typeBadge = m.type === 'multi'
      ? `<span class="ib-type multi">MULTI ${m.received}/${m.total}</span>`
      : `<span class="ib-type">SINGLE</span>`;
    const txt     = m.text || '';
    const preview = txt.slice(0, 60) + (txt.length > 60 ? '…' : '');
    row.innerHTML = `<span class="ib-ts">${ts}</span>
      <span class="ib-from">${_esc(m.from)}</span>
      ${typeBadge}
      <span class="ib-preview">${_esc(preview) || '(kein Text)'}</span>`;
    row.addEventListener('click', () => showInboxDetail(m));
    list.appendChild(row);
  });
}

function showInboxDetail(msg) {
  msg.read = true;
  updateInboxBadge();
  renderInbox();

  const box = document.getElementById('inbox-modal-content');
  if (!box) return;
  const ts = new Date(msg.ts * 1000).toLocaleString('de-AT');
  let html;

  if (msg.type === 'single') {
    const f   = msg.frames[0] || {};
    const ch  = f.channel ?? f.detected_channel ?? '?';
    const snr = f.snr_db ?? f._snr_db ?? null;
    html = `<h3>Nachricht von ${_esc(msg.from)} an ${_esc(msg.to)}</h3>
      <div style="color:var(--text2);font-size:var(--fs-xs);margin-bottom:8px;">${ts}</div>
      <div style="border-top:1px solid var(--border);border-bottom:1px solid var(--border);
           padding:10px 0;margin:8px 0;white-space:pre-wrap;">${_esc(msg.text) || '(kein Text)'}</div>
      <div style="font-size:var(--fs-sm);color:var(--text2);">Frame-Details:<br>
        Kanal: ${ch} &nbsp;|&nbsp; Frame-Type: 0x40 &nbsp;|&nbsp; CRC: OK
        &nbsp;|&nbsp; SNR: ${snr != null ? snr.toFixed(1) + ' dB' : '–'}</div>`;
  } else {
    // Fragmente nach Index indizieren
    const byIdx = {};
    msg.frames.forEach(f => { byIdx[_fragOf(f).frag_index ?? 0] = f; });
    // body: empfangene Chunks zusammensetzen, fehlende als […fehlt…]-Badge einsetzen
    let bodyParts = [];
    for (let i = 0; i < msg.total; i++) {
      if (byIdx[i]) {
        bodyParts.push(_esc(_fragOf(byIdx[i]).text ?? ''));
      } else {
        bodyParts.push(
          `<span style="display:inline-block;background:rgba(240,165,0,0.12);` +
          `border:1px dashed var(--orange);border-radius:3px;` +
          `color:var(--orange);font-size:0.85em;padding:1px 6px;` +
          `font-style:italic;vertical-align:middle;">${t('inbox.missing_frame')}</span>`
        );
      }
    }
    const body = msg.complete ? bodyParts.join('') : bodyParts.join('');
    let rows = '';
    for (let i = 0; i < msg.total; i++) {
      const f  = byIdx[i];
      const t  = f ? new Date((f.ts || msg.ts) * 1000).toLocaleTimeString('de-AT') : '–';
      const ch = f ? (f.channel ?? f.detected_channel ?? '?') : '–';
      const st = f ? t('inbox.status.ok') : t('inbox.status.missing');
      rows += `<tr><td>${i + 1}</td><td>${t}</td><td>${ch}</td><td>${st}</td></tr>`;
    }
    html = `<h3>Nachricht von ${_esc(msg.from)} an ${_esc(msg.to)}
        (${msg.received}/${msg.total} Frames)</h3>
      <div style="color:var(--text2);font-size:var(--fs-xs);margin-bottom:8px;">${ts}</div>
      <div style="border-top:1px solid var(--border);border-bottom:1px solid var(--border);
           padding:10px 0;margin:8px 0;white-space:pre-wrap;
           font-size:var(--fs-lg);color:var(--text);">${body || t('inbox.no_text')}</div>
      <div style="font-size:var(--fs-sm);color:var(--text2);">${t('inbox.frames_label')}</div>
      <table class="seq-table">
        <thead><tr><th>${t('inbox.col.index')}</th><th>${t('inbox.col.time')}</th><th>${t('inbox.col.channel')}</th><th>${t('inbox.col.status')}</th></tr></thead>
        <tbody>${rows}</tbody></table>`;
  }

  box.innerHTML = html;

  // ── Antwort-Block (nur bei vollständigen Nachrichten) ──
  const replyFrom = msg.from;
  if (msg.complete && replyFrom && replyFrom !== '?') {
    const replyDiv = document.createElement('div');
    replyDiv.className = 'inbox-reply-box';
    replyDiv.innerHTML = `
      <label>↩ Antwort an <strong>${_esc(replyFrom)}</strong>:</label>
      <textarea id="inbox-reply-text"
                placeholder="Antwort eingeben …"
                oninput="inboxReplyCount(this)"></textarea>
      <div class="inbox-reply-footer">
        <span class="inbox-reply-counter"
              id="inbox-reply-counter">0 Zeichen</span>
        <button class="btn"
                onclick="sendInboxReply('${_esc(replyFrom)}')"
                id="inbox-reply-btn">
          ↩ Senden
        </button>
      </div>
      <div class="inbox-reply-status" id="inbox-reply-status"></div>
    `;
    box.appendChild(replyDiv);
  }

  document.getElementById('inbox-modal').classList.add('open');
}

function closeInboxModal() {
  document.getElementById('inbox-modal').classList.remove('open');
}

// Zeichenzähler für Antwort-Textarea
function inboxReplyCount(ta) {
  const n  = ta.value.length;
  const el = document.getElementById('inbox-reply-counter');
  if (!el) return;
  // GUST TEXT-Frame: 14 Byte Text je Fragment (UTF-8),
  // Wire-Format max. 16 Fragmente = 224 Byte (BUG-09)
  const bytes = new TextEncoder().encode(ta.value).length;
  const frags = Math.max(1, Math.ceil(bytes / 14));
  el.textContent = n + ' Zeichen'
    + (frags > 1 ? ` (${frags} Fragmente)` : '');
  el.className = 'inbox-reply-counter'
    + (frags > 16 ? ' warn' : '');
}

async function sendInboxReply(toCall) {
  const ta  = document.getElementById('inbox-reply-text');
  const btn = document.getElementById('inbox-reply-btn');
  const st  = document.getElementById('inbox-reply-status');
  if (!ta || !btn || !st) return;

  const text = ta.value.trim();
  if (!text) {
    st.textContent = 'Bitte Antworttext eingeben.';
    st.className   = 'inbox-reply-status err';
    return;
  }

  btn.disabled   = true;
  st.textContent = 'Sende …';
  st.className   = 'inbox-reply-status';

  try {
    // Freitext-TX via bestehende API: POST /api/tx/text.
    // Feldname "to" (nicht "dest") — das Gateway liest
    // data.get("to") und fragmentiert serverseitig.
    const res = await apiFetch('/api/tx/text', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ to: toCall, text }),
    });

    if (res.ok !== false) {
      st.textContent = '✓ Gesendet an ' + toCall;
      st.className   = 'inbox-reply-status ok';
      ta.value       = '';
      inboxReplyCount(ta);
      // Nach 3s: Modal schließen
      setTimeout(() => closeInboxModal(), 3000);
    } else {
      throw new Error(res.error || res.message || 'Fehler');
    }
  } catch (e) {
    // Lesbaren Text aus rohem HTTP-Fehler extrahieren:
    // apiFetch wirft "HTTP 503: {"error":"Kein TX-Gateway..."}"
    // → nur den error-Wert aus dem JSON zeigen
    let msg = e.message || 'Senden fehlgeschlagen';
    try {
      const jsonStart = msg.indexOf('{');
      if (jsonStart >= 0) {
        const parsed = JSON.parse(msg.slice(jsonStart));
        msg = parsed.error || parsed.message || msg;
      }
    } catch (_) { /* JSON-Parse fehlgeschlagen → Originaltext */ }
    // HTTP-Statuscodes in sprechenden Text übersetzen
    if (msg.startsWith('HTTP 503')) msg = 'Kein TX-Gateway aktiv (Monitor-Modus)';
    if (msg.startsWith('HTTP 401')) msg = 'Nicht autorisiert';
    if (msg.startsWith('HTTP 400')) msg = 'Ungültige Eingabe';
    st.textContent = '✗ ' + msg;
    st.className   = 'inbox-reply-status err';
    btn.disabled   = false;
  }
}

function frameDataSummary(f) {
  // Echte RX-Frames: Payload unter payload_decoded
  // Simulator-Frames: unter data — beide abfangen
  const d = f.payload_decoded || f.data || {};
  const tn = f.type_name || '';
  if (!tn && !Object.keys(d).length) return '';

  // WEATHER
  if (tn === 'WEATHER' || f.frame_type === 0x01) {
    const rain = d.rain_mm_h > 0 ? `  🌧 ${d.rain_mm_h?.toFixed(1)} mm/h` : '';
    return `${d.temp_c?.toFixed(1)}°C  ${d.humidity_pct}%  `
         + `${d.pressure_hpa?.toFixed(1)} hPa  `
         + `Wind: ${d.wind_kmh} km/h ${d.wind_deg}°${rain}`;
  }

  // POSITION
  if (tn === 'POSITION' || f.frame_type === 0x02) {
    const spd = d.speed_kmh > 0 ? `  ${d.speed_kmh} km/h  ${d.heading_deg}°` : '';
    return `${d.lat_deg?.toFixed(5)}, ${d.lon_deg?.toFixed(5)}`
         + `  Alt: ${d.alt_m} m${spd}`;
  }

  // TEXT / QSO
  if (tn === 'TEXT' || f.frame_type === 0x40) {
    const dest      = d.dest || d.to || '?';
    const total     = d.frag_total ?? 1;
    const idx       = d.frag_index ?? 0;
    const fragBadge = total > 1 ? ` [${idx + 1}/${total}]` : '';
    return `→ ${dest}${fragBadge}  "${d.text}"`;
  }

  // CQ
  if (tn === 'CQ' || f.frame_type === 0x41)
    return `CQ CQ CQ de ${f.from || '?'}`;

  // EMERGENCY BEACON / EMERG_RSRC
  if (tn === 'EMERG_BEACON' || tn === 'EMERG_RSRC'
      || f.frame_type === 0x20 || f.frame_type === 0x21) {
    const inj  = ['unbekannt','leicht','schwer','kritisch'][d.injury_code] || d.injury_code;
    const prio = d.priority_str || d.priority || '?';
    const snip = d.text_snippet ? `  "${d.text_snippet}"` : '';
    const pos  = (d.lat_deg != null && d.lon_deg != null)
      ? `  📍 ${d.lat_deg.toFixed(4)}, ${d.lon_deg.toFixed(4)}` : '';
    return `⚠ ${d.persons} Person(en)  Verletzung: ${inj}  Prio: ${prio}${pos}${snip}`;
  }

  // SENSOR / Stations-Telemetrie
  if (tn === 'SENSOR' || f.frame_type === 0x03)
    return `${(d.voltage_mv/1000)?.toFixed(2)} V  ${d.current_ma} mA  `
         + `${d.temp_c?.toFixed(1)}°C  CPU: ${d.cpu_pct}%`;

  // Fallback: alle Felder als kompaktes JSON
  return JSON.stringify(d).slice(0, 100);
}

// ═══════════════════════════ AUDIO-METER (RX-Eingang) ════════════
// Zeigt RMS/Peak des RX-Audios als horizontale Balken.
// Skala: -60 dB (links) → 0 dB (rechts).
// Status:
//   "Stille"           — kein/sehr leises Signal (RMS < -55 dB)
//   "Sehr leise"       — -55 .. -40 dB
//   "OK"               — -40 ..  -6 dB
//   "Clipping!"        — Peak > -1 dB
//   "Kein Audio-Signal" — länger als 2 s keine Events
const AM_MIN_DB = -60, AM_MAX_DB = 0;
let _amTimer = null;

function _dbToPct(db) {
  if (db == null || db <= AM_MIN_DB) return 0;
  if (db >= AM_MAX_DB) return 100;
  return ((db - AM_MIN_DB) / (AM_MAX_DB - AM_MIN_DB)) * 100;
}

function updateAudioMeter(d) {
  const box = document.getElementById('audio-meter');
  if (!box) return;
  const rmsDb  = d.rms_db  ?? null;
  const peakDb = d.peak_db ?? null;

  document.getElementById('am-device').textContent  = d.device || 'Audio-Eingang';
  document.getElementById('am-rms-val').textContent =
    rmsDb  != null ? rmsDb.toFixed(1) + ' dB' : '–';
  document.getElementById('am-peak-val').textContent =
    peakDb != null ? peakDb.toFixed(1) + ' dB' : '–';

  const rmsFill  = document.getElementById('am-rms-fill');
  const peakFill = document.getElementById('am-peak-fill');
  rmsFill.style.width  = _dbToPct(rmsDb)  + '%';
  peakFill.style.width = _dbToPct(peakDb) + '%';

  // Farbcodierung am Peak orientiert (Clip-Erkennung)
  peakFill.className = 'am-fill';
  rmsFill.className  = 'am-fill';
  if (d.clipping || (peakDb != null && peakDb > -1)) {
    peakFill.classList.add('clip');
    rmsFill.classList.add('clip');
  } else if (peakDb != null && peakDb > -6) {
    peakFill.classList.add('warn');
  }

  // Status-Klassifikation (am RMS orientiert)
  box.className = '';
  let status;
  if (d.clipping) {
    box.classList.add('clip');
    status = '⚠ Clipping — Eingangspegel reduzieren!';
  } else if (rmsDb == null || rmsDb < -55) {
    box.classList.add('silent');
    status = t('monitor.silence');
  } else if (rmsDb < -40) {
    box.classList.add('weak');
    status = 'Sehr leise — möglicherweise zu wenig Pegel';
  } else {
    box.classList.add('ok');
    status = '✓ Signal erkannt';
  }
  document.getElementById('am-status').textContent = status;

  // Watchdog: wenn 2 s lang kein Event mehr kommt, "Kein Audio-Signal"
  clearTimeout(_amTimer);
  _amTimer = setTimeout(() => {
    const b = document.getElementById('audio-meter');
    if (!b) return;
    b.className = 'nosig';
    document.getElementById('am-status').textContent = 'Kein Audio-Signal (RX-Loop inaktiv?)';
    document.getElementById('am-rms-fill').style.width  = '0%';
    document.getElementById('am-peak-fill').style.width = '0%';
    document.getElementById('am-rms-val').textContent  = '–';
    document.getElementById('am-peak-val').textContent = '–';
  }, 2000);
}

// ═══════════════════════════ AUDIO ══════════════════════════════
let _audioCtx = null;
function _getCtx() {
  if (!_audioCtx) _audioCtx = new (window.AudioContext||window.webkitAudioContext)();
  return _audioCtx;
}
function _beep(freq, dur, vol=0.25, type='sine') {
  try {
    const ctx = _getCtx();
    const osc = ctx.createOscillator();
    const g   = ctx.createGain();
    osc.connect(g); g.connect(ctx.destination);
    osc.type = type; osc.frequency.value = freq;
    g.gain.setValueAtTime(vol, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + dur);
    osc.start(ctx.currentTime); osc.stop(ctx.currentTime + dur + 0.05);
  } catch(e) {}
}
function playAlarm() {
  // Dreiton-Alarm: hohe, absteigende Töne
  _beep(1200, 0.18, 0.35, 'square');
  setTimeout(() => _beep(900, 0.18, 0.35, 'square'), 220);
  setTimeout(() => _beep(600, 0.28, 0.35, 'square'), 440);
}
function playNotify() {
  // Weicher Doppelton
  _beep(880, 0.12, 0.2, 'sine');
  setTimeout(() => _beep(1100, 0.18, 0.2, 'sine'), 160);
}

// ═══════════════════════════ MODAL ═══════════════════════════════
function openFrameModal(frame) {
  // Zusammengeführte TEXT-Nachricht: erweitertes Detail
  if (frame._assembled) {
    const fd  = frame.payload_decoded || frame.data || {};
    const src = frame.source === 'meshcore' ? ' <span class="mc-badge">MC</span>' : '';
    const ts  = frame.ts ? new Date(frame.ts * 1000).toLocaleTimeString('de-AT') : '?';
    const rows = [
      ['Von',        _esc(frame.from || '?') + src],
      ['An',         _esc(fd.dest || '?')],
      ['Kanal',      frame.channel ?? '?'],
      ['Zeitpunkt',  ts],
      ['Sequenz-Nr', fd.seq_nr ?? '?'],
      ['Fragmente',  `${frame._frag_count} / ${frame._frag_total} empfangen`],
      ['Quelle',     frame.source === 'meshcore' ? 'MeshCore Bridge' : 'HF RX'],
      ['Volltext',   `<span style="font-family:monospace;word-break:break-all">${_esc(fd.text || '')}</span>`],
    ];
    const tableHtml = rows.map(([k, v]) =>
      `<tr><td style="color:var(--text2);padding:3px 10px 3px 0;white-space:nowrap">${k}</td>`
      + `<td style="padding:3px 0">${v}</td></tr>`
    ).join('');
    const el = document.getElementById('modal-content');
    if (el) {
      el.innerHTML = `<table style="border-collapse:collapse;width:100%">${tableHtml}</table>`;
      document.getElementById('modal-title').textContent = 'TEXT-Nachricht (vollständig)';
      document.getElementById('frame-modal').classList.add('open');
    }
    return;
  }

  const d  = frame.payload_decoded || frame.data || {};
  const tn = frame.type_name || '?';
  const ch = frame.channel  ?? frame.detected_channel ?? '?';
  const ts = frame.ts ? new Date(frame.ts*1000).toLocaleTimeString('de-AT') : '?';

  document.getElementById('modal-title').textContent =
    `${tn} · Kanal ${ch} · ${frame.from || '?'} · ${ts}`;

  const rows = [];
  const add = (k, v) => rows.push(`<div class="modal-row">
    <span class="modal-key">${k}</span>
    <span class="modal-val">${v !== null && v !== undefined ? v : '–'}</span></div>`);

  add('Typ',      tn);
  add('Von',      frame.from || '?');
  add('Kanal',    ch);
  add('SNR',      frame._snr_db != null ? `${frame._snr_db.toFixed(1)} dB` : '–');
  add('Offset',   frame.freq_offset_hz != null ? `${frame.freq_offset_hz.toFixed(1)} Hz` : '–');
  add('RS-Fehler', frame.rs_errors ?? '–');
  if (frame.test) add('🔬 Testframe', 'JA — Frame ist als Test gekennzeichnet');
  if (frame.authenticated)
    add('🔑 Authentifizierung',
        '<span style="color:#2e7d32">✓ HMAC verifiziert (KEY_ID im AUTH-Frame)</span>');

  // Payload-Felder
  if (Object.keys(d).length) {
    rows.push('<div style="height:6px"></div>');
    for (const [k, v] of Object.entries(d)) {
      if (k === 'flags') continue;
      let val = v;
      if (typeof v === 'number' && (k === 'lat_deg' || k === 'lon_deg'))
        val = v.toFixed(6) + '°';
      else if (typeof v === 'boolean')
        val = v ? '✓ ja' : '– nein';
      add(k, val);
    }
  }

  // Map-Link wenn Position vorhanden
  let mapHtml = '';
  if (d.lat_deg != null && d.lon_deg != null)
    mapHtml = `<a class="modal-map" href="https://www.openstreetmap.org/?mlat=${d.lat_deg}&mlon=${d.lon_deg}&zoom=15" target="_blank">
      🗺 Position auf OpenStreetMap öffnen</a>`;

  document.getElementById('modal-content').innerHTML = rows.join('') + mapHtml;
  document.getElementById('frame-modal').classList.add('open');
}
function closeModal() {
  document.getElementById('frame-modal').classList.remove('open');
}
document.addEventListener('keydown', e => { if(e.key==='Escape'){ closeModal(); closeInboxModal(); } });

// Toggle-Zustand in localStorage speichern
['toggle-audio-emerg','toggle-audio-mine'].forEach(id => {
  const el = document.getElementById(id);
  if (!el) return;
  el.checked = localStorage.getItem(id) === 'true';
  el.addEventListener('change', () => localStorage.setItem(id, el.checked));
});

// ═══════════════════════════ WEBSOCKET ════════════════════════
function connectWsRx() {
  const ind = document.getElementById('ws-indicator');
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const apiKey = document.querySelector('meta[name="api-key"]')?.content || '';
  const sep = apiKey ? '?api_key=' + encodeURIComponent(apiKey) : '';

  state.wsRx = new WebSocket(`${proto}://${location.host}/ws/rx${sep}`);
  state.wsRx.onopen = () => {
    ind.className = 'connected';
    ind.title = 'WebSocket verbunden';
    log2ui('INFO', t('ws.connected'));
  };
  state.wsRx.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === 'rx_frame')     { appendRxFrame(msg.data); slAddFrame(msg.data); }
      if (msg.type === 'frame_authenticated') markFrameAuthenticated(msg.data);
      if (msg.type === 'status')         applyStatusPush(msg.data);
      if (msg.type === 'rx_audio_level') updateAudioMeter(msg.data);
      if (msg.type === 'tx_done')      { state.isSending = false; _setOnAir(false); if (state._txDoneResolve) { const _r = state._txDoneResolve; state._txDoneResolve = null; _r(); } log2ui('INFO', t('tx.done') + ' ' + (msg.data?.type_name||'?')); fetchTxQueue(); appendTxDone(msg.data || {}); }
      if (msg.type === 'ping')           state.wsRx.send(JSON.stringify({type:'pong'}));
      if (msg.type === 'heartbeat')      updateDaemonHeartbeat(msg);
    } catch(e) { /* ignore malformed */ }
  };
  state.wsRx.onerror = () => { ind.className = 'error'; };
  state.wsRx.onclose = () => {
    // Daemon-Status auf WARN sobald WS abbricht — Watchdog übernimmt nach 22s
    if (state.daemonAlive) _setDaemonWarn();
    ind.className = '';
    log2ui('WARNING', t('ws.disconnected'));
    clearTimeout(state.wsRetryTimer);
    state.wsRetryTimer = setTimeout(connectWsRx, 5000);
  };
}

function connectWsLog() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const apiKey = document.querySelector('meta[name="api-key"]')?.content || '';
  const sep = apiKey ? '?api_key=' + encodeURIComponent(apiKey) : '';
  state.wsLog = new WebSocket(`${proto}://${location.host}/ws/log${sep}`);
  state.wsLog.onmessage = (evt) => {
    try { const m = JSON.parse(evt.data); log2ui(m.level, m.msg); } catch(e) {}
  };
  state.wsLog.onclose = () => setTimeout(connectWsLog, 8000);
}

// ═══════════════════════════ LOG FEED ═════════════════════════
// ═══════════════════════════ AKTIVITÄTSLOG ════════════════════

function activityLog(direction, typeName, peer, extra) {
  // direction: 'rx' oder 'tx'
  // typeName:  z.B. 'WEATHER', 'TEXT', 'POSITION'
  // peer:      Rufzeichen (von/an)
  // extra:     optionaler Zusatztext (Kanal, SNR, etc.)
  const feed = document.getElementById('activity-feed');
  if (!feed) return;
  // Platzhalter entfernen
  const ph = feed.querySelector('[style*="color:var(--text2)"]');
  if (ph && feed.children.length === 1) ph.remove();

  const ts   = new Date().toLocaleTimeString('de-AT');
  const arrow = direction === 'rx' ? '←' : '→';
  const color = direction === 'rx' ? 'var(--green)' : 'var(--accent)';
  const icons = { WEATHER:'🌤', POSITION:'📍', TEXT:'💬', TEXT_FRAGMENT:'💬',
                  EMERGENCY:'🆘', EMERG_BEACON:'🆘', EMERG_RSRC:'🆘' };
  const icon  = icons[typeName] || '📦';

  const line = document.createElement('div');
  line.style.cssText = 'padding:2px 0;border-bottom:1px solid var(--border);font-size:var(--fs-sm);';
  line.innerHTML =
    '<span style="color:var(--text2);margin-right:8px;">' + ts + '</span>' +
    '<span style="color:' + color + ';font-weight:bold;margin-right:6px;">' + arrow + '</span>' +
    '<span style="margin-right:6px;">' + icon + '</span>' +
    '<span style="color:' + color + ';margin-right:8px;">' + _esc(peer) + '</span>' +
    '<span style="color:var(--text2);margin-right:8px;">' + _esc(typeName) + '</span>' +
    (extra ? '<span style="color:var(--text2);font-size:var(--fs-xs);">' + _esc(extra) + '</span>' : '');
  feed.appendChild(line);
  while (feed.children.length > 200) feed.removeChild(feed.firstChild);
  feed.scrollTop = feed.scrollHeight;
}

function clearActivityLog() {
  const feed = document.getElementById('activity-feed');
  if (feed) feed.innerHTML = '<div style="color:var(--text2);">' + t('log.activity.empty') + '</div>';
}

function log2ui(level, msg) {
  const feed = document.getElementById('log-feed');
  const ts   = new Date().toLocaleTimeString('de-AT');
  const line = document.createElement('div');
  line.className = 'log-line ' + level;
  line.dataset.level = level;
  line.textContent = `[${ts}] ${level.padEnd(7)} ${msg}`;
  feed.appendChild(line);
  while (feed.children.length > 300) feed.removeChild(feed.firstChild);
  filterLogLevel();
  if (document.getElementById('log-autoscroll').checked)
    feed.scrollTop = feed.scrollHeight;
}

function filterLogLevel() {
  const sel = document.getElementById('log-level-filter').value;
  const order = {DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3};
  const min = order[sel] ?? 0;
  document.querySelectorAll('#log-feed .log-line').forEach(el => {
    el.style.display = (order[el.dataset.level] ?? 0) >= min ? '' : 'none';
  });
}

function clearLog() {
  document.getElementById('log-feed').innerHTML = '';
}

// ═══════════════════════════ HELPERS ══════════════════════════
function applyStatusPush(data) {
  if (data.callsign) {
    state.callsign = data.callsign;
    document.getElementById('callsign-badge').textContent = data.callsign;
  }
  if (data.home_channel != null && state.homeChannel === null) {
    state.homeChannel = data.home_channel;
    buildChannelGrid(state.homeChannel);
  }
  if (data.tx_interval_s) {
    state.txInterval = data.tx_interval_s;
    state.txOffset   = data.tx_time_offset_s || 0;
    const mins = Math.round(state.txInterval / 60);
    const el = document.getElementById('p4-interval');
    if (el) el.textContent = mins > 1 ? `${mins} min` : `${state.txInterval} s`;
    // QSO-Modus-Anzeige synchronisieren (Intervall kann sich durch Status-Push ändern)
    const qsoDisp = document.getElementById('qso-interval-display');
    if (qsoDisp && !state.qsoMode) qsoDisp.textContent = state.txInterval + ' s';
    _tickTxCountdown();
  }
  if (data.ptt_delay_ms != null) {
    const el = document.getElementById('cfg-ptt-delay');
    if (el) el.value = data.ptt_delay_ms;
  }
  if (data.tx_available != null) applyTxAvailability(data.tx_available);

  // MeshCore Bridge Status-Badge
  if (data.meshcore_bridge != null) {
    const mc  = data.meshcore_bridge;
    const el  = document.getElementById('mc-bridge-status');
    if (el) {
      el.style.display = mc.enabled ? '' : 'none';
      if (mc.enabled) {
        const cls = mc.connected ? 'hb-ok' : 'hb-warn';
        el.className = cls;
        el.title = mc.connected
          ? `MeshCore Bridge aktiv — ${mc.port}`
          : `MeshCore Bridge getrennt — ${mc.port}`;
      }
    }
  }
}

// Schaltet den Senden-Tab je nach TX-Verfügbarkeit (Daemon vs. Monitor-Modus).
// Im Monitor-Modus: Hinweisbanner einblenden + Bedienelemente deaktivieren,
// damit kein Komponieren ins Leere läuft (sonst 503 beim Absenden).
function applyTxAvailability(avail) {
  state.txAvailable = avail;
  const notice = document.getElementById('tx-unavailable');
  const panel  = document.getElementById('tab-tx');
  if (notice) notice.classList.toggle('hidden', !!avail);
  if (panel)  panel.classList.toggle('tx-disabled', !avail);
}

// ═══════════════════════════ TX COUNTDOWN ═════════════════════
// Berechnet Sekunden bis zum nächsten TX-Schedule (P4/P3).
// Der Schedule ist deterministisch: offset = SHA256(rufzeichen) % interval.
// Beide Gruppen P4 und P3 teilen denselben TX-Schedule.
function _nextCycleSecs() {
  // Im QSO-Modus: festes 60-s-Raster (kein Offset-Phasenversatz nötig)
  if (state.qsoMode) {
    const nowSec = Math.floor(Date.now() / 1000);
    const delta  = 60 - (nowSec % 60);
    return delta <= 2 ? delta + 60 : delta;   // < 2 s bis Slot → nächsten nehmen
  }
  const iv  = state.txInterval || 300;
  const off = state.txOffset   || 0;
  const nowInCycle = Math.floor(Date.now() / 1000) % iv;
  let delta = off - nowInCycle;
  if (delta <= 0) delta += iv;
  return delta;
}

function _fmtCountdown(s) {
  const m = Math.floor(s / 60), sec = s % 60;
  return m > 0 ? `${m}m ${String(sec).padStart(2,'0')}s` : `${sec}s`;
}

function _tickTxCountdown() {
  const d  = _nextCycleSecs();
  const el4 = document.getElementById('p4-next');
  const el3 = document.getElementById('p3-next');
  if (el4) el4.textContent = _fmtCountdown(d);
  if (el3) el3.textContent = _fmtCountdown(d);
}

setInterval(_tickTxCountdown, 1000);

// ══════════════════════ DAEMON HEARTBEAT ══════════════════════

function updateDaemonHeartbeat(msg) {
  state.lastHeartbeat = Date.now();
  state.daemonAlive   = true;

  const el  = document.getElementById('daemon-hb');
  const lbl = el.querySelector('.hb-label');

  const uptime = msg.uptime_s || 0;
  const h = Math.floor(uptime / 3600);
  const m = Math.floor((uptime % 3600) / 60);
  const uptimeStr = h > 0 ? `${h}h${m}m` : `${m}m`;

  const mode = msg.daemon_mode ? 'DAEMON' : 'MONITOR';
  lbl.textContent = `${mode}  ↑${uptimeStr}`;
  el.className = 'hb-alive';

  let titleParts = [`GUST ${mode} — läuft seit ${uptimeStr}`];
  if (msg.last_rx_ago_s != null) {
    const rxAgo = msg.last_rx_ago_s;
    const rxStr = rxAgo < 60 ? `${rxAgo}s` : `${Math.floor(rxAgo/60)}m${rxAgo%60}s`;
    titleParts.push(`Letzter RX: vor ${rxStr}`);
  } else {
    titleParts.push('Noch kein Frame empfangen');
  }
  el.title = titleParts.join('  ·  ');

  hideDaemonBanner();
}

function _setDaemonDead() {
  const el  = document.getElementById('daemon-hb');
  const lbl = el.querySelector('.hb-label');
  const ago = state.lastHeartbeat
    ? Math.floor((Date.now() - state.lastHeartbeat) / 1000)
    : null;
  el.className = 'hb-dead';
  lbl.textContent = 'OFFLINE';
  el.title = ago != null
    ? `Kein Heartbeat seit ${ago}s — Daemon ausgefallen?`
    : 'Daemon nicht erreichbar';
  showDaemonBanner(ago);
}

function _setDaemonWarn() {
  const el  = document.getElementById('daemon-hb');
  const lbl = el.querySelector('.hb-label');
  el.className = 'hb-warn';
  lbl.textContent = 'WARN';
  el.title = 'Heartbeat überfällig — Daemon reagiert nicht?';
}

function showDaemonBanner(agoSec) {
  const b  = document.getElementById('daemon-offline-banner');
  const ts = state.lastHeartbeat
    ? new Date(state.lastHeartbeat).toLocaleTimeString('de-AT')
    : '—';
  const agoStr = agoSec != null ? `  ·  vor ${agoSec}s` : '';
  b.textContent = `⚠  GUST Daemon nicht erreichbar  ·  Letzter Kontakt: ${ts}${agoStr}`;
  b.classList.add('visible');
}

function hideDaemonBanner() {
  document.getElementById('daemon-offline-banner').classList.remove('visible');
}

function startHeartbeatWatchdog() {
  // Prüft alle 5 s ob ein Heartbeat rechtzeitig ankam.
  // Zeitfenster:  > 12 s → WARN (1 HB überfällig)
  //               > 22 s → DEAD (2 HB verpasst, Daemon ausgefallen)
  setInterval(() => {
    if (state.lastHeartbeat === null) return;  // noch keinen HB empfangen
    const age = (Date.now() - state.lastHeartbeat) / 1000;
    if (age > 22) {
      _setDaemonDead();
    } else if (age > 12) {
      _setDaemonWarn();
    }
    // else: alive → updateDaemonHeartbeat() hält es grün
  }, 5000);
}

// ═══════════════════════════ TX-WARTESCHLANGE ═════════════════
// Holt die ausstehenden Frames samt geschätztem Sendezeitpunkt (eta_ts) vom
// Gateway und zeigt je Frame einen lokal tickenden Countdown. Die Liste wird
// periodisch und bei Ereignissen (Senden, TX fertig) neu geladen; der
// Countdown läuft dazwischen rein im Browser aus eta_ts weiter.
const TXQ_META = {
  // String keys (from enqueue frame_type field)
  weather:          {icon:'🌤', label:'Wetter',        prio:4},
  position:         {icon:'📍', label:'Position',       prio:3},
  text:             {icon:'💬', label:'Freitext',       prio:2},
  text_fragment:    {icon:'💬', label:'Text-Fragment',  prio:2},
  emergency:        {icon:'🆘', label:'Notfall',        prio:1},
  // Integer keys (from get_queue() backend — frame_type as int)
  1:  {icon:'🌤', label:'Wetter',        prio:4},
  2:  {icon:'📍', label:'Position',      prio:3},
  4:  {icon:'💬', label:'Freitext',      prio:2},
  0x40: {icon:'💬', label:'Text-Fragment', prio:2},
  64:   {icon:'💬', label:'Text-Fragment', prio:2},
  3:  {icon:'🆘', label:'Notfall',       prio:1},
};

async function clearTxQueue() {
  const btn = document.getElementById('btn-clear-queue');
  if (!confirm('Alle ausstehenden TX-Frames löschen?')) return;
  try {
    if (btn) btn.disabled = true;
    const r = await apiFetch('/api/tx/queue', { method: 'DELETE' });
    const el = document.getElementById('tx-result');
    if (el) {
      el.className = 'ok'; el.style.display = 'block';
      el.textContent = '✓ ' + (r.message || 'Warteschlange gelöscht.');
      setTimeout(() => el.style.display = 'none', 3000);
    }
    fetchTxQueue();
  } catch(e) {
    const el = document.getElementById('tx-result');
    if (el) {
      el.className = 'err'; el.style.display = 'block';
      el.textContent = '✗ Fehler: ' + e.message;
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function fetchTxQueue() {
  try {
    const r = await apiFetch('/api/tx/queue');
    state.txQueue = Array.isArray(r.queue) ? r.queue : [];
  } catch(e) { state.txQueue = []; }
  renderTxQueue();
}

function renderTxQueue() {
  const box = document.getElementById('tx-queue');
  if (!box) return;
  const q = state.txQueue || [];
  // Browser-seitige Fragment-Queue (noch nicht zum Backend gesendet) einblenden
  const fragQ = (state.txFragQueue || []).map((f, i) => ({
    frame_type: 'text_fragment',
    from:       '–',
    priority:   2,
    eta_ts:     null,
    _local:     true,
    _label:     `Fragment ${f.frag_index + 1}/${f.frag_total} (wartet auf Slot)`,
  }));
  const combined = [...q, ...fragQ];
  if (!combined.length) {
    box.innerHTML = '<div class="txq-empty">' + t('send.queue.empty') + '</div>';
    return;
  }
  box.innerHTML = combined.map((it, i) => {
    const m = TXQ_META[it.frame_type] ?? TXQ_META[String(it.frame_type)] ?? {icon:'📦', label:String(it.frame_type), prio:it.priority};
    const p = it.priority || m.prio;
    const at = it.eta_ts ? new Date(it.eta_ts*1000).toLocaleTimeString('de-AT') : '–';
    const lbl = it._label || `${m.icon} ${m.label}`;
    return `<div class="txq-row${i===0?' next':''}" data-eta="${it.eta_ts||0}">
      <span class="tx-prio-dot p${p}-dot"></span>
      <span class="txq-type">${lbl}</span>
      <span class="txq-prio p${p}-col">P${p}</span>
      <span class="txq-cd" id="txq-cd-${i}">${it._local ? 'wartet' : '–'}</span>
      <span class="txq-at">${it.eta_ts ? '≈ ' + at : ''}</span>
    </div>`;
  }).join('');
  _tickTxQueue();
}

function _tickTxQueue() {
  const now = Date.now() / 1000;
  document.querySelectorAll('#tx-queue .txq-row').forEach(row => {
    const eta = parseFloat(row.dataset.eta) || 0;
    const rem = Math.max(0, Math.round(eta - now));
    const cd  = row.querySelector('.txq-cd');
    if (!cd) return;
    const isLocal = row.dataset.eta === '0' && row.querySelector('.txq-cd')?.textContent === 'wartet';
    if (isLocal) return;   // lokale Fragment-Platzhalter — kein Countdown
    if (rem <= 0) { cd.textContent = 'sendet …'; cd.classList.add('now'); }
    else          { cd.textContent = 'in ' + _fmtCountdown(rem); cd.classList.remove('now'); }
  });
}

setInterval(_tickTxQueue, 1000);     // lokaler Countdown-Tick
setInterval(fetchTxQueue, 5000);     // periodischer Abgleich mit dem Gateway

async function apiFetch(path, opts = {}) {
  const headers = opts.headers || {};
  // API-Key aus meta-Tag falls vorhanden (für auth-geschützte Instanzen)
  const apiKey = document.querySelector('meta[name="api-key"]')?.content;
  if (apiKey) headers['X-API-Key'] = apiKey;
  const resp = await fetch(path, { ...opts, headers });
  if (!resp.ok) {
    const t = await resp.text();
    throw new Error(`HTTP ${resp.status}: ${t}`);
  }
  return resp.json();
}

function formatUptime(s) {
  if (s == null) return '–';
  s = Math.floor(s);
  const d   = Math.floor(s / 86400);
  const h   = Math.floor((s % 86400) / 3600);
  const m   = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const hh = String(h).padStart(2, '0');
  const mm = String(m).padStart(2, '0');
  const ss = String(sec).padStart(2, '0');
  return d > 0 ? `${d}d ${hh}h ${mm}m ${ss}s` : `${h}h ${mm}m ${ss}s`;
}

// Kontinuierlicher Uptime-Sekundenzähler — läuft unabhängig vom aktiven Tab.
// Basis (uptimeBase/uptimeBaseTs) wird bei jedem loadStatus() neu gesetzt.
function _tickUptime() {
  if (state.uptimeBase == null) return;
  const el = document.getElementById('s-uptime');
  if (!el) return;
  el.textContent = formatUptime(
    state.uptimeBase + Math.floor((Date.now() - state.uptimeBaseTs) / 1000));
}
setInterval(_tickUptime, 1000);

function fmtTs(ts) {
  if (!ts) return '–';
  return new Date(ts * 1000).toLocaleTimeString('de-AT');
}

// ═══════════════ STRESSTEST SESSION (Session-Recorder) ════════════════
let stPolling = null;

async function stSession(action) {
  let data;
  try {
    data = await apiFetch('/api/session/' + action, {method:'POST'});
  } catch(e) {
    log2ui('ERROR', 'Session: ' + e.message);
    return;
  }
  if (!data.ok) { alert('Fehler: ' + data.error); return; }
  stUpdateStatus();
  if (action === 'stop') {
    document.getElementById('st-evaluate-box').style.display = '';
    if (stPolling) { clearInterval(stPolling); stPolling = null; }
  } else {
    document.getElementById('st-report-box').style.display = 'none';
    if (stPolling) clearInterval(stPolling);
    stPolling = setInterval(stUpdateStatus, 2000);
  }
}

async function stUpdateStatus() {
  let d;
  try {
    d = await apiFetch('/api/session/status');
  } catch(e) { return; }
  const stateLabel = {idle:'Bereit', recording:'⏺ Aufnahme läuft', done:'Gestoppt'}[d.state] || d.state;
  document.getElementById('st-state-label').textContent = stateLabel;
  document.getElementById('st-frame-count').textContent = d.frame_count + ' Frames';
  const dur = document.getElementById('st-duration');
  if (d.started_at) {
    const end = d.stopped_at || (Date.now() / 1000);
    dur.textContent = 'Dauer: ' + Math.round(end - d.started_at) + ' s';
  } else {
    dur.textContent = '';
  }
  // Buttons
  document.getElementById('st-btn-start').disabled = (d.state === 'recording');
  document.getElementById('st-btn-stop').disabled  = (d.state !== 'recording');
  // Evaluate-Box: nach Stop zeigen
  if (d.state === 'done') {
    document.getElementById('st-evaluate-box').style.display = '';
    if (stPolling) { clearInterval(stPolling); stPolling = null; }
  }
  // Report (überlebt Seiten-Reload — kommt aus /api/session/status mit)
  if (d.report) stShowReport(d.report);
}

async function stEvaluate() {
  const input = document.getElementById('st-csv-input');
  if (!input.files.length) { alert('Bitte eine CSV-Datei auswählen.'); return; }
  const form = new FormData();
  form.append('csv', input.files[0]);
  let data;
  try {
    data = await apiFetch('/api/session/evaluate', {method:'POST', body:form});
  } catch(e) {
    log2ui('ERROR', 'Session-Auswertung: ' + e.message);
    return;
  }
  if (!data.ok) { alert('Auswertungsfehler: ' + data.error); return; }
  stShowReport(data.report);
}

function stShowReport(report) {
  if (!report) return;
  const box = document.getElementById('st-report-box');
  box.style.display = '';
  const passColor = report.pass ? 'var(--green)' : 'var(--orange)';
  document.getElementById('st-report-summary').innerHTML =
    `<div style="font-size:1.3em;font-weight:bold;color:${passColor};margin-bottom:8px">` +
    `${report.decode_rate.toFixed(1)} % ` +
    `<span style="font-size:0.7em">${report.pass ? '✓ PASS (≥80%)' : '✗ FAIL (<80%)'}</span></div>` +
    `Erwartet: ${report.n_expected} &nbsp;|&nbsp; ` +
    `Gefunden: ${report.n_found} &nbsp;|&nbsp; ` +
    `Gematcht: ${report.n_matched} &nbsp;|&nbsp; ` +
    `Fehlend: <b>${report.n_missed}</b> &nbsp;|&nbsp; ` +
    `Überzählig: ${report.n_extra}` +
    (report.n_dual_bonus ? ` &nbsp;|&nbsp; Dual-Bonus: ${report.n_dual_bonus}` : '') +
    (report.session_duration_s ? `<div style="margin-top:6px;color:var(--text2)">Session-Dauer: ${report.session_duration_s} s</div>` : '');

  // Fehlende Frames Tabelle
  const missedBox = document.getElementById('st-missed-box');
  if (report.missed && report.missed.length > 0) {
    missedBox.style.display = '';
    const tbody = document.getElementById('st-missed-tbody');
    tbody.innerHTML = '';
    for (const m of report.missed) {
      const tr = document.createElement('tr');
      tr.style.borderBottom = '1px solid var(--border)';
      let start = 0;
      try { start = parseFloat(m.start_s || 0); } catch(e){}
      tr.innerHTML =
        `<td style="padding:3px 8px">ch${m.channel ?? '?'}</td>` +
        `<td style="padding:3px 8px">${m.frame_type ?? '?'}</td>` +
        `<td style="padding:3px 8px">${m.callsign ?? '?'}</td>` +
        `<td style="padding:3px 8px">@${start.toFixed(2)}s</td>`;
      tbody.appendChild(tr);
    }
  } else {
    missedBox.style.display = 'none';
  }
}

function stDownload(fmt) {
  let url = '/api/session/download?format=' + fmt;
  const apiKey = document.querySelector('meta[name="api-key"]')?.content;
  if (apiKey) url += '&api_key=' + encodeURIComponent(apiKey);
  window.location.href = url;
}

// ═══════════════════════════════════════════════════════════
// SWIMLANE — Konstanten & State
// ═══════════════════════════════════════════════════════════

const SL_COLORS = {
  WEATHER:      '#4A90D9',
  POSITION:     '#27AE60',
  EMERG_BEACON: '#E74C3C',
  EMERG_RSRC:   '#E67E22',
  STATION_TLM:  '#8E44AD',
  TEXT:         '#F39C12',
  CQ:           '#1ABC9C',
};
const SL_DEFAULT_COLOR = '#95A5A6';
const SL_BG_LANES      = ['#1A1A2E', '#16213E'];
const SL_BACKGROUND    = '#0D1117';
const SL_GRID_COLOR    = 'rgba(255,255,255,0.55)';
const SL_GRID_MINOR    = 'rgba(255,255,255,0.18)';
const SL_N_CHANNELS    = 8;
const SL_MAX_WINDOW_S  = 600;    // max. History in Sekunden
const SL_MAX_CANVAS_H  = 8000;   // 600s × 13px/s = 7800px
const SL_PPS_NORMAL    = 6;      // Standard-Zoom
const SL_PPS_DETAIL    = 10;     // Detail-Zoom

// Kanalfrequenzen (600 Hz + ch * 250 Hz, Kanalplan v0.5)
const SL_CH_FREQ = Array.from({length: SL_N_CHANNELS},
                               (_, i) => 600 + i * 250);

const sl = {
  inited:      false,       // Guard: slInit() nur einmal (RAF-Loop!)
  frames:      [],          // alle empfangenen Frames
  windowS:     600,            // fix, nicht mehr änderbar
  pxPerSec:    SL_PPS_NORMAL,  // Pixel pro Sekunde (Zoom-Dropdown)
  paused:      false,
  autoScroll:  true,        // Auto-Scroll nach oben bei neuem Frame
  animFrame:   null,
  txT0:        null,        // Server-Zeit des ersten Frames
  browserT0:   null,        // Browser-Wanduhr beim ersten Frame
  tLast:       null,        // Server-Zeit des letzten Frames
  laneW:          0,        // Breite einer Swimlane in Pixel (berechnet)
  canvasW:        0,
  canvasH:        0,
  scrollOffsetPx: 0,        // px nach unten gescrollt (0=oben=jetzt)
  totalContentH:  0,        // gesamte Inhaltshöhe in px (für Scrollbar)
  frozenFrames:   null,     // snapshot bei Pause (null = live)
  frozenNowS:     0,        // nowS zum Zeitpunkt von Pause
};

// nowS: laufende Zeit relativ zum ersten Frame.
// Basiert auf Browser-Wanduhr — läuft auch ohne
// neue Frames weiter (leere Zeitspannen sichtbar).
sl._nowS = function() {
  if (this.browserT0 === null) return 0;
  return Date.now() / 1000 - this.browserT0;
};

// ═══════════════════════════════════════════════════════════
// SWIMLANE — Monitor-Sub-Tabs (Liste / Swimlane)
// ═══════════════════════════════════════════════════════════

function monSwitchTab(which) {
  const isList = which === 'list';
  document.getElementById('mon-panel-list').style.display =
    isList ? '' : 'none';
  document.getElementById('mon-panel-swimlane').style.display =
    isList ? 'none' : '';
  document.getElementById('mon-btn-list').className =
    isList ? 'btn active' : 'btn secondary';
  document.getElementById('mon-btn-swimlane').className =
    isList ? 'btn secondary' : 'btn active';
  if (!isList) {
    slInit();  // Canvas initialisieren beim ersten Öffnen
    // Canvas fokussieren nachdem Panel sichtbar ist
    // (focus() schlägt fehl bei display:none)
    setTimeout(() => {
      const canvas = document.getElementById('sl-canvas');
      if (canvas) canvas.focus();
    }, 50);
  }
}

// ═══════════════════════════════════════════════════════════
// SWIMLANE — Initialisierung
// ═══════════════════════════════════════════════════════════

function slInit() {
  if (sl.inited) { slResize(); slDraw(); return; }   // idempotent
  sl.inited = true;
  slBuildLegend();
  slResize();
  window.addEventListener('resize', slResize);
  slLoop();

  // wheel auf Container (empfängt Event auch ohne Focus)
  const cont = document.getElementById('sl-container');
  if (cont) {
    cont.addEventListener('wheel', slOnWheel,
      { passive: false });
  }
  // keydown auf document (globaler Handler,
  // nur aktiv wenn Swimlane-Panel sichtbar)
  document.addEventListener('keydown', (e) => {
    const panel = document.getElementById(
      'mon-panel-swimlane');
    // offsetParent === null deckt auch versteckte Vorfahren ab
    // (z.B. Monitor-Tab inaktiv, Sub-Panel aber display:'')
    if (!panel || panel.style.display === 'none'
        || panel.offsetParent === null) return;
    // Eingabefelder nicht kapern (Pfeiltasten/Home/End dort lassen)
    const tag = (e.target && e.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
        || (e.target && e.target.isContentEditable)) return;
    slOnKey(e);
  });

  // Klick auf Frame → Detail-Modal
  const canvas2 = document.getElementById('sl-canvas');
  if (canvas2) {
    canvas2.addEventListener('click', slOnClick);
  }

  // Cursor-Stil: Zeiger wenn Maus über einem Frame
  if (canvas2) {
    canvas2.addEventListener('mousemove', (e) => {
      const HEADER_H_ = 36;
      if (e.offsetY < HEADER_H_) {
        canvas2.style.cursor = 'default';
        return;
      }
      const ch_    = Math.floor(e.offsetX / sl.laneW);
      const clickY_ = e.offsetY + sl.scrollOffsetPx;
      const clickS_ = (clickY_ - HEADER_H_) / sl.pxPerSec;
      const nowS_   = sl.paused ? sl.frozenNowS : sl._nowS();
      const frames_ = sl.paused
        ? (sl.frozenFrames || []) : sl.frames;
      const over = frames_.some(f => {
        if (f.ch !== ch_) return false;
        const ae = nowS_ - (f.startS + f.durS);
        return clickS_ >= ae && clickS_ <= ae + f.durS;
      });
      canvas2.style.cursor = over ? 'pointer' : 'default';
    });
  }
}

function slResize() {
  const container = document.getElementById('sl-container');
  const canvas    = document.getElementById('sl-canvas');
  if (!canvas) return;

  const containerW = container.clientWidth || 800;
  sl.canvasW = Math.max(containerW, SL_N_CHANNELS * 80);
  sl.laneW   = sl.canvasW / SL_N_CHANNELS;

  // FIXE Canvas-Höhe = 70% der Viewport-Höhe
  // (nicht dynamisch wachsend — Scroll ist intern)
  sl.canvasH = Math.min(
    Math.floor(window.innerHeight * 0.70),
    SL_MAX_CANVAS_H
  );
  sl.canvasH = Math.max(sl.canvasH, 300);

  canvas.width  = sl.canvasW;
  canvas.height = sl.canvasH;

  // Scroll-Offset begrenzen nach Resize
  slClampScroll();
}

function slClampScroll() {
  const HEADER_H   = 36;
  const nowS       = sl.paused ? sl.frozenNowS
                               : sl._nowS();
  const contentS   = Math.min(nowS, sl.windowS);
  const contentH   = HEADER_H + contentS * sl.pxPerSec;
  sl.totalContentH = Math.max(contentH, sl.canvasH);
  const maxScroll  = Math.max(0,
    sl.totalContentH - sl.canvasH);
  sl.scrollOffsetPx = Math.max(0,
    Math.min(sl.scrollOffsetPx, maxScroll));
}

function slOnWheel(e) {
  e.preventDefault();
  if (sl.paused) {
    // Bei Pause: nur scrollen, keine Auto-Scroll-Logik
    sl.scrollOffsetPx += e.deltaY * 0.8;
    slClampScroll();
    return;
  }
  // deltaY: positiv = nach unten scrollen (ältere Frames)
  sl.scrollOffsetPx += e.deltaY * 0.8;
  slClampScroll();
  // Nach unten scrollen → Auto-Scroll aus (live verlassen)
  if (e.deltaY > 0) sl.autoScroll = false;
  // Ganz nach oben scrollen → Auto-Scroll wieder ein
  if (sl.scrollOffsetPx === 0) sl.autoScroll = true;
}

function slOnKey(e) {
  const H     = sl.canvasH;
  const step  = sl.pxPerSec * 10;   // 10s pro Tastendruck
  const page  = H * 0.8;            // 80% Canvas-Höhe
  switch (e.key) {
    case 'ArrowDown':  case 'ArrowRight':
      sl.scrollOffsetPx += step;  break;
    case 'ArrowUp':    case 'ArrowLeft':
      sl.scrollOffsetPx -= step;  break;
    case 'PageDown':
      sl.scrollOffsetPx += page;  break;
    case 'PageUp':
      sl.scrollOffsetPx -= page;  break;
    case 'Home':
      sl.scrollOffsetPx = 0;        break;
    case 'End':
      sl.scrollOffsetPx = sl.totalContentH; break;
    default: return;
  }
  e.preventDefault();
  slClampScroll();
  // Auto-Scroll folgt der Scroll-Position:
  // Ganz oben → Auto-Scroll reaktivieren
  if (sl.scrollOffsetPx === 0) {
    sl.autoScroll = true;
  } else {
    // Irgendwo anders → Auto-Scroll aus
    sl.autoScroll = false;
  }
}

function slOnClick(e) {
  const HEADER_H = 36;
  // Klick im Header-Bereich → ignorieren
  if (e.offsetY < HEADER_H) return;

  // Kanal aus X-Position
  const ch = Math.floor(e.offsetX / sl.laneW);
  if (ch < 0 || ch >= SL_N_CHANNELS) return;

  // Zeit aus Y-Position + Scroll-Offset
  const clickY  = e.offsetY + sl.scrollOffsetPx;
  const clickS  = (clickY - HEADER_H) / sl.pxPerSec;

  // Aktueller nowS (eingefroren bei Pause)
  const nowS   = sl.paused ? sl.frozenNowS : sl._nowS();
  const frames = sl.paused
    ? (sl.frozenFrames || [])
    : sl.frames;

  // Frame suchen der an dieser Position liegt:
  // ageEnd = nowS - (startS + durS) → obere Kante
  // ageEnd + durS = nowS - startS   → untere Kante
  const hit = frames.find(f => {
    if (f.ch !== ch) return false;
    const ageEnd = nowS - (f.startS + f.durS);
    const ageTop = ageEnd;          // obere Kante (px ÷ pps)
    const ageBtm = ageEnd + f.durS; // untere Kante
    return clickS >= ageTop && clickS <= ageBtm;
  });

  if (hit && hit.raw) {
    openFrameModal(hit.raw);
  }
}

function slBuildLegend() {
  const el = document.getElementById('sl-legend');
  if (!el) return;
  el.innerHTML = Object.entries(SL_COLORS).map(([t, c]) =>
    `<span style="display:flex;align-items:center;gap:4px">
       <span style="width:12px;height:12px;border-radius:2px;
                    background:${c};display:inline-block"></span>
       <span style="color:var(--text2)">${t}</span>
     </span>`
  ).join('');
}

// ═══════════════════════════════════════════════════════════
// SWIMLANE — Frame empfangen (aus dem /ws/rx-Handler)
// ═══════════════════════════════════════════════════════════

function slAddFrame(data, isHistory = false) {
  // Zeitbezug: data.ts (Unix-Wanduhr) verwenden — es ist auf ALLEN
  // Frame-Quellen vorhanden (GUST wie MeshCore) und in EINER Zeitdomäne.
  // tx_start_s ist GUST-only und monotone Daemon-Zeit (anderer Nullpunkt);
  // mischt man es mit ts-basierten MeshCore-Frames in derselben txT0-Achse,
  // landen GUST-Frames weit außerhalb des Fensters und verschwinden.
  const rawT = (data.ts != null)
    ? data.ts
    : (data.tx_start_s || Date.now() / 1000);

  // Beim normalen Live-Empfang: Nullpunkt setzen
  // Beim History-Load: txT0/browserT0 wird von
  // slLoadHistory() gesetzt — hier überspringen
  if (!isHistory && sl.txT0 === null) {
    sl.txT0      = rawT;
    sl.browserT0 = Date.now() / 1000;
  }
  if (!isHistory) sl.tLast = rawT;

  // startS relativ zu txT0 (kann null sein beim History-Load)
  const startS = sl.txT0 !== null ? rawT - sl.txT0 : rawT;

  const ch       = (data.channel != null)
    ? parseInt(data.channel)
    : (data.detected_channel != null
        ? parseInt(data.detected_channel) : 0);
  const ftype    = data.type_name || '?';
  const callsign = data.from || '?';

  // Frame-Dauer: aus data.duration_s oder Schätzung ~5s (typische
  // GUST-Framedauer; duration_s ist im rx_frame-Event nicht enthalten)
  const durS = data.duration_s || 5.0;

  sl.frames.push({ startS, durS, ch, ftype, callsign,
                   raw: data });

  // History-Load: kein Clamp, kein Scroll, kein Zähler-Update
  // (slLoadHistory() macht das am Ende einmalig)
  if (isHistory) return;

  // History auf SL_MAX_WINDOW_S begrenzen
  {
    const nowS   = sl._nowS();
    const cutoff = nowS - SL_MAX_WINDOW_S;
    if (cutoff > 0)
      sl.frames = sl.frames.filter(f => f.startS > cutoff);
  }

  // Frame-Zähler aktualisieren
  const counter = document.getElementById('sl-counter');
  if (counter) counter.textContent = sl.frames.length + ' Frames';

  // Nur scrollen wenn nicht pausiert UND Auto-Scroll aktiv
  // Bei Pause: Frame wird gespeichert aber View bleibt stehen
  if (sl.autoScroll && !sl.paused) {
    sl.scrollOffsetPx = 0;
  }
}

function slLoadHistory(frames) {
  // Lädt ein Array von History-Frames in die Swimlane.
  // Kalibriert txT0/browserT0 so dass:
  //   - txT0 = tx_start_s des ältesten Frames
  //   - nowS zum Ladezeitpunkt = Alter des neuesten Frames
  //     relativ zu txT0 (Zeitachse läuft ab "letztem Frame")

  if (!frames || frames.length === 0) return;

  // Zeitstempel aller Frames sammeln — data.ts (Unix) konsistent mit
  // slAddFrame; tx_start_s (monotone Zeit) wuerde die Achse verzerren.
  const times = frames.map(f =>
    f.ts != null ? f.ts
                 : (f.tx_start_s || Date.now() / 1000)
  );
  const tMin = Math.min(...times);  // ältester Frame
  const tMax = Math.max(...times);  // neuester Frame

  // Zeitachse kalibrieren:
  // txT0 = ältester Frame → startS aller Frames >= 0
  // browserT0 = jetzt - Alter des neuesten Frames
  //   → _nowS() startet beim neuesten Frame und läuft weiter
  sl.txT0      = tMin;
  sl.browserT0 = Date.now() / 1000 - (tMax - tMin);
  sl.tLast     = tMax;

  // Frames einfügen (isHistory=true → kein Clamp/Scroll)
  for (const f of frames) {
    slAddFrame(f, true);
  }

  // Einmalig: Clamp + Zähler aktualisieren
  {
    const nowS   = sl._nowS();
    const cutoff = nowS - SL_MAX_WINDOW_S;
    if (cutoff > 0)
      sl.frames = sl.frames.filter(f => f.startS > cutoff);
  }
  const counter = document.getElementById('sl-counter');
  if (counter) counter.textContent =
    sl.frames.length + ' Frames';

  // Auto-Scroll bleibt aktiv (oben = aktuell) — die ältesten
  // Frames liegen nach dem ersten Render weiter unten.
}

// ═══════════════════════════════════════════════════════════
// SWIMLANE — Zeichnen
// ═══════════════════════════════════════════════════════════

function slRoundRectPath(ctx, x, y, w, h, r) {
  // roundRect mit Fallback für ältere Browser
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(x, y, w, h, r);
  else               ctx.rect(x, y, w, h);
}

function slDraw() {
  const canvas = document.getElementById('sl-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  const W   = sl.canvasW;
  const H   = sl.canvasH;
  const lW  = sl.laneW;
  const pps = sl.pxPerSec;

  const HEADER_H = 36;        // px — reservierte Header-Zone oben
  const CH = H - HEADER_H;    // nutzbare Zeichenfläche unter dem Header
  const visS = CH / pps;      // sichtbare Sekunden unterhalb des Headers
                              // (Zeitfenster endet am unteren Rand = "jetzt")

  // Theme erkennen — Swimlane-Farben sind theme-sensitiv (Fix 4)
  const isLight = document.documentElement
    .getAttribute('data-theme') === 'light';

  // nowS läuft mit Browser-Wanduhr — unabhängig von Frames.
  // Leere Zeitspannen (keine Frames) sind als leere Fläche
  // sichtbar; die Zeitachse friert nicht ein.
  // Bei Pause: eingefrorener Zeitpunkt und Frames (Snapshot)
  const nowS   = sl.paused ? sl.frozenNowS : sl._nowS();
  const frames = sl.paused ? (sl.frozenFrames || []) : sl.frames;

  // Hintergrund
  ctx.fillStyle = isLight ? '#f0f4f8' : SL_BACKGROUND;
  ctx.fillRect(0, 0, W, H);

  // Gesamt-Inhaltshöhe berechnen (für Scrollbar)
  {
    const contentS_  = Math.min(nowS, sl.windowS);
    sl.totalContentH = Math.max(
      HEADER_H + contentS_ * pps, H);
  }
  const off = sl.scrollOffsetPx;   // aktueller Scroll-Offset in px

  // Translation für Scroll-Offset anwenden
  // Alles außer dem Kanal-Header scrollt mit
  ctx.save();
  ctx.translate(0, -off);

  // ── Swimlane-Hintergründe ────────────────────────────────
  // (statische Möblierung: Offset kompensiert, deckt so trotz
  // Translation immer den ganzen sichtbaren Canvas ab)
  const laneBg = isLight
    ? ['#dde8f5', '#e8f0f8']   // helle Blautöne
    : SL_BG_LANES;
  for (let ch = 0; ch < SL_N_CHANNELS; ch++) {
    ctx.fillStyle = laneBg[ch % 2];
    ctx.fillRect(ch * lW, off, lW, H);
  }

  // ── Zeitraster: age=0 direkt unter Header (=jetzt), ──────
  // age wächst nach unten (=Vergangenheit); Bereich um den
  // Scroll-Offset erweitert (sichtbarer Inhalt = off .. off+visS)
  for (let age = 0; age <= off / pps + visS + 5; age += 5) {
    const yPos = HEADER_H + age * pps;
    if (yPos < HEADER_H + off || yPos > H + off) continue;
    const is10 = (age % 10 === 0);
    ctx.strokeStyle = is10
      ? (isLight ? 'rgba(0,0,0,0.45)' : SL_GRID_COLOR)
      : (isLight ? 'rgba(0,0,0,0.18)' : SL_GRID_MINOR);
    ctx.lineWidth   = is10 ? 1.0 : 0.4;
    ctx.beginPath();
    ctx.moveTo(0, yPos);
    ctx.lineTo(W, yPos);
    ctx.stroke();

    // Label: Alter in Sekunden (age=0 -> "jetzt")
    if (is10) {
      ctx.fillStyle = isLight
        ? 'rgba(0,0,0,0.80)'
        : 'rgba(255,255,255,0.92)';
      ctx.font      = 'bold 11px monospace';
      const label   = age === 0 ? 'jetzt' : `-${age}s`;
      ctx.fillText(label, 3, yPos + 12);
    }
  }

  // ── Swimlane-Trennlinien (vertikal) ──────────────────────
  for (let ch = 0; ch <= SL_N_CHANNELS; ch++) {
    const isEdge = (ch === 0 || ch === SL_N_CHANNELS);
    ctx.strokeStyle = isLight
      ? (isEdge ? 'rgba(0,0,0,0.6)'      : 'rgba(0,0,0,0.3)')
      : (isEdge ? 'rgba(255,255,255,0.85)' : 'rgba(255,255,255,0.55)');
    ctx.lineWidth = isEdge ? 2.0 : 1.2;
    ctx.beginPath();
    ctx.moveTo(ch * lW, HEADER_H + off);
    ctx.lineTo(ch * lW, H + off);
    ctx.stroke();
  }

  // ── Frames zeichnen ──────────────────────────────────────
  const pad = 3;  // px Abstand Block zu Lane-Rand

  // Clipping auf die Zone unter dem Header: teilweise heraus-
  // gescrollte Frames malen sonst in die Header-Zone und schimmern
  // durch den halbtransparenten Header-Balken.
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, HEADER_H + off, W, CH);   // Offset kompensiert → Bildschirm-Zone
  ctx.clip();

  for (const f of frames) {
    // Alter des Frame-ENDES relativ zu nowS
    // (jüngste Frames haben kleines age → nahe am Header)
    const frameEndS = f.startS + f.durS;
    const ageEnd    = nowS - frameEndS;
    const yTop      = HEADER_H + ageEnd * pps;
    const yH   = Math.max(f.durS * pps, 18);  // Mindesthöhe 18px

    // Außerhalb des sichtbaren (gescrollten) Bereichs überspringen
    if (yTop + yH < HEADER_H + off || yTop > H + off) continue;
    if (f.ch < 0 || f.ch >= SL_N_CHANNELS) continue;

    const xLeft = f.ch * lW + pad;
    const bW    = lW - 2 * pad;
    const color = SL_COLORS[f.ftype] || SL_DEFAULT_COLOR;

    // Block mit abgerundeten Ecken
    const r = 4;
    ctx.fillStyle   = color;
    ctx.globalAlpha = 0.90;
    slRoundRectPath(ctx, xLeft, yTop, bW, yH, r);
    ctx.fill();
    ctx.globalAlpha = 1.0;

    // Rahmen
    ctx.strokeStyle = isLight
      ? 'rgba(0,0,0,0.35)'
      : 'rgba(255,255,255,0.6)';
    ctx.lineWidth   = 0.7;
    slRoundRectPath(ctx, xLeft, yTop, bW, yH, r);
    ctx.stroke();

    // Text nur wenn Block groß genug
    if (yH >= 20) {
      ctx.textAlign    = 'center';
      ctx.fillStyle    = 'white';
      const cx = xLeft + bW / 2;
      const cy = yTop  + yH / 2;

      if (yH >= 32) {
        // Rufzeichen + Typ
        ctx.font = 'bold 9px sans-serif';
        ctx.fillText(f.callsign, cx, cy - 4);
        ctx.font      = '7px sans-serif';
        ctx.fillStyle = 'rgba(255,255,255,0.80)';
        const short = f.ftype.length > 10
          ? f.ftype.slice(0, 10) : f.ftype;
        ctx.fillText(short, cx, cy + 7);
      } else {
        // Nur Rufzeichen
        ctx.font = 'bold 8px sans-serif';
        ctx.fillText(f.callsign, cx, cy + 3);
      }
    }

    // 🔑 bei authentifizierten Frames (nur 🔑, KEIN 🔍 in der Swimlane).
    // yTop enthält bereits HEADER_H → Badge oben rechts im Block.
    // save/restore, damit font/textAlign/fillStyle den nächsten Frame nicht stören.
    if (f.authenticated && yH >= 14) {
      ctx.save();
      const keySize = Math.max(8, Math.min(lW * 0.22, 14));
      ctx.font      = `${keySize}px sans-serif`;
      ctx.textAlign = 'right';
      ctx.fillStyle = 'white';
      ctx.fillText('🔑', xLeft + bW - 1, yTop + keySize + 1);
      ctx.restore();
    }
  }

  ctx.restore();   // Clipping aufheben (Jetzt-Linie + Header voll sichtbar)

  // ── "Jetzt"-Linie direkt unter dem Header (= aktuellster Zeitpunkt)
  // Rot funktioniert in beiden Themes. Scrollt mit dem Inhalt —
  // nur zeichnen solange nicht weggescrollt (sonst hinter Header).
  if (off <= 2) {
    ctx.strokeStyle = 'rgba(255,80,80,0.8)';
    ctx.lineWidth   = 1.5;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(0, HEADER_H + 2);
    ctx.lineTo(W, HEADER_H + 2);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  ctx.restore();   // Scroll-Translation aufheben

  // ── Kanal-Header (ZULETZT: immer oben sichtbar, über Frames) ──
  for (let ch = 0; ch < SL_N_CHANNELS; ch++) {
    const x = ch * lW;
    // Hintergrund-Balken (halbtransparent, überdeckt Frame-Oberkanten)
    ctx.fillStyle = isLight
      ? 'rgba(240,244,248,0.90)'
      : 'rgba(13,17,23,0.82)';
    ctx.fillRect(x + 1, 0, lW - 2, HEADER_H);
    // Text
    const cx = x + lW / 2;
    ctx.fillStyle = isLight ? '#1f2328' : '#ffffff';
    ctx.font      = 'bold 12px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(`CH ${ch}`, cx, 15);
    ctx.font      = '9px sans-serif';
    ctx.fillStyle = isLight
      ? 'rgba(31,35,40,0.65)'
      : 'rgba(255,255,255,0.70)';
    ctx.fillText(`${SL_CH_FREQ[ch]} Hz`, cx, 27);
  }

  ctx.textAlign = 'left';  // Reset

  // ── Scrollbar rechts ──────────────────────────────
  const SB_W = 6;   // Scrollbar-Breite in px
  const SB_X = W - SB_W - 1;
  const visRatio  = H / sl.totalContentH;
  const sbH       = Math.max(visRatio * H, 20);
  const sbY       = (sl.scrollOffsetPx / sl.totalContentH) * H;
  ctx.fillStyle   = 'rgba(255,255,255,0.15)';
  ctx.fillRect(SB_X, 0, SB_W, H);
  ctx.fillStyle   = 'rgba(255,255,255,0.55)';
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(SB_X, sbY, SB_W, sbH, 3);
  else               ctx.rect(SB_X, sbY, SB_W, sbH);
  ctx.fill();
}

// ═══════════════════════════════════════════════════════════
// SWIMLANE — Render-Loop & Steuerung
// ═══════════════════════════════════════════════════════════

function slLoop() {
  // Auch bei Pause zeichnen: slDraw() rendert dann aus dem
  // unveränderlichen frozen*-Snapshot — erlaubt flüssiges
  // Scrollen durch das eingefrorene Bild ohne Resume.
  slClampScroll();
  slDraw();
  sl.animFrame = requestAnimationFrame(slLoop);
}

// Wird nicht mehr vom UI aufgerufen (Zeitfenster ist fix 600s) —
// bleibt für internen Gebrauch erhalten.
function slSetWindow(val) {
  sl.windowS = parseInt(val);
  slResize();
  // Zeitfenster-Wechsel: zurück nach oben
  sl.scrollOffsetPx = 0;
}

function slSetZoom(val) {
  sl.pxPerSec       = parseInt(val);
  sl.scrollOffsetPx = 0;   // nach Zoom-Wechsel nach oben
  slClampScroll();
  // kein slResize() nötig — Canvas-Höhe ist fix
}

function slTogglePause() {
  sl.paused = !sl.paused;
  const btn = document.getElementById('sl-pause-btn');
  if (btn) btn.textContent = sl.paused ? '▶ Resume' : '⏸ Pause';

  if (sl.paused) {
    // Snapshot: aktuellen Zustand einfrieren
    sl.frozenFrames = sl.frames.slice();
    sl.frozenNowS   = sl._nowS();
  } else {
    // Resume: Snapshot verwerfen, zurück zu live
    sl.frozenFrames = null;
    sl.frozenNowS   = 0;
    // nach oben springen wenn Auto-Scroll aktiv
    if (sl.autoScroll) sl.scrollOffsetPx = 0;
  }
}

function slExport() {
  const canvas = document.getElementById('sl-canvas');
  if (!canvas) return;
  const a = document.createElement('a');
  a.download = 'gust_swimlane_' +
    new Date().toISOString().slice(0,19).replace(/[T:]/g,'-') + '.png';
  a.href = canvas.toDataURL('image/png');
  a.click();
}

// ═══════════════════════════ INIT ═════════════════════════════
(async function init() {
  // Sprache laden (vor allem anderen, damit UI sofort lokalisiert ist)
  await loadLang(state.currentLang);
  // Sprachschalter auf gespeicherten Wert setzen
  const cfgLang = document.getElementById('cfg-lang');
  if (cfgLang) cfgLang.value = state.currentLang;
  try {
    const s = await apiFetch('/api/status');
    state.callsign   = s.callsign || '–';
    state.homeChannel = s.home_channel ?? null;
    document.getElementById('callsign-badge').textContent = state.callsign;
    buildChannelGrid(state.homeChannel);
    if (s.tx_available != null) applyTxAvailability(s.tx_available);
    // Frame-History nachladen
    const hist = await apiFetch('/api/log');
    if (Array.isArray(hist.frames)) {
      hist.frames.forEach(appendRxFrame);
      slLoadHistory(hist.frames);
    }
  } catch(e) {
    buildChannelGrid(null);
    log2ui('WARNING', t('api.error') + ' ' + e.message);
  }
  connectWsRx();
  startHeartbeatWatchdog();
  connectWsLog();
  fetchTxQueue();
  setInterval(loadStatus, 30000);
})();
</script>
<!-- ══ FRAME DETAIL MODAL ══ -->
<div id="frame-modal" onclick="if(event.target===this)closeModal()">
  <div id="frame-modal-box">
    <h3 id="modal-title">Frame Details</h3>
    <button id="frame-modal-close" onclick="closeModal()">✕</button>
    <div id="frame-modal-body">
      <div id="modal-content"></div>
    </div>
  </div>
</div>

<!-- ══ INBOX DETAIL MODAL ══ -->
<div id="inbox-modal" class="modal-overlay" onclick="if(event.target===this)closeInboxModal()">
  <div class="modal-box">
    <button class="modal-close" onclick="closeInboxModal()">✕</button>
    <div id="inbox-modal-content"></div>
  </div>
</div>

</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════════════
# WEB-SERVER KLASSE
# ═══════════════════════════════════════════════════════════════════════

def _fmt_audio_device(cfg: dict) -> str:
    """Audiogerät als 'ID — Name' formatieren; fällt bei Fehler auf die rohe ID zurück."""
    dev = cfg.get('audio', {}).get('device', None)
    if dev is None:
        return '–'
    try:
        import sounddevice as sd
        info = sd.query_devices(int(dev))
        return f"{int(dev)} — {info['name']}"
    except Exception:
        return str(dev)


class WebServer:
    """
    Eingebetteter aiohttp Web-Server für GUST.

    Parameter:
        config      — Gesamte Konfiguration (dict), Abschnitt "web" wird ausgewertet
        event_bus   — Optional: gust_eventbus.EventBus Instanz (Duck-Typing)
        gateway     — Optional: gust_gateway.Gateway Instanz (Duck-Typing)

    Konfigurationsbeispiel (gateway.json):
        "web": {
            "host":    "0.0.0.0",
            "port":    8080,
            "api_key": ""          // leer = kein Auth
        }
    """

    def __init__(self, config: dict,
                 event_bus=None,
                 gateway=None,
                 config_path: Optional[str] = None):
        web_cfg = config.get("web", {})
        self._host     = web_cfg.get("host", "0.0.0.0")
        self._port     = int(web_cfg.get("port", 8080))
        self._api_key  = web_cfg.get("api_key", "")
        self._callsign = config.get("callsign", "OE3GAS")
        self._config   = config

        # Pfad zur Konfigurationsdatei — None = Schreiben deaktiviert
        # (z.B. Standalone-Test). Bei daemon/rx wird er aus gust.py übergeben.
        self._config_path = config_path
        self._locales_dir = Path(__file__).parent / 'locales'
        # Serialisiert konkurrierende Schreibvorgänge auf gateway.json
        self._config_write_lock = asyncio.Lock()

        self._event_bus = event_bus
        self._gateway   = gateway

        # rigctld-Prozess-Handle (Popen) — nur gesetzt, wenn GUST rigctld
        # selbst gestartet hat. Nur dann darf /api/hamlib/stop ihn beenden;
        # extern gestartete Instanzen werden nicht angetastet.
        self._rigctld_proc = None
        self._tune_task = None   # asyncio.Task — aktiv während Tune läuft

        self._start_time: Optional[float] = None

        # RX-Frame-History (letzte 50 Frames für /api/log)
        self._rx_history: deque = deque(maxlen=350)

        # Zeitstempel des zuletzt empfangenen Frames (für /api/status → "Letzter RX").
        # Wird hier getrackt, weil das TX-Gateway keine RX-Frames sieht.
        self._last_rx_ts: Optional[float] = None

        # ── Session-Recorder (Stresstest-Auswertung) ─────────────────
        # Zustand: "idle" | "recording" | "done"
        self._session_state: str = "idle"
        self._session_frames: list = []         # akkumulierte rx_frame-Dicts
        self._session_start_ts: Optional[float] = None
        self._session_stop_ts: Optional[float] = None
        self._session_report: Optional[dict] = None   # letzter Auswertungs-Report

        # Aktive WebSocket-Verbindungen
        self._ws_rx_clients: Set[web.WebSocketResponse] = set()
        self._ws_log_clients: Set[web.WebSocketResponse] = set()

        # Interner Log-Ring für /ws/log (asyncio.Queue pro Verbindung)
        self._log_queues: Set[asyncio.Queue] = set()

        # asyncio Tasks
        self._eb_task: Optional[asyncio.Task] = None

        self._runner: Optional[web.AppRunner] = None
        self._site:   Optional[web.TCPSite]   = None

    # ── LIFECYCLE ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Server starten. Kann direkt in einem asyncio Event-Loop aufgerufen werden."""
        self._start_time = time.time()
        app = self._make_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()
        log.vital("GUST Web-Server gestartet: http://%s:%d", self._host, self._port)

        # Event-Bus Subscriber starten (falls vorhanden)
        if self._event_bus is not None:
            self._eb_task = asyncio.create_task(
                self._event_bus_reader(), name="web_eb_reader"
            )

        # Heartbeat-Task starten — sendet alle 10 s einen "heartbeat"-Event
        # an alle /ws/rx-Clients, damit das UI Daemon-Aussetzer erkennt.
        asyncio.create_task(self._heartbeat_loop(), name="hb_loop")

    async def stop(self) -> None:
        """Server geordnet beenden."""
        if self._eb_task:
            self._eb_task.cancel()
            try:
                await self._eb_task
            except asyncio.CancelledError:
                pass

        # Alle offenen WebSockets schließen
        for ws in list(self._ws_rx_clients | self._ws_log_clients):
            await ws.close()

        if self._runner:
            await self._runner.cleanup()
        log.info("GUST Web-Server gestoppt.")

    # ── APP SETUP ─────────────────────────────────────────────────────

    def _make_app(self) -> web.Application:
        """aiohttp Application mit Routen und Middleware aufbauen."""
        middlewares = []
        if self._api_key:
            middlewares.append(self._auth_middleware)

        app = web.Application(middlewares=middlewares)
        app.router.add_get("/",              self._handle_index)
        app.router.add_get("/api/health",    self._handle_health)
        app.router.add_get("/api/status",    self._handle_status)
        app.router.add_get("/api/lang/{code}", self._handle_lang)
        app.router.add_get("/api/config",    self._handle_config)
        app.router.add_patch("/api/config",  self._handle_config_patch)
        app.router.add_post("/api/config",   self._handle_config_post)
        app.router.add_get("/api/log",       self._handle_log)
        app.router.add_post("/api/tx/weather",   self._handle_tx_weather)
        app.router.add_post("/api/tx/position",  self._handle_tx_position)
        app.router.add_post("/api/tx/text",      self._handle_tx_text)
        app.router.add_post("/api/tx/text_fragment", self._handle_tx_text_fragment)
        app.router.add_post("/api/tx/emergency", self._handle_tx_emergency)
        app.router.add_get   ("/api/tx/queue",       self._handle_tx_queue)
        app.router.add_delete("/api/tx/queue",       self._handle_tx_queue_clear)
        app.router.add_post  ("/api/trx/activate",    self._handle_trx_activate)
        app.router.add_post  ("/api/trx/save",        self._handle_trx_save)
        app.router.add_delete("/api/trx/profile",     self._handle_trx_delete)
        app.router.add_get ("/api/audio/devices", self._handle_audio_devices)
        app.router.add_get ("/api/audio/config",  self._handle_audio_config_get)
        app.router.add_post("/api/audio/config",  self._handle_audio_config_post)
        app.router.add_get ("/api/sdr/devices",      self._handle_sdr_devices)
        app.router.add_get ("/api/sdr/caps",         self._handle_sdr_caps)
        app.router.add_get ("/api/sdr/config",       self._handle_sdr_config_get)
        app.router.add_post("/api/sdr/config",       self._handle_sdr_config_post)
        app.router.add_get   ("/api/sdr/scan",              self._handle_sdr_scan)
        app.router.add_post  ("/api/sdr/profile/save",      self._handle_sdr_profile_save)
        app.router.add_delete("/api/sdr/profile",           self._handle_sdr_profile_delete)
        app.router.add_post  ("/api/sdr/profile/activate/rx", self._handle_sdr_activate_rx)
        app.router.add_post  ("/api/sdr/profile/activate/tx", self._handle_sdr_activate_tx)
        app.router.add_get ("/api/hamlib/ports",     self._handle_hamlib_ports)
        app.router.add_get ("/api/hamlib/models",    self._handle_hamlib_models)
        app.router.add_get ("/api/hamlib/status",    self._handle_hamlib_status)
        app.router.add_post("/api/hamlib/start",     self._handle_hamlib_start)
        app.router.add_post("/api/hamlib/stop",      self._handle_hamlib_stop)
        app.router.add_post("/api/hamlib/config",    self._handle_hamlib_config)
        app.router.add_post("/api/hamlib/force_restart", self._handle_hamlib_force_restart)
        app.router.add_post("/api/tx/tune",          self._handle_tx_tune)
        app.router.add_post("/api/tx/tune_stop",     self._handle_tx_tune_stop)
        app.router.add_post("/api/session/start",    self._handle_session_start)
        app.router.add_post("/api/session/stop",     self._handle_session_stop)
        app.router.add_get ("/api/session/status",   self._handle_session_status)
        app.router.add_post("/api/session/evaluate", self._handle_session_evaluate)
        app.router.add_get ("/api/session/download", self._handle_session_download)
        app.router.add_get("/ws/rx",  self._handle_ws_rx)
        app.router.add_get("/ws/log", self._handle_ws_log)
        return app

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        """Bearer-Token / X-API-Key Prüfung für API- und WS-Endpunkte."""
        path = request.path
        is_protected = (
            (path.startswith("/api/") or path.startswith("/ws/"))
            and path != "/api/health"   # Health-Endpoint ist öffentlich
        )
        if is_protected:
            key = (
                request.headers.get("X-API-Key")
                or request.headers.get("Authorization", "").removeprefix("Bearer ")
                or request.rel_url.query.get("api_key", "")
            )
            if key != self._api_key:
                raise web.HTTPUnauthorized(
                    text='{"error":"Unauthorized — API-Key fehlt oder ungültig"}',
                    content_type="application/json"
                )
        return await handler(request)

    # ── REST HANDLER ──────────────────────────────────────────────────

    async def _handle_index(self, _request: web.Request) -> web.Response:
        return web.Response(text=_HTML_UI, content_type="text/html", charset="utf-8")

    async def _handle_status(self, _request: web.Request) -> web.Response:
        uptime = time.time() - self._start_time if self._start_time else 0
        home_ch     = _callsign_to_channel(self._callsign)
        tx_interval = self._config.get("gateway", {}).get("interval_s", 300)
        h           = int(hashlib.sha256(
                          self._callsign.upper().encode()).hexdigest(), 16)
        tx_offset   = (h >> 8) % tx_interval

        status = {
            "callsign":          self._callsign,
            "home_channel":      home_ch,
            "uptime_s":          int(uptime),
            "queue_depth":       0,
            "last_tx":           None,
            "last_rx":           None,
            "audio_device":      _fmt_audio_device(self._config),
            "ptt_backend":       (self._config.get("tx_audio") or
                                  self._config.get("audio") or {}).get("ptt_backend", "null"),
            "ptt_delay_ms":      (self._config.get("tx_audio") or
                                  self._config.get("audio") or {}).get("ptt_delay_ms", 250),
            "tx_interval_s":     tx_interval,
            "tx_time_offset_s":  tx_offset,
            "active_trx_profile": self._config.get("active_trx_profile", ""),
            "trx_profile_count":  len(self._config.get("trx_profiles", [])),
            "version":           "0.1.0",
        }
        # Gateway-Status einmischen wenn verfügbar
        if self._gateway is not None:
            try:
                gw_status = self._gateway.get_status()
                status.update(gw_status)
            except Exception as exc:
                log.debug("Gateway.get_status() Fehler: %s", exc)
        # RX-Zeitstempel wird im Web-Server getrackt (Gateway ist TX-only) —
        # nach dem Merge setzen, damit er nicht von None überschrieben wird.
        if self._last_rx_ts is not None:
            status["last_rx"] = self._last_rx_ts
        # TX nur möglich, wenn ein Gateway verdrahtet ist (daemon-Modus).
        # Im Monitor-Modus (rx / Standalone) ist es None → GUI deaktiviert Senden.
        status["tx_available"] = self._gateway is not None
        return web.json_response(status)

    async def _handle_lang(self, request: web.Request) -> web.Response:
        """Liefert die Sprachdatei locales/<code>.json aus."""
        code = request.match_info.get('code', 'de')
        # Nur erlaubte Codes durchlassen (Sicherheit: kein Path-Traversal)
        if not code.isalpha() or len(code) > 5:
            raise web.HTTPBadRequest(text='Invalid language code')
        lang_file = self._locales_dir / f'{code}.json'
        if not lang_file.exists():
            # Fallback auf Deutsch
            lang_file = self._locales_dir / 'de.json'
        try:
            text = lang_file.read_text(encoding='utf-8')
            return web.Response(
                text=text,
                content_type='application/json',
                charset='utf-8',
            )
        except Exception as exc:
            log.error('Lang-Datei lesen fehlgeschlagen: %s', exc)
            raise web.HTTPInternalServerError(text='Lang file error')

    async def _handle_config(self, _request: web.Request) -> web.Response:
        """Aktuelle Konfiguration zurückgeben — API-Key wird ausgeblendet."""
        safe = {k: v for k, v in self._config.items()
                if k not in ("web",)}
        # web-Abschnitt ohne api_key
        web_safe = {k: v for k, v in self._config.get("web", {}).items()
                    if k != "api_key"}
        safe["web"] = web_safe
        return web.json_response(safe)

    async def _handle_config_patch(self, request: web.Request) -> web.Response:
        """
        Partielles Konfig-Update via PATCH /api/config.
        Akzeptiert: {"audio": {"ptt_delay_ms": 250}}
        Schreibt geänderte Werte in self._config und in gateway.json (wenn vorhanden).
        """
        try:
            patch = await request.json()
        except Exception:
            raise web.HTTPBadRequest(
                text='{"error":"Ungültiger JSON-Body"}',
                content_type="application/json"
            )

        import pathlib, json as _json

        # In-Memory-Konfig aktualisieren (nur bekannte Schlüssel)
        for section, values in patch.items():
            if isinstance(values, dict) and section in self._config:
                self._config.setdefault(section, {}).update(values)
            elif section not in ("web",):
                self._config[section] = values

        # gateway.json schreiben falls sie existiert
        cfg_path = pathlib.Path("gateway.json")
        if cfg_path.exists():
            try:
                with open(cfg_path, encoding="utf-8") as f:
                    file_cfg = _json.load(f)
                for section, values in patch.items():
                    if isinstance(values, dict):
                        file_cfg.setdefault(section, {}).update(values)
                    else:
                        file_cfg[section] = values
                with open(cfg_path, "w", encoding="utf-8") as f:
                    _json.dump(file_cfg, f, indent=4, ensure_ascii=False)
                log.info("gateway.json aktualisiert: %s", patch)
            except Exception as exc:
                log.error("gateway.json Schreib-Fehler: %s", exc)
                raise web.HTTPInternalServerError(
                    text=f'{{"error":"{exc}"}}',
                    content_type="application/json"
                )

        return web.json_response({"ok": True, "updated": patch})

    async def _handle_config_post(self, request: web.Request) -> web.Response:
        """
        Konfig-Update via POST /api/config (Web-UI "Status & Config"-Tab).

        Akzeptiert u.a.:
            {"audio": {"input": <int|null>, "output": <int|null>},
             "ptt":   {"backend": "null"|"gpio"|"hamlib"}}

        Die Werte werden auf die kanonischen Konfig-Schlüssel abgebildet
        (audio.device = output, rx.device = input, audio.ptt_backend = backend)
        und in self._config geschrieben. Ist ein config_path bekannt, wird die
        Auswahl zusätzlich best-effort in gateway.json persistiert.
        """
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='{"error":"Ungültiger JSON-Body"}',
                                     content_type="application/json")

        audio = body.get("audio") or {}
        ptt   = body.get("ptt")   or {}

        def _coerce(val):
            if val is None or val == "":
                return None
            try:
                return int(val)
            except (TypeError, ValueError):
                return str(val)

        # Schreiben in die neuen Keys; Seed aus den alten ("audio"/"rx") —
        # bei Alt-Configs zeigt tx_audio/rx_audio dann auf DASSELBE Dict
        if "output" in audio:
            self._config.setdefault(
                "tx_audio", self._config.get("audio") or {}
            )["device"] = _coerce(audio["output"])
        if "input" in audio:
            self._config.setdefault(
                "rx_audio", self._config.get("rx") or {}
            )["device"] = _coerce(audio["input"])
        if "backend" in ptt:
            self._config.setdefault(
                "tx_audio", self._config.get("audio") or {}
            )["ptt_backend"] = str(ptt["backend"])

        # Deep-Decoding-Toggle (rx_audio.deep_decode) — wirkt erst nach Neustart
        rx_body = body.get("rx") or {}
        if "deep_decode" in rx_body:
            self._config.setdefault(
                "rx_audio", self._config.get("rx") or {}
            )["deep_decode"] = bool(rx_body["deep_decode"])

        # Best-effort-Persistenz — für den MVP nicht kritisch, daher kein Fehler
        # nach außen, wenn das Schreiben scheitert.
        if self._config_path is not None:
            try:
                async with self._config_write_lock:
                    await asyncio.get_running_loop().run_in_executor(
                        None, self._save_config_atomic)
            except Exception as exc:
                log.warning("POST /api/config: Persistenz fehlgeschlagen: %s", exc)

        self._publish_log("INFO", "Konfiguration via Web aktualisiert")
        return web.json_response({"ok": True, "message": "Konfiguration gespeichert"})

    async def _handle_log(self, _request: web.Request) -> web.Response:
        """Letzte 50 RX-Frames als JSON-Array."""
        return web.json_response({"frames": list(self._rx_history)})

    async def _handle_tx_weather(self, request: web.Request) -> web.Response:
        return await self._enqueue_tx(request, "weather", priority=4)

    async def _handle_tx_position(self, request: web.Request) -> web.Response:
        return await self._enqueue_tx(request, "position", priority=3)

    async def _handle_tx_text(self, request: web.Request) -> web.Response:
        return await self._enqueue_tx(request, "text", priority=2)

    async def _handle_tx_text_fragment(self, request: web.Request) -> web.Response:
        """
        Nimmt EIN vorberechnetes Text-Fragment entgegen (vom Web-UI Schedule-getaktet).
        Body: {to, text_chunk, seq_nr, frag_index, frag_total}
        Wird als einzelnes 0x40-Frame eingereiht (Prio 2) — keine erneute Fragmentierung.
        """
        try:
            data = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='{"error":"Ungültiger JSON-Body"}',
                                     content_type="application/json")

        required = {"to", "text_chunk", "seq_nr", "frag_index", "frag_total"}
        missing  = required - set(data.keys())
        if missing:
            raise web.HTTPBadRequest(
                text=f'{{"error":"Fehlende Felder: {sorted(missing)}"}}',
                content_type="application/json")

        if self._gateway is None:
            raise web.HTTPServiceUnavailable(
                text='{"error":"Kein TX-Gateway aktiv — diese Station ist '
                     'im Empfangs-/Monitor-Modus."}',
                content_type="application/json")

        frame_dict = {"frame_type": "text_fragment", "data": data,
                      "from": self._callsign, "priority": 2, "ts": time.time()}
        log.info('TX text_fragment [%s/%s] seq=%s to=%s "%s"',
                 int(data["frag_index"]) + 1, data["frag_total"],
                 data["seq_nr"], data["to"], data["text_chunk"])

        try:
            self._gateway.enqueue(frame_dict, priority=2)
        except Exception as exc:
            log.error("Gateway.enqueue() Fehler: %s", exc)
            raise web.HTTPInternalServerError(
                text=f'{{"error":"{exc}"}}', content_type="application/json")

        self._publish_log("INFO",
            f'TX Fragment [{int(data["frag_index"])+1}/{data["frag_total"]}]: '
            f'→{data["to"]}  "{data["text_chunk"]}"')
        return web.json_response({
            "ok": True,
            "frag_index": data["frag_index"],
            "frag_total": data["frag_total"],
            "message": f'Fragment {int(data["frag_index"])+1}/{data["frag_total"]} eingereiht',
        })

    async def _handle_tx_emergency(self, request: web.Request) -> web.Response:
        return await self._enqueue_tx(request, "emergency", priority=1)

    async def _handle_tx_queue(self, _request: web.Request) -> web.Response:
        """Ausstehende TX-Frames mit geschätztem Sendezeitpunkt (für die GUI)."""
        queue = []
        if self._gateway is not None and hasattr(self._gateway, "get_queue"):
            try:
                queue = self._gateway.get_queue()
            except Exception as exc:
                log.debug("Gateway.get_queue() Fehler: %s", exc)
        return web.json_response({"queue": queue, "now": time.time()})

    async def _handle_tx_queue_clear(self, request: web.Request) -> web.Response:
        """DELETE /api/tx/queue — alle ausstehenden TX-Frames löschen."""
        if self._gateway is None:
            return web.json_response(
                {"error": "Kein TX-Gateway aktiv (Monitor-Modus?)."},
                status=503,
            )
        n = self._gateway.clear_queue()
        log.info("TX-Queue via Web-UI gelöscht (%d Frame(s)).", n)
        return web.json_response({"cleared": n, "message": f"{n} Frame(s) gelöscht."})

    async def _handle_trx_activate(self, request: web.Request) -> web.Response:
        """
        POST /api/trx/activate — TRX-Profil aktivieren.
        Body: {"name": "IC-7610"}
        Übernimmt Profilwerte in rigctld- und audio-Block, speichert gateway.json.
        """
        if self._config_path is None:
            raise web.HTTPBadRequest(
                text='{"error":"Schreiben nicht aktiviert."}',
                content_type="application/json")
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(
                text='{"error":"Ungültiger JSON-Body"}',
                content_type="application/json")

        name = str(body.get("name", "")).strip()
        if not name:
            raise web.HTTPBadRequest(
                text='{"error":"name erforderlich"}',
                content_type="application/json")

        profiles = self._config.get("trx_profiles", [])
        profile  = next((p for p in profiles if p.get("name") == name), None)
        if profile is None:
            return web.json_response(
                {"ok": False, "error": f"Profil '{name}' nicht gefunden."},
                status=404)

        async with self._config_write_lock:
            # Profilwerte in aktive Konfiguration übernehmen
            self._config["active_trx_profile"] = name
            if profile.get("ptt_backend") == "hamlib":
                self._config.setdefault("rigctld", {})
                self._config["rigctld"].update({
                    "auto_start": profile.get("auto_start", True),
                    "rig_model":  profile.get("rig_model"),
                    "device":     profile.get("device"),
                    "baud":       profile.get("baud", 19200),
                    "host":       "localhost",
                    "port":       4532,
                })
            audio = dict(self._config.get("tx_audio")
                         or self._config.get("audio") or {})
            audio["ptt_backend"] = profile.get("ptt_backend", "null")
            # TX-Audiogerät: audio_device_tx (Legacy-Fallback: audio_device) → tx_audio.device
            tx_dev = profile.get("audio_device_tx", profile.get("audio_device"))
            if tx_dev is not None:
                audio["device"] = tx_dev
            self._config["tx_audio"] = audio
            self._config["audio"]    = audio   # Kompatibilität altes Format
            # RX-Audiogerät: audio_device_rx → rx_audio.device (fehlend/None = "wie TX")
            if profile.get("audio_device_rx") is not None:
                self._config.setdefault(
                    "rx_audio", self._config.get("rx") or {}
                )["device"] = profile["audio_device_rx"]
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, self._save_config_atomic)
            except Exception as exc:
                log.error("TRX-Profil speichern fehlgeschlagen: %s", exc)
                raise web.HTTPInternalServerError(
                    text=f'{{"error":"Schreiben fehlgeschlagen: {exc}"}}',
                    content_type="application/json")

        log.vital("TRX-Profil aktiviert: %s", name)

        # Beim Profil-Wechsel rigctld immer still neu starten — kein Konflikt-Dialog.
        # Der User hat bewusst ein Profil aktiviert; der laufende rigctld (egal ob
        # GUST-eigen oder vom Gateway gestartet) wird in _do_rigctld_restart() beendet.
        if (profile.get("ptt_backend") == "hamlib"
                and profile.get("auto_start", True)):
            return await self._do_rigctld_restart(
                profile.get("rig_model"),
                profile.get("device"),
                profile.get("baud", 19200))

        return web.json_response({
            "ok": True,
            "message": f"Profil '{name}' aktiviert.",
            "profile": profile,
        })

    async def _handle_trx_save(self, request: web.Request) -> web.Response:
        """
        POST /api/trx/save — Profil anlegen oder aktualisieren.
        Body: vollständiges Profil-Objekt mit name (Pflicht).
        Existiert ein Profil mit diesem Namen: wird ersetzt.
        Existiert keines: wird am Ende der Liste eingefügt.
        """
        if self._config_path is None:
            raise web.HTTPBadRequest(
                text='{"error":"Schreiben nicht aktiviert."}',
                content_type="application/json")
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(
                text='{"error":"Ungültiger JSON-Body"}',
                content_type="application/json")

        name = str(body.get("name", "")).strip()
        if not name:
            raise web.HTTPBadRequest(
                text='{"error":"name erforderlich"}',
                content_type="application/json")

        # Pflichtfelder prüfen
        required = ["rig_model", "device", "baud",
                    "audio_device_tx", "ptt_backend"]
        for f in required:
            if f not in body:
                raise web.HTTPBadRequest(
                    text=f'{{"error":"Feld \'{f}\' fehlt"}}',
                    content_type="application/json")

        # Profil-Objekt bauen (nur bekannte Felder übernehmen)
        profile = {
            "name":           name,
            "rig_model":      int(body["rig_model"]),
            "device":         str(body["device"]),
            "baud":           int(body["baud"]),
            "audio_device_tx": body["audio_device_tx"],
            "ptt_backend":    str(body["ptt_backend"]),
            "auto_start":     bool(body.get("auto_start", True)),
        }
        # Optionale Felder
        if body.get("audio_device_rx") is not None:
            profile["audio_device_rx"] = body["audio_device_rx"]
        if body.get("ptt_delay_ms") is not None:
            profile["ptt_delay_ms"] = int(body["ptt_delay_ms"])
        if body.get("level") is not None:
            profile["level"] = body["level"]
        if body.get("_comment"):
            profile["_comment"] = str(body["_comment"])

        async with self._config_write_lock:
            old_profiles = list(self._config.get("trx_profiles", []))
            profiles = list(old_profiles)
            idx = next((i for i, p in enumerate(profiles)
                        if p.get("name") == name), None)
            if idx is not None:
                profiles[idx] = profile   # Ersetzen
                action = "aktualisiert"
            else:
                profiles.append(profile)  # Neu anfügen
                action = "angelegt"
            self._config["trx_profiles"] = profiles
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, self._save_config_atomic)
            except Exception as exc:
                self._config["trx_profiles"] = old_profiles   # Rollback
                raise web.HTTPInternalServerError(
                    text=f'{{"error":"Schreiben fehlgeschlagen: {exc}"}}',
                    content_type="application/json")

        log.info("TRX-Profil %s: %s", action, name)
        return web.json_response({
            "ok": True,
            "message": f"Profil '{name}' {action}.",
            "profile": profile,
            "count": len(profiles),
        })

    async def _handle_trx_delete(self, request: web.Request) -> web.Response:
        """
        DELETE /api/trx/profile — Profil löschen.
        Query-Parameter: ?name=IC-7610
        Aktives Profil kann nicht gelöscht werden.
        Letztes Profil kann nicht gelöscht werden.
        """
        if self._config_path is None:
            raise web.HTTPBadRequest(
                text='{"error":"Schreiben nicht aktiviert."}',
                content_type="application/json")

        name = request.rel_url.query.get("name", "").strip()
        if not name:
            raise web.HTTPBadRequest(
                text='{"error":"name-Parameter fehlt"}',
                content_type="application/json")

        async with self._config_write_lock:
            profiles = list(self._config.get("trx_profiles", []))
            active   = self._config.get("active_trx_profile", "")

            if name == active:
                return web.json_response(
                    {"ok": False,
                     "error": "Aktives Profil kann nicht gelöscht werden."},
                    status=409)
            if len(profiles) <= 1:
                return web.json_response(
                    {"ok": False,
                     "error": "Letztes Profil kann nicht gelöscht werden."},
                    status=409)

            new_profiles = [p for p in profiles if p.get("name") != name]
            if len(new_profiles) == len(profiles):
                return web.json_response(
                    {"ok": False,
                     "error": f"Profil '{name}' nicht gefunden."},
                    status=404)

            self._config["trx_profiles"] = new_profiles
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, self._save_config_atomic)
            except Exception as exc:
                self._config["trx_profiles"] = profiles   # Rollback
                raise web.HTTPInternalServerError(
                    text=f'{{"error":"Schreiben fehlgeschlagen: {exc}"}}',
                    content_type="application/json")

        log.info("TRX-Profil gelöscht: %s", name)
        return web.json_response({
            "ok": True,
            "message": f"Profil '{name}' gelöscht.",
            "remaining": len(new_profiles),
        })

    async def _enqueue_tx(self, request: web.Request,
                          frame_type: str, priority: int) -> web.Response:
        """Gemeinsame Logik: JSON-Body parsen → Gateway übergeben."""
        try:
            data = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='{"error":"Ungültiger JSON-Body"}',
                                     content_type="application/json")

        frame_dict = {"frame_type": frame_type, "data": data,
                      "from": self._callsign, "priority": priority,
                      "ts": time.time()}
        log.info("TX-Anfrage via Web: type=%s priority=%d data=%s",
                 frame_type, priority, data)

        # Kein TX-Gateway → Station ist im Empfangs-/Monitor-Modus.
        # Ehrliche Fehlermeldung statt vorgetäuschtem Erfolg (das Web-UI
        # zeigt den Fehler an, weil apiFetch bei !ok eine Exception wirft).
        if self._gateway is None:
            log.warning("TX-Anfrage ignoriert — kein TX-Gateway aktiv "
                        "(Empfangs-/Monitor-Modus?).")
            raise web.HTTPServiceUnavailable(
                text='{"error":"Kein TX-Gateway aktiv — diese Station ist '
                     'im Empfangs-/Monitor-Modus."}',
                content_type="application/json")

        try:
            self._gateway.enqueue(frame_dict, priority=priority)
        except Exception as exc:
            log.error("Gateway.enqueue() Fehler: %s", exc)
            raise web.HTTPInternalServerError(
                text=f'{{"error":"{exc}"}}', content_type="application/json")

        self._publish_log("INFO",
            f"TX eingereiht: {frame_type} (Prio {priority}) von {self._callsign}")
        return web.json_response({
            "ok": True,
            "message": f"{frame_type.capitalize()}-Frame eingereiht (Prio {priority})"
        })

    # ── AUDIO-KONFIGURATION ───────────────────────────────────────────

    async def _handle_audio_devices(self, _request: web.Request) -> web.Response:
        """Liste der verfügbaren Audiogeräte (Input + Output) via sounddevice.

        Jedes Gerät erscheint mehrfach — einmal pro Host-API. Unter Windows sind
        das MME, DirectSound, WASAPI, WDM-KS; unter Linux i.d.R. ALSA und (falls
        jackd läuft) JACK. Wir liefern den Host-API-Namen pro Gerät mit, damit das
        Web-UI die Dropdowns nach Host-API gruppieren kann (<optgroup>).
        """
        try:
            import sounddevice as sd
            devs = sd.query_devices()
            hostapis = sd.query_hostapis()
            default_in, default_out = sd.default.device
        except Exception as e:
            return web.json_response(
                {"error": f"sounddevice nicht verfügbar: {e}"},
                status=500,
            )

        def _hostapi_name(idx: int) -> str:
            if 0 <= idx < len(hostapis):
                return str(hostapis[idx].get("name", f"API {idx}"))
            return f"API {idx}"

        inputs, outputs = [], []
        for i, d in enumerate(devs):
            api = int(d.get("hostapi", -1))
            entry = {
                "id":   i,
                "name": str(d.get("name", "?")).strip(),
                "host_api": api,
                "host_api_name": _hostapi_name(api),
                "default_samplerate": float(d.get("default_samplerate", 0.0)),
            }
            if d.get("max_input_channels", 0) > 0:
                inputs.append({**entry,
                               "channels": int(d["max_input_channels"]),
                               "is_default": (i == default_in)})
            if d.get("max_output_channels", 0) > 0:
                outputs.append({**entry,
                                "channels": int(d["max_output_channels"]),
                                "is_default": (i == default_out)})

        return web.json_response({
            "input":          inputs,
            "output":         outputs,
            "default_input":  default_in,
            "default_output": default_out,
        })

    async def _handle_audio_config_get(self, _request: web.Request) -> web.Response:
        """Aktuell konfigurierte Audiogeräte zurückgeben."""
        audio = (self._config.get("tx_audio") or
                 self._config.get("audio") or {})
        rx    = (self._config.get("rx_audio") or
                 self._config.get("rx") or {})
        return web.json_response({
            "tx_device":   audio.get("device"),      # int / str / None
            "rx_device":   rx.get("device"),         # None = "wie TX"
            "ptt_backend": audio.get("ptt_backend", "null"),
            "level":       audio.get("level"),
            "deep_decode": bool(rx.get("deep_decode", False)),
            "config_path": self._config_path,
            "writable":    self._config_path is not None,
        })

    async def _handle_audio_config_post(self, request: web.Request) -> web.Response:
        """
        Neue Audio-Auswahl persistieren in gateway.json (atomar).

        Body: {"tx_device": <int|str|null>, "rx_device": <int|str|null>}

        Nur die im Body gesetzten Felder werden überschrieben. Nicht gesetzte
        Felder bleiben unverändert. rx_device=null bedeutet "wie TX".

        Wirkung:
          • TX: ab dem nächsten transmit_frame() wirksam (cfg["audio"]["device"]
            wird live gelesen).
          • RX: erfordert Neustart des Daemons — der RX-Loop hält das Gerät
            beim Start fest. Response enthält rx_restart_required: true wenn
            sich rx_device geändert hat.
        """
        if self._config_path is None:
            raise web.HTTPBadRequest(
                text='{"error":"Schreiben nicht aktiviert (kein config_path)."}',
                content_type="application/json",
            )

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(
                text='{"error":"Ungültiger JSON-Body"}',
                content_type="application/json",
            )

        # Integer-ID bevorzugt, sonst String (Gerätename), leer/None = Standard
        def _coerce(val):
            if val is None or val == "":
                return None
            try:
                return int(val)
            except (TypeError, ValueError):
                return str(val)

        # Nicht im Body genannte Felder bleiben unverändert
        tx_set = "tx_device" in body
        rx_set = "rx_device" in body
        new_tx = _coerce(body["tx_device"]) if tx_set else None
        new_rx = _coerce(body["rx_device"]) if rx_set else None

        async with self._config_write_lock:
            # Aktuellen Zustand merken (für Diff in Response)
            old_audio = dict(self._config.get("tx_audio")
                             or self._config.get("audio") or {})
            old_rx    = dict(self._config.get("rx_audio")
                             or self._config.get("rx") or {})

            # setdefault mit Alt-Dict als Seed: bei Alt-Configs zeigt
            # tx_audio dann auf DASSELBE Dict wie audio (konsistent)
            if tx_set:
                self._config.setdefault(
                    "tx_audio", self._config.get("audio") or {}
                )["device"] = new_tx
            if rx_set:
                self._config.setdefault(
                    "rx_audio", self._config.get("rx") or {}
                )["device"] = new_rx

            # In Datei schreiben (atomar)
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, self._save_config_atomic
                )
            except Exception as e:
                # Rollback im Speicher
                self._config["tx_audio"] = old_audio
                self._config["rx_audio"] = old_rx
                log.error("gateway.json schreiben fehlgeschlagen: %s", e)
                raise web.HTTPInternalServerError(
                    text=f'{{"error":"Schreiben fehlgeschlagen: {e}"}}',
                    content_type="application/json",
                )

        rx_changed = rx_set and (old_rx.get("device") != new_rx)
        tx_changed = tx_set and (old_audio.get("device") != new_tx)

        msg_parts = []
        if tx_changed:
            msg_parts.append(f"TX → {new_tx if new_tx is not None else 'Standard'}")
        if rx_changed:
            msg_parts.append(f"RX → {new_rx if new_rx is not None else 'wie TX'}")
        msg = "Audio-Konfiguration gespeichert" + (
            ": " + ", ".join(msg_parts) if msg_parts else " (keine Änderung)"
        )

        log.info("Audio-Config geschrieben: tx=%r rx=%r (Datei: %s)",
                 new_tx if tx_set else "(unverändert)",
                 new_rx if rx_set else "(unverändert)",
                 self._config_path)
        self._publish_log("INFO", msg)

        return web.json_response({
            "ok": True,
            "message": msg,
            "tx_device": (self._config.get("tx_audio")
                          or self._config.get("audio") or {}).get("device"),
            "rx_device": (self._config.get("rx_audio")
                          or self._config.get("rx") or {}).get("device"),
            "tx_restart_required": False,    # TX liest cfg live
            "rx_restart_required": rx_changed,
        })

    # ── SDR-TX (SoapySDR) ─────────────────────────────────────────────

    async def _handle_sdr_devices(self, _request: web.Request) -> web.Response:
        """
        GET /api/sdr/devices — Discovery (ADR-16) + Modul-Diagnose.

        Liefert immer 200, auch ohne installiertes SoapySDR oder ohne Gerät
        (leere Listen). Jeder Aufruf re-enumeriert (dient zugleich als „Rescan").
        Die Enumeration kann je nach Treiber mehrere Sekunden brauchen — wir
        führen sie deshalb in einem Thread-Executor aus, damit der Event-Loop
        reaktiv bleibt.
        """
        import gust_soapy_tx as sx
        loop = asyncio.get_running_loop()
        devices = await loop.run_in_executor(None, sx.enumerate_tx_devices)
        modules = await loop.run_in_executor(None, sx.list_modules)
        # Aktuelle Auswahl mitliefern, damit die GUI direkt selektieren kann.
        selected = (self._config.get("sdr_tx") or {}).get("device_args") or {}
        return web.json_response({
            "available":  sx.soapy_available(),
            "devices":    devices,
            "modules":    modules,
            "selected":   selected,
        })

    async def _handle_sdr_caps(self, request: web.Request) -> web.Response:
        """
        GET /api/sdr/caps?driver=…&serial=… — Geräteparameter dynamisch lesen.

        Alle Query-Parameter (außer 'channel') werden 1:1 als Device-Args
        weitergereicht — also exakt das, was auch in `gateway.json.sdr_tx.device_args`
        steht. Antwort enthält Gain-Elemente+Ranges, Sample-Rate-Bereich,
        Antennen, Frequenzbereich.
        """
        import gust_soapy_tx as sx
        if not sx.soapy_available():
            return web.json_response(
                {"error": "SoapySDR nicht installiert"}, status=503)

        args = {k: v for k, v in request.rel_url.query.items() if k != "channel"}
        if not args:
            raise web.HTTPBadRequest(
                text='{"error":"Mindestens ein Device-Arg (z.B. driver=…) nötig."}',
                content_type="application/json")
        try:
            channel = int(request.rel_url.query.get("channel", 0))
        except ValueError:
            channel = 0

        loop = asyncio.get_running_loop()
        try:
            caps = await loop.run_in_executor(
                None, lambda: sx.device_capabilities(args, channel=channel))
        except Exception as exc:
            log.warning("device_capabilities(%s) Fehler: %s", args, exc)
            return web.json_response(
                {"error": f"Gerät konnte nicht abgefragt werden: {exc}",
                 "args":  args},
                status=502)
        return web.json_response({"args": args, "channel": channel, "caps": caps})

    async def _handle_sdr_config_get(self, _request: web.Request) -> web.Response:
        """Aktueller `sdr_tx`-Block. Leerer Block wenn (noch) nicht konfiguriert."""
        sdr = dict(self._config.get("sdr_tx") or {})
        # Defaults defensiv, damit das UI immer Werte hat zum Vorbelegen
        sdr.setdefault("enabled",     False)
        sdr.setdefault("device_args", {})
        sdr.setdefault("label",       "")
        sdr.setdefault("sample_rate", 2_000_000)
        sdr.setdefault("freq_hz",     14_110_000)
        sdr.setdefault("antenna",     "")
        sdr.setdefault("gain",        {"normalized": 0.5})
        sdr.setdefault("tx_channel",  0)
        return web.json_response({
            "sdr_tx":      sdr,
            "writable":    self._config_path is not None,
            "config_path": self._config_path,
        })

    async def _handle_sdr_config_post(self, request: web.Request) -> web.Response:
        """
        POST /api/sdr/config — `sdr_tx`-Block persistieren.

        Body = vollständiger sdr_tx-Block (kein partial-merge — die GUI schickt
        alle relevanten Felder, das vereinfacht die Validierung). Bei
        `enabled=true` wird mindestens `device_args.driver` gefordert.
        """
        if self._config_path is None:
            raise web.HTTPBadRequest(
                text='{"error":"Schreiben nicht aktiviert (kein config_path)."}',
                content_type="application/json")
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(
                text='{"error":"Ungültiger JSON-Body"}',
                content_type="application/json")

        new = dict(body or {})
        if new.get("enabled"):
            args = new.get("device_args") or {}
            if not isinstance(args, dict) or "driver" not in args:
                raise web.HTTPBadRequest(
                    text='{"error":"enabled=true erfordert device_args.driver"}',
                    content_type="application/json")

        # Numerische Felder weich coercen — die GUI schickt Strings aus <input>.
        def _num(v, default, cast):
            try:
                return cast(v) if v not in (None, "") else default
            except (TypeError, ValueError):
                return default

        normalised = {
            "enabled":     bool(new.get("enabled", False)),
            "device_args": new.get("device_args") or {},
            "label":       str(new.get("label", "")),
            "sample_rate": _num(new.get("sample_rate"), 2_000_000, float),
            "freq_hz":     _num(new.get("freq_hz"),    14_110_000, float),
            "antenna":     str(new.get("antenna", "")),
            "gain":        new.get("gain") or {},
            "tx_channel":  _num(new.get("tx_channel"), 0, int),
        }

        async with self._config_write_lock:
            old = dict(self._config.get("sdr_tx") or {})
            self._config["sdr_tx"] = normalised
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, self._save_config_atomic)
            except Exception as exc:
                self._config["sdr_tx"] = old
                log.error("sdr_tx schreiben fehlgeschlagen: %s", exc)
                raise web.HTTPInternalServerError(
                    text=f'{{"error":"Schreiben fehlgeschlagen: {exc}"}}',
                    content_type="application/json")

        log.info("sdr_tx aktualisiert: enabled=%s args=%s",
                 normalised["enabled"], normalised["device_args"])
        self._publish_log("INFO",
            f"SDR-TX {'aktiviert' if normalised['enabled'] else 'deaktiviert'}"
            + (f" — {normalised['label']}" if normalised['label'] else ""))
        return web.json_response({
            "ok":      True,
            "sdr_tx":  normalised,
            "message": "SDR-TX-Konfiguration gespeichert",
            # TX-Pfad-Wechsel greift beim nächsten Sendevorgang.
            "tx_restart_required": False,
        })

    # ── SDR-PROFILE (Schritt 3 — analog zu TRX-Profilen) ─────────────

    async def _handle_sdr_scan(self, _request: web.Request
                               ) -> web.Response:
        """
        GET /api/sdr/scan — enumerate mit RX+TX-Capabilities.
        Dient als "SDR-Geräte suchen"-Button in der UI.
        Liefert immer 200 (leere Liste wenn kein SoapySDR).
        """
        import gust_soapy_tx as sx
        loop = asyncio.get_running_loop()
        devices = await loop.run_in_executor(
            None, sx.enumerate_all_devices)
        modules = await loop.run_in_executor(
            None, sx.list_modules)
        profiles = self._config.get("sdr_profiles", [])
        return web.json_response({
            "available": sx.soapy_available(),
            "devices":   devices,
            "modules":   modules,
            "profiles":  profiles,
            "active_rx": self._config.get("active_sdr_rx_profile"),
            "active_tx": self._config.get("active_sdr_tx_profile"),
        })

    async def _handle_sdr_profile_save(self,
            request: web.Request) -> web.Response:
        """
        POST /api/sdr/profile/save — Profil anlegen/aktualisieren.
        Body: vollständiges Profil-Objekt.
        Pflicht: name, type ("rx"|"tx"|"trx"), driver.
        Optional: serial, _comment, rx {…}, tx {…}.
        Existiert ein Profil mit diesem Namen: wird ersetzt.
        """
        if self._config_path is None:
            raise web.HTTPBadRequest(
                text='{"error":"Schreiben nicht aktiviert."}',
                content_type="application/json")
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(
                text='{"error":"Ungültiger JSON-Body"}',
                content_type="application/json")

        name   = str(body.get("name", "")).strip()
        ptype  = str(body.get("type", "")).strip()
        driver = str(body.get("driver", "")).strip()

        if not name:
            raise web.HTTPBadRequest(
                text='{"error":"name erforderlich"}',
                content_type="application/json")
        if ptype not in ("rx", "tx", "trx"):
            raise web.HTTPBadRequest(
                text='{"error":"type muss rx, tx oder trx sein"}',
                content_type="application/json")
        if not driver:
            raise web.HTTPBadRequest(
                text='{"error":"driver erforderlich"}',
                content_type="application/json")

        profile = {
            "name":   name,
            "type":   ptype,
            "driver": driver,
        }
        if body.get("serial"):
            profile["serial"] = str(body["serial"])
        if body.get("_comment"):
            profile["_comment"] = str(body["_comment"])
        # rx-Unter-Objekt — nur bei type rx oder trx
        if ptype in ("rx", "trx") and isinstance(body.get("rx"), dict):
            profile["rx"] = body["rx"]
        # tx-Unter-Objekt — nur bei type tx oder trx
        if ptype in ("tx", "trx") and isinstance(body.get("tx"), dict):
            profile["tx"] = body["tx"]

        async with self._config_write_lock:
            profiles = list(self._config.get("sdr_profiles", []))
            old_profiles = list(profiles)
            idx = next((i for i, p in enumerate(profiles)
                        if p.get("name") == name), None)
            if idx is not None:
                profiles[idx] = profile
                action = "aktualisiert"
            else:
                profiles.append(profile)
                action = "angelegt"
            self._config["sdr_profiles"] = profiles
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, self._save_config_atomic)
            except Exception as exc:
                self._config["sdr_profiles"] = old_profiles
                raise web.HTTPInternalServerError(
                    text=f'{{"error":"Schreiben fehlgeschlagen: {exc}"}}',
                    content_type="application/json")

        log.info("SDR-Profil %s: %s", action, name)
        return web.json_response({
            "ok": True,
            "message": f"Profil '{name}' {action}.",
            "profile": profile,
            "count":   len(profiles),
        })

    async def _handle_sdr_profile_delete(self,
            request: web.Request) -> web.Response:
        """
        DELETE /api/sdr/profile?name=HackRF
        Aktives RX- oder TX-Profil kann nicht gelöscht werden.
        Letztes Profil kann nicht gelöscht werden.
        """
        if self._config_path is None:
            raise web.HTTPBadRequest(
                text='{"error":"Schreiben nicht aktiviert."}',
                content_type="application/json")
        name = request.rel_url.query.get("name", "").strip()
        if not name:
            raise web.HTTPBadRequest(
                text='{"error":"name-Parameter fehlt"}',
                content_type="application/json")

        async with self._config_write_lock:
            profiles  = list(self._config.get("sdr_profiles", []))
            active_rx = self._config.get("active_sdr_rx_profile", "")
            active_tx = self._config.get("active_sdr_tx_profile", "")

            if name in (active_rx, active_tx):
                return web.json_response(
                    {"ok": False,
                     "error": "Aktives Profil kann nicht "
                              "gelöscht werden."},
                    status=409)
            if len(profiles) <= 1:
                return web.json_response(
                    {"ok": False,
                     "error": "Letztes Profil kann nicht "
                              "gelöscht werden."},
                    status=409)

            new_profiles = [p for p in profiles
                            if p.get("name") != name]
            if len(new_profiles) == len(profiles):
                return web.json_response(
                    {"ok": False,
                     "error": f"Profil '{name}' nicht gefunden."},
                    status=404)

            old_profiles = list(profiles)
            self._config["sdr_profiles"] = new_profiles
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, self._save_config_atomic)
            except Exception as exc:
                self._config["sdr_profiles"] = old_profiles
                raise web.HTTPInternalServerError(
                    text=f'{{"error":"Schreiben fehlgeschlagen: {exc}"}}',
                    content_type="application/json")

        log.info("SDR-Profil gelöscht: %s", name)
        return web.json_response({
            "ok": True,
            "message": f"Profil '{name}' gelöscht.",
            "remaining": len(new_profiles),
        })

    async def _sdr_activate(self, request: web.Request,
                            direction: str) -> web.Response:
        """
        Gemeinsame Logik für activate/rx und activate/tx.
        Body: {"name": "SDRplay"} oder {"name": null}
        null deaktiviert (zurück auf Audio-Pfad).
        """
        if self._config_path is None:
            raise web.HTTPBadRequest(
                text='{"error":"Schreiben nicht aktiviert."}',
                content_type="application/json")
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(
                text='{"error":"Ungültiger JSON-Body"}',
                content_type="application/json")

        name = body.get("name")  # None = deaktivieren
        if name is not None:
            name = str(name).strip() or None

        cfg_key  = f"active_sdr_{direction}_profile"
        ok_types = ("rx", "trx") if direction == "rx" else ("tx", "trx")

        # Prüfen ob Profil existiert und die Richtung unterstützt
        if name is not None:
            profiles = self._config.get("sdr_profiles", [])
            profile  = next((p for p in profiles
                             if p.get("name") == name), None)
            if profile is None:
                return web.json_response(
                    {"ok": False,
                     "error": f"Profil '{name}' nicht gefunden."},
                    status=404)
            if profile.get("type") not in ok_types:
                return web.json_response(
                    {"ok": False,
                     "error": f"Profil '{name}' ist nicht "
                              f"{direction.upper()}-fähig "
                              f"(type={profile.get('type')})."},
                    status=409)

        async with self._config_write_lock:
            self._config[cfg_key] = name
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, self._save_config_atomic)
            except Exception as exc:
                raise web.HTTPInternalServerError(
                    text=f'{{"error":"{exc}"}}',
                    content_type="application/json")

        if direction == "rx":
            msg = (f"SDR-RX aktiviert: {name}"
                   if name else "SDR-RX deaktiviert (Audio/VAC)")
        else:
            msg = (f"SDR-TX aktiviert: {name}"
                   if name else "SDR-TX deaktiviert (Audio-TX-Pfad)")
        log.info(msg)
        return web.json_response({
            "ok": True,
            "message": msg,
            cfg_key: name,
        })

    async def _handle_sdr_activate_rx(self,
            request: web.Request) -> web.Response:
        """POST /api/sdr/profile/activate/rx — active_sdr_rx_profile setzen."""
        return await self._sdr_activate(request, "rx")

    async def _handle_sdr_activate_tx(self,
            request: web.Request) -> web.Response:
        """POST /api/sdr/profile/activate/tx — active_sdr_tx_profile setzen."""
        return await self._sdr_activate(request, "tx")

    # ── HAMLIB / rigctld (P5-14) ──────────────────────────────────────

    def _hamlib_endpoint(self) -> tuple:
        """rigctld-Host/Port aus self._config ermitteln (Defaults localhost:4532)."""
        audio = ((self._config.get("tx_audio") or self._config.get("audio") or {})
                 if isinstance(self._config, dict) else {})
        rig   = self._config.get("rigctld", {}) if isinstance(self._config, dict) else {}
        host = (audio.get("hamlib_host")
                or rig.get("host")
                or "localhost")
        try:
            port = int(audio.get("hamlib_port")
                       or rig.get("port")
                       or 4532)
        except (TypeError, ValueError):
            port = 4532
        return host, port

    async def _handle_hamlib_ports(self, _request: web.Request) -> web.Response:
        """
        GET /api/hamlib/ports — verfügbare serielle Ports plattformübergreifend.

        Nutzt pyserial (serial.tools.list_ports). Ist pyserial nicht installiert,
        wird eine leere Liste zurückgegeben (kein Fehler) — die GUI bleibt
        bedienbar, der User kann den Port-Wert manuell setzen.
        """
        ports = []
        try:
            from serial.tools import list_ports  # pyserial, optional
            for p in list_ports.comports():
                ports.append({
                    "device":      p.device,
                    "description": (p.description or "").strip(),
                })
        except ImportError:
            log.debug("pyserial nicht installiert — /api/hamlib/ports liefert leere Liste")
        except Exception as exc:
            log.warning("Port-Enumeration fehlgeschlagen: %s", exc)
        ports.sort(key=lambda x: x["device"])
        return web.json_response({"ports": ports})

    async def _handle_hamlib_models(self, request: web.Request) -> web.Response:
        """
        GET /api/hamlib/models?q=… — Hamlib-Rig-Liste via `rigctld --list`.

        Parst die tabellarische Ausgabe (erste Spalte = Modell-ID = Integer,
        Rest der Zeile = Label). Filtert case-insensitive nach `q`, gibt max.
        50 Treffer zurück. Modell 1 (Hamlib Dummy) ist immer der erste Eintrag.
        Ist rigctld nicht im PATH, kommt eine leere Liste + Fehlerfeld zurück.
        """
        q = (request.query.get("q") or "").strip().lower()

        def _run_list() -> tuple:
            import subprocess
            try:
                proc = subprocess.run(
                    ["rigctld", "--list"],
                    capture_output=True, text=True, timeout=10,
                )
            except FileNotFoundError:
                return None, "rigctld nicht im PATH gefunden"
            except subprocess.TimeoutExpired:
                return None, "rigctld --list Timeout"
            except Exception as exc:                       # pragma: no cover
                return None, f"rigctld --list fehlgeschlagen: {exc}"
            return (proc.stdout or "") + (proc.stderr or ""), None

        loop = asyncio.get_running_loop()
        raw, err = await loop.run_in_executor(None, _run_list)
        if raw is None:
            return web.json_response({"models": [], "error": err})

        models = []
        dummy = None
        for line in raw.splitlines():
            line = line.rstrip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            try:
                model_id = int(parts[0])
            except ValueError:
                # Kopfzeile / Trennzeile — überspringen
                continue
            label = parts[1].strip()
            entry = {"id": model_id, "label": label}
            if model_id == 1:
                dummy = entry           # Hamlib Dummy gesondert vormerken
                continue
            if q and q not in label.lower() and q not in str(model_id):
                continue
            models.append(entry)
            if len(models) >= 50:
                break

        # Modell 1 immer als ersten Eintrag (unabhängig von q)
        if dummy is None:
            dummy = {"id": 1, "label": "Hamlib Dummy"}
        result = [dummy] + models
        return web.json_response({"models": result, "error": None})

    async def _handle_hamlib_status(self, _request: web.Request) -> web.Response:
        """
        GET /api/hamlib/status — rigctld TCP-Erreichbarkeit + aktuelle Frequenz.

        Verbindet sich auf hamlib_host:hamlib_port (Defaults localhost:4532),
        sendet 'f\\n' und liest die Frequenz (float). Timeout 1 s, keine
        Exception nach außen — Fehler werden als running:false + error gemeldet.
        """
        host, port = self._hamlib_endpoint()

        def _probe() -> tuple:
            import socket
            try:
                with socket.create_connection((host, port), timeout=1.0) as sock:
                    sock.settimeout(1.0)
                    sock.sendall(b"f\n")
                    data = sock.recv(256).decode("ascii", "replace").strip()
                # Antwort ist üblicherweise die Frequenz in Hz als Ganzzahl.
                freq = None
                first = data.splitlines()[0].strip() if data else ""
                try:
                    freq = float(first)
                except ValueError:
                    freq = None
                return True, freq, None
            except Exception as exc:
                return False, None, str(exc)

        loop = asyncio.get_running_loop()
        running, freq, err = await loop.run_in_executor(None, _probe)
        return web.json_response({
            "running": running,
            "freq_hz": freq,
            "error":   err,
        })

    async def _handle_hamlib_start(self, _request: web.Request) -> web.Response:
        """
        POST /api/hamlib/start — rigctld via ensure_rigctld_running() starten.

        Bei Erfolg wird das Popen-Handle (falls GUST den Prozess gestartet hat)
        in self._rigctld_proc gemerkt, damit /api/hamlib/stop ihn beenden kann.
        Fehler werden mit HTTP 200 + ok:false im Body gemeldet (die GUI wertet
        'ok' aus, kein HTTP-Fehler).
        """
        def _start():
            from gust_audio import ensure_rigctld_running
            return ensure_rigctld_running(self._config, verbose=False)

        loop = asyncio.get_running_loop()
        try:
            proc = await loop.run_in_executor(None, _start)
        except RuntimeError as exc:
            return web.json_response({"ok": False, "error": str(exc)})
        except Exception as exc:                            # pragma: no cover
            return web.json_response({"ok": False, "error": str(exc)})

        if proc is not None:
            # GUST hat rigctld selbst gestartet → Handle für /stop merken.
            self._rigctld_proc = proc
            msg = "rigctld gestartet"
            device = self._config.get("rigctld", {}).get("device") or "?"
            log.vital("[rigctld] gestartet via GUI (PID %d, %s)",
                      proc.pid, device)
        else:
            # rigctld lief bereits — nicht von GUST gestartet.
            msg = "rigctld war bereits erreichbar"
        return web.json_response({"ok": True, "message": msg})

    async def _handle_hamlib_stop(self, _request: web.Request) -> web.Response:
        """
        POST /api/hamlib/stop — rigctld beenden, NUR wenn GUST ihn gestartet hat.

        Extern gestartete rigctld-Instanzen (self._rigctld_proc is None) werden
        nicht angetastet.
        """
        proc = self._rigctld_proc
        if proc is None:
            return web.json_response({
                "ok": False,
                "error": "rigctld wurde nicht von GUST gestartet",
            })
        try:
            proc.terminate()
        except Exception as exc:
            log.warning("rigctld terminate fehlgeschlagen: %s", exc)
        self._rigctld_proc = None
        log.vital("[rigctld] gestoppt via GUI")
        return web.json_response({"ok": True, "message": "rigctld gestoppt"})

    def _find_port_owner(self, port: int) -> dict | None:
        """
        Prueft ob ein Prozess auf dem angegebenen TCP-Port lauscht.
        Gibt {pid, name} zurueck oder None wenn der Port frei ist.
        Benoetigt psutil — falls nicht installiert: None zurueckgeben.
        """
        try:
            import psutil
            for conn in psutil.net_connections(kind='tcp'):
                if (conn.laddr.port == port
                        and conn.status == psutil.CONN_LISTEN):
                    try:
                        proc = psutil.Process(conn.pid)
                        return {"pid": conn.pid, "name": proc.name()}
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        return {"pid": conn.pid, "name": "(unbekannt)"}
        except ImportError:
            log.debug("psutil nicht installiert — Port-Konflikt-Pruefung uebersprungen")
        except Exception as exc:
            log.debug("_find_port_owner Fehler: %s", exc)
        return None

    def _managed_rigctld_pid(self) -> "int | None":
        """PID des von GUST gestarteten rigctld, sofern noch laufend.

        Quellen in dieser Reihenfolge: der Web-Server selbst (Start/Tune/Restart
        via GUI) und das TX-Gateway (build_ptt im TX-Pfad startet rigctld bei der
        ersten Sendung, ohne dass die GUI direkt beteiligt ist). Gibt None zurück,
        wenn GUST keinen lebenden eigenen rigctld kennt.
        """
        for proc in (self._rigctld_proc,
                     getattr(self._gateway, "_rigctld_proc", None)):
            if proc is not None and proc.poll() is None:
                return proc.pid
        return None

    async def _restart_or_report_conflict(self, rig_model, device, baud) -> web.Response:
        """Port-Konflikt prüfen, dann rigctld (neu) starten — gemeinsamer Flow.

        Genutzt von _handle_hamlib_config und _handle_trx_activate:
        - Fremdprozess belegt den Port → conflict-Response (GUI fragt User).
        - Eigener rigctld (GUI/Tune/Gateway) oder Port frei → _do_rigctld_restart.
        """
        _, target_port = self._hamlib_endpoint()
        owner = self._find_port_owner(target_port)
        if owner is not None:
            # Prüfen ob der belegende Prozess unser eigener rigctld ist
            # (GUI-Start/Tune/Restart ODER vom TX-Gateway gestartet).
            own_pid = self._managed_rigctld_pid()
            if own_pid is not None and owner["pid"] == own_pid:
                # Eigener rigctld — still neu starten, kein Konflikt-Dialog
                log.info("rigctld (PID %d) ist GUST-eigener Prozess — "
                         "wird still neu gestartet.", own_pid)
                return await self._do_rigctld_restart(rig_model, device, baud)
            # Fremdprozess belegt den Port — User fragen
            return web.json_response({
                "ok":      False,
                "conflict": True,
                "pid":      owner["pid"],
                "proc_name": owner["name"],
                "port":     target_port,
                "message":  (
                    f"Port {target_port} wird von '{owner['name']}' "
                    f"(PID {owner['pid']}) belegt. "
                    f"Soll dieser Prozess beendet und rigctld neu gestartet werden?"
                ),
            })
        # Port frei — direkt starten
        return await self._do_rigctld_restart(rig_model, device, baud)

    async def _handle_hamlib_config(self, request: web.Request) -> web.Response:
        """
        POST /api/hamlib/config — rigctld-Block + PTT-Backend in gateway.json.

        Body: {"rig_model": 2034, "device": "COM5", "baud": 9600,
               "auto_start": true}. Setzt zusätzlich audio.ptt_backend=hamlib
               sowie hamlib_host/hamlib_port. Atomar via _save_config_atomic().
        """
        if self._config_path is None:
            raise web.HTTPBadRequest(
                text='{"error":"Schreiben nicht aktiviert (kein config_path)."}',
                content_type="application/json")
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(
                text='{"error":"Ungültiger JSON-Body"}',
                content_type="application/json")

        def _int(v, default):
            try:
                return int(v) if v not in (None, "") else default
            except (TypeError, ValueError):
                return default

        rig_model  = _int(body.get("rig_model"), None)
        device     = str(body.get("device", "") or "")
        baud       = _int(body.get("baud"), 9600)
        auto_start = bool(body.get("auto_start", True))

        if not rig_model:
            raise web.HTTPBadRequest(
                text='{"error":"rig_model erforderlich"}',
                content_type="application/json")
        if not device:
            raise web.HTTPBadRequest(
                text='{"error":"device (serieller Port) erforderlich"}',
                content_type="application/json")

        profile_created = None
        async with self._config_write_lock:
            old_rig      = self._config.get("rigctld")
            old_audio    = dict(self._config.get("tx_audio")
                                or self._config.get("audio") or {})
            old_profiles = self._config.get("trx_profiles")
            old_active   = self._config.get("active_trx_profile")
            self._config["rigctld"] = {
                "auto_start": auto_start,
                "rig_model":  rig_model,
                "device":     device,
                "baud":       baud,
                "host":       "localhost",
                "port":       4532,
            }
            audio = dict(self._config.get("tx_audio")
                         or self._config.get("audio") or {})
            audio["ptt_backend"] = "hamlib"
            audio["hamlib_host"] = "localhost"
            audio["hamlib_port"] = 4532
            self._config["tx_audio"] = audio
            self._config["audio"]    = audio   # Kompatibilität altes Format

            # Erstes Speichern: automatisch als Profil anlegen falls noch kein
            # trx_profiles-Array existiert (Rückwärtskompatibilität / Onboarding).
            # In derselben gesperrten, atomaren Schreiboperation wie oben.
            if not self._config.get("trx_profiles"):
                first_profile = {
                    "name":         device or "TRX-1",
                    "rig_model":    rig_model,
                    "device":       device,
                    "baud":         baud,
                    "audio_device": audio.get("device"),
                    "ptt_backend":  "hamlib",
                    "auto_start":   auto_start,
                }
                self._config["trx_profiles"]      = [first_profile]
                self._config["active_trx_profile"] = first_profile["name"]
                profile_created = first_profile["name"]

            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, self._save_config_atomic)
            except Exception as exc:
                # Rollback bei Schreibfehler
                if old_rig is None:
                    self._config.pop("rigctld", None)
                else:
                    self._config["rigctld"] = old_rig
                self._config["tx_audio"] = old_audio
                self._config["audio"]    = old_audio
                # Profil-Rollback (nur falls in diesem Aufruf neu angelegt)
                if old_profiles is None:
                    self._config.pop("trx_profiles", None)
                else:
                    self._config["trx_profiles"] = old_profiles
                if old_active is None:
                    self._config.pop("active_trx_profile", None)
                else:
                    self._config["active_trx_profile"] = old_active
                log.error("rigctld-Konfiguration schreiben fehlgeschlagen: %s", exc)
                raise web.HTTPInternalServerError(
                    text=f'{{"error":"Schreiben fehlgeschlagen: {exc}"}}',
                    content_type="application/json")

        log.info("Hamlib-Konfiguration gespeichert: model=%s device=%s baud=%s auto_start=%s",
                 rig_model, device, baud, auto_start)
        self._publish_log("INFO",
            f"Hamlib konfiguriert — Modell {rig_model}, {device} @ {baud} Bd")
        if profile_created:
            log.info("Erstes TRX-Profil automatisch angelegt: %s", profile_created)

        # ── Port-Konflikt prüfen + rigctld (neu) starten ───────────────
        if auto_start:
            return await self._restart_or_report_conflict(rig_model, device, baud)

        return web.json_response({
            "ok": True,
            "message": "Konfiguration gespeichert (auto_start deaktiviert — rigctld nicht gestartet).",
        })

    async def _handle_hamlib_force_restart(self, request: web.Request) -> web.Response:
        """
        POST /api/hamlib/force_restart — beendet den Prozess auf dem konfigurierten
        Port (nach expliziter User-Bestaetigung in der GUI) und startet rigctld neu.
        Body: {pid: int}  — GUST beendet nur genau diesen PID.
        """
        try:
            body = await request.json()
            pid  = int(body.get("pid", 0))
        except Exception:
            return web.json_response({"ok": False, "error": "Ungueltiger Body"})

        if pid <= 0:
            return web.json_response({"ok": False, "error": "Kein gueltiger PID"})

        # Genau diesen Prozess beenden
        try:
            import psutil
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except psutil.TimeoutExpired:
                proc.kill()
            log.vital("[rigctld] Prozess PID %d beendet auf User-Anfrage", pid)
        except ImportError:
            return web.json_response({
                "ok": False,
                "error": "psutil nicht installiert — kann Prozess nicht beenden. Bitte manuell beenden."
            })
        except psutil.NoSuchProcess:
            pass  # bereits beendet — trotzdem weitermachen
        except Exception as exc:
            return web.json_response({"ok": False, "error": f"Prozess beenden fehlgeschlagen: {exc}"})

        # Kurz warten damit Port freigegeben wird
        await asyncio.sleep(1.0)
        self._rigctld_proc = None

        rig  = self._config.get("rigctld", {})
        return await self._do_rigctld_restart(
            rig.get("rig_model"), rig.get("device"), rig.get("baud"))

    async def _do_rigctld_restart(self, rig_model, device, baud) -> web.Response:
        """Startet rigctld mit aktueller Konfiguration und liest Frequenz zur Bestaetigung."""
        await self._stop_tune()
        # Defensiv: rigctld auf Port 4532 beenden — egal ob von GUST oder extern
        # gestartet. Beim Profil-Wechsel muss der alte Prozess weg, sonst Bind-Fehler.
        _, target_port = self._hamlib_endpoint()
        owner = self._find_port_owner(target_port)
        if owner is not None:
            try:
                import psutil
                proc = psutil.Process(owner["pid"])
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except psutil.TimeoutExpired:
                    proc.kill()
                log.info("[rigctld] Prozess PID %d (%s) auf Port %d beendet (Profil-Wechsel).",
                         owner["pid"], owner["name"], target_port)
            except Exception as exc:
                log.warning("[rigctld] Beenden von PID %d fehlgeschlagen: %s", owner["pid"], exc)
            self._rigctld_proc = None
            await asyncio.sleep(0.8)  # Port-Freigabe abwarten
        elif self._rigctld_proc is not None and self._rigctld_proc.poll() is None:
            try:
                self._rigctld_proc.terminate()
                await asyncio.sleep(0.5)
            except Exception:
                pass
            self._rigctld_proc = None
        try:
            from gust_audio import ensure_rigctld_running
            loop = asyncio.get_running_loop()
            proc = await loop.run_in_executor(None, ensure_rigctld_running, self._config)
            self._rigctld_proc = proc
            freq_msg = ""
            try:
                import socket
                host, port = self._hamlib_endpoint()
                sock = socket.create_connection((host, port), timeout=1)
                sock.sendall(b"f\n")
                raw  = sock.recv(64).decode(errors="replace").strip()
                sock.close()
                freq_hz  = float(raw.split()[0])
                freq_msg = f" — Frequenz: {freq_hz/1e6:.6f} MHz"
            except Exception:
                pass
            msg = (f"Konfiguration gespeichert, rigctld neu gestartet "
                   f"(Modell {rig_model}, {device}, {baud} Bd){freq_msg}.")
            log.vital("[rigctld] %s", msg)
            self._publish_log("INFO", msg)
            return web.json_response({"ok": True, "message": msg})
        except RuntimeError as exc:
            msg = f"Konfiguration gespeichert, rigctld-Start fehlgeschlagen: {exc}"
            log.warning("[rigctld] %s", msg)
            return web.json_response({"ok": False, "message": msg})

    # ── SESSION-RECORDER (Stresstest-Auswertung) ──────────────────────
    # Auth: läuft wie alle /api/-Routen über _auth_middleware (api_key).

    async def _handle_session_start(self, _request: web.Request) -> web.Response:
        """POST /api/session/start — Session starten."""
        if self._session_state == "recording":
            return web.json_response({"ok": False, "error": "Session läuft bereits"})
        self._session_state    = "recording"
        self._session_frames   = []
        self._session_start_ts = time.time()
        self._session_stop_ts  = None
        self._session_report   = None
        log.info("Session-Recorder gestartet.")
        self._publish_log("INFO", "Session-Recorder gestartet — Frames werden aufgezeichnet.")
        return web.json_response({"ok": True, "started_at": self._session_start_ts})

    async def _handle_session_stop(self, _request: web.Request) -> web.Response:
        """POST /api/session/stop — Session stoppen."""
        if self._session_state != "recording":
            return web.json_response({"ok": False, "error": "Keine aktive Session"})
        self._session_state   = "done"
        self._session_stop_ts = time.time()
        n = len(self._session_frames)
        log.info("Session-Recorder gestoppt. %d Frames aufgezeichnet.", n)
        self._publish_log("INFO", f"Session-Recorder gestoppt — {n} Frames aufgezeichnet.")
        return web.json_response({
            "ok":          True,
            "frame_count": n,
            "stopped_at":  self._session_stop_ts,
            "duration_s":  round(self._session_stop_ts - self._session_start_ts, 1),
        })

    async def _handle_session_status(self, _request: web.Request) -> web.Response:
        """GET /api/session/status — aktueller Zustand (inkl. Report falls vorhanden)."""
        return web.json_response({
            "state":       self._session_state,
            "frame_count": len(self._session_frames),
            "started_at":  self._session_start_ts,
            "stopped_at":  self._session_stop_ts,
            "has_report":  self._session_report is not None,
            "report":      self._session_report,
        })

    async def _handle_session_evaluate(self, request: web.Request) -> web.Response:
        """
        POST /api/session/evaluate
        multipart/form-data: Feld "csv" = Ground-Truth-CSV von gust_stresstest.py
        Führt match_live_session() durch und speichert den Report.
        """
        if self._session_state not in ("done", "recording"):
            return web.json_response(
                {"ok": False,
                 "error": "Keine Session vorhanden (zuerst starten und stoppen)"})
        if not self._session_frames:
            return web.json_response(
                {"ok": False, "error": "Session enthält keine Frames"})

        # CSV aus Upload lesen
        try:
            reader = await request.multipart()
            csv_bytes = None
            async for field in reader:
                if field.name == "csv":
                    csv_bytes = await field.read(decode=True)
                    break
            if csv_bytes is None:
                return web.json_response(
                    {"ok": False, "error": "Kein CSV-Feld im Upload"})
        except Exception as e:
            return web.json_response(
                {"ok": False, "error": f"Upload-Fehler: {e}"})

        # CSV temporär in Datei schreiben (load_csv_log erwartet Pfad)
        import tempfile, os
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".csv", prefix="gust_session_")
        try:
            with os.fdopen(tmp_fd, "wb") as fh:
                fh.write(csv_bytes)
            # Auswertung
            from gust_stress_decode import match_live_session
            report = match_live_session(self._session_frames, tmp_path)
            # Nur JSON-serialisierbare Felder übernehmen (matched enthält
            # (found, expected)-Tupel — für die Anzeige nicht nötig)
            report_json = {
                "n_expected":   report["n_expected"],
                "n_found":      report["n_found"],
                "n_matched":    report["n_matched"],
                "n_missed":     report["n_missed"],
                "n_extra":      report["n_extra"],
                "n_dual_bonus": report["n_dual_bonus"],
                "decode_rate":  round(report["decode_rate"] * 100.0, 1),
                "pass":         report["decode_rate"] >= 0.80,
                "missed":       report["missed"],
                "extra":        [
                    {"channel": f["channel"], "frame_type": f["frame_type"],
                     "callsign": f["callsign"], "start_s": round(f["start_s"], 2)}
                    for f in report["extra"]
                ],
                "session_duration_s": round(
                    (self._session_stop_ts or time.time()) - self._session_start_ts, 1
                ) if self._session_start_ts else None,
                "frame_count": len(self._session_frames),
            }
            self._session_report = report_json
            self._publish_log(
                "INFO" if report_json["pass"] else "WARNING",
                f"Session-Auswertung: {report_json['decode_rate']:.1f} % "
                f"({report_json['n_matched']}/{report_json['n_expected']} Frames) "
                f"{'✓ PASS' if report_json['pass'] else '✗ FAIL'}"
            )
            return web.json_response({"ok": True, "report": report_json})
        except Exception as e:
            log.exception("Session-Auswertung Fehler")
            return web.json_response({"ok": False, "error": str(e)})
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    async def _handle_session_download(self, request: web.Request) -> web.Response:
        """
        GET /api/session/download?format=json|csv
        Lädt die aufgezeichneten Session-Frames oder den Report herunter.
        format=json  → alle aufgezeichneten rx_frame-Dicts + Report (falls vorhanden)
        format=csv   → aufgezeichnete Frames als CSV (gleiche Felder wie Ergebnis-CSV)
        """
        if not self._session_frames:
            return web.json_response(
                {"ok": False, "error": "Keine Session-Daten vorhanden"})

        fmt = request.rel_url.query.get("format", "json")
        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")

        if fmt == "csv":
            import io
            import csv as csvmod
            buf = io.StringIO()
            # tx_start_s: physikalischer Sendezeitstempel (0.0 = alte Session)
            # deep:       True = Fund des Deep-Decoders (20s-Fenster)
            fields = ["nr", "ts", "start_s", "tx_start_s", "channel",
                      "frame_type", "callsign", "freq_offset_hz", "deep"]
            session_start = min(
                (f.get("ts", 0.0) for f in self._session_frames), default=0.0)
            w = csvmod.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for i, f in enumerate(self._session_frames, start=1):
                ch = f.get("channel")
                if ch is None:
                    ch = f.get("detected_channel", -1)
                w.writerow({
                    "nr":            i,
                    "ts":            round(f.get("ts", 0.0), 3),
                    "start_s":       round(float(f.get("ts", 0.0)) - session_start, 3),
                    "tx_start_s":    round(float(f.get("tx_start_s", 0.0)), 3),
                    "channel":       int(ch) if ch is not None else -1,
                    "frame_type":    f.get("type_name", ""),
                    "callsign":      f.get("from", ""),
                    "freq_offset_hz": 0.0,
                    "deep":          bool(f.get("deep", False)),
                })
            content = buf.getvalue().encode("utf-8")
            return web.Response(
                body=content,
                content_type="text/csv",
                headers={"Content-Disposition":
                         f'attachment; filename="gust_session_{ts_str}.csv"'},
            )
        else:
            payload = {
                "session_start": self._session_start_ts,
                "session_stop":  self._session_stop_ts,
                "frame_count":   len(self._session_frames),
                "report":        self._session_report,
                "frames":        self._session_frames,
            }
            content = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
            return web.Response(
                body=content,
                content_type="application/json",
                headers={"Content-Disposition":
                         f'attachment; filename="gust_session_{ts_str}.json"'},
            )

    async def _handle_tx_tune(self, _request: web.Request) -> web.Response:
        """POST /api/tx/tune — 1000-Hz-Sinuston mit PTT, fix 15 Sekunden."""
        if self._gateway is None:
            return web.json_response(
                {"ok": False, "error": "Kein TX-Gateway aktiv."})

        await self._stop_tune()

        async def _run():
            import numpy as np
            from gust_modulator import SAMPLE_RATE
            from gust_audio import build_ptt

            duration_s = 15.0
            freq_hz    = 1000.0
            t     = np.linspace(0, duration_s,
                                int(SAMPLE_RATE * duration_s),
                                endpoint=False, dtype=np.float32)
            audio = (0.8 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)

            audio_cfg = ((self._config.get("tx_audio")
                          or self._config.get("audio") or {})
                         if isinstance(self._config, dict) else {})
            # rigctld muss bereits laufen (Früh-Start in cmd_daemon).
            # ensure_rigctld_running() wird hier NICHT aufgerufen — auf Windows
            # akzeptiert rigctld nur eine TCP-Verbindung gleichzeitig; ein zweiter
            # Start würde einen Doppelprozess erzeugen wenn der Polling-Loop
            # gerade verbunden ist.
            # Fallback: falls rigctld doch nicht läuft, HamlibPTT-Connect schlägt
            # mit klarer Fehlermeldung fehl.
            from gust_audio import HamlibPTT
            loop = asyncio.get_running_loop()
            # HamlibPTT direkt instanziieren — rigctld läuft bereits,
            # build_ptt würde intern nochmals ensure_rigctld_running aufrufen
            # was auf Windows zu ConnectionRefused führt (nur 1 TCP-Conn erlaubt)
            # localhost → 127.0.0.1 (IPv4/IPv6-Konflikt Windows, siehe gust_knowledge §20)
            _h = audio_cfg.get('hamlib_host', 'localhost')
            host = "127.0.0.1" if _h in ("localhost", "127.0.0.1") else _h
            port = int(audio_cfg.get('hamlib_port', 4532))
            ptt  = HamlibPTT(host=host, port=port)
            try:
                import sounddevice as sd
                device = audio_cfg.get("device")

                def _play():
                    ptt.activate()
                    try:
                        sd.play(audio, samplerate=SAMPLE_RATE,
                                device=device, blocking=True)
                    finally:
                        ptt.release()

                await loop.run_in_executor(None, _play)
            except asyncio.CancelledError:
                try:
                    ptt.release()
                except Exception:
                    pass
            except Exception as exc:
                log.warning("[Tune] Fehler: %s", exc)
                try:
                    ptt.release()
                except Exception:
                    pass
            finally:
                self._tune_task = None

        self._tune_task = asyncio.create_task(_run())
        return web.json_response({"ok": True, "message": "Tune gestartet (15 s, 1000 Hz)"})

    async def _handle_tx_tune_stop(self, _request: web.Request) -> web.Response:
        """POST /api/tx/tune_stop — Tune sofort abbrechen, PTT lösen."""
        await self._stop_tune()
        return web.json_response({"ok": True, "message": "Tune gestoppt"})

    async def _stop_tune(self):
        """Tune-Task canceln und auf Beendigung warten."""
        if self._tune_task is not None and not self._tune_task.done():
            self._tune_task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._tune_task), timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        self._tune_task = None

    def _save_config_atomic(self) -> None:
        """
        gateway.json atomar speichern: tempfile → os.replace.

        Verhindert dass ein abgebrochener Schreibvorgang die Datei
        leer/halbfertig hinterlässt. Interne Felder mit Prefix '_'
        (z.B. _verbose) werden nicht persistiert.
        """
        import os, tempfile
        from pathlib import Path

        path = Path(self._config_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Nur persistierbare Felder filtern (keine "_internen")
        persisted = {k: v for k, v in self._config.items()
                     if not k.startswith("_")}

        # Temp-Datei im selben Verzeichnis (atomarer Rename)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".gateway.", suffix=".json.tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(persisted, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp_path, str(path))
        except Exception:
            try: os.unlink(tmp_path)
            except Exception: pass
            raise

    # ── WEBSOCKET HANDLER ─────────────────────────────────────────────

    async def _handle_ws_rx(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket /ws/rx — sendet RX-Frames und Status-Updates."""
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._ws_rx_clients.add(ws)
        log.debug("WS /ws/rx — neuer Client, gesamt: %d", len(self._ws_rx_clients))

        # Initial-Status senden (Client könnte sofort trennen)
        try:
            home_ch = _callsign_to_channel(self._callsign)
            await ws.send_json({"type": "status", "data": {
                "callsign": self._callsign, "home_channel": home_ch,
                "tx_available": self._gateway is not None,
            }})
        except Exception:
            self._ws_rx_clients.discard(ws)
            return ws

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        m = json.loads(msg.data)
                        if m.get("type") == "pong":
                            pass   # Heartbeat-Antwort — kein Log-Spam
                    except Exception:
                        pass
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            self._ws_rx_clients.discard(ws)
            log.debug("WS /ws/rx — Client getrennt, verbleibend: %d",
                      len(self._ws_rx_clients))
        return ws

    async def _handle_ws_log(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket /ws/log — sendet Systemlog-Zeilen."""
        ws  = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._ws_log_clients.add(ws)
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._log_queues.add(q)
        log.debug("WS /ws/log — neuer Client")

        try:
            while not ws.closed:
                # ── Auf nächsten Log-Eintrag warten ──────────────────
                try:
                    entry = await asyncio.wait_for(q.get(), timeout=25.0)
                except (asyncio.TimeoutError, TimeoutError):
                    # Kein Log-Eintrag in 25 s → Keepalive-Ping senden
                    if ws.closed:
                        break
                    try:
                        await ws.send_json({"type": "ping"})
                    except Exception:
                        break   # Verbindung tot, Loop beenden
                    continue
                except asyncio.CancelledError:
                    break

                # ── Eintrag an Client senden ──────────────────────────
                if ws.closed:
                    break
                try:
                    await ws.send_json(entry)
                except Exception:
                    break   # Verbindung während dem Senden getrennt
        finally:
            self._log_queues.discard(q)
            self._ws_log_clients.discard(ws)
            log.debug("WS /ws/log — Client getrennt")
        return ws

    # ── BROADCAST HELPERS ─────────────────────────────────────────────

    async def broadcast_rx_frame(self, frame: dict) -> None:
        """
        RX-Frame an alle verbundenen /ws/rx-Clients senden.
        Wird vom Event-Bus-Reader aufgerufen (intern) oder
        kann direkt von Integrationscode aufgerufen werden.
        """
        self._rx_history.append(frame)   # History für /api/log
        self._last_rx_ts = frame.get("ts", time.time())   # "Letzter RX" im Status
        msg = json.dumps({"type": "rx_frame", "data": frame}, cls=_BytesEncoder)
        dead = set()
        for ws in list(self._ws_rx_clients):
            if ws.closed:
                dead.add(ws)
                continue
            try:
                await ws.send_str(msg)
            except Exception:
                dead.add(ws)
        if dead:
            self._ws_rx_clients -= dead
            log.debug("broadcast_rx_frame: %d tote WS-Verbindung(en) entfernt.", len(dead))

    def _publish_log(self, level: str, message: str) -> None:
        """Log-Eintrag an alle /ws/log-Clients senden (non-blocking)."""
        entry = {"type": "log", "level": level, "msg": message,
                 "ts": time.time()}
        for q in list(self._log_queues):
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                pass   # Langsame Clients verlieren Einträge — kein Blocking

    # ── EVENT-BUS INTEGRATION ─────────────────────────────────────────

    async def _event_bus_reader(self) -> None:
        """
        Asyncio-Task: Abonniert den internen Event-Bus und verteilt
        Events an die WebSocket-Clients.

        Erwartetes Interface von event_bus:
            queue = event_bus.subscribe()      # -> asyncio.Queue
            event_bus.unsubscribe(queue)
        Event-Dict Felder: type, data, ts
        """
        queue = self._event_bus.subscribe()
        log.info("Web-Server: Event-Bus abonniert.")
        try:
            while True:
                event = await queue.get()
                etype = event.get("type")
                data  = event.get("data", {})

                if etype == "rx_frame":
                    # ts aus dem Event-Umschlag in data injizieren,
                    # damit frame.ts im JavaScript verfügbar ist.
                    import time as _time
                    if "ts" not in data:
                        data = {**data, "ts": event.get("ts", _time.time())}
                    # AUTH-Frames (0x50) gehören NICHT in den Live Feed —
                    # nur ins Systemlog. Das 🔑-Badge wird stattdessen
                    # retroaktiv am Daten-Frame gesetzt (frame_authenticated).
                    if data.get("type") == 0x50 or data.get("type_name") == "AUTH":
                        _kid = (data.get("payload_decoded") or {}).get("key_id", "?")
                        self._publish_log("INFO",
                            f"AUTH-Frame: {data.get('from','?')} KEY_ID={_kid}")
                    else:
                        await self.broadcast_rx_frame(data)
                        # Session-Recorder: Frame aufzeichnen wenn aktiv
                        if self._session_state == "recording":
                            self._session_frames.append(dict(data))
                        self._publish_log("INFO",
                            f"RX: {data.get('from','?')} [{data.get('type_name','?')}] "
                            f"Kanal {data.get('channel','?')}")

                elif etype == "frame_authenticated":
                    # Retroaktives 🔑-Signal: die GUI markiert den passenden
                    # bereits angezeigten Daten-Frame.
                    msg = json.dumps({"type": "frame_authenticated", "data": data},
                                     cls=_BytesEncoder)
                    for ws in list(self._ws_rx_clients):
                        if not ws.closed:
                            try:
                                await ws.send_str(msg)
                            except Exception:
                                pass
                    self._publish_log("INFO",
                        f"AUTH: {data.get('from','?')} "
                        f"REF_TYPE=0x{data.get('ref_type', 0):02X} "
                        f"KEY_ID={data.get('key_id','?')} verifiziert")

                elif etype == "tx_done":
                    msg = json.dumps({"type": "tx_done", "data": data})
                    for ws in list(self._ws_rx_clients):
                        try:
                            await ws.send_str(msg)
                        except Exception:
                            pass
                    self._publish_log("INFO",
                        f"TX abgeschlossen: {data.get('type_name','?')}")

                elif etype == "status":
                    msg = json.dumps({"type": "status", "data": data})
                    for ws in list(self._ws_rx_clients):
                        try:
                            await ws.send_str(msg)
                        except Exception:
                            pass

                elif etype == "rx_audio_level":
                    # Pegel-Updates an alle Monitor-Clients durchreichen.
                    # Hochfrequent (~4 Hz) — kein Log-Spam erzeugen.
                    msg = json.dumps({"type": "rx_audio_level", "data": data})
                    for ws in list(self._ws_rx_clients):
                        try:
                            await ws.send_str(msg)
                        except Exception:
                            pass

                elif etype == "log":
                    self._publish_log(
                        data.get("level", "INFO"),
                        data.get("msg", "")
                    )

        except asyncio.CancelledError:
            log.info("Web-Server: Event-Bus-Reader beendet.")
        finally:
            try:
                self._event_bus.unsubscribe(queue)
            except Exception:
                pass

    # ── HEARTBEAT ─────────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """
        Sendet alle 10 s einen Heartbeat-Event an alle verbundenen WS-Clients.

        Läuft als eigener asyncio-Task (gestartet in start()).
        Unabhängig vom Event-Bus — sendet direkt auf die WS-Sockets.
        Fällt der Daemon aus, stoppt dieser Task und der Client erkennt
        nach 20 s (2 verpasste HBs) den Ausfall.
        """
        while True:
            await asyncio.sleep(10)
            uptime = int(time.time() - self._start_time) if self._start_time else 0
            last_rx_ago = None
            if self._last_rx_ts is not None:
                last_rx_ago = int(time.time() - self._last_rx_ts)

            msg = json.dumps({
                "type":          "heartbeat",
                "ts":            time.time(),
                "uptime_s":      uptime,
                "last_rx_ago_s": last_rx_ago,
                "daemon_mode":   self._gateway is not None,
            })

            dead = []
            for ws in list(self._ws_rx_clients):
                try:
                    await ws.send_str(msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._ws_rx_clients.discard(ws)

    async def _handle_health(self, _request: web.Request) -> web.Response:
        """
        GET /api/health — Daemon-Erreichbarkeit (nicht Auth-geschützt).

        Liefert immer HTTP 200, solange der Prozess läuft.
        Geeignet für externe Monitoring-Checks (curl, Uptime-Kuma, etc.).
        """
        uptime = int(time.time() - self._start_time) if self._start_time else 0
        last_rx_ago = None
        if self._last_rx_ts is not None:
            last_rx_ago = int(time.time() - self._last_rx_ts)
        return web.json_response({
            "alive":          True,
            "uptime_s":       uptime,
            "last_rx_ago_s": last_rx_ago,
            "ws_clients":    len(self._ws_rx_clients),
            "daemon_mode":   self._gateway is not None,
            "callsign":      self._callsign,
        })


# ═══════════════════════════════════════════════════════════════════════
# STANDALONE TEST / DEVELOPMENT SERVER
# ═══════════════════════════════════════════════════════════════════════

async def _mock_rx_injector(server: WebServer) -> None:
    """
    Simuliert eingehende RX-Frames für Entwicklungszwecke.
    Wird nur im Standalone-Modus aktiv.
    """
    import random
    frame_types = [
        (0x01, "WEATHER",  lambda: {
            "temp_c": round(random.uniform(5, 35), 1),
            "humidity": random.randint(30, 95),
            "pressure_hpa": round(random.uniform(995, 1030), 1),
            "wind_kmh": random.randint(0, 60),
            "wind_dir": random.randint(0, 359),
            "rain_mmh": round(random.uniform(0, 5), 1),
            "uv_index": random.randint(0, 10),
        }),
        (0x02, "POSITION", lambda: {
            "lat": round(48.2082 + random.uniform(-0.05, 0.05), 5),
            "lon": round(16.3738 + random.uniform(-0.05, 0.05), 5),
            "alt_m": random.randint(150, 500),
            "speed_kmh": random.randint(0, 120),
            "heading": random.randint(0, 359),
        }),
        (0x40, "TEXT",     lambda: {
            "to": random.choice(["OE3GAS", "OE1XTU", "OE3GAT", "BCAST"]),
            "text": random.choice(["73 de OE3GAS", "Test OK", "QRM auf Kanal 3", "HB9 kann ich hören"]),
        }),
    ]
    callsigns = ["OE3GAS", "OE1XTU", "OE3GAT", "OE5RFP", "HB9XYZ"]
    while True:
        await asyncio.sleep(random.uniform(4, 12))
        ft, name, data_fn = random.choice(frame_types)
        ch = random.randint(0, 9)
        frame = {
            "frame_type": ft,
            "type_name":  name,
            "from":       random.choice(callsigns),
            "channel":    ch,
            "data":       data_fn(),
            "ts":         time.time(),
            "snr_db":     round(random.uniform(-5, 20), 1),
            "rs_errors":  random.randint(0, 4),
        }
        await server.broadcast_rx_frame(frame)
        log.info("Mock RX: %s von %s Kanal %d", name, frame["from"], ch)


async def _main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="GUST Web-Server")
    parser.add_argument("--host",     default="0.0.0.0")
    parser.add_argument("--port",     type=int, default=8080)
    parser.add_argument("--callsign", default="OE3GAS")
    parser.add_argument("--api-key",  default="")
    parser.add_argument("--mock-rx",  action="store_true",
                        help="Simulierte RX-Frames für Entwicklung")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s — %(message)s",
        datefmt="%H:%M:%S"
    )

    config = {
        "callsign": args.callsign,
        "web": {
            "host":    args.host,
            "port":    args.port,
            "api_key": args.api_key,
        },
        "audio": {"device": "USB Audio (simuliert)"},
        "ptt":   {"backend": "null"},
    }

    server = WebServer(config)
    await server.start()
    print(f"\n  GUST Dashboard →  http://{args.host}:{args.port}/\n")

    tasks = []
    if args.mock_rx:
        log.info("Mock-RX-Injektor aktiv (--mock-rx)")
        tasks.append(asyncio.create_task(_mock_rx_injector(server)))

    try:
        await asyncio.Event().wait()   # Läuft bis STRG+C
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for t in tasks:
            t.cancel()
        await server.stop()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\nServer beendet.")