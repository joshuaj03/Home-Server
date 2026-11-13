#ifndef CONFIG_EXAMPLE_H
#define CONFIG_EXAMPLE_H

#include <Arduino.h>

/**
 * ============================================================================
 * ESP32 DHT11 IoT Node Configuration Template
 * ============================================================================
 * Copy this file to config.h and populate with your local network credentials.
 */

// 1. Hardware Pin Configurations
#define DHTPIN 4
#define DHTTYPE DHT11
#define LED_PIN 2

// 2. Wi-Fi Network Credentials
const char* const WIFI_SSID = "YOUR_WIFI_SSID";
const char* const WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// 3. MQTT Broker Settings
const char* const MQTT_SERVER = "192.168.1.150";
const uint16_t    MQTT_PORT = 1883;
const char* const MQTT_USER = "homepi";
const char* const MQTT_PASS = "YOUR_MQTT_PASSWORD";
const char* const MQTT_CLIENT_ID = "ESP32_DHT11_Sensor";

// 4. Timing & Interval Parameters (milliseconds)
const unsigned long SENSOR_PUBLISH_INTERVAL_MS = 2000UL;
const unsigned long DIAGNOSTICS_PUBLISH_INTERVAL_MS = 30000UL;
const unsigned long WIFI_RECONNECT_MIN_DELAY_MS = 2000UL;
const unsigned long WIFI_RECONNECT_MAX_DELAY_MS = 60000UL;
const unsigned long MQTT_RECONNECT_MIN_DELAY_MS = 2000UL;
const unsigned long MQTT_RECONNECT_MAX_DELAY_MS = 30000UL;
const uint8_t SENSOR_FAILURE_THRESHOLD = 3;

// 5. MQTT Topics
const char* const TOPIC_TEMPERATURE   = "home/dht11/temperature";
const char* const TOPIC_HUMIDITY      = "home/dht11/humidity";
const char* const TOPIC_STATUS        = "home/dht11/status";
const char* const TOPIC_RSSI          = "home/dht11/rssi";
const char* const TOPIC_UPTIME        = "home/dht11/uptime";
const char* const TOPIC_IP            = "home/dht11/ip";
const char* const TOPIC_SENSOR_STATUS = "home/dht11/sensor_status";
const char* const TOPIC_ERROR_COUNT   = "home/dht11/error_count";

// 6. Home Assistant MQTT Discovery Settings
const bool        HA_DISCOVERY_ENABLED = true;
const char* const HA_DISCOVERY_PREFIX  = "homeassistant";
const char* const HA_DEVICE_ID         = "esp32_dht11_node";
const char* const HA_DEVICE_NAME       = "ESP32 DHT11 Sensor Node";
const char* const HA_DEVICE_MODEL      = "ESP32 DHT11";
const char* const HA_DEVICE_MFG        = "Espressif / DIY";
const char* const HA_DEVICE_SW_VERSION = "2.0.0";

#endif // CONFIG_EXAMPLE_H

