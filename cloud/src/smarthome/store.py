"""Room state in DynamoDB.

One table, two kinds of item under the partition key ``HOUSE#<house>``:

- ``ROOM#<room>``: the latest reading and device state of a room.
- ``ALERT#<room>#<key>``: a marker that an alert was sent, removed by TTL. It
  stops the same alert being sent again on every message.
"""

from __future__ import annotations

from decimal import Decimal

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError


def _to_dynamo(value):
    # DynamoDB does not accept floats; numbers are stored as Decimal.
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _to_dynamo(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_to_dynamo(item) for item in value]
    return value


def _from_dynamo(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _from_dynamo(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(item) for item in value]
    return value


class StateStore:
    def __init__(self, table) -> None:
        self._table = table

    def get_room(self, house: str, room: str) -> dict | None:
        key = {"pk": f"HOUSE#{house}", "sk": f"ROOM#{room}"}
        item = self._table.get_item(Key=key).get("Item")
        return _from_dynamo(item) if item else None

    def list_rooms(self, house: str) -> list[dict]:
        result = self._table.query(
            KeyConditionExpression=Key("pk").eq(f"HOUSE#{house}") & Key("sk").begins_with("ROOM#")
        )
        return [_from_dynamo(item) for item in result.get("Items", [])]

    def save_room(self, house: str, room: str, state: dict) -> None:
        item = {**state, "pk": f"HOUSE#{house}", "sk": f"ROOM#{room}", "room": room}
        self._table.put_item(Item=_to_dynamo(item))

    def claim_alert(self, house: str, room: str, key: str, now: int, cooldown_s: int) -> bool:
        """Return True if this alert may be sent now, and record that it was.

        The conditional write makes the check and the record a single step, so
        two messages processed at the same moment cannot both send the alert.
        """
        try:
            self._table.put_item(
                Item={
                    "pk": f"HOUSE#{house}",
                    "sk": f"ALERT#{room}#{key}",
                    "expires_at": now + cooldown_s,
                },
                # TTL deletion can lag, so the expiry is checked here as well.
                ConditionExpression="attribute_not_exists(pk) OR expires_at <= :now",
                ExpressionAttributeValues={":now": now},
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True
