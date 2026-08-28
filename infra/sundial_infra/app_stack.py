"""``SundialApp`` — the replaceable half (§13).

Functions, the HTTP API, and the authorizer. CloudFront and S3 arrive with M0b,
which is blocked on the domain (§15.1).
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_authorizers as authorizers
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from sundial_infra.naming import param

LAMBDA_ASSET = "../backend/dist/lambda"
"""Built by ``backend/build.sh`` before synth; see infra/README.md."""


class SundialApp(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        table: dynamodb.ITableV2,
        key: kms.IKey,
        session_key: secretsmanager.ISecret,
        google_client_secret: secretsmanager.ISecret,
        **kwargs: Any,
    ):
        super().__init__(scope, construct_id, **kwargs)

        # Deploy-time SSM lookups, not synth-time: `cdk synth` has to work in CI
        # without AWS credentials (§13).
        google_client_id = ssm.StringParameter.value_for_string_parameter(
            self, param(env_name, "google-client-id")
        )
        allowed_account_id = ssm.StringParameter.value_for_string_parameter(
            self, param(env_name, "allowed-google-account-id")
        )
        app_base_url = ssm.StringParameter.value_for_string_parameter(
            self, param(env_name, "app-base-url")
        )

        code = lambda_.Code.from_asset(LAMBDA_ASSET)
        common_env = {
            "SUNDIAL_ENV": env_name,
            "SUNDIAL_TABLE_NAME": table.table_name,
            "SUNDIAL_KMS_KEY_ID": key.key_arn,
            "SUNDIAL_SESSION_KEY_SECRET_ARN": session_key.secret_arn,
            "SUNDIAL_APP_BASE_URL": app_base_url,
        }

        def function(construct_id: str, handler: str, **extra_env: str) -> lambda_.Function:
            # An explicit log group, so the 7-day retention (§12) is a property
            # of the group rather than of a custom resource that runs after the
            # first invocation has already written at the default forever.
            log_group = logs.LogGroup(
                self,
                f"{construct_id}Logs",
                retention=logs.RetentionDays.ONE_WEEK,
                removal_policy=RemovalPolicy.DESTROY,
            )
            return lambda_.Function(
                self,
                construct_id,
                runtime=lambda_.Runtime.PYTHON_3_13,
                architecture=lambda_.Architecture.ARM_64,
                code=code,
                handler=handler,
                memory_size=512,
                timeout=Duration.seconds(29),  # the HTTP API's own ceiling
                environment={**common_env, **extra_env},
                log_group=log_group,
            )

        # The authorizer holds the session secret because verifying the JWT is
        # its whole job.
        self.authorizer_fn = function("AuthorizerFn", "sundial.api.authorizer.handler")
        session_key.grant_read(self.authorizer_fn)

        # `api` serves everything except /auth/*. It can read and write the
        # table, but it holds no Decrypt on the CMK and cannot read the Google
        # client secret — §12's "the api role cannot decrypt the Google secret
        # at all", enforced rather than asserted.
        self.api_fn = function("ApiFn", "sundial.api.handler.handler")
        table.grant_read_write_data(self.api_fn)

        # `oauth` runs the authorization-code flow and the refresh, so it is the
        # one function that needs the client secret and the key.
        self.oauth_fn = function(
            "OauthFn",
            "sundial.api.handler.handler",
            SUNDIAL_GOOGLE_CLIENT_ID=google_client_id,
            SUNDIAL_GOOGLE_CLIENT_SECRET_ARN=google_client_secret.secret_arn,
            SUNDIAL_ALLOWED_GOOGLE_ACCOUNT_ID=allowed_account_id,
            SUNDIAL_GOOGLE_REDIRECT_URI=f"{app_base_url}/api/auth/callback",
        )
        table.grant_read_write_data(self.oauth_fn)
        session_key.grant_read(self.oauth_fn)
        google_client_secret.grant_read(self.oauth_fn)
        key.grant_encrypt_decrypt(self.oauth_fn)

        authorizer = authorizers.HttpLambdaAuthorizer(
            "SessionAuthorizer",
            self.authorizer_fn,
            response_types=[authorizers.HttpLambdaResponseType.SIMPLE],
            identity_source=["$request.header.Cookie"],
            results_cache_ttl=Duration.seconds(300),  # §5.1 step 4
        )

        self.http_api = apigw.HttpApi(self, "HttpApi", create_default_stage=True)

        # /api/auth/* is unauthenticated: it is how a session comes to exist.
        self.http_api.add_routes(
            path="/api/auth/{proxy+}",
            methods=[apigw.HttpMethod.ANY],
            integration=integrations.HttpLambdaIntegration("OauthIntegration", self.oauth_fn),
        )
        self.http_api.add_routes(
            path="/api/{proxy+}",
            methods=[apigw.HttpMethod.ANY],
            integration=integrations.HttpLambdaIntegration("ApiIntegration", self.api_fn),
            authorizer=authorizer,
        )

        CfnOutput(self, "ApiEndpoint", value=self.http_api.api_endpoint)
