# ════════════════════════════════════════════════════════════════════════
# GUST — Makefile (Entwickler-Shortcuts)
# OE3GAS — Amateurfunk-Digitalprotokoll für KW-Telemetrie
# ────────────────────────────────────────────────────────────────────────
# Verwendung:
#     make help         Alle Ziele auflisten
#     make install      pip install -e ".[dev]"
#     make test         pytest tests/ -v
#     make run          python gust.py daemon --sim
# ════════════════════════════════════════════════════════════════════════

PYTHON ?= python3
PIP    ?= $(PYTHON) -m pip

.PHONY: help install install-rpi test test-frame test-mod run run-dry \
        devices clean lint format

# Standard-Ziel: Hilfe
.DEFAULT_GOAL := help

help:  ## Diese Hilfe anzeigen
	@echo "GUST Makefile — verfügbare Ziele:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ── Installation ────────────────────────────────────────────────────────
install:  ## Editable-Install mit Entwicklungs-Extras
	$(PIP) install -e ".[dev]"

install-rpi:  ## Raspberry Pi: Install mit RPi-Extras (PEP 668 umgehen)
	$(PIP) install --break-system-packages -e ".[rpi]"

# ── Tests ───────────────────────────────────────────────────────────────
test:  ## pytest-Suite ausführen (tests/-Verzeichnis)
	$(PYTHON) -m pytest tests/ -v

test-frame:  ## Selbsttest gust_frame.py (Encoder/Decoder, CRC, RS-FEC)
	$(PYTHON) gust_frame.py

test-mod:  ## Selbsttest gust_modulator.py (MFSK Mod/Demod, Breitband-SYNC)
	$(PYTHON) gust_modulator.py

# ── Daemon-Start ────────────────────────────────────────────────────────
run:  ## Daemon mit Simulator starten (kein Hardware nötig)
	$(PYTHON) gust.py daemon --sim

run-dry:  ## Daemon dry-run (kein TX, nur Web + Simulator)
	$(PYTHON) gust.py daemon --sim --dry-run

devices:  ## Verfügbare Audiogeräte anzeigen
	$(PYTHON) gust.py devices

# ── Aufräumen ───────────────────────────────────────────────────────────
clean:  ## __pycache__, *.pyc, dist/, *.egg-info entfernen
	@find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -delete 2>/dev/null || true
	@rm -rf dist/ build/ *.egg-info/ .pytest_cache/ .ruff_cache/
	@echo "Aufgeräumt."

# ── Lint / Format (optional — nur wenn ruff installiert) ────────────────
lint:  ## Code-Lint mit ruff (falls installiert)
	@command -v ruff >/dev/null 2>&1 && ruff check . || \
		echo "ruff nicht installiert — überspringen (pip install ruff)"

format:  ## Code-Formatierung mit ruff (falls installiert)
	@command -v ruff >/dev/null 2>&1 && ruff format . || \
		echo "ruff nicht installiert — überspringen (pip install ruff)"
