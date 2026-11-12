"""
testing.py
----------

Test helpers for MCP servers built with the kit: a local signing key that issues tokens shaped like the
identity provider's (tenant, roles, claims), a verifier that trusts it, and an identity-provider stand-in
for the token exchange. Every server generated from the template replays the failure cases with these.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

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


class FakeIdentityProvider:
    """An httpx transport answering the token exchange like the identity provider would.

    It accepts `client_id`'s exchange only for `allowed_audience`, only for a subject token this
    factory signed with the client in its audience, and mints a T2 for the same user and tenant.
    """

    def __init__(self, factory: TokenFactory, *, client_id: str, client_secret: str, allowed_audience: str) -> None:
        """Answer exchanges as the identity provider configured for one MCP server client."""
        self.factory = factory
        self.client_id = client_id
        self.client_secret = client_secret
        self.allowed_audience = allowed_audience
        self.requests = 0

    def transport(self) -> httpx.MockTransport:
        """The transport to hand to `TokenExchanger`."""
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        auth = request.headers.get("authorization", "")
        expected = httpx.BasicAuth(self.client_id, self.client_secret)._auth_header
        if auth != expected:
            return httpx.Response(401, json={"error": "unauthorized_client"})
        if form.get("audience") != self.allowed_audience:
            return httpx.Response(400, json={"error": "invalid_request"})
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
        org = subject.get("organization") or {}
        (alias, details), *_ = org.items()
        token = self.factory.issue(
            audience=self.allowed_audience,
            subject=subject["sub"],
            org_alias=alias,
            org_id=details["id"],
            roles=subject.get("realm_access", {}).get("roles", []),
            **{
                k: v
                for k, v in subject.items()
                if k not in {"iss", "sub", "aud", "iat", "exp", "jti", "realm_access", "organization"}
            },
        )
        return httpx.Response(200, content=json.dumps({"access_token": token, "expires_in": 300}))
