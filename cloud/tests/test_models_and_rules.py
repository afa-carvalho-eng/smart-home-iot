import json
import time

import pytest
from conftest import NOW, telemetry

from smarthome.metrics import emf_line
from smarthome.models import InvalidTelemetry, parse_telemetry
from smarthome.rules import Thresholds, evaluate

T = Thresholds()


def reading(at=NOW, **sensors):
    return parse_telemetry(telemetry(at=at, **sensors))


# --- validation ---------------------------------------------------------------


def test_house_and_room_come_from_the_thing_name():
    event = telemetry(temperature=22.5, motion=True)
    event["room_id"] = "bedroom"  # a device cannot claim to be another room
    parsed = parse_telemetry(event)
    assert (parsed.house, parsed.room) == ("home01", "livingroom")
    assert parsed.received_at == NOW
    assert parsed.sensors() == {"temperature": 22.5, "motion": True}


@pytest.mark.parametrize(
    "event",
    [
        telemetry(thing="not a thing name", temperature=20),
        telemetry(temperature=500),
        telemetry(humidity=-1),
        telemetry(light=5000),
        telemetry(temperature="hot"),
        telemetry(temperature=True),
        telemetry(motion=1),
        telemetry(),
        {"thing_name": "home01-livingroom"},
        "not an object",
    ],
)
def test_bad_messages_are_rejected(event):
    with pytest.raises(InvalidTelemetry):
        parse_telemetry(event)


def test_missing_broker_time_falls_back_to_the_clock():
    event = telemetry(temperature=20)
    del event["received_at"]
    before = int(time.time())
    assert before <= parse_telemetry(event).received_at <= before + 5


# --- automation rules ---------------------------------------------------------


def test_high_temperature_raises_an_alert_and_starts_the_fan():
    decision = evaluate(reading(temperature=31.0), None, T)
    assert [alert.key for alert in decision.alerts] == ["high_temperature"]
    assert decision.desired == {"fan": True}


def test_fan_has_hysteresis():
    running = {"desired": {"fan": True}}
    assert evaluate(reading(temperature=27.0), running, T).desired == {}
    assert evaluate(reading(temperature=26.0), running, T).desired == {"fan": False}
    assert evaluate(reading(temperature=27.0), {"desired": {"fan": False}}, T).desired == {}


def test_door_alert_only_when_it_opens():
    closed = {"sensors": {"door_open": False}}
    opened = {"sensors": {"door_open": True}}
    assert [a.key for a in evaluate(reading(door_open=True), closed, T).alerts] == ["door_opened"]
    assert evaluate(reading(door_open=True), opened, T).alerts == []
    assert evaluate(reading(door_open=False), opened, T).alerts == []


def test_motion_in_the_dark_turns_the_light_on():
    decision = evaluate(reading(motion=True, light=100), None, T)
    assert decision.desired == {"light": True}
    assert decision.auto_light is True
    assert decision.last_motion_at == NOW


def test_motion_in_daylight_leaves_the_light_alone():
    assert evaluate(reading(motion=True, light=900), None, T).desired == {}


def test_automatic_light_turns_off_after_the_idle_time():
    state = {"desired": {"light": True}, "auto_light": True, "last_motion_at": NOW}
    early = evaluate(reading(at=NOW + T.light_idle_s - 1, motion=False), state, T)
    late = evaluate(reading(at=NOW + T.light_idle_s, motion=False), state, T)
    assert early.desired == {}
    assert late.desired == {"light": False}
    assert late.auto_light is False


def test_a_light_the_user_turned_on_is_not_turned_off():
    state = {"desired": {"light": True}, "auto_light": False, "last_motion_at": NOW}
    assert evaluate(reading(at=NOW + 10_000, motion=False), state, T).desired == {}


# --- metrics ------------------------------------------------------------------


def test_metrics_line_follows_the_embedded_metric_format():
    record = json.loads(emf_line(reading(temperature=22.5, motion=True)))
    directive = record["_aws"]["CloudWatchMetrics"][0]
    assert record["_aws"]["Timestamp"] == NOW * 1000
    assert directive["Namespace"] == "SmartHome"
    assert directive["Dimensions"] == [["House", "Room"]]
    assert [metric["Name"] for metric in directive["Metrics"]] == ["Temperature", "Motion"]
    assert (record["House"], record["Room"]) == ("home01", "livingroom")
    assert (record["Temperature"], record["Motion"]) == (22.5, 1.0)
