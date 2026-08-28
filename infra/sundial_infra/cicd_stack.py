"""``SundialCicd`` — the roles GitHub Actions assumes (§13).

Deployed **once, by hand**, from an admin session. It cannot be deployed by the
pipeline it enables, and putting it in CDK rather than in console clicks is
what makes the trust policy reviewable.

The account already has a GitHub OIDC provider serving other repositories, so
this stack references it. Creating a second one fails: an issuer URL is unique
per account.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_iam as iam
from constructs import Construct

from sundial_infra.naming import ENVIRONMENTS

GITHUB_ISSUER = "token.actions.githubusercontent.com"
BOOTSTRAP_QUALIFIER = "hnb659fds"


class SundialCicd(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        repository: str,
        **kwargs: Any,
    ):
        super().__init__(scope, construct_id, **kwargs)

        provider = iam.OpenIdConnectProvider.from_open_id_connect_provider_arn(
            self,
            "GitHubOidc",
            f"arn:aws:iam::{self.account}:oidc-provider/{GITHUB_ISSUER}",
        )

        for env_name in ENVIRONMENTS:
            role = iam.Role(
                self,
                f"Deploy{env_name.title()}",
                role_name=f"sundial-deploy-{env_name}",
                description=f"GitHub Actions deploys Sundial {env_name}",
                assumed_by=iam.WebIdentityPrincipal(
                    provider.open_id_connect_provider_arn,
                    {
                        "StringEquals": {f"{GITHUB_ISSUER}:aud": "sts.amazonaws.com"},
                        "StringLike": {f"{GITHUB_ISSUER}:sub": _subjects(repository, env_name)},
                    },
                ),
            )

            # Everything CloudFormation-shaped goes through the CDK bootstrap
            # roles, so the deploy role itself needs no service permissions at
            # all — it only needs to be allowed to become them. This is far
            # narrower than the usual "attach PowerUserAccess and hope".
            role.add_to_policy(
                iam.PolicyStatement(
                    actions=["sts:AssumeRole"],
                    resources=[
                        f"arn:aws:iam::{self.account}:role/cdk-{BOOTSTRAP_QUALIFIER}-{purpose}-"
                        f"role-{self.account}-{self.region}"
                        for purpose in (
                            "deploy",
                            "file-publishing",
                            "image-publishing",
                            "lookup",
                        )
                    ],
                )
            )

            # Reading stack outputs is how the workflow learns the site bucket
            # and distribution id without them being hardcoded.
            role.add_to_policy(
                iam.PolicyStatement(
                    actions=["cloudformation:DescribeStacks"],
                    resources=[
                        f"arn:aws:cloudformation:{self.region}:{self.account}:stack/"
                        f"Sundial*-{env_name}/*"
                    ],
                )
            )

            # The static site is synced directly rather than through
            # CloudFormation: a BucketDeployment would round-trip every asset
            # through a Lambda for no benefit.
            role.add_to_policy(
                iam.PolicyStatement(
                    actions=[
                        "s3:PutObject",
                        "s3:DeleteObject",
                        "s3:ListBucket",
                        "s3:GetObject",
                    ],
                    resources=[
                        f"arn:aws:s3:::sundial-site-{env_name}-{self.account}",
                        f"arn:aws:s3:::sundial-site-{env_name}-{self.account}/*",
                    ],
                )
            )

            role.add_to_policy(
                iam.PolicyStatement(
                    actions=["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"],
                    resources=[f"arn:aws:cloudfront::{self.account}:distribution/*"],
                )
            )

            CfnOutput(self, f"Deploy{env_name.title()}RoleArn", value=role.role_arn)


def _subjects(repository: str, env_name: str) -> list[str]:
    """Which GitHub tokens may assume this role.

    Bound to **GitHub Environments**, not to a branch. That is the stronger
    gate: a token carrying `environment:prod` is only issued after that
    environment's required reviewers have approved the run, so the approval is
    enforced by AWS at `sts:AssumeRoleWithWebIdentity` rather than only by
    GitHub's UI. Removing the reviewer requirement is then not enough on its
    own to reach the account.

    Two subjects per environment, because the durable stack deploys from a
    separately-gated `<env>-infra` environment. They are listed explicitly
    rather than matched with `dev*`, so a newly created environment does not
    silently inherit deploy rights.
    """
    return [
        f"repo:{repository}:environment:{env_name}",
        f"repo:{repository}:environment:{env_name}-infra",
    ]
