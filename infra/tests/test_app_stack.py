"""IAM isolation and routing in ``SundialApp`` (§12, §5.1)."""

from __future__ import annotations

import json
from typing import Any

from aws_cdk.assertions import Match, Template


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


def test_every_function_is_arm64_on_python_313(app_template: Template) -> None:
    functions = app_template.find_resources("AWS::Lambda::Function")
    assert len(functions) == 3
    for resource in functions.values():
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


def test_no_hostname_is_baked_in(app_template: Template) -> None:
    """The domain is deferred (§15.1); it arrives from SSM at deploy time."""
    body = json.dumps(app_template.to_json())
    assert "sundial.com" not in body
    assert "cloudfront" not in body.lower()
    app_template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": {
                "Variables": Match.object_like({"SUNDIAL_APP_BASE_URL": Match.any_value()})
            }
        },
    )
