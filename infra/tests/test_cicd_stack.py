"""``SundialCicd`` — what GitHub Actions is and is not allowed to do (§12, §13)."""

from __future__ import annotations

import json
from typing import Any

from aws_cdk.assertions import Template

REPO = "JoeMcMahon87/Sundial"


def _roles(template: Template) -> dict[str, Any]:
    return {
        resource["Properties"]["RoleName"]: resource
        for resource in template.find_resources("AWS::IAM::Role").values()
    }


def _logical_id(template: Template, role_name: str) -> str:
    for logical_id, resource in template.find_resources("AWS::IAM::Role").items():
        if resource["Properties"]["RoleName"] == role_name:
            return logical_id
    raise AssertionError(f"no role named {role_name}")


def _statements(template: Template, role_name: str) -> list[dict[str, Any]]:
    """Policies attach to a role by `Ref` to its logical id, not by its name,
    so matching on the name finds nothing and every assertion over the result
    passes vacuously. Empty is therefore treated as a failure, not as a pass."""
    logical_id = _logical_id(template, role_name)
    collected: list[dict[str, Any]] = []
    for policy in template.find_resources("AWS::IAM::Policy").values():
        if logical_id in json.dumps(policy["Properties"].get("Roles", [])):
            collected.extend(policy["Properties"]["PolicyDocument"]["Statement"])
    assert collected, f"no policy statements found for {role_name}"
    return collected


def _actions(statements: list[dict[str, Any]]) -> set[str]:
    actions: set[str] = set()
    for statement in statements:
        raw = statement.get("Action", [])
        actions.update([raw] if isinstance(raw, str) else raw)
    return actions


def test_a_role_exists_for_each_environment(cicd_template: Template) -> None:
    assert set(_roles(cicd_template)) == {"sundial-deploy-dev", "sundial-deploy-prod"}


def test_the_oidc_provider_is_referenced_never_created(cicd_template: Template) -> None:
    """The account already has one serving other repositories, and an issuer
    URL is unique per account — creating a second fails the deploy."""
    assert cicd_template.find_resources("AWS::IAM::OIDCProvider") == {}
    assert cicd_template.find_resources("Custom::AWSCDKOpenIdConnectProvider") == {}


def test_trust_is_scoped_to_this_repository_and_its_environments(
    cicd_template: Template,
) -> None:
    for name, role in _roles(cicd_template).items():
        env_name = name.removeprefix("sundial-deploy-")
        (statement,) = role["Properties"]["AssumeRolePolicyDocument"]["Statement"]
        condition = statement["Condition"]

        assert condition["StringEquals"]["token.actions.githubusercontent.com:aud"] == (
            "sts.amazonaws.com"
        )
        assert condition["StringLike"]["token.actions.githubusercontent.com:sub"] == [
            f"repo:{REPO}:environment:{env_name}",
            f"repo:{REPO}:environment:{env_name}-infra",
        ]


def test_no_role_trusts_another_repository(cicd_template: Template) -> None:
    """The account already holds a role trusting `repo:JoeMcMahon87/*:*`.
    Sundial's roles must not repeat that: a wildcard means any repository in
    the namespace, including one created tomorrow, can assume them."""
    body = json.dumps(cicd_template.to_json())
    assert f"repo:{REPO}:" in body
    assert "repo:JoeMcMahon87/*" not in body
    assert ":environment:*" not in body


def test_prod_cannot_be_reached_from_the_dev_environment(cicd_template: Template) -> None:
    prod = _roles(cicd_template)["sundial-deploy-prod"]
    subjects = prod["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]["Condition"][
        "StringLike"
    ]["token.actions.githubusercontent.com:sub"]
    assert not [s for s in subjects if "environment:dev" in s]


def test_deploy_happens_through_the_bootstrap_roles_not_directly(
    cicd_template: Template,
) -> None:
    """The role holds no service permissions of its own — it is only allowed to
    become the CDK bootstrap roles. Far narrower than attaching PowerUser."""
    for name in _roles(cicd_template):
        actions = _actions(_statements(cicd_template, name))

        assert "sts:AssumeRole" in actions
        assert not [
            a for a in actions if a.startswith(("iam:", "dynamodb:", "kms:", "lambda:"))
        ]
        assert "*" not in actions


def test_assumable_roles_are_only_the_cdk_bootstrap_roles(cicd_template: Template) -> None:
    for name in _roles(cicd_template):
        for statement in _statements(cicd_template, name):
            if "sts:AssumeRole" not in json.dumps(statement.get("Action")):
                continue
            for resource in statement["Resource"]:
                assert "cdk-hnb659fds-" in json.dumps(resource)


def test_the_site_bucket_grant_is_scoped_to_its_own_environment(
    cicd_template: Template,
) -> None:
    """Otherwise the dev pipeline could overwrite the production site."""
    for name in _roles(cicd_template):
        env_name = name.removeprefix("sundial-deploy-")
        other = "prod" if env_name == "dev" else "dev"
        for statement in _statements(cicd_template, name):
            if not statement.get("Action", []) or "s3:PutObject" not in json.dumps(
                statement["Action"]
            ):
                continue
            resources = json.dumps(statement["Resource"])
            assert f"sundial-site-{env_name}-" in resources
            assert f"sundial-site-{other}-" not in resources


def test_cloudformation_reads_are_scoped_to_sundial_stacks(cicd_template: Template) -> None:
    for name in _roles(cicd_template):
        for statement in _statements(cicd_template, name):
            if "cloudformation:DescribeStacks" not in json.dumps(statement.get("Action")):
                continue
            assert "stack/Sundial" in json.dumps(statement["Resource"])
