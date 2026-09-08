"""
- Title:    Process-wide Valkey/Redis client
- Author:   ai-circus-framework contributors

ai_circus_shared.cache.connect() is a plain factory (it has no module-global
state of its own, unlike ai_circus_shared.document_store's engine) — each
consuming service holds onto the one client it creates at startup, exactly as
that module's own docstring says to. Lazily initialized in app.py's lifespan,
not at import time, so importing this module never requires a real Valkey
connection (or even a fully configured EnvConfig) to be available — same
"init once at startup, not at import" shape as
ai_circus_shared.document_store.init_engine/get_session.
"""

from __future__ import annotations

import redis
from ai_circus_shared.cache import CacheConfig, connect

_client: redis.Redis | None = None


def init_client(config: CacheConfig) -> redis.Redis:
    """Create the process-wide Redis-protocol client. Call once, at startup."""
    global _client
    _client = connect(config)
    return _client


def get_client() -> redis.Redis:
    """FastAPI dependency: the initialized process-wide client."""
    if _client is None:
        raise RuntimeError("Cache client not initialized — call init_client() at startup first.")
    return _client
