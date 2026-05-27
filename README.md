# GUST — Generic Universal Shortwave Telemetry

> *A terse HF broadcast protocol inspired by Olivia, optimized for sub-5-second transmissions.*

GUST is an open amateur radio digital protocol for robust one-way transmission of telemetry data and short messages on shortwave (HF). It serves as an **HF backbone** between local data sources (weather stations, LoRa/Meshtastic meshes, sensors) and remote receive-only stations — independent of internet infrastructure.

Developed by **OE3GAS** (Vienna, Austria) · Protocol **v0.5** (May 2026) · License: CC BY-SA 4.0

---

## Protocol at a Glance

| Parameter | Value |
|---|---|
| Modulation | MFSK-8 (8 tones, orthogonal) |
| Symbol rate | 31.25 Baud |
| Channel bandwidth | 250 Hz |
| Channels | 8 (NF 600–2600 Hz, SSB plateau ±0.5 dB) |
| SYNC | Costas array [2,0,6,7,1,4,3,5] (order 8, single autocorrelation peak) |
| Frame duration | 4.1–5.4 s (typ. 4.9 s for weather) |
| FEC | Reed-Solomon RS(255,223), shortened for short frames |
| CRC | CRC-16/CCITT-FALSE |
| Dial frequency | 14.110 MHz USB (20 m, all stations identical) |
| Channel assignment | SHA-256(callsign) % 8 — deterministic, no coordination |
| Input sources | Audio (SSB transceiver) · IQ (RTL-SDR, all 8 channels in parallel) |

### Frame Types

| Code | Type | Payload |
|---|---|---|
| `0x01` | Weather telemetry | Temp, humidity, pressure, wind, rain, UV |
| `0x02` | Position | Lat/Lon/Alt/Speed/Heading (APRS-compatible fields) |
| `0x03` | Station telemetry | Voltage, current, temp, CPU, uptime |
| `0x20` | Emergency beacon | Position + persons + injury + resources + priority |
| `0x40` | Free text / QSO fragment | Destination callsign + UTF-8 text |
| `0x41` | CQ / call | Callsign broadcast |

---

## Quickstart

**Requirements:** Python 3.9+, a PC soundcard connected to an SSB transceiver (or HackRF for lab tests).

```bash
git clone https://github.com/OE3GAS/gust.git
cd gust
pip install -r requirements.txt

# Copy and edit the config
cp gateway.json.example gateway.json

# List audio devices
python gust.py devices

# Monitor mode (RX only)
python gust.py rx --device <audio_device_index>

# Send a weather frame (simulation)
python gust.py tx weather --dry-run

# Full gateway daemon (TX + RX + Web UI)
python gust.py daemon
```

Web interface available at `http://localhost:8080` after starting daemon.

---

## Test Frequencies

Planned test operation with selected partner stations on the following HF digital sub-bands (IARU Region 1):

| Band | Segment (kHz) |
|------|--------------|
| 630 m | 475–479 |
| 160 m | 1838–1840 |
| 80 m | 3570–3600 |
| 40 m | 7040–7050 |
| 30 m | 10130–10150 |
| **20 m** | **14110–14125** ← active |
| 17 m | 18105–18109 |
| 15 m | 21090–21110 |
| 10 m | 28120–28150 |

Additional tests on VHF/UHF: 144.900 MHz (2 m), 432.200 MHz (70 cm).

---

## File Structure

```
gust.py               — CLI entry point (tx / rx / daemon / info / devices)
gust_frame.py         — Frame layer: encoding, CRC, RS-FEC, channel assignment
gust_modulator.py     — MFSK-8 modulator / demodulator (FFT-based)
gust_audio.py         — Audio I/O, PTT control (GPIO / hamlib / null)
gust_hackrf.py        — HackRF One TX path (SoapySDR)
gust_rx.py            — Continuous RX loop (asyncio, ring buffer, dedup cache)
gust_iq_rx.py         — IQ input path (RTL-SDR filterbank, all 8 channels)
gust_decode.py        — Standalone decoder helper
gust_eventbus.py      — asyncio fan-out event bus
gust_web.py           — aiohttp web server, REST API, WebSocket, web UI
gateway.json.example  — Configuration template
requirements.txt      — Python dependencies
gust_spec.md          — Full protocol specification
```

---

## Configuration

Copy `gateway.json.example` to `gateway.json` and adapt:

```json
{
  "callsign": "OE3GAS",
  "audio": {
    "device": 9,
    "ptt_backend": "hamlib",
    "level": 30,
    "hamlib_host": "localhost",
    "hamlib_port": 4532
  },
  "web": {
    "host": "0.0.0.0",
    "port": 8080,
    "api_key": ""
  }
}
```

`ptt_backend` options: `hamlib` · `gpio` · `null` (dry-run / lab)

---

## Dependencies

```
numpy          — Signal processing
scipy          — FFT, WAV I/O
reedsolo       — Reed-Solomon FEC
sounddevice    — Audio I/O
aiohttp        — Web server (daemon mode)
```

Optional (Raspberry Pi gateway):
```
RPi.GPIO       — PTT via GPIO
smbus2         — BME280 weather sensor
meshtastic     — Meshtastic/MeshCom integration
paho-mqtt      — MQTT input from WiFi sources
```

Install: `pip install -r requirements.txt`

---

## Status

| Phase | Description | Status |
|---|---|---|
| 1 | Modulator / Demodulator | ✅ Complete |
| 2 | Frame layer | ✅ Complete |
| 3 | Hardware integration | ✅ Complete |
| 4 | Source integration | ✅ Complete |
| 5 | Web interface & event bus | ✅ Complete |
| 6 | Connector layer + MQTT bridge | 🔲 Concept ready, implementation open |
| 7 | On-air tests & decoder robustness | ✅ Largely complete |
| 8 | Publication | 🚧 In progress |
| 9 | Protocol v0.5: Costas-SYNC · 8-channel plan · IQ input | ✅ Complete |

Decoder performance (HackRF → IC-7610 loopback): decode threshold ≤ 10 dB SNR, 100% frame recovery rate (20/20 simplex test, May 2026).

---

## License

© 2025–2026 OE3GAS  
[Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)](https://creativecommons.org/licenses/by-sa/4.0/)

You are free to share and adapt this work, provided you give appropriate credit and distribute any derivative works under the same license.

---

## Contributing / Contact

This is a personal amateur radio research project. Feedback, reports of successful reception, and suggestions are welcome via GitHub Issues.

73 de OE3GAS