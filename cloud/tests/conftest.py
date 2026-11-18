"""Shared fixtures. AWS is replaced by moto, an in-process imitation of the services."""

import json

import boto3
import pytest
from moto import mock_aws

from smarthome.devices import DeviceController
from smarthome.service import Runtime
from smarthome.store import StateStore

HOUSE = "home01"
THING = "home01-livingroom"
NOW = 1_800_000_000  # a fixed "current time" in epoch seconds


class SpySns:
    """Records alerts instead of sending them."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def publish(self, **kwargs) -> None:
        self.messages.append(kwargs["Message"])


@pytest.fixture(autouse=True)
def aws_environment(monkeypatch):
    # Fake credentials: no test can reach a real AWS account.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")


@pytest.fixture
def rt():
    with mock_aws():
        table = boto3.resource("dynamodb").create_table(
            TableName="state",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        boto3.client("iot").create_thing(thingName=THING)
        yield Runtime(
            house=HOUSE,
            store=StateStore(table),
            devices=DeviceController(boto3.client("iot-data")),
            sns=SpySns(),
            alert_topic_arn="arn:aws:sns:eu-west-1:123456789012:alerts",
        )


def telemetry(at: int = NOW, thing: str = THING, **sensors) -> dict:
    """A telemetry event as the IoT rule delivers it to the Lambda function."""
    return {"thing_name": thing, "received_at": at * 1000, "fw": "1.0.0", "sensors": sensors}


def shadow_desired(rt: Runtime, thing: str = THING) -> dict:
    response = boto3.client("iot-data").get_thing_shadow(thingName=thing)
    return json.loads(response["payload"].read())["state"].get("desired", {})
