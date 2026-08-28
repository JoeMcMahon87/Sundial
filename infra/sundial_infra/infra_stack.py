"""``SundialInfra`` — the durable half (§13).

Destroying ``SundialApp`` must never take data with it, which is the entire
reason these live in a separate stack with ``RemovalPolicy.RETAIN``.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_kms as kms
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from sundial_infra.naming import secret_name, suffix


class SundialInfra(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, env_name: str, **kwargs: Any):
        super().__init__(scope, construct_id, **kwargs)
        self.env_name = env_name

        # The CMK protects the Google refresh token and nothing else (§5.4).
        # The table is deliberately *not* encrypted with it: sharing the key
        # would force every table reader to hold `kms:Decrypt`, which is exactly
        # the isolation §12 asks for ("the api role cannot decrypt the Google
        # secret at all").
        self.key = kms.Key(
            self,
            "GoogleTokenKey",
            alias=f"alias/{suffix(env_name)}-google-token",
            description="Encrypts the Google refresh token stored in AUTH#google",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        self.table = dynamodb.TableV2(
            self,
            "Table",
            table_name=suffix(env_name),
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing=dynamodb.Billing.on_demand(),
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            dynamo_stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            removal_policy=RemovalPolicy.RETAIN,
            global_secondary_indexes=[
                # GSI1 — "do I already know this Google event?", asked on every
                # inbound sync page (§3.2).
                dynamodb.GlobalSecondaryIndexPropsV2(
                    index_name="GSI1",
                    partition_key=dynamodb.Attribute(
                        name="GSI1PK", type=dynamodb.AttributeType.STRING
                    ),
                    sort_key=dynamodb.Attribute(
                        name="GSI1SK", type=dynamodb.AttributeType.STRING
                    ),
                ),
                # GSI2 — task work queues, so the scheduler never scans (§3.2).
                dynamodb.GlobalSecondaryIndexPropsV2(
                    index_name="GSI2",
                    partition_key=dynamodb.Attribute(
                        name="GSI2PK", type=dynamodb.AttributeType.STRING
                    ),
                    sort_key=dynamodb.Attribute(
                        name="GSI2SK", type=dynamodb.AttributeType.STRING
                    ),
                ),
            ],
            time_to_live_attribute="ttl",
        )

        # Two of §12's four secrets. The Anthropic API key (M5) and the VAPID
        # private key (M4) land with the milestones that need them; adding a
        # secret to this stack later is a no-op for the data in it.
        self.session_key = self._placeholder_secret(
            "SessionKey",
            secret_name(env_name, "session-key"),
            "RS256 keypair for the Sundial session JWT (§5.1)",
        )
        self.google_client_secret = self._placeholder_secret(
            "GoogleClientSecret",
            secret_name(env_name, "google-client"),
            "Google OAuth client secret for this environment (§13)",
        )

        CfnOutput(self, "TableName", value=self.table.table_name)
        CfnOutput(self, "KeyArn", value=self.key.key_arn)

    def _placeholder_secret(
        self, construct_id: str, name: str, description: str
    ) -> secretsmanager.Secret:
        """Created empty on purpose.

        The real material is written out of band by the runbook — an RSA
        keypair and a Google client secret both have to come from somewhere
        other than a CloudFormation template, which is world-readable to anyone
        with `cloudformation:GetTemplate` (§12: no credentials in the repo).
        """
        return secretsmanager.Secret(
            self,
            construct_id,
            secret_name=name,
            description=description,
            removal_policy=RemovalPolicy.RETAIN,
        )
