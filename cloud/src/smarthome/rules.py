"""Automation rules.

Pure functions: a reading and the room's previous state go in, a decision comes
out. Nothing here talks to AWS, which keeps the rules easy to test and to change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from smarthome.models import Reading


@dataclass(frozen=True)
class Thresholds:
    alert_above_c: float = 30.0
    # Two thresholds (hysteresis) so the fan does not flap around one value.
    fan_on_at_c: float = 28.0
    fan_off_at_c: float = 26.0
    dark_below: int = 300  # raw light level under which the room counts as dark
    light_idle_s: int = 20 * 60


@dataclass(frozen=True)
class Alert:
    key: str  # used to rate-limit repeats of the same alert
    message: str


@dataclass
class Decision:
    desired: dict[str, bool] = field(default_factory=dict)  # device states to change
    alerts: list[Alert] = field(default_factory=list)
    last_motion_at: int | None = None
    auto_light: bool = False  # the light is on because automation switched it on


def evaluate(reading: Reading, previous: dict | None, thresholds: Thresholds) -> Decision:
    previous = previous or {}
    before = previous.get("sensors", {})
    desired = previous.get("desired", {})
    decision = Decision(
        last_motion_at=previous.get("last_motion_at"),
        auto_light=bool(previous.get("auto_light")),
    )

    temperature = reading.temperature
    if temperature is not None:
        if temperature > thresholds.alert_above_c:
            decision.alerts.append(
                Alert(
                    "high_temperature",
                    f"{reading.room}: temperature is {temperature:.1f} °C",
                )
            )
        if temperature >= thresholds.fan_on_at_c and not desired.get("fan"):
            decision.desired["fan"] = True
        elif temperature <= thresholds.fan_off_at_c and desired.get("fan"):
            decision.desired["fan"] = False

    # Alert on the change from closed to open, not on every message while open.
    if reading.door_open and not before.get("door_open"):
        decision.alerts.append(Alert("door_opened", f"{reading.room}: the door was opened"))

    if reading.motion:
        decision.last_motion_at = reading.received_at
        is_dark = reading.light is not None and reading.light < thresholds.dark_below
        if is_dark and not desired.get("light"):
            decision.desired["light"] = True
            decision.auto_light = True
    elif decision.auto_light and desired.get("light") and decision.last_motion_at is not None:
        # Only lights that automation switched on are switched off again; a light
        # the user turned on by voice or from the watch is left alone.
        if reading.received_at - decision.last_motion_at >= thresholds.light_idle_s:
            decision.desired["light"] = False
            decision.auto_light = False

    return decision
