# GUST — Getting Started

**Generic Universal Shortwave Telemetry** · [github.com/oe3gas/gust](https://github.com/oe3gas/gust)

> A terse HF broadcast protocol inspired by Olivia, optimized for sub-5-second transmissions.

---

> [!NOTE]
> **GUST is experimental software.** The protocol, frame format, and API may change.
> On-air tests are ongoing and feedback from the amateur radio community is very welcome.
> If you receive GUST signals or run a receiving station, please get in touch —
> **test participants are actively sought!** Contact: [OE3GAS on GitHub](https://github.com/oe3gas/gust/issues)

---

## 0. Try GUST without hardware — Docker

The fastest way to explore GUST — no transceiver, no Python, no configuration needed.
Docker runs a complete GUST gateway in simulator mode. All web UI features are fully
functional: live frame feed, channel grid, REST API, WebSocket, TX queue.

**This is the recommended first step** before setting up real hardware.

### What you need

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows / macOS)
  or Docker Engine (Linux / WSL2)
- A browser

### Start in three commands

```bash
git clone https://github.com/OE3GAS/gust.git
cd gust
docker build -t gust .
docker run --rm -p 8080:8080 -e GUST_CALLSIGN=OE0XYZ gust
```

Replace `OE0XYZ` with your callsign. Open **[http://localhost:8080](http://localhost:8080)**.

Simulated WEATHER, POSITION and TEXT frames arrive automatically every 45–90 seconds.

### What works in Docker

| Feature | Available |
|---------|-----------|
| Web dashboard (all tabs) | ✅ |
| Live frame feed (WebSocket) | ✅ |
| Channel grid + frame detail popup | ✅ |
| REST API (`/api/status`, `/api/tx/*`) | ✅ |
| TX queue, priority system | ✅ |
| Emergency frame dialog | ✅ |
| Audio TX / PTT | ❌ (no soundcard access) |
| CAT control / rigctld | ❌ (no COM port access) |

### docker compose (alternative)

```bash
GUST_CALLSIGN=OE0XYZ docker compose up
```

### Installing Docker on Windows 11 with WSL2

If Docker Desktop is not available, Docker Engine runs directly inside WSL2:

```bash
sudo apt update && sudo apt install -y ca-certificates curl gnupg
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update && sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo service docker start
sudo usermod -aG docker $USER && newgrp docker
```

Port forwarding from WSL2 to Windows works automatically —
`http://localhost:8080` opens in any Windows browser.

> **Ready for hardware?** Continue with [§1 Prerequisites](#1-prerequisites) below.

---

## 1. Prerequisites

### Software

| What | Where | Notes |
|------|-------|-------|
| **Python 3.9+** | [python.org](https://www.python.org/downloads/) | 3.9 required on Windows (PothosSDR); 3.9+ elsewhere |
| **PothosSDR** *(Windows, HackRF only)* | [github.com/pothosware/PothosSDR](https://github.com/pothosware/PothosSDR/releases) | SoapySDR + HackRF drivers for Windows |
| **Git** | [git-scm.com](https://git-scm.com) | To clone the repo |

### Hardware (all optional)

| Hardware | Purpose |
|----------|---------|
| SSB transceiver (e.g. IC-7610) | RX via USB audio, TX via audio line-in + PTT |
| HackRF One | Lab TX / loopback testing |
| USB audio adapter | Any soundcard connected to transceiver |
| Raspberry Pi | Headless gateway / receive-only station |

GUST runs fully without hardware using `--sim` mode.

---

## 2. Installation

### Windows

```powershell
git clone https://github.com/oe3gas/gust.git
cd gust
pip install -r requirements.txt
cp gateway.json.example gateway.json
```

> **HackRF only:** PothosSDR must be installed first. Set the environment variable
> before any GUST command that uses the HackRF:
> ```powershell
> $env:PYTHONPATH = "C:\Program Files\PothosSDR\lib\python3.9"
> ```

### Linux / Raspberry Pi

```bash
# System dependencies (PortAudio for audio I/O)
sudo apt update && sudo apt install -y portaudio19-dev python3-pip python3-venv git

# HackRF support (optional)
sudo apt install -y soapysdr-tools soapysdr-module-hackrf

# Clone
git clone https://github.com/oe3gas/gust.git
cd gust
```

**Raspberry Pi OS Bookworm (2023+) requires a virtual environment** — direct
`pip install` into the system Python is blocked. Create a venv once:

```bash
python3 -m venv .venv
source .venv/bin/activate        # activate (repeat after every new shell)
pip install -r requirements.txt
```

The prompt changes to `(.venv)` when the environment is active.
Always run `source .venv/bin/activate` before using GUST in a new terminal.

> **Tip:** Add the following line to `~/.bashrc` to activate automatically
> whenever you enter the gust directory:
> ```bash
> cd ~/gust && source .venv/bin/activate
> ```

```bash
cp gateway.json.example gateway.json
```

For Raspberry Pi GPIO PTT (optional):
```bash
pip install RPi.GPIO          # inside the venv, no --break-system-packages needed
```

On **older RPi OS (Bullseye and earlier)** a venv is optional but still recommended.
Without it, append `--break-system-packages` to the pip command:
```bash
pip install -r requirements.txt --break-system-packages
```

### macOS

```bash
# Homebrew required — install from https://brew.sh if not present
brew install python@3.9 portaudio git

# HackRF support (optional)
brew install soapysdr hackrf

git clone https://github.com/oe3gas/gust.git
cd gust
pip3.9 install -r requirements.txt

cp gateway.json.example gateway.json
```

### Configuration

Edit `gateway.json` with your callsign and audio device:

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

Leave `"device": 0` for now — find the correct number using `python gust.py devices`.

---

## 3. Quick Start — Simulation (no hardware needed)

```bash
python gust.py daemon --sim
```

Open **[http://localhost:8080](http://localhost:8080)** — the web dashboard shows
simulated WEATHER, POSITION and TEXT frames. All features are fully functional
(channel grid, frame detail popup, audio alerts). Stop with **Ctrl+C**.

---

## 4. Full Operation — RX + TX with Hardware

### Step 1 — Find audio device numbers

```bash
python gust.py devices
```

```
  0  Built-in Audio
  1  IC-7610 USB Audio CODEC   ← RX input (from transceiver)
  9  IC-7610 USB Audio CODEC   ← TX output (to transceiver)
```

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

`ptt_backend`: `"null"` (no PTT) · `"hamlib"` (via rigctld) · `"gpio"` (RPi GPIO)

### Step 3 — Start the daemon

```bash
# Windows (with HackRF)
$env:PYTHONPATH = "C:\Program Files\PothosSDR\lib\python3.9"
python gust.py daemon --device 1

# Linux / macOS / RPi
python gust.py daemon --device 1
```

Replace `1` with your RX device number. Open **[http://localhost:8080](http://localhost:8080)**.

### Step 4 — TX loopback test (HackRF)

In a second terminal — sends random frames via HackRF into the transceiver:

```bash
# Windows
$env:PYTHONPATH = "C:\Program Files\PothosSDR\lib\python3.9"
python gust_tx_test.py --min-gain 4 --max-gain 12 --pause 4

# Linux / macOS
python gust_tx_test.py --min-gain 4 --max-gain 12 --pause 4
```

All `gust_tx_test.py` frames carry the **TEST flag** (blue `TEST` badge in dashboard).
Use `--no-test` to send untagged frames.

---

## 5. CLI — One-Shot Commands

```bash
# Channel assignment for your callsign
python gust.py info

# Channel assignment for another callsign
python gust.py info OE1XTU

# List audio devices
python gust.py devices

# RX monitor only (no web UI)
python gust.py rx --device 1
```

### Send a single frame

```bash
# Weather
python gust.py tx weather --temp 22 --humidity 60 --pressure 1013 --wind 15 --wind-dir 270

# Position
python gust.py tx position --lat 48.21 --lon 16.37 --alt 180

# Text message
python gust.py tx text "73 de OE0XYZ" OE1XTU

# Emergency beacon  (--confirm required for live TX)
python gust.py tx emergency --persons 2 --injury 1 --lat 48.21 --lon 16.37 --confirm

# Dry-run: build and display without transmitting
python gust.py tx weather --temp 22 --dry-run
```

Override callsign, device or level on the fly:
```bash
python gust.py tx weather --temp 22 --callsign OE0XYZ --device 9 --level 50
```

---

## 6. Frequencies

GUST uses **USB mode** throughout. The 10 NF channels (400–2900 Hz) fit within any
standard SSB passband. Dial frequency determines the RF band.

### Preferred frequency — 30 m (primary)

| Dial | NF channels | RF occupied |
|------|-------------|-------------|
| **10.139 MHz USB** | 400–2900 Hz | **10.1394–10.1419 MHz** ✓ within 10.130–10.150 |

**Set your transceiver to 10.139 MHz USB.** This places all GUST channels squarely
within the 30 m digital sub-band (10.130–10.150 MHz), away from CW and PSK31.

### Planned test segments (IARU Region 1)

| Band | Segment (kHz) | Dial (MHz, USB) |
|------|--------------|-----------------|
| 630 m | 475–479 | 0.474 |
| 160 m | 1838–1840 | 1.837 |
| 80 m | 3570–3600 | 3.568 |
| 40 m | 7040–7050 | 7.038 |
| **30 m** | **10130–10150** | **10.139** ← preferred |
| 20 m | 14110–14125 | 14.108 |
| 17 m | 18105–18109 | 18.103 |
| 15 m | 21090–21110 | 21.088 |
| 10 m | 28120–28150 | 28.118 |
| 2 m *(VHF test)* | 144.900 MHz | 144.898 |
| 70 cm *(VHF test)* | 432.200 MHz | 432.198 |

Your home channel within any band is fixed by your callsign:
```bash
python gust.py info
# → Channel 2  TX-Offset +220s  (cycle: 5 min)
```

---

## 7. Transceiver via Hamlib / rigctld

GUST controls PTT and reads frequency via **rigctld** (part of Hamlib). Configuration is done entirely through the Web UI under **Config → Transceiver (Hamlib)**.

### Setup

1. Install Hamlib: [hamlib.org](https://hamlib.org) (Windows installer available)
2. Open the Web UI → **Config → Transceiver (Hamlib)**
3. Select serial port, search for your rig model, set baud rate
4. Enable **auto_start** and click **💾 Speichern**
5. Click **🔌 Verbinden & Testen** — the green dot and current frequency confirm success

GUST will start rigctld automatically on every daemon start.

### Microham USB Interface III — Critical Setting

If you use a **Microham USB Interface III** (or similar USB CAT interface), you must configure the **Microham USB Device Router** correctly:

| Field | Setting |
|-------|---------|
| Radio | Your COM port + baud rate (e.g. COM10, 4800 Baud) |
| CW | COM port + DTR (optional, for CW keying) |
| **PTT** | **none** ← this is critical! |
| SQL | none |

> **Why?** If PTT is set to a COM port in the Microham router, the router controls PTT via RTS/DTR. rigctld simultaneously tries to control PTT via CAT protocol (`T 1`/`T 0`). This conflict causes PTT to get stuck — the transceiver stays in TX and does not return to RX.
>
> With PTT set to **none**, rigctld handles PTT exclusively via CAT. No hardware RTS/DTR is needed.

**Tested configuration (TS-790E + Microham USB III):**
```
Microham USB Device Router:
  Radio:  COM10, 4800 8N2
  PTT:    none          ← PTT via CAT only

gateway.json:
  rig_model: 2007  (Kenwood TS-790)
  device:    COM10
  baud:      4800
```

### Tune Button

The **📡 Tune** button (Config → Transceiver → bottom right) transmits a 1000 Hz sine tone for 15 seconds with PTT active — useful for checking output power and SWR. It works as a toggle: first click starts, second click stops early. Frequency polling is paused automatically during Tune to avoid CAT collisions.

---

## 8. Troubleshooting

| Problem | Solution |
|---------|---------|
| `No module named gust_msg_simulator` | File not in working directory — copy from repo root |
| `No module named sounddevice` | `pip install sounddevice` |
| `No module named aiohttp` | `pip install aiohttp` |
| HackRF not found (Windows) | Set `$env:PYTHONPATH` to PothosSDR Python path |
| HackRF not found (Linux) | `sudo apt install soapysdr-module-hackrf` |
| `portaudio` error on macOS/Linux | `brew install portaudio` / `apt install portaudio19-dev` |
| Web UI shows "Invalid Date" | Update `gust_web.py` to latest version; hard-refresh browser (Ctrl+Shift+R) |
| No audio output | Check `"level"` in `gateway.json` (try 50–80); check device number |
| SNR reads negative on channel 0 | Update `gust_rx.py` (adaptive noise band fix, v0.4+) |
| PTT stuck / TRx stays in TX | Check Microham USB Device Router: set PTT to **none** (see Section 7) |
| rigctld not starting | Verify `rigctld` is in PATH: `rigctld --version`; check COM port and baud rate |
| Tune button has no effect | Ensure rigctld is running first (green dot in Transceiver tab) |

---

## 9. Get Involved

GUST is an open amateur radio experiment. Every decoded frame, every report of
successful reception, and every suggestion helps improve the protocol.

- **GitHub:** [github.com/oe3gas/gust](https://github.com/oe3gas/gust)
- **Issues / feedback:** open a GitHub issue or pull request
- **On air:** listen on 10.139 MHz USB (30 m) — reports welcome!

73 de OE3GAS

---

*GUST v0.5 · License: CC BY-SA 4.0 · OE3GAS · June 2026*