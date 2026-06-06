#!/usr/bin/env python3
"""
GUST — Generischer SoapySDR-TX-Pfad                        P7-04 / ADR-16
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Version : 1.0.0
Datum   : Mai 2026

Dünne, treiberneutrale TX-Schicht über SoapySDR. Ersetzt die Festlegung
auf einen einzelnen Treiber (HackRF, Soapy7610 …) durch reine Discovery —
`SoapySDR.Device.enumerate()` liefert alle Kandidaten, persistiert wird
die Identität (driver + serial/label), NICHT der Listenindex (ADR-09 +
ADR-16). „7610" ist kein Sonderfall mehr, sondern nur ein Args-Satz.

Write-Loop, Block-Größe und Default-Timeout sind exakt aus
`gust_hackrf.HackRFTransmitter.transmit_iq()` übernommen (ADR-13):
ein langer `writeStream`-Timeout hatte beim ersten Lauf zu einem TX-
Underrun mit Firmware-Hänger geführt — diese Disziplin gilt generisch.

Beispiel:
    from gust_soapy_tx import SoapyTxBackend, enumerate_tx_devices

    for d in enumerate_tx_devices():
        print(d["label"], d["tx_capable"], d["num_tx_channels"])

    with SoapyTxBackend(
        device_args={"driver": "hackrf"},
        freq_hz=14_110_000.0, sample_rate=2_000_000,
        antenna="TX/RX", gain={"normalized": 0.5},
    ) as tx:
        tx.transmit_iq(iq_complex64)        # vorgemixt
        tx.transmit(audio_nf_8khz_float32)  # NF → USB-IQ → senden

CLI-Selbsttest (ohne Hardware unproblematisch — leere Liste):
    py gust_soapy_tx.py --probe
    py gust_soapy_tx.py --caps driver=hackrf
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import numpy as np

# SoapySDR ist optional — Modul muss auch ohne Bindings importierbar sein,
# damit gust_web.py einen Status-Endpoint anbieten kann ohne hart zu fehlen.
try:
    import SoapySDR
    from SoapySDR import SOAPY_SDR_TX, SOAPY_SDR_CF32
    _SOAPY_AVAILABLE = True
except ImportError:
    _SOAPY_AVAILABLE = False
    SoapySDR = None              # type: ignore[assignment]
    SOAPY_SDR_TX = SOAPY_SDR_CF32 = None  # type: ignore[assignment]


log = logging.getLogger("gust.soapy_tx")

# ─── KONSTANTEN ────────────────────────────────────────────────────────
# Aus dem bewährten HackRF-TX-Pfad übernommen (ADR-13).
WRITE_BLOCK = 4096   # writeStream-Blockgröße — bewusst klein
FLUSH_S     = 0.1    # Null-Padding am Ende, leert den Geräte-Puffer sauber

# Identitäts-Schlüssel in Bevorzugungsreihenfolge. „serial" ist stabil über
# Reboots/USB-Replug; manche Treiber liefern nur „label" oder „device_id".
_ID_KEYS = ("serial", "label", "device_id")


# ═══════════════════════════════════════════════════════════════════════
# ARGS-HELFER
# ═══════════════════════════════════════════════════════════════════════

def _args_dict(args: Any) -> Dict[str, str]:
    """
    Vereinheitlicht alles, was als Device-Args daherkommt, zu einem
    str→str-Dict, das `SoapySDR.Device(...)` direkt verdaut.
    Akzeptiert: dict, Kwargs-Objekt aus enumerate(), oder String
    der Form 'driver=hackrf,serial=…'.
    """
    if args is None:
        return {}
    if isinstance(args, dict):
        return {str(k): str(v) for k, v in args.items()}
    if isinstance(args, str):
        out: Dict[str, str] = {}
        for piece in args.split(","):
            piece = piece.strip()
            if "=" in piece:
                k, v = piece.split("=", 1)
                out[k.strip()] = v.strip()
        return out
    # SoapySDR Kwargs-Objekt o.ä. — ist dict-konvertierbar
    try:
        return {str(k): str(v) for k, v in dict(args).items()}
    except Exception:
        return {}


def _args_identity(args: Any) -> Dict[str, str]:
    """
    Liefert die stabile Identität eines Gerätes: driver + bestes verfügbares
    Stabilitäts-Feld. Wird in `gateway.json` persistiert — vgl. ADR-16.
    """
    a = _args_dict(args)
    out: Dict[str, str] = {}
    if "driver" in a:
        out["driver"] = a["driver"]
    for k in _ID_KEYS:
        if k in a:
            out[k] = a[k]
            break
    return out


def _args_label(args: Any) -> str:
    """Kurzes Lesbar-Label für UI/Log."""
    a = _args_dict(args)
    drv = a.get("driver", "?")
    if "label" in a:
        return str(a["label"])
    for k in ("serial", "device_id"):
        if k in a:
            return f"{drv} ({a[k]})"
    return drv


# ═══════════════════════════════════════════════════════════════════════
# MODUL-FUNKTIONEN: ENUMERATION, DIAGNOSE, CAPABILITIES
# ═══════════════════════════════════════════════════════════════════════

def soapy_available() -> bool:
    """Public-Check: sind die SoapySDR-Python-Bindings importierbar?"""
    return _SOAPY_AVAILABLE


def enumerate_tx_devices() -> List[Dict[str, Any]]:
    """
    Alle via SoapySDR auffindbaren Geräte; jedes Gerät wird kurz geöffnet,
    um `getNumChannels(SOAPY_SDR_TX)` für den TX-Capability-Filter zu lesen
    (RX-only-Geräte wie RTL-SDR ⇒ `tx_capable: false`).

    Niemals exception-werfend: ohne SoapySDR oder ohne Gerät → leere Liste.
    Pro Gerät enthält das Result-Dict zumindest:
        args, identity, label, driver, serial,
        tx_capable, num_tx_channels, probe_error
    """
    if not _SOAPY_AVAILABLE:
        return []
    try:
        raw = SoapySDR.Device.enumerate()
    except Exception as exc:
        log.warning("SoapySDR.Device.enumerate() Fehler: %s", exc)
        return []

    out: List[Dict[str, Any]] = []
    for args in raw:
        info = _args_dict(args)
        num_tx = 0
        tx_capable = False
        probe_err: Optional[str] = None
        dev = None
        try:
            dev = SoapySDR.Device(info)
            num_tx = int(dev.getNumChannels(SOAPY_SDR_TX))
            tx_capable = num_tx > 0
        except Exception as exc:
            probe_err = str(exc)
        finally:
            if dev is not None:
                try:
                    del dev
                except Exception:
                    pass
        out.append({
            "args":            info,
            "identity":        _args_identity(info),
            "label":           info.get("label") or _args_label(info),
            "driver":          info.get("driver", ""),
            "serial":          info.get("serial", ""),
            "num_tx_channels": num_tx,
            "tx_capable":      tx_capable,
            "probe_error":     probe_err,
        })
    return out


def enumerate_all_devices() -> List[Dict[str, Any]]:
    """
    Alle via SoapySDR auffindbaren Geräte mit RX- UND TX-Fähigkeit.
    Öffnet jedes Gerät kurz um getNumChannels(RX) und
    getNumChannels(TX) zu lesen.

    Result-Dict pro Gerät:
        args, identity, label, driver, serial,
        rx_capable, num_rx_channels,
        tx_capable, num_tx_channels,
        type ("rx"|"tx"|"trx"|"unknown"),
        probe_error
    Niemals exception-werfend.
    """
    if not _SOAPY_AVAILABLE:
        return []
    try:
        raw = SoapySDR.Device.enumerate()
    except Exception as exc:
        log.warning("SoapySDR.Device.enumerate() Fehler: %s", exc)
        return []

    # SOAPY_SDR_RX importieren — analog zu SOAPY_SDR_TX
    try:
        from SoapySDR import SOAPY_SDR_RX as _RX
    except ImportError:
        _RX = 0   # Fallback-Konstante

    out: List[Dict[str, Any]] = []
    for args in raw:
        info = _args_dict(args)
        num_rx = 0
        num_tx = 0
        probe_err: Optional[str] = None
        dev = None
        try:
            dev = SoapySDR.Device(info)
            num_rx = int(dev.getNumChannels(_RX))
            num_tx = int(dev.getNumChannels(SOAPY_SDR_TX))
        except Exception as exc:
            probe_err = str(exc)
        finally:
            if dev is not None:
                try:
                    del dev
                except Exception:
                    pass

        rx_cap = num_rx > 0
        tx_cap = num_tx > 0
        if rx_cap and tx_cap:
            dev_type = "trx"
        elif rx_cap:
            dev_type = "rx"
        elif tx_cap:
            dev_type = "tx"
        else:
            dev_type = "unknown"

        out.append({
            "args":            info,
            "identity":        _args_identity(info),
            "label":           info.get("label") or _args_label(info),
            "driver":          info.get("driver", ""),
            "serial":          info.get("serial", ""),
            "num_rx_channels": num_rx,
            "num_tx_channels": num_tx,
            "rx_capable":      rx_cap,
            "tx_capable":      tx_cap,
            "type":            dev_type,
            "probe_error":     probe_err,
        })
    return out


def list_modules() -> List[Dict[str, str]]:
    """
    Geladene SoapySDR-Treiber-Module (+ Version, falls verfügbar) —
    rein diagnostische Anzeige (ADR-16: kein Eingabefeld).
    """
    if not _SOAPY_AVAILABLE:
        return []
    try:
        mods = SoapySDR.listModules()
    except Exception as exc:
        log.warning("SoapySDR.listModules() Fehler: %s", exc)
        return []

    out: List[Dict[str, str]] = []
    for path in mods:
        ver = ""
        try:
            ver = SoapySDR.getModuleVersion(path) or ""
        except Exception:
            pass
        out.append({"path": str(path), "version": str(ver)})
    return out


def device_capabilities(args: Any, channel: int = 0) -> Dict[str, Any]:
    """
    Treiberabhängige Parameter eines Gerätes nach Auswahl (Gain-Elemente,
    Sample-Rate-Bereich/Liste, Antennen, Frequenzbereich).

    Wirft `RuntimeError`, wenn SoapySDR fehlt oder das Gerät nicht öffenbar
    ist — der Aufrufer (REST-Handler) übersetzt das in 4xx/5xx.
    """
    if not _SOAPY_AVAILABLE:
        raise RuntimeError("SoapySDR nicht installiert.")
    info = _args_dict(args)
    dev = SoapySDR.Device(info)
    try:
        try:
            num_tx = int(dev.getNumChannels(SOAPY_SDR_TX))
        except Exception:
            num_tx = 0

        # Gain-Elemente (treiberabhängig — HackRF z.B. ["AMP","VGA"])
        try:
            gain_names = [str(n) for n in dev.listGains(SOAPY_SDR_TX, channel)]
        except Exception:
            gain_names = []
        elements: List[Dict[str, Any]] = []
        for name in gain_names:
            try:
                rng = dev.getGainRange(SOAPY_SDR_TX, channel, name)
                elements.append({
                    "name": name,
                    "min":  float(rng.minimum()),
                    "max":  float(rng.maximum()),
                    "step": float(rng.step()) if rng.step() else 0.0,
                })
            except Exception:
                elements.append({"name": name, "min": None, "max": None, "step": None})

        # Gesamt-Gain-Range — Grundlage für „normalisiert 0–1"
        try:
            rng = dev.getGainRange(SOAPY_SDR_TX, channel)
            overall = {
                "min":  float(rng.minimum()),
                "max":  float(rng.maximum()),
                "step": float(rng.step()) if rng.step() else 0.0,
            }
        except Exception:
            overall = None

        # Sample-Rates
        sr_list: List[float] = []
        try:
            sr_list = [float(r) for r in dev.listSampleRates(SOAPY_SDR_TX, channel)]
        except Exception:
            pass
        sr_ranges: List[Dict[str, float]] = []
        try:
            for r in dev.getSampleRateRange(SOAPY_SDR_TX, channel):
                sr_ranges.append({
                    "min":  float(r.minimum()),
                    "max":  float(r.maximum()),
                    "step": float(r.step()) if r.step() else 0.0,
                })
        except Exception:
            pass

        # Antennen-Ports
        antennas: List[str] = []
        try:
            antennas = [str(a) for a in dev.listAntennas(SOAPY_SDR_TX, channel)]
        except Exception:
            pass

        # Frequenzbereich (rein informativ — wird im Backend nicht erzwungen)
        freq_ranges: List[Dict[str, float]] = []
        try:
            for r in dev.getFrequencyRange(SOAPY_SDR_TX, channel):
                freq_ranges.append({"min": float(r.minimum()),
                                    "max": float(r.maximum())})
        except Exception:
            pass

        return {
            "num_tx_channels":    num_tx,
            "gain_elements":      elements,
            "gain_overall":       overall,
            "sample_rates":       sr_list,
            "sample_rate_ranges": sr_ranges,
            "antennas":           antennas,
            "frequency_ranges":   freq_ranges,
        }
    finally:
        try:
            del dev
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════
# SOAPY-TX-BACKEND
# ═══════════════════════════════════════════════════════════════════════

class SoapyTxBackend:
    """
    Hält ein SoapySDR-Device-Handle und kennt einen TX-Stream-Lifecycle.

    Geräteparameter werden im Konstruktor entgegen­genommen und beim
    `open()` aufs Device angewandt; `transmit_iq()` öffnet/schließt den
    Stream pro Aussendung — exakt dieselbe Disziplin wie der bewährte
    `gust_hackrf.HackRFTransmitter.transmit_iq()` (ADR-13).

    Args:
        device_args: dict | str — was an `SoapySDR.Device(...)` geht
        freq_hz:     TX-Mittenfrequenz in Hz
        sample_rate: TX-Sample-Rate in Hz
        channel:     TX-Kanal-Index (i.d.R. 0)
        antenna:     Antennen-Port (treiberabhängig) — None = Default
        gain:        dict: {"normalized": 0..1}  oder
                          {"elements": {"VGA": 14, "AMP": 0}}  oder
                          {"overall_db": <float>}
    """

    def __init__(
        self,
        device_args:  Any,
        freq_hz:      float,
        sample_rate:  float,
        channel:      int            = 0,
        antenna:      Optional[str]  = None,
        gain:         Optional[Dict] = None,
    ):
        if not _SOAPY_AVAILABLE:
            raise RuntimeError(
                "SoapySDR nicht installiert.\n"
                "Installation (Windows): PothosSDR + Python 3.9 verwenden;\n"
                "Linux: apt install python3-soapysdr soapysdr-module-…"
            )
        self._args        = _args_dict(device_args)
        self._freq_hz     = float(freq_hz)
        self._sr          = float(sample_rate)
        self._ch          = int(channel)
        self._antenna     = antenna
        self._gain        = gain or {}
        self._sdr         = None  # type: ignore[assignment]

    # ── Eigenschaften ────────────────────────────────────────────────
    @property
    def sample_rate(self) -> float:
        return self._sr

    @property
    def device_args(self) -> Dict[str, str]:
        return dict(self._args)

    # ── Lifecycle ────────────────────────────────────────────────────
    def open(self) -> None:
        if self._sdr is not None:
            return
        log.info("Soapy TX öffnen: %s", _args_label(self._args))
        self._sdr = SoapySDR.Device(self._args)

        ntx = int(self._sdr.getNumChannels(SOAPY_SDR_TX))
        if ntx <= 0:
            self.close()
            raise RuntimeError(
                f"Gerät hat keinen TX-Kanal: {_args_label(self._args)}"
            )
        if self._ch >= ntx:
            self.close()
            raise RuntimeError(
                f"TX-Kanal {self._ch} ungültig (Gerät hat {ntx} Kanäle)."
            )

        self._sdr.setSampleRate(SOAPY_SDR_TX, self._ch, self._sr)
        self._sdr.setFrequency (SOAPY_SDR_TX, self._ch, self._freq_hz)
        if self._antenna:
            try:
                self._sdr.setAntenna(SOAPY_SDR_TX, self._ch, str(self._antenna))
            except Exception as exc:
                log.warning("setAntenna(%r) fehlgeschlagen: %s",
                            self._antenna, exc)

        self._apply_gain()

        log.info("Soapy TX bereit: SR=%.0f Hz  Freq=%.6f MHz  Ant=%s",
                 self._sr, self._freq_hz / 1e6,
                 self._antenna or "(default)")

    def close(self) -> None:
        if self._sdr is not None:
            try:
                del self._sdr
            except Exception:
                pass
            self._sdr = None

    def __enter__(self) -> "SoapyTxBackend":
        self.open()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ── Gain ─────────────────────────────────────────────────────────
    def _apply_gain(self) -> None:
        """
        Drei Modi (in dieser Priorität):
          1. normalized: 0..1 → auf Gesamt-Gain-Range gemappt
          2. elements:   {Name: dB}-Map (HackRF: AMP/VGA, andere Treiber: …)
          3. overall_db: einzelner Float, direkt gesetzt
        Verstehen ist treiberabhängig — siehe ADR-16, daher generös loggen
        statt Bei-Fehler-aussteigen.
        """
        g = self._gain or {}

        if "normalized" in g and g["normalized"] is not None:
            v = float(g["normalized"])
            v = max(0.0, min(1.0, v))
            try:
                rng = self._sdr.getGainRange(SOAPY_SDR_TX, self._ch)
                lo, hi = float(rng.minimum()), float(rng.maximum())
                dB = lo + (hi - lo) * v
                self._sdr.setGain(SOAPY_SDR_TX, self._ch, dB)
                log.info("Gain (normalisiert) %.2f → %.1f dB (Range %.0f..%.0f)",
                         v, dB, lo, hi)
                return
            except Exception as exc:
                log.warning("Gain normalisiert fehlgeschlagen: %s", exc)

        if isinstance(g.get("elements"), dict):
            for name, val in g["elements"].items():
                try:
                    self._sdr.setGain(SOAPY_SDR_TX, self._ch, str(name), float(val))
                    log.info("Gain[%s] = %.1f", name, float(val))
                except Exception as exc:
                    log.warning("Gain[%s] = %s fehlgeschlagen: %s", name, val, exc)

        if "overall_db" in g and g["overall_db"] is not None:
            try:
                self._sdr.setGain(SOAPY_SDR_TX, self._ch, float(g["overall_db"]))
                log.info("Gain (overall) = %.1f dB", float(g["overall_db"]))
            except Exception as exc:
                log.warning("Gain overall_db fehlgeschlagen: %s", exc)

    # ── IQ-Senden ────────────────────────────────────────────────────
    def transmit_iq(self, iq: np.ndarray) -> None:
        """
        Vorgemixtes Complex64-IQ senden.

        Schreibschleife EXAKT wie `gust_hackrf.HackRFTransmitter.transmit_iq()`
        (ADR-13): Default-`writeStream`-Timeout, Block=4096,
        `pos += sr.ret if sr.ret > 0 else BLOCK`. Kein langer Timeout —
        sonst Underrun → Firmware-Hänger.
        """
        if self._sdr is None:
            self.open()

        flush = int(self._sr * FLUSH_S)
        iq = np.concatenate([
            iq.astype(np.complex64),
            np.zeros(flush, dtype=np.complex64),
        ])

        duration = len(iq) / self._sr
        log.info("Soapy TX: %d Samples @ %.0f Hz (%.2f s)",
                 len(iq), self._sr, duration)

        stream = self._sdr.setupStream(SOAPY_SDR_TX, SOAPY_SDR_CF32, [self._ch])
        self._sdr.activateStream(stream)
        try:
            pos = 0
            n   = len(iq)
            while pos < n:
                block = iq[pos:pos + WRITE_BLOCK]
                sr    = self._sdr.writeStream(stream, [block], len(block))
                pos  += sr.ret if sr.ret > 0 else WRITE_BLOCK
        finally:
            self._sdr.deactivateStream(stream)
            self._sdr.closeStream(stream)

    def transmit(self, audio_nf: np.ndarray) -> None:
        """
        NF (8 kHz float) → USB-SSB-IQ (über `gust_hackrf.nf_to_iq_usb`) → senden.
        Bequemlichkeit für den Aufrufer; identisch zu manuellem
        `transmit_iq(nf_to_iq_usb(audio_nf, sample_rate))`.
        """
        # Lokaler Import: gust_hackrf hat ggf. eigene optionale Abhängigkeiten,
        # die wir nicht erzwingen wollen, nur weil das Modul geladen wird.
        from gust_hackrf import nf_to_iq_usb
        iq = nf_to_iq_usb(audio_nf, int(self._sr))
        self.transmit_iq(iq)


# ═══════════════════════════════════════════════════════════════════════
# CLI / SELBSTTEST
# ═══════════════════════════════════════════════════════════════════════

def _main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        prog="gust_soapy_tx.py",
        description="GUST generischer SoapySDR-TX — Diagnose-CLI",
    )
    p.add_argument("--probe", action="store_true",
                   help="enumerate_tx_devices() + list_modules() als JSON")
    p.add_argument("--caps", metavar="ARGS",
                   help="device_capabilities für ARGS-String "
                        "(z.B. 'driver=hackrf' oder 'driver=hackrf,serial=…')")
    args = p.parse_args()

    if not _SOAPY_AVAILABLE:
        print(json.dumps({"error": "SoapySDR nicht installiert"}, indent=2))
        return

    if args.caps:
        try:
            caps = device_capabilities(args.caps)
        except Exception as exc:
            caps = {"error": str(exc)}
        print(json.dumps(caps, indent=2))
        return

    # Default = --probe
    out = {
        "soapy_api_version": SoapySDR.getAPIVersion(),
        "modules":           list_modules(),
        "devices":           enumerate_tx_devices(),
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    _main()
