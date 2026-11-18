"""AWS infrastructure for the smart home, as one CDK stack.

Devices    ESP32 -> MQTT over TLS -> AWS IoT Core (one certificate per device)
Telemetry  IoT rule -> Lambda -> DynamoDB (latest state), CloudWatch (history),
           SNS (alerts), Device Shadow (automatic device control)
Control    Alexa skill / watch app -> Lambda -> Device Shadow -> ESP32
Monitoring Grafana reads CloudWatch; CloudWatch alarms report silent devices
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_authorizers as authorizers
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cloudwatch_actions
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_iot as iot
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subscriptions
from constructs import Construct

CLOUD_SOURCE = str(Path(__file__).resolve().parent.parent / "cloud" / "src")
DEVICE_POLICY_NAME = "SmartHomeDevice"
METRIC_NAMESPACE = "SmartHome"  # must match smarthome/metrics.py
THING = "${iot:Connection.Thing.ThingName}"  # resolved by AWS IoT for each connection


class SmartHomeStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        house: str = "home01",
        rooms: tuple[str, ...] = ("livingroom",),
        alert_email: str | None = None,
        alexa_skill_id: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- State and alerts ----------------------------------------------
        state = dynamodb.Table(
            self,
            "State",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.DESTROY,
        )
        alerts = sns.Topic(self, "Alerts", enforce_ssl=True)
        if alert_email:
            alerts.add_subscription(subscriptions.EmailSubscription(alert_email))

        # --- Lambda functions (one code bundle, three entry points) ---------
        environment = {
            "STATE_TABLE": state.table_name,
            "ALERT_TOPIC_ARN": alerts.topic_arn,
            "HOUSE_ID": house,
        }
        code = lambda_.Code.from_asset(CLOUD_SOURCE, exclude=["**/__pycache__", "*.egg-info"])
        house_shadows = iam.PolicyStatement(
            actions=["iot:GetThingShadow", "iot:UpdateThingShadow"],
            resources=[f"arn:aws:iot:{self.region}:{self.account}:thing/{house}-*"],
        )

        def function(name: str, module: str, **extra) -> lambda_.Function:
            fn = lambda_.Function(
                self,
                name,
                runtime=lambda_.Runtime.PYTHON_3_12,
                code=code,
                handler=f"smarthome.handlers.{module}.handler",
                memory_size=256,
                timeout=Duration.seconds(10),
                log_group=logs.LogGroup(
                    self,
                    f"{name}Logs",
                    retention=logs.RetentionDays.ONE_MONTH,
                    removal_policy=RemovalPolicy.DESTROY,
                ),
                **extra,
            )
            state.grant_read_write_data(fn)
            fn.add_to_role_policy(house_shadows)
            return fn

        telemetry_fn = function(
            "Telemetry",
            "telemetry",
            environment=environment,
            # The IoT rule invokes the function asynchronously: Lambda retries a
            # failed message twice, then parks it in this queue instead of losing it.
            dead_letter_queue_enabled=True,
        )
        alerts.grant_publish(telemetry_fn)
        api_fn = function("ApiHandler", "api", environment=environment)
        alexa_fn = function(
            "Alexa",
            "alexa",
            environment={**environment, "ALEXA_SKILL_ID": alexa_skill_id or ""},
        )
        if alexa_skill_id:
            # Only this one Alexa skill may invoke the function.
            alexa_fn.add_permission(
                "AlexaSkill",
                principal=iam.ServicePrincipal("alexa-appkit.amazon.com"),
                event_source_token=alexa_skill_id,
            )

        # --- AWS IoT: device policy and telemetry rule ----------------------
        iot_arn = f"arn:aws:iot:{self.region}:{self.account}"
        shadow = f"$aws/things/{THING}/shadow"
        iot.CfnPolicy(
            self,
            "DevicePolicy",
            policy_name=DEVICE_POLICY_NAME,
            # Least privilege: a device may connect only under its own thing name,
            # publish only its own telemetry and use only its own shadow.
            policy_document={
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "iot:Connect",
                        "Resource": f"{iot_arn}:client/{THING}",
                    },
                    {
                        "Effect": "Allow",
                        "Action": "iot:Publish",
                        "Resource": [
                            f"{iot_arn}:topic/smarthome/{THING}/telemetry",
                            f"{iot_arn}:topic/{shadow}/update",
                            f"{iot_arn}:topic/{shadow}/get",
                        ],
                    },
                    {
                        "Effect": "Allow",
                        "Action": "iot:Subscribe",
                        "Resource": [
                            f"{iot_arn}:topicfilter/{shadow}/update/delta",
                            f"{iot_arn}:topicfilter/{shadow}/get/accepted",
                        ],
                    },
                    {
                        "Effect": "Allow",
                        "Action": "iot:Receive",
                        "Resource": [
                            f"{iot_arn}:topic/{shadow}/update/delta",
                            f"{iot_arn}:topic/{shadow}/get/accepted",
                        ],
                    },
                ],
            },
        )

        rule_errors = logs.LogGroup(
            self,
            "RuleErrors",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        rule_role = iam.Role(
            self, "RuleErrorsRole", assumed_by=iam.ServicePrincipal("iot.amazonaws.com")
        )
        rule_errors.grant_write(rule_role)
        rule = iot.CfnTopicRule(
            self,
            "TelemetryRule",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                # Only named fields are forwarded. thing_name comes from the topic
                # (which the device policy restricts) and received_at from the
                # broker's clock, so neither can be forged in the payload.
                sql=(
                    "SELECT sensors, fw, topic(2) AS thing_name, timestamp() AS received_at "
                    "FROM 'smarthome/+/telemetry'"
                ),
                aws_iot_sql_version="2016-03-23",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        lambda_=iot.CfnTopicRule.LambdaActionProperty(
                            function_arn=telemetry_fn.function_arn
                        )
                    )
                ],
                error_action=iot.CfnTopicRule.ActionProperty(
                    cloudwatch_logs=iot.CfnTopicRule.CloudwatchLogsActionProperty(
                        log_group_name=rule_errors.log_group_name, role_arn=rule_role.role_arn
                    )
                ),
            ),
        )
        telemetry_fn.add_permission(
            "IotRule",
            principal=iam.ServicePrincipal("iot.amazonaws.com"),
            source_arn=rule.attr_arn,
        )

        # --- Monitoring: alarms ---------------------------------------------
        notify = cloudwatch_actions.SnsAction(alerts)
        for room in rooms:
            # No temperature sample for ten minutes means the device is offline.
            offline = cloudwatch.Alarm(
                self,
                f"Offline{room.capitalize()}",
                alarm_description=f"No telemetry from the {room} device",
                metric=cloudwatch.Metric(
                    namespace=METRIC_NAMESPACE,
                    metric_name="Temperature",
                    dimensions_map={"House": house, "Room": room},
                    statistic="SampleCount",
                    period=Duration.minutes(5),
                ),
                threshold=1,
                comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
                evaluation_periods=2,
                treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            )
            offline.add_alarm_action(notify)
            offline.add_ok_action(notify)

        failures = cloudwatch.Alarm(
            self,
            "TelemetryErrors",
            alarm_description="The telemetry function is failing",
            metric=telemetry_fn.metric_errors(period=Duration.minutes(5)),
            threshold=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        failures.add_alarm_action(notify)

        # --- Grafana: read-only access to the metrics and logs ---------------
        grafana_policy = iam.ManagedPolicy(
            self,
            "GrafanaReadOnly",
            description="Read-only CloudWatch access for the Grafana data source",
            statements=[
                iam.PolicyStatement(
                    actions=[
                        "cloudwatch:DescribeAlarms",
                        "cloudwatch:DescribeAlarmsForMetric",
                        "cloudwatch:GetMetricData",
                        "cloudwatch:GetMetricStatistics",
                        "cloudwatch:ListMetrics",
                        "ec2:DescribeRegions",
                        "logs:DescribeLogGroups",
                        "logs:GetLogEvents",
                        "logs:GetLogGroupFields",
                        "logs:GetQueryResults",
                        "logs:StartQuery",
                        "logs:StopQuery",
                        "tag:GetResources",
                    ],
                    resources=["*"],
                )
            ],
        )

        # --- HTTP API for the watch app, protected by Cognito ---------------
        users = cognito.UserPool(
            self,
            "Users",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            removal_policy=RemovalPolicy.DESTROY,
        )
        app_client = users.add_client(
            "WatchClient", auth_flows=cognito.AuthFlow(user_password=True, user_srp=True)
        )
        api = apigwv2.HttpApi(self, "Api")
        integration = integrations.HttpLambdaIntegration("ApiIntegration", api_fn)
        authorizer = authorizers.HttpUserPoolAuthorizer(
            "CognitoAuthorizer", users, user_pool_clients=[app_client]
        )
        for method, path in (
            (apigwv2.HttpMethod.GET, "/rooms"),
            (apigwv2.HttpMethod.GET, "/rooms/{room}"),
            (apigwv2.HttpMethod.POST, "/rooms/{room}/devices/{device}"),
        ):
            api.add_routes(
                path=path, methods=[method], integration=integration, authorizer=authorizer
            )

        CfnOutput(self, "ApiUrl", value=api.api_endpoint)
        CfnOutput(self, "UserPoolId", value=users.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=app_client.user_pool_client_id)
        CfnOutput(self, "AlexaFunctionArn", value=alexa_fn.function_arn)
        CfnOutput(self, "DevicePolicyName", value=DEVICE_POLICY_NAME)
        CfnOutput(self, "GrafanaPolicyArn", value=grafana_policy.managed_policy_arn)
        CfnOutput(self, "AlertsTopicArn", value=alerts.topic_arn)
