"""Lambda: the HTTP API used by the watch app.

Routes (API Gateway HTTP API, payload format 2.0). API Gateway has already
checked the caller's Cognito token before this code runs.

    GET  /rooms
    GET  /rooms/{room}
    POST /rooms/{room}/devices/{device}     body: {"on": true}
"""

from __future__ import annotations

import base64
import json
import time

from smarthome.models import DEVICES
from smarthome.service import Runtime, room_view, runtime, set_device


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _body(event: dict) -> dict:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def route(event: dict, rt: Runtime, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    route_key = event.get("routeKey", "")
    params = event.get("pathParameters") or {}

    if route_key == "GET /rooms":
        rooms = [room_view(state, now) for state in rt.store.list_rooms(rt.house)]
        return _response(200, {"rooms": sorted(rooms, key=lambda room: room["room"])})

    if route_key == "GET /rooms/{room}":
        state = rt.store.get_room(rt.house, params.get("room", ""))
        if state is None:
            return _response(404, {"error": "unknown room"})
        return _response(200, room_view(state, now))

    if route_key == "POST /rooms/{room}/devices/{device}":
        device = params.get("device", "")
        if device not in DEVICES:
            return _response(404, {"error": f"unknown device; expected one of {list(DEVICES)}"})
        on = _body(event).get("on")
        if not isinstance(on, bool):
            return _response(400, {"error": 'the body must be {"on": true} or {"on": false}'})
        state = set_device(rt, params.get("room", ""), device, on)
        if state is None:
            return _response(404, {"error": "unknown room"})
        # 202: the request is recorded; the device confirms through its shadow.
        return _response(202, room_view(state, now))

    return _response(404, {"error": "not found"})


def handler(event, context):
    return route(event, runtime())
