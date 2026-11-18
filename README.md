# Smart home on AWS IoT

A small smart home system: an ESP32 in each room reports temperature, humidity, light, motion and door state to AWS; the cloud stores the readings, raises alerts and switches a light and a fan automatically; and the house can be watched in Grafana and controlled by voice through Alexa or from a Wear OS watch.

![Architecture: an ESP32 talks MQTT to AWS IoT Core; an IoT rule sends telemetry to a Lambda function that updates DynamoDB, CloudWatch, SNS and the Device Shadow; Grafana reads CloudWatch; a watch app and an Alexa skill read state and switch devices through two more Lambda functions](docs/architecture.svg)

## Status

What has and has not been verified:

| Part | State |
|---|---|
| Cloud code (Python) | 43 unit tests pass. AWS is replaced by [moto](https://github.com/getmoto/moto), an in-process imitation of DynamoDB, IoT and the Device Shadow. |
| Infrastructure (CDK) | Synthesizes to a CloudFormation template (37 resources). **Never deployed.** |
| ESP32 firmware | **Never compiled or run on a board.** The CI workflow compiles it on every push. |
| Grafana dashboard and provisioning | Written from the Grafana documentation. **Never loaded into Grafana.** |
| Alexa interaction model | Valid JSON. **Never imported into the Alexa developer console.** |
| Wear OS app | **Never built.** Dependency versions in the Gradle files need checking in Android Studio. |
| End to end | **Nothing has run against a real AWS account or real hardware.** |

Expect to fix things in the unverified parts on first use. [Known gaps](#known-gaps) lists the limits that are already known.

## How it works

**A device reports.** Each ESP32 connects to AWS IoT Core over MQTT with mutual TLS, using its own X.509 certificate. It publishes one kind of message, telemetry, every 30 seconds and immediately when motion or the door changes.

**The cloud processes.** An IoT rule forwards each message to the telemetry Lambda function, which:

1. validates it (types, ranges, known device), and drops what is invalid;
2. writes the readings as CloudWatch metrics, which is the history Grafana charts;
3. applies the automation rules and, if a device should change, writes the wanted state to the Device Shadow;
4. sends alerts through SNS, at most once per 30 minutes for the same cause;
5. saves the room's latest state in DynamoDB.

**A device is controlled.** Nothing sends the ESP32 a direct command. Automation, Alexa and the watch all write a *desired* state to the device's shadow. AWS IoT delivers the difference to the device, which switches the output and writes back the *reported* state. If the device is offline, the shadow keeps the request and the device applies it when it reconnects.

**The user looks and asks.** Grafana reads CloudWatch. The Alexa skill and the watch app read the latest state from DynamoDB through their own Lambda functions. Alerts arrive by email, which a phone mirrors to a paired watch as a notification.

### Automation rules

Defined in [rules.py](cloud/src/smarthome/rules.py) as pure functions:

| Condition | Action |
|---|---|
| Temperature above 30 °C | Alert |
| Temperature at or above 28 °C | Fan on (off again at or below 26 °C) |
| Motion while the room is dark | Light on |
| No motion for 20 minutes | Light off, only if automation turned it on |
| Door changes from closed to open | Alert |
| No telemetry for 10 minutes | "Device offline" alert (CloudWatch alarm) |

### Message contract

A device publishes to `smarthome/<thing-name>/telemetry`, where the thing name is `<house>-<room>`, for example `home01-livingroom`:

```json
{
  "fw": "1.0.0",
  "sensors": {
    "temperature": 22.5,
    "humidity": 54.2,
    "light": 650,
    "motion": true,
    "door_open": false
  }
}
```

Every sensor field is optional, so a device with fewer sensors, or with a failed sensor, sends what it has. The message carries no room name and no timestamp on purpose: the cloud takes the room from the topic and the time from the broker (see the design decisions).

Device control uses the standard shadow document: `{"state": {"desired": {"light": true, "fan": false}}}`.

## Repository layout

| Folder | Contents |
|---|---|
| [cloud/](cloud/) | Python code for the three Lambda functions, and their tests |
| [infra/](infra/) | AWS CDK stack that creates all AWS resources |
| [firmware/](firmware/) | ESP32 firmware (C++, PlatformIO, Arduino framework) |
| [grafana/](grafana/) | Docker Compose file, data source and dashboard for Grafana |
| [alexa/](alexa/) | Interaction model of the Alexa custom skill |
| [watch/](watch/) | Wear OS app (Kotlin, Compose) |
| [tools/](tools/) | Device registration, a device simulator, the dashboard generator |

## Design decisions

Where this differs from a typical first design for such a project, the reason is given.

- **CloudWatch metrics instead of Amazon Timestream for the history.** Timestream for LiveAnalytics [has been closed to new AWS customers since June 2025](https://docs.aws.amazon.com/timestream/latest/developerguide/AmazonTimestreamForLiveAnalytics-availability-change.html), so a new account cannot create it. Its suggested replacement runs on always-on instances, which is costly for a three-room house. CloudWatch metrics are serverless, Grafana reads them with its built-in data source, and the same metrics drive the "device offline" alarm. The trade-off: CloudWatch keeps one-minute detail for 15 days and coarser detail for up to 15 months, and it is not a database you can query freely.
- **Metrics are written through the log, not through an API call.** The function prints each reading in CloudWatch's Embedded Metric Format and CloudWatch turns the log line into metrics. This adds no latency or failure point to message processing.
- **The device's identity comes from the MQTT topic, not from the payload.** The device policy lets a certificate publish only to its own topic, and the rule copies the thing name from that topic. A device therefore cannot report readings for another room, whatever it puts in the message.
- **The cloud sets the timestamp.** An ESP32 has no reliable clock. Using the broker's arrival time also lets the function discard messages that arrive late or twice.
- **One telemetry message type.** Periodic readings and events (motion, door) use the same message; an event is simply a message sent early. This keeps the contract between firmware and cloud to one schema.
- **Control through the Device Shadow only.** A command topic loses commands sent while the device is offline and needs its own acknowledgement protocol. The shadow provides both.
- **Rules live in the cloud, not in the firmware.** Changing a threshold or adding a rule is a Lambda deployment, not a re-flash of every board. The cost is that automation stops when the internet connection does.
- **Rules are pure functions.** They take a reading and the previous state and return a decision, with no AWS calls, so each rule is covered by a plain unit test.
- **Alerts are rate-limited with a conditional write.** A marker item in DynamoDB, removed by TTL, records that an alert was sent. The check and the write are one atomic operation, so two messages processed at the same time cannot both alert.
- **Three Lambda functions, one code bundle.** Telemetry, API and Alexa are separate functions with separate permissions and logs, built from the same source folder.
- **Grafana runs in Docker on your machine.** Amazon Managed Grafana is billed per user per month; the local container costs nothing and uses the same dashboard file. The dashboard is generated by [build_dashboard.py](tools/build_dashboard.py) so it can be reviewed as code.
- **An Alexa custom skill, without the Alexa SDK.** The request and response are plain JSON, so the function has no dependencies beyond what the Lambda runtime provides.

## Run the tests

Requires Python 3.12 or newer. No AWS account is needed.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
ruff check .
```

## Deploy

> **Cost.** Every service here is billed by use and nothing runs around the clock, but none of it is guaranteed free: IoT messages, Lambda invocations, custom CloudWatch metrics (charged per metric per month), alarms and SNS all have prices. Set an AWS budget alert before deploying, keep the 30-second reporting interval or raise it, and run `cdk destroy` when you are done.

Requires an AWS account with credentials configured, Node.js and the AWS CDK CLI.

```bash
cd infra
pip install -r requirements.txt
npx aws-cdk bootstrap
npx aws-cdk deploy -c rooms=livingroom -c alert_email=you@example.com
```

`rooms` is a comma-separated list; each room gets a "device offline" alarm. AWS sends a confirmation email to the alert address, and no alerts are delivered until you confirm it.

### Register a device

```bash
python tools/provision_device.py home01-livingroom
```

This creates the AWS IoT thing and its certificate, attaches the device policy and writes `firmware/include/secrets.h`. That file and the `certs/` folder hold the device's private key. Both are in `.gitignore`: never commit them.

### Try it without hardware

```bash
python tools/simulate_device.py home01-livingroom --interval 10
```

The simulator publishes random readings on the real telemetry topic, so everything downstream of AWS IoT Core behaves as it would with a board.

### Build the hardware

One ESP32 development board per room. Pins are set in [config.h](firmware/include/config.h):

| Part | Connection |
|---|---|
| BME280 (temperature, humidity) | I2C: SDA 21, SCL 22 |
| PIR motion sensor | GPIO 27 |
| Reed switch (door) | GPIO 26 to GND, internal pull-up |
| LDR voltage divider (light) | GPIO 34 (ADC1; ADC2 pins do not work with Wi-Fi on) |
| Light output | GPIO 25 |
| Fan output | GPIO 33 |

> **Safety.** Switch low-voltage loads only: an LED or a small 5 V fan. Do not wire this prototype to mains electricity. It is a demonstration, not a certified safety or alarm device.

Set `WIFI_SSID` and `WIFI_PASSWORD` in `firmware/include/secrets.h`, then:

```bash
cd firmware
pio run -t upload
pio device monitor
```

### Grafana

Attach the read-only policy from the stack output `GrafanaPolicyArn` to the IAM identity of an AWS profile on your machine, then:

```bash
cd grafana
docker compose up -d
```

Open http://localhost:3000 (first login `admin` / `admin`; Grafana asks for a new password). The "Smart home" dashboard shows the current values and the history per room.

### Alexa

1. In the [Alexa developer console](https://developer.amazon.com/alexa/console/ask), create a custom skill and copy its skill ID.
2. Redeploy with `-c alexa_skill_id=<skill ID>`. This allows that one skill, and no other, to call the function.
3. In the skill's JSON editor, paste [interaction-model.json](alexa/interaction-model.json). Set the endpoint to the stack output `AlexaFunctionArn`. Build the model.
4. Say: "Alexa, ask my little house what is the temperature in the living room", or "…to turn on the living room light".

A skill in development is usable only by the Amazon account that owns it.

### Watch app

Create a user in the Cognito user pool (stack output `UserPoolId`), put the stack outputs `ApiUrl` and `UserPoolClientId` in [watch/gradle.properties](watch/gradle.properties), open the `watch` folder in Android Studio and run the app on a Wear OS 3 or newer watch or emulator. It lists the rooms with their readings and has a switch for each light and fan.

The same HTTP API can be called from anything that holds a Cognito ID token:

| Request | Result |
|---|---|
| `GET /rooms` | All rooms with their latest readings |
| `GET /rooms/{room}` | One room |
| `POST /rooms/{room}/devices/{light or fan}` with `{"on": true}` | Requests a switch; returns `202` |

## Security

- **Devices:** one X.509 certificate each; the policy allows a device to connect only under its own name, publish only its own telemetry and use only its own shadow.
- **Transport:** TLS everywhere (MQTT on port 8883, HTTPS for the API).
- **API:** API Gateway rejects requests without a valid Cognito token before any code runs. Self sign-up is disabled.
- **Alexa:** the Lambda permission and the function itself both check the skill ID.
- **Functions:** each has its own role, limited to the state table, this house's shadows and, for telemetry only, the alert topic.
- **Secrets:** private keys and Wi-Fi credentials stay out of Git; Grafana gets read-only access.

## Known gaps

- **No offline buffering.** Readings taken while Wi-Fi or AWS is unreachable are lost. The device reconnects with exponential backoff and resumes.
- **No offline automation.** With the rules in the cloud, the light does not react to motion while the connection is down.
- **Manual and automatic control can disagree.** If you turn the light off while the room is dark and there is motion, the next telemetry message turns it on again.
- **State is written as a whole item.** A device command and a telemetry message processed at the same instant can overwrite each other's change to the room state. The shadow still holds the correct desired state.
- **The private key is in the firmware image.** A production device would keep it in a secure element or use encrypted flash, and certificates would be rotated.
- **No over-the-air firmware updates.**
- **The Alexa skill has no account linking**, so it cannot tell users apart. It serves the single house it is deployed for.
- **The watch app stores its refresh token unencrypted** in app-private storage, and asks for the password on the watch itself.
- **Alerts reach the watch through the phone's notification mirroring**, not through a push channel of the app's own.
- **One house per deployment.** The house ID is a setting of the stack.
- **No gas, smoke or energy sensors.** They were left out to keep the first version small.
