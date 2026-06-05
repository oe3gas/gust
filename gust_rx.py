#!/usr/bin/env python3
"""
GUST — Kontinuierlicher RX-Loop                            Phase 7
═══════════════════════════════════════════════════════════════════════
Autor   : OE3GAS
Version : 1.0.0
Datum   : Mai 2026

Dieses Modul implementiert den dauerhaften Audio-Empfang für den
Gateway- und Monitor-Betrieb.

── Architektur ────────────────────────────────────────────────────────

  Soundkarte (AudioReceiver)
       │  kontinuierlicher Ringpuffer, 8000 Hz
       ▼
  Scan-Tick (alle SCAN_INTERVAL_S Sekunden)
       │  get_snapshot(WINDOW_S) → np.ndarray
       ▼
  ThreadPoolExecutor
       │  receive() — CPU-intensiv, blockiert asyncio nicht
       ▼
  Deduplication-Cache
       │  (callsign, frame_type, payload_crc) mit TTL
       ▼
  EventBus.publish(make_rx_frame_event(...))
       │
       ▼  WebSocket /ws/rx → Browser-Dashboard
          Logfile, MQTT (spätere Phase)

── Überlappende Fenster ──────────────────────────────────────────────

  Scan-Intervall: 2,0 s  (kürzere als halbe Frame-Dauer ~4,9 s)
  Fenstergröße:   8,0 s  (größer als maximale Frame-Dauer ~5,5 s)

  Ein Frame der bei t=3,0 s beginnt wird spätestens beim Scan bei
  t=8,0 s vollständig im Fenster sein — selbst wenn er erst bei
  t=6,5 s endet. Kein Frame geht verloren.

── TX-Muting ─────────────────────────────────────────────────────────

  Während einer eigenen Sendung wird kein Decode-Versuch gestartet.
  Aufruf: rx_loop.mute() vor PTT, rx_loop.unmute() nach PTT.
  Hintergrund: Der eigene TX-Ton würde den Decoder beschäftigen,
  der Frame würde als eigener empfangener Frame fehlgedeutet.

── Deduplication ─────────────────────────────────────────────────────

  Schlüssel: (callsign, frame_type, payload_hash_16bit)
  TTL:       DEDUP_TTL_S (Standard: 30 Sekunden)

  Derselbe Wetter-Frame von OE3GAS wird innerhalb von 30 Sekunden
  nur einmal in den EventBus publiziert — unabhängig davon wie viele
  Scan-Fenster ihn enthalten.

── Konfiguration (gateway.json) ──────────────────────────────────────

  "rx": {
      "device":           null,     Audiogeräte-ID (null = Standard)
      "scan_interval_s":  2.0,      Sekunden zwischen Scan-Versuchen
      "window_s":         9.0,      Audiohistorie pro Versuch (>= MAX_FRAME_S + Intervall)
      "dedup_ttl_s":      30,       Dedup-Fenster in Sekunden
      "enabled":          true      false = RX-Loop nicht starten
  }

── Verwendung in gust.py ──────────────────────────────────────────

  from gust_rx import AudioRXLoop

  rx = AudioRXLoop(
      device          = cfg["audio"].get("rx_device"),
      event_bus       = bus,
      scan_interval_s = cfg["rx"].get("scan_interval_s", 2.0),
      window_s        = cfg["rx"].get("window_s", 9.0),
      dedup_ttl_s     = cfg["rx"].get("dedup_ttl_s", 30),
  )
  asyncio.create_task(rx.run())

  # Während TX:
  rx.mute()    # vor PTT-activate
  rx.unmute()  # nach PTT-release
"""

import asyncio
import hashlib
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional, Union

import numpy as np

log = logging.getLogger("gust.rx")


# ═══════════════════════════════════════════════════════════════════════
# STANDARD-PARAMETER
# ═══════════════════════════════════════════════════════════════════════

SCAN_INTERVAL_S  = 2.0    # Sekunden zwischen Scan-Versuchen (Capture-Kadenz)
MAX_FRAME_S      = 5.5    # längste Framedauer (TEXT-Fragment ~5,4s) + Reserve
WINDOW_S         = 9.0    # Audiohistorie pro Versuch
                          # Vollfenster-Garantie: WINDOW_S >= MAX_FRAME_S + SCAN_INTERVAL_S
                          # (9,0 >= 5,5 + 2,0 = 7,5 → Marge 1,5s). Zusammen mit der
                          # Fixed-Cadence-Schleife (Decode-Zeit bläht das Intervall nicht
                          # auf) ist damit jede Sendung in >= 1 Scan komplett enthalten.
DEDUP_TTL_S      = 30     # Dedup-Cache TTL in Sekunden
MIN_AUDIO_LEVEL  = 0.001  # Mindest-RMS um Stille zu überspringen


def _ts() -> str:
    """Aktueller Timestamp für Konsolenausgabe."""
    return datetime.now().strftime("%H:%M:%S.%f")[:12]


# ═══════════════════════════════════════════════════════════════════════
# SNR-MESSUNG
# ═══════════════════════════════════════════════════════════════════════

def _measure_audio_snr(audio: np.ndarray, f0_hz: float) -> float:
    """
    Schätzt den Audio-SNR des GUST-Signals im übergebenen Snapshot.

    Args:
        audio:  float32 Audio-Array (8000 Hz)
        f0_hz:  Tatsächliche Ton-0-Frequenz (= channel_frequency(ch) + offset)

    Returns:
        SNR in dB (typisch 5–25 dB für empfangbare Signale)
    """
    try:
        from scipy.signal import welch
        SAMPLE_RATE   = 8000
        TONE_SPACING  = 31.25
        HALF_BW       = 8.0

        freqs, psd = welch(audio, SAMPLE_RATE, nperseg=min(8192, len(audio)))

        tones = [f0_hz + i * TONE_SPACING for i in range(8)]
        sig_vals = []
        for t in tones:
            if t < 100 or t > 3800:
                continue
            mask = (freqs >= t - HALF_BW) & (freqs <= t + HALF_BW)
            if np.any(mask):
                sig_vals.append(float(np.mean(psd[mask])))

        if not sig_vals:
            return 0.0

        sig_power = float(np.mean(sig_vals))

        # Rauschband adaptiv relativ zum Signalband [f0, f0+218.75 Hz].
        # Auf BEIDEN Seiten messen (mit Guard-Abstand) und die NIEDRIGERE
        # Schätzung nehmen. So wird eine durch Signal-Leakage (z.B. Kanal 0
        # an der unteren Bandkante) oder durch einen Nachbarkanal
        # kontaminierte Seite automatisch verworfen.
        GUARD   = 80.0    # Hz Abstand zum Signalband (klärt den Symbol-Skirt)
        NOISE_W = 150.0   # Hz Breite des Rauschbands je Seite
        sig_lo  = f0_hz
        sig_hi  = f0_hz + 7 * TONE_SPACING

        noise_candidates = []
        for lo, hi in ((sig_lo - GUARD - NOISE_W, sig_lo - GUARD),   # unterhalb
                       (sig_hi + GUARD, sig_hi + GUARD + NOISE_W)):  # oberhalb
            lo = max(80.0, lo)
            hi = min(3900.0, hi)
            if hi - lo < 30.0:            # zu schmal / außerhalb → überspringen
                continue
            m = (freqs >= lo) & (freqs <= hi)
            if np.any(m):
                noise_candidates.append(float(np.mean(psd[m])))

        if not noise_candidates:
            return 0.0
        noise_power = min(noise_candidates)   # sauberere Seite gewinnt

        if noise_power <= 0:
            return 0.0

        return round(10.0 * np.log10(sig_power / noise_power), 1)

    except Exception:
        return 0.0


# ═══════════════════════════════════════════════════════════════════════
# DEDUPLICATION-CACHE
# ═══════════════════════════════════════════════════════════════════════

class _DedupCache:
    """
    Einfacher TTL-Cache für Frame-Deduplication.

    Schlüssel: (callsign: str, frame_type: int, payload_hash: str)
    Wert:      Empfangszeitpunkt (float, time.monotonic())

    Veraltete Einträge werden beim nächsten lookup() bereinigt.
    """

    def __init__(self, ttl_s: float = DEDUP_TTL_S):
        self._ttl   = ttl_s
        self._cache: dict[tuple, float] = {}

    def is_duplicate(self, callsign: str, frame_type: int,
                     payload_raw: bytes) -> bool:
        """
        Gibt True zurück wenn dieser Frame innerhalb der TTL bereits gesehen wurde.
        Registriert den Frame gleichzeitig (side-effect).
        """
        self._evict()
        key = self._key(callsign, frame_type, payload_raw)
        now = time.monotonic()
        if key in self._cache:
            return True
        self._cache[key] = now
        return False

    def _key(self, callsign: str, frame_type: int,
             payload_raw: bytes) -> tuple:
        h = hashlib.md5(payload_raw).hexdigest()[:8]
        return (callsign.upper(), int(frame_type), h)

    def _evict(self):
        now = time.monotonic()
        expired = [k for k, t in self._cache.items()
                   if now - t > self._ttl]
        for k in expired:
            del self._cache[k]

    @property
    def size(self) -> int:
        return len(self._cache)


# ═══════════════════════════════════════════════════════════════════════
# AUDIO-RX-LOOP
# ═══════════════════════════════════════════════════════════════════════

class AudioRXLoop:
    """
    Kontinuierlicher RX-Decode-Loop als asyncio Task.

    Startet einen AudioReceiver (Ringpuffer), scannt ihn periodisch,
    dekodiert via receive() im Thread-Pool und publiziert CRC-OK-Frames
    in den EventBus.

    Args:
        device:          Audiogeräte-ID (int/str) oder None für Standard
        event_bus:       EventBus-Instanz für Frame-Events
        scan_interval_s: Sekunden zwischen Scan-Versuchen
        window_s:        Audiohistorie (Ringpuffer-Snapshot) pro Versuch
        dedup_ttl_s:     TTL des Dedup-Caches in Sekunden
        executor:        ThreadPoolExecutor (None = eigener mit 1 Worker)
    """

    def __init__(
        self,
        device:          Optional[Union[int, str]] = None,
        event_bus                         = None,
        scan_interval_s: float            = SCAN_INTERVAL_S,
        window_s:        float            = WINDOW_S,
        dedup_ttl_s:     float            = DEDUP_TTL_S,
        executor:        Optional[ThreadPoolExecutor] = None,
        force_samplerate: Optional[int]   = None,
    ):
        self._device     = device
        self._bus        = event_bus
        self._interval   = scan_interval_s
        self._window     = window_s
        self._force_sr   = force_samplerate
        self._dedup      = _DedupCache(ttl_s=dedup_ttl_s)
        self._executor   = executor or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="oe3rx"
        )

        self._muted      = False     # True während eigenem TX
        self._running    = False
        self._scan_count = 0         # Gesamtzahl Scan-Versuche
        self._rx_count   = 0         # Erfolgreich dekodierte Frames
        self._dup_count  = 0         # Unterdrückte Duplikate
        self._no_sync_count = 0    # Scans ohne SYNC seit letztem Frame

        # Letztes Decode-Ergebnis für Diagnose
        self._last_result: Optional[dict] = None
        self._last_scan_ms: float = 0.0

    # ── Muting (TX-Integration) ───────────────────────────────────────

    def mute(self):
        """RX-Decode pausieren — aufrufen vor PTT-activate."""
        self._muted = True
        log.debug("[RX] Muted (TX aktiv)")

    def unmute(self):
        """RX-Decode wieder aktivieren — aufrufen nach PTT-release."""
        self._muted = False
        log.debug("[RX] Unmuted")

    # ── Statistik ────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Aktuelle RX-Statistik."""
        return {
            "scans":     self._scan_count,
            "decoded":   self._rx_count,
            "duplicates": self._dup_count,
            "dedup_cache_size": self._dedup.size,
            "last_scan_ms": round(self._last_scan_ms, 1),
            "muted":     self._muted,
            "running":   self._running,
        }

    # ── Haupt-Loop ────────────────────────────────────────────────────

    async def run(self) -> None:
        """
        Hauptschleife als asyncio Task.

        Startet AudioReceiver, scannt periodisch, dekodiert im Executor.
        Läuft bis zur CancelledError (Strg+C / Daemon-Shutdown).
        """
        print(f"{_ts()}  [RX] run() gestartet", flush=True)

        try:
            from gust_audio import AudioReceiver
            print(f"{_ts()}  [RX] gust_audio importiert ✓", flush=True)
        except Exception as e:
            print(f"{_ts()}  [RX] FEHLER gust_audio: {e}", flush=True)
            print(f"{_ts()}  [RX] Tipp: gust_audio.py mit Python-3.9-Version ersetzen", flush=True)
            return

        try:
            from gust_modulator import receive as _receive
            print(f"{_ts()}  [RX] gust_modulator importiert ✓", flush=True)
        except Exception as e:
            print(f"{_ts()}  [RX] FEHLER gust_modulator: {e}", flush=True)
            return

        try:
            from gust_eventbus import make_rx_frame_event, make_audio_level_event
            print(f"{_ts()}  [RX] gust_eventbus importiert ✓", flush=True)
        except Exception as e:
            print(f"{_ts()}  [RX] FEHLER gust_eventbus: {e}", flush=True)
            return

        try:
            receiver = AudioReceiver(
                device           = self._device,
                buffer_seconds   = max(self._window * 2, 30.0),
                force_samplerate = self._force_sr,
            )
            print(f"{_ts()}  [RX] AudioReceiver erstellt  Gerät={self._device}", flush=True)
        except Exception as e:
            print(f"{_ts()}  [RX] FEHLER AudioReceiver: {e}", flush=True)
            print(f"{_ts()}  [RX] Tipp: sounddevice installiert? Gerät-ID korrekt?", flush=True)
            return

        print(
            f"{_ts()}  [RX] Loop startet  Gerät={self._device or 'Standard'}"
            f"  Interval={self._interval}s  Fenster={self._window}s",
            flush=True,
        )
        log.info(
            "[RX] Loop startet  |  Gerät: %s  |  Interval: %.1fs  |  Fenster: %.1fs",
            self._device or "Standard",
            self._interval,
            self._window,
        )

        # Vollfenster-Garantie prüfen: WINDOW_S >= MAX_FRAME_S + SCAN_INTERVAL_S.
        # Ist sie erfüllt, ist bei Fixed-Cadence-Capture jede Sendung in
        # mindestens einem Scanfenster vollständig und ausgerichtet enthalten
        # (verhindert BUG-07: Simplex-Fenstertiming-Miss).
        _margin = self._window - MAX_FRAME_S - self._interval
        if _margin >= 0:
            print(
                f"{_ts()}  [RX] Vollfenster-Garantie ✓  "
                f"(Fenster {self._window}s >= Frame {MAX_FRAME_S}s + Intervall "
                f"{self._interval}s, Marge {_margin:+.1f}s)",
                flush=True,
            )
        else:
            print(
                f"{_ts()}  [RX] ⚠ Vollfenster-Garantie NICHT erfüllt  "
                f"(Marge {_margin:+.1f}s) — Frames können durch Fenster fallen. "
                f"Fenster >= {MAX_FRAME_S + self._interval:.1f}s wählen.",
                flush=True,
            )
            log.warning(
                "[RX] Vollfenster-Garantie verletzt: Fenster %.1fs < Frame %.1fs + "
                "Intervall %.1fs (Marge %.1fs)",
                self._window, MAX_FRAME_S, self._interval, _margin,
            )

        self._running = True
        loop = asyncio.get_running_loop()
        level_task: Optional[asyncio.Task] = None

        # ── Audio-Level-Publisher ─────────────────────────────────────
        # Publiziert alle 250 ms RMS+Peak eines kurzen Slices aus dem
        # Ringpuffer. Damit zeigt das Web-UI ob der Audio-Eingang
        # überhaupt Signal sieht. Pausiert bei TX (self._muted).
        AUDIO_LEVEL_INTERVAL_S = 0.25
        AUDIO_LEVEL_SLICE_S    = 0.2

        async def _level_publisher():
            while True:
                try:
                    await asyncio.sleep(AUDIO_LEVEL_INTERVAL_S)
                    if self._muted or self._bus is None:
                        continue
                    chunk = await loop.run_in_executor(
                        self._executor,
                        receiver.get_snapshot,
                        AUDIO_LEVEL_SLICE_S,
                    )
                    if chunk is None or len(chunk) == 0:
                        continue
                    rms  = float(np.sqrt(np.mean(chunk ** 2)))
                    peak = float(np.max(np.abs(chunk)))
                    dev  = str(self._device) if self._device is not None else "Standard"
                    await self._bus.publish(
                        make_audio_level_event(rms=rms, peak=peak, device=dev)
                    )
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    log.debug("[RX] Level-Publisher: %s", e)

        try:
            print(f"{_ts()}  [RX] receiver.start() wird aufgerufen ...", flush=True)
            receiver.start()
            print(f"{_ts()}  [RX] receiver.start() OK — warte {min(self._window, 3.0):.0f}s ...", flush=True)
            # Kurz warten damit der Puffer erste Samples enthält
            await asyncio.sleep(min(self._window, 3.0))
            print(f"{_ts()}  [RX] Warten beendet — Scan-Loop startet", flush=True)

            # Level-Publisher starten (parallel zum Scan-Loop)
            level_task = asyncio.create_task(_level_publisher(), name="rx_level")

            # Fixed-Cadence-Scheduling: Der nächste Snapshot wird auf einen
            # festen Zeitplan (next_tick += interval) gelegt, NICHT erst nach
            # der Decode-Verarbeitung. So bläht die (variable) Decode-Zeit das
            # Capture-Intervall nicht auf — das effektive Fenster-Intervall
            # bleibt = self._interval, solange ein Decode < interval dauert.
            # Damit hält die Vollfenster-Garantie (siehe WINDOW_S).
            next_tick = time.monotonic()

            while True:
                next_tick += self._interval
                _sleep_for = next_tick - time.monotonic()
                if _sleep_for > 0:
                    await asyncio.sleep(_sleep_for)
                else:
                    # Decode hat das Intervall überzogen → Zeitplan resynchron-
                    # isieren (kein Aufstauen) und sofort weiterscannen.
                    log.debug("[RX] Scan-Verarbeitung über Intervall (%.2fs Rückstand) "
                              "— resync", -_sleep_for)
                    next_tick = time.monotonic()

                if self._muted:
                    log.debug("[RX] Scan übersprungen (TX aktiv)")
                    continue

                # Snapshot aus Ringpuffer (inkl. resample_poly — im Executor um asyncio nicht zu blockieren)
                audio = await loop.run_in_executor(
                    self._executor,
                    receiver.get_snapshot,
                    self._window,
                )

                # Stille überspringen (spart CPU)
                rms = float(np.sqrt(np.mean(audio ** 2)))
                if rms < MIN_AUDIO_LEVEL:
                    log.debug("[RX] Stille erkannt (RMS=%.5f) — Scan übersprungen", rms)
                    continue

                # CPU-intensiven Decode im Thread-Pool ausführen
                self._scan_count += 1
                t0 = time.monotonic()

                try:
                    result = await loop.run_in_executor(
                        self._executor,
                        _receive,
                        audio,
                        None,   # channel=None → Breitband
                        True,   # use_fec=True
                        0.0,    # freq_offset=0.0 (Breitband erkennt selbst)
                    )
                except Exception as e:
                    log.warning("[RX] Decode-Fehler: %s", e)
                    continue
                finally:
                    self._last_scan_ms = (time.monotonic() - t0) * 1000

                self._last_result = result

                if not result.get("crc_ok"):
                    sync_found = result.get("sync_found", False)
                    if sync_found:
                        # Frame-Struktur erkannt, aber CRC fehlgeschlagen
                        self._no_sync_count = 0   # SYNC-Sequenz gefunden → Zähler reset
                        try:
                            from gust_frame import channel_frequency as _cf
                            _ch  = result.get("detected_channel") or 2
                            _f0  = _cf(_ch) + result.get("freq_offset_hz", 0)
                        except Exception:
                            _f0 = 900.0 + result.get("freq_offset_hz", 0)
                        snr = _measure_audio_snr(audio, _f0)
                        msg = (
                            f"{_ts()}  [RX] ⚠ Frame identifiziert — CRC-Fehler  "
                            f"(nicht dekodierbar)  "
                            f"Kanal {result.get('detected_channel','?')}  "
                            f"off={result.get('freq_offset_hz',0):+.1f}Hz  "
                            f"Score={result.get('_sync_score',0):.3f}  "
                            f"SNR≈{snr:+.1f}dB  {self._last_scan_ms:.0f}ms"
                        )
                        print(msg, flush=True)
                        log.warning(msg)
                    else:
                        # Kein SYNC erkannt — Rauschen oder kein GUST-Signal
                        self._no_sync_count += 1
                        log.debug(
                            "[RX] Scan #%d  %.0f ms  kein Frame erkannt",
                            self._scan_count, self._last_scan_ms,
                        )
                        # Periodischer Heartbeat alle 30 Scans (~60s bei 2s-Intervall)
                        if self._no_sync_count % 30 == 0:
                            log.info(
                                "[RX] %d Scans ohne Frame — kein GUST-Signal erkannt",
                                self._no_sync_count,
                            )
                    continue

                # Frame dekodiert — SNR messen
                try:
                    from gust_frame import channel_frequency as _cf
                    _ch  = result.get("detected_channel") or 2
                    _f0  = _cf(_ch) + result.get("freq_offset_hz", 0)
                except Exception:
                    _f0 = 900.0 + result.get("freq_offset_hz", 0)
                snr = _measure_audio_snr(audio, _f0)
                result["_snr_db"] = snr

                # Deduplication
                callsign    = result.get("from",    "?")
                frame_type  = result.get("type",     0)
                payload_dec = result.get("payload_decoded", {})
                try:
                    payload_bytes = str(sorted(payload_dec.items())).encode()
                except Exception:
                    payload_bytes = str(payload_dec).encode()

                if self._dedup.is_duplicate(callsign, frame_type, payload_bytes):
                    self._dup_count += 1
                    log.debug(
                        "[RX] Duplikat unterdrückt: %s [%s]",
                        callsign, result.get("type_name", "?"),
                    )
                    continue

                # Neuer Frame → ausgeben + EventBus
                self._rx_count += 1
                self._no_sync_count = 0   # Erfolgreich dekodiert → Zähler reset
                msg = (
                    f"{_ts()}  [RX] ✓ Frame #{self._rx_count}  "
                    f"von {callsign:<8}  [{result.get('type_name','?'):<10}]  "
                    f"Kanal {result.get('detected_channel','?')}  "
                    f"off={result.get('freq_offset_hz',0):+.1f}Hz  "
                    f"SNR={snr:+.1f}dB  "
                    f"Score={result.get('_sync_score',0):.3f}  "
                    f"{self._last_scan_ms:.0f}ms"
                )
                print(msg, flush=True)
                log.info(msg)

                if self._bus is not None:
                    event = make_rx_frame_event(result)
                    await self._bus.publish(event)

        except asyncio.CancelledError:
            print(f"{_ts()}  [RX] Loop beendet (CancelledError)", flush=True)
            log.info("[RX] Loop beendet (CancelledError)")
        except Exception as e:
            print(f"{_ts()}  [RX] FEHLER im Loop: {e}", flush=True)
            import traceback; traceback.print_exc()
            log.error("[RX] Unerwarteter Fehler: %s", e, exc_info=True)
        finally:
            self._running = False
            if level_task is not None and not level_task.done():
                level_task.cancel()
                try:
                    await level_task
                except (asyncio.CancelledError, Exception):
                    pass
            receiver.stop()
            print(f"{_ts()}  [RX] Statistik: {self._scan_count} Scans / {self._rx_count} dekodiert / {self._dup_count} Duplikate", flush=True)
            log.info(
                "[RX] Statistik: %d Scans / %d dekodiert / %d Duplikate",
                self._scan_count, self._rx_count, self._dup_count,
            )


# ═══════════════════════════════════════════════════════════════════════
# HILFSFUNKTION für gust.py
# ═══════════════════════════════════════════════════════════════════════

def build_rx_loop(cfg: dict, event_bus) -> "Optional[AudioRXLoop]":
    """
    AudioRXLoop aus Konfigurationsdict erzeugen.

    Liest den Abschnitt cfg["rx"]. Gibt None zurück wenn
    cfg["rx"]["enabled"] == False.

    Erwartete Struktur in gateway.json:
        "rx": {
            "device":           null,
            "scan_interval_s":  2.0,
            "window_s":         9.0,
            "dedup_ttl_s":      30,
            "enabled":          true
        }

    Fallback: Falls "rx" nicht vorhanden → Standard-Parameter,
    Gerät aus cfg["audio"]["device"] (selbes Gerät wie TX, nur
    wenn kein separates RX-Gerät konfiguriert).
    """
    rx_cfg = cfg.get("rx", {})

    if not rx_cfg.get("enabled", True):
        log.info("[RX] Loop deaktiviert (rx.enabled=false in gateway.json)")
        return None

    # RX-Gerät: explizit aus cfg["rx"]["device"], Fallback auf TX-Gerät
    device = rx_cfg.get("device", cfg.get("audio", {}).get("device"))

    # Optionaler Samplerate-Override (z.B. 48000 für IC-7200 statt default 44100)
    input_sr = rx_cfg.get("input_sample_rate")
    if input_sr is not None:
        input_sr = int(input_sr)

    return AudioRXLoop(
        device            = device,
        event_bus         = event_bus,
        scan_interval_s   = float(rx_cfg.get("scan_interval_s", SCAN_INTERVAL_S)),
        window_s          = float(rx_cfg.get("window_s",         WINDOW_S)),
        dedup_ttl_s       = float(rx_cfg.get("dedup_ttl_s",      DEDUP_TTL_S)),
        force_samplerate  = input_sr,
    )


# ═══════════════════════════════════════════════════════════════════════
# STANDALONE-TEST
# ═══════════════════════════════════════════════════════════════════════

async def _demo():
    """
    Standalone-Demo: Lauscht auf dem Standard-Audiogerät und
    dekodiert GUST-Frames ohne EventBus.
    Beenden mit Strg+C.
    """
    import argparse

    p = argparse.ArgumentParser(
        prog="gust_rx.py",
        description=(
            "GUST RX — Kontinuierlicher Empfangs-Scan-Loop\n\n"
            "Hört dauerhaft auf allen 10 GUST-Kanälen (NF 400–2900 Hz) zu und\n"
            "dekodiert eingehende Frames. Gibt dekodierte Frames auf der Konsole aus.\n\n"
            "Zum Einsatz mit IC-7610, SDRplay oder beliebiger Soundkarte als Audio-Eingang."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--device", type=int, default=None, metavar="ID",
                   help="Audio-Eingabegerät (Integer-ID, siehe `python gust.py devices`). "
                        "Standard: Systemstandard")
    p.add_argument("--interval", type=float, default=SCAN_INTERVAL_S, metavar="SEK",
                   help=f"Scan-Intervall in Sekunden — wie oft der Ringpuffer "
                        f"ausgewertet wird (Standard: {SCAN_INTERVAL_S} s)")
    p.add_argument("--window", type=float, default=WINDOW_S, metavar="SEK",
                   help=f"Länge des Analyse-Fensters in Sekunden — muss >= "
                        f"MAX_FRAME_S + SCAN_INTERVAL_S sein (Standard: {WINDOW_S} s)")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Ausführliche Ausgabe inkl. Debug-Infos (SNR, Offset, Score)")

    # No-Args-Hint — vor parse_args()
    if len(sys.argv) == 1:
        print("Verwendung: python gust_rx.py -h  oder  --help  für Parameterübersicht")
        sys.exit(0)

    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    print(f"""
╔══════════════════════════════════════════════════════╗
║  GUST RX-Monitor  v1.0                           ║
╠══════════════════════════════════════════════════════╣
║  Gerät    : {str(args.device or 'Standard'):<40}║
║  Intervall: {args.interval:<5.1f}s                                  ║
║  Fenster  : {args.window:<5.1f}s                                  ║
║  Stoppen  : Strg+C                                  ║
╚══════════════════════════════════════════════════════╝
""")

    # Einfacher Demo-Bus: gibt Events direkt aus
    class _PrintBus:
        async def publish(self, event):
            d = event.get("data", {})
            pl = d.get("payload_decoded", {})
            print(f"\n{'─'*54}")
            print(f"  Frame empfangen:")
            print(f"    Von   : {d.get('from','?')}")
            print(f"    Typ   : {d.get('type_name','?')}")
            print(f"    Kanal : {d.get('detected_channel','?')}")
            print(f"    Offset: {d.get('freq_offset_hz',0):+.1f} Hz")
            if isinstance(pl, dict):
                for k, v in pl.items():
                    if k != "flags":
                        print(f"    {k:20s} = {v}")
            print(f"{'─'*54}\n")

    rx = AudioRXLoop(
        device          = args.device,
        event_bus       = _PrintBus(),
        scan_interval_s = args.interval,
        window_s        = args.window,
    )

    try:
        await rx.run()
    except KeyboardInterrupt:
        pass
    finally:
        s = rx.stats()
        print(f"\nStatistik: {s['scans']} Scans  |  "
              f"{s['decoded']} dekodiert  |  "
              f"{s['duplicates']} Duplikate  |  "
              f"⌀ {s['last_scan_ms']:.0f} ms/Scan")


if __name__ == "__main__":
    asyncio.run(_demo())
