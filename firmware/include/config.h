// Wiring and timing. Change the pins here to match your board.
#pragma once

#define FIRMWARE_VERSION "1.0.0"

// Sensors
#define PIN_PIR 27    // PIR motion sensor, digital output
#define PIN_REED 26   // reed switch to GND, internal pull-up: LOW = door closed
#define PIN_LDR 34    // light sensor divider. ADC1 pin: ADC2 does not work while Wi-Fi is on
// BME280 on the default I2C pins: SDA 21, SCL 22
#define BME280_ADDRESS_PRIMARY 0x76
#define BME280_ADDRESS_SECONDARY 0x77

// Outputs. Low-voltage loads only (an LED, a 5 V fan): no mains wiring.
#define PIN_LIGHT 25
#define PIN_FAN 33
#define OUTPUT_ACTIVE_LEVEL HIGH  // set to LOW for active-low relay boards

// Timing
#define TELEMETRY_INTERVAL_MS 30000UL  // periodic report
#define SENSOR_POLL_MS 50UL            // how often motion and door are sampled
#define RECONNECT_MIN_MS 2000UL        // reconnect backoff: doubles up to the maximum
#define RECONNECT_MAX_MS 60000UL
