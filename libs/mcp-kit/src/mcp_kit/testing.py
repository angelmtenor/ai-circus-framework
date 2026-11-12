"""
testing.py
----------

Test helpers for MCP servers built with the kit: a local signing key that issues tokens shaped like the
identity provider's (tenant, roles, claims), a verifier that trusts it, and an identity-provider stand-in
for the token exchange, the live token check and revocation (Level 3, docs/adr/0025), plus the checks a
stand-in API makes on a DPoP-bound token. Every server generated from the template replays the failure
cases with these.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from mcp_kit import pki
from mcp_kit.dpop import DPOP_SCHEME, PROOF_TYPE, access_token_hash, htu, jwk_thumbprint
from mcp_kit.identity import TokenVerifier

ISSUER = "http://idp.test/realms/test"


class TokenFactory:
    """Issues RS256 tokens with a throwaway key, and verifiers that trust only that key."""

    def __init__(self, issuer: str = ISSUER) -> None:
        """A fresh key per factory; a second factory's tokens are forgeries to this one."""
        self.issuer = issuer
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def issue(
        self,
        *,
        audience: str | Iterable[str],
        subject: str = "user-1",
        org_alias: str | None = "acme",
        org_id: str = "org-acme",
        roles: Iterable[str] = (),
        expires_in: int = 300,
        issuer: str | None = None,
        **claims: Any,
    ) -> str:
        """A signed token; `org_alias=None` leaves the tenant out."""
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": issuer or self.issuer,
            "sub": subject,
            "aud": audience if isinstance(audience, str) else list(audience),
            "iat": now,
            "exp": now + expires_in,
            "jti": str(uuid.uuid4()),
            "realm_access": {"roles": list(roles)},
            **claims,
        }
        if org_alias is not None:
            payload["organization"] = {org_alias: {"id": org_id}}
        return jwt.encode(payload, self._key, algorithm="RS256")

    def verifier(self, audience: str) -> TokenVerifier:
        """A verifier for this factory's issuer and key, expecting `audience`."""
        public = self._key.public_key()
        return TokenVerifier(issuer=self.issuer, audience=audience, key_for=lambda _token: public)


class DpopError(Exception):
    """A DPoP proof that a stand-in API (or the identity provider stand-in) refuses."""


def verify_dpop(proof: str | None, *, method: str, url: str, access_token: str | None = None) -> str:
    """Check a proof the way an API must (RFC 9449 §4.3) and return its key's thumbprint.

    Type, algorithm, a public key and the signature by it, method, URL and freshness, and for a resource call
    the access token's hash. Replay of the `jti` and the token's own `cnf.jkt` are the caller's to compare.
    """
    if not proof:
        raise DpopError("missing proof")
    try:
        header = jwt.get_unverified_header(proof)
        jwk = header.get("jwk") or {}
        if header.get("typ") != PROOF_TYPE or header.get("alg") not in ("ES256", "RS256", "PS256") or "d" in jwk:
            raise DpopError("malformed proof")
        key = jwt.PyJWK(jwk).key
        claims = jwt.decode(proof, key, algorithms=[header["alg"]], options={"require": ["iat", "jti", "htm", "htu"]})
    except (jwt.PyJWTError, KeyError) as exc:
        raise DpopError("invalid proof") from exc
    if claims["htm"] != method.upper() or claims["htu"] != htu(url):
        raise DpopError("proof for another request")
    if abs(time.time() - int(claims["iat"])) > 60:
        raise DpopError("stale proof")
    if access_token is not None and claims.get("ath") != access_token_hash(access_token):
        raise DpopError("proof for another token")
    return jwk_thumbprint(jwk)


def dpop_bound_token(request: httpx.Request) -> str:
    """The token of an `Authorization: DPoP …` request, or DpopError for any other scheme."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme != DPOP_SCHEME or not token:
        raise DpopError("a DPoP-bound token is required")
    return token


class FakeIdentityProvider:
    """An httpx transport answering the token exchange and introspection like the identity provider would.

    It accepts `client_id`'s exchange only for `allowed_audience`, only for a subject token this
    factory signed with the client in its audience, and mints a T2 for the same user and tenant. With a
    DPoP proof it binds T2 to the proof's key (`require_dpop` refuses an exchange without one).
    Introspection (`…/introspect`) answers active for an unexpired token of this factory that names the
    asking client in its audience, unless its subject was revoked (`revoke`); `down` makes it fail.
    """

    def __init__(
        self,
        factory: TokenFactory,
        *,
        client_id: str,
        client_secret: str,
        allowed_audience: str,
        require_dpop: bool = False,
    ) -> None:
        """Answer exchanges as the identity provider configured for one MCP server client."""
        self.factory = factory
        self.client_id = client_id
        self.client_secret = client_secret
        self.allowed_audience = allowed_audience
        self.require_dpop = require_dpop
        self.requests = 0
        self.introspections = 0
        self.revoked: set[str] = set()
        self.down = False

    def revoke(self, subject: str) -> None:
        """Sign a user out: every token of theirs is inactive from now on."""
        self.revoked.add(subject)

    def transport(self) -> httpx.MockTransport:
        """The transport to hand to `TokenExchanger`."""
        return httpx.MockTransport(self._handle)

    def _introspect(self, form: dict[str, str]) -> httpx.Response:
        self.introspections += 1
        if self.down:
            return httpx.Response(503, text="unavailable")
        try:
            claims = jwt.decode(
                form.get("token", ""),
                self.factory.verifier(self.client_id).key_for(""),
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=self.factory.issuer,
            )
        except jwt.PyJWTError:
            return httpx.Response(200, json={"active": False})
        if claims["sub"] in self.revoked:
            return httpx.Response(200, json={"active": False})
        return httpx.Response(200, json={**claims, "active": True, "client_id": claims.get("azp", "")})

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        auth = request.headers.get("authorization", "")
        expected = httpx.BasicAuth(self.client_id, self.client_secret)._auth_header
        if auth != expected:
            return httpx.Response(401, json={"error": "unauthorized_client"})
        if request.url.path.endswith("/introspect"):
            return self._introspect(form)
        if form.get("audience") != self.allowed_audience:
            return httpx.Response(400, json={"error": "invalid_request"})
        bound: dict[str, Any] = {}
        if "dpop" in request.headers:
            try:
                bound = {"cnf": {"jkt": verify_dpop(request.headers["dpop"], method="POST", url=str(request.url))}}
            except DpopError:
                return httpx.Response(400, json={"error": "invalid_dpop_proof"})
        elif self.require_dpop:
            return httpx.Response(400, json={"error": "invalid_request", "error_description": "DPoP proof is missing"})
        try:
            public_key = self.factory.verifier(self.client_id).key_for("")
            subject = jwt.decode(
                form.get("subject_token", ""),
                public_key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=self.factory.issuer,
            )
        except jwt.PyJWTError:
            return httpx.Response(400, json={"error": "invalid_token"})
        if subject["sub"] in self.revoked:
            return httpx.Response(400, json={"error": "invalid_request", "error_description": "Invalid token"})
        org = subject.get("organization") or {}
        (alias, details), *_ = org.items()
        token = self.factory.issue(
            audience=self.allowed_audience,
            subject=subject["sub"],
            org_alias=alias,
            org_id=details["id"],
            roles=subject.get("realm_access", {}).get("roles", []),
            azp=self.client_id,
            **bound,
            **{
                k: v
                for k, v in subject.items()
                if k not in {"iss", "sub", "aud", "iat", "exp", "jti", "realm_access", "organization", "azp"}
            },
        )
        token_type = "DPoP" if bound else "Bearer"
        body = {"access_token": token, "expires_in": 300, "token_type": token_type}
        return httpx.Response(200, content=json.dumps(body))


def write_test_pki(
    directory: Path, *, server_name: str = "api.test", client_name: str = "mcp-server"
) -> dict[str, Path]:
    """A throwaway authority with an API server certificate and an MCP server client certificate, as PEM files:
    keys `ca`, `server_cert`, `server_key`, `client_cert`, `client_key`."""
    ca = pki.issue_ca("test-ca")
    files = {
        name: directory / f"{name}.pem" for name in ("ca", "server_cert", "server_key", "client_cert", "client_key")
    }
    files["ca"].write_bytes(ca.certificate_pem())
    pki.write(pki.issue_leaf(ca, server_name, usage=pki.SERVER), files["server_cert"], files["server_key"])
    pki.write(pki.issue_leaf(ca, client_name, usage=pki.CLIENT), files["client_cert"], files["client_key"])
    return files
