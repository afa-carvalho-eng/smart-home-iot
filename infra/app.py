#!/usr/bin/env python3
"""CDK entry point. Run from the infra folder: cdk synth / cdk deploy.

Optional settings, passed as context (for example ``-c rooms=livingroom,bedroom``):

    house           house ID used in thing names         (default: home01)
    rooms           comma-separated rooms to watch       (default: livingroom)
    alert_email     address subscribed to the alerts     (default: none)
    alexa_skill_id  the Alexa skill allowed to call in   (default: none)
"""

import aws_cdk as cdk
from smart_home_stack import SmartHomeStack

app = cdk.App()
context = app.node.try_get_context
SmartHomeStack(
    app,
    "SmartHome",
    house=context("house") or "home01",
    rooms=tuple(room.strip() for room in (context("rooms") or "livingroom").split(",")),
    alert_email=context("alert_email"),
    alexa_skill_id=context("alexa_skill_id"),
)
app.synth()
