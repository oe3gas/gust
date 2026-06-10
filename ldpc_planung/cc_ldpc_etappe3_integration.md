# ✅ FREIGEGEBEN — alle Voraussetzungen erfuellt (Juni 2026)
#
# P8-13 abgeschlossen:
#   - _fft_detect_symbol_soft()  → Ton-LLR shape (8,)       gust_modulator.py
#   - symbol_llr_to_bit_llr()    → Bit-LLR shape (3,)       gust_modulator.py
#   - symbols_to_bit_llr_array() → Bit-LLR flach (N×3)      gust_modulator.py
#   - gust_ldpc.py n=255         → 85 Symbole/Block, kein Padding
#
# Soft-Decision-Kette vollstaendig:
#   85 MFSK-Symbole → 255 Bit-LLR → 1 LDPC-Block (191 Daten-Bit)
#
═══════════════════════════════════════════════════════════════════════

# Claude Code Prompt: LDPC Etappe 3 — Integration in gust_frame.py + gateway.json

## Voraussetzung

Etappen 1 und 2 abgeschlossen:
- `gust_fec.py` Selbsttest ✓
- `gust_ldpc.py` Selbsttest ✓ (alle Roundtrip-Tests grün)

## Ziel dieser Etappe

`gust_frame.py` so erweitern dass es das FEC-Backend aus `gust_fec.py`
verwendet — opt-in per Modul-Level-Variable, steuerbar aus `gateway.json`.

**RS bleibt der Default. Keine Verhaltensänderung ohne explizite Konfiguration.**

---

## Änderung 1: gust_frame.py — FEC-Backend wählbar machen

### 1a. Import am Modulanfang ergänzen

Nach den bestehenden Imports (nach dem reedsolo-try/except-Block):

```python
# ── FEC-Backend (ADR-24) ──────────────────────────────────────────────
# Standard: RS (produktiv). LDPC: opt-in per set_fec_backend().
# Wird von gust.py beim Start gesetzt wenn gateway.json "fec": "ldpc" enthält.
_fec_backend = None   # None = lazy init beim ersten Aufruf → RS

def set_fec_backend(name: str) -> None:
    """
    FEC-Backend global setzen.
    Wird einmalig beim Daemon-Start aufgerufen (aus gust.py).

    Args:
        name: "rs" (Standard) oder "ldpc" (experimentell)
    """
    global _fec_backend
    from gust_fec import get_fec_backend
    _fec_backend = get_fec_backend(name)
    import logging
    logging.getLogger("gust.frame").info(
        "[FEC] Backend gewechselt: %s  (overhead=%d B)",
        name, _fec_backend.overhead
    )

def _get_fec() -> "FECBackend":
    """Lazy-Init: FEC-Backend holen, RS als Default."""
    global _fec_backend
    if _fec_backend is None:
        from gust_fec import get_fec_backend
        _fec_backend = get_fec_backend("rs")
    return _fec_backend
```

### 1b. rs_encode() und rs_decode() auf FEC-Backend umleiten

Die bestehenden Funktionen `rs_encode()` und `rs_decode()` werden
zu dünnen Weiterleitungen — so bleibt die Schnittstelle für
`gust_modulator.py` unverändert:

```python
# ALT:
def rs_encode(data: bytes) -> bytes:
    """Wendet Reed-Solomon(255,223) auf data an. ..."""
    if not _RS_AVAILABLE:
        raise RuntimeError("reedsolo nicht installiert")
    return bytes(_rs_codec.encode(data))

def rs_decode(data: bytes) -> bytes:
    """Dekodiert RS-geschützte Daten. ..."""
    if not _RS_AVAILABLE:
        raise RuntimeError("reedsolo nicht installiert")
    decoded, _, _ = _rs_codec.decode(data)
    return bytes(decoded)

# NEU:
def rs_encode(data: bytes) -> bytes:
    """
    FEC-Kodierung (Backend per set_fec_backend() wählbar, Standard: RS).
    Gibt data + Paritätsbytes zurück.
    """
    return _get_fec().encode(data)

def rs_decode(data: bytes) -> bytes:
    """
    FEC-Dekodierung (Backend per set_fec_backend() wählbar, Standard: RS).
    Gibt die originalen Nutzdaten zurück.
    Wirft backend-spezifische Exception bei nicht korrigierbaren Fehlern.
    """
    return _get_fec().decode(data)
```

**Wichtig:** Den bestehenden `_rs_codec`-Block und `RS_OVERHEAD`-Konstante
**nicht** entfernen — `RS_OVERHEAD` wird von `gust_modulator.py` importiert
und ist dort für die Multi-Try-Dekodierung nötig. Die Konstante bleibt,
der `_rs_codec` bleibt (wird von `ReedSolomonFEC` intern verwendet).

### 1c. RS_OVERHEAD dynamisch machen

`RS_OVERHEAD` wird in `gust_modulator.py` für die Berechnung der
minimalen Frame-Länge verwendet. Bei LDPC ist der Overhead anders.
Daher eine neue Funktion ergänzen:

```python
def get_fec_overhead() -> int:
    """
    Aktuellen FEC-Overhead in Bytes zurückgeben.
    Für RS: RS_OVERHEAD (32).
    Für LDPC: abhängig von der Blockgröße.
    """
    return _get_fec().overhead if _get_fec().overhead > 0 else RS_OVERHEAD
```

---

## Änderung 2: gust_modulator.py — get_fec_overhead() verwenden

In `gust_modulator.py`, in der Multi-Try-Dekodierungsschleife:

```python
# ALT:
from gust_frame import (
    symbols_to_bytes, rs_decode, _RS_AVAILABLE,
    RS_OVERHEAD, parse_frame, decode_payload,
)
# ...
min_len = RS_OVERHEAD + 7

# NEU:
from gust_frame import (
    symbols_to_bytes, rs_decode, _RS_AVAILABLE,
    RS_OVERHEAD, get_fec_overhead, parse_frame, decode_payload,
)
# ...
min_len = get_fec_overhead() + 7
```

Nur diese eine Zeile ändern — der Rest der Dekodierungsschleife
bleibt unverändert.

---

## Änderung 3: gust.py — FEC-Backend aus gateway.json lesen

In `main()`, nach `cfg = load_config(...)`:

```python
# FEC-Backend aus Konfiguration setzen (Standard: "rs")
fec_name = cfg.get("fec", "rs").lower()
if fec_name != "rs":
    try:
        from gust_frame import set_fec_backend
        set_fec_backend(fec_name)
        log.info("[FEC] Backend: %s (aus gateway.json)", fec_name)
    except Exception as e:
        log.warning("[FEC] Backend '%s' nicht verfügbar: %s — Fallback auf RS",
                    fec_name, e)
```

---

## gateway.json — Neues optionales Feld

In `gateway.json.example` folgendes Feld ergänzen (kommentiert):

```json
{
  "callsign": "OE3GAS",
  "fec": "rs",
  ...
}
```

Mit folgendem Kommentar in GETTING_STARTED.md oder gateway.json.example:
```
"fec": "rs"     Standard — RS(255,223), 16 Byte-Fehler, produktiv
"fec": "ldpc"   Experimentell — LDPC Rate 3/4, benötigt gust_ldpc.py
                Achtung: TX und RX müssen gleiches Backend verwenden!
```

---

## Verifikation

```powershell
# 1. Syntax-Check aller drei Dateien
python -m py_compile gust_frame.py
python -m py_compile gust_modulator.py
python -m py_compile gust.py

# 2. gust_frame.py Selbsttest — RS muss unverändert funktionieren
py gust_frame.py
# Erwartung: alle Tests ✓, keine Regression

# 3. RS-Standard-Verhalten (kein gateway.json-Eintrag)
python -c "
from gust_frame import rs_encode, rs_decode, RS_OVERHEAD
data = b'OE3GAS\x01' + bytes(14)
enc = rs_encode(data)
dec = rs_decode(enc)
assert dec == data
print(f'RS Standard: {len(data)}B → {len(enc)}B → {len(dec)}B ✓')
print(f'Overhead: {len(enc)-len(data)} (erwartet: {RS_OVERHEAD})')
"

# 4. LDPC opt-in
python -c "
import gust_ldpc   # registriert sich
from gust_frame import set_fec_backend, rs_encode, rs_decode
set_fec_backend('ldpc')
data = b'OE3GAS\x01' + bytes(14)
enc = rs_encode(data)   # jetzt LDPC intern
dec = rs_decode(enc)
assert dec == data
print(f'LDPC opt-in: {len(data)}B → {len(enc)}B → {len(dec)}B ✓')
"

# 5. Daemon-Start mit RS (Standard)
py gust.py --dry-run --sim
# Kein FEC-Log-Output erwartet (RS ist default, keine Meldung)

# 6. Daemon-Start mit LDPC (gateway.json: "fec": "ldpc")
# gateway.json temporär bearbeiten: "fec": "ldpc" ergänzen
py gust.py --dry-run --sim
# Erwartung: "[FEC] Backend: ldpc (aus gateway.json)" im Log
```

---

## Wichtig

- `RS_OVERHEAD` und `_rs_codec` in gust_frame.py **nicht entfernen**
- Die bestehende Multi-Try-Schleife in gust_modulator.py **unverändert**
  lassen — nur `min_len` auf `get_fec_overhead()` umstellen
- **Kein Protokollbruch im Default-Betrieb** — ohne gateway.json-Änderung
  verhält sich alles exakt wie vor dieser Etappe
