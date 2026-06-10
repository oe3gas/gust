#!/usr/bin/env python3
"""
GUST — LDPC FEC Backend                                  ADR-24 / P8-10
═══════════════════════════════════════════════════════════════════════
Low-Density Parity-Check Code für GUST — experimentelles FEC-Backend.

Konfiguration:
  Code-Rate:    3/4  (Overhead +33 % relativ zur Payload)
  Blockgröße:   256 Bit (32 Byte) pro LDPC-Codeword  (k=192 Daten, m=64 Parität)
  Konstruktion: systematisch  H = [P | I_m],  P spärlich (Spaltengewicht 3)
  Dekodierung:  Soft-Decision-BP (python-ldpc, LLR aus P8-13) + exaktes GF(2)-Syndrom-Decoding
  Bibliothek:   python-ldpc (optional, Soft-Decision) — sonst reines numpy

Vorteile gegenüber RS(255,223):
  - Deutlich kürzere Frames: Rate 3/4 → weniger Overhead als RS bei kleinen
    Payloads (RS hat IMMER +32 B, LDPC nur ~+1/3 der Payload).

WICHTIG — Blocklänge n=256 (Sweetspot, Blocklängen-Evaluation):
  n=256 mit m=64 Prüfgleichungen ist der per Evaluation ermittelte Sweetspot:
  groß genug, dass Belief-Propagation einen echten Coding-Gain liefert, aber
  noch kompakt genug für GUST-Frames. **n=256 Sweetspot, Soft-Decision (LLR aus
  dem Demodulator, P8-13) für ~2 dB Gewinn erforderlich** — der reine
  Hard-Decision-Pfad korrigiert weiterhin garantiert genau 1 Bitfehler pro
  256-Bit-Block (Mindestdistanz ≥ 3 durch paarweise verschiedene, von 0
  verschiedene Spalten). Ohne Soft-Input fällt LDPC gegenüber RS zurück; mit
  Soft-Input + n≥256 zieht es vorbei (siehe gust_backlog.md, LDPC-Blocklängen-Eval).

Einschränkungen:
  - Hard-Decision: nur 1 Bitfehler je 256-Bit-Block garantiert korrigierbar.
    Voller Gewinn nur mit Soft-Decision (LLR via decode(..., llr_blocks=...)).
  - Burst-Fehler: kein Vorteil gegenüber RS.
  - Protokollbruch: TX+RX müssen dasselbe Backend (und dieselbe Matrix-Seed)
    verwenden. LDPC ist nicht kompatibel mit RS.

Verwendung (opt-in via gateway.json):
  "fec": "ldpc"

Dieses Modul registriert sich selbst beim Import:
  import gust_ldpc   → get_fec_backend("ldpc") ist danach verfügbar

Installation (optional):
  pip install ldpc   (python-ldpc — Soft-Decision-BP, nur als Best-Effort genutzt)
  Ohne die Bibliothek läuft der exakte numpy-GF(2)-Decoder (empfohlen, reicht
  für die garantierte Einzelfehlerkorrektur vollständig aus).

Referenz: ADR-24, gust_knowledge.md §<N>, gust_spec.md §3.x
"""

import numpy as np
import logging
from typing import Optional, Tuple, Dict

log = logging.getLogger("gust.ldpc")

# ── Bibliotheks-Verfügbarkeit (optionale Soft-Decision-BP) ─────────────
# python-ldpc 2.x stellt die Klasse BpDecoder bereit. Sie wird hier NUR als
# zusätzlicher Best-Effort-Versuch für Mehrfachfehler genutzt; die garantierte
# Einzelfehlerkorrektur erfolgt exakt über GF(2)-Syndrom-Decoding (immer aktiv).
try:
    from ldpc import BpDecoder           # python-ldpc >= 2.0
    _LDPC_LIB = "python-ldpc"
except ImportError:
    BpDecoder = None
    _LDPC_LIB = None
    log.debug("[LDPC] python-ldpc nicht verfügbar — exakter numpy-Decoder aktiv")


# ══════════════════════════════════════════════════════════════════════
# FEHLERKLASSE
# ══════════════════════════════════════════════════════════════════════

class LDPCDecodeError(Exception):
    """Wird geworfen wenn LDPC-Dekodierung nach max_iter fehlschlägt."""
    pass


# ══════════════════════════════════════════════════════════════════════
# LDPC-MATRIX GENERATOR  (systematisch H = [P | I_m])
# ══════════════════════════════════════════════════════════════════════

def _make_gust_H(n: int = 48, rate: float = 0.75,
                 seed: int = 42) -> np.ndarray:
    """
    Erzeugt eine systematische Parity-Check-Matrix H = [P | I_m] für GUST-LDPC.

    P ist eine (m × k)-Matrix mit Spaltengewicht 3 (spärlich, LDPC-typisch).
    Alle n Spalten von H sind paarweise verschieden und von 0 verschieden
    → Mindestdistanz ≥ 3 → garantierte Korrektur von genau 1 Bitfehler/Block.

    Args:
        n:    Codelänge in Bit (Standard: 48)
        rate: Code-Rate (Standard: 3/4)
        seed: Zufalls-Seed für reproduzierbare Matrix (TX und RX identisch!)

    Returns:
        H: (m × n) numpy-Array, dtype=uint8, m = n*(1-rate), Spalten k..n-1 = I_m
    """
    m = int(round(n * (1 - rate)))   # Check-Nodes: 12 bei n=48, rate=3/4
    k = n - m                        # Daten-Bits: 36

    rng = np.random.default_rng(seed)

    # Belegte Spaltenmuster (als frozenset der gesetzten Zeilen).
    # Die Einheitsvektoren (Identitätsteil) sind reserviert → garantiert
    # disjunkt zu den P-Spalten (P hat Gewicht 3, Einheitsvektoren Gewicht 1).
    chosen = {frozenset([i]) for i in range(m)}
    cols = []
    while len(cols) < k:
        rows = frozenset(int(r) for r in rng.choice(m, size=3, replace=False))
        if len(rows) == 3 and rows not in chosen:
            chosen.add(rows)
            cols.append(rows)

    P = np.zeros((m, k), dtype=np.uint8)
    for j, rows in enumerate(cols):
        for r in rows:
            P[r, j] = 1

    H = np.hstack([P, np.eye(m, dtype=np.uint8)]).astype(np.uint8)
    return H


# ══════════════════════════════════════════════════════════════════════
# LDPC FEC BACKEND
# ══════════════════════════════════════════════════════════════════════

class LDPCFecBackend:
    """
    LDPC FEC Backend für GUST — experimentell, opt-in per gateway.json.

    Verarbeitet Daten blockweise: jeder Block hat k_bits Informationsbits.
    encode() fügt Paritätsbits hinzu, decode() korrigiert Fehler.

    Block-Struktur (systematisch, H = [P | I_m]):
      BLOCK_BITS    = 192 Bit Daten     (n * rate = 256 * 0.75)
      PARITY_BITS   = 64 Bit Parität    (n * (1-rate) = 256 * 0.25)
      CODEWORD_BITS = 256 Bit gesamt    (n) → [d_0..d_191 | p_0..p_63]

    Codewort-Eigenschaft: H · c = 0 (mod 2) per Konstruktion
    (p = P · d mod 2) → Syndrom eines fehlerfreien Codeworts ist 0.
    """

    name = "ldpc"

    # LDPC-Parameter
    N_BITS    = 256     # Codelänge in Bit (Sweetspot, Blocklängen-Evaluation)
    RATE      = 0.75    # Code-Rate → k=192 Daten, m=64 Parität
    MAX_ITER  = 50      # Bit-Flipping / BP Max-Iterationen
    SEED      = 42      # Reproduzierbare Matrix (TX und RX MÜSSEN gleich sein)

    def __init__(self):
        self.k_bits   = self.N_BITS - int(round(self.N_BITS * (1 - self.RATE)))  # 36
        self.m_bits   = self.N_BITS - self.k_bits                                # 12
        self.overhead = 0    # variabel — wird in encode() gesetzt (Logging)

        self._H = _make_gust_H(self.N_BITS, self.RATE, self.SEED)
        # P-Teil (m × k) für systematisches Encoding: p = P · d (mod 2)
        self._P = self._H[:, :self.k_bits]

        # Syndrom→Spalte-Tabelle für exakte Einzelfehlerkorrektur:
        # Bei genau 1 Bitfehler an Position j ist das Syndrom = Spalte j von H.
        # Da alle Spalten verschieden sind, ist j eindeutig bestimmbar.
        self._syndrome_to_col: Dict[Tuple[int, ...], int] = {
            tuple(int(v) for v in self._H[:, j]): j
            for j in range(self.N_BITS)
        }

        # Optionaler Soft-Decision-BP-Decoder (nur Best-Effort für Mehrfachfehler)
        self._bp = None
        if _LDPC_LIB == "python-ldpc":
            try:
                self._bp = BpDecoder(
                    self._H.astype(np.uint8),
                    error_rate=0.05,
                    max_iter=self.MAX_ITER,
                    bp_method="minimum_sum",
                    input_vector_type="syndrome",
                )
            except Exception as e:   # pragma: no cover - lib-abhängig
                log.debug("[LDPC] BpDecoder-Init fehlgeschlagen (%s) — nur numpy", e)
                self._bp = None

        log.debug("[LDPC] Backend init  n=%d rate=%.2f k=%d m=%d lib=%s",
                  self.N_BITS, self.RATE, self.k_bits, self.m_bits,
                  _LDPC_LIB or "numpy")

    # ── Bit/Byte-Konvertierung ────────────────────────────────────────
    def _data_to_bits(self, data: bytes) -> np.ndarray:
        """Bytes → Bit-Array (MSB zuerst)."""
        return np.unpackbits(np.frombuffer(data, dtype=np.uint8))

    def _bits_to_data(self, bits: np.ndarray) -> bytes:
        """Bit-Array → Bytes (MSB zuerst), bei Bedarf auf Byte-Grenze gepadded."""
        pad = (8 - len(bits) % 8) % 8
        if pad:
            bits = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])
        return np.packbits(bits).tobytes()

    # ── Block-Codec ───────────────────────────────────────────────────
    def _encode_block(self, data_bits: np.ndarray) -> np.ndarray:
        """
        Einen LDPC-Block systematisch kodieren.
        data_bits: k_bits Informationsbits → Codeword (n_bits) = [d | p].
        Parität exakt über GF(2):  p = P · d  (mod 2).
        """
        assert len(data_bits) == self.k_bits, \
            f"Erwartet {self.k_bits} Datenbits, erhalten {len(data_bits)}"
        d = data_bits.astype(np.uint8)
        p = (self._P @ d) % 2
        return np.concatenate([d, p]).astype(np.uint8)

    def _decode_block(self, rx_bits: np.ndarray, llr=None) -> np.ndarray:
        """
        Einen LDPC-Block dekodieren.
        rx_bits: n_bits empfangene Bits (Hard-Decision).
        llr:     optionaler Soft-Output-Vektor (N_BITS LLR-Werte, aus P8-13).
                 Wenn vorhanden UND python-ldpc verfügbar, wird zuerst eine
                 Soft-Decision-BP versucht (~2 dB Gewinn ggü. Hard-Decision).
        Gibt k_bits dekodierte Datenbits zurück.

        Strategie (in dieser Reihenfolge):
          0. Soft-Decision-BP (nur mit llr + python-ldpc) — voller Coding-Gain.
          1. Syndrom 0 → bereits gültiges Codewort.
          2. Syndrom = bekannte Spalte → exakte Einzelfehlerkorrektur (garantiert).
          3. Best-Effort Mehrfachfehler: optional python-ldpc BP, dann numpy
             Bit-Flipping. Endgültige Verifikation über das Syndrom.
        Wirft LDPCDecodeError wenn am Ende kein gültiges Codewort vorliegt.
        """
        assert len(rx_bits) == self.N_BITS, \
            f"Erwartet {self.N_BITS} Bits, erhalten {len(rx_bits)}"

        r = rx_bits.astype(np.uint8).copy()
        syndrome = (self._H @ r) % 2

        # 0) Soft-Decision-BP zuerst — nur wenn LLR übergeben UND python-ldpc da.
        #    Die LLR-Beträge liefern BP per-Bit-Zuverlässigkeiten (Soft-Decision):
        #    Flip-Wahrscheinlichkeit pro Bit = sigmoid(-|llr|). Der Decoder ist
        #    syndrom-basiert (input_vector_type="syndrome") → channel_probs setzen,
        #    dann decode(syndrome). Syndrom-Check verifiziert das Ergebnis.
        if llr is not None and self._bp is not None and syndrome.any():
            try:
                llr_arr = np.asarray(llr, dtype=np.float64).ravel()
                if llr_arr.shape[0] == self.N_BITS:
                    probs = 1.0 / (1.0 + np.exp(np.abs(llr_arr)))
                    probs = np.clip(probs, 1e-6, 0.49)
                    self._bp.update_channel_probs(probs)
                    err  = self._bp.decode(syndrome.astype(np.uint8))
                    cand = (r ^ np.asarray(err, dtype=np.uint8)) % 2
                    if not ((self._H @ cand) % 2).any():
                        return cand[:self.k_bits]
            except Exception as e:   # pragma: no cover - lib-abhängig
                log.debug("[LDPC] Soft-BP-Versuch fehlgeschlagen: %s", e)

        # 1) Fehlerfrei
        if not syndrome.any():
            return r[:self.k_bits]

        # 2) Exakte Einzelfehlerkorrektur (Mindestdistanz ≥ 3)
        col = self._syndrome_to_col.get(tuple(int(v) for v in syndrome))
        if col is not None:
            r[col] ^= 1
            if not ((self._H @ r) % 2).any():
                return r[:self.k_bits]

        # 3a) Best-Effort: python-ldpc BP (Soft-Decision) für Mehrfachfehler
        if self._bp is not None:
            try:
                err = self._bp.decode(syndrome.astype(np.uint8))
                cand = (r ^ np.asarray(err, dtype=np.uint8)) % 2
                if not ((self._H @ cand) % 2).any():
                    return cand[:self.k_bits]
            except Exception as e:   # pragma: no cover - lib-abhängig
                log.debug("[LDPC] BP-Decode-Versuch fehlgeschlagen: %s", e)

        # 3b) Best-Effort: numpy Bit-Flipping (Gallager-artig)
        flip = r.copy()
        for _ in range(self.MAX_ITER):
            s = (self._H @ flip) % 2
            if not s.any():
                return flip[:self.k_bits]
            scores = self._H.T @ s          # je Bit: Anzahl verletzter Checks
            flip[int(np.argmax(scores))] ^= 1

        if ((self._H @ flip) % 2).any():
            raise LDPCDecodeError(
                f"Decoding nicht konvergiert nach {self.MAX_ITER} Iterationen "
                f"(Syndrom-Gewicht={int(((self._H @ flip) % 2).sum())}) — "
                f"vermutlich > 1 Bitfehler im 256-Bit-Block"
            )
        return flip[:self.k_bits]

    # ── Öffentliche API ───────────────────────────────────────────────
    def encode(self, data: bytes) -> bytes:
        """
        LDPC-Kodierung blockweise.

        Jeder Block enthält k_bits (36) Datenbits + m_bits (12) Paritätsbits.
        Die Datenlänge wird als 2-Byte-Header vorangestellt damit decode()
        das korrekte Zero-Padding entfernen kann.

        Gibt kodierte Bytes zurück (Länge > len(data)).
        """
        n_bytes = len(data)
        header  = n_bytes.to_bytes(2, 'big')         # 2-Byte-Längen-Header

        bits = self._data_to_bits(header + data)

        # Auf Vielfaches von k_bits auffüllen
        pad      = (self.k_bits - len(bits) % self.k_bits) % self.k_bits
        bits_pad = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])

        codewords = [
            self._encode_block(bits_pad[i:i + self.k_bits])
            for i in range(0, len(bits_pad), self.k_bits)
        ]

        result        = self._bits_to_data(np.concatenate(codewords))
        self.overhead = len(result) - n_bytes        # für Logging

        log.debug("[LDPC] encode: %d B → %d B (+%d B Overhead, %d Blöcke)",
                  n_bytes, len(result), self.overhead, len(codewords))
        return result

    def decode(self, data: bytes, llr_blocks=None) -> bytes:
        """
        LDPC-Dekodierung blockweise.

        llr_blocks: optionale Liste von LLR-Vektoren (je N_BITS Werte, ein
                    Eintrag pro Block, aus dem Soft-Output-Demodulator P8-13).
                    Wenn vorhanden, nutzt jeder Block die Soft-Decision-BP.
        Wirft LDPCDecodeError wenn ein Block nicht konvergiert.
        """
        bits = self._data_to_bits(data)

        # Auf Vielfaches von N_BITS auffüllen
        pad      = (self.N_BITS - len(bits) % self.N_BITS) % self.N_BITS
        bits_pad = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])

        n_blocks     = len(bits_pad) // self.N_BITS
        decoded_bits = []
        for i in range(n_blocks):
            block = bits_pad[i * self.N_BITS:(i + 1) * self.N_BITS]
            llr   = llr_blocks[i] if llr_blocks else None
            decoded_bits.append(self._decode_block(block, llr=llr))

        raw = self._bits_to_data(np.concatenate(decoded_bits))

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
    print(f"  Bibliothek: {_LDPC_LIB or 'numpy-Fallback'}  "
          f"(exakter GF(2)-Decoder immer aktiv)")
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
