"""
- Title:    Tenant-scoped cache / key-value store (Redis-protocol)
- Author:   ai-circus-framework contributors

The unified data layer's low-latency companion to ai_circus_shared.storage's
durable object store: session state, rate-limit counters, and anything else a
service would rather not round-trip through Postgres or SeaweedFS for. Points at
any Redis-protocol server — this repo's own docker-compose/k8s manifests run
Valkey (the BSD-3-licensed, Linux-Foundation-governed fork), not Redis itself,
since Redis Inc. re-licensed the Redis server away from an OSI-approved license
in 2024; the RESP wire protocol — and this module's `redis-py` client — don't
care which server is on the other end, so swapping back to Redis or to another
RESP-compatible server later is a one-line endpoint change, not a rewrite.

Every key is tenant-prefixed the same way ai_circus_shared.storage prefixes
object keys, so one tenant's cache entries can never collide with — or be
enumerated by — another's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

import redis

# Mirrors ai_circus_shared.storage's _SAFE_ORG_ID — see that module's docstring
# for why this guard exists even though every current call site already passes a
# validated `Identity.org_id`, never raw client input.
_SAFE_ORG_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class CacheConfig(Protocol):
    """Shape a service's own EnvConfig must satisfy to call connect() — one full
    connection URL, same convention as every service's own QDRANT_URL (a
    `docker`-profile default of `redis://valkey:6379`, a `local`-profile default
    of `redis://localhost:6379`, per that service's settings.yaml).
    """

    CACHE_URL: str


def connect(config: CacheConfig) -> redis.Redis:
    """Create the process-wide Redis-protocol client. Call once, at startup."""
    return redis.Redis.from_url(config.CACHE_URL, decode_responses=True)


@dataclass(frozen=True)
class TenantCache:
    """A cache client bound to one process-wide connection, tenant-scoping every key."""

    _client: redis.Redis

    def _key(self, tenant_org_id: str, key: str) -> str:
        if not _SAFE_ORG_ID.match(tenant_org_id):
            raise ValueError(f"Invalid tenant_org_id {tenant_org_id!r}: must match {_SAFE_ORG_ID.pattern}.")
        return f"tenant-{tenant_org_id}:{key}"

    def set(self, tenant_org_id: str, key: str, value: str, ttl_seconds: int | None = None) -> None:
        """Set a value, optionally expiring after ttl_seconds."""
        self._client.set(self._key(tenant_org_id, key), value, ex=ttl_seconds)

    def get(self, tenant_org_id: str, key: str) -> str | None:
        """None if the key doesn't exist (or already expired) for this tenant."""
        return self._client.get(self._key(tenant_org_id, key))

    def delete(self, tenant_org_id: str, key: str) -> bool:
        """True if a key existed and was removed."""
        return self._client.delete(self._key(tenant_org_id, key)) > 0

    def incr(self, tenant_org_id: str, key: str, ttl_seconds: int | None = None) -> int:
        """Atomically increment a counter and return its new value — the
        rate-limit-window idiom. `ttl_seconds` is applied with NX semantics (only
        the call that creates the key sets its expiry), so a burst of concurrent
        callers can never reset another caller's window mid-count.
        """
        full_key = self._key(tenant_org_id, key)
        pipe = self._client.pipeline()
        pipe.incr(full_key)
        if ttl_seconds is not None:
            pipe.expire(full_key, ttl_seconds, nx=True)
        results = pipe.execute()
        return int(results[0])

    def incr_by_float(self, tenant_org_id: str, key: str, amount: float, ttl_seconds: int | None = None) -> float:
        """Same idiom as incr(), generalized to a non-integer amount (e.g. a dollar
        cost rather than a request count) via Redis's INCRBYFLOAT.
        """
        full_key = self._key(tenant_org_id, key)
        pipe = self._client.pipeline()
        pipe.incrbyfloat(full_key, amount)
        if ttl_seconds is not None:
            pipe.expire(full_key, ttl_seconds, nx=True)
        results = pipe.execute()
        return float(results[0])
