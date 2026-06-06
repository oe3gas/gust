#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gust_stress_decode.py — Batch-Decoder für Stresstest-WAV-Dateien
══════════════════════════════════════════════════════════════════

Liest eine von gust_stresstest.py erzeugte WAV-Datei vollständig durch,
findet alle enthaltenen GUST-Frames via Sliding-Window-Scan (gleiche
Strategie wie gust_rx.py im Echtbetrieb) und vergleicht das Ergebnis
gegen das zugehörige CSV-Log des Generators.

Aufruf:
    python gust_stress_decode.py <wav_datei> [--csv <csv_datei>] [-v]
                                 [--out <pfad>] [--window 9.0] [--step 2.0]

Exit-Codes:
    0  Dekodierrate >= 80 %
    1  Dekodierrate < 80 % oder kein Frame gefunden
    2  Fehler (Datei nicht gefunden etc.)

Autor: OE3GAS — GUST-Projekt
"""

import argparse
import csv
import os
import sys
from datetime import datetime

import numpy as np

# Windows-Konsole (cp1252) kann Box-Zeichen nicht darstellen → UTF-8 erzwingen
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from gust_modulator import receive, load_wav, SAMPLE_RATE
from gust_frame import N_CHANNELS, channel_frequency, frame_type_name

# ── Konstanten (aus gust_rx.py übernommen) ─────────────────────────────
WINDOW_S    = 9.0    # Fenstergröße in Sekunden
STEP_S      = 2.0    # Schrittweite in Sekunden
DEDUP_TTL_S = 10.0   # Dedup-Zeitfenster: gleicher Frame innerhalb 10 s = Duplikat
TIME_TOL_S  = 8.0    # Zeittoleranz beim Soll/Ist-Matching
PASS_RATE   = 0.80   # Dekodierrate für Exit-Code 0


def _ts() -> str:
    """Kurzer Zeitstempel für Verbose-Ausgaben."""
    return datetime.now().strftime("%H:%M:%S")


# ═══════════════════════════════════════════════════════════════════════
# SLIDING-WINDOW-DECODER
# ═══════════════════════════════════════════════════════════════════════

def sliding_window_decode(audio: np.ndarray,
                          window_s: float = WINDOW_S,
                          step_s: float = STEP_S,
                          verbose: bool = False) -> list:
    """
    Schiebt ein Fenster über das gesamte Audio-Array und sammelt alle
    dekodierten Frames (CRC OK) ein.

    Pro Fensterposition: receive(window, channel=ch, use_fec=True) für
    alle N_CHANNELS Kanäle (Direktmodus) — der Breitband-Modus liefert
    nur einen Frame pro Fenster und würde bei 8 gleichzeitig aktiven
    Kanälen 7 von 8 Frames ignorieren.

    Deduplizierung: Schlüssel (callsign, frame_type, channel); ein Fund
    wird verworfen, wenn ein Fund mit demselben Schlüssel existiert,
    dessen start_s weniger als DEDUP_TTL_S zurückliegt.

    Rückgabe: Liste von Dicts mit start_s, callsign, frame_type,
    channel, crc_ok, freq_offset_hz.
    """
    window_samples = int(window_s * SAMPLE_RATE)
    step_samples   = int(step_s * SAMPLE_RATE)
    n_samples      = len(audio)

    found = []          # alle akzeptierten Funde
    last_seen = {}      # (callsign, frame_type, channel) -> letzte start_s

    offsets = list(range(0, n_samples, step_samples))
    n_scans = 0

    for scan_idx, offset in enumerate(offsets):
        window = audio[offset:offset + window_samples]
        # Zu kurze Restfenster überspringen (kein Frame passt mehr hinein)
        if len(window) < 2 * SAMPLE_RATE:
            continue
        n_scans += 1

        scan_hits = []   # Funde dieses Fensters (für Verbose-Ausgabe)

        # Direktmodus: alle Kanäle einzeln dekodieren
        for ch in range(N_CHANNELS):
            result = receive(window, channel=ch, use_fec=True)

            if not result.get("crc_ok"):
                continue

            start_s = offset / SAMPLE_RATE + (result.get("sync_offset_s") or 0.0)
            callsign   = result.get("from", "?")
            frame_type = result.get("type_name", "?")
            channel    = result.get("detected_channel", ch)
            if channel is None:
                channel = ch

            scan_hits.append("{} ch{} {}".format(frame_type, channel, callsign))

            # ── Deduplizierung ──────────────────────────────────────
            key = (callsign, frame_type, channel)
            prev = last_seen.get(key)
            if prev is not None and abs(start_s - prev) < DEDUP_TTL_S:
                continue
            last_seen[key] = start_s

            found.append({
                "start_s":        start_s,
                "callsign":       callsign,
                "frame_type":     frame_type,
                "channel":        channel,
                "crc_ok":         True,
                "freq_offset_hz": result.get("freq_offset_hz", 0.0),
            })

        if verbose:
            pos_s = offset / SAMPLE_RATE
            status = "; ".join(scan_hits) if scan_hits else "—"
            print("{}  Scan {:>3d}/{:d}  @{:6.1f}s  {}".format(
                _ts(), scan_idx + 1, len(offsets), pos_s, status), flush=True)
        elif n_scans % 10 == 0:
            print(".", end="", flush=True)

    if not verbose:
        print("", flush=True)   # Zeilenumbruch nach Fortschrittsbalken

    return found


# ═══════════════════════════════════════════════════════════════════════
# CSV-LOG DES GENERATORS LADEN
# ═══════════════════════════════════════════════════════════════════════

def load_csv_log(csv_path: str) -> list:
    """
    Liest das CSV-Log von gust_stresstest.py.

    Felder: nr, start_s, end_s, channel, channel_b, frame_type,
            callsign, duration_s

    Dual-Kopien ('dual' im frame_type) werden mitgelesen — die
    Auswertung behandelt sie gesondert (Bonus, nicht 'erwartet').
    """
    rows = []
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


def _base_type(frame_type: str) -> str:
    """Frame-Typ ohne '(dual<-chX)'-Suffix; Generator-Kurzname normalisiert."""
    t = frame_type.split("(")[0].strip()
    # Generator schreibt Dual-Kopien als 'EMERG_BCN(dual<-chX)' —
    # der Decoder liefert aber 'EMERG_BEACON' (frame_type_name)
    if t == "EMERG_BCN":
        t = "EMERG_BEACON"
    return t


def _is_dual(row: dict) -> bool:
    """True wenn die CSV-Zeile eine Dual-Redundanzkopie ist."""
    return "dual" in row.get("frame_type", "").lower()


# ═══════════════════════════════════════════════════════════════════════
# SOLL/IST-VERGLEICH
# ═══════════════════════════════════════════════════════════════════════

def match_results(found: list, expected: list) -> dict:
    """
    Vergleicht gefundene Frames mit den erwarteten aus dem CSV.

    Ein Fund f matcht einen erwarteten Frame e wenn:
      1. f['frame_type'] == Basis-Typ von e['frame_type']
      2. f['channel'] == e['channel'] ODER == e['channel_b'] (Dual)
      3. |f['start_s'] - e['start_s']| < TIME_TOL_S

    Jeder erwartete Frame wird höchstens einmal gematcht (greedy,
    erster passender Fund gewinnt). Funde, die nur auf eine
    Dual-Kopie passen, zählen als Bonus (nicht als überzählig).
    """
    primary = [e for e in expected if not _is_dual(e)]
    duals   = [e for e in expected if _is_dual(e)]

    matched      = []     # Paare (found, expected)
    matched_exp  = set()  # ids bereits gematchter expected-Zeilen
    matched_fnd  = set()  # Indizes bereits gematchter Funde

    def _channels_of(e):
        """Primärkanal + ggf. channel_b einer expected-Zeile."""
        chans = []
        try:
            chans.append(int(e["channel"]))
        except (ValueError, KeyError, TypeError):
            pass
        ch_b = e.get("channel_b", "")
        if ch_b not in ("", None):
            try:
                chans.append(int(ch_b))
            except (ValueError, TypeError):
                pass
        return chans

    def _matches(f, e):
        if f["frame_type"] != _base_type(e["frame_type"]):
            return False
        if f["channel"] not in _channels_of(e):
            return False
        try:
            exp_start = float(e["start_s"])
        except (ValueError, KeyError, TypeError):
            return False
        return abs(f["start_s"] - exp_start) < TIME_TOL_S

    # Greedy: erster passender Fund gewinnt
    for fi, f in enumerate(found):
        for e in primary:
            if id(e) in matched_exp:
                continue
            if _matches(f, e):
                matched.append((f, e))
                matched_exp.add(id(e))
                matched_fnd.add(fi)
                break

    # Ungematchte Funde gegen Dual-Kopien prüfen → Bonus, nicht 'extra'
    extra = []
    n_dual_bonus = 0
    for fi, f in enumerate(found):
        if fi in matched_fnd:
            continue
        is_bonus = any(_matches(f, e) for e in duals)
        if is_bonus:
            n_dual_bonus += 1
        else:
            extra.append(f)

    missed = [e for e in primary if id(e) not in matched_exp]

    n_expected = len(primary)
    n_matched  = len(matched)

    return {
        "n_expected":   n_expected,
        "n_found":      len(found),
        "n_matched":    n_matched,
        "n_missed":     len(missed),
        "n_extra":      len(extra),
        "n_dual_bonus": n_dual_bonus,
        "decode_rate":  (n_matched / n_expected) if n_expected else 0.0,
        "missed":       missed,
        "extra":        extra,
        "matched":      matched,
    }


# ═══════════════════════════════════════════════════════════════════════
# BERICHT
# ═══════════════════════════════════════════════════════════════════════

def print_report(stats: dict, wav_path: str, csv_path: str,
                 duration_s: float, n_scans: int,
                 window_s: float, step_s: float):
    """Übersichtlicher Abschlussbericht auf stdout."""
    line = "═" * 62
    print(line)
    print("  GUST Stresstest-Auswertung")
    print(line)
    print("  WAV:      {}  ({:.1f} s)".format(os.path.basename(wav_path), duration_s))
    print("  CSV-Log:  {}".format(os.path.basename(csv_path) if csv_path else "— (kein Soll/Ist-Vergleich)"))
    print("  Fenster:  {:.1f} s, Schritt {:.1f} s  ->  {} Scans".format(
        window_s, step_s, n_scans))
    print()

    if stats is None:
        return

    print("  Erwartet: {:>3d} Frames  (Primär, ohne Dual-Kopien)".format(stats["n_expected"]))
    print("  Gefunden: {:>3d} Frames  (nach Deduplizierung)".format(stats["n_found"]))
    print("  Gematcht: {:>3d} Frames".format(stats["n_matched"]))
    print("  Fehlend:  {:>3d} Frames".format(stats["n_missed"]))
    print("  Überzählig: {:>2d} Frame(s)".format(stats["n_extra"]))
    if stats.get("n_dual_bonus"):
        print("  Dual-Bonus: {:>2d} Frame(s)  (Redundanzkopien dekodiert)".format(
            stats["n_dual_bonus"]))
    print("  " + "─" * 58)
    print("  Dekodierrate: {:.1f} %".format(stats["decode_rate"] * 100.0))

    if stats["missed"]:
        print()
        print("  Fehlende Frames:")
        for e in stats["missed"]:
            try:
                start = float(e.get("start_s", 0.0))
            except (ValueError, TypeError):
                start = 0.0
            print("    ch{:<2s} {:<14s} {:<8s} @{:.2f}s".format(
                str(e.get("channel", "?")),
                _base_type(e.get("frame_type", "?")),
                e.get("callsign", "?"),
                start))

    if stats["extra"]:
        print()
        print("  Überzählige Funde (kein Match):")
        for f in stats["extra"]:
            print("    ch{:<2d} {:<14s} {:<8s} @{:.2f}s".format(
                f["channel"] if f["channel"] is not None else -1,
                f["frame_type"],
                f["callsign"],
                f["start_s"]))

    print(line)


def print_findings_only(found: list):
    """Auflistung der Funde ohne Soll/Ist-Vergleich (kein CSV vorhanden)."""
    if not found:
        print("  Keine Frames gefunden.")
        return
    print("  Gefundene Frames ({}):".format(len(found)))
    for f in found:
        print("    ch{:<2d} {:<14s} {:<8s} @{:7.2f}s  ({:+.1f} Hz)".format(
            f["channel"] if f["channel"] is not None else -1,
            f["frame_type"],
            f["callsign"],
            f["start_s"],
            f["freq_offset_hz"]))


# ═══════════════════════════════════════════════════════════════════════
# ERGEBNIS-CSV
# ═══════════════════════════════════════════════════════════════════════

def write_result_csv(path: str, found: list, stats: dict):
    """
    Schreibt die Funde als CSV.

    Felder: nr, start_s, channel, frame_type, callsign, freq_offset_hz,
            matched, matched_expected_nr
    """
    # Mapping Fund -> Nr. der gematchten expected-Zeile
    match_nr = {}
    if stats is not None:
        for f, e in stats["matched"]:
            match_nr[id(f)] = e.get("nr", "")

    fields = ["nr", "start_s", "channel", "frame_type", "callsign",
              "freq_offset_hz", "matched", "matched_expected_nr"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for i, f in enumerate(found, start=1):
            w.writerow({
                "nr":                  i,
                "start_s":             "{:.3f}".format(f["start_s"]),
                "channel":             f["channel"],
                "frame_type":          f["frame_type"],
                "callsign":            f["callsign"],
                "freq_offset_hz":      f["freq_offset_hz"],
                "matched":             id(f) in match_nr,
                "matched_expected_nr": match_nr.get(id(f), ""),
            })
    print("  Ergebnis-CSV geschrieben: {}".format(path))


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def main() -> int:
    p = argparse.ArgumentParser(
        description="Batch-Decoder für GUST-Stresstest-WAV-Dateien "
                    "(Sliding-Window-Scan + Soll/Ist-Vergleich).")
    p.add_argument("wav", help="WAV-Datei vom Stresstest-Generator")
    p.add_argument("--csv", default=None,
                   help="CSV-Log (Standard: gleicher Basename wie WAV, .csv)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Pro Scan eine Zeile ausgeben")
    p.add_argument("--out", default=None,
                   help="Ergebnis-CSV schreiben (Standard: keins)")
    p.add_argument("--window", type=float, default=WINDOW_S,
                   help="Fenstergröße in Sekunden (Standard: 9.0)")
    p.add_argument("--step", type=float, default=STEP_S,
                   help="Schrittweite in Sekunden (Standard: 2.0)")
    args = p.parse_args()

    # ── Eingaben prüfen ─────────────────────────────────────────────
    if not os.path.isfile(args.wav):
        print("FEHLER: WAV-Datei nicht gefunden: {}".format(args.wav),
              file=sys.stderr)
        return 2

    csv_path = args.csv
    if csv_path is None:
        candidate = os.path.splitext(args.wav)[0] + ".csv"
        csv_path = candidate if os.path.isfile(candidate) else None
    elif not os.path.isfile(csv_path):
        print("FEHLER: CSV-Log nicht gefunden: {}".format(csv_path),
              file=sys.stderr)
        return 2

    # ── WAV laden (load_wav resampelt automatisch auf 8000 Hz) ─────
    try:
        audio, sr = load_wav(args.wav)
    except Exception as e:
        print("FEHLER beim Laden der WAV-Datei: {}".format(e), file=sys.stderr)
        return 2

    duration_s = len(audio) / SAMPLE_RATE
    n_scans = max(0, int(np.ceil(duration_s / args.step)))
    print("{}  Scan startet: {} ({:.1f} s), Fenster {:.1f} s, Schritt {:.1f} s".format(
        _ts(), os.path.basename(args.wav), duration_s, args.window, args.step),
        flush=True)

    # ── Sliding-Window-Scan ─────────────────────────────────────────
    found = sliding_window_decode(audio, window_s=args.window,
                                  step_s=args.step, verbose=args.verbose)

    # ── Auswertung ──────────────────────────────────────────────────
    stats = None
    if csv_path is not None:
        try:
            expected = load_csv_log(csv_path)
        except Exception as e:
            print("FEHLER beim Lesen des CSV-Logs: {}".format(e), file=sys.stderr)
            return 2
        stats = match_results(found, expected)

    print_report(stats, args.wav, csv_path, duration_s, n_scans,
                 args.window, args.step)
    if stats is None:
        print_findings_only(found)

    # ── Ergebnis-CSV ────────────────────────────────────────────────
    if args.out:
        write_result_csv(args.out, found, stats)

    # ── Exit-Code ───────────────────────────────────────────────────
    if not found:
        return 1
    if stats is not None and stats["decode_rate"] < PASS_RATE:
        return 1
    return 0


# ═══════════════════════════════════════════════════════════════════════
# PROGRAMMATISCHE API — Live-Session-Auswertung (Web-UI Session-Recorder)
# ═══════════════════════════════════════════════════════════════════════

def match_live_session(session_frames: list, csv_path: str) -> dict:
    """
    Vergleicht live-dekodierte Frames einer Daemon-Session gegen die
    Ground-Truth-CSV von gust_stresstest.py.

    session_frames: Liste von rx_frame-Event-Dicts vom EventBus.
        Relevante Felder pro Dict:
            "from"          → callsign (str)
            "type_name"     → Frame-Typ-Name (str, z.B. "WEATHER")
            "channel"       → Kanal (int), Fallback: "detected_channel"
            "tx_start_s"    → physikalischer Sendezeitstempel (float,
                              monotone Zeit des Daemons, jitterfrei).
                              Bevorzugtes Feld.
            "ts"            → Unix-Timestamp Decode-Zeitpunkt (float).
                              Fallback für alte Sessions ohne tx_start_s.

    csv_path: Pfad zur CSV-Datei von gust_stresstest.py.

    Rückgabe: dict mit denselben Feldern wie match_results():
        n_expected, n_found, n_matched, n_missed, n_extra,
        n_dual_bonus, decode_rate, missed, extra, matched

    Zeitbezug: Das CSV hat start_s relativ zum WAV-Anfang; die Session-
    Zeitquelle (tx_start_s bzw. ts) hat einen unbekannten Nullpunkt.
      1. Grob:  start_s = t − min(t) aller session_frames.
      2. Fein:  Der konstante Restversatz wird als Median der Differenzen
         zum jeweils nächstliegenden Typ/Kanal-kompatiblen CSV-Eintrag
         geschätzt und abgezogen. Auch im tx_start_s-Pfad nötig: die
         min()-Normierung setzt den ERSTEN DEKODIERTEN Frame auf 0 —
         dessen wahres start_s im WAV ist unbekannt und überschreitet
         TIME_TOL_S, sobald frühe Frames nicht dekodiert wurden.
         Mit tx_start_s sind die Frame-ABSTÄNDE jitterfrei (kein Anteil
         aus Framedauer/Scan-Latenz wie bei ts), der Median schätzt den
         Versatz daher exakt. TIME_TOL_S = 8.0 s bleibt.
    """
    import statistics

    expected = load_csv_log(csv_path)

    # tx_start_s bevorzugen (physikalischer Sendezeitstempel),
    # Fallback auf ts (Decode-Zeitpunkt — alte Sessions).
    # Quellen: EventBus-Events (float) ODER CSV-Upload (String,
    # "0.0" = Platzhalter alter Sessions ohne tx_start_s).
    def _tx_start_of(f):
        raw = f.get("tx_start_s")
        if raw in (None, "", "0.0", "0", 0.0, 0):
            return None
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None

    use_tx_start = bool(session_frames) and all(
        _tx_start_of(f) is not None for f in session_frames)

    def _time_of(f):
        if use_tx_start:
            return _tx_start_of(f)
        try:
            return float(f.get("ts", 0.0) or 0.0)
        except (ValueError, TypeError):
            return 0.0

    # session_frames → internes found-Format für match_results()
    t0 = min((_time_of(f) for f in session_frames), default=0.0)
    found = []
    for f in session_frames:
        ch = f.get("channel")
        if ch is None:
            ch = f.get("detected_channel")
        found.append({
            "frame_type":     f.get("type_name", ""),
            "callsign":       f.get("from", ""),
            "channel":        int(ch) if ch is not None else -1,
            "start_s":        _time_of(f) - t0,
            "freq_offset_hz": 0.0,
        })

    # ── Zeitachsen-Kalibrierung (Schritt 2) ────────────────────────────
    # Für jeden Fund: nächstliegender Typ/Kanal-kompatibler CSV-Eintrag,
    # Differenz sammeln, Median als globalen Versatz abziehen.
    def _exp_channels(e):
        chans = []
        for key in ("channel", "channel_b"):
            v = e.get(key, "")
            if v not in ("", None):
                try:
                    chans.append(int(v))
                except (ValueError, TypeError):
                    pass
        return chans

    deltas = []
    for f in found:
        best = None
        for e in expected:
            if f["frame_type"] != _base_type(e.get("frame_type", "")):
                continue
            if f["channel"] not in _exp_channels(e):
                continue
            try:
                d = f["start_s"] - float(e["start_s"])
            except (ValueError, KeyError, TypeError):
                continue
            if best is None or abs(d) < abs(best):
                best = d
        if best is not None:
            deltas.append(best)

    if deltas:
        offset = statistics.median(deltas)
        for f in found:
            f["start_s"] -= offset

    return match_results(found, expected)


if __name__ == "__main__":
    sys.exit(main())
