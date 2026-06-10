# Claude Code Prompt: LDPC Etappe 1 — gust_fec.py (Interface + RS-Wrapper)

## Ziel dieser Etappe

Neue Datei `gust_fec.py` erstellen — das FEC-Abstraktions-Interface.
Kein bestehender Code wird geändert. RS bleibt in `gust_frame.py`
unverändert produktiv.

`gust_fec.py` definiert:
1. `FECBackend` — abstrakte Basisklasse (Interface)
2. `ReedSolomonFEC` — Wrapper um den bestehenden RS-Code
3. `get_fec_backend(name)` — Factory-Funktion

---

## Datei erstellen: `gust_fec.py`

```python
#!/usr/bin/env python3
"""
GUST — FEC-Backend-Abstraktion                              ADR-24
═══════════════════════════════════════════════════════════════════════
Abstrakte Basisklasse und Implementierungen für Forward Error Correction.

Backends:
  ReedSolomonFEC  — RS(255,223), shortened, 16 Byte-Fehler (produktiv)
  LDPCFecBackend  — LDPC Rate 3/4, iterativ  (experimentell, gust_ldpc.py)

Verwendung:
  from gust_fec import get_fec_backend
  fec = get_fec_backend("rs")        # oder "ldpc"
  encoded = fec.encode(data)
  decoded = fec.decode(encoded)

Konfiguration via gateway.json:
  "fec": "rs"     ← Standard, produktiv
  "fec": "ldpc"   ← experimentell, opt-in (erfordert gust_ldpc.py)

Hinweis: TX und RX müssen dasselbe Backend verwenden.
         LDPC ist nicht kompatibel mit RS — nur in geschlossenen
         Testaufbauten verwenden bis es als v1.0-Standard definiert wird.

Referenz: ADR-24, gust_knowledge.md §<N> (FEC-Evaluation Juni 2026)
"""

from abc import ABC, abstractmethod
from typing import Optional


# ══════════════════════════════════════════════════════════════════════
# INTERFACE
# ══════════════════════════════════════════════════════════════════════

class FECBackend(ABC):
    """
    Abstrakte Basisklasse für FEC-Backends.

    Jedes Backend implementiert encode() und decode().
    Das overhead-Attribut gibt an wie viele Bytes encode() hinzufügt.
    """

    #: Anzahl Byte die encode() zum Input hinzufügt (Paritätsbytes)
    overhead: int = 0

    #: Kurzname für Logging und gateway.json
    name: str = "abstract"

    @abstractmethod
    def encode(self, data: bytes) -> bytes:
        """
        Daten mit FEC kodieren.
        Gibt data + Paritätsbytes zurück (len = len(data) + overhead).
        Wirft RuntimeError wenn Backend nicht verfügbar.
        """
        ...

    @abstractmethod
    def decode(self, data: bytes) -> bytes:
        """
        FEC-geschützte Daten dekodieren und Fehler korrigieren.
        Gibt die originalen Nutzdaten zurück (ohne Paritätsbytes).
        Wirft eine backend-spezifische Exception bei nicht korrigierbaren Fehlern:
          ReedSolomonFEC → reedsolo.ReedSolomonError
          LDPCFecBackend → LDPCDecodeError
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(overhead={self.overhead})"


# ══════════════════════════════════════════════════════════════════════
# RS-BACKEND (Wrapper um gust_frame.py RS-Code)
# ══════════════════════════════════════════════════════════════════════

class ReedSolomonFEC(FECBackend):
    """
    Reed-Solomon FEC Backend — RS(255,223), shortened.
    Wrapper um den bestehenden rs_encode()/rs_decode()-Code in gust_frame.py.

    Korrigiert bis zu 16 Byte-Fehler.
    Overhead: 32 Byte (RS_OVERHEAD aus gust_frame.py).
    Sendedauer: +32 Byte × (8 bit / 3 bit/Symbol) / 31.25 Baud ≈ +2,7 s
    """

    name = "rs"

    def __init__(self):
        from gust_frame import rs_encode, rs_decode, RS_OVERHEAD, _RS_AVAILABLE
        if not _RS_AVAILABLE:
            raise RuntimeError(
                "reedsolo nicht installiert — RS-FEC nicht verfügbar. "
                "pip install reedsolo"
            )
        self._encode    = rs_encode
        self._decode    = rs_decode
        self.overhead   = RS_OVERHEAD   # 32

    def encode(self, data: bytes) -> bytes:
        """RS(255,223) Encoding. Gibt data + 32 Byte Parität zurück."""
        return self._encode(data)

    def decode(self, data: bytes) -> bytes:
        """RS(255,223) Dekodierung. Korrigiert bis zu 16 Byte-Fehler."""
        return self._decode(data)


# ══════════════════════════════════════════════════════════════════════
# FACTORY
# ══════════════════════════════════════════════════════════════════════

# Registry: name → Klasse
_BACKENDS: dict = {
    "rs": ReedSolomonFEC,
}

def register_backend(name: str, cls) -> None:
    """
    Externes Backend registrieren (z.B. aus gust_ldpc.py).

    Beispiel (in gust_ldpc.py am Modulende):
        from gust_fec import register_backend
        register_backend("ldpc", LDPCFecBackend)
    """
    _BACKENDS[name] = cls


def get_fec_backend(name: str = "rs") -> FECBackend:
    """
    FEC-Backend per Name instanziieren.

    Args:
        name: "rs" (Standard) oder "ldpc" (experimentell, gust_ldpc.py nötig)

    Returns:
        FECBackend-Instanz

    Raises:
        ValueError: Unbekannter Backend-Name
        RuntimeError: Backend-Abhängigkeit nicht erfüllt
        ImportError: gust_ldpc.py nicht vorhanden (bei name="ldpc")
    """
    name = name.lower().strip()

    # LDPC: lazy import damit gust_ldpc.py optional bleibt
    if name == "ldpc" and "ldpc" not in _BACKENDS:
        try:
            import gust_ldpc  # registriert sich selbst via register_backend()
        except ImportError:
            raise ImportError(
                "gust_ldpc.py nicht gefunden — LDPC-Backend nicht verfügbar. "
                "Bitte gust_ldpc.py im GUST-Verzeichnis ablegen."
            )

    if name not in _BACKENDS:
        available = ", ".join(sorted(_BACKENDS.keys()))
        raise ValueError(
            f"Unbekanntes FEC-Backend '{name}'. "
            f"Verfügbar: {available}"
        )

    return _BACKENDS[name]()


# ══════════════════════════════════════════════════════════════════════
# SELBSTTEST
# ══════════════════════════════════════════════════════════════════════

def _run_tests() -> bool:
    """Selbsttest: RS-Backend encode/decode Roundtrip + Fehlerkorrektur."""
    import sys

    print("── gust_fec.py Selbsttest ──────────────────────────────")
    errors = []

    # ── Test 1: Factory ──────────────────────────────────────────────
    try:
        fec = get_fec_backend("rs")
        assert fec.name == "rs", f"Name falsch: {fec.name}"
        assert fec.overhead == 32, f"Overhead falsch: {fec.overhead}"
        print(f"  ✓  get_fec_backend('rs') → {fec}")
    except Exception as e:
        errors.append(f"Factory: {e}")
        print(f"  ✗  Factory: {e}")

    # ── Test 2: Encode/Decode Roundtrip ─────────────────────────────
    try:
        fec  = get_fec_backend("rs")
        data = b"\x01OE3GAS\x00\x14\x28\x00\x00\x00\x00"  # WEATHER-Frame-Body
        enc  = fec.encode(data)
        assert len(enc) == len(data) + fec.overhead, \
            f"Länge falsch: {len(enc)} != {len(data) + fec.overhead}"
        dec  = fec.decode(enc)
        assert dec == data, "Roundtrip fehlgeschlagen"
        print(f"  ✓  Encode/Decode Roundtrip  "
              f"{len(data)} B → {len(enc)} B → {len(dec)} B")
    except Exception as e:
        errors.append(f"Roundtrip: {e}")
        print(f"  ✗  Roundtrip: {e}")

    # ── Test 3: Fehlerkorrektur (5 Byte-Fehler) ──────────────────────
    try:
        fec       = get_fec_backend("rs")
        data      = b"\x02OE3GAS\x48\x21\x99\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        enc       = fec.encode(data)
        corrupted = bytearray(enc)
        for i in [2, 5, 10, 15, 20]:
            corrupted[i] ^= 0xFF   # 5 Byte-Fehler injizieren
        dec = fec.decode(bytes(corrupted))
        assert dec == data, "Fehlerkorrektur fehlgeschlagen"
        print(f"  ✓  Fehlerkorrektur: 5 Byte-Fehler injiziert → korrekt dekodiert")
    except Exception as e:
        errors.append(f"Fehlerkorrektur: {e}")
        print(f"  ✗  Fehlerkorrektur: {e}")

    # ── Test 4: Ungültiger Backend-Name ─────────────────────────────
    try:
        get_fec_backend("xyz")
        errors.append("ValueError erwartet für 'xyz', aber nicht geworfen")
        print(f"  ✗  ValueError für 'xyz' nicht geworfen")
    except ValueError:
        print(f"  ✓  ValueError für unbekannten Namen korrekt")
    except Exception as e:
        errors.append(f"Unerwartete Exception: {e}")

    # ── Ergebnis ─────────────────────────────────────────────────────
    print(f"{'─' * 54}")
    if errors:
        print(f"  ✗  {len(errors)} Fehler:")
        for e in errors:
            print(f"     - {e}")
    else:
        print(f"  ✓  Alle Tests bestanden.")
    print(f"{'─' * 54}")
    return len(errors) == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run_tests() else 1)
```

---

## Verifikation

```powershell
# 1. Syntax-Check
python -m py_compile gust_fec.py

# 2. Selbsttest
py gust_fec.py
# Erwartung: alle 4 Tests ✓, Exit-Code 0

# 3. Import-Check aus gust_frame.py-Kontext
python -c "from gust_fec import get_fec_backend, ReedSolomonFEC; print('OK')"

# 4. Sicherstellen dass gust_frame.py unverändert ist
python -m py_compile gust_frame.py
py gust_frame.py
# Erwartung: Selbsttest unverändert ✓
```

---

## Wichtig

- **Nur neue Datei erstellen** — kein bestehender Code wird geändert
- `gust_frame.py` bleibt unverändert produktiv
- Die Integration in `gust_frame.py` und `gust_modulator.py` kommt in Etappe 3
- `gust_ldpc.py` kommt in Etappe 2
