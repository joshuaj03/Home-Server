from flask import Flask, jsonify, render_template
import paho.mqtt.client as mqtt
import threading
import time
from datetime import datetime

# InfluxDB Client
from influxdb_client import InfluxDBClient, Point, WritePrecision

# Flask App
app =  Flask(__name__)

# Global variables to store latest sensor data
latest_data = {
    "temperature": None,
    "humidity": None,
    "timestamp": None
}

# MQTT Settings
MQTT_BROKER = "mosquitto"
MQTT_PORT = 1883
MQTT_TOPIC = [("home/dht11/temperature", 0), ("home/dht11/humidity", 0)]

# InfluxDB Settings
INFLUX_URL = "http://influxdb:8086"
INFLUX_TOKEN = "homepi_token124"
INFLUX_ORG = "homeiot"
INFLUX_BUCKET = "SENSORS"

# Create InfluxDB Client
influx_client = InfluxDBClient (
    url=INFLUX_URL,
    token=INFLUX_TOKEN,
    org=INFLUX_ORG
)

# Send data to influxdb
def point(field_name, payload):
    point = (
            Point("dht11")
            .tag("sensor","dht11")
            .field(field_name, payload)
            .time(datetime.utcnow(), WritePrecision.NS)
        )
    write_api.write(bucket=INFLUX_BUCKET, record=point)

# Create write API
write_api = influx_client.write_api()

# MQTT callback when a message is received
def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode()
        if msg.topic == "home/dht11/temperature":
            latest_data["temperature"] = int(payload)
            field_name = "temperature"
            point(field_name, payload)
        elif msg.topic == "home/dht11/humidity":
            latest_data["humidity"] = int(payload)
            field_name = "humidity"
            point(field_name, payload)
        latest_data["timestamp"] = datetime.now().isoformat()
        print(f"Updated: {latest_data}")

        # point = (
        #     Point("dht11")
        #     .tag("sensor","dht11")
        #     .field(field_name, payload)
        #     .time(datetime.utcnow(), WritePrecision.NS)
        # )
        # write_api.write(bucket=INFLUX_BUCKET, record=point)

        print(f"[MQTT → Influx] {field_name}: {payload} at {latest_data['timestamp']}")

    except Exception as e:
        print(f"Error parsing MQTT message: {e}")

# MQTT Client Setup
def mqtt_thread():
    client = mqtt.Client()
    client.username_pw_set("homepi", "mqtt@homepi")  
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT)
    client.subscribe(MQTT_TOPIC)
    client.loop_forever()

# Start MQTT in a seperate thread
threading.Thread(target=mqtt_thread, daemon=True).start()

# Flask endpoint to get latest sensor data
@app.route("/sensor")
def get_sensor():
    return jsonify(latest_data)
@app.route('/')
def dashboard():
    # Make sure temp/humidity are integers
    temp = int(latest_data["temperature"] or 0)
    hum = int(latest_data["humidity"] or 0)
    
    return render_template("index.html", temp=temp, hum=hum)

# Run Flask app
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

