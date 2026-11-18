"""Sensor history as CloudWatch metrics, written in Embedded Metric Format.

A Lambda function that prints a JSON line in this format gets CloudWatch metrics
created from its log, with no API call and no extra latency. Grafana reads the
metrics through its built-in CloudWatch data source.
"""

from __future__ import annotations

import json

from smarthome.models import Reading

NAMESPACE = "SmartHome"

_METRICS = (
    ("Temperature", "temperature", "None"),
    ("Humidity", "humidity", "Percent"),
    ("Light", "light", "None"),
    ("Motion", "motion", "Count"),
    ("DoorOpen", "door_open", "Count"),
)


def emf_line(reading: Reading) -> str:
    values = reading.sensors()
    record: dict = {"House": reading.house, "Room": reading.room}
    definitions = []
    for metric, sensor, unit in _METRICS:
        if sensor in values:
            # Booleans become 0/1 so they can be charted and summed.
            record[metric] = float(values[sensor])
            definitions.append({"Name": metric, "Unit": unit})
    record["_aws"] = {
        "Timestamp": reading.received_at * 1000,
        "CloudWatchMetrics": [
            {
                "Namespace": NAMESPACE,
                "Dimensions": [["House", "Room"]],
                "Metrics": definitions,
            }
        ],
    }
    return json.dumps(record)
