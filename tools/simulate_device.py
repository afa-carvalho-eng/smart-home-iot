"""Publish made-up telemetry, to try the cloud side without any hardware.

    python tools/simulate_device.py home01-livingroom --interval 10

Sends through the AWS IoT data API with the AWS credentials configured on this
machine, on the same topic a real device uses, so the IoT rule, the Lambda
function, the alerts and the Grafana dashboard all behave as they would with an
ESP32. The values are random and are not measurements.
"""

from __future__ import annotations

import argparse
import json
import random
import time

import boto3


def next_sensors(previous: dict, rng: random.Random) -> dict:
    """A small random step from the previous values, kept inside realistic limits."""
    temperature = previous.get("temperature", 22.0) + rng.uniform(-0.3, 0.3)
    humidity = previous.get("humidity", 50.0) + rng.uniform(-1.0, 1.0)
    return {
        "temperature": round(min(max(temperature, 15.0), 35.0), 1),
        "humidity": round(min(max(humidity, 20.0), 90.0), 1),
        "light": rng.randint(0, 1200),
        "motion": rng.random() < 0.2,
        "door_open": rng.random() < 0.05,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("thing", help="thing name, '<house>-<room>'")
    parser.add_argument("--interval", type=float, default=30.0, help="seconds between messages")
    parser.add_argument("--count", type=int, default=0, help="messages to send (0 = until Ctrl+C)")
    args = parser.parse_args()

    client = boto3.client("iot-data")
    topic = f"smarthome/{args.thing}/telemetry"
    rng = random.Random()
    sensors: dict = {}
    sent = 0
    while args.count == 0 or sent < args.count:
        sensors = next_sensors(sensors, rng)
        client.publish(
            topic=topic, qos=1, payload=json.dumps({"fw": "simulator", "sensors": sensors})
        )
        sent += 1
        print(f"{topic}: {sensors}")
        if args.count == 0 or sent < args.count:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
