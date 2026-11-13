"""IoT Dashboard Flask Application.

This service acts as an intermediary bridge and web presentation layer
for room climate telemetry produced by the ESP32 DHT11 sensor node.

Key responsibilities:
    1. Subscribes to Mosquitto MQTT topics (`home/dht11/temperature` and
       `home/dht11/humidity`).
    2. Stores the latest telemetry in thread-safe in-memory state.
    3. Asynchronously forwards sensor data points to InfluxDB 2.x time-series database.
    4. Exposes an HTTP JSON API at `/sensor` consumed by Glance and external clients.
    5. Serves an auto-refreshing dark-themed HTML climate card at `/`.

Environment Variables:
    FLASK_HOST (str): Web host binding (default: '0.0.0.0').
    FLASK_PORT (int): Web port binding (default: 5000).
    MQTT_BROKER (str): MQTT broker hostname or IP (default: 'mosquitto').
    MQTT_PORT (int): MQTT broker TCP port (default: 1883).
    MQTT_USER (str): MQTT username (default: 'homepi').
    MQTT_PASSWORD (str): MQTT password (default: 'mqtt@homepi').
    MQTT_TOPIC_TEMP (str): Temperature topic (default: 'home/dht11/temperature').
    MQTT_TOPIC_HUM (str): Humidity topic (default: 'home/dht11/humidity').
    INFLUX_ENABLED (bool): Flag to enable/disable InfluxDB writes (default: True).
    INFLUX_URL (str): InfluxDB HTTP endpoint (default: 'http://influxdb:8086').
    INFLUX_TOKEN (str): InfluxDB auth token (default: 'homepi_token124').
    INFLUX_ORG (str): InfluxDB organisation name (default: 'homeiot').
    INFLUX_BUCKET (str): InfluxDB bucket name (default: 'SENSORS').
"""

import logging
import os
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
import paho.mqtt.client as mqtt

# ------------------------------------------------------------------------------
# Configuration & Environment Setup
# ------------------------------------------------------------------------------
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("iot_dashboard")

FLASK_HOST: str = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT: int = int(os.getenv("FLASK_PORT", "5000"))

MQTT_BROKER: str = os.getenv("MQTT_BROKER", "mosquitto")
MQTT_PORT: int = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER: str = os.getenv("MQTT_USER", "homepi")
MQTT_PASSWORD: str = os.getenv("MQTT_PASSWORD", "mqtt@homepi")
MQTT_TOPIC_TEMP: str = os.getenv("MQTT_TOPIC_TEMP", "home/dht11/temperature")
MQTT_TOPIC_HUM: str = os.getenv("MQTT_TOPIC_HUM", "home/dht11/humidity")

INFLUX_ENABLED: bool = os.getenv("INFLUX_ENABLED", "true").lower() in ("true", "1", "yes")
INFLUX_URL: str = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN: str = os.getenv("INFLUX_TOKEN", "homepi_token124")
INFLUX_ORG: str = os.getenv("INFLUX_ORG", "homeiot")
INFLUX_BUCKET: str = os.getenv("INFLUX_BUCKET", "SENSORS")

# ------------------------------------------------------------------------------
# Thread-Safe Telemetry Store
# ------------------------------------------------------------------------------
class SensorState:
    """Thread-safe storage for the latest sensor readings."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {
            "temperature": None,
            "humidity": None,
            "timestamp": None,
        }

    def update(self, field: str, value: int) -> Dict[str, Any]:
        """Update a specific sensor field and set current ISO timestamp.

        Args:
            field: The metric name ('temperature' or 'humidity').
            value: Integer measurement value.

        Returns:
            A snapshot copy of the updated sensor state dictionary.
        """
        with self._lock:
            self._data[field] = value
            self._data["timestamp"] = datetime.now(timezone.utc).isoformat()
            return dict(self._data)

    def get_snapshot(self) -> Dict[str, Any]:
        """Retrieve a thread-safe copy of the latest sensor data.

        Returns:
            Dictionary containing 'temperature', 'humidity', and 'timestamp'.
        """
        with self._lock:
            return dict(self._data)


state = SensorState()

# ------------------------------------------------------------------------------
# InfluxDB Client Service
# ------------------------------------------------------------------------------
class InfluxService:
    """Manages writing telemetry time-series points to InfluxDB 2.x."""

    def __init__(
        self,
        url: str,
        token: str,
        org: str,
        bucket: str,
        enabled: bool = True,
    ) -> None:
        self.url = url
        self.token = token
        self.org = org
        self.bucket = bucket
        self.enabled = enabled
        self._client: Optional[InfluxDBClient] = None
        self._write_api = None

        if self.enabled:
            self._init_client()

    def _init_client(self) -> None:
        """Initialize the InfluxDB client and write API."""
        try:
            self._client = InfluxDBClient(
                url=self.url,
                token=self.token,
                org=self.org,
                timeout=5000,
            )
            # Use background write API with batching
            self._write_api = self._client.write_api()
            logger.info("InfluxDB client initialized for org='%s', bucket='%s' at %s", self.org, self.bucket, self.url)
        except Exception as exc:
            logger.warning("Failed to initialize InfluxDB client (%s). Writes will be skipped.", exc)
            self._client = None
            self._write_api = None

    def record_reading(self, field_name: str, value: int) -> None:
        """Write a single DHT11 metric point to InfluxDB.

        Args:
            field_name: The metric field name ('temperature' or 'humidity').
            value: Integer reading value.
        """
        if not self.enabled or self._write_api is None:
            return

        try:
            point = (
                Point("dht11")
                .tag("sensor", "dht11")
                .field(field_name, value)
                .time(datetime.now(timezone.utc), WritePrecision.NS)
            )
            self._write_api.write(bucket=self.bucket, record=point)
        except Exception as exc:
            # Non-blocking catch to ensure InfluxDB connectivity blips do not
            # interrupt MQTT consumption or Flask web delivery.
            logger.debug("InfluxDB write suppressed: %s", exc)


influx_service = InfluxService(
    url=INFLUX_URL,
    token=INFLUX_TOKEN,
    org=INFLUX_ORG,
    bucket=INFLUX_BUCKET,
    enabled=INFLUX_ENABLED,
)

# ------------------------------------------------------------------------------
# MQTT Subscriber Client
# ------------------------------------------------------------------------------
def create_mqtt_client() -> mqtt.Client:
    """Instantiate and configure the Paho MQTT client."""
    try:
        # Paho MQTT v2 API compatibility
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="flask_dashboard_subscriber")
    except AttributeError:
        # Fallback for Paho MQTT v1.x
        client = mqtt.Client(client_id="flask_dashboard_subscriber")

    if MQTT_USER and MQTT_PASSWORD:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

    def on_connect(client_instance: mqtt.Client, userdata: Any, flags: Any, rc: int) -> None:
        """Callback executed upon MQTT connection establishment."""
        if rc == 0:
            logger.info("Connected to MQTT Broker at %s:%d (rc=0)", MQTT_BROKER, MQTT_PORT)
            client_instance.subscribe([(MQTT_TOPIC_TEMP, 0), (MQTT_TOPIC_HUM, 0)])
            logger.info("Subscribed to topics: %s, %s", MQTT_TOPIC_TEMP, MQTT_TOPIC_HUM)
        else:
            logger.error("Failed to connect to MQTT Broker, return code=%d", rc)

    def on_disconnect(client_instance: mqtt.Client, userdata: Any, rc: int) -> None:
        """Callback executed upon MQTT disconnection."""
        if rc != 0:
            logger.warning("Unexpected MQTT disconnection (rc=%d). Auto-reconnecting...", rc)
        else:
            logger.info("Cleanly disconnected from MQTT broker.")

    def on_message(client_instance: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        """Callback executed when an incoming MQTT message arrives."""
        try:
            payload_str = msg.payload.decode("utf-8").strip()
            topic = msg.topic

            if topic == MQTT_TOPIC_TEMP:
                val = int(payload_str)
                updated = state.update("temperature", val)
                influx_service.record_reading("temperature", val)
                logger.info("Updated: %s", updated)
                logger.info("[MQTT → Influx] temperature: %d at %s", val, updated["timestamp"])

            elif topic == MQTT_TOPIC_HUM:
                val = int(payload_str)
                updated = state.update("humidity", val)
                influx_service.record_reading("humidity", val)
                logger.info("Updated: %s", updated)
                logger.info("[MQTT → Influx] humidity: %d at %s", val, updated["timestamp"])

        except ValueError as val_err:
            logger.warning("Received non-integer MQTT payload on %s: '%s' (%s)", msg.topic, msg.payload, val_err)
        except Exception as exc:
            logger.error("Error processing MQTT message on %s: %s", msg.topic, exc)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    return client


def start_mqtt_background_thread() -> None:
    """Start the MQTT client in a background daemon thread."""
    def run_worker() -> None:
        client = create_mqtt_client()
        while True:
            try:
                logger.info("Initiating MQTT connection to %s:%d...", MQTT_BROKER, MQTT_PORT)
                client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
                client.loop_forever()
            except Exception as exc:
                logger.warning("MQTT worker connection failed: %s. Retrying in 5 seconds...", exc)
                import time
                time.sleep(5)

    thread = threading.Thread(target=run_worker, name="MQTTSubscriberThread", daemon=True)
    thread.start()


# ------------------------------------------------------------------------------
# Flask Application & HTTP Endpoints
# ------------------------------------------------------------------------------
app = Flask(__name__)


@app.route("/sensor", methods=["GET"])
def get_sensor() -> Response:
    """HTTP JSON endpoint exposing the latest temperature, humidity, and timestamp.

    Glance and external HTTP monitoring tools consume this endpoint.

    Returns:
        JSON object with 'temperature', 'humidity', and 'timestamp'.
    """
    return jsonify(state.get_snapshot())


@app.route("/", methods=["GET"])
def dashboard() -> str:
    """HTTP endpoint rendering the dark-themed climate widget card.

    Returns:
        Rendered HTML page with current integer temperature and humidity.
    """
    snapshot = state.get_snapshot()
    temp_val = int(snapshot["temperature"] if snapshot["temperature"] is not None else 0)
    hum_val = int(snapshot["humidity"] if snapshot["humidity"] is not None else 0)
    return render_template("index.html", temp=temp_val, hum=hum_val)


@app.route("/health", methods=["GET"])
def health() -> Response:
    """Service health and telemetry status endpoint."""
    snapshot = state.get_snapshot()
    has_data = snapshot["temperature"] is not None and snapshot["humidity"] is not None
    return jsonify({
        "status": "healthy" if has_data else "waiting_for_telemetry",
        "latest_telemetry": snapshot,
        "mqtt_broker": f"{MQTT_BROKER}:{MQTT_PORT}",
        "influx_enabled": INFLUX_ENABLED,
    })


# Start MQTT background thread when module loads
start_mqtt_background_thread()

if __name__ == "__main__":
    logger.info("Starting Flask application on %s:%d", FLASK_HOST, FLASK_PORT)
    app.run(host=FLASK_HOST, port=FLASK_PORT)
