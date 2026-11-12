"""
introspection.py
----------------

A live check of the user's token on every call (RFC 7662, docs/adr/0025). A signed token stays valid until it
expires; at Level 3 the MCP server also asks the identity provider whether it is still active, so signing a user
out, disabling them or revoking the token stops them on their next call, not minutes later. Keycloak answers only
to a client in the token's audience, so each hop checks its own token: the MCP server T1, the API T2.

When the identity provider cannot answer, the call is refused (503): Level 3 fails closed.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import httpx

from mcp_kit.identity import TokenRejectedError
from mcp_kit.telemetry import INTROSPECTION_SECONDS, INTROSPECTIONS

MAX_CACHED = 1024


class TokenInactiveError(TokenRejectedError):
    """The identity provider says the token is no longer active (signed out, revoked, disabled) → 401."""


class IntrospectionUnavailableError(Exception):
    """The identity provider could not be asked (network, 5xx, malformed answer) → 503, never a pass."""


@dataclass(frozen=True)
class IntrospectionSettings:
    """Where to ask and as whom. `cache_seconds` 0 asks on every call (Level 3); more trades revocation lag for load."""

    url: str
    client_id: str
    client_secret: str
    cache_seconds: float = 0.0
    timeout_seconds: float = 5.0


class TokenIntrospector:
    """Asks whether a token is still active and returns the identity provider's view of it."""

    def __init__(self, settings: IntrospectionSettings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """`transport` is for tests; production uses the default HTTP transport."""
        self.settings = settings
        self._transport = transport
        self.server = "mcp-server"  # the metrics label; McpKit sets it to the server's name
        self._cache: OrderedDict[str, tuple[dict[str, Any], float]] = OrderedDict()

    async def check(self, token: str, *, subject: str) -> dict[str, Any]:
        """The active token's claims, for the subject the signed token named. Raises TokenInactiveError or
        IntrospectionUnavailableError."""
        key = hashlib.sha256(token.encode()).hexdigest()
        now = time.monotonic()
        if self.settings.cache_seconds > 0:
            cached = self._cache.get(key)
            if cached is not None and cached[1] > now:
                INTROSPECTIONS.labels(self.server, "cached").inc()
                return cached[0]
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout_seconds, transport=self._transport) as client:
                response = await client.post(
                    self.settings.url,
                    data={"token": token, "token_type_hint": "access_token"},
                    auth=(self.settings.client_id, self.settings.client_secret),
                )
            status = response.status_code
            body = response.json() if status == 200 else None
        except (httpx.HTTPError, ValueError) as exc:
            INTROSPECTIONS.labels(self.server, "unavailable").inc()
            raise IntrospectionUnavailableError(f"Token check unavailable ({type(exc).__name__}).") from exc
        finally:
            INTROSPECTION_SECONDS.labels(self.server).observe(time.perf_counter() - started)
        if not isinstance(body, dict):
            INTROSPECTIONS.labels(self.server, "unavailable").inc()
            raise IntrospectionUnavailableError(f"Token check unavailable (HTTP {status}).")
        if body.get("active") is not True or body.get("sub") != subject:
            INTROSPECTIONS.labels(self.server, "inactive").inc()
            raise TokenInactiveError("The token is no longer active.")
        INTROSPECTIONS.labels(self.server, "active").inc()
        if self.settings.cache_seconds > 0:
            self._cache[key] = (body, now + self.settings.cache_seconds)
            while len(self._cache) > MAX_CACHED:
                self._cache.popitem(last=False)
        return body
