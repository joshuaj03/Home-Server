# ESP32 DHT11 Sensor Node - Production Firmware & Integration Guide

High-reliability ESP32 firmware for DHT11 temperature and humidity sensing with non-blocking Wi-Fi/MQTT reconnection, Last Will and Testament (LWT), and native Home Assistant MQTT Discovery.

---

## 1. Hardware Pinout & Circuit Wiring

| Component Pin | ESP32 GPIO | Notes |
| :--- | :--- | :--- |
| **DHT11 VCC (Pin 1 / `+`)** | `3V3` or `5V` (VIN) | Standard 3.3V or 5V power rail |
| **DHT11 Data (Pin 2 / `out`)** | `GPIO 4` | Bi-directional single-bus data line (with 4.7kΩ–10kΩ pull-up resistor to VCC if bare sensor) |
| **DHT11 NC (Pin 3)** | Not connected | (Only present on 4-pin bare modules) |
| **DHT11 GND (Pin 4 / `-`)** | `GND` | Common ground rail |
| **Status LED** | `GPIO 2` | Onboard blue status LED (`LED_BUILTIN`) |

### Status LED Behavior
- **Solid ON**: Wi-Fi and MQTT connected; telemetry active and sensor healthy.
- **Blinking / OFF**: Wi-Fi or MQTT disconnected, or reconnection backoff active.

---

## 2. Directory Structure

```text
stacks/iot/firmware/
├── DHT11_Sensor/
│   ├── DHT11_Sensor.ino    # Production non-blocking firmware
│   ├── config.h             # Local secrets, credentials, intervals, and topics
│   └── config.example.h     # Git-safe configuration template
├── DHT11_Sensor_v1_backup.ino # Original legacy firmware backup
└── README.md                # This comprehensive documentation
```

---

## 3. Configuration Parameters (`config.h`)

All user-configurable parameters and credentials reside cleanly in `config.h`:

```cpp
// 1. Hardware Pin Configurations
#define DHTPIN 4               // GPIO pin for DHT11 data line
#define DHTTYPE DHT11          // Adafruit DHT sensor type
#define LED_PIN 2              // Status indicator LED

// 2. Wi-Fi Credentials
const char* const WIFI_SSID = "Raider";
const char* const WIFI_PASSWORD = "raiderx530";

// 3. MQTT Broker Settings
const char* const MQTT_SERVER = "192.168.1.150";
const uint16_t    MQTT_PORT = 1883;
const char* const MQTT_USER = "homepi";
const char* const MQTT_PASS = "mqtt@homepi";
const char* const MQTT_CLIENT_ID = "ESP32_DHT11_LivingRoom";

// 4. Timing & Intervals (milliseconds)
const unsigned long SENSOR_PUBLISH_INTERVAL_MS = 2000UL;      // Primary telemetry interval
const unsigned long DIAGNOSTICS_PUBLISH_INTERVAL_MS = 30000UL; // Periodic diagnostics
const unsigned long WIFI_RECONNECT_MIN_DELAY_MS = 2000UL;     // Wi-Fi backoff start
const unsigned long WIFI_RECONNECT_MAX_DELAY_MS = 60000UL;    // Wi-Fi backoff ceiling (1 min)
const unsigned long MQTT_RECONNECT_MIN_DELAY_MS = 2000UL;     // MQTT backoff start
const unsigned long MQTT_RECONNECT_MAX_DELAY_MS = 30000UL;    // MQTT backoff ceiling (30 sec)
const uint8_t SENSOR_FAILURE_THRESHOLD = 3;                  // Errors before offline status
```

---

## 4. Final MQTT Topic Structure

| Topic | Direction | Payload Example | QoS | Retained | Purpose / Target Consumer |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `home/dht11/temperature` | Publish | `26` | 0 | **No** | Temperature reading (Flask app, InfluxDB, Glance, Home Assistant) |
| `home/dht11/humidity` | Publish | `44` | 0 | **No** | Humidity reading (Flask app, InfluxDB, Glance, Home Assistant) |
| `home/dht11/status` | Publish (LWT) | `online` / `offline` | 1 | **Yes** | Device connectivity state & MQTT Last Will and Testament |
| `home/dht11/rssi` | Publish | `-62` | 0 | **No** | Wi-Fi signal strength in dBm |
| `home/dht11/uptime` | Publish | `1450` | 0 | **No** | ESP32 uptime in seconds |
| `home/dht11/ip` | Publish | `192.168.1.185` | 0 | **No** | Local assigned IP address |
| `home/dht11/sensor_status` | Publish | `online` / `offline` | 0 | **No** | DHT11 hardware operational health |
| `home/dht11/error_count` | Publish | `0` | 0 | **No** | Cumulative sensor read errors since boot |

> [!IMPORTANT]
> `home/dht11/temperature` and `home/dht11/humidity` MUST remain un-retained integer strings. Retaining these topics would cause InfluxDB to receive duplicate historical entries upon broker reconnect.

---

## 5. Home Assistant MQTT Discovery Entities

All entities automatically group under a single device in Home Assistant (`ESP32 DHT11 Sensor Node`):

| Entity ID | Name | Device Class | State Class | Unit | State Topic |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `sensor.room_temperature` | Room Temperature | `temperature` | `measurement` | `°C` | `home/dht11/temperature` |
| `sensor.room_humidity` | Room Humidity | `humidity` | `measurement` | `%` | `home/dht11/humidity` |
| `binary_sensor.esp32_dht11_online` | ESP32 DHT11 Online | `connectivity` | - | - | `home/dht11/status` |
| `sensor.esp32_dht11_wifi_signal` | ESP32 DHT11 WiFi Signal | `signal_strength` | `measurement` | `dBm` | `home/dht11/rssi` |
| `sensor.esp32_dht11_uptime` | ESP32 DHT11 Uptime | `duration` | `measurement` | `s` | `home/dht11/uptime` |
| `sensor.esp32_dht11_error_count` | ESP32 DHT11 Error Count | - | `total_increasing` | `errors` | `home/dht11/error_count` |
| `binary_sensor.dht11_sensor_health` | DHT11 Sensor Health | - | - | - | `home/dht11/sensor_status` |

---

## 6. How to Build & Flash

### Option A: Using Arduino IDE
1. Install **ESP32 Board Package**:
   - Go to **Tools > Board > Boards Manager...**, search for `esp32` by Espressif Systems and install version `3.x` (or `2.0.x`).
2. Install Required Libraries via **Tools > Manage Libraries...**:
   - `PubSubClient` (by Nick O'Leary)
   - `DHT sensor library` (by Adafruit)
   - `Adafruit Unified Sensor` (by Adafruit)
   - `ArduinoJson` (v7 by Benoit Blanchon)
3. Open `stacks/iot/firmware/DHT11_Sensor/DHT11_Sensor.ino`.
4. Connect ESP32 via USB and select port under **Tools > Port**.
5. Select board **ESP32 Dev Module**.
6. Click **Upload**.

### Option B: Using `arduino-cli` (Directly on Linux / Pi)
```bash
# Compile
arduino-cli compile --fqbn esp32:esp32:esp32 /home/jake/HomeServer/stacks/iot/firmware/DHT11_Sensor

# Upload (replace /dev/ttyUSB0 with your ESP32 serial port)
arduino-cli upload -p /dev/ttyUSB0 --fqbn esp32:esp32:esp32 /home/jake/HomeServer/stacks/iot/firmware/DHT11_Sensor
```

---

## 7. Verification & Runbook Checklist

1. **Normal Startup:**
   - ESP32 connects to Wi-Fi, prints local IP and RSSI.
   - Connects to Mosquitto MQTT broker with LWT registered.
   - Publishes `home/dht11/status = online` (retained, QoS 1).
   - Publishes Home Assistant discovery JSON payloads to `homeassistant/.../config`.
   - Begins publishing `temperature` and `humidity` every 2 seconds.
2. **Mosquitto Restart:**
   - Stop broker: `docker restart mosquitto`.
   - ESP32 detects connection failure and begins non-blocking exponential backoff (2s, 4s, 8s...).
   - When Mosquitto restarts, ESP32 reconnects automatically and republishes `online` status.
3. **Wi-Fi Drop / Restore:**
   - Temporarily disable AP or move out of range.
   - ESP32 handles disconnection gracefully without resetting or crashing.
   - Restores Wi-Fi connection and re-establishes MQTT connection automatically.
4. **Flask Dashboard & Glance:**
   - Verify `http://192.168.1.150:5000/sensor` returns JSON telemetry.
   - Verify Glance widget at `http://192.168.1.150:8080` displays live climate readings.

