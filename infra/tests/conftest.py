from __future__ import annotations

import pytest
from aws_cdk import App, Environment
from aws_cdk.assertions import Template

from sundial_infra.app_stack import SundialApp
from sundial_infra.cicd_stack import SundialCicd
from sundial_infra.infra_stack import SundialInfra


@pytest.fixture(scope="session")
def stacks() -> tuple[SundialInfra, SundialApp]:
    app = App()
    env = Environment(account="111122223333", region="us-east-1")
    infra = SundialInfra(app, "SundialInfra-dev", env_name="dev", env=env)
    application = SundialApp(
        app,
        "SundialApp-dev",
        env_name="dev",
        table=infra.table,
        key=infra.key,
        session_key=infra.session_key,
        google_client_secret=infra.google_client_secret,
        env=env,
    )
    return infra, application


@pytest.fixture(scope="session")
def cicd_template() -> Template:
    app = App()
    stack = SundialCicd(
        App(),
        "SundialCicd",
        repository="JoeMcMahon87/Sundial",
        repository_owner_id="14055195",
        repository_id="1339893752",
        env=Environment(account="111122223333", region="us-east-1"),
    )
    del app
    return Template.from_stack(stack)


@pytest.fixture(scope="session")
def prod_app_template() -> Template:
    """The aliased path: prod carries a custom hostname and a certificate."""
    app = App(
        context={
            "domain_name": "sundial.example.org",
            "certificate_arn": "arn:aws:acm:us-east-1:111122223333:certificate/abc",
        }
    )
    env = Environment(account="111122223333", region="us-east-1")
    infra = SundialInfra(app, "SundialInfra-prod", env_name="prod", env=env)
    return Template.from_stack(
        SundialApp(
            app,
            "SundialApp-prod",
            env_name="prod",
            table=infra.table,
            key=infra.key,
            session_key=infra.session_key,
            google_client_secret=infra.google_client_secret,
            env=env,
        )
    )


@pytest.fixture(scope="session")
def infra_template(stacks: tuple[SundialInfra, SundialApp]) -> Template:
    return Template.from_stack(stacks[0])


@pytest.fixture(scope="session")
def app_template(stacks: tuple[SundialInfra, SundialApp]) -> Template:
    return Template.from_stack(stacks[1])
