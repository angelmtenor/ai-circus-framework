"""
test_level3.py
--------------

Level 3 (docs/adr/0025) against the attacks it exists for: a per-API token copied out of a log or a proxy, a user
signed out while their token is still unexpired, an identity provider that cannot answer, and a server configured
below its level. Each must end in a refusal, never in data.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from starlette.applications import Starlette

from mcp_kit import (
    ApiTls,
    DpopKey,
    ExchangeRefusedError,
    ExchangeSettings,
    IntrospectionSettings,
    IntrospectionUnavailableError,
    Level3ConfigError,
    McpKit,
    Policy,
    PolicyError,
    TokenExchanger,
    TokenInactiveError,
    TokenIntrospector,
)
from mcp_kit.dpop import access_token_hash, bound_thumbprint, htu, jwk_thumbprint
from mcp_kit.testing import (
    DpopError,
    FakeIdentityProvider,
    TokenFactory,
    dpop_bound_token,
    verify_dpop,
    write_test_pki,
)

SERVER = "crm-mcp"
API = "crm-api"
SECRET = "test-secret"
TOKEN_URL = "http://idp.test/realms/test/token"
INTROSPECT_URL = f"{TOKEN_URL}/introspect"
API_URL = "https://api.test"
POLICY = Policy.from_mapping({"level": 3, "tenants": ["acme"], "roles": {"analyst": ["lookup"]}})


# ── DPoP primitives ────────────────────────────────────────────────────────────────────────────────


def test_thumbprint_matches_rfc7638_example() -> None:
    # RFC 7638 §3.1: the example RSA key and its SHA-256 thumbprint.
    jwk = {
        "kty": "RSA",
        "n": (
            "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAtVT86zwu1RK7aPFFxuhDR1L6tSoc_BJECPebWKRXjBZC"
            "iFV4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4QyQ5v-65YGjQR0_FDW2QvzqY368QQMicAtaSqzs8KJ"
            "ZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n91CbOpbISD08qNLyrdkt-bFTWhAI4vMQFh6WeZu0fM4lFd2NcRwr3XPksINHaQ-G_x"
            "BniIqbw0Ls1jF44-csFCur-kEgU8awapJzKnqDKgw"
        ),
        "e": "AQAB",
        "alg": "RS256",
        "kid": "2011-04-29",
    }
    assert jwk_thumbprint(jwk) == "NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs"


def test_a_proof_names_one_request_and_carries_only_the_public_key() -> None:
    key = DpopKey()
    proof = key.proof("get", "https://API.test/customers?q=S012#x", access_token="t2")
    header = jwt.get_unverified_header(proof)
    claims = jwt.decode(proof, options={"verify_signature": False})
    assert header["typ"] == "dpop+jwt" and header["alg"] == "ES256" and "d" not in header["jwk"]
    assert claims["htm"] == "GET" and claims["htu"] == "https://api.test/customers"
    assert claims["ath"] == access_token_hash("t2")
    assert verify_dpop(proof, method="GET", url="https://api.test/customers", access_token="t2") == key.thumbprint
    assert key.proof("GET", "https://api.test/x") != key.proof("GET", "https://api.test/x"), "every proof is unique"
    assert htu("HTTPS://Api.Test") == "https://api.test/"


@pytest.mark.parametrize(
    ("method", "url", "token"),
    [
        ("POST", "https://api.test/customers", "t2"),
        ("GET", "https://other.test/customers", "t2"),
        ("GET", "https://api.test/customers", "t3"),
    ],
)
def test_a_proof_for_another_request_or_token_is_refused(method: str, url: str, token: str) -> None:
    proof = DpopKey().proof("GET", "https://api.test/customers", access_token="t2")
    with pytest.raises(DpopError):
        verify_dpop(proof, method=method, url=url, access_token=token)


# ── the per-API token is bound to the server's key ─────────────────────────────────────────────────


@pytest.fixture
def factory() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def idp(factory: TokenFactory) -> FakeIdentityProvider:
    return FakeIdentityProvider(
        factory, client_id=SERVER, client_secret=SECRET, allowed_audience=API, require_dpop=True
    )


def exchanger(idp: FakeIdentityProvider, key: DpopKey | None) -> TokenExchanger:
    settings = ExchangeSettings(
        token_url=TOKEN_URL, client_id=SERVER, client_secret=SECRET, api_audience=API, dpop_key=key
    )
    return TokenExchanger(settings, transport=idp.transport())


def test_the_exchange_binds_the_per_api_token_to_the_servers_key(
    factory: TokenFactory, idp: FakeIdentityProvider
) -> None:
    key = DpopKey()
    t1 = factory.issue(audience=SERVER, roles=["analyst"])
    t2 = asyncio.run(exchanger(idp, key).exchange(t1))
    assert bound_thumbprint(t2) == key.thumbprint


def test_an_exchange_without_a_proof_is_refused_where_binding_is_required(
    factory: TokenFactory, idp: FakeIdentityProvider
) -> None:
    with pytest.raises(ExchangeRefusedError):
        asyncio.run(exchanger(idp, None).exchange(factory.issue(audience=SERVER)))


def test_an_unbound_answer_is_refused_at_level_3(factory: TokenFactory) -> None:
    # An identity provider (or a proxy in front of it) that answers with a plain bearer token.
    bearer = factory.issue(audience=API)
    transport = httpx.MockTransport(
        lambda _r: httpx.Response(200, json={"access_token": bearer, "token_type": "Bearer", "expires_in": 300})
    )
    settings = ExchangeSettings(
        token_url=TOKEN_URL, client_id=SERVER, client_secret=SECRET, api_audience=API, dpop_key=DpopKey()
    )
    with pytest.raises(ExchangeRefusedError):
        asyncio.run(TokenExchanger(settings, transport=transport).exchange(factory.issue(audience=SERVER)))


# ── live token check ───────────────────────────────────────────────────────────────────────────────


def introspector(idp: FakeIdentityProvider, cache_seconds: float = 0.0) -> TokenIntrospector:
    settings = IntrospectionSettings(
        url=INTROSPECT_URL, client_id=SERVER, client_secret=SECRET, cache_seconds=cache_seconds
    )
    return TokenIntrospector(settings, transport=idp.transport())


def test_introspection_follows_revocation_and_never_passes_when_unanswered(
    factory: TokenFactory, idp: FakeIdentityProvider
) -> None:
    check = introspector(idp)
    t1 = factory.issue(audience=SERVER, subject="u1")
    assert asyncio.run(check.check(t1, subject="u1"))["active"] is True
    with pytest.raises(TokenInactiveError):
        asyncio.run(check.check(t1, subject="someone-else"))
    idp.revoke("u1")
    with pytest.raises(TokenInactiveError):
        asyncio.run(check.check(t1, subject="u1"))
    idp.down = True
    with pytest.raises(IntrospectionUnavailableError):
        asyncio.run(check.check(t1, subject="u1"))


def test_level_3_asks_on_every_call_unless_a_cache_is_chosen(factory: TokenFactory, idp: FakeIdentityProvider) -> None:
    t1 = factory.issue(audience=SERVER, subject="u1")
    every_call = introspector(idp)
    asyncio.run(every_call.check(t1, subject="u1"))
    asyncio.run(every_call.check(t1, subject="u1"))
    assert idp.introspections == 2
    cached = introspector(idp, cache_seconds=30)
    asyncio.run(cached.check(t1, subject="u1"))
    asyncio.run(cached.check(t1, subject="u1"))
    assert idp.introspections == 3


# ── the whole server at level 3 ────────────────────────────────────────────────────────────────────


def test_the_policy_carries_the_level() -> None:
    assert Policy.from_mapping({"roles": {}}).level == 2
    assert POLICY.level == 3
    for bad in (1, 4, "3", True):
        with pytest.raises(PolicyError):
            Policy.from_mapping({"level": bad})


def test_a_level_3_server_refuses_to_start_without_its_parts(factory: TokenFactory) -> None:
    with pytest.raises(Level3ConfigError) as refused:
        McpKit(
            name=SERVER,
            instructions="x",
            verifier=factory.verifier(SERVER),
            policy=POLICY,
            exchanger=TokenExchanger(
                ExchangeSettings(token_url=TOKEN_URL, client_id=SERVER, client_secret=SECRET, api_audience=API)
            ),
            api_base_url="http://api.test",
        )
    message = str(refused.value)
    assert "introspector" in message and "DPoP" in message and "mutual TLS" in message


class StandInApi:
    """An API that accepts only a DPoP-bound token for it, with a fresh proof of the key it is bound to."""

    def __init__(self, factory: TokenFactory) -> None:
        self.factory = factory
        self.seen_jti: set[str] = set()
        self.calls: list[dict[str, Any]] = []
        self.tokens: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        try:
            token = dpop_bound_token(request)
            claims = jwt.decode(token, self.factory.verifier(API).key_for(""), algorithms=["RS256"], audience=API)
            proof = request.headers.get("dpop")
            jkt = verify_dpop(proof, method=request.method, url=str(request.url), access_token=token)
            jti = jwt.decode(proof or "", options={"verify_signature": False})["jti"]
            if jti in self.seen_jti or jkt != (claims.get("cnf") or {}).get("jkt"):
                raise DpopError("replayed proof or another key")
            self.seen_jti.add(jti)
        except (DpopError, jwt.PyJWTError) as exc:
            return httpx.Response(401, json={"detail": str(exc)}, headers={"WWW-Authenticate": "DPoP"})
        self.calls.append({"sub": claims["sub"], "jkt": jkt})
        self.tokens.append(token)
        return httpx.Response(200, json={"sub": claims["sub"]})


def build(
    factory: TokenFactory, idp: FakeIdentityProvider, api: StandInApi, tls_dir: Path
) -> tuple[Starlette, DpopKey]:
    files = write_test_pki(tls_dir)
    key = DpopKey()
    kit = McpKit(
        name=SERVER,
        instructions="Level 3 test server.",
        verifier=factory.verifier(SERVER),
        policy=POLICY,
        exchanger=exchanger(idp, key),
        api_base_url=API_URL,
        api_transport=httpx.MockTransport(api),
        introspector=introspector(idp),
        api_tls=ApiTls(
            ca_file=str(files["ca"]), cert_file=str(files["client_cert"]), key_file=str(files["client_key"])
        ),
    )

    @kit.tool()
    async def lookup(query: str) -> dict[str, Any]:
        """Ask the API, as the caller."""
        ctx = kit.context()
        assert ctx.api is not None
        return await ctx.api.get("/customers", params={"q": query})

    return kit.asgi_app(), key


def call(app: Starlette, token: str, method: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
    async def go() -> tuple[int, Any]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://kit.test") as client,
        ):
            response = await client.post("/mcp/", headers=headers, content=json.dumps(body))
        return response.status_code, response.json()

    return asyncio.run(go())


def test_level_3_end_to_end_and_every_attack_refused(
    factory: TokenFactory, idp: FakeIdentityProvider, tmp_path: Path
) -> None:
    api = StandInApi(factory)
    app, key = build(factory, idp, api, tmp_path)
    t1 = factory.issue(audience=SERVER, subject="u1", org_alias="acme", roles=["analyst"])

    status, body = call(app, t1, "tools/call", {"name": "lookup", "arguments": {"query": "x"}})
    assert status == 200 and not body["result"]["isError"]
    assert api.calls == [{"sub": "u1", "jkt": key.thumbprint}]
    (t2,) = api.tokens

    # The copied per-API token, replayed as a bearer token, or with a proof of another key: refused by the API.
    as_bearer = httpx.Request("GET", f"{API_URL}/customers", headers={"Authorization": f"Bearer {t2}"})
    assert api(as_bearer).status_code == 401
    thief = DpopKey()
    stolen = httpx.Request(
        "GET",
        f"{API_URL}/customers",
        headers={"Authorization": f"DPoP {t2}", "DPoP": thief.proof("GET", f"{API_URL}/customers", t2)},
    )
    assert api(stolen).status_code == 401

    # Signed out: the still-unexpired user token stops working on the next request.
    idp.revoke("u1")
    status, body = call(app, t1, "tools/list")
    assert status == 401 and "no longer active" in body["detail"]

    # The identity provider cannot answer: refused, never waved through.
    idp.revoked.clear()
    idp.down = True
    status, _ = call(app, t1, "tools/list")
    assert status == 503
