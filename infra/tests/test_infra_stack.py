"""What ``SundialInfra`` must never stop doing (§13, §3.2)."""

from __future__ import annotations

from aws_cdk.assertions import Match, Template


def test_nothing_durable_is_destroyed_with_the_stack(infra_template: Template) -> None:
    """§13: destroying the app stack must never take data with it — which only
    holds if the durable stack retains on its own account too."""
    for resource_type in (
        "AWS::DynamoDB::GlobalTable",
        "AWS::KMS::Key",
        "AWS::SecretsManager::Secret",
    ):
        for resource in infra_template.find_resources(resource_type).values():
            assert resource["DeletionPolicy"] == "Retain", resource_type
            assert resource["UpdateReplacePolicy"] == "Retain", resource_type


def test_table_has_both_indexes_and_pitr(infra_template: Template) -> None:
    infra_template.has_resource_properties(
        "AWS::DynamoDB::GlobalTable",
        {
            "TableName": "sundial-dev",
            "GlobalSecondaryIndexes": Match.array_with(
                [
                    Match.object_like({"IndexName": "GSI1"}),
                    Match.object_like({"IndexName": "GSI2"}),
                ]
            ),
            "TimeToLiveSpecification": {"AttributeName": "ttl", "Enabled": True},
        },
    )
    replicas = infra_template.find_resources("AWS::DynamoDB::GlobalTable")
    (table,) = replicas.values()
    (replica,) = table["Properties"]["Replicas"]
    assert replica["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"] is True


def test_streams_are_on_for_the_reminder_materializer(infra_template: Template) -> None:
    infra_template.has_resource_properties(
        "AWS::DynamoDB::GlobalTable",
        {"StreamSpecification": {"StreamViewType": "NEW_AND_OLD_IMAGES"}},
    )


def test_key_rotation_is_enabled(infra_template: Template) -> None:
    infra_template.has_resource_properties("AWS::KMS::Key", {"EnableKeyRotation": True})


def test_no_secret_material_is_in_the_template(infra_template: Template) -> None:
    """§12: no credentials in the repo, and a CloudFormation template is readable
    by anyone holding cloudformation:GetTemplate.

    CDK emits an empty ``GenerateSecretString`` by default, which is a
    generation *instruction* and carries no material; a populated
    ``SecretStringTemplate`` would.
    """
    secrets = infra_template.find_resources("AWS::SecretsManager::Secret")
    assert secrets
    for resource in secrets.values():
        properties = resource["Properties"]
        assert "SecretString" not in properties
        assert properties.get("GenerateSecretString", {}) == {}
