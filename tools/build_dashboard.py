"""Generate grafana/dashboards/smart-home.json.

The dashboard is built from a short description here instead of being edited by
hand as a long JSON file. Run after changing it:

    python tools/build_dashboard.py
"""

from __future__ import annotations

import json
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "grafana" / "dashboards" / "smart-home.json"
DATASOURCE = {"type": "cloudwatch", "uid": "cloudwatch"}
NAMESPACE = "SmartHome"  # must match smarthome/metrics.py


def query(metric: str, statistic: str, room: str = "$room") -> dict:
    return {
        "refId": "A",
        "datasource": DATASOURCE,
        "queryMode": "Metrics",
        "metricQueryType": 0,
        "metricEditorMode": 0,
        "region": "default",
        "namespace": NAMESPACE,
        "metricName": metric,
        "dimensions": {"House": "$house", "Room": room},
        "matchExact": True,
        "statistic": statistic,
        "period": "",
        "label": "${PROP('Dim.Room')}",
    }


def panel(kind, title, metric, statistic, x, y, w, h, unit="none", **options) -> dict:
    return {
        "type": kind,
        "title": title,
        "datasource": DATASOURCE,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [query(metric, statistic)],
        "fieldConfig": {"defaults": {"unit": unit, **options.pop("defaults", {})}, "overrides": []},
        "options": options,
    }


def on_off(on_text: str, off_text: str) -> dict:
    return {
        "mappings": [
            {
                "type": "range",
                "options": {
                    "from": 0.5,
                    "to": None,
                    "result": {"text": on_text, "color": "orange"},
                },
            },
            {
                "type": "range",
                "options": {
                    "from": None,
                    "to": 0.5,
                    "result": {"text": off_text, "color": "green"},
                },
            },
        ]
    }


def build() -> dict:
    stat = {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value", "graphMode": "area"}
    flag = {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value", "graphMode": "none"}
    lines = {"legend": {"displayMode": "list", "placement": "bottom"}}
    return {
        "uid": "smart-home",
        "title": "Smart home",
        "tags": ["iot"],
        "schemaVersion": 39,
        "refresh": "1m",
        "time": {"from": "now-6h", "to": "now"},
        "templating": {
            "list": [
                {
                    "name": "house",
                    "type": "textbox",
                    "label": "House",
                    "query": "home01",
                    "current": {"text": "home01", "value": "home01"},
                },
                {
                    "name": "room",
                    "type": "query",
                    "label": "Room",
                    "datasource": DATASOURCE,
                    "query": {
                        "queryType": "dimensionValues",
                        "namespace": NAMESPACE,
                        "region": "default",
                        "metricName": "Temperature",
                        "dimensionKey": "Room",
                        "dimensionFilters": {"House": "$house"},
                    },
                    "refresh": 2,
                    "includeAll": False,
                    "multi": False,
                },
            ]
        },
        "panels": [
            panel("stat", "Temperature", "Temperature", "Average", 0, 0, 6, 5, "celsius", **stat),
            panel("stat", "Humidity", "Humidity", "Average", 6, 0, 6, 5, "percent", **stat),
            panel(
                "stat",
                "Motion",
                "Motion",
                "Maximum",
                12,
                0,
                6,
                5,
                defaults=on_off("Detected", "None"),
                **flag,
            ),
            panel(
                "stat",
                "Door",
                "DoorOpen",
                "Maximum",
                18,
                0,
                6,
                5,
                defaults=on_off("Open", "Closed"),
                **flag,
            ),
            panel(
                "timeseries",
                "Temperature history",
                "Temperature",
                "Average",
                0,
                5,
                12,
                8,
                "celsius",
                **lines,
            ),
            panel(
                "timeseries",
                "Humidity history",
                "Humidity",
                "Average",
                12,
                5,
                12,
                8,
                "percent",
                **lines,
            ),
            panel("timeseries", "Light level (raw)", "Light", "Average", 0, 13, 12, 8, **lines),
            panel(
                "timeseries",
                "Motion reports per period",
                "Motion",
                "Sum",
                12,
                13,
                12,
                8,
                **lines,
            ),
        ],
    }


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT}")
