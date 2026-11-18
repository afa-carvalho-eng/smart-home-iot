"""Device control through the AWS IoT Device Shadow.

The cloud never sends "do this now" commands. It writes the state it wants
(``desired``) to the device's shadow; the device applies it and writes back what
it actually did (``reported``). If the device is offline, the shadow keeps the
desired state and the device picks it up when it reconnects.
"""

from __future__ import annotations

import json

from botocore.exceptions import ClientError


class DeviceController:
    def __init__(self, iot_data) -> None:
        self._iot = iot_data

    def set_desired(self, thing_name: str, changes: dict[str, bool]) -> None:
        payload = json.dumps({"state": {"desired": changes}})
        self._iot.update_thing_shadow(thingName=thing_name, payload=payload.encode())

    def get_reported(self, thing_name: str) -> dict:
        try:
            response = self._iot.get_thing_shadow(thingName=thing_name)
        except ClientError as error:
            if error.response["Error"]["Code"] == "ResourceNotFoundException":
                return {}  # the device has never connected
            raise
        document = json.loads(response["payload"].read())
        return document.get("state", {}).get("reported", {})
