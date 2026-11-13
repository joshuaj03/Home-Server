/**
 * ============================================================================
 * Project: ESP32 DHT11 IoT Node - High Reliability & Home Assistant Discovery
 * ============================================================================
 * Features:
 *   - Non-blocking Wi-Fi connection with exponential backoff & auto-recovery.
 *   - Non-blocking MQTT reconnection with Last Will & Testament (LWT).
 *   - Automatic Home Assistant MQTT Discovery for all sensors & diagnostics.
 *   - Exact preservation of existing MQTT topics & integer payloads:
 *       * home/dht11/temperature
 *       * home/dht11/humidity
 *   - Additional diagnostic topics:
 *       * home/dht11/status        ("online" / "offline", retained)
 *       * home/dht11/rssi          (Wi-Fi signal strength in dBm)
 *       * home/dht11/uptime        (seconds since boot)
 *       * home/dht11/ip            (ESP32 local IP address)
 *       * home/dht11/sensor_status ("online" / "offline")
 *       * home/dht11/error_count   (cumulative read errors)
 *   - Sensor sanity checking (NaN rejection, bounds check, error tracking).
 *   - Visual status indication via onboard LED.
 * 
 * Required Libraries:
 *   - PubSubClient (by Nick O'Leary)
 *   - DHT sensor library (by Adafruit)
 *   - Adafruit Unified Sensor
 *   - ArduinoJson (v6 or v7)
 * ============================================================================
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <ArduinoJson.h>
#include "config.h"

// ----------------------------------------------------------------------------
// Global Hardware & Protocol Objects
// ----------------------------------------------------------------------------
DHT dht(DHTPIN, DHTTYPE);
WiFiClient espClient;
PubSubClient client(espClient);

// ----------------------------------------------------------------------------
// Runtime State Variables
// ----------------------------------------------------------------------------
// Timers (all using non-blocking millis() arithmetic)
static unsigned long lastSensorReadTime     = 0;
static unsigned long lastDiagnosticsTime    = 0;
static unsigned long lastWifiAttemptTime    = 0;
static unsigned long lastMqttAttemptTime    = 0;

// Exponential backoff intervals
static unsigned long currentWifiDelay = WIFI_RECONNECT_MIN_DELAY_MS;
static unsigned long currentMqttDelay = MQTT_RECONNECT_MIN_DELAY_MS;

// Connection state flags
static bool wasWifiConnected = false;
static bool wasMqttConnected = false;

// Sensor health and metrics
static float    lastValidTemperature = 0.0f;
static float    lastValidHumidity    = 0.0f;
static uint32_t totalSensorErrors    = 0;
static uint8_t  consecutiveErrors    = 0;
static bool     isSensorOnline       = false;

// ----------------------------------------------------------------------------
// Forward Declarations
// ----------------------------------------------------------------------------
void manageWiFi();
void manageMQTT();
void readAndPublishSensor();
void publishDiagnostics();
void publishHomeAssistantDiscovery();
void publishDiscoveryEntity(
    const char* domain,
    const char* objectId,
    const char* name,
    const char* stateTopic,
    const char* unit,
    const char* devClass,
    const char* stateClass,
    const char* uniqueId,
    const char* payloadOn = nullptr,
    const char* payloadOff = nullptr,
    uint32_t expireAfter = 0
);

// ----------------------------------------------------------------------------
// Setup
// ----------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(500);

  Serial.println();
  Serial.println(F("=================================================="));
  Serial.println(F(" Starting ESP32 DHT11 Reliable Telemetry Node     "));
  Serial.println(F(" Version 2.0.0 (Home Assistant Native Discovery)  "));
  Serial.println(F("=================================================="));

  // Configure onboard LED
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  // Initialize DHT sensor
  Serial.println(F("[Hardware] Initializing DHT11 sensor on GPIO 4..."));
  dht.begin();

  // Configure MQTT client buffer size
  // CRITICAL: Default PubSubClient buffer is 128 bytes, which truncates HA
  // Discovery JSON payloads. 1024 bytes accommodates full device metadata.
  client.setBufferSize(1024);
  client.setServer(MQTT_SERVER, MQTT_PORT);

  // Configure Wi-Fi mode and start initial connection
  WiFi.mode(WIFI_STA);
  Serial.printf("[WiFi] Initiating connection to SSID: %s\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastWifiAttemptTime = millis();
}

// ----------------------------------------------------------------------------
// Main Loop
// ----------------------------------------------------------------------------
void loop() {
  // 1. Maintain Wi-Fi connectivity (non-blocking)
  manageWiFi();

  // 2. Maintain MQTT connectivity (non-blocking)
  if (WiFi.status() == WL_CONNECTED) {
    manageMQTT();
  }

  // 3. Process MQTT internal event loop if connected
  if (client.connected()) {
    client.loop();
  }

  // 4. Periodic sensor reading and telemetry publishing
  readAndPublishSensor();

  // 5. Periodic diagnostic telemetry publishing
  publishDiagnostics();

  // Small yield to allow ESP32 RTOS background housekeeping
  yield();
}

// ----------------------------------------------------------------------------
// Wi-Fi Connection Manager (Non-Blocking with Exponential Backoff)
// ----------------------------------------------------------------------------
void manageWiFi() {
  wl_status_t status = WiFi.status();

  if (status == WL_CONNECTED) {
    if (!wasWifiConnected) {
      wasWifiConnected = true;
      currentWifiDelay = WIFI_RECONNECT_MIN_DELAY_MS;
      Serial.println();
      Serial.println(F("[WiFi] Connection established successfully!"));
      Serial.printf("[WiFi] IP Address : %s\n", WiFi.localIP().toString().c_str());
      Serial.printf("[WiFi] Signal RSSI: %d dBm\n", WiFi.RSSI());
    }
    return;
  }

  // Wi-Fi is disconnected
  if (wasWifiConnected) {
    wasWifiConnected = false;
    wasMqttConnected = false;
    digitalWrite(LED_PIN, LOW);
    Serial.println(F("[WiFi] Connection lost. Initiating automatic reconnect..."));
  }

  // Check if backoff interval has elapsed before re-attempting
  unsigned long now = millis();
  if (now - lastWifiAttemptTime >= currentWifiDelay) {
    lastWifiAttemptTime = now;
    Serial.printf("[WiFi] Attempting reconnection (retry interval: %lu ms)...\n", currentWifiDelay);

    // Re-trigger connection attempt
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    // Exponential backoff up to maximum configured ceiling
    currentWifiDelay = currentWifiDelay * 2;
    if (currentWifiDelay > WIFI_RECONNECT_MAX_DELAY_MS) {
      currentWifiDelay = WIFI_RECONNECT_MAX_DELAY_MS;
    }
  }
}

// ----------------------------------------------------------------------------
// MQTT Connection Manager (Non-Blocking with LWT & Exponential Backoff)
// ----------------------------------------------------------------------------
void manageMQTT() {
  if (client.connected()) {
    return;
  }

  // Connection dropped
  if (wasMqttConnected) {
    wasMqttConnected = false;
    digitalWrite(LED_PIN, LOW);
    Serial.println(F("[MQTT] Disconnected from broker. Will retry..."));
  }

  // Check if backoff interval has elapsed
  unsigned long now = millis();
  if (now - lastMqttAttemptTime < currentMqttDelay) {
    return;
  }
  lastMqttAttemptTime = now;

  Serial.printf("[MQTT] Attempting connection to broker %s:%d...\n", MQTT_SERVER, MQTT_PORT);

  // Configure Last Will and Testament (LWT)
  // Topic: home/dht11/status, Payload: offline, QoS: 1, Retain: true
  bool success = client.connect(
      MQTT_CLIENT_ID,
      MQTT_USER,
      MQTT_PASS,
      TOPIC_STATUS,
      1,
      true,
      "offline"
  );

  if (success) {
    wasMqttConnected = true;
    currentMqttDelay = MQTT_RECONNECT_MIN_DELAY_MS;
    digitalWrite(LED_PIN, HIGH);
    Serial.println(F("[MQTT] Connected to broker successfully!"));

    // 1. Publish birth message: home/dht11/status = online (retained, QoS 1)
    client.publish(TOPIC_STATUS, "online", true);
    Serial.printf("[MQTT] Published %s -> online (retained)\n", TOPIC_STATUS);

    // 2. Publish Home Assistant Discovery configurations
    if (HA_DISCOVERY_ENABLED) {
      publishHomeAssistantDiscovery();
    }

    // 3. Publish immediate diagnostic metrics
    publishDiagnostics();
  } else {
    Serial.printf("[MQTT] Connect failed, state = %d. Next retry in %lu ms\n", client.state(), currentMqttDelay);

    // Exponential backoff for MQTT retries
    currentMqttDelay = currentMqttDelay * 2;
    if (currentMqttDelay > MQTT_RECONNECT_MAX_DELAY_MS) {
      currentMqttDelay = MQTT_RECONNECT_MAX_DELAY_MS;
    }
  }
}

// ----------------------------------------------------------------------------
// Sensor Reading & Telemetry Publishing
// ----------------------------------------------------------------------------
void readAndPublishSensor() {
  unsigned long now = millis();
  if (now - lastSensorReadTime < SENSOR_PUBLISH_INTERVAL_MS) {
    return;
  }
  lastSensorReadTime = now;

  float humidity = dht.readHumidity();
  float tempC    = dht.readTemperature();

  // Validate measurement integrity
  bool isValid = !isnan(humidity) && !isnan(tempC) &&
                 humidity >= 0.0f && humidity <= 100.0f &&
                 tempC >= -20.0f  && tempC <= 80.0f;

  if (!isValid) {
    consecutiveErrors++;
    totalSensorErrors++;
    Serial.printf("[DHT11] Read error (NaN or out-of-range). Consecutive: %u, Total: %u\n",
                  consecutiveErrors, totalSensorErrors);

    // If consecutive errors exceed threshold, mark sensor as offline
    if (consecutiveErrors >= SENSOR_FAILURE_THRESHOLD && isSensorOnline) {
      isSensorOnline = false;
      if (client.connected()) {
        client.publish(TOPIC_SENSOR_STATUS, "offline", false);
      }
      Serial.println(F("[DHT11] Health status degraded -> offline"));
    }
    return;
  }

  // Sensor reading is valid
  consecutiveErrors = 0;
  lastValidTemperature = tempC;
  lastValidHumidity    = humidity;

  if (!isSensorOnline) {
    isSensorOnline = true;
    if (client.connected()) {
      client.publish(TOPIC_SENSOR_STATUS, "online", false);
    }
    Serial.println(F("[DHT11] Sensor recovered -> online"));
  }

  // Only publish over MQTT if broker connection is live
  if (client.connected()) {
    // Integer string formatting exactly matching existing consumer requirements
    char tempBuf[12];
    snprintf(tempBuf, sizeof(tempBuf), "%d", (int)tempC);
    client.publish(TOPIC_TEMPERATURE, tempBuf, false); // NOT retained

    char humBuf[12];
    snprintf(humBuf, sizeof(humBuf), "%d", (int)humidity);
    client.publish(TOPIC_HUMIDITY, humBuf, false); // NOT retained

    Serial.printf("[DHT11] Published -> Temp: %s C | Humidity: %s %%\n", tempBuf, humBuf);
  }
}

// ----------------------------------------------------------------------------
// Periodic System Diagnostics Publishing
// ----------------------------------------------------------------------------
void publishDiagnostics() {
  if (!client.connected()) {
    return;
  }

  unsigned long now = millis();
  if (now - lastDiagnosticsTime < DIAGNOSTICS_PUBLISH_INTERVAL_MS && lastDiagnosticsTime != 0) {
    return;
  }
  lastDiagnosticsTime = now;

  // 1. Wi-Fi RSSI in dBm
  char rssiBuf[12];
  snprintf(rssiBuf, sizeof(rssiBuf), "%d", WiFi.RSSI());
  client.publish(TOPIC_RSSI, rssiBuf, false);

  // 2. System Uptime in seconds
  char uptimeBuf[16];
  snprintf(uptimeBuf, sizeof(uptimeBuf), "%lu", (unsigned long)(millis() / 1000UL));
  client.publish(TOPIC_UPTIME, uptimeBuf, false);

  // 3. Local IP Address
  client.publish(TOPIC_IP, WiFi.localIP().toString().c_str(), false);

  // 4. Cumulative Sensor Error Count
  char errBuf[16];
  snprintf(errBuf, sizeof(errBuf), "%u", totalSensorErrors);
  client.publish(TOPIC_ERROR_COUNT, errBuf, false);

  // 5. Sensor Operational Health
  client.publish(TOPIC_SENSOR_STATUS, isSensorOnline ? "online" : "offline", false);

  Serial.println(F("[Diagnostics] Periodic telemetry published to MQTT."));
}

// ----------------------------------------------------------------------------
// Home Assistant Automatic MQTT Discovery
// ----------------------------------------------------------------------------
void publishDiscoveryEntity(
    const char* domain,
    const char* objectId,
    const char* name,
    const char* stateTopic,
    const char* unit,
    const char* devClass,
    const char* stateClass,
    const char* uniqueId,
    const char* payloadOn,
    const char* payloadOff,
    uint32_t expireAfter
) {
  // Construct topic: <prefix>/<domain>/<device_id>/<object_id>/config
  char topic[128];
  snprintf(topic, sizeof(topic), "%s/%s/%s/%s/config",
           HA_DISCOVERY_PREFIX, domain, HA_DEVICE_ID, objectId);

  // Build JSON discovery payload
  JsonDocument doc;

  doc["name"] = name;
  doc["object_id"] = objectId;
  doc["state_topic"] = stateTopic;
  doc["unique_id"] = uniqueId;

  // Availability configuration pointing to LWT status
  doc["availability_topic"] = TOPIC_STATUS;
  doc["payload_available"] = "online";
  doc["payload_not_available"] = "offline";

  if (unit != nullptr && strlen(unit) > 0) {
    doc["unit_of_measurement"] = unit;
  }
  if (devClass != nullptr && strlen(devClass) > 0) {
    doc["device_class"] = devClass;
  }
  if (stateClass != nullptr && strlen(stateClass) > 0) {
    doc["state_class"] = stateClass;
  }
  if (payloadOn != nullptr) {
    doc["payload_on"] = payloadOn;
  }
  if (payloadOff != nullptr) {
    doc["payload_off"] = payloadOff;
  }
  if (expireAfter > 0) {
    doc["expire_after"] = expireAfter;
  }

  // Device grouping metadata
  JsonObject device = doc["device"].to<JsonObject>();
  JsonArray identifiers = device["identifiers"].to<JsonArray>();
  identifiers.add(HA_DEVICE_ID);
  device["name"] = HA_DEVICE_NAME;
  device["model"] = HA_DEVICE_MODEL;
  device["manufacturer"] = HA_DEVICE_MFG;
  device["sw_version"] = HA_DEVICE_SW_VERSION;

  // Serialize and publish
  char payload[768];
  size_t len = serializeJson(doc, payload, sizeof(payload));

  bool pubSuccess = client.publish(topic, (const uint8_t*)payload, len, true); // Retained = true
  if (pubSuccess) {
    Serial.printf("[HA Discovery] Registered entity: %s\n", name);
  } else {
    Serial.printf("[HA Discovery] ERROR publishing %s (buffer size issue?)\n", topic);
  }
}

void publishHomeAssistantDiscovery() {
  Serial.println(F("[HA Discovery] Publishing entity discovery configurations..."));

  // 1. sensor.room_temperature
  publishDiscoveryEntity(
      "sensor",
      "room_temperature",
      "Room Temperature",
      TOPIC_TEMPERATURE,
      "°C",
      "temperature",
      "measurement",
      "esp32_dht11_room_temperature",
      nullptr, nullptr,
      60 // Expire after 60s of silence
  );

  // 2. sensor.room_humidity
  publishDiscoveryEntity(
      "sensor",
      "room_humidity",
      "Room Humidity",
      TOPIC_HUMIDITY,
      "%",
      "humidity",
      "measurement",
      "esp32_dht11_room_humidity",
      nullptr, nullptr,
      60 // Expire after 60s of silence
  );

  // 3. binary_sensor.esp32_dht11_online
  publishDiscoveryEntity(
      "binary_sensor",
      "esp32_dht11_online",
      "ESP32 DHT11 Online",
      TOPIC_STATUS,
      "",
      "connectivity",
      "",
      "esp32_dht11_online_status",
      "online", "offline",
      0
  );

  // 4. sensor.esp32_dht11_wifi_signal
  publishDiscoveryEntity(
      "sensor",
      "esp32_dht11_wifi_signal",
      "ESP32 DHT11 WiFi Signal",
      TOPIC_RSSI,
      "dBm",
      "signal_strength",
      "measurement",
      "esp32_dht11_wifi_signal",
      nullptr, nullptr,
      0
  );

  // 5. sensor.esp32_dht11_uptime
  publishDiscoveryEntity(
      "sensor",
      "esp32_dht11_uptime",
      "ESP32 DHT11 Uptime",
      TOPIC_UPTIME,
      "s",
      "duration",
      "measurement",
      "esp32_dht11_uptime",
      nullptr, nullptr,
      0
  );

  // 6. sensor.esp32_dht11_error_count
  publishDiscoveryEntity(
      "sensor",
      "esp32_dht11_error_count",
      "ESP32 DHT11 Error Count",
      TOPIC_ERROR_COUNT,
      "errors",
      "",
      "total_increasing",
      "esp32_dht11_error_count",
      nullptr, nullptr,
      0
  );

  // 7. binary_sensor.dht11_sensor_health
  publishDiscoveryEntity(
      "binary_sensor",
      "dht11_sensor_health",
      "DHT11 Sensor Health",
      TOPIC_SENSOR_STATUS,
      "",
      "",
      "",
      "esp32_dht11_sensor_health",
      "online", "offline",
      0
  );

  Serial.println(F("[HA Discovery] All entities published successfully."));
}

