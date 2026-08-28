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
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from sundial_infra.naming import param, site_bucket

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

        # `sync_cal` calls Google, so it needs the refresh token — which makes
        # it the second and last function holding kms:Decrypt (§12). It gets no
        # session key: it never mints or verifies a session.
        self.sync_cal_fn = function(
            "SyncCalFn",
            "sundial.sync_cal.handler.handler",
            SUNDIAL_GOOGLE_CLIENT_ID=google_client_id,
            SUNDIAL_GOOGLE_CLIENT_SECRET_ARN=google_client_secret.secret_arn,
            SUNDIAL_ALLOWED_GOOGLE_ACCOUNT_ID=allowed_account_id,
        )
        table.grant_read_write_data(self.sync_cal_fn)
        google_client_secret.grant_read(self.sync_cal_fn)
        key.grant_encrypt_decrypt(self.sync_cal_fn)

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

        self.sync_cal_fn.add_environment("SUNDIAL_LOG_LEVEL", "INFO")

        self._add_distribution(env_name)

        CfnOutput(self, "ApiEndpoint", value=self.http_api.api_endpoint)
        CfnOutput(self, "SyncCalFunctionName", value=self.sync_cal_fn.function_name)
        CfnOutput(self, "SiteBucketName", value=self.site_bucket.bucket_name)
        CfnOutput(self, "DistributionId", value=self.distribution.distribution_id)
        CfnOutput(self, "SiteUrl", value=f"https://{self.site_host}")

    def _add_distribution(self, env_name: str) -> None:
        """CloudFront in front of both origins (§4.1).

        Serving the SPA and `/api/*` from one origin is what makes the session
        cookie first-party, which is what makes it survive iOS Safari (§5.1).
        It is the whole reason this distribution exists.
        """
        self.site_bucket = s3.Bucket(
            self,
            "SiteBucket",
            bucket_name=site_bucket(env_name, self.account),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            # The bucket holds a build artefact and nothing else. Losing it
            # costs one `npm run build`, so it is not durable infrastructure.
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # Prod carries the custom hostname; dev uses the distribution's own
        # domain. Dev is same-origin either way, so it needs no DNS and no
        # certificate — which is what lets it deploy before any DNS exists.
        domain_name = self.node.try_get_context("domain_name")
        certificate_arn = self.node.try_get_context("certificate_arn")
        aliased = bool(domain_name and certificate_arn)

        certificate = (
            acm.Certificate.from_certificate_arn(self, "SiteCertificate", certificate_arn)
            if aliased
            else None
        )

        headers = cloudfront.ResponseHeadersPolicy(
            self,
            "SecurityHeaders",
            security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                content_security_policy=cloudfront.ResponseHeadersContentSecurityPolicy(
                    # No `unsafe-inline` (§12). `connect-src` is same-origin
                    # because the API is served from this very distribution.
                    content_security_policy=(
                        "default-src 'self'; img-src 'self' data:; "
                        "style-src 'self'; script-src 'self'; connect-src 'self'; "
                        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
                    ),
                    override=True,
                ),
                content_type_options=cloudfront.ResponseHeadersContentTypeOptions(
                    override=True
                ),
                frame_options=cloudfront.ResponseHeadersFrameOptions(
                    frame_option=cloudfront.HeadersFrameOption.DENY, override=True
                ),
                referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                    referrer_policy=cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                    override=True,
                ),
                strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(365),
                    include_subdomains=True,
                    override=True,
                ),
            ),
        )

        api_origin = origins.HttpOrigin(
            f"{self.http_api.http_api_id}.execute-api.{self.region}.amazonaws.com",
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
        )

        self.distribution = cloudfront.Distribution(
            self,
            "Distribution",
            comment=f"Sundial {env_name}",
            default_root_object="index.html",
            # §12 wants TLS 1.2 minimum, and CloudFront only honours that on a
            # distribution with its own certificate — the default *.cloudfront.net
            # certificate has a fixed policy. So dev, which has no alias, cannot
            # enforce it. That is acceptable for dev and must not be for prod.
            minimum_protocol_version=(
                cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021 if aliased else None
            ),
            domain_names=[domain_name] if aliased else None,
            certificate=certificate,
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(self.site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                response_headers_policy=headers,
            ),
            additional_behaviors={
                "/api/*": cloudfront.BehaviorOptions(
                    origin=api_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    # Nothing under /api is cacheable, and the whole design
                    # depends on cookies reaching the origin intact.
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                    response_headers_policy=headers,
                ),
            },
            error_responses=[
                # The SPA owns routing; a deep link must not 403 from S3.
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(5),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(5),
                ),
            ],
        )

        self.site_host = domain_name if aliased else self.distribution.distribution_domain_name
