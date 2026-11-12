"""
exchange.py
-----------

The per-API token (T2): the caller's token (T1) exchanged at the identity provider (RFC 8693) for a
token whose only audience is this server's API, for the same user (rules R4, R5). The identity
provider decides which audience this server's client may ask for; asking for another fails.

At Level 3 (docs/adr/0025) the exchange carries a DPoP proof of the server's own key, and a T2 that the
identity provider did not bind to that key is refused: it would be a plain bearer token again.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass

import httpx

from mcp_kit.dpop import DpopKey, bound_thumbprint
from mcp_kit.telemetry import TOKEN_EXCHANGE_SECONDS, TOKEN_EXCHANGES

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
# Never reuse a T2 this close to its expiry.
EXPIRY_MARGIN_SECONDS = 30
MAX_CACHED_TOKENS = 1024


class ExchangeRefusedError(Exception):
    """The identity provider refused the exchange (wrong audience, revoked or expired T1, …)."""


@dataclass(frozen=True)
class ExchangeSettings:
    """Where and as whom to exchange, and for which API."""

    token_url: str
    client_id: str
    client_secret: str
    api_audience: str
    # Scopes the T2 must carry, e.g. "organization" so the API sees the tenant.
    scope: str = "organization"
    timeout_seconds: float = 10.0
    # Level 3: the key the T2 must be bound to (proof of possession). None = a bearer T2 (Level 2).
    dpop_key: DpopKey | None = None


class TokenExchanger:
    """Exchanges T1 for T2 and caches T2 per T1 until shortly before it expires (bounded)."""

    def __init__(self, settings: ExchangeSettings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """`transport` is for tests; production uses the default HTTP transport."""
        self.settings = settings
        self._transport = transport
        # The metrics label; McpKit sets it to the server's name.
        self.server = "mcp-server"
        self._cache: OrderedDict[str, tuple[str, float]] = OrderedDict()

    @staticmethod
    def _key(subject_token: str) -> str:
        return hashlib.sha256(subject_token.encode()).hexdigest()

    async def exchange(self, subject_token: str) -> str:
        """A T2 for this API, for the user T1 names. Raises ExchangeRefusedError."""
        cache_key = self._key(subject_token)
        cached = self._cache.get(cache_key)
        now = time.time()
        if cached is not None and cached[1] - EXPIRY_MARGIN_SECONDS > now:
            self._cache.move_to_end(cache_key)
            TOKEN_EXCHANGES.labels(self.server, "cached").inc()
            return cached[0]
        data = {
            "grant_type": TOKEN_EXCHANGE_GRANT,
            "subject_token": subject_token,
            "subject_token_type": ACCESS_TOKEN_TYPE,
            "requested_token_type": ACCESS_TOKEN_TYPE,
            "audience": self.settings.api_audience,
        }
        if self.settings.scope:
            data["scope"] = self.settings.scope
        key = self.settings.dpop_key
        headers = {"DPoP": key.proof("POST", self.settings.token_url)} if key is not None else {}
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.settings.timeout_seconds, transport=self._transport) as client:
            response = await client.post(
                self.settings.token_url,
                data=data,
                headers=headers,
                auth=(self.settings.client_id, self.settings.client_secret),
            )
        TOKEN_EXCHANGE_SECONDS.labels(self.server).observe(time.perf_counter() - started)
        if response.status_code != 200:
            TOKEN_EXCHANGES.labels(self.server, "refused").inc()
            # The provider's error code only (e.g. invalid_request, access_denied): never the token.
            code = response.json().get("error", "error") if "json" in response.headers.get("content-type", "") else ""
            raise ExchangeRefusedError(f"Token exchange refused ({response.status_code} {code}).")
        body = response.json()
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            TOKEN_EXCHANGES.labels(self.server, "refused").inc()
            raise ExchangeRefusedError("Token exchange returned no access token.")
        if key is not None and (
            str(body.get("token_type", "")).lower() != "dpop" or bound_thumbprint(token) != key.thumbprint
        ):
            TOKEN_EXCHANGES.labels(self.server, "refused").inc()
            raise ExchangeRefusedError("Token exchange returned a token not bound to this server's key.")
        TOKEN_EXCHANGES.labels(self.server, "issued").inc()
        expires_at = now + float(body.get("expires_in") or 60)
        self._cache[cache_key] = (token, expires_at)
        while len(self._cache) > MAX_CACHED_TOKENS:
            self._cache.popitem(last=False)
        return token
