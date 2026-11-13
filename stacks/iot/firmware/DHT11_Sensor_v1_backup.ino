#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>

// WiFi Credentials
const char* ssid =  "Raider";
const char* password = "raiderx530";

// MQTT Broker Config
const char* mqtt_server = "192.168.1.150";
const int mqtt_port = 1883;
const char* mqtt_user = "homepi";
const char* mqtt_pass = "mqtt@homepi";

// DHT Sensor Config
#define DHTPIN 4
#define DHTTYPE DHT11

#define LED_BUILTIN 2

DHT dht(DHTPIN, DHTTYPE);
WiFiClient espClient;
PubSubClient client(espClient);

void setup_wifi() {
  delay(10);
  Serial.println("Connecting to WiFi...");
  WiFi.begin(ssid, password);
  digitalWrite(LED_BUILTIN, LOW);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    digitalWrite(LED_BUILTIN, LOW);
    Serial.print(".");
  }

  Serial.println("");
  Serial.println("IP address: ");
  Serial.println(WiFi.localIP());
  digitalWrite(LED_BUILTIN, HIGH);
}

// Reconnecting to MQTT Server
void reconnect() {
  // Loop until reconnected
  while (!client.connected()) {
    Serial.print("Connecting to MQTT...");
    if (client.connect("ESP32Client", mqtt_user, mqtt_pass)) {
      digitalWrite(LED_BUILTIN, HIGH);
      Serial.println("Connected!");
    } else {
      Serial.print("Failed, rc = ");
      Serial.print(client.state());
      digitalWrite(LED_BUILTIN, LOW);
      Serial.println("Try again in 2 seconds...");
      delay(2000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_BUILTIN, OUTPUT);
  delay(1000);
  Serial.println("Initializing DHT11...");
  dht.begin();
  Serial.println("Initializing WiFi and MQTT...");
  setup_wifi();
  client.setServer(mqtt_server, mqtt_port);
}

void loop() {
  if (!client.connected()) {
    reconnect();
  }
  client.loop();

  float humidity = dht.readHumidity();
  float tempC = dht.readTemperature();

  if (isnan(humidity)  || isnan(tempC)) {
    Serial.println("Failed to read from DHT sensor! Check wiring.");
    delay(2000);
    return;
  }

  // Publish temperature and humidity readings real-time
  char tempStr[8];
  snprintf(tempStr, sizeof(tempStr), "%d", (int)tempC);
  client.publish("home/dht11/temperature", tempStr);

  char humStr[8];
  snprintf(humStr, sizeof(humStr), "%d", (int)humidity);
  client.publish("home/dht11/humidity", humStr);

  Serial.print("\nHumidity: "); Serial.print(humidity); Serial.print(" %\t");
  Serial.print("Temperature: "); Serial.print(tempC); Serial.print(" °C");
  
  delay(2000);
}
