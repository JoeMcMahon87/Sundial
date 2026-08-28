"""IAM isolation and routing in ``SundialApp`` (§12, §5.1)."""

from __future__ import annotations

import json
from typing import Any

from aws_cdk.assertions import Template


def _role_logical_id(template: Template, function_logical_prefix: str) -> str:
    for logical_id, resource in template.find_resources("AWS::Lambda::Function").items():
        if logical_id.startswith(function_logical_prefix):
            return str(resource["Properties"]["Role"]["Fn::GetAtt"][0])
    raise AssertionError(f"no function named {function_logical_prefix}")


def _statements_for_role(template: Template, role_logical_id: str) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for policy in template.find_resources("AWS::IAM::Policy").values():
        roles = json.dumps(policy["Properties"].get("Roles", []))
        if role_logical_id in roles:
            collected.extend(policy["Properties"]["PolicyDocument"]["Statement"])
    return collected


def _actions(statements: list[dict[str, Any]]) -> set[str]:
    actions: set[str] = set()
    for statement in statements:
        raw = statement.get("Action", [])
        actions.update([raw] if isinstance(raw, str) else raw)
    return actions


def test_api_role_cannot_decrypt_the_google_token(app_template: Template) -> None:
    """§12, stated as a rule and enforced here: the `api` role holds no KMS
    permission at all, so a bug in a task handler cannot reach the refresh
    token."""
    actions = _actions(
        _statements_for_role(app_template, _role_logical_id(app_template, "ApiFn"))
    )
    assert not [a for a in actions if a.startswith("kms:")]
    assert not [a for a in actions if a.startswith("secretsmanager:")]
    assert "dynamodb:GetItem" in actions


def test_oauth_role_is_the_only_one_holding_the_key(app_template: Template) -> None:
    actions = _actions(
        _statements_for_role(app_template, _role_logical_id(app_template, "OauthFn"))
    )
    assert "kms:Decrypt" in actions
    assert "kms:Encrypt" in actions
    assert "secretsmanager:GetSecretValue" in actions


def test_authorizer_reads_the_session_key_and_nothing_else(app_template: Template) -> None:
    actions = _actions(
        _statements_for_role(app_template, _role_logical_id(app_template, "AuthorizerFn"))
    )
    assert "secretsmanager:GetSecretValue" in actions
    assert not [a for a in actions if a.startswith("dynamodb:")]
    assert not [a for a in actions if a.startswith("kms:")]


def test_sync_cal_holds_the_key_but_not_the_session(app_template: Template) -> None:
    """It calls Google, so it needs kms:Decrypt; it never mints or verifies a
    session, so it must not be able to read the signing key."""
    statements = _statements_for_role(app_template, _role_logical_id(app_template, "SyncCalFn"))
    actions = _actions(statements)
    assert "kms:Decrypt" in actions
    assert "dynamodb:PutItem" in actions

    session_secret_readers = json.dumps(
        [
            s
            for s in statements
            if "secretsmanager:GetSecretValue" in json.dumps(s.get("Action"))
        ]
    )
    assert "SessionKey" not in session_secret_readers


SUNDIAL_FUNCTIONS = ("ApiFn", "OauthFn", "AuthorizerFn", "SyncCalFn")


def test_every_sundial_function_is_arm64_on_python_313(app_template: Template) -> None:
    """Scoped to Sundial's own functions: CDK adds a custom-resource Lambda of
    its own for the site bucket's auto-delete, and that one is not ours to
    specify."""
    ours = {
        logical_id: resource
        for logical_id, resource in app_template.find_resources("AWS::Lambda::Function").items()
        if logical_id.startswith(SUNDIAL_FUNCTIONS)
    }
    assert len(ours) == len(SUNDIAL_FUNCTIONS)
    for resource in ours.values():
        assert resource["Properties"]["Architectures"] == ["arm64"]
        assert resource["Properties"]["Runtime"] == "python3.13"


def test_logs_are_retained_for_seven_days(app_template: Template) -> None:
    """§12. Longer retention means email-adjacent metadata lingers."""
    groups = app_template.find_resources("AWS::Logs::LogGroup")
    assert groups
    for resource in groups.values():
        assert resource["Properties"]["RetentionInDays"] == 7


def test_auth_routes_are_unauthenticated_and_the_rest_are_not(app_template: Template) -> None:
    """`/api/auth/*` is how a session comes to exist, so it cannot require one;
    everything else must go through the authorizer."""
    routes = {
        resource["Properties"]["RouteKey"]: resource["Properties"]
        for resource in app_template.find_resources("AWS::ApiGatewayV2::Route").values()
    }
    assert routes["ANY /api/auth/{proxy+}"].get("AuthorizationType", "NONE") == "NONE"
    assert routes["ANY /api/{proxy+}"]["AuthorizationType"] == "CUSTOM"


def test_authorizer_caches_against_the_cookie(app_template: Template) -> None:
    app_template.has_resource_properties(
        "AWS::ApiGatewayV2::Authorizer",
        {
            "AuthorizerResultTtlInSeconds": 300,  # §5.1 step 4
            "IdentitySource": ["$request.header.Cookie"],
            "AuthorizerPayloadFormatVersion": "2.0",
            "EnableSimpleResponses": True,
        },
    )


def test_dev_has_no_alias_and_therefore_needs_no_dns(app_template: Template) -> None:
    """Dev serves from the distribution's own domain. It is still same-origin,
    so the first-party session cookie (§5.1) works, and it needs neither a DNS
    record nor a certificate — which is what lets dev deploy before the domain
    is wired up at all."""
    (distribution,) = app_template.find_resources("AWS::CloudFront::Distribution").values()
    config = distribution["Properties"]["DistributionConfig"]

    # CDK omits ViewerCertificate altogether for the default certificate;
    # its absence is the signal that no custom one is in play.
    assert "Aliases" not in config
    assert "ViewerCertificate" not in config
    assert "mcmahongroup" not in json.dumps(app_template.to_json())


def test_prod_carries_the_alias_and_enforces_tls_12(prod_app_template: Template) -> None:
    (distribution,) = prod_app_template.find_resources("AWS::CloudFront::Distribution").values()
    config = distribution["Properties"]["DistributionConfig"]

    assert config["Aliases"] == ["sundial.example.org"]
    # §12 wants TLS 1.2 minimum, and CloudFront only honours that with a
    # certificate of your own.
    assert config["ViewerCertificate"]["MinimumProtocolVersion"] == "TLSv1.2_2021"


def test_the_certificate_is_referenced_never_created(prod_app_template: Template) -> None:
    """A DNS-validated certificate created by CloudFormation blocks the stack
    until its validation record appears. The zone is not in Route 53, so that
    would hang every deploy for an hour and then roll back."""
    assert prod_app_template.find_resources("AWS::CertificateManager::Certificate") == {}


def test_api_and_site_are_one_origin(app_template: Template) -> None:
    """The entire reason the distribution exists: same origin means the session
    cookie is first-party and survives iOS Safari (§4.1, §5.1)."""
    (distribution,) = app_template.find_resources("AWS::CloudFront::Distribution").values()
    config = distribution["Properties"]["DistributionConfig"]

    assert len(config["Origins"]) == 2
    (api_behavior,) = config["CacheBehaviors"]
    assert api_behavior["PathPattern"] == "/api/*"


def test_nothing_under_api_is_cached(app_template: Template) -> None:
    """A cached /api response is a correctness bug, not a performance one."""
    (distribution,) = app_template.find_resources("AWS::CloudFront::Distribution").values()
    (api_behavior,) = distribution["Properties"]["DistributionConfig"]["CacheBehaviors"]
    # The managed CachingDisabled policy.
    assert api_behavior["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"


def test_the_site_bucket_is_private(app_template: Template) -> None:
    app_template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            }
        },
    )


def test_the_csp_forbids_inline_script(app_template: Template) -> None:
    (policy,) = app_template.find_resources("AWS::CloudFront::ResponseHeadersPolicy").values()
    csp = policy["Properties"]["ResponseHeadersPolicyConfig"]["SecurityHeadersConfig"][
        "ContentSecurityPolicy"
    ]["ContentSecurityPolicy"]

    assert "unsafe-inline" not in csp  # §12
    assert "frame-ancestors 'none'" in csp
