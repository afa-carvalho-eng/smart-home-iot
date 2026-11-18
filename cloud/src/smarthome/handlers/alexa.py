"""Lambda: the Alexa custom skill.

Alexa turns speech into an intent with slots (see alexa/interaction-model.json)
and sends it here as JSON. The reply is the sentence Alexa speaks. The request
and response shapes are plain JSON, so no Alexa SDK is needed.
"""

from __future__ import annotations

import os
import time

from smarthome.service import STALE_AFTER_S, Runtime, runtime, set_device

HELP = (
    "You can ask for the temperature, the humidity or the door of a room, "
    "or tell me to turn the light or the fan on or off."
)


def _speak(text: str, end_session: bool = True) -> dict:
    return {
        "version": "1.0",
        "response": {
            "outputSpeech": {"type": "PlainText", "text": text},
            "shouldEndSession": end_session,
        },
    }


def _slot(intent: dict, name: str) -> str | None:
    """The canonical ID of a slot value ("living room" resolves to "livingroom")."""
    slot = intent.get("slots", {}).get(name) or {}
    for authority in slot.get("resolutions", {}).get("resolutionsPerAuthority", []):
        if authority.get("status", {}).get("code") == "ER_SUCCESS_MATCH":
            return authority["values"][0]["value"]["id"]
    value = slot.get("value")
    return value.lower().replace(" ", "") if value else None


def _room_state(rt: Runtime, room: str | None, now: int) -> tuple[dict | None, str | None]:
    """Return (state, None), or (None, a sentence explaining why there is no answer)."""
    if not room:
        return None, "Which room do you mean?"
    state = rt.store.get_room(rt.house, room)
    if state is None:
        return None, f"I have no readings for the {room} yet."
    if now - int(state.get("updated_at", 0)) > STALE_AFTER_S:
        return None, f"The {room} sensor has not reported for a while. It may be offline."
    return state, None


def _sensor_answer(rt: Runtime, intent: dict, now: int, sensor: str, sentence) -> dict:
    room = _slot(intent, "Room")
    state, problem = _room_state(rt, room, now)
    if state is None:
        return _speak(problem, end_session=room is not None)
    value = state.get("sensors", {}).get(sensor)
    if value is None:
        return _speak(f"The {room} has no {sensor.replace('_', ' ')} sensor.")
    return _speak(sentence(room, value))


def _switch(rt: Runtime, intent: dict, on: bool) -> dict:
    room, device = _slot(intent, "Room"), _slot(intent, "Device")
    if not room or not device:
        return _speak("Tell me the device and the room.", end_session=False)
    try:
        state = set_device(rt, room, device, on)
    except ValueError:
        return _speak(f"I cannot control a {device}.")
    if state is None:
        return _speak(f"I do not know a room called {room}.")
    return _speak(f"Okay, turning the {room} {device} {'on' if on else 'off'}.")


def respond(event: dict, rt: Runtime, now: int | None = None, skill_id: str = "") -> dict:
    now = int(time.time()) if now is None else now

    # Only requests from this project's own skill are accepted.
    application = event.get("context", {}).get("System", {}).get("application", {})
    if skill_id and application.get("applicationId") != skill_id:
        raise PermissionError("the request does not come from the configured Alexa skill")

    request = event.get("request", {})
    if request.get("type") == "LaunchRequest":
        return _speak("Smart home is ready. " + HELP, end_session=False)
    if request.get("type") != "IntentRequest":
        return _speak("Goodbye.")

    intent = request.get("intent", {})
    name = intent.get("name")
    if name == "GetTemperatureIntent":
        return _sensor_answer(
            rt, intent, now, "temperature", lambda room, v: f"The {room} is at {v:.1f} degrees."
        )
    if name == "GetHumidityIntent":
        return _sensor_answer(
            rt, intent, now, "humidity", lambda room, v: f"The {room} humidity is {v:.0f} percent."
        )
    if name == "GetDoorStatusIntent":
        return _sensor_answer(
            rt,
            intent,
            now,
            "door_open",
            lambda room, v: f"The {room} door is {'open' if v else 'closed'}.",
        )
    if name == "TurnOnIntent":
        return _switch(rt, intent, True)
    if name == "TurnOffIntent":
        return _switch(rt, intent, False)
    if name == "AMAZON.HelpIntent":
        return _speak(HELP, end_session=False)
    if name in ("AMAZON.StopIntent", "AMAZON.CancelIntent"):
        return _speak("Goodbye.")
    return _speak("Sorry, I did not understand. " + HELP, end_session=False)


def handler(event, context):
    return respond(event, runtime(), skill_id=os.environ.get("ALEXA_SKILL_ID", ""))
