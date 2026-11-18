import json

import pytest
from conftest import HOUSE, NOW, shadow_desired, telemetry

from smarthome.handlers import alexa, api
from smarthome.handlers.telemetry import ALERT_COOLDOWN_S, process
from smarthome.service import STALE_AFTER_S


def run(rt, event):
    """Process a telemetry event, returning the result and the lines it logged."""
    lines = []
    return process(event, rt, emit=lines.append), lines


# --- store --------------------------------------------------------------------


def test_store_round_trips_numbers_and_lists_rooms(rt):
    rt.store.save_room(HOUSE, "kitchen", {"sensors": {"temperature": 21.5, "light": 300}})
    rt.store.save_room(HOUSE, "bedroom", {"sensors": {"motion": True}})
    assert rt.store.get_room(HOUSE, "kitchen")["sensors"] == {"temperature": 21.5, "light": 300}
    assert rt.store.get_room(HOUSE, "attic") is None
    assert sorted(room["room"] for room in rt.store.list_rooms(HOUSE)) == ["bedroom", "kitchen"]


def test_an_alert_is_claimed_once_per_cooldown(rt):
    assert rt.store.claim_alert(HOUSE, "kitchen", "door_opened", NOW, 60) is True
    assert rt.store.claim_alert(HOUSE, "kitchen", "door_opened", NOW + 59, 60) is False
    assert rt.store.claim_alert(HOUSE, "kitchen", "door_opened", NOW + 60, 60) is True
    assert rt.store.claim_alert(HOUSE, "bedroom", "door_opened", NOW, 60) is True


# --- telemetry ----------------------------------------------------------------


def test_telemetry_is_stored_and_measured(rt):
    result, lines = run(rt, telemetry(temperature=22.5, humidity=54.2, motion=False))
    state = rt.store.get_room(HOUSE, "livingroom")
    assert result == {"status": "processed", "desired": {}, "alerts": []}
    assert state["sensors"] == {"temperature": 22.5, "humidity": 54.2, "motion": False}
    assert state["updated_at"] == NOW
    assert json.loads(lines[0])["Temperature"] == 22.5


def test_a_hot_room_starts_the_fan_and_alerts_once(rt):
    run(rt, telemetry(temperature=31.0))
    run(rt, telemetry(at=NOW + 30, temperature=31.5))
    assert shadow_desired(rt) == {"fan": True}
    assert rt.sns.messages == ["livingroom: temperature is 31.0 °C"]

    run(rt, telemetry(at=NOW + ALERT_COOLDOWN_S, temperature=32.0))
    assert len(rt.sns.messages) == 2


def test_partial_messages_keep_the_other_sensor_values(rt):
    run(rt, telemetry(temperature=22.0, door_open=False))
    run(rt, telemetry(at=NOW + 5, door_open=True))
    assert rt.store.get_room(HOUSE, "livingroom")["sensors"] == {
        "temperature": 22.0,
        "door_open": True,
    }
    assert rt.sns.messages == ["livingroom: the door was opened"]


def test_invalid_telemetry_is_rejected_without_raising(rt):
    result, lines = run(rt, telemetry(temperature=999))
    assert result["status"] == "rejected"
    assert "telemetry rejected" in lines[0]
    assert rt.store.get_room(HOUSE, "livingroom") is None


def test_late_messages_do_not_overwrite_newer_state(rt):
    run(rt, telemetry(at=NOW, temperature=25.0))
    result, _ = run(rt, telemetry(at=NOW - 60, temperature=10.0))
    assert result["status"] == "ignored"
    assert rt.store.get_room(HOUSE, "livingroom")["sensors"]["temperature"] == 25.0


# --- HTTP API -----------------------------------------------------------------


def call(rt, route_key, now=NOW, body=None, **params):
    event = {"routeKey": route_key, "pathParameters": params}
    if body is not None:
        event["body"] = body if isinstance(body, str) else json.dumps(body)
    response = api.route(event, rt, now=now)
    return response["statusCode"], json.loads(response["body"])


def test_api_lists_rooms_and_flags_stale_readings(rt):
    run(rt, telemetry(temperature=22.5))
    status, body = call(rt, "GET /rooms")
    assert status == 200
    assert body["rooms"][0]["room"] == "livingroom"
    assert body["rooms"][0]["stale"] is False
    assert body["rooms"][0]["devices"] == {"light": False, "fan": False}

    _, later = call(rt, "GET /rooms/{room}", now=NOW + STALE_AFTER_S + 1, room="livingroom")
    assert later["stale"] is True


def test_api_switches_a_device_through_the_shadow(rt):
    run(rt, telemetry(temperature=22.5))
    status, body = call(
        rt,
        "POST /rooms/{room}/devices/{device}",
        body={"on": True},
        room="livingroom",
        device="light",
    )
    assert status == 202
    assert body["devices"]["light"] is True
    assert shadow_desired(rt) == {"light": True}


@pytest.mark.parametrize(
    ("params", "body", "expected"),
    [
        ({"room": "attic", "device": "light"}, {"on": True}, 404),
        ({"room": "livingroom", "device": "oven"}, {"on": True}, 404),
        ({"room": "livingroom", "device": "light"}, {"on": "yes"}, 400),
        ({"room": "livingroom", "device": "light"}, "not json", 400),
    ],
)
def test_api_rejects_bad_device_requests(rt, params, body, expected):
    run(rt, telemetry(temperature=22.5))
    status, _ = call(rt, "POST /rooms/{room}/devices/{device}", body=body, **params)
    assert status == expected


def test_api_unknown_routes_and_rooms(rt):
    assert call(rt, "GET /rooms/{room}", room="attic")[0] == 404
    assert call(rt, "DELETE /rooms")[0] == 404


# --- Alexa --------------------------------------------------------------------


def ask(rt, intent, now=NOW, skill_id="", **slots):
    event = {
        "context": {"System": {"application": {"applicationId": "amzn1.ask.skill.mine"}}},
        "request": {
            "type": "IntentRequest",
            "intent": {
                "name": intent,
                "slots": {name: {"name": name, "value": value} for name, value in slots.items()},
            },
        },
    }
    response = alexa.respond(event, rt, now=now, skill_id=skill_id)["response"]
    return response["outputSpeech"]["text"]


def test_alexa_reads_the_sensors(rt):
    run(rt, telemetry(temperature=21.84, humidity=54.2, door_open=False))
    assert (
        ask(rt, "GetTemperatureIntent", Room="living room") == "The livingroom is at 21.8 degrees."
    )
    assert (
        ask(rt, "GetHumidityIntent", Room="livingroom") == "The livingroom humidity is 54 percent."
    )
    assert ask(rt, "GetDoorStatusIntent", Room="livingroom") == "The livingroom door is closed."


def test_alexa_says_when_it_cannot_answer(rt):
    run(rt, telemetry(temperature=21.8))
    assert "no readings" in ask(rt, "GetTemperatureIntent", Room="attic")
    assert "Which room" in ask(rt, "GetTemperatureIntent")
    assert "no humidity sensor" in ask(rt, "GetHumidityIntent", Room="livingroom")
    late = NOW + STALE_AFTER_S + 1
    assert "may be offline" in ask(rt, "GetTemperatureIntent", now=late, Room="livingroom")


def test_alexa_switches_devices(rt):
    run(rt, telemetry(temperature=21.8))
    answer = ask(rt, "TurnOnIntent", Room="livingroom", Device="fan")
    assert answer == "Okay, turning the livingroom fan on."
    assert shadow_desired(rt) == {"fan": True}
    assert "cannot control" in ask(rt, "TurnOffIntent", Room="livingroom", Device="oven")
    assert "do not know a room" in ask(rt, "TurnOffIntent", Room="attic", Device="fan")


def test_alexa_uses_the_resolved_slot_id(rt):
    run(rt, telemetry(temperature=21.8))
    slot = {
        "name": "Room",
        "value": "the lounge",
        "resolutions": {
            "resolutionsPerAuthority": [
                {
                    "status": {"code": "ER_SUCCESS_MATCH"},
                    "values": [{"value": {"name": "living room", "id": "livingroom"}}],
                }
            ]
        },
    }
    event = {
        "request": {
            "type": "IntentRequest",
            "intent": {"name": "GetTemperatureIntent", "slots": {"Room": slot}},
        }
    }
    text = alexa.respond(event, rt, now=NOW)["response"]["outputSpeech"]["text"]
    assert text == "The livingroom is at 21.8 degrees."


def test_alexa_rejects_requests_from_another_skill(rt):
    with pytest.raises(PermissionError):
        ask(rt, "AMAZON.HelpIntent", skill_id="amzn1.ask.skill.other")
    assert "You can ask" in ask(rt, "AMAZON.HelpIntent", skill_id="amzn1.ask.skill.mine")


def test_alexa_launch_keeps_the_session_open(rt):
    response = alexa.respond({"request": {"type": "LaunchRequest"}}, rt, now=NOW)["response"]
    assert response["shouldEndSession"] is False
