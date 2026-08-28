"""Fixtures for the backend suite.

Everything AWS-shaped runs against moto; nothing here reaches the network.
The module-level caches in ``config``, ``session``, ``google`` and ``store``
are per-execution-environment by design, so each test resets them.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from moto import mock_aws

REGION = "us-east-1"
UID = "108234567890123456789"


@pytest.fixture(autouse=True)
def _aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": REGION,
    }.items():
        monkeypatch.setenv(name, value)


@pytest.fixture(autouse=True)
def _reset_caches() -> Iterator[None]:
    from sundial.core import config, store
    from sundial.oauth import google, session, tokens

    def clear() -> None:
        config.settings.cache_clear()
        session._keypair.cache_clear()
        google._client_secret.cache_clear()
        store._table = None
        tokens._access_cache.clear()

    clear()
    yield
    clear()


def _rsa_pem_pair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    return private, public


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """A moto-backed table, KMS key, and the two secrets the app reads."""
    with mock_aws():
        dynamodb = boto3.client("dynamodb", region_name=REGION)
        dynamodb.create_table(
            TableName="sundial-test",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"}
                for name in ("PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK")
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": index,
                    "KeySchema": [
                        {"AttributeName": f"{index}PK", "KeyType": "HASH"},
                        {"AttributeName": f"{index}SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
                for index in ("GSI1", "GSI2")
            ],
        )

        key_id = boto3.client("kms", region_name=REGION).create_key()["KeyMetadata"]["KeyId"]

        secrets_client = boto3.client("secretsmanager", region_name=REGION)
        private, public = _rsa_pem_pair()
        session_arn = secrets_client.create_secret(
            Name="sundial/test/session-key",
            SecretString=json.dumps({"private_key": private, "public_key": public}),
        )["ARN"]
        client_arn = secrets_client.create_secret(
            Name="sundial/test/google-client",
            SecretString=json.dumps({"client_secret": "google-client-secret"}),
        )["ARN"]

        for name, value in {
            "SUNDIAL_ENV": "dev",
            "SUNDIAL_TABLE_NAME": "sundial-test",
            "SUNDIAL_KMS_KEY_ID": key_id,
            "SUNDIAL_SESSION_KEY_SECRET_ARN": session_arn,
            "SUNDIAL_GOOGLE_CLIENT_SECRET_ARN": client_arn,
            "SUNDIAL_GOOGLE_CLIENT_ID": "test-client-id.apps.googleusercontent.com",
            "SUNDIAL_ALLOWED_GOOGLE_ACCOUNT_ID": UID,
            "SUNDIAL_APP_BASE_URL": "http://localhost:5173",
            "SUNDIAL_GOOGLE_REDIRECT_URI": "http://localhost:5173/api/auth/callback",
        }.items():
            os.environ[name] = value

        yield {"key_id": key_id, "session_secret_arn": session_arn}

        for name in list(os.environ):
            if name.startswith("SUNDIAL_"):
                del os.environ[name]
