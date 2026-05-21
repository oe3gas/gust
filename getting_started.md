# GUST — Getting Started

**Generic Universal Shortwave Telemetry** · [github.com/oe3gas/gust](https://github.com/oe3gas/gust)

> A terse HF broadcast protocol inspired by Olivia, optimized for sub-5-second transmissions.

---

## 1. Prerequisites

### Software

| What | Where | Notes |
|------|-------|-------|
| **Python 3.9** | [python.org](https://www.python.org/downloads/) | 3.9 required for PothosSDR bindings on Windows; 3.9+ on Linux/RPi |
| **PothosSDR** *(Windows, HackRF only)* | [github.com/pothosware/PothosSDR](https://github.com/pothosware/PothosSDR/releases) | Provides SoapySDR + HackRF drivers |
| **Git** | [git-scm.com](https://git-scm.com) | To clone the repo |

### Hardware (optional)

| Hardware | Purpose |
|----------|---------|
| SSB transceiver (e.g. IC-7610) | RX via USB audio, TX via audio line-in + PTT |
| HackRF One | Lab TX directly to transceiver (loopback test) |
| USB audio adapter | Any stereo soundcard connected to transceiver |

GUST runs fully without hardware using `--sim` or `--dry-run` mode.

---

## 2. Installation

```bash
# Clone
git clone https://github.com/oe3gas/gust.git
cd gust

# Install Python dependencies
pip install -r requirements.txt
# On Raspberry Pi: pip install -r requirements.txt --break-system-packages

# Copy and edit config
cp gateway.json.example gateway.json
```

Edit `gateway.json`:

```json
{
  "callsign": "OE0XYZ",
  "audio": {
    "device": 0,
    "ptt_backend": "null",
    "level": 30
  },
  "web": { "host": "0.0.0.0", "port": 8080, "api_key": "" }
}
```

Set `"callsign"` to your callsign. Leave `"device": 0` for now — you will find the correct number in step 3.

---

## 3. Quick Start — Simulation (no hardware needed)

Start the daemon with a built-in frame simulator:

```bash
python gust.py daemon --sim
```

Open your browser: **[http://localhost:8080](http://localhost:8080)**

The web dashboard shows simulated WEATHER, POSITION and TEXT frames rolling in every few minutes. All features (channel grid, frame detail popup, audio alerts) are fully functional.

To stop: **Ctrl+C**

---

## 4. Full Operation — RX + TX with Hardware

### Step 1 — Find your audio device number

```bash
python gust.py devices
```

Sample output:
```
  0  Microsoft Sound Mapper
  1  IC-7610 USB Audio CODEC   ← RX input from transceiver
  9  IC-7610 USB Audio CODEC   ← TX output to transceiver
 ...
```

Note the device numbers for RX (input from transceiver) and TX (output to transceiver).

### Step 2 — Configure gateway.json

```json
{
  "callsign": "OE0XYZ",
  "audio": {
    "device": 9,
    "ptt_backend": "hamlib",
    "level": 30,
    "hamlib_host": "localhost",
    "hamlib_port": 4532
  }
}
```

`ptt_backend` options:
- `"null"` — no PTT (monitor-only / dry-run)
- `"hamlib"` — PTT via Hamlib rigctld
- `"gpio"` — PTT via Raspberry Pi GPIO pin

### Step 3 — Start the daemon

```bash
# Windows (HackRF support)
$env:PYTHONPATH = "C:\Program Files\PothosSDR\lib\python3.9"
python gust.py daemon --device 1
```

```bash
# Linux / Raspberry Pi
python gust.py daemon --device 1
```

Replace `1` with your RX audio device number from Step 1.

Open your browser: **[http://localhost:8080](http://localhost:8080)**

The daemon starts the RX loop, web server, and event bus simultaneously. Decoded frames appear live in the dashboard.

### Step 4 — TX test (HackRF loopback)

In a second terminal — sends random frames via HackRF, received by your transceiver:

```bash
# Windows
$env:PYTHONPATH = "C:\Program Files\PothosSDR\lib\python3.9"
python gust_tx_test.py --min-gain 4 --max-gain 12 --pause 4
```

All frames from `gust_tx_test.py` are automatically tagged with the **TEST flag** (visible as a blue `TEST` badge in the dashboard). To send untagged frames: `--no-test`.

---

## 5. CLI — One-Shot Commands

```bash
# Show channel assignment for your callsign
python gust.py info

# Show channel assignment for another callsign
python gust.py info OE1XTU

# List available audio devices
python gust.py devices

# RX monitor only (no TX, no web UI)
python gust.py rx --device 1
```

### Send a single frame

```bash
# Weather frame (values from command line)
python gust.py tx weather --temp 22 --humidity 60 --pressure 1013 --wind 15 --wind-dir 270

# Position frame
python gust.py tx position --lat 48.21 --lon 16.37 --alt 180

# Free text to a specific callsign
python gust.py tx text "73 de OE0XYZ" OE1XTU

# Emergency beacon (requires --confirm for live TX)
python gust.py tx emergency --persons 2 --injury 1 --lat 48.21 --lon 16.37 --confirm

# Dry-run: build and display frame without transmitting
python gust.py tx weather --temp 22 --dry-run
```

All `tx` commands use the audio device and callsign from `gateway.json`. Override on the fly:

```bash
python gust.py tx weather --temp 22 --callsign OE0XYZ --device 9 --level 50
```

---

## 6. Dial Frequency & Channel Plan

Set your transceiver to **14.110 MHz USB**. GUST uses 10 audio channels (NF) within the SSB passband:

| Channel | NF (Hz) | RF (MHz) |
|---------|---------|---------|
| 0 | 400–650 | 14.1104–14.1126 |
| 1 | 650–900 | 14.1106–14.1129 |
| … | … | … |
| 9 | 2650–2900 | 14.1126–14.1129 |

Your home channel is assigned deterministically from your callsign:
```bash
python gust.py info
# → Kanal 2, TX-Offset +220s (Zyklus 5 min)
```

---

## 7. Troubleshooting

| Problem | Solution |
|---------|---------|
| `ModuleNotFoundError: gust_msg_simulator` | Copy `gust_msg_simulator.py` from the repo root to your working directory |
| `No module named sounddevice` | `pip install sounddevice` |
| HackRF not found (Windows) | Set `$env:PYTHONPATH = "C:\Program Files\PothosSDR\lib\python3.9"` |
| Web UI shows "Invalid Date" | Update `gust_web.py` to the latest version |
| SNR shown as negative on channel 0 | Update `gust_rx.py` to the latest version (adaptive noise band fix) |
| Audio device not transmitting | Check `"level"` in `gateway.json` (default 30 = 30%) |

---

*GUST v0.4 · License: CC BY-SA 4.0 · OE3GAS · May 2026*