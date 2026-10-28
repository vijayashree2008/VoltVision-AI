import json
import os
import time
from pathlib import Path

import paho.mqtt.client as mqtt

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "live_data.json"
HISTORY_FILE = BASE_DIR / "live_history.jsonl"

MQTT_HOST = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_TOPIC = "nilm-expo/demo-7f3a91/telemetry"


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to MQTT broker")
        client.subscribe(MQTT_TOPIC)
        print("Subscribed to:", MQTT_TOPIC)
    else:
        print("MQTT connection failed:", rc)


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))

        # Record the time this message was received.
        data["received_at"] = time.time()

        # Save the latest reading.
        temp_file = DATA_FILE.with_suffix(".tmp")
        temp_file.write_text(
            json.dumps(data),
            encoding="utf-8"
        )
        os.replace(temp_file, DATA_FILE)

        # Save history for charts and energy calculations.
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")

        print(
            time.strftime("%H:%M:%S"),
            "Received:",
            json.dumps(data)
        )

    except (ValueError, OSError) as error:
        print("Could not process message:", error)


client = mqtt.Client(client_id="NILMReceiverPython")
client.on_connect = on_connect
client.on_message = on_message

print("Connecting to MQTT broker:", MQTT_HOST)

try:
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_forever()
except (OSError, Exception) as error:
    print("MQTT connection error:", error)
