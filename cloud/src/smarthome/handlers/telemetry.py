"""Lambda: process one telemetry message forwarded by the AWS IoT rule."""

from __future__ import annotations

import json

from smarthome.metrics import emf_line
from smarthome.models import InvalidTelemetry, parse_telemetry
from smarthome.rules import Thresholds, evaluate
from smarthome.service import Runtime, runtime

ALERT_COOLDOWN_S = 30 * 60


def process(event: dict, rt: Runtime, thresholds: Thresholds | None = None, emit=print) -> dict:
    try:
        reading = parse_telemetry(event)
    except InvalidTelemetry as error:
        # Returning normally (instead of raising) stops Lambda retrying a message
        # that can never succeed.
        emit(json.dumps({"level": "warning", "message": f"telemetry rejected: {error}"}))
        return {"status": "rejected", "reason": str(error)}

    previous = rt.store.get_room(reading.house, reading.room)
    if previous and previous.get("updated_at", 0) > reading.received_at:
        # MQTT can deliver late or twice; an older message must not overwrite newer state.
        return {"status": "ignored", "reason": "older than the stored reading"}

    decision = evaluate(reading, previous, thresholds or Thresholds())
    emit(emf_line(reading))

    if decision.desired:
        rt.devices.set_desired(reading.thing_name, decision.desired)

    sent = []
    for alert in decision.alerts:
        claimed = rt.store.claim_alert(
            reading.house, reading.room, alert.key, reading.received_at, ALERT_COOLDOWN_S
        )
        if claimed and rt.alert_topic_arn:
            rt.sns.publish(
                TopicArn=rt.alert_topic_arn, Subject="Smart home alert", Message=alert.message
            )
            sent.append(alert.key)

    previous = previous or {}
    rt.store.save_room(
        reading.house,
        reading.room,
        {
            "thing_name": reading.thing_name,
            "updated_at": reading.received_at,
            # Keep the last known value of a sensor that this message did not include.
            "sensors": {**previous.get("sensors", {}), **reading.sensors()},
            "desired": {**previous.get("desired", {}), **decision.desired},
            "last_motion_at": decision.last_motion_at,
            "auto_light": decision.auto_light,
            "firmware": reading.firmware or previous.get("firmware"),
        },
    )
    return {"status": "processed", "desired": decision.desired, "alerts": sent}


def handler(event, context):
    return process(event, runtime())
