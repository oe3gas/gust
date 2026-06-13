# GUST-Authentifizierung: HMAC und ECDSA — Zwei Verfahren für zwei Szenarien

**OE3GAS · Juni 2026**

---

## Warum überhaupt Authentifizierung auf Kurzwelle?

GUST überträgt Nutzdaten — Wettermesswerte, GPS-Positionen, Notfallmeldungen.
Das Protokoll ist offen, jeder kann mit einem billigen SDR-Empfänger mithören.
Das ist Amateurfunk-Prinzip und gewünscht.

Was aber *nicht* gewünscht ist: dass jemand einen Frame mit einem beliebigen
Rufzeichen und beliebigen Daten sendet — etwa eine gefälschte Notfallmeldung
mit falschen Koordinaten. Authentifizierung löst genau dieses Problem.

Wichtig: **Authentifizierung ≠ Verschlüsselung.** Der Inhalt eines GUST-Frames
bleibt für jeden lesbar — das ist gesetzliche Pflicht im Amateurfunk.
Authentifizierung beweist nur: *dieser Frame stammt wirklich von dieser Station.*

---

## Das Grundprinzip: mathematische Unterschrift

Eine Authentifizierung funktioniert wie eine Unterschrift unter ein Dokument.
Der Sender berechnet aus dem Frame-Inhalt und einem geheimen Schlüssel einen
kurzen Prüfwert — die *Signatur*. Der Empfänger kann mit seinem Wissen
prüfen: stimmt die Signatur zum Frame-Inhalt?

Wird der Frame-Inhalt auch nur um ein Bit verändert, stimmt die Signatur
nicht mehr. Wird ein Frame mit einem unbekannten Schlüssel signiert, erkennt
der Empfänger die Fälschung.

---

## GUST-S: HMAC-SHA256 — die symmetrische Lösung

### Wie es funktioniert

HMAC (*Hash-based Message Authentication Code*) verwendet einen **gemeinsamen
geheimen Schlüssel** — denselben Schlüssel auf beiden Seiten.

```
OE3GAS und OE1XTU vereinbaren vorab:
  Schlüssel = "a3f2...b891"  (32 zufällige Bytes)

OE3GAS sendet:
  1. Daten-Frame (z.B. WEATHER)
  2. AUTH-Frame: HMAC-SHA256(Frame-Inhalt + Timestamp, Schlüssel)
     → 14 Byte Prüfwert

OE1XTU empfängt:
  1. Daten-Frame → im 60-Sekunden-Puffer ablegen
  2. AUTH-Frame → HMAC mit eigenem Schlüssel berechnen
     → stimmt überein → Frame ist echt ✓
```

Der gemeinsame Schlüssel wird *außerhalb von GUST* ausgetauscht — per
E-Mail, persönlich, oder über eine sichere Verbindung. Er steht in
`gateway.json` und verlässt das eigene System nie.

### Wie der HMAC konkret berechnet wird

**Was hineinfließt (Eingabe):**

Der HMAC wird über genau zwei Dinge berechnet:

1. **Frame-Body** — das ist der vollständige Inhalt des Daten-Frames so
   wie er über die Luft gesendet wird, ohne SYNC-Präambel und ohne
   Reed-Solomon-Parität:
   ```
   TYPE     1 Byte   — Frame-Typ, z.B. 0x01 (WEATHER)
   CHANNEL  1 Byte   — Kanal + Flags
   FROM     4 Byte   — Rufzeichen in Base-40-Kodierung
   PAYLOAD  variabel — Nutzdaten (z.B. Wetterdaten, GPS-Koordinaten)
   CRC      2 Byte   — Prüfsumme über alle obigen Felder
   ```

2. **Timestamp** — Unix-Zeitstempel als 4-Byte-Ganzzahl (big-endian),
   derselbe Wert der auch im AUTH-Frame steht.

```python
# Implementierung in gust_frame.py:
import hmac, hashlib, struct

msg = frame_body + struct.pack(">I", timestamp)
# ">I" = unsigned int, 4 Byte, big-endian

tag = hmac.new(key, msg, hashlib.sha256).digest()
# SHA256 liefert immer 32 Byte
```

**Was herauskommt (Ergebnis):**

SHA256 erzeugt intern einen 256-Bit-Hashwert = **32 Byte**.
Davon werden nur die **ersten 14 Byte** verwendet — das reicht für
~112 Bit Sicherheit, genug für den Amateurfunk-Anwendungsfall, und
passt in das 20-Byte-Payload-Budget des AUTH-Frames.

```
SHA256-Ausgabe (32 Byte, hexadezimal):
  9e 6c 84 de 30 a5 fb 29 36 77 93 74 28 02 f1 c3
  a8 47 55 0d 11 b4 7e 3c 05 2e 8d 91 f0 6a 12 bb
  ↑                                              ↑
  erste 14 Byte werden verwendet      Rest wird verworfen

HMAC-Tag im AUTH-Frame (14 Byte):
  9e 6c 84 de 30 a5 fb 29 36 77 93 74 28 02
```

**Das vollständige AUTH-Frame-Payload (20 Byte):**

```
Offset  Länge  Inhalt
──────────────────────────────────────────────────
  0       4    TIMESTAMP  Unix-Zeitstempel (uint32, big-endian)
                          z.B.: 6a 4c 83 e1  = 1 783 157 729 s
  4       1    REF_TYPE   Typ des Daten-Frames
                          z.B.: 0x01 = WEATHER
  5       1    KEY_ID     Schlüsselnummer
                          z.B.: 0x01 = erster konfigurierter Schlüssel
  6      14    HMAC-TAG   Prüfwert (SHA256 truncated auf 14 Byte)
                          z.B.: 9e 6c 84 de 30 a5 fb 29 36 77 93 74 28 02
──────────────────────────────────────────────────
Gesamt: 20 Byte
```

**Wie der Empfänger prüft:**

```
1. TIMESTAMP aus AUTH-Frame lesen
   → abs(jetzt - TIMESTAMP) ≤ 60 s? Nein → abweisen (Replay-Schutz)

2. KEY_ID → Schlüssel in gateway.json nachschlagen
   → nicht gefunden → abweisen

3. Frame-Body des Daten-Frames aus dem 60-s-Puffer holen
   (Schlüssel: Rufzeichen + REF_TYPE)
   → nicht gefunden → abweisen

4. HMAC selbst berechnen:
   erwartet = HMAC-SHA256(frame_body + TIMESTAMP, schlüssel)[:14]

5. Vergleich (timing-sicher):
   hmac.compare_digest(tag_aus_frame, erwartet)
   → gleich → ✓ authentifiziert
   → ungleich → ✗ Fälschung
```

Der Vergleich in Schritt 5 verwendet `hmac.compare_digest()` statt
eines normalen `==` — das verhindert Timing-Angriffe, bei denen ein
Angreifer durch Zeitmessung herausfinden könnte, wie viele Bytes
seines gefälschten Tags zufällig stimmen.

### Stärken

- **Einfach:** Keine Infrastruktur, keine Zertifikate, keine Datenbank.
  Zwei Stationen tauschen einen Schlüssel aus — fertig.
- **Kompakt:** 14 Byte Signatur passen in die 20-Byte-Payload von GUST-S.
- **Keine externen Abhängigkeiten:** `hmac` und `hashlib` sind in Python
  standardmäßig enthalten. Läuft auf dem Raspberry Pi ohne zusätzliche Pakete.
- **Replay-Schutz:** Der TIMESTAMP im AUTH-Frame wird geprüft.
  Ein Frame der älter als 60 Sekunden ist, wird abgewiesen — auch wenn
  der HMAC stimmt.

### Schwäche: nur bilateral verifizierbar

Wer einen HMAC prüfen kann, könnte theoretisch auch einen erzeugen —
denn er kennt den Schlüssel. Das ist kein praktisches Problem solange
die Schlüsselpartner einander vertrauen. Für eine *geschlossene Gruppe*
(Notfunk-Netz, Ortsverband, Expedition) ist das vollkommen ausreichend.

Es bedeutet aber: **nur die Schlüsselpartner können die Echtheit eines
Frames beweisen.** Wer den Schlüssel nicht kennt, kann gar nichts prüfen.

### Wann HMAC die richtige Wahl ist

- Zwei Stationen oder eine Gruppe von Stationen kennen einander
- Kein Fremder soll jemals die Herkunft eines Frames verifizieren müssen
- Beispiele: OE3GAS ↔ OE1XTU (bilateral), ÖVSV-Notfunknetz (geschlossene Gruppe)

---

## GUST-X: ECDSA P-256 — die asymmetrische Lösung

### Das Kernproblem mit HMAC bei öffentlicher Verifikation

Stell dir vor, das Rote Kreuz (OE1XRK) sendet einen Notruf-Beacon über GUST.
Ein Gateway in Salzburg — der noch nie Kontakt mit OE1XRK hatte — empfängt
den Frame. Er möchte sicherstellen: *Kommt dieser Notruf wirklich vom Roten Kreuz?*

Mit HMAC ist das unmöglich: der Salzburger Gateway hat keinen gemeinsamen
Schlüssel mit OE1XRK. Er müsste vorher mit OE1XRK Kontakt aufgenommen und
einen Schlüssel ausgetauscht haben. Das ist bei einer Notfallsituation
unrealistisch.

### Die Lösung: zwei verschiedene Schlüssel

ECDSA (*Elliptic Curve Digital Signature Algorithm*) verwendet ein
**Schlüsselpaar**: einen *privaten* und einen *öffentlichen* Schlüssel.

```
OE1XRK generiert einmalig:
  Privater Schlüssel → bleibt auf OE1XRKs Rechner, nie weitergegeben
  Öffentlicher Schlüssel → wird auf QRZ.com veröffentlicht

OE1XRK sendet einen Notruf:
  1. EMERG_EX-Frame
  2. AUTH_EX (0x85): r-Hälfte der ECDSA-Signatur (32 Byte)
  3. AUTH_EX_B (0x86): s-Hälfte der ECDSA-Signatur (32 Byte)

Gateway in Salzburg empfängt:
  1. Lädt öffentlichen Schlüssel von OE1XRK von QRZ.com
  2. Verifiziert: ECDSA-Prüfung mit öffentlichem Schlüssel
  3. Ergebnis: Frame kommt wirklich von OE1XRK ✓
```

Der öffentliche Schlüssel kann *jeder* kennen — er hilft nur beim
Prüfen, nicht beim Fälschen. Wer fälschen will, bräuchte den privaten
Schlüssel. Den hat nur OE1XRK.

### Wie die ECDSA-Signatur berechnet wird

ECDSA verwendet intern ebenfalls SHA256 als Hashfunktion — aber auf
eine mathematisch völlig andere Art als HMAC.

**Schritt 1 — Nachricht zusammenstellen (wie bei HMAC):**

```
msg = frame_body + struct.pack(">I", timestamp)
```

Dieselbe Eingabe wie beim HMAC: Frame-Body + Timestamp als 4-Byte-Integer.

**Schritt 2 — SHA256-Hash der Nachricht:**

```
hash = SHA256(msg)   → 32 Byte
```

Dieser Hash wird nicht direkt als Signatur verwendet, sondern ist
der Eingabewert für die elliptische-Kurven-Mathematik.

**Schritt 3 — Elliptische Kurve P-256:**

Auf der Kurve P-256 (auch secp256r1 genannt) wird mit dem
*privaten Schlüssel* und dem Hash eine mathematische Operation
durchgeführt. Das Ergebnis sind zwei Ganzzahlen: `r` und `s`.

```python
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes

sig_der = private_key.sign(msg, ec.ECDSA(hashes.SHA256()))
# sig_der ist DER-kodiert (ASN.1-Format)

# r und s extrahieren:
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
r, s = decode_dss_signature(sig_der)
```

**Was herauskommt (Ergebnis):**

Beide Werte `r` und `s` sind Ganzzahlen im Bereich 1 bis n-1,
wobei n die Gruppenordnung der Kurve P-256 ist
(n ≈ 1,16 × 10⁷⁷ — eine 256-Bit-Zahl).

```
r als 32-Byte-Wert (big-endian):
  3c 7a 9f 12 e4 05 8b 3d f1 62 a8 44 77 19 b3 2e
  0c 88 51 f4 da 39 12 7b 55 06 3e c9 91 a2 ff 84

s als 32-Byte-Wert (big-endian):
  a1 55 0d 7c 3f 9e 2b 44 68 f3 c1 92 8a 04 e7 51
  29 d7 88 3c b0 44 71 60 0e 3a 55 f9 12 ab 88 c7
```

Zusammen: **64 Byte Signatur** — das passt nicht in einen einzigen
GUST-X-Frame (44 Byte Payload). Daher zwei Frames:

```
Frame 0x85 AUTH_EX:   TIMESTAMP(4) | REF_TYPE(1) | KEY_ID(1) | r(32) | reserviert(6)
Frame 0x86 AUTH_EX_B: TIMESTAMP(4) | REF_TYPE(1) | KEY_ID(1) | s(32) | reserviert(6)
```

**Warum r und s nicht gekürzt werden können:**

Bei HMAC ist Kürzen unproblematisch: 32 Byte SHA256 auf 14 Byte
reduziert, Sicherheit sinkt etwas aber bleibt ausreichend.

Bei ECDSA ist das prinzipiell unmöglich: Die Verifikation berechnet
aus `s`, dem Hash und dem öffentlichen Schlüssel einen Punkt auf der
Kurve und prüft ob dessen x-Koordinate gleich `r` ist. Fehlt auch
nur ein Bit von `r` oder `s`, schlägt diese Berechnung fehl — ohne
Ausnahme, ohne Workaround.

**Wie der Empfänger prüft:**

```
1. Beide AUTH_EX-Frames sammeln (60-s-Fenster)
2. r und s zu 64-Byte-Signatur zusammensetzen
3. Öffentlichen Schlüssel von OE1XRK laden (QRZ.com)
4. msg = frame_body + struct.pack(">I", timestamp)
5. public_key.verify(sig_der, msg, ec.ECDSA(hashes.SHA256()))
   → kein Fehler → ✓ authentifiziert
   → InvalidSignature → ✗ Fälschung
```

### Warum zwei Frames?

Eine vollständige ECDSA-Signatur (Kurve P-256) besteht aus zwei Werten:
`r` und `s` — zusammen 64 Byte. Das passt nicht in einen einzigen
GUST-X-Frame (44 Byte Payload).

Eine *gekürzte* Signatur ist keine Option: die ECDSA-Verifikation braucht
`r` und `s` vollständig. Fehlt auch nur ein Bit, ist die Verifikation
prinzipiell unmöglich — kein Trick kann das beheben.

Lösung: zwei aufeinanderfolgende Frames. Der Empfänger wartet im
60-Sekunden-Fenster auf beide, setzt die Signatur zusammen und
führt dann eine normale, standardkonforme Verifikation durch.

```
Frame 0x85 AUTH_EX:   TIMESTAMP | REF_TYPE | KEY_ID | r (32 Byte)
Frame 0x86 AUTH_EX_B: TIMESTAMP | REF_TYPE | KEY_ID | s (32 Byte)
                                ↓
                        r + s = 64 Byte Signatur
                                ↓
                   ECDSA P-256 Verifikation mit öffentlichem Schlüssel
```

### Stärken

- **Öffentlich verifizierbar:** Jeder der den öffentlichen Schlüssel kennt
  (z.B. von QRZ.com), kann den Frame verifizieren — ohne vorherigen Kontakt.
- **Keine Infrastruktur für Verifizierer:** Der öffentliche Schlüssel ist
  frei zugänglich. Nur der Sender braucht den privaten Schlüssel.
- **Skalierbar:** Wenn OE1XRK bekannt wird, können Tausende von Gateways
  ihre Frames verifizieren — ohne dass OE1XRK mit jedem Schlüssel tauscht.

### Schwächen

- **Größer:** 64 Byte Signatur über zwei Frames statt 14 Byte in einem.
  Das kostet ~10 Sekunden Sendezeit (2 × ~5 s GUST-X-Frames).
- **Schlüsselinfrastruktur nötig:** Der öffentliche Schlüssel muss irgendwo
  hinterlegt sein (QRZ.com, GUST-Key-Register). Das ist einmalig, aber
  ein Extra-Schritt.
- **Privater Schlüssel muss sicher verwahrt werden:** Geht er verloren oder
  wird er kompromittiert, müssen alle Empfänger benachrichtigt werden.

### Wann ECDSA die richtige Wahl ist

- Die Station soll für *beliebige* Empfänger verifizierbar sein
- Kein vorheriger Schlüsselaustausch möglich oder gewünscht
- Beispiele: Rotes Kreuz OE1XRK (Notrufe), ÖVSV-Leuchtturm-Expedition,
  bekannte Wetterstation die von vielen abgehört wird

---

## Vergleich auf einen Blick

| Eigenschaft | HMAC (GUST-S, 0x50) | ECDSA (GUST-X, 0x85+0x86) |
|---|---|---|
| Schlüsseltyp | Ein gemeinsamer Schlüssel | Schlüsselpaar: privat + öffentlich |
| Schlüsselaustausch | Bilateral, außerhalb GUST | Öffentlicher Schlüssel auf QRZ.com |
| Wer kann prüfen? | Nur Schlüsselpartner | Jeder mit öffentlichem Schlüssel |
| Hash-Algorithmus | SHA256 (via HMAC) | SHA256 (innerhalb ECDSA) |
| Signatur-Eingabe | frame_body + timestamp | frame_body + timestamp |
| Signatur-Ausgabe | 32 Byte → truncated auf 14 B | r(32 B) + s(32 B) = 64 B |
| Signatur-Größe | 14 Byte | 64 Byte (2 Frames) |
| Kürzbar? | Ja (Sicherheit sinkt etwas) | Nein (Verifikation unmöglich) |
| Sendedauer AUTH | ~5 s (1 Frame) | ~10 s (2 Frames) |
| Infrastruktur | Keine | Key-Register (QRZ.com) |
| Sicherheitsniveau | ~112 Bit | ~128 Bit |
| Implementierung | Python stdlib | `cryptography`-Bibliothek |
| Status in GUST | Implementiert (v0.5) | Spezifiziert (GUST-X, P8-12) |

---

## Das Analogiebild

**HMAC** ist wie ein Codewort unter Freunden:
OE3GAS und OE1XTU haben vereinbart: "Wenn du die Losung sagst, weiß
ich dass du es bist." Jeder der die Losung kennt, könnte sie auch
weitersagen — aber das würde dem Weitersager selbst schaden.

**ECDSA** ist wie ein amtlicher Stempel:
Das Rote Kreuz hat einen Stempel den nur sie besitzen (privater Schlüssel)
und ein öffentliches Prüfsystem (öffentlicher Schlüssel). Jeder kann
prüfen ob der Stempel echt ist — aber nur das Rote Kreuz kann stempeln.

---

## Replay-Schutz: Timestamps in beiden Verfahren

Beide Verfahren enthalten einen **Unix-Timestamp** im AUTH-Frame.
Der Empfänger prüft: ist dieser Timestamp höchstens 60 Sekunden alt?
Ist er älter, wird der Frame abgewiesen — auch wenn die Signatur stimmt.

Das verhindert *Replay-Angriffe*: Ein Angreifer könnte einen echten AUTH-Frame
aufzeichnen und später nochmals senden, um einen gefälschten Daten-Frame
als echt erscheinen zu lassen. Mit dem Timestamp-Check schlägt das fehl.

Voraussetzung: TX und RX müssen zeitlich synchronisiert sein.
GPS oder NTP mit einer Toleranz von ±30 Sekunden reicht vollständig aus.

---

## Lizenzrechtliche Anmerkung

Sowohl HMAC als auch ECDSA sind Authentifizierungsverfahren, keine
Verschlüsselung. Der Frame-Inhalt bleibt vollständig lesbar — für jeden.
Die Signatur beweist nur die Herkunft, verbirgt aber nichts.

Das ist im Einklang mit den gesetzlichen Bestimmungen für den Amateurfunk-
dienst, der Inhaltsverschlüsselung auf Amateurfunk-Frequenzen untersagt.
Authentifizierung ist ausdrücklich erlaubt und für Notfunk-Anwendungen
sinnvoll.

---

*Referenz: `gust_spec.md §3.5` (AUTH 0x50) und `§3.9` (AUTH_EX 0x85+0x86),
`gust_knowledge.md §28` (AUTH Design-Entscheidungen)*