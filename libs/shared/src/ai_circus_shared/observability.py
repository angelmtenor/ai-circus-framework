"""Prometheus metrics wiring shared by every FastAPI service.

Each `tabular_ml`/`conversational_rag`/`assisted_form` FastAPI service (prediction,
assistant, rag-agent, form-agent, platform-registry) calls `configure_metrics(app)`
once, right after constructing its `FastAPI` instance, to expose a `/metrics` endpoint
next to the existing `/healthz` route — no per-service boilerplate, no OpenTelemetry
collector to run locally. Uses `prometheus-fastapi-instrumentator`, which auto-tracks
request count/latency/in-progress gauges by method+path+status and needs no further
configuration for that baseline; add custom metrics at the call site if a service
later needs them.
"""

from __future__ import annotations

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator


def configure_metrics(app: FastAPI) -> None:
    """Instrument `app` and expose Prometheus-format metrics at `/metrics`.

    Idempotent per `app` instance — call once, immediately after `FastAPI(...)` is
    constructed, before any middleware/routers that should themselves be measured are
    added (the instrumentator wraps whatever is already on the app at expose-time).
    """
    Instrumentator().instrument(app).expose(app, include_in_schema=False)
