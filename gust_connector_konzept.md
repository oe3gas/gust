# GUST — Connector Layer: Erweiterungskonzept
**Semantic Bridging zwischen externen Nachrichtensystemen und GUST-Frames**
*OE3GAS — Mai 2026 · Entwurf Phase 6/8*

---

## 1. Problemstellung

Phase 6 (Backlog P6-01–P6-05) beschreibt eine einfache MQTT-Brücke: RX-Frames
auf Topics publizieren, TX-Anfragen via MQTT empfangen. Das ist **technisch**
korrekt, aber **semantisch blind** — ein JSON von einer Wetterstation landet
auf `gust/tx/raw` und der Server muss raten, was daraus zu machen ist.

Die neue Anforderung ist anspruchsvoller:

> *Eine Nachricht von einer Wetterstation soll als **WEATHER-Frame** gesendet
> werden. Eine Position als **POSITION-Frame**. Ein Notruf als
> **EMERG_BEACON-Frame**. Und das Ganze soll auch für künftige Protokolle
> (HTTP-Webhook, Meshtastic, APRS-Gateway) funktionieren — ohne den
> Frame-Layer anzufassen.*

Das ist das **Semantic Impedance Problem**: externe Nachrichtenformate sprechen
eine andere Sprache als GUST-Frames. Die Lösung ist eine dedizierte
Schicht zwischen beiden.

---

## 2. Leitprinzipien des Entwurfs

| Prinzip | Konsequenz |
|---|---|
| Frame-Layer bleibt unberührt | `gust_frame.py` — kein einziger geänderter Byte |
| Event-Bus bleibt Integrationshub | Connector publiziert/konsumiert Events, nicht Frames direkt |
| Semantik ist explizit konfiguriert | Mapping-Tabelle in YAML, nicht hart kodiert |
| Bidirektional von Anfang an | Eingehend *und* ausgehend im selben Entwurf |
| Erweiterbar auf andere Protokolle | MQTT ist nur ein Connector unter vielen |

---

## 3. Architekturübersicht

```
╔═══════════════════════════════════════════════════════════════╗
║  EXTERNE WELT                                                 ║
║  MQTT-Broker │ HTTP-Webhook │ Meshtastic │ APRS │ ...         ║
╚══════╤════════════════╤══════════════════╤════════════════════╝
       │                │                  │
╔══════▼════════════════▼══════════════════▼════════════════════╗
║  CONNECTOR LAYER  (gust_connector.py)                        ║
║  MQTTConnector  │  WebhookConnector  │  MeshtasticConnector  ║
║                                                               ║
║  Jeder Connector:                                             ║
║   • empfängt rohe externe Nachrichten                        ║
║   • delegiert ans SemanticMapping                            ║
║   • publiziert auf / konsumiert vom Event-Bus                ║
╚══════╤════════════════════════════════════════════════════════╝
       │  rohe Nachricht (topic, payload_bytes/dict)
╔══════▼════════════════════════════════════════════════════════╗
║  SEMANTIC MAPPING  (gust_transforms.py + connectors.yaml)    ║
║                                                               ║
║  topic/Quelle → FrameType     (Routing)                      ║
║  externes Format → frame-Dict (Transform IN)                 ║
║  frame-Dict → externes Format (Transform OUT)                ║
╚══════╤════════════════════════════════════════════════════════╝
       │  (frame_type, payload_dict, from_call)
╔══════▼════════════════════════════════════════════════════════╗
║  EVENT BUS  (gust_eventbus.py — unverändert)                 ║
║                                                               ║
║  EventType.MQTT_RX    ← bereits definiert ✓                  ║
║  EventType.RX_FRAME   → Connector abonniert für Outbound     ║
╚══════╤════════════════════════════════════════════════════════╝
       │
╔══════▼════════════════════════════════════════════════════════╗
║  FRAME LAYER  (gust_frame.py — unverändert)                  ║
║  encode_weather(), build_frame(), parse_frame() ...          ║
╚═══════════════════════════════════════════════════════════════╝
```

---

## 4. Neue Dateien

| Datei | Inhalt | Ersetzt/Ergänzt |
|---|---|---|
| `gust_connector.py` | `GustConnector` ABC + `ConnectorRegistry` | neu |
| `gust_mqtt.py` | `MQTTConnector` Implementierung | P6-01/P6-02 |
| `gust_transforms.py` | Transform-Funktionen (Bibliothek) | neu |
| `connectors.yaml` | Konfiguration: Topics, Mappings, Broker | P6-03 |

`gust_frame.py`, `gust_eventbus.py`, `gust_rx.py`, `gust_modulator.py`,
`gust_audio.py` — **keine Änderungen**.

---

## 5. `gust_connector.py` — Abstract Base

```python
from abc import ABC, abstractmethod
from gust_eventbus import EventBus

class GustConnector(ABC):
    """
    Abstrakte Basis für alle externen Nachrichtenbrücken.

    Unterklassen implementieren start()/stop() und die
    protokollspezifische Kommunikation. Die Semantic-Mapping-
    Logik liegt in gust_transforms.py und ist connector-übergreifend.
    """

    def __init__(self, name: str, mapping: "SemanticMapping"):
        self.name    = name
        self.mapping = mapping
        self._bus: EventBus | None = None

    @abstractmethod
    async def start(self, bus: EventBus) -> None:
        """Verbindung aufbauen, Event-Bus-Subscription registrieren."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Verbindung sauber trennen."""
        ...

    @abstractmethod
    async def _publish_external(self, topic: str, payload: dict) -> None:
        """Protokollspezifisches Senden nach außen."""
        ...

    # ── Inbound: extern → Event-Bus ──────────────────────────────────

    async def _handle_inbound(self, source_key: str, raw: dict) -> None:
        """
        Wird von der Unterklasse aufgerufen wenn eine externe Nachricht
        eintrifft. Mapped raw → frame_type + payload_dict und publiziert
        einen MQTT_RX-Event auf den Bus.
        """
        result = self.mapping.map_inbound(source_key, raw)
        if result is None:
            return   # kein Mapping definiert → ignorieren
        frame_type, payload_dict, from_call = result
        event = make_connector_rx_event(
            frame_type, payload_dict, from_call, source=self.name
        )
        await self._bus.publish(event)

    # ── Outbound: Event-Bus → extern ─────────────────────────────────

    async def _handle_outbound(self, rx_frame_event: dict) -> None:
        """
        Abonniert RX_FRAME-Events vom Bus und leitet sie nach außen.
        topic und payload werden vom SemanticMapping berechnet.
        """
        frame = rx_frame_event["frame"]
        result = self.mapping.map_outbound(frame)
        if result is None:
            return   # kein Outbound-Mapping für diesen Frame-Typ
        topic, payload = result
        await self._publish_external(topic, payload)


class ConnectorRegistry:
    """Hält alle aktiven Connectors, startet/stoppt sie gemeinsam."""

    def __init__(self):
        self._connectors: list[GustConnector] = []

    def register(self, connector: GustConnector) -> None:
        self._connectors.append(connector)

    async def start_all(self, bus: EventBus) -> None:
        for c in self._connectors:
            await c.start(bus)

    async def stop_all(self) -> None:
        for c in self._connectors:
            await c.stop()
```

---

## 6. `gust_transforms.py` — Semantic Mapping

Das ist das Herzstück. Zwei Konzepte:

### 6.1 Mapping-Tabelle (konfiguriert via YAML)

```yaml
# connectors.yaml  (Ausschnitt)

mqtt:
  broker: "localhost"
  port: 1883
  client_id: "gust-gateway"

  inbound:
    # Home Assistant Außenstation
    - topic: "homeassistant/sensor/outdoor/state"
      frame_type: WEATHER
      transform: weather_from_ha_json
      from_call: "OE3GAS"

    # Eigene APRS-zu-JSON-Bridge
    - topic: "aprs/position/+"
      frame_type: POSITION
      transform: position_from_aprs_json
      from_call: "{topic[2]}"     # letztes Topic-Segment = Rufzeichen

    # Native GUST TX-Anfrage (bereits im GUST-Format)
    - topic: "gust/tx/weather"
      frame_type: WEATHER
      transform: passthrough       # JSON-Keys entsprechen schon dem Frame-Dict

    # Catch-all: alles andere → SENSOR-Frame (TLV)
    - topic: "gust/tx/sensor/#"
      frame_type: SENSOR
      transform: sensor_from_json

  outbound:
    WEATHER:      "gust/rx/weather/{from}"
    POSITION:     "gust/rx/position/{from}"
    EMERG_BEACON: "gust/rx/emergency/{from}"
    TEXT:         "gust/rx/text/{from}"
    _default:     "gust/rx/raw/{type_name}/{from}"
```

### 6.2 Transform-Funktionen

```python
# gust_transforms.py
#
# Jede Funktion: (topic: str, raw: dict) → frame_payload_dict
# Rückgabe muss direkt an den entsprechenden encode_*() übergeben werden.

def weather_from_ha_json(topic: str, raw: dict) -> dict:
    """
    Home Assistant sensor JSON → GUST WEATHER payload dict.
    Erwartet HA-Standard-Attribute (temperature, humidity, ...).
    """
    return {
        "temp_c":       float(raw.get("temperature", 0.0)),
        "humidity_pct": int(raw.get("humidity",    0)),
        "pressure_hpa": float(raw.get("pressure",  1013.0)),
        "wind_kmh":     int(raw.get("wind_speed",  0)),
        "wind_deg":     int(raw.get("wind_bearing",0)),
        "rain_mm_h":    float(raw.get("precipitation", 0.0)),
        "uv_index":     int(raw.get("uv_index",    0)),
        "flags":        0x03,   # Sensor OK + Batterie OK
    }

def position_from_aprs_json(topic: str, raw: dict) -> dict:
    """
    APRS-zu-JSON-Bridge-Format → GUST POSITION payload dict.
    """
    return {
        "lat_deg":    float(raw["lat"]),
        "lon_deg":    float(raw["lon"]),
        "alt_m":      int(raw.get("altitude", 0)),
        "speed_kmh":  int(raw.get("speed",    0)),
        "heading_deg":int(raw.get("course",   0)),
        "timestamp":  int(raw.get("time",     0)) & 0xFFFF,
        "flags":      0x01,   # GPS_FIX
    }

def passthrough(topic: str, raw: dict) -> dict:
    """
    Payload-Keys entsprechen bereits dem GUST-Frame-Dict.
    Kein Transform nötig — direkt durchreichen.
    """
    return raw

def sensor_from_json(topic: str, raw: dict) -> dict:
    """
    Generischer Sensor: beliebige Key-Value-Paare → SENSOR TLV.
    Jeder Key wird als Tag kodiert.
    """
    # Implementierung Phase 8 — hier nur Stub
    return {"tlv_raw": raw}
```

### 6.3 `SemanticMapping` Klasse

```python
class SemanticMapping:
    """
    Lädt die Mapping-Tabelle aus connectors.yaml und führt
    die eigentliche Übersetzung durch.
    """

    TRANSFORMS = {
        "weather_from_ha_json":     weather_from_ha_json,
        "position_from_aprs_json":  position_from_aprs_json,
        "passthrough":              passthrough,
        "sensor_from_json":         sensor_from_json,
    }

    def map_inbound(self, topic: str, raw: dict
                   ) -> tuple[int, dict, str] | None:
        """
        Gibt (frame_type_int, payload_dict, from_call) zurück
        oder None wenn kein Mapping passt.
        """
        rule = self._match_rule(topic)
        if rule is None:
            return None
        transform_fn = self.TRANSFORMS[rule["transform"]]
        payload_dict = transform_fn(topic, raw)
        frame_type   = getattr(FrameType, rule["frame_type"])
        from_call    = self._resolve_from(rule, topic)
        return frame_type, payload_dict, from_call

    def map_outbound(self, frame: dict) -> tuple[str, dict] | None:
        """
        Gibt (mqtt_topic, json_payload) zurück oder None.
        """
        type_name = frame_type_name(frame["type"])
        template  = (self._outbound.get(type_name)
                     or self._outbound.get("_default"))
        if template is None:
            return None
        topic   = template.format(
            from_=frame["from"],
            type_name=type_name.lower()
        )
        payload = frame.get("payload_decoded", {})
        payload["_from"]  = frame["from"]
        payload["_type"]  = type_name
        payload["_crc_ok"]= frame.get("crc_ok", False)
        return topic, payload
```

---

## 7. MQTT-Topic-Schema

### Inbound (extern → GUST TX)

```
gust/tx/weather          → WEATHER Frame
gust/tx/position         → POSITION Frame
gust/tx/emergency        → EMERG_BEACON Frame
gust/tx/text             → TEXT Frame
gust/tx/sensor/<tag>     → SENSOR Frame

# Externe Quellen (konfigurierbar):
homeassistant/sensor/+/state   → je nach Geräteklasse gemapped
aprs/position/+                → POSITION Frame
```

### Outbound (GUST RX → extern)

```
gust/rx/weather/<rufzeichen>      {"temp_c": 21.5, "humidity_pct": 68, ...}
gust/rx/position/<rufzeichen>     {"lat_deg": 48.21, "lon_deg": 16.37, ...}
gust/rx/emergency/<rufzeichen>    {"priority": "URGENT", ...}
gust/rx/text/<rufzeichen>         {"text": "OE3GAS DE OE1XTU ..."}
gust/rx/raw/<type>/<rufzeichen>   {"_raw": "..."}  ← alle anderen Frame-Typen
```

### Home Assistant Auto-Discovery (P6-04)

```
homeassistant/sensor/gust_OE3GAS_temperature/config
  → {"name": "GUST OE3GAS Temperatur",
     "state_topic": "gust/rx/weather/OE3GAS",
     "value_template": "{{ value_json.temp_c }}",
     "unit_of_measurement": "°C",
     "device_class": "temperature"}
```

---

## 8. Datenfluss-Beispiele

### 8.1 Wetterstation → GUST HF-Frame (Inbound)

```
[Wetterstation]
  publiziert auf MQTT:
    topic:   "homeassistant/sensor/outdoor/state"
    payload: {"temperature": 18.3, "humidity": 72, "pressure": 1018.5,
              "wind_speed": 12, "wind_bearing": 245}

[MQTTConnector]  →  _handle_inbound("homeassistant/sensor/outdoor/state", raw)

[SemanticMapping.map_inbound()]
  rule   = {frame_type: WEATHER, transform: "weather_from_ha_json",
            from_call: "OE3GAS"}
  result = (0x01, {temp_c: 18.3, humidity_pct: 72, ...}, "OE3GAS")

[EventBus]  →  publiziert MQTT_RX Event

[TX-Queue]  →  build_frame(WEATHER, "OE3GAS", encode_weather(...))

[gust_modulator]  →  MFSK-8 Audio → IC-7610 → HF
```

### 8.2 GUST HF-Frame → MQTT (Outbound)

```
[gust_rx.py / gust_decode.py]
  dekodiert WEATHER Frame von OE1XTU
  publiziert RX_FRAME Event auf Bus

[MQTTConnector]  ←  abonniert RX_FRAME

[SemanticMapping.map_outbound()]
  type_name = "WEATHER"
  topic     = "gust/rx/weather/OE1XTU"
  payload   = {"temp_c": 21.5, "humidity_pct": 68, ...,
               "_from": "OE1XTU", "_crc_ok": true}

[MQTT-Broker]
  publiziert auf "gust/rx/weather/OE1XTU"

[Home Assistant / Node-RED / ...]  ←  empfängt und verarbeitet
```

---

## 9. Erweiterbarkeit auf andere Protokolle

Das Connector-Muster macht weitere Protokolle zum Plug-In:

| Connector | Besonderheit |
|---|---|
| `MQTTConnector` | paho-mqtt, async via `publish_threadsafe` (im EventBus bereits vorbereitet) |
| `WebhookConnector` | aiohttp POST-Handler, kein Broker nötig |
| `MeshtasticConnector` | LoRa-Mesh, bereits in P7-09 geplant; `from_call` aus Node-ID |
| `APRSConnector` | APRS-IS oder TNC, `position_from_aprs_json` bereits geschrieben |
| `LoRaWANConnector` | ChirpStack/TTN MQTT-Integration (spezialisierter Transform) |

Alle teilen denselben `SemanticMapping`-Kern — nur die Transportschicht
(Topic-Syntax, Authentifizierung, Verbindungsmanagement) ist verschieden.

---

## 10. Integration in bestehende Architektur

### Was sich ändert (minimal)

```python
# gust_eventbus.py — eine Zeile ergänzen:
class EventType:
    # ... unverändert ...
    CONNECTOR_RX = "connector_rx"  # neu: von beliebigem Connector eingelangt
    #                               (MQTT_RX bleibt als Alias für Compat.)
```

```python
# gust.py daemon — Connector-Registry einbinden:
async def run_daemon(config):
    bus      = EventBus()
    registry = ConnectorRegistry()
    mapping  = SemanticMapping.from_yaml("connectors.yaml")

    # Connector registrieren
    registry.register(MQTTConnector("mqtt", mapping))
    # registry.register(WebhookConnector("webhook", mapping))  # später

    await registry.start_all(bus)
    # ... restlicher Daemon unverändert ...
    await registry.stop_all()
```

### Was unverändert bleibt

- `gust_frame.py` — kein einziger geänderter Byte ✓
- `gust_eventbus.py` — EventType.MQTT_RX bereits definiert ✓
- `gust_rx.py` — RX-Loop unverändert ✓
- `gust_modulator.py`, `gust_audio.py` — unverändert ✓

---

## 11. Backlog-Auswirkung

### Phase 6 — MQTT-Bridge (Backlog P6-01–P6-05)

Die bestehenden P6-Items werden durch dieses Konzept **ersetzt und erweitert**:

| ID | Alter Titel | Neuer Scope |
|---|---|---|
| P6-01 | MQTTBridge — RX publish | `MQTTConnector` outbound + SemanticMapping OUT |
| P6-02 | MQTT TX-Input | `MQTTConnector` inbound + SemanticMapping IN |
| P6-03 | Topic-Schema dokumentieren | → Topic-Schema in `connectors.yaml` |
| P6-04 | Home Assistant Integration | → Transform `weather_from_ha_json` + Discovery |
| P6-05 | Node-RED Flow | → unverändert (Empfänger von `gust/rx/*`) |

**Neue Items Phase 6:**

| ID | Typ | Titel |
|---|---|---|
| P6-06 | feature | `gust_connector.py` — ABC + Registry |
| P6-07 | feature | `gust_transforms.py` — Transform-Bibliothek |
| P6-08 | feature | `connectors.yaml` — Konfigurations-Schema |
| P6-09 | feature | `SemanticMapping` — YAML-Loader + Matcher |

---

## 12. Was dieses Konzept bewusst *nicht* löst

| Offene Frage | Warum vertagt |
|---|---|
| Transform-Validierung (Pflichtfelder fehlen) | Ausreichend: Exception + Log, kein Frame senden |
| Bidirektionale Meshtastic-Bridge (P7-09) | Eigener Connector, gleiche Schicht |
| SENSOR TLV-Mapping (0x30) | Phase 8, wenn TLV-Schema stabil |
| Authentifizierung externer MQTT-Quellen | Broker-Konfiguration, nicht GUST-Problem |
| Mehrere MQTT-Broker gleichzeitig | Zwei `MQTTConnector`-Instanzen in der Registry |

---

*Dokument: gust_connector_konzept.md*
*Autor: OE3GAS*
*Stand: Mai 2026 — Entwurf Phase 6/8*