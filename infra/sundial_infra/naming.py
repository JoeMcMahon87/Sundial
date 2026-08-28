"""Resource naming and SSM parameter paths.

`dev` and `prod` share one AWS account (§16 decision 3), so every resource name
carries the environment suffix and every configuration value is read from
`/sundial/<env>/...`. Nothing here may be derived from a hostname — the domain
is deferred (§15.1).
"""

from __future__ import annotations

ENVIRONMENTS = ("dev", "prod")


def suffix(env: str) -> str:
    return f"sundial-{env}"


def param(env: str, name: str) -> str:
    return f"/sundial/{env}/{name}"


def secret_name(env: str, name: str) -> str:
    return f"sundial/{env}/{name}"


def site_bucket(env: str, account: str) -> str:
    """Bucket names are globally unique, so the account id is part of it."""
    return f"sundial-site-{env}-{account}"
