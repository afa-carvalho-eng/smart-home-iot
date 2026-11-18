// Template. The real file, secrets.h, is written by tools/provision_device.py
// and is ignored by Git because it holds the device's private key.
#pragma once

#define WIFI_SSID "CHANGE_ME"
#define WIFI_PASSWORD "CHANGE_ME"

// Must equal the AWS IoT thing name: "<house>-<room>".
#define THING_NAME "home01-livingroom"
// From: aws iot describe-endpoint --endpoint-type iot:Data-ATS
#define AWS_IOT_ENDPOINT "xxxxxxxxxxxxxx-ats.iot.eu-west-1.amazonaws.com"

static const char AWS_ROOT_CA[] PROGMEM = R"PEM(
-----BEGIN CERTIFICATE-----
(Amazon Root CA 1)
-----END CERTIFICATE-----
)PEM";

static const char DEVICE_CERTIFICATE[] PROGMEM = R"PEM(
-----BEGIN CERTIFICATE-----
(this device's certificate)
-----END CERTIFICATE-----
)PEM";

static const char DEVICE_PRIVATE_KEY[] PROGMEM = R"PEM(
-----BEGIN RSA PRIVATE KEY-----
(this device's private key)
-----END RSA PRIVATE KEY-----
)PEM";
