// Smart home room node for the ESP32.
//
// Reads the sensors, publishes telemetry to AWS IoT Core over MQTT with mutual
// TLS, and switches the light and fan outputs to match the Device Shadow.
//
// The device holds no rules: what to switch, and when, is decided in the cloud.
// It only reports what it measures and applies the state it is asked for.

#include <Arduino.h>
#include <ArduinoJson.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <Wire.h>

#include <Adafruit_BME280.h>

#include "config.h"
#include "secrets.h"

static WiFiClientSecure tls;
static PubSubClient mqtt(tls);
static Adafruit_BME280 bme;
static bool bmeReady = false;

static const String shadowTopic = String("$aws/things/") + THING_NAME + "/shadow";
static const String telemetryTopic = String("smarthome/") + THING_NAME + "/telemetry";
static const String shadowUpdateTopic = shadowTopic + "/update";
static const String shadowDeltaTopic = shadowTopic + "/update/delta";
static const String shadowGetTopic = shadowTopic + "/get";
static const String shadowGetAcceptedTopic = shadowTopic + "/get/accepted";

static bool lightOn = false;
static bool fanOn = false;
static bool reportPending = false;     // outputs changed: tell the shadow
static bool telemetryPending = true;   // send a reading as soon as connected

static bool lastMotion = false;
static bool lastDoorOpen = false;
static unsigned long lastPollMs = 0;
static unsigned long lastTelemetryMs = 0;
static unsigned long lastAttemptMs = 0;
static bool hasTriedToConnect = false;
static unsigned long reconnectDelayMs = RECONNECT_MIN_MS;

static void applyOutputs() {
  digitalWrite(PIN_LIGHT, lightOn ? OUTPUT_ACTIVE_LEVEL : !OUTPUT_ACTIVE_LEVEL);
  digitalWrite(PIN_FAN, fanOn ? OUTPUT_ACTIVE_LEVEL : !OUTPUT_ACTIVE_LEVEL);
}

// Applies the "light" and "fan" members of a shadow state object, if present.
static void applyDesired(JsonVariantConst state) {
  if (state["light"].is<bool>()) lightOn = state["light"].as<bool>();
  if (state["fan"].is<bool>()) fanOn = state["fan"].as<bool>();
  applyOutputs();
  reportPending = true;
}

static void onMessage(char *topic, byte *payload, unsigned int length) {
  JsonDocument doc;
  if (deserializeJson(doc, payload, length)) return;  // not valid JSON: ignore

  if (shadowDeltaTopic == topic) {
    // A change requested while the device is online.
    applyDesired(doc["state"]);
  } else if (shadowGetAcceptedTopic == topic) {
    // The full shadow, requested after connecting: catches up on anything
    // requested while the device was offline.
    applyDesired(doc["state"]["desired"]);
  }
}

static void publishReportedState() {
  JsonDocument doc;
  JsonObject reported = doc["state"]["reported"].to<JsonObject>();
  reported["light"] = lightOn;
  reported["fan"] = fanOn;
  reported["fw"] = FIRMWARE_VERSION;
  char buffer[160];
  serializeJson(doc, buffer);
  if (mqtt.publish(shadowUpdateTopic.c_str(), buffer)) reportPending = false;
}

static void publishTelemetry() {
  JsonDocument doc;
  doc["fw"] = FIRMWARE_VERSION;
  JsonObject sensors = doc["sensors"].to<JsonObject>();

  if (bmeReady) {
    float temperature = bme.readTemperature();
    float humidity = bme.readHumidity();
    // A failed read gives NaN. The field is left out rather than sending a bad value.
    if (!isnan(temperature)) sensors["temperature"] = roundf(temperature * 10.0f) / 10.0f;
    if (!isnan(humidity)) sensors["humidity"] = roundf(humidity * 10.0f) / 10.0f;
  }
  sensors["light"] = analogRead(PIN_LDR);
  sensors["motion"] = lastMotion;
  sensors["door_open"] = lastDoorOpen;

  char buffer[256];
  serializeJson(doc, buffer);
  if (mqtt.publish(telemetryTopic.c_str(), buffer)) {
    telemetryPending = false;
    lastTelemetryMs = millis();
  }
}

static void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Wi-Fi: connecting to %s\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000UL) delay(250);
  if (WiFi.status() != WL_CONNECTED) return;

  // The clock is needed to check the validity dates of the server certificate.
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  start = millis();
  while (time(nullptr) < 1700000000 && millis() - start < 10000UL) delay(250);
}

static bool connectMqtt() {
  Serial.println("MQTT: connecting to AWS IoT Core");
  // The client ID must be the thing name: the device policy allows nothing else.
  if (!mqtt.connect(THING_NAME)) {
    Serial.printf("MQTT: failed, state %d\n", mqtt.state());
    return false;
  }
  mqtt.subscribe(shadowDeltaTopic.c_str(), 1);
  mqtt.subscribe(shadowGetAcceptedTopic.c_str(), 1);
  mqtt.publish(shadowGetTopic.c_str(), "");  // ask for the current shadow
  reportPending = true;
  telemetryPending = true;
  return true;
}

// Reconnects with exponential backoff and without blocking the sensor loop.
static void maintainConnection() {
  if (mqtt.connected()) return;
  // Elapsed-time comparison, so the millis() rollover after 49 days is harmless.
  if (hasTriedToConnect && millis() - lastAttemptMs < reconnectDelayMs) return;

  connectWifi();
  bool connected = WiFi.status() == WL_CONNECTED && connectMqtt();
  hasTriedToConnect = true;
  lastAttemptMs = millis();
  reconnectDelayMs = connected ? RECONNECT_MIN_MS : min(reconnectDelayMs * 2, RECONNECT_MAX_MS);
}

void setup() {
  Serial.begin(115200);

  pinMode(PIN_PIR, INPUT);
  pinMode(PIN_REED, INPUT_PULLUP);
  pinMode(PIN_LIGHT, OUTPUT);
  pinMode(PIN_FAN, OUTPUT);
  applyOutputs();  // start with both outputs off

  Wire.begin();
  bmeReady = bme.begin(BME280_ADDRESS_PRIMARY) || bme.begin(BME280_ADDRESS_SECONDARY);
  if (!bmeReady) Serial.println("BME280 not found: temperature and humidity are not reported");

  tls.setCACert(AWS_ROOT_CA);
  tls.setCertificate(DEVICE_CERTIFICATE);
  tls.setPrivateKey(DEVICE_PRIVATE_KEY);
  mqtt.setServer(AWS_IOT_ENDPOINT, 8883);
  mqtt.setBufferSize(1024);  // shadow documents are larger than the 256-byte default
  mqtt.setCallback(onMessage);
}

void loop() {
  maintainConnection();
  mqtt.loop();

  unsigned long now = millis();
  if (now - lastPollMs >= SENSOR_POLL_MS) {
    lastPollMs = now;
    bool motion = digitalRead(PIN_PIR) == HIGH;
    bool doorOpen = digitalRead(PIN_REED) == HIGH;
    // Motion and door changes are reported at once, not at the next interval.
    if (motion != lastMotion || doorOpen != lastDoorOpen) telemetryPending = true;
    lastMotion = motion;
    lastDoorOpen = doorOpen;
  }
  if (now - lastTelemetryMs >= TELEMETRY_INTERVAL_MS) telemetryPending = true;

  if (mqtt.connected()) {
    if (reportPending) publishReportedState();
    if (telemetryPending) publishTelemetry();
  }
}
