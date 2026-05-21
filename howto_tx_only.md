# GUST — TX-Only Setup

Minimal installation for transmitting GUST frames.
Two paths covered — choose the one matching your hardware:

| Path | Hardware | Best for |
|------|---------|----------|
| **A — HackRF** | HackRF One | Lab loopback, direct RF into transceiver |
| **B — RPi + USB Audio** | Raspberry Pi + USB soundcard + any SSB rig | FT-818, IC-705, field gateway |

No receiver, no web interface required for either path.

---

---

# Path A — HackRF TX

## What you need

### Files (4 + config)

Copy these files from the [GUST repository](https://github.com/oe3gas/gust) into a folder of your choice:

```
gust_tx_test.py
gust_frame.py
gust_modulator.py
gust_hackrf.py
gateway.json.example   → rename to gateway.json
```

### gateway.json

Only the callsign is mandatory:

```json
{
  "callsign": "OE0XYZ"
}
```

Replace `OE0XYZ` with your callsign.

---

## Installation

### Windows

**Step 1 — Install Python 3.9**

Download from [python.org](https://www.python.org/downloads/). During setup, check
*"Add Python to PATH"*.

> Python 3.9 specifically is required — PothosSDR ships Python bindings only for 3.9.

**Step 2 — Install PothosSDR**

Download the latest installer from
[github.com/pothosware/PothosSDR/releases](https://github.com/pothosware/PothosSDR/releases).
This provides SoapySDR and the HackRF USB driver in one package.

**Step 3 — Install Python packages**

```powershell
pip install numpy scipy reedsolo
```

**Step 4 — Set PYTHONPATH**

This is required every time before running GUST in a new PowerShell window:

```powershell
$env:PYTHONPATH = "C:\Program Files\PothosSDR\lib\python3.9"
```

To avoid repeating this, add it permanently to your user environment variables
(*System → Advanced → Environment Variables → User variables → New*).

**Step 5 — Verify HackRF is found**

Plug in the HackRF, then:

```powershell
& "C:\Users\<yourname>\AppData\Local\Programs\Python\Python39\python.exe" -c "import SoapySDR; print(SoapySDR.Device.enumerate())"
```

Expected output: `[{'driver': 'hackrf', 'serial': '...'}]`

---

### Linux (Debian / Ubuntu / Raspberry Pi OS)

**Step 1 — System packages**

```bash
sudo apt update
sudo apt install -y python3-pip python3-soapysdr soapysdr-module-hackrf
```

**Step 2 — Python packages**

```bash
pip install numpy scipy reedsolo --break-system-packages
```

**Step 3 — Verify HackRF is found**

Plug in the HackRF, then:

```bash
python3 -c "import SoapySDR; print(SoapySDR.Device.enumerate())"
```

Expected output: `[{'driver': 'hackrf', 'serial': '...'}]`

If you get a *permission denied* error on the USB device:

```bash
sudo usermod -aG plugdev $USER   # then log out and back in
```

---

### macOS

**Step 1 — Install Homebrew** (if not present)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

**Step 2 — Install SoapySDR + HackRF**

```bash
brew install python@3.9 soapysdr hackrf
```

**Step 3 — Python packages**

```bash
pip3.9 install numpy scipy reedsolo
```

**Step 4 — Verify**

```bash
python3.9 -c "import SoapySDR; print(SoapySDR.Device.enumerate())"
```

---

## Running the TX test

Navigate to the folder containing the GUST files, then:

```bash
# Windows (PowerShell)
$env:PYTHONPATH = "C:\Program Files\PothosSDR\lib\python3.9"
python gust_tx_test.py --min-gain 4 --max-gain 12 --pause 4

# Linux / macOS
python3 gust_tx_test.py --min-gain 4 --max-gain 12 --pause 4
```

This sends random frames (WEATHER, POSITION, TEXT, EMERGENCY) continuously with
random gain between 4–12 dB and random pauses up to 4 seconds.
All frames are automatically tagged with the **TEST flag** (bit 7 of the channel byte).

### Useful options

```bash
# Fixed gain, 20 transmissions, then stop
python gust_tx_test.py --min-gain 10 --max-gain 10 --count 20

# Single frame type only, specific channel
python gust_tx_test.py --channels 2 --no-dual --count 10

# Dual-channel frames only (diversity TX on two channels simultaneously)
python gust_tx_test.py --dual-only --count 10

# Show what would be sent without actually transmitting
python gust_tx_test.py --dry-run --count 5

# Send without TEST flag (live operation frames)
python gust_tx_test.py --no-test --min-gain 10 --max-gain 10
```

### Gain guide (HackRF → transceiver audio input)

| `--min/max-gain` | Signal level | Use case |
|-----------------|-------------|---------|
| 28–32 dB | Strong | Close coupling, attenuator recommended |
| 14–22 dB | Medium | Typical lab loopback |
| 4–12 dB | Weak | SNR threshold testing |
| 1 dB | Near noise | Minimum detectable signal |

---

## Frequency setup

Set your transceiver to **USB mode**. Recommended starting frequency:

| Band | Dial frequency | Segment |
|------|---------------|---------|
| **30 m** *(preferred)* | **10.139 MHz USB** | 10.130–10.150 MHz digital |
| 20 m | 14.110 MHz USB | 14.110–14.125 MHz digital |
| 40 m | 7.038 MHz USB | 7.040–7.050 MHz digital |

GUST occupies a 2.5 kHz wide NF window (400–2900 Hz) within the SSB passband.
All 10 channels are visible simultaneously in a waterfall display.

---

## Troubleshooting

| Problem | Solution |
|---------|---------|
| `ModuleNotFoundError: SoapySDR` | PYTHONPATH not set (Windows) or `python3-soapysdr` not installed |
| `No devices found` | HackRF not plugged in or USB driver missing |
| `hackrf_open() failed` | HackRF in use by another application (SDR#, GQRX etc.) — close it first |
| `FEHLER Dual-IQ` / Python error | Check that all 4 `.py` files are in the same folder |
| `ModuleNotFoundError: reedsolo` | `pip install reedsolo` |
| Frames transmitted but not decoded | Check transceiver is on USB, correct dial frequency, audio input level |
| Permission denied on HackRF (Linux) | `sudo usermod -aG plugdev $USER` then re-login |

---


---

# Path B — Raspberry Pi + USB Audio TX

Transmit GUST frames via the RPi audio output into any SSB transceiver.
PTT is controlled via **Hamlib / rigctld** — compatible with microHAM interfaces,
USB-CAT adapters, and any rig supported by Hamlib.

Example setup used here: **Raspberry Pi + FT-818 + microHAM USB Interface III**,
but the procedure works for any Hamlib-supported transceiver.

---

## Hardware connections

```
Raspberry Pi
  USB port ──► USB audio adapter ──► 3.5 mm cable ──► Transceiver MIC input
                                                        (adjust level with "level" in gateway.json)

microHAM USB Interface III
  USB port ──► RPi USB port        (appears as /dev/ttyUSB0 or similar)
  PTT output ──► Transceiver PTT   (via RJ45 / ACC connector)
```

> **Audio level:** Start with `"level": 30` (30 %) and increase slowly while watching
> the transceiver's ALC meter. ALC should just barely move — no compression.

---

## Required files

```
gust.py
gust_frame.py
gust_modulator.py
gust_audio.py
gust_eventbus.py
gust_web.py
gust_msg_simulator.py
gateway.json
```

> `gust_tx_test.py` is **HackRF-only** and cannot be used with the audio path.
> Use `gust.py tx` (one-shot) or `gust.py daemon --sim` (continuous) instead.

---

## Installation on Raspberry Pi

### Step 1 — System packages

```bash
sudo apt update
sudo apt install -y python3-pip portaudio19-dev git hamlib-utils
```

### Step 2 — Virtual environment (recommended on RPi OS Bookworm+)

Raspberry Pi OS Bookworm (2023+) blocks direct `pip install` into the system Python.
Create a venv once and activate it for every session:

```bash
python3 -m venv .venv
source .venv/bin/activate    # prompt changes to (.venv)
```

### Step 3 — Python packages

```bash
pip install numpy scipy reedsolo sounddevice aiohttp
```

> On **older RPi OS (Bullseye)** without venv, append `--break-system-packages` instead.

### Step 4 — Find your USB audio device number

Plug in the USB audio adapter, then:

```bash
python3 gust.py devices
```

Sample output:
```
  0  bcm2835 Headphones
  1  USB Audio Device        ← this is your TX output
```

Note the device number (here: `1`).

### Step 5 — Configure gateway.json

```json
{
  "callsign":  "OE0XYZ",
  "audio": {
    "device":       1,
    "ptt_backend":  "hamlib",
    "level":        30,
    "hamlib_host":  "localhost",
    "hamlib_port":  4532
  }
}
```

Replace `OE0XYZ` with your callsign and `1` with your audio device number.

---

## PTT setup with microHAM + Hamlib

### Find the microHAM serial port

```bash
ls /dev/ttyUSB*    # or /dev/ttyACM*
# Typically: /dev/ttyUSB0
```

If multiple devices are listed, check which is the microHAM:
```bash
dmesg | grep tty | tail -10
```

### Start rigctld

**FT-818 / FT-817:**
```bash
rigctld -m 1020 -r /dev/ttyUSB0 -s 9600 &
```

**IC-705 (USB CAT, no microHAM needed):**
```bash
rigctld -m 3085 -r /dev/ttyACM0 -s 19200 &
```

**Other rigs:** find the model number with `rigctld --list | grep <rigname>`

### Test PTT

```bash
# Key the transmitter for 2 seconds, then unkey
rigctl -m 2 -r localhost:4532 T 1 && sleep 2 && rigctl -m 2 -r localhost:4532 T 0
```

The rig should go into transmit mode. If not, check the serial port and baud rate.

### Auto-start rigctld on boot (optional)

```bash
sudo nano /etc/rc.local
```

Add before `exit 0`:
```bash
rigctld -m 1020 -r /dev/ttyUSB0 -s 9600 &
```

---

## Sending frames

### One-shot TX (single frame)

```bash
# Weather frame
python3 gust.py tx weather --temp 12 --humidity 75 --pressure 1015 --wind 8 --wind-dir 270

# Position
python3 gust.py tx position --lat 48.21 --lon 16.37 --alt 180

# Free text
python3 gust.py tx text "73 de OE0XYZ" OE1XTU

# Emergency (--confirm required for live TX)
python3 gust.py tx emergency --persons 1 --injury 0 --lat 48.21 --lon 16.37 --confirm

# Test without transmitting
python3 gust.py tx weather --temp 12 --dry-run
```

### Continuous TX test (simulated frames)

```bash
python3 gust.py daemon --sim
```

This sends WEATHER, POSITION and TEXT frames at regular intervals (5 min / 2 min)
and starts the web dashboard at [http://localhost:8080](http://localhost:8080).
Stop with **Ctrl+C**.

To run headless without the web interface, use `rx: {enabled: false}` in gateway.json
or send output to a logfile:

```bash
python3 gust.py daemon --sim > gust.log 2>&1 &
```

---

## Frequency setup

Set the transceiver to **USB mode**:

| Band | Dial frequency | Segment |
|------|---------------|---------|
| **30 m** *(preferred)* | **10.139 MHz USB** | 10.130–10.150 MHz digital |
| 20 m | 14.110 MHz USB | 14.110–14.125 MHz |
| 40 m | 7.038 MHz USB | 7.040–7.050 MHz |

GUST occupies 2.5 kHz (NF 400–2900 Hz) — fits inside any SSB passband.

---

## Troubleshooting

| Problem | Solution |
|---------|---------|
| No audio output | Wrong device number — run `python3 gust.py devices` again |
| Rig keys but no audio | Check audio cable and transceiver mic gain / input selection |
| ALC peaking | Reduce `"level"` in gateway.json (try 20–30) |
| rigctld: Permission denied on `/dev/ttyUSB0` | `sudo usermod -aG dialout $USER` then re-login |
| rigctld: Connection refused | rigctld not running — start it first, or check port 4532 |
| PTT but no TX audio | Check transceiver mode is USB, not FM or AM |
| No PTT at all | Verify microHAM wiring to rig PTT input; check rigctld model number |


*GUST v0.4 · [github.com/oe3gas/gust](https://github.com/oe3gas/gust) · License: CC BY-SA 4.0*
