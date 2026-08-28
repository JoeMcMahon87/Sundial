#!/usr/bin/env python3
"""Generate an RS256 keypair and write it to the session-key secret (§13 runbook).

The material never touches the repo or a CloudFormation template — the secret
is created empty by SundialInfra and populated from here (§12).

    python3 scripts/rotate_session_key.py --env dev

Every existing session is invalidated: the new public key will not verify
tokens signed by the old private key. That is the point of the runbook entry
"revoke and re-issue the session signing key".
"""

from __future__ import annotations

import argparse
import json

import boto3
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def keypair() -> dict[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return {
        "private_key": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        "public_key": key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=("dev", "prod"), required=True)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    secret_id = f"sundial/{args.env}/session-key"
    client = boto3.client("secretsmanager", region_name=args.region)
    client.put_secret_value(SecretId=secret_id, SecretString=json.dumps(keypair()))

    print(f"wrote a new RS256 keypair to {secret_id}; all existing sessions are now invalid")


if __name__ == "__main__":
    main()
