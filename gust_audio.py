#!/usr/bin/env python3
"""
GUST — Audio TX/RX + PTT-Steuerung                        Phase 3
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Version : 1.0.0
Datum   : Mai 2026

Dieses Modul implementiert die Hardware-Integration für den
Gateway-Betrieb auf dem Raspberry Pi und am IC-7610.

── PTT-Backends ───────────────────────────────────────────────────────
  NullPTT       Simulation (kein Hardware, für Tests und PC-Betrieb)
  GPIUPTT       Raspberry Pi GPIO-Pin (für Relais-/Transistor-PTT)
  HamlibPTT     hamlib rigctld (IC-7610 und alle anderen hamlib-Rigs)

── Audio-Ausgabe (sounddevice) ────────────────────────────────────────
  AudioTransmitter  PTT ein → Stille → Audio → Stille → PTT aus
  list_audio_devices()  Alle Geräte auflisten (für Gerätewahl)

── TX-Helfer ──────────────────────────────────────────────────────────
  transmit_frame()  Alles in einem: Frame → Audio → PTT → Sender

── Signalverarbeitung ─────────────────────────────────────────────────
  Das NF-Signal geht direkt an den Line-In / Mic-Eingang des IC-7610
  oder wird über einen USB-Audioadapter ausgegeben.
  Pegel: normalisiert auf 80% Vollaussteuerung (ausreichend Headroom).

── Verdrahtung IC-7610 ────────────────────────────────────────────────
  USB-Soundkarte → 3,5mm Klinkenstecker → ACC-Buchse IC-7610
  GPIO Pin 17    → Transistor/Relais    → PTT-Buchse IC-7610
  Alternativ: hamlib rigctld (kein GPIO nötig, PTT via CAT/CI-V)

── Verdrahtung Raspberry Pi GPIO ─────────────────────────────────────
  GPIO 17 (Pin 11)  PTT-Ausgang (HIGH = TX, LOW = RX)
  Schaltung: GPIO → 10kΩ → Basis BC547 → PTT-Buchse
             Kollektor an PTT, Emitter an GND
  Mit Pull-Down: kein unbeabsichtigter TX beim Start

── hamlib rigctld für IC-7610 ────────────────────────────────────────
  Starten (IC-7610 USB CI-V):
    rigctld -m 3085 -r /dev/ttyUSB0 -s 19200

  Starten (IC-7610 Soapy7610 / USB-Audio):
    rigctld -m 3085 -r /dev/ttyUSB0 --vfo-comp=0 -T localhost -t 4532

  PTT testen:
    rigctl -m 2 T 1    # PTT ein (Simulated Rig)
    rigctl -m 2 T 0    # PTT aus
"""

import os
import time
import socket
import subprocess
import threading
import sys
import logging

import numpy as np
from typing import Union, Optional

# sounddevice (optional — nur für Audio-Ausgabe benötigt)
try:
    import sounddevice as sd
    _SD_AVAILABLE = True
except ImportError:
    _SD_AVAILABLE = False

# RPi.GPIO (optional — nur auf Raspberry Pi verfügbar)
try:
    import RPi.GPIO as GPIO
    _GPIO_AVAILABLE = True
except ImportError:
    _GPIO_AVAILABLE = False

from gust_modulator import SAMPLE_RATE, transmit, receive, load_wav
from gust_frame import FrameType, encode_weather, build_frame, N_CHANNELS

log = logging.getLogger("gust.audio")

# VITAL-Fallback für Standalone-Betrieb (z.B. `py gust_rx.py`):
# log.vital() wird normalerweise von gust.py definiert (Level 35).
# Ohne diesen Guard würde _callback() im PortAudio-Thread mit
# AttributeError abbrechen, wenn gust.py nicht geladen ist.
if not hasattr(logging.Logger, "vital"):
    logging.addLevelName(35, "VITAL")
    def _vital(self, message, *args, **kwargs):
        if self.isEnabledFor(35):
            self._log(35, message, args, **kwargs)
    logging.Logger.vital = _vital


# ═══════════════════════════════════════════════════════════════════════
# KONSTANTEN
# ═══════════════════════════════════════════════════════════════════════

PTT_PIN_DEFAULT  = 17        # GPIO-Pin BCM (Pin 11 auf 40-Pin Header)
PTT_LEAD_S       = 0.050     # 50 ms Vorlauf: Sender hochfahren, VFO stabilisieren
PTT_TAIL_S       = 0.020     # 20 ms Nachlauf: letztes Symbol ausklingen lassen
AUDIO_LEVEL      = 0.80      # Normalisierungspegel (80% → Headroom für ALC)

RIGCTLD_HOST_DEFAULT = "127.0.0.1"  # IPv4 explizit — Windows löst 'localhost' als ::1 auf
RIGCTLD_PORT_DEFAULT = 4532


# ═══════════════════════════════════════════════════════════════════════
# PTT-BACKENDS
# ═══════════════════════════════════════════════════════════════════════
#
# Alle Backends implementieren dieselbe Schnittstelle:
#   .activate()    PTT einschalten (TX)
#   .release()     PTT ausschalten (RX)
#   .close()       Ressourcen freigeben
#
# Sicherheitsregel: Im Fehlerfall immer PTT lösen (try/finally).
# Das AudioTransmitter garantiert dies unabhängig vom Backend.

class PTTBackend:
    """Basis-Klasse für alle PTT-Backends."""

    def activate(self):
        """PTT einschalten — Sender auf TX."""
        raise NotImplementedError

    def release(self):
        """PTT ausschalten — Sender auf RX."""
        raise NotImplementedError

    def close(self):
        """Ressourcen freigeben (GPIO cleanup, Socket schließen etc.)."""
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.release()
        self.close()


# ──────────────────────────────────────────────────────────────────────
# NullPTT  — Simulation ohne Hardware
# ──────────────────────────────────────────────────────────────────────
class NullPTT(PTTBackend):
    """
    Simuliertes PTT-Backend — kein Hardware erforderlich.

    Loggt PTT-Ereignisse als debug (nur --verbose sichtbar).
    Verwendung: Tests auf dem PC, Entwicklung ohne Funkgerät.
    """
    def __init__(self, verbose: bool = True):
        # verbose: nur noch für Rückwärtskompatibilität (Aufrufer
        # setzen es teils) — Sichtbarkeit steuert jetzt das Logging.
        self.verbose = verbose
        self._active = False

    def activate(self):
        self._active = True
        log.debug("[NullPTT] PTT aktiviert")

    def release(self):
        if not self._active:
            return   # bereits released — kein Doppel-Log
        self._active = False
        log.debug("[NullPTT] PTT gelöst")

    @property
    def is_active(self) -> bool:
        return self._active


# ──────────────────────────────────────────────────────────────────────
# GPIUPTT  — Raspberry Pi GPIO
# ──────────────────────────────────────────────────────────────────────
class GPIUPTT(PTTBackend):
    """
    PTT über Raspberry Pi GPIO-Pin (BCM-Nummerierung).

    Schaltung:
        GPIO Pin (3,3V) → 10kΩ Vorwiderstand → Basis BC547 NPN
        Kollektor → PTT-Buchse am Transceiver
        Emitter   → Masse (GND)

    Hinweis:
        Der RPi GPIO-Pin liefert max. 16 mA — ausreichend für einen
        kleinen NPN-Transistor (BC547, BC548). Keinen PTT-Pin direkt
        mit dem Transceiver verbinden.

    Args:
        pin:  BCM GPIO-Nummer (Standard: 17 = Pin 11 am 40-Pin Header)
    """
    def __init__(self, pin: int = PTT_PIN_DEFAULT):
        if not _GPIO_AVAILABLE:
            raise RuntimeError(
                "RPi.GPIO nicht installiert oder kein Raspberry Pi.\n"
                "Installation: pip install RPi.GPIO  (nur auf RPi)"
            )
        self.pin = pin
        self._active = False
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
        log.vital("[PTT GPIUPTT] GPIO Pin %d initialisiert (BCM, Output, LOW)", pin)

    def activate(self):
        self._active = True
        GPIO.output(self.pin, GPIO.HIGH)
        log.vital("[PTT GPIUPTT] Pin %d HIGH → TX EIN", self.pin)

    def release(self):
        if not self._active:
            return   # bereits released — kein Doppel-Impuls
        self._active = False
        GPIO.output(self.pin, GPIO.LOW)
        log.vital("[PTT GPIUPTT] Pin %d LOW → TX AUS", self.pin)

    def close(self):
        GPIO.cleanup(self.pin)
        log.debug("[PTT GPIUPTT] GPIO cleanup Pin %d", self.pin)


# ──────────────────────────────────────────────────────────────────────
# HamlibPTT  — hamlib rigctld (IC-7610, IC-7300, alle hamlib-Rigs)
# ──────────────────────────────────────────────────────────────────────
class HamlibPTT(PTTBackend):
    """
    PTT über hamlib rigctld-Daemon (TCP, Port 4532).

    Funktioniert mit IC-7610, IC-7300, FT-991, und allen anderen
    Transceivern die hamlib unterstützt.

    Vorteil gegenüber GPIO:
      - Kein Hardware-Eingriff am RPi nötig
      - PTT über CAT/CI-V-Interface des Transceivers
      - Kann auch Frequenz, Mode, etc. steuern

    rigctld starten (IC-7610, USB CI-V auf ttyUSB0):
      rigctld -m 3085 -r /dev/ttyUSB0 -s 19200 &

    rigctld starten (Simulation zum Testen ohne Rig):
      rigctld -m 1 &   # Hamlib Dummy-Rig

    Args:
        host:  rigctld hostname (Standard: localhost)
        port:  rigctld TCP-Port  (Standard: 4532)
    """
    def __init__(self, host: str = RIGCTLD_HOST_DEFAULT,
                 port: int = RIGCTLD_PORT_DEFAULT):
        self.host = host
        self.port = port
        self._sock = None
        self._active = False
        self._connect()

    def _connect(self):
        """TCP-Verbindung zu rigctld aufbauen."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(5.0)
        try:
            self._sock.connect((self.host, self.port))
            # debug statt vital: _cmd() verbindet pro Kommando neu
            # (Windows-rigctld) — als vital wäre das Dauer-Spam
            log.debug("[PTT HamlibPTT] Verbunden mit rigctld @ %s:%d",
                      self.host, self.port)
        except ConnectionRefusedError:
            raise RuntimeError(
                f"rigctld nicht erreichbar auf {self.host}:{self.port}\n"
                f"Prüfe ob rigctld läuft (gateway.json: rigctld.auto_start=true)\n"
                f"Oder manuell: rigctld -m <rig_model> -r <device> -s <baud>"
            )

    def _cmd(self, command: str) -> str:
        """Sendet einen rigctld-Befehl mit frischer Verbindung pro Kommando.

        rigctld auf Windows schliesst die TCP-Verbindung nach jedem Kommando
        (Request/Response-Verhalten). Eine persistente Verbindung funktioniert
        daher nicht zuverlaessig. Loesung: jedes Kommando erhaelt eine neue
        Verbindung — guenstig, da GUST nur wenige PTT-Kommandos pro Frame sendet.
        """
        try:
            self._sock.close()
        except Exception:
            pass
        self._connect()
        self._sock.sendall((command + "\n").encode())
        return self._sock.recv(256).decode().strip()

    def activate(self):
        """PTT einschalten via rigctld 'T 1'."""
        self._active = True
        resp = self._cmd("T 1")
        log.vital("[PTT HamlibPTT] T 1 → '%s' → TX EIN", resp)

    def release(self):
        """PTT ausschalten via rigctld 'T 0'.

        OSError wird abgefangen und geloggt statt weiterzuwerfen:
        Das Audio ist zu diesem Zeitpunkt bereits vollstaendig gesendet.
        Der TRX beendet TX automatisch wenn kein NF-Signal mehr anliegt.
        """
        if not self._active:
            return   # bereits released — kein Doppel-Kommando an den TRX
        self._active = False
        try:
            resp = self._cmd("T 0")
            log.vital("[PTT HamlibPTT] T 0 → '%s' → TX AUS", resp)
        except OSError as e:
            log.error("[PTT HamlibPTT] T 0 fehlgeschlagen (%s) — "
                      "TRX setzt PTT automatisch zurueck", e)

    def get_frequency(self) -> float:
        """Aktuelle VFO-Frequenz abfragen (Hz)."""
        resp = self._cmd("f")
        try:
            return float(resp.split("\n")[0])
        except ValueError:
            return 0.0

    def set_mode(self, mode: str = "USB"):
        """Betriebsart setzen (USB, LSB, FM, AM, ...)."""
        self._cmd(f"M {mode} 0")

    def close(self):
        """Socket schließen. PTT wird von AudioTransmitter.close() bereits gelöst."""
        try:
            self._sock.close()
        except Exception:
            pass
        log.debug("[PTT HamlibPTT] Verbindung geschlossen")


# ──────────────────────────────────────────────────────────────────────
# rigctld Auto-Start  —  Hintergrundprozess gemaess gateway.json
# ──────────────────────────────────────────────────────────────────────

def _is_rigctld_alive(host: str, port: int, timeout: float = 0.5) -> bool:
    """TCP-Connect + kurzes 'f'-Kommando als Lebenszeichen.

    SO_LINGER=0: Socket wird mit RST statt FIN geschlossen → kein TCP TIME_WAIT
    unter Windows. Notwendig weil rigctld (Windows) nur eine gleichzeitige
    Verbindung akzeptiert und TIME_WAIT nachfolgende HamlibPTT-Connects blockiert.
    """
    s = None
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.sendall(b"f\n")
        result = len(s.recv(64)) > 0
        return result
    except (OSError, socket.timeout):
        return False
    finally:
        if s is not None:
            try:
                import struct
                s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                             struct.pack("ii", 1, 0))
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass


def ensure_rigctld_running(cfg: dict,
                           host: Optional[str] = None,
                           port: Optional[int] = None,
                           verbose: bool = True) -> Optional[subprocess.Popen]:
    """
    Stellt sicher, dass rigctld erreichbar ist.

    Ablauf:
      1) TCP-Connect zu host:port — laeuft rigctld bereits, return None.
      2) Nicht erreichbar und cfg['rigctld'] fehlt → RuntimeError mit
         Anleitung, welcher Block in gateway.json ergaenzt werden muss.
      3) cfg['rigctld'].auto_start == false → RuntimeError mit dem
         Befehl, den der User manuell ausfuehren soll.
      4) auto_start == true → rigctld als Hintergrundprozess starten
         (Windows: DETACHED_PROCESS, Linux: start_new_session) und bis
         zu 5 s auf das Hochfahren warten.

    Args:
        cfg:     vollstaendiges gateway.json-Dict (Section 'rigctld' wird genutzt)
        host:    Override fuer Host. Default: cfg['rigctld'].host
                 oder cfg['audio'].hamlib_host oder 'localhost'
        port:    Override fuer Port. Default analog (4532).
        verbose: Statusmeldungen loggen (vital/debug). False z.B. im
                 Web-Handler, der selbst eine vital-Meldung absetzt.

    Returns:
        None              — rigctld lief bereits
        subprocess.Popen  — rigctld wurde gestartet (Handle, falls Aufrufer
                            ihn beenden moechte; meist einfach laufen lassen)
    """
    rig_cfg   = cfg.get("rigctld") if isinstance(cfg, dict) else None
    audio_cfg = ((cfg.get("tx_audio") or cfg.get("audio") or {})
                 if isinstance(cfg, dict) else {})

    if host is None:
        host = (rig_cfg or {}).get("host", audio_cfg.get("hamlib_host", "localhost"))
    if port is None:
        port = int((rig_cfg or {}).get("port", audio_cfg.get("hamlib_port", 4532)))

    # 1) Schon erreichbar?
    if _is_rigctld_alive(host, port):
        if verbose:
            log.debug("[rigctld] schon erreichbar @ %s:%d", host, port)
        return None

    # 2) Eintrag fehlt komplett
    if not rig_cfg:
        raise RuntimeError(
            f"rigctld nicht erreichbar auf {host}:{port} und kein "
            f"'rigctld'-Block in gateway.json.\n"
            f"Bitte Eintrag ergaenzen (Beispiel fuer IC-7610 / COM11):\n"
            f'  "rigctld": {{\n'
            f'    "auto_start": true,\n'
            f'    "path": "rigctld",   // oder absoluter Pfad zur EXE\n'
            f'    "rig_model": 3078,    // 3078 = IC-7610\n'
            f'    "device": "COM11",    // serieller Port\n'
            f'    "baud": 19200,\n'
            f'    "host": "localhost",\n'
            f'    "port": 4532\n'
            f'  }}\n'
            f"Alternativ rigctld manuell starten: "
            f"rigctld -m 3078 -r COM11 -s 19200"
        )

    rig_model = rig_cfg.get("rig_model")
    device    = rig_cfg.get("device")
    baud      = rig_cfg.get("baud", 19200)
    exe       = rig_cfg.get("path") or "rigctld"

    # 3) Auto-Start deaktiviert
    if not rig_cfg.get("auto_start", False):
        raise RuntimeError(
            f"rigctld nicht erreichbar auf {host}:{port} und 'auto_start' "
            f"ist deaktiviert.\n"
            f"Manuell starten mit:\n"
            f"  {exe} -m {rig_model} -r {device} -s {baud} -T {host} -t {port}"
        )

    # 4) Auto-Start
    # localhost → 127.0.0.1: Windows löst 'localhost' als ::1 (IPv6) auf,
    # Python-Sockets verbinden aber auf 127.0.0.1 (IPv4) → ConnectionRefused.
    _bind_host = "127.0.0.1" if host in ("localhost", "127.0.0.1") else host
    cmd = [
        exe,
        "-m", str(rig_model),
        "-r", str(device),
        "-s", str(baud),
        "-T", _bind_host,
        "-t", str(port),
    ]
    extra = rig_cfg.get("extra_args") or []
    cmd.extend(str(a) for a in extra)

    if verbose:
        log.vital("[rigctld] Auto-Start als Hintergrundprozess: %s",
                  " ".join(cmd))

    import tempfile, io

    # stderr in temporaere Datei umleiten um Fehlermeldung lesbar zu machen
    stderr_fh = tempfile.TemporaryFile()

    popen_kwargs = {
        "stdin":  subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": stderr_fh,
    }
    if os.name == "nt":
        # Windows: kein eigenes Fenster, ueberlebt Parent-Prozess
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True

    def _read_stderr() -> str:
        try:
            stderr_fh.seek(0)
            return stderr_fh.read(2000).decode(errors='replace').strip()
        except Exception:
            return ''

    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except FileNotFoundError:
        stderr_fh.close()
        raise RuntimeError(
            f"rigctld-Programm nicht gefunden: '{exe}'\n"
            f"Bitte 'rigctld.path' in gateway.json setzen (absoluter Pfad) "
            f"oder rigctld in PATH legen."
        )

    # Auf Boot warten (max. 5 s)
    for _ in range(50):
        time.sleep(0.1)
        if _is_rigctld_alive(host, port):
            if verbose:
                log.vital("[rigctld] gestartet (PID %d), "
                          "erreichbar @ %s:%d", proc.pid, host, port)
            stderr_fh.close()
            return proc
        rc = proc.poll()
        if rc is not None:
            detail = _read_stderr()
            stderr_fh.close()
            raise RuntimeError(
                f"rigctld beendete sofort (Exit-Code {rc}).\n"
                + (f"rigctld-Ausgabe: {detail}\n" if detail else "")
                + f"Pruefen: COM-Port {device} frei? Baud {baud} korrekt? "
                f"rig_model {rig_model} richtig? rigctld-Pfad '{exe}' korrekt?"
            )

    detail = _read_stderr()
    try:
        proc.terminate()
    except Exception:
        pass
    stderr_fh.close()
    raise RuntimeError(
        f"rigctld gestartet, aber Port {port} nicht erreichbar nach 5 s.\n"
        + (f"rigctld-Ausgabe: {detail}\n" if detail else "")
        + f"Konfiguration in gateway.json pruefen."
    )


# ──────────────────────────────────────────────────────────────────────
# PTT-Backend aus Konfiguration erzeugen  (gemeinsam genutzt)
# ──────────────────────────────────────────────────────────────────────

def build_ptt(audio_cfg: dict, full_cfg: Optional[dict] = None) -> PTTBackend:
    """
    PTT-Backend aus der Konfiguration instanziieren.

    Gemeinsam genutzt von der CLI (gust.py, One-Shot-TX) und vom
    Web-TX-Gateway (gust_gateway.py), damit beide Pfade dieselbe
    PTT-Logik verwenden.

    audio_cfg['ptt_backend']:
      "null"   → NullPTT   (Simulation, kein Hardware — Standard)
      "hamlib" → HamlibPTT (rigctld; wird bei Bedarf via gateway.json
                            ['rigctld'].auto_start automatisch gestartet)
      "gpio"   → GPIUPTT   (Raspberry Pi GPIO)

    Zusätzliche Felder in audio_cfg:
      hamlib_host  (Standard: "localhost")
      hamlib_port  (Standard: 4532)
      gpio_pin     (Standard: 17)

    Args:
        audio_cfg: Der "audio"-Abschnitt aus gateway.json.
        full_cfg:  Vollständige Konfiguration — für ensure_rigctld_running()
                   (wertet den Abschnitt 'rigctld' aus).

    Raises:
        RuntimeError: rigctld nicht erreichbar / nicht startbar,
                      RPi.GPIO nicht verfügbar etc.
    """
    backend = (audio_cfg.get("ptt_backend", "null") or "null").lower()

    if backend == "hamlib":
        host = audio_cfg.get("hamlib_host", "localhost")
        port = int(audio_cfg.get("hamlib_port", 4532))
        # rigctld ggf. starten (oder klare Fehlermeldung werfen)
        _proc = ensure_rigctld_running(full_cfg or {}, host=host, port=port)
        log.info("PTT-Backend: HamlibPTT @ %s:%d", host, port)
        ptt = HamlibPTT(host=host, port=port)
        # Handle des ggf. von GUST gestarteten rigctld an den Aufrufer
        # weiterreichen (None, falls rigctld bereits lief). Das TX-Gateway
        # meldet ihn der Web-GUI, damit _handle_hamlib_config den eigenen
        # Prozess nicht als Fremd-Port-Konflikt behandelt.
        ptt._rigctld_proc = _proc
        return ptt

    elif backend == "gpio":
        pin = int(audio_cfg.get("gpio_pin", 17))
        log.info("PTT-Backend: GPIUPTT Pin %d", pin)
        return GPIUPTT(pin=pin)

    else:
        log.info("PTT-Backend: NullPTT (Simulation)")
        return NullPTT()


# ═══════════════════════════════════════════════════════════════════════
# AUDIO-TRANSMITTER
# ═══════════════════════════════════════════════════════════════════════

class AudioTransmitter:
    """
    Vollständige TX-Steuerung: PTT + Audioausgabe via sounddevice.

    Ablauf bei jedem transmit()-Aufruf:
        1. PTT aktivieren
        2. PTT_LEAD_S warten (Sender hochfahren)
        3. Audio abspielen (blockierend, bis letztes Sample gespielt)
        4. PTT_TAIL_S warten (letztes Symbol ausklingen)
        5. PTT lösen
        → Schritt 5 wird auch bei Ausnahmen ausgeführt (try/finally)

    Args:
        ptt:        PTT-Backend (NullPTT, GPIUPTT oder HamlibPTT)
        device:     sounddevice Geräteindex oder Name.
                    None = Systemstandard.
                    Anzeigen mit: gust_audio.py --list
        ptt_lead_s: Vorlaufzeit in Sekunden (Standard: 50 ms)
        ptt_tail_s: Nachlaufzeit in Sekunden (Standard: 20 ms)
        level:      Ausgangspegel 0.0–1.0 (Standard: 0.80 = 80%)
    """

    def __init__(
        self,
        ptt:        PTTBackend = None,
        device:     "Union[int, str, None]" = None,
        ptt_lead_s: float      = PTT_LEAD_S,
        ptt_tail_s: float      = PTT_TAIL_S,
        level:      float      = AUDIO_LEVEL,
    ):
        if not _SD_AVAILABLE:
            raise RuntimeError(
                "sounddevice nicht installiert.\n"
                "Installation: pip install sounddevice\n"
                "Linux: apt install libportaudio2  (PortAudio-Abhängigkeit)"
            )
        self.ptt        = ptt or NullPTT()
        self.device     = device
        self.ptt_lead   = ptt_lead_s
        self.ptt_tail   = ptt_tail_s
        self.level      = level
        self._tx_count  = 0    # Anzahl abgeschlossener Übertragungen

    def transmit_audio(self, audio: np.ndarray,
                       sample_rate: int = SAMPLE_RATE) -> None:
        """
        Gibt Audio über Soundkarte aus mit PTT-Steuerung.

        Erkennt automatisch ob das Gerät Mono oder Stereo erwartet.
        Geräte ohne Mono-Support (viele Windows USB-Audiokarten) bekommen
        ein dupliziertes Stereo-Signal — sonst PaErrorCode -9998.

        Args:
            audio:       Float32-Audiosignal, Mono (1D-Array, normalisiert)
            sample_rate: Abtastrate (Standard: 8000 Hz)
        """
        # Normalisieren auf gewünschten Ausgangspegel
        peak = float(np.max(np.abs(audio)))
        if peak > 0:
            audio = (audio / peak * self.level).astype(np.float32)

        # Auto-Mono/Stereo: Gerät abfragen, Signal ggf. duplizieren
        try:
            dev_info   = sd.query_devices(self.device, kind='output')
            max_out_ch = int(dev_info.get('max_output_channels', 1))
        except Exception:
            max_out_ch = 1
        out_channels = max(1, min(max_out_ch, 2))
        if out_channels == 2 and audio.ndim == 1:
            audio = np.column_stack([audio, audio])

        duration = len(audio) / sample_rate
        dev_name = self.device if self.device is not None else "Standard"
        log.vital("[TX] Gerät: '%s'  |  Dauer: %.2fs  |  "
                  "Pegel: %.0f%%  |  Kanäle: %d",
                  dev_name, duration, self.level * 100, out_channels)

        try:
            self.ptt.activate()
            time.sleep(self.ptt_lead)

            sd.play(audio, samplerate=sample_rate,
                    device=self.device, blocking=True)

            time.sleep(self.ptt_tail)
            self._tx_count += 1
        finally:
            # PTT IMMER lösen — auch bei Ctrl+C oder Ausnahmen
            self.ptt.release()

        log.vital("[TX] Fertig  (#%d)", self._tx_count)

    def transmit_frame(
        self,
        frame_type:     int,
        callsign:       str,
        payload:        bytes,
        channel:        int  = None,
        use_fec:        bool = True,
        window:         bool = True,
    ) -> int:
        """
        Vollständige TX-Pipeline: Payload → Audio → Sender.

        Ruft intern transmit() aus gust_modulator.py auf und
        gibt das Audio via transmit_audio() aus.

        Args:
            frame_type: z.B. FrameType.WEATHER
            callsign:   Rufzeichen der sendenden Station (z.B. 'OE3GAS')
            payload:    Kodierte Nutzdaten (aus encode_*() Funktionen)
            channel:    Kanal 0–9; None = automatisch aus assign_channel()
            use_fec:    Reed-Solomon FEC aktivieren (empfohlen)
            window:     Raised Cosine Fensterung (True für On-Air-Betrieb)

        Returns:
            Verwendeter Kanal (0–9)

        Beispiel:
            ptt = HamlibPTT()
            tx  = AudioTransmitter(ptt=ptt, device='USB Audio CODEC')
            payload = encode_weather(21.5, 68, 1013.2, 15, 270)
            tx.transmit_frame(FrameType.WEATHER, 'OE3GAS', payload)
        """
        audio, used_channel, frame_body = transmit(
            frame_type, callsign, payload,
            channel=channel,
            use_fec=use_fec,
            window=window,
            add_silence_ms=0,   # Stille wird durch PTT-Lead/Tail ersetzt
        )
        log.vital("[TX] Kanal %d  |  Frame: %d Byte  |  Window: %s",
                  used_channel, len(frame_body),
                  "RC" if window else "Rect")
        self.transmit_audio(audio)
        return used_channel

    def transmit_frame_dual(
        self,
        frame_type:  int,
        callsign:    str,
        payload:     bytes,
        channel_a:   int,
        channel_b:   int,
        use_fec:     bool = True,
        window:      bool = True,
    ) -> tuple:
        """
        Parallelkanal-Diversity (ADR-12): denselben Frame gleichzeitig auf
        ZWEI NF-Kanälen senden.

        Beide Kanal-Signale werden zu EINEM NF-Signal gemischt (addiert) und
        in einem einzigen PTT-Zyklus ausgegeben — gleiche Sendedauer wie ein
        Einzel-Frame, die Sendeleistung teilt sich auf beide Kanäle (~6 dB je
        Kanal). Jede Kopie trägt ihr eigenes CHANNEL-Byte (channel_a bzw.
        channel_b); der Breitband-RX erkennt beide und dedupliziert.

        Eingesetzt für Notfall-Frames: zwei Kopien auf gespreizten Kanälen
        erhöhen Dekodierwahrscheinlichkeit und QRM-Schutz deutlich
        (Simplex ~90% → Dual 100%, T-10.2).

        Args:
            channel_a, channel_b: die beiden Zielkanäle (0–9, verschieden).

        Returns:
            (channel_a, channel_b) — die tatsächlich verwendeten Kanäle.
        """
        audio_a, used_a, _ = transmit(
            frame_type, callsign, payload,
            channel=channel_a, use_fec=use_fec, window=window, add_silence_ms=0,
        )
        audio_b, used_b, _ = transmit(
            frame_type, callsign, payload,
            channel=channel_b, use_fec=use_fec, window=window, add_silence_ms=0,
        )

        # Beide Signale auf gleiche Länge bringen und addieren.
        max_len = max(len(audio_a), len(audio_b))
        mixed = np.zeros(max_len, dtype=np.float32)
        mixed[:len(audio_a)] += audio_a
        mixed[:len(audio_b)] += audio_b
        # Endgültige Pegelnormierung (Peak → self.level) macht transmit_audio().

        log.vital("[TX] Dual-Kanal %d+%d  |  Frames gemischt  |  Window: %s",
                  used_a, used_b, "RC" if window else "Rect")
        self.transmit_audio(mixed)
        return used_a, used_b

    def close(self):
        """PTT lösen und Ressourcen freigeben."""
        self.ptt.release()
        self.ptt.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ═══════════════════════════════════════════════════════════════════════
# AUDIO-EMPFÄNGER (Echtzeit-RX)
# ═══════════════════════════════════════════════════════════════════════

class AudioReceiver:
    """
    Echtzeit-Audio-Aufnahme für RX-Monitoring.

    Nimmt kontinuierlich auf und speichert in einem Ringpuffer.
    Für die Dekodierung wird ein Snapshot des Puffers extrahiert.

    Verwendung:
        rx = AudioReceiver(device='USB Audio CODEC')
        rx.start()
        time.sleep(30)           # aufnehmen lassen
        audio = rx.get_snapshot(seconds=6)
        result = receive(audio, channel=2)
        rx.stop()

    Hinweis zur Kanalwahl:
        Phase 3 demoduliert einen Kanal auf einmal.
        Phase 4/GNU Radio: alle 10 Kanäle parallel via GNU Radio OOT-Block.
    """

    def __init__(self, device: "Union[int, str, None]" = None,
                 buffer_seconds: float = 60.0,
                 force_samplerate: "Optional[int]" = None):
        if not _SD_AVAILABLE:
            raise RuntimeError("sounddevice nicht installiert.")
        self.device           = device
        self._buf_seconds     = buffer_seconds
        self._force_sr        = force_samplerate   # None = auto aus Gerät
        self._buf_size        = int(buffer_seconds * SAMPLE_RATE)  # wird in start() korrigiert
        self._buffer          = np.zeros(self._buf_size, dtype=np.float32)
        self._write_pos       = 0
        self._total_written   = 0
        self._lock            = threading.Lock()
        self._stream          = None
        self._running         = False
        self._native_sr       = SAMPLE_RATE   # wird in start() gesetzt

    def _callback(self, indata, frames, time_info, status):
        """PortAudio Callback — wird im Audio-Thread aufgerufen."""
        if status:
            log.vital("[RX Audio] %s", status)
        mono = indata[:, 0] if indata.ndim > 1 else indata.ravel()
        # Callback bleibt minimal — kein Resampling hier (würde Overflow auf RPi 3 verursachen)
        # Resampling erfolgt in get_snapshot() beim Abholen
        n = len(mono)
        with self._lock:
            end = self._write_pos + n
            if end <= self._buf_size:
                self._buffer[self._write_pos:end] = mono
            else:
                # Überlauf: Ringpuffer wrap-around
                part1 = self._buf_size - self._write_pos
                self._buffer[self._write_pos:] = mono[:part1]
                self._buffer[:n - part1] = mono[part1:]
            self._write_pos   = end % self._buf_size
            self._total_written += n

    def start(self):
        """
        Audioaufnahme starten.

        Erkennt automatisch ob das Gerät Mono oder Stereo liefert.
        Bei Stereo-Geräten wird Kanal 0 (links) als Mono verwendet —
        typisch für USB-Audioadapter und IC-7610 USB-Audio.
        """
        # Gerät-Info abfragen: wie viele Eingangskanäle hat es?
        try:
            dev_info = sd.query_devices(self.device, kind='input')
            max_ch   = int(dev_info.get('max_input_channels', 1))
        except Exception:
            max_ch = 1

        channels = max(1, min(max_ch, 2))   # 1 oder 2 — nie mehr als 2 nötig

        if channels == 2:
            # Stereo-Gerät: Callback nimmt Kanal 0 (bereits in _callback via [:, 0])
            log.debug("[RX] Gerät liefert Stereo — verwende Kanal 0 (links) als Mono")

        # Native Samplerate: force_samplerate hat Vorrang vor Geräte-Default
        if self._force_sr:
            self._native_sr = self._force_sr
        else:
            try:
                dev_info = sd.query_devices(self.device, kind='input')
                self._native_sr = int(dev_info.get('default_samplerate', SAMPLE_RATE))
            except Exception:
                self._native_sr = SAMPLE_RATE

        # Ringpuffer auf native SR-Größe anpassen
        self._buf_size = int(self._buf_seconds * self._native_sr)
        self._buffer   = np.zeros(self._buf_size, dtype=np.float32)
        self._write_pos     = 0
        self._total_written = 0

        # blocksize in nativen Samples — entspricht 1 MFSK-Symbol (256 @ 8kHz)
        from math import gcd
        g         = gcd(SAMPLE_RATE, self._native_sr)
        blocksize = 256 * (self._native_sr // g) // (SAMPLE_RATE // g)

        self._stream = sd.InputStream(
            samplerate=self._native_sr,
            channels=channels,
            dtype="float32",
            device=self.device,
            callback=self._callback,
            blocksize=blocksize,
        )
        self._stream.start()
        self._running = True
        dev_name = self.device if self.device is not None else "Standard"
        sr_info  = (f"{self._native_sr} Hz → resample → {SAMPLE_RATE} Hz"
                    if self._native_sr != SAMPLE_RATE
                    else f"{SAMPLE_RATE} Hz (nativ)")
        log.debug("[RX] Aufnahme gestartet  |  Gerät: '%s'  |  "
                  "Kanäle: %d  |  SR: %s  |  Puffer: %ds",
                  dev_name, channels, sr_info,
                  self._buf_size // self._native_sr)

    def stop(self):
        """Audioaufnahme stoppen."""
        if self._running and self._stream:
            self._stream.stop()
            self._stream.close()
            self._running = False
            log.debug("[RX] Aufnahme gestoppt")

    def get_snapshot(self, seconds: float = 6.0) -> np.ndarray:
        """
        Gibt die letzten `seconds` Sekunden als Array zurück, resampelt auf 8000 Hz.
        Resampling erfolgt hier (nicht im Callback) um Input-Overflow zu vermeiden.
        """
        n = min(int(seconds * self._native_sr), self._buf_size)
        with self._lock:
            wp = self._write_pos
            # Zirkulär lesen: von (wp - n) bis wp
            start = (wp - n) % self._buf_size
            if start < wp:
                raw = self._buffer[start:wp].copy()
            else:
                raw = np.concatenate([self._buffer[start:], self._buffer[:wp]])

        # Resampling auf 8000 Hz falls nötig
        if self._native_sr != SAMPLE_RATE:
            from math import gcd
            from scipy.signal import resample_poly
            g   = gcd(SAMPLE_RATE, self._native_sr)
            up  = SAMPLE_RATE     // g
            dn  = self._native_sr // g
            raw = resample_poly(raw.astype(np.float64), up, dn).astype(np.float32)

        return raw

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()


# ═══════════════════════════════════════════════════════════════════════
# HILFSFUNKTIONEN
# ═══════════════════════════════════════════════════════════════════════

def list_audio_devices():
    """Alle verfügbaren Audiogeräte auflisten."""
    if not _SD_AVAILABLE:
        print("sounddevice nicht installiert. Installation: pip install sounddevice")
        return
    print("\nVerfügbare Audiogeräte:")
    print("─" * 70)
    devices = sd.query_devices()
    if not devices:
        print("  (keine Geräte gefunden — nur in dieser Testumgebung)")
    else:
        print(devices)
    try:
        print(f"\nStandard-Eingang:   Gerät {sd.default.device[0]}")
        print(f"Standard-Ausgang:   Gerät {sd.default.device[1]}")
    except Exception:
        pass
    print()


def ptt_test(ptt: PTTBackend, duration_s: float = 1.0):
    """
    Einfacher PTT-Funktionstest: activate → warten → release.

    Nützlich um die GPIO-Verkabelung oder rigctld-Verbindung zu prüfen
    bevor man das erste Mal auf Sendung geht.
    """
    print(f"\nPTT-Test ({ptt.__class__.__name__}): {duration_s}s Sendung")
    print("─" * 40)
    ptt.activate()
    for i in range(int(duration_s * 10)):
        time.sleep(0.1)
        print(f"\r  TX: {(i+1)*0.1:.1f}s", end="", flush=True)
    ptt.release()
    print("\n─" * 40)
    print("PTT-Test abgeschlossen ✓")


# ═══════════════════════════════════════════════════════════════════════
# SELBSTTEST & CLI
# ═══════════════════════════════════════════════════════════════════════

def _run_demo():
    """
    Phase-3 Demo: Wetter-Frame erzeugen und via NullPTT 'senden'.
    Dient als Loopback-Test ohne Funkgerät.
    """
    from gust_frame import encode_weather

    print("=" * 60)
    print("  GUST Audio TX — Phase 3 Demo")
    print("=" * 60)

    list_audio_devices()

    print("\n── Demo: Wetter-Frame via NullPTT ──")
    payload = encode_weather(
        temp_c=21.5, humidity_pct=68, pressure_hpa=1013.2,
        wind_kmh=15, wind_deg=270, rain_mm_h=0.2, uv_index=3
    )

    ptt = NullPTT(verbose=True)

    # Ohne sounddevice: nur die Audio-Erzeugung zeigen
    audio, channel, frame_body = transmit(
        FrameType.WEATHER, "OE3GAS", payload,
        channel=None, use_fec=True, window=True, add_silence_ms=0
    )
    from gust_frame import channel_frequency, CHANNEL_BW_HZ
    base = channel_frequency(channel)
    print(f"\n  Frame erzeugt:")
    print(f"    Rufzeichen:  OE3GAS")
    print(f"    Kanal:       {channel}  ({base:.0f}–{base+CHANNEL_BW_HZ:.0f} Hz NF)")
    print(f"    Frame-Body:  {len(frame_body)} Byte")
    print(f"    Audio:       {len(audio)} Samples = {len(audio)/SAMPLE_RATE:.2f}s")
    print(f"    Fensterung:  Raised Cosine (window=True)")

    print(f"\n  Simulierte Übertragung:")
    ptt.activate()
    time.sleep(PTT_LEAD_S)
    print(f"    [Audio wird gespielt ...]  ({len(audio)/SAMPLE_RATE:.2f}s)")
    time.sleep(0.050)   # kurze Simulation
    time.sleep(PTT_TAIL_S)
    ptt.release()

    print(f"\n  sounddevice verfügbar: {'ja' if _SD_AVAILABLE else 'nein'}")
    print(f"  RPi.GPIO verfügbar:    {'ja' if _GPIO_AVAILABLE else 'nein (kein RPi)'}")

    print("\n── Verwendung mit Hardware ──")
    print("""
  # IC-7610 via hamlib rigctld:
  from gust_audio import HamlibPTT, AudioTransmitter
  from gust_frame  import encode_weather, FrameType

  ptt = HamlibPTT(host='localhost', port=4532)
  tx  = AudioTransmitter(ptt=ptt, device='USB Audio CODEC')

  payload = encode_weather(21.5, 68, 1013.2, 15, 270)
  tx.transmit_frame(FrameType.WEATHER, 'OE3GAS', payload)
  tx.close()

  # Raspberry Pi GPIO:
  from gust_audio import GPIUPTT, AudioTransmitter
  ptt = GPIUPTT(pin=17)
  tx  = AudioTransmitter(ptt=ptt, device=None)  # Standard-Ausgang
  tx.transmit_frame(FrameType.WEATHER, 'OE3GAS', payload)
  tx.close()
""")
    print("=" * 60)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="gust_audio.py",
        description=(
            "GUST Audio — Gerätelist und TX-Loopback-Test\n"
            "\n"
            "Ohne Parameter: Hinweis auf --help.\n"
            "Mit --list: Alle verfügbaren Audio-Geräte mit IDs ausgeben.\n"
            "Mit --demo: Demo-Übertragung mit NullPTT (kein Funkgerät nötig).\n"
            "Mit --ptt-test: PTT-Funktion via rigctld prüfen.\n"
            "\n"
            "Hauptverwendung: Modul-Import durch gust.py. Direktaufruf zur\n"
            "Diagnose von Audio-Gerät und PTT-Backend."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--list", "--list-devices", dest="list", action="store_true",
        help="Verfügbare Audio-Geräte mit IDs auflisten "
             "(Integer-ID für gateway.json)"
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Demo-Übertragung mit NullPTT (kein Funkgerät, kein hamlib nötig)"
    )
    parser.add_argument(
        "--ptt-test", action="store_true",
        help="PTT-Funktion über rigctld testen (Toggle key down/up)"
    )
    parser.add_argument(
        "--hamlib-host", default=RIGCTLD_HOST_DEFAULT, metavar="HOST",
        help=f"rigctld Hostname/IP (Standard: {RIGCTLD_HOST_DEFAULT})"
    )
    parser.add_argument(
        "--hamlib-port", type=int, default=RIGCTLD_PORT_DEFAULT, metavar="PORT",
        help=f"rigctld TCP-Port (Standard: {RIGCTLD_PORT_DEFAULT})"
    )

    # No-Args-Hint — vor parse_args()
    if len(sys.argv) == 1:
        print("Verwendung: python gust_audio.py -h  oder  --help  für Parameterübersicht")
        sys.exit(0)

    args = parser.parse_args()

    if args.list:
        list_audio_devices()
    elif args.ptt_test:
        try:
            ptt = HamlibPTT(args.hamlib_host, args.hamlib_port)
            ptt_test(ptt)
            ptt.close()
        except RuntimeError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)
    else:
        _run_demo()


if __name__ == "__main__":
    main()