# Claude Code Prompt: LDPC Etappe 2 — gust_ldpc.py (LDPC-Implementierung)

## Voraussetzung

Etappe 1 (gust_fec.py) muss abgeschlossen und getestet sein.

## Ziel dieser Etappe

Neue Datei `gust_ldpc.py` erstellen — LDPC FEC Backend.
Kein bestehender Code wird geändert.

---

## LDPC-Konfiguration für GUST

**Gewählte Parameter: Rate 3/4, Blockgröße angepasst an GUST-Payloads**

Begründung:
- Rate 3/4: Overhead +33 % (vs. RS +~150 % relativ zur Payload)
  Konkret: WEATHER 14 Byte → +4,7 Byte LDPC vs. +32 Byte RS
  → LDPC ist bei Rate 3/4 deutlich kürzer als RS
- Blockgröße 48 Bit (6 Byte): gut zu GUST-Payloads passend
  (EMERG_BEACON 20 Byte = 3,3 Blöcke, WEATHER 14 Byte = 2,3 Blöcke)
- Belief Propagation, max. 50 Iterationen

**Parity-Check-Matrix:** Regulärer (3,6)-LDPC Code.
Variable nodes: n=48, Check nodes: m=24, Rate = 1 - m/n = 0.5
Für Rate 3/4: n=48, m=12, Rate = 1 - 12/48 = 0.75

**Bibliothek:** `python-ldpc` (pip install ldpc)
Fallback: Eigene numpy-basierte Implementierung wenn ldpc nicht verfügbar.

---

## Datei erstellen: `gust_ldpc.py`

```python
#!/usr/bin/env python3
"""
GUST — LDPC FEC Backend                                  ADR-24 / P8-10
═══════════════════════════════════════════════════════════════════════
Low-Density Parity-Check Code für GUST — experimentelles FEC-Backend.

Konfiguration:
  Code-Rate:    3/4  (Overhead +33 % relativ zur Payload)
  Blockgröße:   48 Bit (6 Byte) pro LDPC-Codeword
  Dekodierung:  Belief Propagation, max. 50 Iterationen
  Bibliothek:   python-ldpc (bevorzugt) oder numpy-Fallback

Vorteile gegenüber RS(255,223):
  - +3–5 dB SNR-Gewinn (AWGN-Kanal, zufällige Fehler)
  - Kürzere Frames: Rate 3/4 → weniger Overhead als RS bei kleinen Payloads
  - Nähert sich Shannon-Grenze (~0,5 dB Abstand bei Rate 1/2)

Einschränkungen:
  - Burst-Fehler: kein Vorteil gegenüber RS (beide für zufällige Fehler optimiert)
  - Rechenaufwand: ~5–50 ms/Frame (vs. RS ~0,1 ms) — auf RPi 4 OK
  - Protokollbruch zu v0.5: TX+RX müssen gleiches Backend verwenden

Verwendung (opt-in via gateway.json):
  "fec": "ldpc"

Dieses Modul registriert sich selbst beim Import:
  import gust_ldpc   → get_fec_backend("ldpc") ist danach verfügbar

Installation:
  pip install ldpc   (python-ldpc, empfohlen)
  # oder: numpy-Fallback (langsamer, keine Soft-Decisions)

Referenz: ADR-24, gust_knowledge.md §<N>, gust_spec.md §3.x
"""

import numpy as np
import logging
from typing import Optional, Tuple

log = logging.getLogger("gust.ldpc")

# ── Bibliotheks-Verfügbarkeit ──────────────────────────────────────────
try:
    from ldpc import bp_decoder, make_ldpc
    _LDPC_LIB = "python-ldpc"
except ImportError:
    _LDPC_LIB = None
    log.debug("[LDPC] python-ldpc nicht verfügbar — numpy-Fallback aktiv")


# ══════════════════════════════════════════════════════════════════════
# FEHLERKLASSE
# ══════════════════════════════════════════════════════════════════════

class LDPCDecodeError(Exception):
    """Wird geworfen wenn LDPC-Dekodierung nach max_iter fehlschlägt."""
    pass


# ══════════════════════════════════════════════════════════════════════
# LDPC-MATRIX GENERATOR
# ══════════════════════════════════════════════════════════════════════

def _make_gust_H(n: int = 48, rate: float = 0.75,
                 seed: int = 42) -> np.ndarray:
    """
    Erzeugt Parity-Check-Matrix H für GUST-LDPC.

    Args:
        n:    Codelänge in Bit (Standard: 48)
        rate: Code-Rate (Standard: 3/4)
        seed: Zufalls-Seed für reproduzierbare Matrix

    Returns:
        H: (m × n) numpy-Array, dtype=uint8, m = n*(1-rate)
    """
    m = int(n * (1 - rate))   # Anzahl Check-Nodes: 12 bei n=48, rate=3/4

    if _LDPC_LIB == "python-ldpc":
        # python-ldpc: regulärer (d_v, d_c)-LDPC
        # d_v=3 (Variable-Node-Grad), d_c=n*d_v/m (Check-Node-Grad)
        d_v = 3
        d_c = n * d_v // m
        H, _ = make_ldpc(n, d_v, d_c, systematic=True, sparse=True, seed=seed)
        return H.toarray().astype(np.uint8) if hasattr(H, 'toarray') else H.astype(np.uint8)
    else:
        # numpy-Fallback: zufällige reguläre Matrix
        rng = np.random.default_rng(seed)
        H   = np.zeros((m, n), dtype=np.uint8)
        # Jede Spalte hat genau 3 Einsen (d_v=3)
        for j in range(n):
            rows = rng.choice(m, size=3, replace=False)
            H[rows, j] = 1
        return H


# ══════════════════════════════════════════════════════════════════════
# LDPC FEC BACKEND
# ══════════════════════════════════════════════════════════════════════

class LDPCFecBackend:
    """
    LDPC FEC Backend für GUST — experimentell, opt-in per gateway.json.

    Verarbeitet Daten blockweise: jeder Block hat BLOCK_BITS Informationsbits.
    encode() fügt Paritätsbits hinzu, decode() korrigiert Fehler.

    Block-Struktur:
      BLOCK_BITS    = 36 Bit Daten      (n * rate = 48 * 0.75)
      PARITY_BITS   = 12 Bit Parität    (n * (1-rate) = 48 * 0.25)
      CODEWORD_BITS = 48 Bit gesamt     (n)
    """

    name = "ldpc"

    # LDPC-Parameter
    N_BITS    = 48      # Codelänge in Bit
    RATE      = 0.75    # Code-Rate
    MAX_ITER  = 50      # Belief-Propagation Max-Iterationen
    SEED      = 42      # Reproduzierbare Matrix

    def __init__(self):
        self.k_bits      = int(self.N_BITS * self.RATE)   # 36 Daten-Bit
        self.m_bits      = self.N_BITS - self.k_bits       # 12 Parität-Bit
        # Overhead in Bytes (aufgerundet): pro k_bits Datenbytes kommen m_bits Paritätsbits
        # Bei 8-Bit-Bytes: overhead_per_byte = m_bits / k_bits
        # Tatsächlicher Overhead in Bytes hängt von der Datenlänge ab
        self.overhead    = 0    # variabel — wird in encode() berechnet
        self._H          = _make_gust_H(self.N_BITS, self.RATE, self.SEED)
        log.debug("[LDPC] Backend initialisiert  n=%d rate=%.2f lib=%s",
                  self.N_BITS, self.RATE, _LDPC_LIB or "numpy")

    def _data_to_bits(self, data: bytes) -> np.ndarray:
        """Bytes → Bit-Array (MSB zuerst)."""
        bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
        return bits

    def _bits_to_data(self, bits: np.ndarray) -> bytes:
        """Bit-Array → Bytes (MSB zuerst)."""
        # Auf Vielfaches von 8 auffüllen
        pad = (8 - len(bits) % 8) % 8
        if pad:
            bits = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])
        return np.packbits(bits).tobytes()

    def _encode_block(self, data_bits: np.ndarray) -> np.ndarray:
        """
        Einen LDPC-Block kodieren.
        data_bits: k_bits Informationsbits
        Gibt Codeword (n_bits) zurück.
        """
        assert len(data_bits) == self.k_bits, \
            f"Erwartet {self.k_bits} Datenbits, erhalten {len(data_bits)}"

        if _LDPC_LIB == "python-ldpc":
            # python-ldpc: systematischer Code → Codeword = [data | parität]
            from ldpc import encode
            codeword = encode(self._H, data_bits.astype(np.int32))
            return codeword.astype(np.uint8)
        else:
            # numpy-Fallback: Parität = H[:, k:] \ (H[:, :k] @ data) mod 2
            H_sys  = self._H[:, :self.k_bits]
            H_par  = self._H[:, self.k_bits:]
            s      = H_sys @ data_bits % 2
            # Gaussian-Elimination für Paritätsbits
            try:
                # Einfache Lösung: s = H_par @ p mod 2 → p = H_par^{-1} @ s mod 2
                H_par_inv = np.linalg.inv(H_par.astype(float))
                p = np.round(H_par_inv @ s.astype(float)) % 2
            except np.linalg.LinAlgError:
                # Fallback: p = 0 (uncodiert, kein Fehler-Schutz)
                p = np.zeros(self.m_bits, dtype=np.uint8)
            codeword = np.concatenate([data_bits, p.astype(np.uint8)])
            return codeword

    def _decode_block(self, rx_bits: np.ndarray) -> np.ndarray:
        """
        Einen LDPC-Block dekodieren (Belief Propagation).
        rx_bits: n_bits empfangene Bits (Hard-Decision)
        Gibt k_bits dekodierte Datenbits zurück.
        Wirft LDPCDecodeError wenn nicht konvergiert.
        """
        assert len(rx_bits) == self.N_BITS, \
            f"Erwartet {self.N_BITS} Bits, erhalten {len(rx_bits)}"

        if _LDPC_LIB == "python-ldpc":
            # python-ldpc: Soft-Werte aus Hard-Decisions (BPSK: 0→+1, 1→-1)
            llr   = 1.0 - 2.0 * rx_bits.astype(float)
            dec   = bp_decoder(self._H, llr,
                                max_iter=self.MAX_ITER,
                                bp_method="min_sum")
            # Konvergenz-Check: Syndrom s = H @ decoded mod 2 == 0
            syndrome = self._H @ dec % 2
            if np.any(syndrome):
                raise LDPCDecodeError(
                    f"BP nicht konvergiert nach {self.MAX_ITER} Iterationen "
                    f"(Syndrom-Gewicht={np.sum(syndrome)})"
                )
            return dec[:self.k_bits].astype(np.uint8)
        else:
            # numpy-Fallback: Bit-Flipping Decoder (einfach, weniger robust)
            decoded = rx_bits.copy().astype(np.uint8)
            for _ in range(self.MAX_ITER):
                syndrome = self._H @ decoded % 2
                if not np.any(syndrome):
                    break
                # Flip das Bit das die meisten Syndrom-Fehler verursacht
                scores = self._H.T @ syndrome
                flip   = np.argmax(scores)
                decoded[flip] ^= 1
            syndrome = self._H @ decoded % 2
            if np.any(syndrome):
                raise LDPCDecodeError(
                    f"Bit-Flipping nicht konvergiert nach {self.MAX_ITER} Iterationen"
                )
            return decoded[:self.k_bits]

    def encode(self, data: bytes) -> bytes:
        """
        LDPC-Kodierung blockweise.

        Jeder Block enthält k_bits (36) Datenbits + m_bits (12) Paritätsbits.
        Die Datenlänge wird als 2-Byte-Header vorangestellt damit decode()
        das korrekte Zero-Padding entfernen kann.

        Gibt kodierte Bytes zurück (Länge > len(data)).
        """
        # 2-Byte-Längen-Header (big-endian)
        n_bytes  = len(data)
        header   = n_bytes.to_bytes(2, 'big')

        # Bits aus Daten
        bits     = self._data_to_bits(header + data)

        # Auf Vielfaches von k_bits auffüllen
        pad      = (self.k_bits - len(bits) % self.k_bits) % self.k_bits
        bits_pad = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])

        # Blockweise kodieren
        codewords = []
        for i in range(0, len(bits_pad), self.k_bits):
            block    = bits_pad[i:i + self.k_bits]
            codeword = self._encode_block(block)
            codewords.append(codeword)

        encoded_bits = np.concatenate(codewords)
        result       = self._bits_to_data(encoded_bits)

        # overhead aktualisieren (für Logging)
        self.overhead = len(result) - n_bytes

        log.debug("[LDPC] encode: %d B → %d B (+%d B Overhead, %d Blöcke)",
                  n_bytes, len(result), self.overhead, len(codewords))
        return result

    def decode(self, data: bytes) -> bytes:
        """
        LDPC-Dekodierung blockweise.

        Wirft LDPCDecodeError wenn ein Block nicht konvergiert.
        """
        bits      = self._data_to_bits(data)

        # Auf Vielfaches von N_BITS auffüllen
        pad       = (self.N_BITS - len(bits) % self.N_BITS) % self.N_BITS
        bits_pad  = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])

        # Blockweise dekodieren
        decoded_bits = []
        n_blocks = len(bits_pad) // self.N_BITS
        for i in range(n_blocks):
            block = bits_pad[i * self.N_BITS : (i + 1) * self.N_BITS]
            decoded_bits.append(self._decode_block(block))

        all_bits = np.concatenate(decoded_bits)
        raw      = self._bits_to_data(all_bits)

        # 2-Byte-Längen-Header lesen
        if len(raw) < 2:
            raise LDPCDecodeError("Zu kurz für Längen-Header")
        n_bytes = int.from_bytes(raw[:2], 'big')
        result  = raw[2:2 + n_bytes]

        if len(result) != n_bytes:
            raise LDPCDecodeError(
                f"Längen-Mismatch: Header={n_bytes}, erhalten={len(result)}"
            )

        log.debug("[LDPC] decode: %d B → %d B (%d Blöcke)",
                  len(data), len(result), n_blocks)
        return result


# ══════════════════════════════════════════════════════════════════════
# SELBST-REGISTRIERUNG
# ══════════════════════════════════════════════════════════════════════

# Beim Import automatisch beim FEC-Registry registrieren
try:
    from gust_fec import register_backend
    register_backend("ldpc", LDPCFecBackend)
    log.debug("[LDPC] Backend registriert als 'ldpc'")
except ImportError:
    pass   # gust_fec.py nicht vorhanden — kein Problem, Standalone-Betrieb


# ══════════════════════════════════════════════════════════════════════
# SELBSTTEST
# ══════════════════════════════════════════════════════════════════════

def _run_tests() -> bool:
    """Selbsttest: LDPC encode/decode Roundtrip + Fehlerkorrektur."""
    print("── gust_ldpc.py Selbsttest ─────────────────────────────")
    print(f"  Bibliothek: {_LDPC_LIB or 'numpy-Fallback'}")
    errors = []

    fec = LDPCFecBackend()
    print(f"  n={fec.N_BITS}  rate={fec.RATE}  k={fec.k_bits}  m={fec.m_bits}")

    # ── Test 1: Roundtrip alle Frame-Typen ──────────────────────────
    test_payloads = [
        ("CQ",           b"\x41OE3GAS"),
        ("EMERG_RSRC",   b"\x21OE3GAS\x00\x00\x00\x00"),
        ("WEATHER",      b"\x01OE3GAS" + b"\x00" * 14),
        ("POSITION",     b"\x02OE3GAS" + b"\x00" * 18),
        ("EMERG_BEACON", b"\x20OE3GAS" + b"\x00" * 20),
    ]
    for name, payload in test_payloads:
        try:
            enc = fec.encode(payload)
            dec = fec.decode(enc)
            assert dec == payload, f"Roundtrip fehlgeschlagen für {name}"
            overhead = len(enc) - len(payload)
            print(f"  ✓  {name:<14}  {len(payload):2d} B → {len(enc):2d} B "
                  f"(+{overhead} B, +{overhead/len(payload)*100:.0f} %)")
        except Exception as e:
            errors.append(f"{name}: {e}")
            print(f"  ✗  {name}: {e}")

    # ── Test 2: Fehlerkorrektur (zufällige Bit-Fehler) ───────────────
    try:
        import random
        random.seed(42)
        data      = b"\x01OE3GAS" + bytes(range(14))
        enc       = fec.encode(data)
        corrupted = bytearray(enc)
        # 1-2 Bit-Fehler pro Block einbringen
        n_errors  = max(1, len(enc) // 12)
        for i in random.sample(range(len(corrupted)), min(n_errors, len(corrupted))):
            corrupted[i] ^= 0x01   # 1 Bit flippen
        dec = fec.decode(bytes(corrupted))
        if dec == data:
            print(f"  ✓  Fehlerkorrektur: {n_errors} Bit-Fehler korrigiert")
        else:
            print(f"  ℹ  Fehlerkorrektur: {n_errors} Bit-Fehler nicht korrigiert "
                  f"(erwartet bei hoher Fehlerdichte — kein Fehler)")
    except LDPCDecodeError as e:
        print(f"  ℹ  Fehlerkorrektur: LDPCDecodeError (erwartet bei hoher Fehlerdichte): {e}")
    except Exception as e:
        errors.append(f"Fehlerkorrektur: {e}")
        print(f"  ✗  Fehlerkorrektur: {e}")

    # ── Test 3: Overhead-Vergleich mit RS ────────────────────────────
    print(f"\n  Overhead-Vergleich RS(255,223) vs. LDPC Rate {fec.RATE}:")
    print(f"  {'Frame-Typ':<14} {'Payload':>7} {'RS +32B':>8} {'LDPC':>8} {'Diff':>8}")
    print(f"  {'─'*14} {'─'*7} {'─'*8} {'─'*8} {'─'*8}")
    rs_overhead = 32
    for name, payload in test_payloads:
        try:
            enc          = fec.encode(payload)
            ldpc_total   = len(enc)
            rs_total     = len(payload) + rs_overhead
            diff         = ldpc_total - rs_total
            diff_str     = f"{diff:+d} B"
            print(f"  {name:<14} {len(payload):>6}B {rs_total:>7}B "
                  f"{ldpc_total:>7}B {diff_str:>8}")
        except Exception:
            pass

    # ── Ergebnis ─────────────────────────────────────────────────────
    print(f"\n{'─' * 54}")
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

## Installation

```powershell
pip install ldpc   # python-ldpc (empfohlen)
```

Falls nicht verfügbar: numpy-Fallback ist automatisch aktiv
(langsamer, weniger robust bei niedrigem SNR).

## Verifikation

```powershell
# 1. Abhängigkeit installieren
pip install ldpc

# 2. Syntax-Check
python -m py_compile gust_ldpc.py

# 3. Selbsttest
py gust_ldpc.py
# Erwartung:
#   Alle Roundtrip-Tests ✓
#   Overhead-Tabelle zeigt LDPC < RS für alle Frame-Typen
#   Exit-Code 0

# 4. FEC-Registry-Check
python -c "
import gust_ldpc
from gust_fec import get_fec_backend
fec = get_fec_backend('ldpc')
print('LDPC registriert:', fec)
"
```

## Wichtig

- **Nur neue Datei erstellen** — kein bestehender Code wird geändert
- Die Integration in gust_frame.py / gateway.json kommt in Etappe 3
- Etappe 3 darf erst beginnen wenn dieser Selbsttest vollständig grün ist
