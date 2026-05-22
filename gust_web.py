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
from collections import deque
from datetime import datetime, timezone
from typing import Optional, Set

from aiohttp import web, WSMsgType
import aiohttp

# ═══════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════

log = logging.getLogger("gust.web")


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
[data-theme="light"] .tx-prio-info,
[data-theme="light"] .ch-freq,
[data-theme="light"] .ch-time,
[data-theme="light"] .frame-row .ts,
[data-theme="light"] .log-line,
[data-theme="light"] .field-row .unit { font-weight: normal; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: 'Courier New', monospace;
       font-size: 13px; min-height: 100vh;
       transition: background .2s, color .2s; }

/* ── HEADER ── */
header { background: var(--bg2); border-bottom: 1px solid var(--border);
         padding: 10px 16px; display: flex; align-items: center; gap: 16px; }
header h1 { font-size: 16px; color: var(--accent); letter-spacing: 2px; flex: 1; }
header h1 span { color: var(--text2); font-size: 11px; font-weight: normal;
                  margin-left: 8px; letter-spacing: 0; }
#ws-indicator { width: 10px; height: 10px; border-radius: 50%;
                background: var(--text2); transition: background .3s; }
#ws-indicator.connected { background: var(--green); box-shadow: 0 0 6px var(--green); }
#ws-indicator.error     { background: var(--red);   box-shadow: 0 0 6px var(--red); }
#theme-btn { background: none; border: 1px solid var(--border); color: var(--text2);
             padding: 3px 8px; border-radius: 4px; cursor: pointer; font-size: 12px; }
#theme-btn:hover { border-color: var(--accent); color: var(--accent); }
#callsign-badge { background: var(--bg3); border: 1px solid var(--border);
                  padding: 3px 10px; border-radius: 12px; font-size: 12px;
                  color: var(--accent); font-weight: bold; }

/* ── TABS ── */
nav { background: var(--bg2); border-bottom: 1px solid var(--border); display: flex; gap: 2px; padding: 0 8px; }
nav button { background: none; border: none; color: var(--text2); padding: 10px 16px;
             cursor: pointer; font-family: inherit; font-size: 13px; border-bottom: 2px solid transparent; }
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
                        font-size: 11px; color: var(--text2); margin: 3px 0; }
#audio-meter .am-label { width: 38px; flex-shrink: 0; color: var(--text2); }
#audio-meter .am-bar   { flex: 1; height: 10px; background: var(--bg3);
                          border: 1px solid var(--border); border-radius: 3px;
                          overflow: hidden; position: relative; }
#audio-meter .am-fill  { height: 100%; background: var(--green);
                          width: 0%; transition: width .15s linear; }
#audio-meter .am-fill.warn { background: var(--orange); }
#audio-meter .am-fill.clip { background: var(--red); }
#audio-meter .am-val   { width: 70px; flex-shrink: 0; text-align: right;
                          font-family: 'Courier New', monospace;
                          color: var(--text); font-size: 11px; }
#audio-meter .am-hdr   { display: flex; justify-content: space-between;
                          align-items: center; font-size: 11px;
                          color: var(--text2); margin-bottom: 4px; }
#audio-meter .am-status { color: var(--text); font-weight: bold; }
#audio-meter.silent  .am-status { color: var(--text2); }
#audio-meter.ok      .am-status { color: var(--green); }
#audio-meter.weak    .am-status { color: var(--orange); }
#audio-meter.clip    .am-status { color: var(--red); }
#audio-meter.nosig   .am-status { color: var(--red); }

/* ── CHANNEL GRID ── */
#channel-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin-bottom: 16px; }
.ch-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
           padding: 8px 10px; cursor: default; transition: border-color .2s; }
.ch-card.home    { border-color: var(--accent); }
.ch-card.emerg-active { border: 2px solid #e24b4a !important; background: rgba(226,75,74,.06); }
.ch-card.active  { border-color: var(--green); background: rgba(63,185,80,.08); }
.ch-card .ch-num { font-size: 18px; font-weight: bold; color: var(--accent); }
.ch-card .ch-freq { font-size: 11px; color: var(--text2); margin-top: 2px; }
.ch-card .ch-last { font-size: 11px; color: var(--text); margin-top: 6px; min-height: 14px;
                    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ch-card .ch-time { font-size: 10px; color: var(--text2); }

/* ── FRAME FEED ── */
#rx-feed { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
           height: 320px; overflow-y: auto; padding: 8px; }
.frame-row { padding: 4px 6px; border-bottom: 1px solid var(--border); display: flex;
             gap: 10px; align-items: baseline; font-size: 12px; }
.frame-row:last-child { border-bottom: none; }
.frame-row .ts   { color: var(--text2); white-space: nowrap; }
.frame-row .ch   { color: var(--accent); width: 20px; text-align: center; }
.frame-row .from { color: var(--blue); font-weight: bold; width: 70px; }
.frame-row .type { color: var(--green); width: 90px; }
.frame-row .snr  { width: 58px; text-align: right; font-weight: bold; font-size: 11px; white-space: nowrap; }
.frame-row .off  { color: var(--text2); width: 52px; text-align: right; font-size: 11px; white-space: nowrap; }
.frame-row .data { color: var(--text); flex: 1; }
.frame-row.emergency .type { color: var(--red); font-weight: bold; }
.frame-row.emergency      { background: rgba(248,81,73,.08); }
.frame-row.testframe      { border-left: 3px solid #1f6feb; }
.test-pill { display:inline-block; background:#1f6feb; color:#fff !important;
  font-size:9px; font-weight:bold; padding:1px 6px; border-radius:3px;
  letter-spacing:.5px; white-space:nowrap; vertical-align:middle; flex-shrink:0; }
.frame-row { cursor: pointer; }
.frame-row:hover { background: rgba(255,255,255,.04); }

/* ── MODAL ── */
#frame-modal { display:none; position:fixed; inset:0; z-index:1000;
  background:rgba(0,0,0,.82); align-items:center; justify-content:center; }
#frame-modal.open { display:flex; }
#frame-modal-box { background:var(--bg3); border:2px solid var(--accent);
  border-radius:8px; min-width:360px; max-width:560px;
  width:90vw; max-height:80vh; overflow-y:auto; position:relative; }
#frame-modal-box h3 { font-size:12px; font-weight:bold; margin:0; padding:10px 40px 10px 16px;
  color:var(--bg); background:var(--accent); border-radius:5px 5px 0 0;
  letter-spacing:.5px; }
#frame-modal-body { padding:14px 16px 16px; }
#frame-modal-close { position:absolute; top:8px; right:12px; background:none;
  border:none; color:var(--bg); font-size:18px; cursor:pointer; line-height:1;
  opacity:.8; }
#frame-modal-close:hover { opacity:1; }
.modal-row { display:flex; gap:8px; padding:4px 0;
  border-bottom:1px solid var(--border); font-size:12px; }
.modal-row:last-child { border-bottom:none; }
.modal-key { color:var(--text2); width:140px; flex-shrink:0; }
.modal-val { color:var(--text); word-break:break-all; font-weight:bold; }
.modal-map { display:inline-block; margin-top:10px; font-size:11px;
  color:var(--accent); text-decoration:none; }
.modal-map:hover { text-decoration:underline; }
[data-theme="light"] #frame-modal-box { background:#f6f8fa; }

/* ── AUDIO TOGGLES ── */
.audio-toggles { display:flex; gap:16px; align-items:center;
  margin-bottom:8px; flex-wrap:wrap; }
.toggle-sw { display:flex; align-items:center; gap:7px;
  cursor:pointer; font-size:11px; color:var(--text2); user-select:none; }
.toggle-sw input[type=checkbox] { accent-color:var(--accent);
  width:14px; height:14px; cursor:pointer; }
.toggle-sw:hover { color:var(--text); }
.snr-hi  { color: #3fb950; }   /* > 15 dB  — stark */
.test-badge { background: #1f6feb; color: #fff; font-size: 9px; font-weight: bold;
  padding: 1px 5px; border-radius: 3px; letter-spacing: .5px; vertical-align: middle;
  margin-left: 4px; }
.snr-mid { color: #e3b341; }   /* 8–15 dB  — ok    */
.snr-lo  { color: #f85149; }   /* < 8 dB   — schwach */

/* ── SECTION TITLES ── */
h2 { font-size: 13px; color: var(--text2); text-transform: uppercase;
     letter-spacing: 1px; margin-bottom: 8px; margin-top: 16px; }
h2:first-child { margin-top: 0; }

/* ── TX FORMS ── */
.tx-form { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
           padding: 16px; max-width: 520px; }
.tx-form.hidden { display: none; }
.field-row { display: flex; gap: 8px; margin-bottom: 8px; align-items: center; flex-wrap: wrap; }
.field-row label { color: var(--text2); width: 120px; flex-shrink: 0; font-size: 12px; }
.field-row input, .field-row select {
  background: var(--bg3); border: 1px solid var(--border); color: var(--text);
  padding: 5px 8px; border-radius: 4px; font-family: inherit; font-size: 12px;
  flex: 1; min-width: 0; }
.field-row input:focus, .field-row select:focus {
  outline: none; border-color: var(--accent); }
.field-row .unit { color: var(--text2); font-size: 11px; white-space: nowrap; }
.btn { background: var(--accent); color: #000; border: none; padding: 7px 20px;
       border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 13px;
       font-weight: bold; margin-top: 8px; }
.btn:hover { filter: brightness(1.1); }
.btn.danger { background: var(--red); color: #fff; }
.btn.secondary { background: var(--bg3); color: var(--text); border: 1px solid var(--border); font-weight: normal; }
.btn.secondary:hover { border-color: var(--accent); }
#tx-result { margin-top: 8px; font-size: 12px; padding: 6px 10px; border-radius: 4px; display: none; }
#tx-result.ok  { background: rgba(63,185,80,.15); color: var(--green); }
#tx-result.err { background: rgba(248,81,73,.15); color: var(--red); }
/* ── TX GRUPPEN-SELEKTOR ── */
.tx-groups { margin-bottom: 14px; display: flex; flex-direction: column; gap: 8px; }
.tx-group-hdr { display: flex; align-items: center; gap: 7px; margin-bottom: 5px; }
.tx-prio-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.tx-prio-name { font-size: 10px; text-transform: uppercase; letter-spacing: 1.2px; font-weight: bold; }
.tx-prio-info { font-size: 11px; color: var(--text2); }
.tx-prio-info .cd { color: var(--text); font-weight: bold; }
.tx-btn-row { display: flex; gap: 6px; flex-wrap: wrap; }
.tx-btn { background: var(--bg3); border: 1px solid var(--border); color: var(--text2);
  padding: 5px 12px; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; }
.tx-btn:hover:not(:disabled) { color: var(--text); border-color: var(--text2); }
.tx-btn.active   { border-color: var(--accent); color: var(--accent); }
.tx-btn.p1-btn.active { border-color: var(--red);    color: var(--red); }
.tx-btn:disabled { opacity: .35; cursor: default; }
.tx-btn .coming  { font-size: 9px; color: var(--text2); margin-left: 3px;
  border: 1px solid var(--border); border-radius: 2px; padding: 0 3px; }
.p4-col { color: var(--green); }   .p4-dot { background: var(--green); }
.p3-col { color: var(--blue); }    .p3-dot { background: var(--blue); }
.p2-col { color: var(--orange); }  .p2-dot { background: var(--orange); }
.p1-col { color: var(--red); }     .p1-dot { background: var(--red); }

/* ── STATUS CARDS ── */
#status-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.stat-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px; padding: 12px 14px; }
.stat-card .key { color: var(--text2); font-size: 11px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }
.stat-card .val { color: var(--text); font-size: 16px; font-weight: bold; }
.stat-card .val.accent { color: var(--accent); }
.stat-card .val.green  { color: var(--green); }

/* ── LOG ── */
#log-feed { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
            height: 420px; overflow-y: auto; padding: 8px; font-size: 11px; }
.log-line { padding: 2px 4px; border-radius: 3px; }
.log-line.WARNING { color: #e3b341; }
.log-line.ERROR   { color: var(--red); }
.log-line.DEBUG   { color: var(--text2); }
.log-line.INFO    { color: var(--text); }
#log-controls { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }
#log-controls select { background: var(--bg3); border: 1px solid var(--border); color: var(--text);
  padding: 4px 8px; border-radius: 4px; font-family: inherit; font-size: 12px; }
#autoscroll-toggle { display: flex; align-items: center; gap: 6px; cursor: pointer; color: var(--text2); font-size: 12px; }
#autoscroll-toggle input { accent-color: var(--accent); }

/* ── SPLIT CARD (Heimatkanal / TX-Offset) ── */
.stat-card-split { display: flex; padding: 0; overflow: hidden; }
.split-half      { flex: 1; padding: 12px 12px; }
.split-divider   { width: 1px; background: var(--border); flex-shrink: 0; align-self: stretch; }
.split-sub       { font-size: 10px; color: var(--text2); margin-top: 2px; font-weight: normal; }

/* ── AUDIO-EINSTELLUNGEN ─────────────────────────────────────────── */
.audio-cfg-card { background: var(--bg2); border: 1px solid var(--border);
                  border-radius: 6px; padding: 14px 16px; max-width: 640px; }
.audio-cfg-card h3 { font-size: 12px; color: var(--text2);
                     text-transform: uppercase; letter-spacing: 1px;
                     margin-bottom: 10px; }
.audio-cfg-row { display: flex; gap: 10px; margin-bottom: 10px;
                 align-items: center; flex-wrap: wrap; }
.audio-cfg-row label { width: 130px; font-size: 12px; color: var(--text2);
                       flex-shrink: 0; }
.audio-cfg-row select { flex: 1; min-width: 240px; background: var(--bg3);
                        border: 1px solid var(--border); color: var(--text);
                        padding: 6px 10px; border-radius: 4px;
                        font-family: inherit; font-size: 12px; }
.audio-cfg-row select:focus { outline: none; border-color: var(--accent); }
.audio-cfg-note { font-size: 11px; color: var(--text2); margin: 6px 0 12px;
                  padding: 8px 10px; background: var(--bg3);
                  border-left: 2px solid var(--accent); border-radius: 3px; }
.audio-cfg-actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
#audio-cfg-result { margin-top: 10px; font-size: 12px; padding: 8px 10px;
                    border-radius: 4px; display: none; }
#audio-cfg-result.ok  { background: rgba(63,185,80,.12);  color: var(--green); }
#audio-cfg-result.err { background: rgba(248,81,73,.12);  color: var(--red); }
#audio-cfg-result.warn{ background: rgba(255,166,87,.12); color: var(--orange); }

/* ── MOBILE / RESPONSIVE ─────────────────────────────────────────── */
@media (max-width: 640px) {
  header { padding: 8px 10px; gap: 8px; flex-wrap: wrap; }
  header h1 span { display: none; }                /* Untertitel auf kleinen Screens weglassen */
  main { padding: 10px 8px; }
  nav { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  nav button { padding: 8px 10px; font-size: 11px; white-space: nowrap; }
  #channel-grid { grid-template-columns: repeat(2, 1fr); }  /* 5→2 Spalten */
  #status-grid  { grid-template-columns: repeat(2, 1fr); }  /* 3→2 Spalten */
  .stat-card-split { flex-direction: column; }               /* Split-Card untereinander */
  .split-divider   { width: auto; height: 1px; }
  .field-row label { width: 80px; font-size: 11px; }
  .tx-form { max-width: 100%; }
  .tx-group-hdr { flex-wrap: wrap; }
  .tx-prio-info { width: 100%; padding-left: 15px; margin-top: 2px; }
  #rx-feed  { height: 200px; }
  #log-feed { height: 260px; }
}
</style>
</head>
<body>

<header>
  <h1>GUST <span>Generic Universal Shortwave Telemetry</span></h1>
  <span id="callsign-badge">–</span>
  <span id="ws-indicator" title="WebSocket Status"></span>
  <button id="theme-btn" onclick="toggleTheme()" title="Theme wechseln">🌙 Light</button>
</header>

<nav>
  <button class="active" onclick="switchTab('monitor',this)">📡 Monitor</button>
  <button onclick="switchTab('tx',this)">📤 Senden</button>
  <button onclick="switchTab('audio',this)">🎛 Audio</button>
  <button onclick="switchTab('status',this)">📊 Status</button>
  <button onclick="switchTab('log',this)">🗒 Log</button>
</nav>

<main>

<!-- ══════════════════════════════════════════════════════ TAB: MONITOR -->
<div id="tab-monitor" class="tab-panel active">
  <h2>Audio-Eingang (RX)</h2>
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

  <h2>Kanalübersicht — 10 Kanäle (400–2900 Hz NF)</h2>
  <div id="channel-grid"></div>

  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
    <h2 style="margin:0;">Live RX-Feed</h2>
    <label id="autoscroll-toggle"><input type="checkbox" id="autoscroll" checked> Auto-Scroll</label>
  </div>
  <div class="audio-toggles">
    <label class="toggle-sw">
      <input type="checkbox" id="toggle-audio-emerg">
      🔔 Ton bei Emergency
    </label>
    <label class="toggle-sw">
      <input type="checkbox" id="toggle-audio-mine">
      🔔 Ton bei Nachricht für mich
    </label>
  </div>
  <div id="rx-feed">
    <div style="color:var(--text2);padding:8px;">Warte auf RX-Frames …</div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════ TAB: SENDEN -->
<div id="tab-tx" class="tab-panel">
  <h2>One-Shot TX</h2>
<div class="tx-groups">

  <div class="tx-group">
    <div class="tx-group-hdr">
      <span class="tx-prio-dot p4-dot"></span>
      <span class="tx-prio-name p4-col">Telemetrie</span>
      <span class="tx-prio-info">P4 · zyklisch alle <span id="p4-interval">5 min</span> — nächste Sendung in <span class="cd" id="p4-next">–</span></span>
    </div>
    <div class="tx-btn-row">
      <button class="tx-btn active" onclick="selectTxType('weather',this)">🌤 Wetter</button>
      <button class="tx-btn" disabled>📟 Station <span class="coming">bald</span></button>
      <button class="tx-btn" disabled>🌡 Sensor <span class="coming">bald</span></button>
    </div>
  </div>

  <div class="tx-group">
    <div class="tx-group-hdr">
      <span class="tx-prio-dot p3-dot"></span>
      <span class="tx-prio-name p3-col">Navigation</span>
      <span class="tx-prio-info">P3 · nächster Zyklus in <span class="cd" id="p3-next">–</span></span>
    </div>
    <div class="tx-btn-row">
      <button class="tx-btn" onclick="selectTxType('position',this)">📍 Position</button>
      <button class="tx-btn" disabled>🔄 Rotor-Status <span class="coming">bald</span></button>
    </div>
  </div>

  <div class="tx-group">
    <div class="tx-group-hdr">
      <span class="tx-prio-dot p2-dot"></span>
      <span class="tx-prio-name p2-col">Kommunikation</span>
      <span class="tx-prio-info">P2 · Sendung ≤ 30 s nach Einreihung</span>
    </div>
    <div class="tx-btn-row">
      <button class="tx-btn" onclick="selectTxType('text',this)">💬 Freitext</button>
      <button class="tx-btn" disabled>📻 CQ-Anruf <span class="coming">bald</span></button>
    </div>
  </div>

  <div class="tx-group">
    <div class="tx-group-hdr">
      <span class="tx-prio-dot p1-dot"></span>
      <span class="tx-prio-name p1-col">Notfall</span>
      <span class="tx-prio-info">P1 · sofort — überspringt Cooldown</span>
    </div>
    <div class="tx-btn-row">
      <button class="tx-btn p1-btn" onclick="selectTxType('emergency',this)">🆘 Notfall-Beacon</button>
      <button class="tx-btn" disabled>⚕ Ressourcen <span class="coming">bald</span></button>
    </div>
  </div>

</div>

  <!-- Wetter-Formular -->
  <div id="form-weather" class="tx-form">
    <div class="field-row"><label>Temperatur</label>
      <input type="number" id="w-temp" value="20.0" step="0.1"><span class="unit">°C</span></div>
    <div class="field-row"><label>Luftfeuchte</label>
      <input type="number" id="w-hum" value="65" min="0" max="100"><span class="unit">%</span></div>
    <div class="field-row"><label>Luftdruck</label>
      <input type="number" id="w-pres" value="1013.2" step="0.1"><span class="unit">hPa</span></div>
    <div class="field-row"><label>Windgeschw.</label>
      <input type="number" id="w-wind" value="15" min="0"><span class="unit">km/h</span></div>
    <div class="field-row"><label>Windrichtung</label>
      <input type="number" id="w-wdir" value="270" min="0" max="359"><span class="unit">°</span></div>
    <div class="field-row"><label>Niederschlag</label>
      <input type="number" id="w-rain" value="0.0" step="0.1"><span class="unit">mm/h</span></div>
    <div class="field-row"><label>UV-Index</label>
      <input type="number" id="w-uv" value="3" min="0" max="15"></div>
    <button class="btn" onclick="sendTx('weather')">Wetter senden</button>
  </div>

  <!-- Position-Formular -->
  <div id="form-position" class="tx-form hidden">
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
      <select id="p-mobile"><option value="0">Nein (Bake)</option><option value="1">Ja (mobil)</option></select></div>
    <button class="btn" onclick="sendTx('position')">Position senden</button>
  </div>

  <!-- Text-Formular -->
  <div id="form-text" class="tx-form hidden">
    <div class="field-row"><label>An (Rufzeichen)</label>
      <input type="text" id="t-to" value="OE3GAT" maxlength="6" style="text-transform:uppercase"></div>
    <div class="field-row"><label>Nachricht</label>
      <input type="text" id="t-msg" value="OE3GAS de OE3GAS, Test 73" maxlength="80"></div>
    <button class="btn" onclick="sendTx('text')">Text senden</button>
  </div>

  <!-- Notfall-Formular -->
  <div id="form-emergency" class="tx-form hidden">
    <div style="background:rgba(248,81,73,.12);border:1px solid var(--red);border-radius:4px;
                padding:8px;margin-bottom:12px;color:var(--red);font-size:12px;">
      ⚠ Notfall-Frames erhalten Priorität 1 — sofortige Übertragung ohne Cooldown
    </div>
    <div class="field-row"><label>Latitude</label>
      <input type="number" id="e-lat" value="48.2082" step="0.0001"><span class="unit">°</span></div>
    <div class="field-row"><label>Longitude</label>
      <input type="number" id="e-lon" value="16.3738" step="0.0001"><span class="unit">°</span></div>
    <div class="field-row"><label>Personen</label>
      <input type="number" id="e-persons" value="1" min="1"></div>
    <div class="field-row"><label>Verletzung</label>
      <select id="e-injury">
        <option value="0">Unbekannt</option><option value="1">Leicht</option>
        <option value="2">Schwer</option><option value="3">Kritisch</option>
      </select></div>
    <div class="field-row"><label>Priorität</label>
      <select id="e-prio">
        <option value="1">Mittel</option><option value="2">Hoch</option>
        <option value="3" selected>Sofort</option>
      </select></div>
    <div class="field-row"><label>Kurztext (4 Z.)</label>
      <input type="text" id="e-text" value="HELP" maxlength="4" style="text-transform:uppercase"></div>
    <button class="btn danger" onclick="sendTx('emergency')">🆘 NOTFALL senden</button>
  </div>

  <div id="tx-result"></div>
</div>

<!-- ══════════════════════════════════════════════════════ TAB: AUDIO -->
<div id="tab-audio" class="tab-panel">
  <h2>Audio-Geräte</h2>
  <div class="audio-cfg-card">
    <h3>Auswahl Eingang (RX) und Ausgang (TX)</h3>

    <div class="audio-cfg-row">
      <label>RX-Eingang</label>
      <select id="ac-rx-device"><option value="">– Standard / wie TX –</option></select>
    </div>

    <div class="audio-cfg-row">
      <label>TX-Ausgang</label>
      <select id="ac-tx-device"><option value="">– Standard –</option></select>
    </div>

    <div class="audio-cfg-note">
      Änderungen werden in <code>gateway.json</code> gespeichert.
      <b>TX-Wechsel</b> wirkt sofort beim nächsten Sendevorgang.
      <b>RX-Wechsel</b> erfordert einen Neustart des Daemons —
      der RX-Loop hält das Gerät beim Start fest.
    </div>

    <div class="audio-cfg-actions">
      <button class="btn" onclick="saveAudioConfig()">💾 Speichern</button>
      <button class="btn secondary" onclick="loadAudioConfig()">↻ Neu laden</button>
    </div>

    <div id="audio-cfg-result"></div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════ TAB: STATUS -->
<div id="tab-status" class="tab-panel">
  <h2>System-Status</h2>
  <div id="status-grid">
    <div class="stat-card"><div class="key">Rufzeichen</div><div class="val accent" id="s-call">–</div></div>
    <div class="stat-card stat-card-split">
      <div class="split-half">
        <div class="key">Heimatkanal</div>
        <div class="val accent" id="s-ch">–</div>
      </div>
      <div class="split-divider"></div>
      <div class="split-half">
        <div class="key">TX-Offset</div>
        <div class="val accent" id="s-ch-offset">–</div>
        <div class="split-sub" id="s-ch-cycle"></div>
      </div>
    </div>
    <div class="stat-card"><div class="key">Uptime</div><div class="val" id="s-uptime">–</div></div>
    <div class="stat-card"><div class="key">TX-Queue</div><div class="val" id="s-queue">–</div></div>
    <div class="stat-card"><div class="key">Letzter TX</div><div class="val" id="s-last-tx">–</div></div>
    <div class="stat-card"><div class="key">Letzter RX</div><div class="val green" id="s-last-rx">–</div></div>
    <div class="stat-card"><div class="key">Audio-Gerät</div><div class="val" id="s-audio">–</div></div>
    <div class="stat-card"><div class="key">PTT-Backend</div><div class="val" id="s-ptt">–</div></div>
    <div class="stat-card"><div class="key">RX-Frames (Session)</div><div class="val green" id="s-rx-count">0</div></div>
  </div>
  <div style="margin-top:16px;">
    <button class="btn secondary" onclick="loadStatus()">↻ Aktualisieren</button>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════ TAB: LOG -->
<div id="tab-log" class="tab-panel">
  <div id="log-controls">
    <span style="color:var(--text2);font-size:12px;">Ebene:</span>
    <select id="log-level-filter" onchange="filterLogLevel()">
      <option value="ALL">Alle</option>
      <option value="INFO">INFO+</option>
      <option value="WARNING">WARNING+</option>
      <option value="ERROR">ERROR</option>
    </select>
    <label id="autoscroll-toggle">
      <input type="checkbox" id="log-autoscroll" checked> Auto-Scroll
    </label>
    <button class="btn secondary" onclick="clearLog()">Leeren</button>
  </div>
  <div id="log-feed"></div>
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
  wsRetryTimer: null,
  txInterval: 300,    // Sendezyklus in Sekunden (aus /api/status)
  txOffset:   0,      // Zeitversatz dieses Rufzeichens innerhalb des Zyklus
};

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
}

// Beim Laden: gespeichertes Theme wiederherstellen
(function() {
  const saved = localStorage.getItem('gust-theme') || 'dark';
  applyTheme(saved);
})();

// ═══════════════════════════ TABS ═════════════════════════════
function switchTab(name, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
  if (name === 'status') loadStatus();
  if (name === 'audio')  loadAudioConfig();
}

// ═══════════════════════════ TX FORMS ═════════════════════════
function selectTxType(type, btn) {
  document.querySelectorAll('.tx-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tx-form').forEach(f => f.classList.add('hidden'));
  btn.classList.add('active');
  document.getElementById('form-' + type).classList.remove('hidden');
}

async function sendTx(type) {
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
    payload = {
      to:   document.getElementById('t-to').value.toUpperCase(),
      text: document.getElementById('t-msg').value,
    };
  } else if (type === 'emergency') {
    payload = {
      lat:       parseFloat(document.getElementById('e-lat').value),
      lon:       parseFloat(document.getElementById('e-lon').value),
      persons:   parseInt(document.getElementById('e-persons').value),
      injury:    parseInt(document.getElementById('e-injury').value),
      priority:  parseInt(document.getElementById('e-prio').value),
      text_snippet: document.getElementById('e-text').value.toUpperCase().padEnd(4,' ').slice(0,4),
    };
  }

  const el = document.getElementById('tx-result');
  el.style.display = 'none';
  try {
    const r = await apiFetch('/api/tx/' + type, { method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    el.className = 'ok'; el.style.display = 'block';
    el.textContent = '✓ ' + (r.message || 'Frame eingereiht');
  } catch(e) {
    el.className = 'err'; el.style.display = 'block';
    el.textContent = '✗ Fehler: ' + e.message;
  }
  setTimeout(() => el.style.display = 'none', 4000);
}

// ═══════════════════════════ STATUS ═══════════════════════════
async function loadStatus() {
  try {
    const s = await apiFetch('/api/status');
    document.getElementById('s-call').textContent = s.callsign || '–';
    document.getElementById('s-ch').textContent   = 'Kanal ' + (s.home_channel ?? '–');
    if (s.tx_time_offset_s != null && s.tx_interval_s) {
      const off  = s.tx_time_offset_s;
      const itvl = s.tx_interval_s;
      const om   = Math.floor(off / 60), os = off % 60;
      const im   = Math.floor(itvl / 60);
      document.getElementById('s-ch-offset').textContent =
        om > 0 ? `+${om}m ${String(os).padStart(2,'0')}s` : `+${os}s`;
      document.getElementById('s-ch-cycle').textContent  =
        `Zyklus: ${im} min`;
    }
    document.getElementById('s-uptime').textContent = formatUptime(s.uptime_s);
    document.getElementById('s-queue').textContent  = s.queue_depth ?? '–';
    document.getElementById('s-last-tx').textContent = s.last_tx ? fmtTs(s.last_tx) : '–';
    document.getElementById('s-last-rx').textContent = s.last_rx ? fmtTs(s.last_rx) : '–';
    document.getElementById('s-audio').textContent  = s.audio_device || '–';
    document.getElementById('s-ptt').textContent    = s.ptt_backend  || '–';
    document.getElementById('s-rx-count').textContent = state.rxCount;
    applyStatusPush(s);   // Interval + Offset für Countdown übernehmen
  } catch(e) { /* ignore */ }
}

// ═══════════════════════════ AUDIO CONFIG ═════════════════════
// Lädt verfügbare Audio-Geräte + aktuelle Auswahl in die Dropdowns.
// "Standard" = leerer Wert = sounddevice-Default.
// RX = leerer Wert bedeutet "wie TX" (gust_rx.py fällt automatisch zurück).
async function loadAudioConfig() {
  const result = document.getElementById('audio-cfg-result');
  result.style.display = 'none';
  try {
    const [devs, cur] = await Promise.all([
      apiFetch('/api/audio/devices'),
      apiFetch('/api/audio/config'),
    ]);

    function _fill(selectId, items, currentId, placeholder) {
      const sel = document.getElementById(selectId);
      sel.innerHTML = `<option value="">${placeholder}</option>`;
      for (const d of items) {
        const opt = document.createElement('option');
        opt.value = String(d.id);
        const def = d.is_default ? ' ★' : '';
        opt.textContent = `[${d.id}] ${d.name} (${d.channels}ch)${def}`;
        // Markiere aktuell konfiguriertes Gerät
        if (currentId !== null && currentId !== undefined &&
            String(currentId) === String(d.id)) {
          opt.selected = true;
        }
        sel.appendChild(opt);
      }
    }

    _fill('ac-rx-device', devs.input,  cur.rx_device, '– Standard / wie TX –');
    _fill('ac-tx-device', devs.output, cur.tx_device, '– Standard –');

    if (!cur.writable) {
      result.className = 'warn';
      result.style.display = 'block';
      result.textContent = '⚠ Schreiben deaktiviert — kein config_path bekannt. ' +
                           'Auswahl kann nicht gespeichert werden.';
    }
  } catch (e) {
    result.className = 'err';
    result.style.display = 'block';
    result.textContent = '✗ Geräte konnten nicht geladen werden: ' + e.message;
  }
}

async function saveAudioConfig() {
  const rxSel = document.getElementById('ac-rx-device').value;
  const txSel = document.getElementById('ac-tx-device').value;
  // Leer = null (= Standard / wie TX bei RX)
  const body = {
    tx_device: txSel === '' ? null : parseInt(txSel, 10),
    rx_device: rxSel === '' ? null : parseInt(rxSel, 10),
  };

  const result = document.getElementById('audio-cfg-result');
  result.style.display = 'none';
  try {
    const r = await apiFetch('/api/audio/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    result.className = r.rx_restart_required ? 'warn' : 'ok';
    result.style.display = 'block';
    let txt = '✓ ' + (r.message || 'Gespeichert');
    if (r.rx_restart_required) {
      txt += '  —  ⚠ RX-Wechsel: bitte GUST-Daemon neu starten.';
    }
    result.textContent = txt;
  } catch (e) {
    result.className = 'err';
    result.style.display = 'block';
    result.textContent = '✗ Fehler: ' + e.message;
  }
}

// ═══════════════════════════ CHANNEL GRID ═════════════════════
function buildChannelGrid(homeChannel) {
  const grid = document.getElementById('channel-grid');
  const plan = [
    [0,'400–650'],[1,'650–900'],[2,'900–1150'],[3,'1150–1400'],[4,'1400–1650'],
    [5,'1650–1900'],[6,'1900–2150'],[7,'2150–2400'],[8,'2400–2650'],[9,'2650–2900']
  ];
  grid.innerHTML = plan.map(([ch, freq]) => `
    <div class="ch-card ${ch === homeChannel ? 'home' : ''}" id="ch-card-${ch}">
      <div class="ch-num">${ch}${ch === homeChannel ? ' ★' : ''}</div>
      <div class="ch-freq">${freq} Hz</div>
      <div class="ch-last" id="ch-last-${ch}">–</div>
      <div id="ch-test-${ch}" style="margin-top:3px;min-height:14px"></div>
      <div class="ch-snr"  id="ch-snr-${ch}"></div>
      <div class="ch-time" id="ch-time-${ch}"></div>
    </div>`).join('');
}

function snrClass(snr) {
  if (snr == null) return '';
  return snr > 15 ? 'snr-hi' : snr >= 8 ? 'snr-mid' : 'snr-lo';
}
function snrLabel(snr) {
  if (snr == null) return '';
  return (snr > 0 ? '+' : '') + snr.toFixed(1) + ' dB';
}

function updateChannelCard(ch, from, typeName, tsStr, snr, isEmerg, isTest) {
  const lastEl = document.getElementById('ch-last-' + ch);
  const snrEl  = document.getElementById('ch-snr-'  + ch);
  const timeEl = document.getElementById('ch-time-' + ch);
  const card   = document.getElementById('ch-card-' + ch);
  if (!lastEl) return;
  lastEl.textContent = from + ' · ' + typeName;
  const testEl = document.getElementById('ch-test-' + ch);
  if (testEl) testEl.innerHTML  = isTest ? '<span class="test-pill" style="font-size:10px">TEST</span>' : '';
  if (snrEl) {
    snrEl.textContent  = snr != null ? snrLabel(snr) : '';
    snrEl.className    = 'ch-snr ' + snrClass(snr);
  }
  timeEl.textContent = tsStr;
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
function appendRxFrame(frame) {
  state.rxCount++;
  document.getElementById('s-rx-count').textContent = state.rxCount;

  const feed = document.getElementById('rx-feed');
  // Leere Platzhalter entfernen
  const placeholder = feed.querySelector('[style*="color:var(--text2)"]');
  if (placeholder) placeholder.remove();

  const ts  = new Date(frame.ts * 1000).toLocaleTimeString('de-AT');
  const ch  = frame.channel ?? frame.detected_channel ?? '?';
  const frm = frame.from ?? '?';
  const typ = frame.type_name ?? '?';
  const dat = frameDataSummary(frame);
  const _ftype  = frame.frame_type ?? frame.type ?? 0;
  const isEmerg = (_ftype === 0x20 || _ftype === 0x21
               || frame.type_name === 'EMERG_BEACON'
               || frame.type_name === 'EMERG_RSRC');

  const snr  = frame.snr_db  ?? frame._snr_db  ?? null;
  const off  = frame.freq_offset_hz ?? frame.offset_hz ?? null;
  const offStr  = off  != null ? (off > 0 ? '+' : '') + off.toFixed(0) + ' Hz' : '';
  const snrCls  = snrClass(snr);

  const row = document.createElement('div');
  const isTest = !!frame.test;
  row.className = 'frame-row' + (isEmerg ? ' emergency' : '') + (isTest ? ' testframe' : '');
  row.innerHTML = `<span class="ts">${ts}</span>
    <span class="ch">${ch}</span>
    <span class="from">${frm}</span>
    <span style="display:flex;align-items:center;gap:5px;width:122px;flex-shrink:0"><span class="type" style="width:auto">${typ}</span>${isTest ? '<span class="test-pill">TEST</span>' : ''}</span>
    <span class="snr ${snrCls}">${snr != null ? snrLabel(snr) : '–'}</span>
    <span class="off">${offStr}</span>
    <span class="data">${dat}</span>`;
  row.addEventListener('click', () => openFrameModal(frame));
  feed.appendChild(row);

  // Maximal 100 Zeilen im DOM
  while (feed.children.length > 100) feed.removeChild(feed.firstChild);

  if (document.getElementById('autoscroll').checked)
    feed.scrollTop = feed.scrollHeight;

  updateChannelCard(ch, frm, typ, ts, snr, isEmerg, isTest);

  // Audio-Alerts
  if (isEmerg && document.getElementById('toggle-audio-emerg')?.checked)
    playAlarm();
  const myCall = state.callsign?.toUpperCase();
  if (myCall && (frame.type_name === 'TEXT') &&
      (frame.payload_decoded?.dest || frame.data?.dest || '')
        .toUpperCase() === myCall &&
      document.getElementById('toggle-audio-mine')?.checked)
    playNotify();
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
    const dest = d.dest || d.to || '?';
    return `→ ${dest}  "${d.text}"`;
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
    status = 'Stille — kein nennenswertes Signal';
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
document.addEventListener('keydown', e => { if(e.key==='Escape') closeModal(); });

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
    log2ui('INFO', 'WS /ws/rx verbunden');
  };
  state.wsRx.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === 'rx_frame')       appendRxFrame(msg.data);
      if (msg.type === 'status')         applyStatusPush(msg.data);
      if (msg.type === 'rx_audio_level') updateAudioMeter(msg.data);
      if (msg.type === 'tx_done')        log2ui('INFO', 'TX abgeschlossen: ' + (msg.data?.type_name||'?'));
      if (msg.type === 'ping')           state.wsRx.send(JSON.stringify({type:'pong'}));
    } catch(e) { /* ignore malformed */ }
  };
  state.wsRx.onerror = () => { ind.className = 'error'; };
  state.wsRx.onclose = () => {
    ind.className = '';
    log2ui('WARNING', 'WS /ws/rx getrennt — reconnect in 5 s');
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
    _tickTxCountdown();
  }
}

// ═══════════════════════════ TX COUNTDOWN ═════════════════════
// Berechnet Sekunden bis zum nächsten Sendezyklus (P4/P3).
// Der Zyklus ist deterministisch: offset = SHA256(rufzeichen) % interval.
// Beide Gruppen P4 und P3 teilen denselben Sendezyklus.
function _nextCycleSecs() {
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
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return `${h}h ${m}m ${sec}s`;
}

function fmtTs(ts) {
  if (!ts) return '–';
  return new Date(ts * 1000).toLocaleTimeString('de-AT');
}

// ═══════════════════════════ INIT ═════════════════════════════
(async function init() {
  try {
    const s = await apiFetch('/api/status');
    state.callsign   = s.callsign || '–';
    state.homeChannel = s.home_channel ?? null;
    document.getElementById('callsign-badge').textContent = state.callsign;
    buildChannelGrid(state.homeChannel);
    // Frame-History nachladen
    const hist = await apiFetch('/api/log');
    if (Array.isArray(hist.frames)) hist.frames.forEach(appendRxFrame);
  } catch(e) {
    buildChannelGrid(null);
    log2ui('WARNING', 'Status-API nicht erreichbar: ' + e.message);
  }
  connectWsRx();
  connectWsLog();
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

</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════════════
# WEB-SERVER KLASSE
# ═══════════════════════════════════════════════════════════════════════

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
        # Serialisiert konkurrierende Schreibvorgänge auf gateway.json
        self._config_write_lock = asyncio.Lock()

        self._event_bus = event_bus
        self._gateway   = gateway

        self._start_time: Optional[float] = None

        # RX-Frame-History (letzte 50 Frames für /api/log)
        self._rx_history: deque = deque(maxlen=50)

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
        log.info("GUST Web-Server gestartet: http://%s:%d", self._host, self._port)

        # Event-Bus Subscriber starten (falls vorhanden)
        if self._event_bus is not None:
            self._eb_task = asyncio.create_task(
                self._event_bus_reader(), name="web_eb_reader"
            )

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
        app.router.add_get("/api/status",    self._handle_status)
        app.router.add_get("/api/config",    self._handle_config)
        app.router.add_get("/api/log",       self._handle_log)
        app.router.add_post("/api/tx/weather",   self._handle_tx_weather)
        app.router.add_post("/api/tx/position",  self._handle_tx_position)
        app.router.add_post("/api/tx/text",      self._handle_tx_text)
        app.router.add_post("/api/tx/emergency", self._handle_tx_emergency)
        app.router.add_get ("/api/audio/devices", self._handle_audio_devices)
        app.router.add_get ("/api/audio/config",  self._handle_audio_config_get)
        app.router.add_post("/api/audio/config",  self._handle_audio_config_post)
        app.router.add_get("/ws/rx",  self._handle_ws_rx)
        app.router.add_get("/ws/log", self._handle_ws_log)
        return app

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        """Bearer-Token / X-API-Key Prüfung für API- und WS-Endpunkte."""
        path = request.path
        is_protected = path.startswith("/api/") or path.startswith("/ws/")
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
            "audio_device":      self._config.get("audio", {}).get("device", "–"),
            "ptt_backend":       self._config.get("ptt", {}).get("backend", "null"),
            "tx_interval_s":     tx_interval,
            "tx_time_offset_s":  tx_offset,
            "version":           "0.1.0",
        }
        # Gateway-Status einmischen wenn verfügbar
        if self._gateway is not None:
            try:
                gw_status = self._gateway.get_status()
                status.update(gw_status)
            except Exception as exc:
                log.debug("Gateway.get_status() Fehler: %s", exc)
        return web.json_response(status)

    async def _handle_config(self, _request: web.Request) -> web.Response:
        """Aktuelle Konfiguration zurückgeben — API-Key wird ausgeblendet."""
        safe = {k: v for k, v in self._config.items()
                if k not in ("web",)}
        # web-Abschnitt ohne api_key
        web_safe = {k: v for k, v in self._config.get("web", {}).items()
                    if k != "api_key"}
        safe["web"] = web_safe
        return web.json_response(safe)

    async def _handle_log(self, _request: web.Request) -> web.Response:
        """Letzte 50 RX-Frames als JSON-Array."""
        return web.json_response({"frames": list(self._rx_history)})

    async def _handle_tx_weather(self, request: web.Request) -> web.Response:
        return await self._enqueue_tx(request, "weather", priority=4)

    async def _handle_tx_position(self, request: web.Request) -> web.Response:
        return await self._enqueue_tx(request, "position", priority=3)

    async def _handle_tx_text(self, request: web.Request) -> web.Response:
        return await self._enqueue_tx(request, "text", priority=2)

    async def _handle_tx_emergency(self, request: web.Request) -> web.Response:
        return await self._enqueue_tx(request, "emergency", priority=1)

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

        if self._gateway is not None:
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
        """Liste der verfügbaren Audiogeräte (Input + Output) via sounddevice."""
        try:
            import sounddevice as sd
            devs = sd.query_devices()
            default_in, default_out = sd.default.device
        except Exception as e:
            return web.json_response(
                {"error": f"sounddevice nicht verfügbar: {e}"},
                status=500,
            )

        inputs, outputs = [], []
        for i, d in enumerate(devs):
            entry = {
                "id":   i,
                "name": d.get("name", "?"),
                "host_api": int(d.get("hostapi", -1)),
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
        audio = self._config.get("audio", {}) or {}
        rx    = self._config.get("rx",    {}) or {}
        return web.json_response({
            "tx_device":   audio.get("device"),      # int / str / None
            "rx_device":   rx.get("device"),         # None = "wie TX"
            "ptt_backend": audio.get("ptt_backend", "null"),
            "level":       audio.get("level"),
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
            old_audio = dict(self._config.get("audio", {}))
            old_rx    = dict(self._config.get("rx",    {}))

            if tx_set:
                self._config.setdefault("audio", {})["device"] = new_tx
            if rx_set:
                self._config.setdefault("rx", {})["device"] = new_rx

            # In Datei schreiben (atomar)
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, self._save_config_atomic
                )
            except Exception as e:
                # Rollback im Speicher
                self._config["audio"] = old_audio
                self._config["rx"]    = old_rx
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
            "tx_device": self._config["audio"].get("device"),
            "rx_device": self._config.get("rx", {}).get("device"),
            "tx_restart_required": False,    # TX liest cfg live
            "rx_restart_required": rx_changed,
        })

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
                "callsign": self._callsign, "home_channel": home_ch
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
        msg = json.dumps({"type": "rx_frame", "data": frame})
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
                    await self.broadcast_rx_frame(data)
                    self._publish_log("INFO",
                        f"RX: {data.get('from','?')} [{data.get('type_name','?')}] "
                        f"Kanal {data.get('channel','?')}")

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