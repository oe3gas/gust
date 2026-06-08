# GUST — LDPC Blocklängen-Evaluation (Etappe 2b) — Ergebnis

**Datum:** 8. Juni 2026
**Autor:** OE3GAS (mit Claude Code)
**Skript:** `ldpc_planung/ldpc_blocklen_eval.py` · **Rohdaten:** `ldpc_blocklen_curves.csv`
**Auslöser:** Designfrage aus Etappe 2 — ist `gust_ldpc.py` mit n=48 / Rate 3/4
zu kurz, und rechtfertigt eine größere Blocklänge LDPC gegenüber RS(255,223)?

---

## 1. Methodik

- **Kanal:** AWGN, BPSK, Eb/N0 auf **Informationsbit** normiert (rate-korrekt).
- **Code:** regulärer (3,12)-LDPC, Rate 3/4, exaktes Spaltengewicht 3, balancierte
  Zeilen, keine Doppelkanten (eigener Konstruktor `make_regular_ldpc`).
- **Decoder:** `python-ldpc` BpDecoder, min-sum, 50 Iterationen.
- **Zwei Decision-Modi:**
  - **SOFT** — volle Kanal-LLR (erfordert einen Soft-Output-Demodulator).
  - **HARD** — BSC aus Hard-Bits. **Das ist der heutige GUST-Pfad** (MFSK-Demod
    liefert Hard-Bytes, kein Soft-Output).
- **Referenz:** RS(255,223) hard-decision, **analytisch** (exakte Binomial-Tail-FER,
  t=16 Symbolfehler). Das ist das heute produktive GUST-FEC.
- **FER** je Punkt bis 100 Frame-Fehler bzw. 30 000 Frames (All-Zero-Codewort-Trick).

> ⚠️ **Wichtige Einordnung:** AWGN/BPSK ist **nicht** der reale GUST-Kanal (MFSK-8,
> nicht-kohärent, KW-Fading). Die **absoluten** dB-Werte sind nicht 1:1 auf GUST
> übertragbar. Die Studie ist ein **relativer** Vergleich — sie zeigt belastbar, *ob*
> und *ab welcher Blocklänge* sowie *unter welcher Decision-Art* LDPC Gewinn liefert.

---

## 2. Ergebnisse (voller Lauf, FER bei Eb/N0)

### SOFT-Decision (volle LLR)

| n (Bit) | 2 dB | 3 dB | 4 dB | 5 dB | 6 dB | **Eb/N0 @ FER 1e-2** | Δ vs n=48 |
|--------:|-----:|-----:|-----:|-----:|-----:|:--------------------:|:---------:|
| 48      | 0,435 | 0,187 | 0,071 | 0,017 | 0,005 | **5,40 dB** | — |
| 128     | 0,599 | 0,193 | 0,030 | 0,003 | 0,001 | **4,51 dB** | +0,89 dB |
| 256     | 0,694 | 0,174 | 0,009 | <e-2  | <e-2  | **3,96 dB** | +1,45 dB |
| 512     | 0,794 | 0,149 | 0,002 | <e-2  | <e-2  | **3,63 dB** | +1,77 dB |
| 1024    | 0,917 | 0,087 | 0,000 | <e-2  | <e-2  | **3,35 dB** | +2,06 dB |

### HARD-Decision (heutiger GUST-Pfad)

| n (Bit) | 4 dB | 5 dB | 6 dB | 7 dB | Eb/N0 @ FER 1e-2 |
|--------:|-----:|-----:|-----:|-----:|:----------------:|
| 48      | 0,613 | 0,420 | 0,230 | 0,104 | **> 7 dB (n/a)** |
| 256     | 0,935 | 0,775 | 0,314 | 0,063 | **> 7 dB (n/a)** |
| 1024    | 1,000 | 0,962 | 0,316 | 0,011 | **> 7 dB (n/a)** |

### Referenz RS(255,223), hard-decision (analytisch)

| 4 dB | 5 dB | 6 dB | 7 dB | **Eb/N0 @ FER 1e-2** |
|-----:|-----:|-----:|-----:|:--------------------:|
| 1,000 | 0,671 | 0,0049 | 2,5e-8 | **5,91 dB** |

### Direktvergleich (Eb/N0 für FER = 1e-2, gleiche Achse)

| Verfahren | Eb/N0 @ 1e-2 | Bewertung |
|:----------|:------------:|:----------|
| LDPC n=1024 **soft** | 3,35 dB | bester Gewinn, aber großer Block |
| LDPC n=512 **soft**  | 3,63 dB | |
| LDPC n=256 **soft**  | 3,96 dB | guter Kompromiss |
| LDPC n=48 **soft**   | 5,40 dB | aktuelle GUST-Größe |
| **RS(255,223) hard** | **5,91 dB** | **heutiges GUST-FEC** |
| LDPC **hard**, jedes n | **> 7 dB** | schlechter als RS |

---

## 3. GUST-Passung: Blocklänge vs. reale Frame-Größen

GUST-Payloads sind klein (7–27 Byte). Ein Rate-3/4-Codewort der Länge n fasst
k = 0,75·n Bit = 0,094·n Byte Nutzdaten:

| n (Bit) | Kapazität | WEATHER (21 B) füllt | Konsequenz |
|--------:|:---------:|:--------------------:|:-----------|
| 48      | 4 B   | braucht ~5 Blöcke | heutiger Zustand (Multi-Block je Frame) |
| 128     | 12 B  | braucht ~2 Blöcke | |
| **256** | **24 B** | **88 % — 1 Block** | **passt fast exakt auf 1 GUST-Frame** |
| 512     | 48 B  | 44 % | Padding-Verlust oder 2 Frames aggregieren |
| 1024    | 96 B  | 22 % | starkes Padding oder 4–5 Frames aggregieren |

→ **n=256 ist der „natürliche" GUST-Sweetspot:** ein Codewort ≈ ein WEATHER-Frame,
und es holt bereits **+1,45 dB von max. +2,06 dB** (n=1024) ab — klare abnehmende
Grenzerträge oberhalb von n=256.

---

## 4. Interpretation

1. **n=48 ist tatsächlich zu kurz.** Soft-decision verliert n=48 rund **2 dB**
   gegenüber n=1024 und liegt schlechter als jeder größere Block. Der in frühen
   Entwürfen behauptete „+3–5 dB"-Gewinn ist bei n=48 nicht vorhanden.

2. **Die eigentliche Einschränkung ist HARD vs. SOFT, nicht die Blocklänge allein.**
   Im heutigen GUST-Pfad (Hard-Bytes nach MFSK-Demod) erreicht LDPC bei Rate 3/4
   **für keine Blocklänge** FER 1e-2 unter 7 dB — und ist damit **schlechter als
   das produktive RS(255,223) (5,91 dB)**. Ein Wechsel auf LDPC mit dem aktuellen
   Demodulator wäre eine **Regression**.

3. **LDPC gewinnt nur unter zwei Bedingungen gleichzeitig:**
   - **(a) Soft-Output-Demodulator** (MFSK liefert LLR statt Hard-Bits), und
   - **(b) Blocklänge n ≥ 256**.
   Dann erreicht n=256 soft **3,96 dB** — ca. **2 dB besser als RS** — und passt
   zugleich auf einen GUST-Frame.

4. **Kosten des Soft-Gewinns:** (a) Soft-Output-Demod = nicht-trivialer DSP-Umbau
   (MFSK-8-Bin-Energien → bitweise LLR), (b) größerer Block ⇒ Frame-Aggregation
   oder Padding (Latenz/Airtime), (c) Protokollbruch (TX+RX müssen Code+Seed teilen).

---

## 5. Empfehlung für Etappe 3

> **LDPC NICHT mit n=48 und NICHT auf dem heutigen Hard-Decision-Pfad einführen.**
> In dieser Konstellation ist RS(255,223) überlegen.

Konkrete Optionen:

- **Option A (empfohlen, konservativ):** RS bleibt v1.0-Standard. LDPC-Arbeit
  pausieren, bis ein **Soft-Output-MFSK-Demodulator** existiert (eigene Phase).
  Etappe 3 (Integration) dann **zurückstellen**.

- **Option B (wenn LDPC strategisch gewollt):** Etappe 3 neu fassen als
  **„LDPC n=256 + Soft-Output-Demod"**. Reihenfolge:
  1. Soft-Output-Demod (MFSK-Bin-Energien → LLR) als Vorarbeit.
  2. `gust_ldpc.py` auf n=256, Rate 3/4 umstellen (Matrix + Block-Framing).
  3. Frame-Aggregation/Padding-Strategie definieren (1 Frame ≈ 1 Codewort).
  4. Erst dann TX/RX-Integration + On-Air-Vergleich gegen RS.

- **Nicht weiterverfolgen:** LDPC n=48 als Produktiv-Code (weder hard noch soft
  konkurrenzfähig).

`gust_ldpc.py` (n=48) bleibt als **experimentelles, korrektes Backend** bestehen
(garantierte 1-Bit/Block-Korrektur, Selbsttest grün) — als Referenz/Spielwiese,
nicht als v1.0-Kandidat.

---

## 6. Reproduktion

```powershell
# Voller Lauf (soft + hard + RS-Referenz + GUST-Passung), ~5–7 min:
py ldpc_planung/ldpc_blocklen_eval.py --blocklens 48,128,256,512,1024 `
   --ebn0 1,2,3,4,5,6,7 --target-errs 100 --csv ldpc_planung/ldpc_blocklen_curves.csv

# Schnelllauf (grobe Kurven, ~1 min):
py ldpc_planung/ldpc_blocklen_eval.py --quick --blocklens 48,256,1024
```

> Monte-Carlo-Werte schwanken ±0,1–0,2 dB je Seed/Frame-Zahl; die Rangfolge und
> die ~2-dB-Aussagen sind stabil. RS-Referenz ist analytisch (exakt).
