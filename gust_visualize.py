#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gust_visualize.py — GUST WAV-Datei Swimlane-Visualisierung
═══════════════════════════════════════════════════════════════════════
Liest eine von gust_stresstest.py erzeugte WAV-Datei, dekodiert alle
8 Kanäle via Sliding-Window-Scan und stellt das Ergebnis als
Swimlane-Diagramm dar (Kanäle als Spalten, Zeit vertikal t=0 oben).

Verwendung:
    python gust_visualize.py <wav_datei> [--out <ausgabe.jpg>]
                             [--csv <ground_truth.csv>]
                             [--window 9.0] [--step 2.0]
                             [--width 24] [--height 10]

Ausgabe:
    JPG-Datei mit Swimlane-Diagramm.
    Falls --out nicht angegeben: <wav_datei>.jpg

Autor: OE3GAS — GUST-Projekt
"""

import argparse
import csv
import os
import sys
from datetime import datetime

import numpy as np

# Matplotlib-Backend auf Agg setzen (kein Display nötig)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
from matplotlib.ticker import MultipleLocator

# ── Windows-Konsole UTF-8 ────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── GUST-Module ───────────────────────────────────────────────────────
try:
    from gust_modulator import receive, load_wav, SAMPLE_RATE
    from gust_frame import N_CHANNELS, channel_frequency, frame_type_name
except ImportError as e:
    print(f"FEHLER: GUST-Module nicht gefunden: {e}")
    print("Bitte das Skript im GUST-Projektverzeichnis ausführen.")
    sys.exit(1)

# ── Konstanten ────────────────────────────────────────────────────────
WINDOW_S    = 9.0
STEP_S      = 2.0
DEDUP_TTL_S = 10.0

# Farben pro Frame-Typ
FRAME_COLORS = {
    "WEATHER":     "#4A90D9",   # Blau
    "POSITION":    "#27AE60",   # Grün
    "EMERG_BEACON":"#E74C3C",   # Rot
    "EMERG_RSRC":  "#E67E22",   # Orange
    "STATION_TLM": "#8E44AD",   # Lila
    "TEXT":        "#F39C12",   # Gelb
    "CQ":          "#1ABC9C",   # Türkis
}
DEFAULT_COLOR  = "#95A5A6"      # Grau für unbekannte Typen
MISSED_COLOR   = "#FFB3B3"      # Hellrot für fehlende Frames (aus CSV)
SWIMLANE_BG    = ["#1A1A2E", "#16213E"]   # Abwechselnde Swimlane-Hintergründe
GRID_COLOR     = "#2C3E50"
TEXT_COLOR     = "#ECF0F1"
TITLE_COLOR    = "#ECF0F1"
AXIS_COLOR     = "#7F8C8D"
BACKGROUND     = "#0D1117"


def _ts():
    return datetime.now().strftime("%H:%M:%S")


# ═══════════════════════════════════════════════════════════════════════
# SLIDING-WINDOW-DECODER (analog zu gust_stress_decode.py)
# ═══════════════════════════════════════════════════════════════════════

def decode_wav(audio: np.ndarray,
               window_s: float = WINDOW_S,
               step_s: float = STEP_S,
               verbose: bool = True) -> list:
    """
    Sliding-Window-Scan über alle 8 Kanäle.
    Rückgabe: Liste von Dicts mit start_s, end_s, callsign,
              frame_type, channel, freq_offset_hz, duration_s
    """
    window_samples = int(window_s * SAMPLE_RATE)
    step_samples   = int(step_s   * SAMPLE_RATE)
    n_samples      = len(audio)
    duration_s     = n_samples / SAMPLE_RATE

    offsets = list(range(0, n_samples, step_samples))
    n_scans = len(offsets)

    found     = []
    last_seen = {}   # (callsign, frame_type, channel) → start_s

    if verbose:
        print(f"{_ts()}  Dekodiere {duration_s:.1f}s Audio  |  "
              f"{n_scans} Scans × {N_CHANNELS} Kanäle ...", flush=True)

    for scan_idx, offset in enumerate(offsets):
        window = audio[offset : offset + window_samples]
        if len(window) < 2 * SAMPLE_RATE:
            continue

        pos_s = offset / SAMPLE_RATE

        for ch in range(N_CHANNELS):
            try:
                result = receive(window, channel=ch, use_fec=True)
            except Exception:
                continue

            if not result.get("crc_ok"):
                continue

            # start_s = Position des SYNC im WAV
            sync_off = result.get("sync_offset_s") or 0.0
            start_s  = pos_s + sync_off
            callsign  = result.get("from", "?")
            ftype     = result.get("type_name", "?")
            det_ch    = result.get("detected_channel", ch)
            if det_ch is None:
                det_ch = ch

            # Deduplizierung
            key  = (callsign, ftype, det_ch)
            prev = last_seen.get(key)
            if prev is not None and abs(start_s - prev) < DEDUP_TTL_S:
                continue
            last_seen[key] = start_s

            # Frame-Dauer schätzen aus Symbol-Anzahl
            n_syms   = result.get("data_symbols", 52)
            sym_dur  = (n_syms + 8) * (256 / SAMPLE_RATE)  # 8 SYNC + Daten
            end_s    = start_s + sym_dur

            found.append({
                "start_s":        round(start_s, 3),
                "end_s":          round(end_s,   3),
                "duration_s":     round(sym_dur,  3),
                "callsign":       callsign,
                "frame_type":     ftype,
                "channel":        int(det_ch),
                "freq_offset_hz": result.get("freq_offset_hz", 0.0),
            })

        if verbose and (scan_idx + 1) % 10 == 0:
            pct = (scan_idx + 1) / n_scans * 100
            print(f"{_ts()}  Scan {scan_idx+1:3d}/{n_scans}  "
                  f"({pct:.0f}%)  {len(found)} Frames bisher", flush=True)

    if verbose:
        print(f"{_ts()}  Dekodierung abgeschlossen: {len(found)} Frames gefunden.",
              flush=True)

    return found


# ═══════════════════════════════════════════════════════════════════════
# CSV GROUND-TRUTH LADEN (optional)
# ═══════════════════════════════════════════════════════════════════════

def load_csv(csv_path: str) -> list:
    """Lädt Ground-Truth-CSV von gust_stresstest.py."""
    rows = []
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            # Dual-Kopien überspringen
            if "dual" in row.get("frame_type", "").lower():
                continue
            try:
                rows.append({
                    "start_s":    float(row["start_s"]),
                    "end_s":      float(row["end_s"]),
                    "channel":    int(row["channel"]),
                    "frame_type": row["frame_type"].split("(")[0].strip(),
                    "callsign":   row["callsign"],
                })
            except (ValueError, KeyError):
                continue
    return rows


# ═══════════════════════════════════════════════════════════════════════
# SWIMLANE-VISUALISIERUNG
# ═══════════════════════════════════════════════════════════════════════

def visualize(frames: list,
              duration_s: float,
              wav_name: str,
              out_path: str,
              gt_frames: list = None,
              fig_width: float = 12.0,
              fig_height: float = 20.0):
    """
    Erzeugt das Swimlane-Diagramm und speichert es als JPG.

    Layout: Kanäle horizontal (X-Achse), Zeit vertikal (Y-Achse, t=0 oben).
    frames:    dekodierte Frames (aus decode_wav())
    duration_s: Gesamtdauer der WAV in Sekunden
    wav_name:  Dateiname für den Titel
    out_path:  Ausgabepfad (.jpg)
    gt_frames: Ground-Truth-Frames (optional, für Vergleich)
    """
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    fig.patch.set_facecolor(BACKGROUND)
    ax.set_facecolor(BACKGROUND)

    lane_width   = 1.0          # Breite einer Swimlane (Kanal)
    lane_padding = 0.06         # Abstand Block-Rand zur Lane-Kante
    block_width  = lane_width - 2 * lane_padding
    total_width  = N_CHANNELS * lane_width

    # ── Swimlane-Hintergründe ────────────────────────────────────────
    for ch in range(N_CHANNELS):
        x_left = ch * lane_width
        color  = SWIMLANE_BG[ch % 2]
        ax.add_patch(mpatches.Rectangle(
            (x_left, 0), lane_width, duration_s,
            facecolor=color, edgecolor="none", zorder=0
        ))

    # ── Vertikale Swimlane-Trennlinien (deutlich, weiß) ─────────────
    for ch in range(N_CHANNELS + 1):
        lw    = 2.2 if ch in (0, N_CHANNELS) else 1.4
        alpha = 1.0 if ch in (0, N_CHANNELS) else 0.85
        ax.axvline(x=ch * lane_width,
                   color="white", linewidth=lw, alpha=alpha, zorder=2)

    # ── Horizontales Zeitraster (5s, weiß) — hinter den Blöcken ─────
    for t in np.arange(0, duration_s + 0.1, 5.0):
        is_10s = (round(t) % 10 == 0)
        ax.axhline(y=t,
                   color="white",
                   linewidth=1.2 if is_10s else 0.5,
                   alpha=0.55 if is_10s else 0.25,
                   linestyle="-",
                   zorder=1)

    # ── Ground-Truth-Frames (falls vorhanden) — als Umriss ──────────
    if gt_frames:
        for gf in gt_frames:
            ch     = gf["channel"]
            start  = gf["start_s"]
            end    = gf["end_s"]
            height = max(end - start, 0.3)
            x_left = ch * lane_width + lane_padding * 0.5
            ax.add_patch(FancyBboxPatch(
                (x_left, start), block_width + lane_padding, height,
                boxstyle="round,pad=0.02",
                facecolor=MISSED_COLOR, edgecolor="#FF6B6B",
                linewidth=1.0, alpha=0.25, zorder=2
            ))

    # ── Dekodierte Frames als Blöcke ─────────────────────────────────
    for f in frames:
        ch     = f["channel"]
        start  = f["start_s"]
        end    = f["end_s"]
        height = max(end - start, 0.8)   # Mindesthöhe für Lesbarkeit
        ftype  = f["frame_type"]
        call   = f["callsign"]
        color  = FRAME_COLORS.get(ftype, DEFAULT_COLOR)

        x_left = ch * lane_width + lane_padding

        # Block
        block = FancyBboxPatch(
            (x_left, start), block_width, height,
            boxstyle="round,pad=0.04",
            facecolor=color, edgecolor="white",
            linewidth=0.7, alpha=0.92, zorder=3
        )
        ax.add_patch(block)

        # Text im Block — Rufzeichen + Typ
        cx = x_left + block_width / 2
        cy = start + height / 2

        # Rufzeichen (oben, fett)
        ax.text(cx, cy - height * 0.12, call,
                ha="center", va="center",
                fontsize=7.0, fontweight="bold",
                color="white", zorder=4,
                clip_on=True)

        # Frame-Typ (unten, kleiner)
        short_type = ftype[:10] if len(ftype) > 10 else ftype
        ax.text(cx, cy + height * 0.18, short_type,
                ha="center", va="center",
                fontsize=5.5, color="white",
                alpha=0.85, zorder=4,
                clip_on=True)

    # ── X-Achse: Kanal-Labels (oben) ────────────────────────────────
    ax.set_xticks([ch * lane_width + lane_width / 2
                   for ch in range(N_CHANNELS)])
    ax.set_xticklabels(
        [f"CH {ch}\n{channel_frequency(ch):.0f} Hz"
         for ch in range(N_CHANNELS)],
        fontsize=8.5, color=TEXT_COLOR
    )
    ax.tick_params(axis="x", colors=AXIS_COLOR, length=0,
                   top=True, labeltop=True,
                   bottom=False, labelbottom=False)

    # ── Y-Achse: Zeit (t=0 oben → invertieren) ──────────────────────
    ax.set_xlim(0, total_width)
    ax.set_ylim(duration_s, 0)          # invertiert: 0 oben
    ax.set_ylabel("Zeit (s)", fontsize=10, color=TEXT_COLOR, labelpad=8)
    ax.tick_params(axis="y", colors=AXIS_COLOR)
    ax.yaxis.set_major_locator(MultipleLocator(5))
    ax.yaxis.set_minor_locator(MultipleLocator(1))
    for spine in ax.spines.values():
        spine.set_edgecolor("#3D4F63")

    plt.setp(ax.get_yticklabels(), color=TEXT_COLOR, fontsize=8)

    # ── Legende ──────────────────────────────────────────────────────
    legend_patches = [
        mpatches.Patch(facecolor=c, edgecolor="white",
                       linewidth=0.5, label=t)
        for t, c in FRAME_COLORS.items()
    ]
    if gt_frames:
        legend_patches.append(
            mpatches.Patch(facecolor=MISSED_COLOR, edgecolor="#FF6B6B",
                           linewidth=1.0, alpha=0.5,
                           label="GT (erwartet)")
        )
    ax.legend(
        handles=legend_patches,
        loc="lower right",
        fontsize=7,
        framealpha=0.4,
        facecolor="#1A1A2E",
        edgecolor="#4A5568",
        labelcolor=TEXT_COLOR,
        ncol=2,
    )

    # ── Titel ─────────────────────────────────────────────────────────
    n_found = len(frames)
    n_gt    = len(gt_frames) if gt_frames else 0
    rate    = f"  |  {n_found}/{n_gt} = {n_found/n_gt*100:.1f}%" \
              if n_gt > 0 else f"  |  {n_found} Frames"
    title = (f"GUST Swimlane — {os.path.basename(wav_name)}"
             f"  |  {duration_s:.1f}s{rate}")
    ax.set_title(title, fontsize=11, color=TITLE_COLOR,
                 fontweight="bold", pad=14)

    # ── Statistik-Box ─────────────────────────────────────────────────
    type_counts = {}
    for f in frames:
        type_counts[f["frame_type"]] = type_counts.get(f["frame_type"], 0) + 1
    stats_lines = ["Dekodiert:"] + \
                  [f"  {t}: {c}" for t, c in sorted(type_counts.items())]
    stats_text  = "\n".join(stats_lines)
    ax.text(0.01, 0.99, stats_text,
            transform=ax.transAxes,
            fontsize=6.5, color=TEXT_COLOR, alpha=0.75,
            verticalalignment="top",
            bbox=dict(boxstyle="round,pad=0.3",
                      facecolor="#1A1A2E", edgecolor="#4A5568",
                      alpha=0.6))

    # ── Speichern ─────────────────────────────────────────────────────
    plt.tight_layout(pad=1.5)
    plt.savefig(out_path, dpi=150, format="jpeg",
                facecolor=BACKGROUND, bbox_inches="tight",
                pil_kwargs={"quality": 92, "optimize": True})
    plt.close(fig)
    print(f"{_ts()}  Gespeichert: {out_path}", flush=True)


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="gust_visualize.py",
        description="GUST WAV-Datei Swimlane-Visualisierung",
    )
    parser.add_argument("wav", help="WAV-Datei (von gust_stresstest.py)")
    parser.add_argument("--out", metavar="PFAD",
                        help="Ausgabe-JPG (Standard: <wav>.jpg)")
    parser.add_argument("--csv", metavar="PFAD",
                        help="Ground-Truth-CSV von gust_stresstest.py "
                             "(optional, zeigt erwartete Frames als Umriss)")
    parser.add_argument("--window", type=float, default=WINDOW_S,
                        help=f"Fenstergröße in Sekunden (Standard: {WINDOW_S})")
    parser.add_argument("--step",   type=float, default=STEP_S,
                        help=f"Schrittweite in Sekunden (Standard: {STEP_S})")
    parser.add_argument("--width",  type=float, default=12.0,
                        help="Bildbreite in Zoll (Standard: 12)")
    parser.add_argument("--height", type=float, default=20.0,
                        help="Bildhöhe in Zoll (Standard: 20)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Ausführliche Ausgabe")
    args = parser.parse_args()

    # WAV laden
    if not os.path.exists(args.wav):
        print(f"FEHLER: Datei nicht gefunden: {args.wav}", file=sys.stderr)
        sys.exit(1)

    print(f"{_ts()}  Lade {args.wav} ...", flush=True)
    audio, sr = load_wav(args.wav)
    duration_s = len(audio) / SAMPLE_RATE
    print(f"{_ts()}  {duration_s:.1f}s  |  {len(audio)} Samples @ {sr} Hz",
          flush=True)

    # Dekodieren
    frames = decode_wav(audio,
                        window_s=args.window,
                        step_s=args.step,
                        verbose=True)

    # Ground-Truth laden (optional)
    gt_frames = None
    if args.csv:
        if os.path.exists(args.csv):
            gt_frames = load_csv(args.csv)
            print(f"{_ts()}  Ground-Truth: {len(gt_frames)} Frames aus {args.csv}",
                  flush=True)
        else:
            print(f"WARNUNG: CSV nicht gefunden: {args.csv}", file=sys.stderr)

    # Ausgabepfad
    out_path = args.out or os.path.splitext(args.wav)[0] + ".jpg"

    # Visualisierung
    print(f"{_ts()}  Erstelle Swimlane-Diagramm ...", flush=True)
    visualize(
        frames     = frames,
        duration_s = duration_s,
        wav_name   = args.wav,
        out_path   = out_path,
        gt_frames  = gt_frames,
        fig_width  = args.width,
        fig_height = args.height,
    )

    print(f"\n  Frames dekodiert: {len(frames)}")
    if gt_frames:
        print(f"  Ground-Truth:     {len(gt_frames)}")
        print(f"  Dekodierrate:     {len(frames)/len(gt_frames)*100:.1f}%")
    print(f"  Ausgabe:          {out_path}")


if __name__ == "__main__":
    main()