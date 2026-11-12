"""
test_api.py
-----------

The example API against the API checklist of docs/adr/0022: only tokens issued for it, identity from the
token only, role-filtered and masked answers.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from crm_api.api import TokenSettings
from crm_api.app import create_app

ISSUER = "http://idp.test/realms/test"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(
    *,
    audience: str = "crm-api",
    roles: tuple[str, ...] = ("retail_analyst",),
    stores: list[str] | None = None,
    tenant: str = "lite-demo",
    key: Any = KEY,
    expires_in: int = 300,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": "user-1",
        "aud": audience,
        "iat": now,
        "exp": now + expires_in,
        "organization": {tenant: {"id": f"id-{tenant}"}},
        "realm_access": {"roles": list(roles)},
    }
    if stores is not None:
        claims["stores"] = stores
    return jwt.encode(claims, key, algorithm="RS256")


@pytest.fixture
def client() -> TestClient:
    public = KEY.public_key()
    return TestClient(create_app(TokenSettings(issuer=ISSUER, audience="crm-api", key_for=lambda _t: public)))


def get(client: TestClient, path: str, bearer: str | None, **params: Any) -> Any:
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    return client.get(path, headers=headers, params=params)


@pytest.mark.parametrize(
    "bearer",
    [
        None,
        token(audience="crm-mcp"),  # a T1 replayed straight at the API
        token(key=OTHER_KEY),  # forged
        token(expires_in=-120),  # expired
    ],
    ids=["no-token", "wrong-audience", "forged", "expired"],
)
def test_only_a_valid_token_for_this_api_is_accepted(client: TestClient, bearer: str | None) -> None:
    assert get(client, "/customers", bearer).status_code == 401


def test_an_analyst_sees_every_store_masked(client: TestClient) -> None:
    body = get(client, "/customers", token(), limit=50).json()
    assert body["visible_stores"] == "all"
    assert body["count"] == 27
    assert all("•••@" in c["email"] and c["display_name"].endswith(".") for c in body["customers"])


def test_a_store_manager_sees_only_its_stores(client: TestClient) -> None:
    body = get(client, "/customers", token(roles=("retail_store_manager",), stores=["S012"]), limit=50).json()
    assert body["visible_stores"] == ["S012"]
    assert body["count"] == 4 and {c["home_store"] for c in body["customers"]} == {"S012"}


def test_a_store_manager_cannot_read_another_stores_customer(client: TestClient) -> None:
    manager = token(roles=("retail_store_manager",), stores=["S012"])
    assert get(client, "/customers/C001", manager).status_code == 404
    assert get(client, "/customers/C012", manager).status_code == 200


def test_a_role_without_a_grant_is_refused(client: TestClient) -> None:
    assert get(client, "/customers", token(roles=("visitor",))).status_code == 403


def test_another_tenant_gets_nothing(client: TestClient) -> None:
    assert get(client, "/customers", token(tenant="globex")).json()["count"] == 0


@pytest.mark.parametrize("param", ["user_id", "org_id", "tenant", "user"])
def test_a_request_naming_someone_else_is_refused(client: TestClient, param: str) -> None:
    assert get(client, "/customers", token(), **{param: "someone-else"}).status_code == 403


def test_naming_yourself_is_harmless(client: TestClient) -> None:
    assert get(client, "/customers", token(), tenant="lite-demo", user_id="user-1").status_code == 200


def test_sorting_by_lifetime_value_and_bounded_limits(client: TestClient) -> None:
    body = get(client, "/customers", token(), sort="lifetime_value", limit=3).json()
    values = [c["lifetime_value_eur"] for c in body["customers"]]
    assert values == sorted(values, reverse=True) and len(values) == 3
    assert get(client, "/customers", token(), limit=500).status_code == 422
