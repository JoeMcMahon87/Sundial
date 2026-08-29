#!/usr/bin/env python3
"""Sundial CDK application.

One AWS account, two environments (§16 decision 3). Which environment is
synthesised comes from the ``env`` context value: ``cdk synth -c env=prod``.
"""

from __future__ import annotations

import aws_cdk as cdk

from sundial_infra.app_stack import SundialApp
from sundial_infra.cicd_stack import SundialCicd
from sundial_infra.infra_stack import SundialInfra
from sundial_infra.naming import ENVIRONMENTS

app = cdk.App()

env_name = app.node.try_get_context("env") or "dev"
if env_name not in ENVIRONMENTS:
    raise SystemExit(f"env must be one of {ENVIRONMENTS}, got {env_name!r}")

env = cdk.Environment(
    account=app.node.try_get_context("account"),
    region=app.node.try_get_context("region") or "us-east-1",
)

# Deployed once, by hand, from an admin session — it cannot be deployed by
# the pipeline whose roles it creates.
SundialCicd(
    app,
    "SundialCicd",
    repository=app.node.try_get_context("repository") or "JoeMcMahon87/Sundial",
    repository_owner_id=app.node.try_get_context("repository_owner_id") or "14055195",
    repository_id=app.node.try_get_context("repository_id") or "1339893752",
    env=env,
)

infra = SundialInfra(app, f"SundialInfra-{env_name}", env_name=env_name, env=env)
SundialApp(
    app,
    f"SundialApp-{env_name}",
    env_name=env_name,
    table=infra.table,
    key=infra.key,
    session_key=infra.session_key,
    google_client_secret=infra.google_client_secret,
    env=env,
)

app.synth()
