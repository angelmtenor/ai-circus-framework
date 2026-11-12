"""
test_level3.py
--------------

crm-api at Level 3 (docs/adr/0025) against the attacks it exists for: a per-API token copied out of a log and
replayed as a bearer token or with another key's proof, a proof replayed or made for another request or token,
a user signed out before their token expired, Keycloak unreachable, and a client without the shared authority's
certificate. Each ends in a refusal; only the bound key's fresh proof of a live token gets data.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import json
import socket
import ssl
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi.testclient import TestClient

from crm_api.api import TokenSettings
from crm_api.app import create_app, health_app, level3_servers, serve_together
from crm_api.core.dpop import DpopVerifier
from crm_api.core.introspection import Introspector
from crm_api.data_model import EnvConfig

ISSUER = "http://idp.test/realms/test"
SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
URL = "http://testserver/customers"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Key:
    """A DPoP key, as the MCP server holds it."""

    def __init__(self) -> None:
        """A fresh P-256 key and its thumbprint."""
        self.private = ec.generate_private_key(ec.SECP256R1())
        n = self.private.public_key().public_numbers()
        self.jwk = {"kty": "EC", "crv": "P-256", "x": b64(n.x.to_bytes(32, "big")), "y": b64(n.y.to_bytes(32, "big"))}
        canonical = json.dumps({k: self.jwk[k] for k in ("crv", "kty", "x", "y")}, separators=(",", ":"))
        self.jkt = b64(hashlib.sha256(canonical.encode()).digest())

    def proof(self, token: str | None, *, method: str = "GET", url: str = URL, iat: int | None = None) -> str:
        """A proof for one request, optionally for a token."""
        claims: dict[str, Any] = {"htm": method, "htu": url, "iat": iat or int(time.time()), "jti": str(uuid.uuid4())}
        if token is not None:
            claims["ath"] = b64(hashlib.sha256(token.encode()).digest())
        return jwt.encode(claims, self.private, algorithm="ES256", headers={"typ": "dpop+jwt", "jwk": self.jwk})


def lightweight_t2(jkt: str | None, *, subject: str = "user-1") -> str:
    """The per-API token as Keycloak issues it at Level 3: ids and the key binding only, no roles or stores."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": subject,
        "aud": "crm-api",
        "azp": "crm-mcp",
        "iat": now,
        "exp": now + 300,
        "organization": {"lite-demo": {"id": "id-lite-demo"}},
    }
    if jkt is not None:
        claims["cnf"] = {"jkt": jkt}
    return jwt.encode(claims, SIGNING_KEY, algorithm="RS256")


class Keycloak:
    """Introspection as Keycloak answers it to crm-api: the full claims of an active token."""

    def __init__(self) -> None:
        """Every token active, for a store manager of S012."""
        self.signed_out: set[str] = set()
        self.down = False
        self.cnf_override: str | None = None
        self.roles = ["retail_store_manager"]
        self.stores = ["S012"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one introspection request."""
        if self.down:
            return httpx.Response(503)
        token = dict(x.split("=", 1) for x in request.content.decode().split("&"))["token"]
        claims = jwt.decode(token, options={"verify_signature": False})
        if claims["sub"] in self.signed_out:
            return httpx.Response(200, json={"active": False})
        cnf = {"jkt": self.cnf_override} if self.cnf_override else claims.get("cnf")
        body = {
            **claims,
            "active": True,
            "cnf": cnf,
            "realm_access": {"roles": self.roles},
            "stores": self.stores,
        }
        return httpx.Response(200, json=body)


@pytest.fixture
def keycloak() -> Keycloak:
    return Keycloak()


@pytest.fixture
def client(keycloak: Keycloak) -> TestClient:
    public = SIGNING_KEY.public_key()
    settings = TokenSettings(
        issuer=ISSUER,
        audience="crm-api",
        key_for=lambda _t: public,
        level=3,
        introspector=Introspector(
            url="http://idp.test/introspect",
            client_id="crm-api",
            client_secret="s",  # ruff: ignore[hardcoded-password-func-arg] - a test-only secret
            transport=httpx.MockTransport(keycloak),
        ),
        dpop=DpopVerifier(),
    )
    return TestClient(create_app(settings))


def call(client: TestClient, token: str, proof: str | None, scheme: str = "DPoP") -> httpx.Response:
    headers = {"Authorization": f"{scheme} {token}"}
    if proof is not None:
        headers["DPoP"] = proof
    return client.get("/customers", headers=headers, params={"limit": 50})


def test_the_bound_keys_fresh_proof_of_a_live_token_gets_its_roles_rows(client: TestClient) -> None:
    key = Key()
    t2 = lightweight_t2(key.jkt)
    response = call(client, t2, key.proof(t2))
    assert response.status_code == 200
    body = response.json()
    # Roles and the store claim are not in the token: they came from Keycloak's live answer.
    assert body["visible_stores"] == ["S012"] and body["count"] == 4


def test_a_copied_token_is_useless(client: TestClient) -> None:
    key, thief = Key(), Key()
    t2 = lightweight_t2(key.jkt)
    as_bearer = call(client, t2, None, scheme="Bearer")
    assert as_bearer.status_code == 401 and as_bearer.headers["www-authenticate"].startswith("DPoP")
    assert call(client, t2, None).status_code == 401, "no proof"
    assert call(client, t2, thief.proof(t2)).status_code == 401, "a proof by another key"


@pytest.mark.parametrize(
    "bad",
    ["another method", "another url", "another token", "no token hash", "stale", "unbound token"],
)
def test_a_proof_for_anything_else_is_refused(client: TestClient, bad: str) -> None:
    key = Key()
    t2 = lightweight_t2(None if bad == "unbound token" else key.jkt)
    proof = {
        "another method": key.proof(t2, method="POST"),
        "another url": key.proof(t2, url="http://testserver/customers/C001"),
        "another token": key.proof(lightweight_t2(key.jkt, subject="user-2")),
        "no token hash": key.proof(None),
        "stale": key.proof(t2, iat=int(time.time()) - 600),
        "unbound token": key.proof(t2),
    }[bad]
    assert call(client, t2, proof).status_code == 401


def test_a_proof_is_accepted_once(client: TestClient) -> None:
    key = Key()
    t2 = lightweight_t2(key.jkt)
    proof = key.proof(t2)
    assert call(client, t2, proof).status_code == 200
    assert call(client, t2, proof).status_code == 401


def test_signed_out_is_refused_at_once_and_an_unreachable_keycloak_fails_closed(
    client: TestClient, keycloak: Keycloak
) -> None:
    key = Key()
    t2 = lightweight_t2(key.jkt)
    keycloak.signed_out.add("user-1")
    assert call(client, t2, key.proof(t2)).status_code == 401
    keycloak.signed_out.clear()
    keycloak.down = True
    assert call(client, t2, key.proof(t2)).status_code == 503


def test_keycloak_naming_another_key_wins_over_the_token(client: TestClient, keycloak: Keycloak) -> None:
    key = Key()
    t2 = lightweight_t2(key.jkt)
    keycloak.cnf_override = Key().jkt
    assert call(client, t2, key.proof(t2)).status_code == 401


def test_level_3_never_runs_without_its_checks() -> None:
    with pytest.raises(ValueError, match="Level 3"):
        TokenSettings(issuer=ISSUER, audience="crm-api", key_for=lambda _t: None, level=3)


def test_the_plain_listener_serves_health_and_metrics_only() -> None:
    plain = TestClient(health_app())
    assert plain.get("/healthz").json() == {"status": "ok"}
    assert plain.get("/metrics").status_code == 200
    assert plain.get("/customers").status_code == 404


# ── mutual TLS, on real sockets ────────────────────────────────────────────────────────────────────


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _cert(subject: str, key: Any, issuer: tuple[x509.Name, Any], *, ca: bool = False, eku: Any = None) -> bytes:
    now = dt.datetime.now(dt.UTC)
    builder = (
        x509
        .CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(issuer[0])
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=0 if ca else None), critical=True)
        # Python 3.13+ verifies strictly (VERIFY_X509_STRICT): identifiers on both sides of the chain.
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer[1].public_key()), critical=False)
    )
    if ca:
        usage = dict.fromkeys(
            ("digital_signature", "content_commitment", "key_encipherment", "data_encipherment", "key_agreement"), False
        )
        builder = builder.add_extension(
            x509.KeyUsage(**usage, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False),
            critical=True,
        )
    if eku is not None:
        builder = builder.add_extension(x509.ExtendedKeyUsage([eku]), critical=False)
    if eku == ExtendedKeyUsageOID.SERVER_AUTH:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
    return builder.sign(issuer[1], hashes.SHA256()).public_bytes(serialization.Encoding.PEM)


def _key_pem(key: Any) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _pki(directory: Path, name: str) -> dict[str, Path]:
    """One authority, a server certificate (localhost) and a client certificate, as files."""
    ca_key, server_key, client_key = (ec.generate_private_key(ec.SECP256R1()) for _ in range(3))
    issuer = (_name(f"{name}-ca"), ca_key)
    files = {
        "ca": _cert(f"{name}-ca", ca_key, issuer, ca=True),
        "server_cert": _cert("localhost", server_key, issuer, eku=ExtendedKeyUsageOID.SERVER_AUTH),
        "server_key": _key_pem(server_key),
        "client_cert": _cert(f"{name}-client", client_key, issuer, eku=ExtendedKeyUsageOID.CLIENT_AUTH),
        "client_key": _key_pem(client_key),
    }
    paths = {}
    for kind, data in files.items():
        paths[kind] = directory / f"{name}-{kind}.pem"
        paths[kind].write_bytes(data)
    return paths


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def level3_server(tmp_path: Path, client: TestClient) -> Iterator[tuple[int, int, dict[str, Path], dict[str, Path]]]:
    ours, theirs = _pki(tmp_path, "crm"), _pki(tmp_path, "other")
    plain_port, tls_port = _free_port(), _free_port()
    config = EnvConfig(
        LOG_LEVEL="WARNING",
        HTTP_PORT=str(plain_port),
        KEYCLOAK_ISSUER=ISSUER,
        KEYCLOAK_JWKS_URL="http://idp.test/jwks",
        API_AUDIENCE="crm-api",
        TLS_PORT=str(tls_port),
        TLS_CERT_FILE=str(ours["server_cert"]),
        TLS_KEY_FILE=str(ours["server_key"]),
        TLS_CLIENT_CA_FILE=str(ours["ca"]),
    )
    servers = level3_servers(client.app, config)  # type: ignore[arg-type]
    thread = threading.Thread(target=lambda: asyncio.run(serve_together(servers)), daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline and not all(server.started for server in servers):
        time.sleep(0.05)
    yield plain_port, tls_port, ours, theirs
    for server in servers:
        server.should_exit = True
    thread.join(timeout=5)


def _tls(ca: Path, cert: Path | None = None, key: Path | None = None) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(ca))
    if cert is not None and key is not None:
        context.load_cert_chain(str(cert), str(key))
    return context


def test_only_the_shared_authoritys_client_reaches_the_api(level3_server: Any) -> None:
    plain_port, tls_port, ours, theirs = level3_server
    url = f"https://localhost:{tls_port}/healthz"
    with httpx.Client(verify=_tls(ours["ca"], ours["client_cert"], ours["client_key"])) as ok:
        assert ok.get(url).status_code == 200
    for refused in (_tls(ours["ca"]), _tls(ours["ca"], theirs["client_cert"], theirs["client_key"])):
        with (
            httpx.Client(verify=refused) as client,
            pytest.raises((httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError)),
        ):
            client.get(url)
    # The plain port answers health and metrics, and never the API.
    assert httpx.get(f"http://127.0.0.1:{plain_port}/customers").status_code == 404
