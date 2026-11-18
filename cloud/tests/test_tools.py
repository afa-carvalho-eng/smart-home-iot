import random

import boto3
import pytest
from moto import mock_aws

from smarthome.models import parse_telemetry
from tools.provision_device import provision
from tools.simulate_device import next_sensors


def test_provisioning_registers_the_device_and_writes_its_secrets(tmp_path):
    with mock_aws():
        iot = boto3.client("iot")
        iot.create_policy(policyName="SmartHomeDevice", policyDocument="{}")

        secrets = provision("home01-kitchen", "SmartHomeDevice", iot, "ROOT-CA\n", tmp_path)

        principals = iot.list_thing_principals(thingName="home01-kitchen")["principals"]
        attached = iot.list_attached_policies(target=principals[0])["policies"]
        assert [policy["policyName"] for policy in attached] == ["SmartHomeDevice"]

    text = secrets.read_text()
    assert '#define THING_NAME "home01-kitchen"' in text
    assert "ROOT-CA" in text and "BEGIN CERTIFICATE" in text
    assert (tmp_path / "certs" / "home01-kitchen" / "private.key").exists()


def test_provisioning_rejects_a_badly_formed_thing_name(tmp_path):
    with pytest.raises(SystemExit):
        provision("Living Room", "SmartHomeDevice", None, "", tmp_path)


def test_simulated_telemetry_passes_validation():
    rng = random.Random(7)
    sensors: dict = {}
    for _ in range(200):
        sensors = next_sensors(sensors, rng)
        event = {"thing_name": "home01-livingroom", "received_at": 1, "sensors": sensors}
        assert parse_telemetry(event).sensors() == sensors
