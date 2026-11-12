"""
introspection.py
----------------

crm-api's live check of its own tokens at Level 3 (RFC 7662, docs/adr/0025). Keycloak answers only to a client in
the token's audience, so crm-api asks, as itself, about the per-API tokens issued for it. The answer also carries
what a lightweight token leaves out (roles, the store claim): those reach crm-api from Keycloak only, never inside
the token that crosses the MCP server.

A token Keycloak no longer calls active is refused (401); when Keycloak cannot answer, the request is refused
(503): Level 3 fails closed.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class TokenInactiveError(Exception):
    """Keycloak says the token is no longer active (signed out, revoked, expired)."""


class IntrospectionUnavailableError(Exception):
    """Keycloak could not be asked."""


@dataclass(frozen=True)
class Introspector:
    """Asks Keycloak about a token, as this API's own client."""

    url: str
    client_id: str
    client_secret: str
    timeout_seconds: float = 5.0
    transport: httpx.BaseTransport | None = None

    def check(self, token: str, *, subject: str) -> dict[str, Any]:
        """The active token's claims, for the subject its signature named; raises otherwise."""
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = client.post(
                    self.url,
                    data={"token": token, "token_type_hint": "access_token"},
                    auth=(self.client_id, self.client_secret),
                )
            body = response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as exc:
            raise IntrospectionUnavailableError(type(exc).__name__) from exc
        if not isinstance(body, dict):
            raise IntrospectionUnavailableError(f"HTTP {response.status_code}")
        if body.get("active") is not True or body.get("sub") != subject:
            raise TokenInactiveError("inactive")
        return body
