"""
- Title:    data-ingest HTTP surface (internal)
- Author:   Angel Martinez-Tenor

Health for the probes, plus a status report and a landing-scan trigger for data-api and the
admin Platform view. Not routed by Traefik; the two non-probe routes still require the shared
ADMIN_API_KEY bearer, like data-platform-manager's.
"""

from __future__ import annotations

from typing import Any

from ai_circus_shared.auth import is_admin_bearer_token
from ai_circus_shared.data_product import bronze_table
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import text

from data_ingest import get_env_config
from data_ingest.core.runtime import Runtime

router = APIRouter()


def require_admin(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency: 401 unless the caller presents the shared ADMIN_API_KEY bearer."""
    if not is_admin_bearer_token(authorization, get_env_config().ADMIN_API_KEY.get_secret_value()):
        raise HTTPException(status_code=401, detail="Admin bearer token required.")


def get_runtime(request: Request) -> Runtime:
    """FastAPI dependency: the started runtime, or 503 while the lifespan is still bootstrapping."""
    runtime: Runtime | None = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="data-ingest is still starting.")
    return runtime


@router.get("/healthz")
def healthz(request: Request) -> dict[str, Any]:
    """Liveness/readiness: 503 until the runtime exists and while any worker thread has died."""
    runtime: Runtime | None = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="starting")
    workers = runtime.alive()
    if not all(workers.values()):
        raise HTTPException(status_code=503, detail=f"worker stopped: {workers}")
    return {"status": "ok", "workers": workers}


@router.get("/status", dependencies=[Depends(require_admin)])
def status(runtime: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    """Per data product: bronze row counts and last arrival, open quarantine, file reconciliation, worker counters."""
    products: dict[str, Any] = {}
    with runtime.warehouse.connect() as conn:
        for slug, product in runtime.products.items():
            bronze = {
                source.id: conn.execute(
                    text(
                        f"SELECT count(*), max(received_at), count(*) FILTER (WHERE archived_at IS NULL) "
                        f'FROM "{slug}"."{bronze_table(source.id)}"'
                    )
                ).one()
                for source in product.config.kafka_sources()
            }
            quarantine = conn.execute(
                text(f"SELECT count(*) FILTER (WHERE status = 'open'), count(*) FROM \"{slug}\".ops_quarantine")
            ).one()
            deliveries = dict(
                conn.execute(text(f'SELECT status, count(*) FROM "{slug}".ops_file_deliveries GROUP BY status')).all()
            )
            products[slug] = {
                "bronze": {
                    k: {"rows": v[0], "last_received_at": v[1], "not_archived": v[2]} for k, v in bronze.items()
                },
                "quarantine": {"open": quarantine[0], "total": quarantine[1]},
                "file_deliveries": deliveries,
            }
    return {
        "workers": runtime.alive(),
        "stream": {k: dict(v) for k, v in runtime.stream.stats.items()},
        "cdc": {"captured": runtime.cdc.captured},
        "lake": {"archived": runtime.lake.archived, "last_error": runtime.lake.last_error},
        "products": products,
    }


@router.post("/landing/scan", status_code=202, dependencies=[Depends(require_admin)])
def scan_landing(runtime: Runtime = Depends(get_runtime)) -> dict[str, str]:
    """Wake the landing worker now instead of at its next interval."""
    runtime.files.trigger()
    return {"status": "scan requested"}
