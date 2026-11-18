"""The telemetry contract: what a device may send and how it is validated."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

# A thing is named "<house>-<room>", for example "home01-livingroom".
THING_NAME = re.compile(r"^([a-z0-9]+)-([a-z0-9]+)$")

# Accepted ranges. Values outside them mean a faulty sensor or a bad payload.
TEMPERATURE_C = (-40.0, 85.0)  # BME280 operating range
HUMIDITY_PCT = (0.0, 100.0)
LIGHT_RAW = (0, 4095)  # ESP32 12-bit ADC

DEVICES = ("light", "fan")


class InvalidTelemetry(ValueError):
    """The message cannot be used. Retrying it would not help."""


@dataclass(frozen=True)
class Reading:
    house: str
    room: str
    thing_name: str
    received_at: int  # epoch seconds, set by AWS IoT, not by the device
    temperature: float | None = None
    humidity: float | None = None
    light: int | None = None
    motion: bool | None = None
    door_open: bool | None = None
    firmware: str | None = None

    def sensors(self) -> dict:
        values = {
            "temperature": self.temperature,
            "humidity": self.humidity,
            "light": self.light,
            "motion": self.motion,
            "door_open": self.door_open,
        }
        return {name: value for name, value in values.items() if value is not None}


def thing_name_for(house: str, room: str) -> str:
    return f"{house}-{room}"


def parse_thing_name(thing_name: object) -> tuple[str, str]:
    match = THING_NAME.match(thing_name) if isinstance(thing_name, str) else None
    if not match:
        raise InvalidTelemetry(f"thing name {thing_name!r} is not '<house>-<room>'")
    return match.group(1), match.group(2)


def _number(sensors: dict, name: str, bounds: tuple[float, float]) -> float | None:
    value = sensors.get(name)
    if value is None:
        return None
    # bool is a subclass of int in Python, so it has to be excluded by hand.
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidTelemetry(f"{name} must be a number")
    low, high = bounds
    if not low <= value <= high:
        raise InvalidTelemetry(f"{name}={value} is outside {low}..{high}")
    return value


def _flag(sensors: dict, name: str) -> bool | None:
    value = sensors.get(name)
    if value is not None and not isinstance(value, bool):
        raise InvalidTelemetry(f"{name} must be true or false")
    return value


def parse_telemetry(event: dict) -> Reading:
    """Turn the event built by the IoT rule into a validated reading.

    The rule adds two fields the device cannot influence: ``thing_name``, taken
    from the MQTT topic the device is allowed to publish on, and ``received_at``,
    the broker's clock in milliseconds. House and room come from the thing name,
    so a device cannot report on behalf of another room.
    """
    if not isinstance(event, dict):
        raise InvalidTelemetry("the message is not a JSON object")
    house, room = parse_thing_name(event.get("thing_name"))

    sensors = event.get("sensors")
    if not isinstance(sensors, dict):
        raise InvalidTelemetry("'sensors' is missing")

    received_ms = event.get("received_at")
    if isinstance(received_ms, bool) or not isinstance(received_ms, int | float):
        received_at = int(time.time())
    else:
        received_at = int(received_ms // 1000)

    temperature = _number(sensors, "temperature", TEMPERATURE_C)
    humidity = _number(sensors, "humidity", HUMIDITY_PCT)
    light = _number(sensors, "light", LIGHT_RAW)
    firmware = event.get("fw")

    reading = Reading(
        house=house,
        room=room,
        thing_name=event["thing_name"],
        received_at=received_at,
        temperature=None if temperature is None else float(temperature),
        humidity=None if humidity is None else float(humidity),
        light=None if light is None else int(light),
        motion=_flag(sensors, "motion"),
        door_open=_flag(sensors, "door_open"),
        firmware=firmware if isinstance(firmware, str) else None,
    )
    if not reading.sensors():
        raise InvalidTelemetry("the message has no sensor values")
    return reading
