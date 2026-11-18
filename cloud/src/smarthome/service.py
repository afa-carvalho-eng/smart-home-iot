"""Operations shared by the HTTP API and the Alexa skill."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

import boto3

from smarthome.devices import DeviceController
from smarthome.models import DEVICES
from smarthome.store import StateStore

# A reading older than this is reported as stale: the device is probably offline.
STALE_AFTER_S = 5 * 60


@dataclass(frozen=True)
class Runtime:
    house: str
    store: StateStore
    devices: DeviceController
    sns: object
    alert_topic_arn: str


@lru_cache(maxsize=1)
def runtime() -> Runtime:
    """Build the AWS clients once per Lambda container, from environment variables."""
    table = boto3.resource("dynamodb").Table(os.environ["STATE_TABLE"])
    return Runtime(
        house=os.environ.get("HOUSE_ID", "home01"),
        store=StateStore(table),
        devices=DeviceController(boto3.client("iot-data")),
        sns=boto3.client("sns"),
        alert_topic_arn=os.environ.get("ALERT_TOPIC_ARN", ""),
    )


def room_view(state: dict, now: int) -> dict:
    """The public shape of a room, as returned by the API."""
    updated_at = int(state.get("updated_at", 0))
    return {
        "room": state["room"],
        "sensors": state.get("sensors", {}),
        "devices": {name: bool(state.get("desired", {}).get(name)) for name in DEVICES},
        "updated_at": datetime.fromtimestamp(updated_at, UTC).isoformat(),
        "stale": now - updated_at > STALE_AFTER_S,
    }


def set_device(rt: Runtime, room: str, device: str, on: bool) -> dict | None:
    """Ask a device to switch on or off. Returns None if the room is unknown."""
    if device not in DEVICES:
        raise ValueError(f"unknown device {device!r}")
    state = rt.store.get_room(rt.house, room)
    if state is None:
        return None
    rt.devices.set_desired(state["thing_name"], {device: on})
    state["desired"] = {**state.get("desired", {}), device: on}
    if device == "light":
        # A light the user set by hand is no longer automation's to switch off.
        state["auto_light"] = False
    rt.store.save_room(rt.house, room, state)
    return state
