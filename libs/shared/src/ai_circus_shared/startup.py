"""Wait for a backing dependency at service start-up instead of crash-looping.

Every service connects to its dependencies (Postgres, SeaweedFS, llm-gateway) in its
FastAPI lifespan. On a cold boot — `make k3s-resume`, a fresh `k3s-up`, a laptop that
just woke up — those dependencies come up in parallel with the services, so a
first-attempt "connection refused" is the normal case, not a failure. Crashing on it
hands the pod to Kubernetes' CrashLoopBackOff (10s, 20s, 40s … between attempts),
which is what made rag-agent/form-agent take ~50s and two restarts to become Ready.
Polling here instead makes a service Ready within about a second of its dependency.

The ceiling (`DEPENDENCY_WAIT_SECONDS`, default 120s) is kept below the k8s
`startupProbe` window in k8s/base, so a dependency that truly never comes up still
fails the pod loudly rather than hanging it forever.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

_INITIAL_DELAY_SECONDS = 0.5
_MAX_DELAY_SECONDS = 5.0


def dependency_wait_seconds() -> float:
    """How long `wait_for` keeps retrying — `DEPENDENCY_WAIT_SECONDS` env var, default 120."""
    return float(os.getenv("DEPENDENCY_WAIT_SECONDS", "120"))


def wait_for[T](
    attempt: Callable[[], T],
    *,
    what: str,
    retryable: Callable[[Exception], bool],
    timeout_seconds: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call `attempt` until it succeeds, retrying (with capped exponential backoff) only
    errors `retryable` accepts — anything else (bad credentials, a 4xx) is a real
    misconfiguration and is raised immediately. Re-raises the last retryable error
    once `timeout_seconds` (default: `dependency_wait_seconds()`) has elapsed.
    """
    deadline = time.monotonic() + (dependency_wait_seconds() if timeout_seconds is None else timeout_seconds)
    delay = _INITIAL_DELAY_SECONDS
    while True:
        try:
            return attempt()
        except Exception as exc:
            if not retryable(exc) or time.monotonic() + delay > deadline:
                raise
            logger.warning("Waiting for %s (%s: %s) — retrying in %.1fs", what, type(exc).__name__, exc, delay)
            sleep(delay)
            delay = min(delay * 2, _MAX_DELAY_SECONDS)
