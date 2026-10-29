"""
- Title:    data-api routes — the governed door of every data product
- Author:   Angel Martinez-Tenor

Every `/v1/{scenario_slug}/…` route resolves the caller through `get_access` (entitlement,
then the data product's role / row filter / masks — ai_circus_shared.data_policy) and only then
touches data, always scoped to the access's `org_id`. Blocking work runs in FastAPI's
threadpool (plain `def` routes); the one async route is the SSE stream.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Annotated, Any

from ai_circus_shared.data_policy import AccessDeniedError, DataAccess
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from data_api.core import governed_sql, lineage, metrics, records
from data_api.core.identity import get_access, get_product
from data_api.core.metrics import MetricError, MetricRequest
from data_api.core.products import DataProduct
from data_api.core.records import NotFoundError
from data_api.core.runtime import Runtime, describe_product

router = APIRouter()
SSE_MAX_SECONDS = 30 * 60


def runtime_of(request: Request) -> Runtime:
    """The started runtime, or 503 while the lifespan is still connecting."""
    runtime: Runtime | None = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="data-api is still starting.")
    return runtime


def _guard(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except AccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (MetricError, governed_sql.GovernedSqlError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/healthz")
def healthz(request: Request) -> dict[str, Any]:
    """Liveness/readiness: the data products served and the marts engine."""
    runtime = runtime_of(request)
    return {"status": "ok", "products": sorted(runtime.products), "marts_engine": runtime.warehouse.marts_engine}


@router.get("/v1/{scenario_slug}/overview")
def overview(
    request: Request, product: DataProduct = Depends(get_product), access: DataAccess = Depends(get_access)
) -> dict[str, Any]:
    """Everything the workspace header needs at once.

    Access, live tile, ops health, last transform, simulator status and the tiers that are up.
    """
    runtime = runtime_of(request)
    hot = None
    if product.config.hot_path:
        try:
            hot = records.hot(runtime.warehouse, product, access, product.config.hot_path[0].name, 60)
        except AccessDeniedError:
            hot = None
    config = product.config
    return {
        "access": access.describe(),
        "roles": [{"name": name, "label": role.label, "rows": role.rows} for name, role in config.access.roles.items()],
        "row_filter": config.access.row_filter.model_dump() if config.access.row_filter else None,
        "row_filter_options": records.row_filter_options(runtime.warehouse, product, access),
        "product": describe_product(product),
        "hot": hot,
        "ops": records.ops_summary(runtime.warehouse, product, access),
        "capture": records.capture_stats(runtime.warehouse, product, access),
        "transform": records.last_transform(runtime.warehouse, product),
        "simulator": runtime.simulation.status(product, access.org_id) if access.can_simulate else None,
        "tiers": runtime.tiers(),
        "marts_engine": runtime.warehouse.marts_engine,
        "now": datetime.now(UTC).isoformat(),
    }


@router.get("/v1/{scenario_slug}/hot/{name}")
def hot(
    name: str,
    request: Request,
    minutes: int = Query(60, ge=5, le=1440),
    product: DataProduct = Depends(get_product),
    access: DataAccess = Depends(get_access),
) -> dict[str, Any]:
    """One hot-path aggregate: per-minute series and today's totals (approximate, seconds old)."""
    return _guard(records.hot, runtime_of(request).warehouse, product, access, name, minutes)


@router.get("/v1/{scenario_slug}/metrics")
def metric_catalogue(
    product: DataProduct = Depends(get_product), access: DataAccess = Depends(get_access)
) -> list[dict[str, Any]]:
    """The governed metrics the caller's role can query."""
    return metrics.catalogue(product, access)


@router.get("/v1/{scenario_slug}/metrics/{metric}")
def metric(
    metric: str,
    request: Request,
    dims: str = Query("", max_length=200),
    period: str = Query("last_7_days", max_length=20),
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
    compare: str = Query("none", max_length=30),
    filters: str = Query("", max_length=500),
    limit: int = Query(50, ge=1, le=2000),
    order: str = Query("value_desc", pattern="^(value_desc|value_asc|dimension)$"),
    product: DataProduct = Depends(get_product),
    access: DataAccess = Depends(get_access),
) -> dict[str, Any]:
    """One governed metric over a period, split by declared dimensions, optionally compared."""
    req = MetricRequest(
        metric=metric,
        dimensions=tuple(d.strip() for d in dims.split(",") if d.strip())[:4],
        period=period,
        start=from_,
        end=to,
        compare=compare,
        filters=tuple((k, tuple(v)) for k, v in metrics.parse_filters(filters).items()),
        limit=limit,
        order=order,  # type: ignore[arg-type]
    )
    return _guard(metrics.query_metric, runtime_of(request).warehouse, product, access, req, datetime.now(UTC).date())


@router.get("/v1/{scenario_slug}/datasets")
def datasets(
    request: Request, product: DataProduct = Depends(get_product), access: DataAccess = Depends(get_access)
) -> list[dict[str, Any]]:
    """The curated datasets the role may browse, with row counts and freshness vs their SLO."""
    return _guard(records.datasets, runtime_of(request).warehouse, product, access)


@router.get("/v1/{scenario_slug}/datasets/{name}/rows")
def dataset_rows(
    name: str,
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    where: list[str] = Query([], max_length=4, description="column=value equality filters (columns of the dataset)"),
    product: DataProduct = Depends(get_product),
    access: DataAccess = Depends(get_access),
) -> dict[str, Any]:
    """The latest rows of one dataset, filtered and masked for the role."""
    equals: dict[str, str] = {}
    for item in where:
        column, sep, value = item.partition("=")
        if not sep or len(value) > 120:
            raise HTTPException(status_code=422, detail=f"where must be column=value: {item[:40]!r}")
        equals[column] = value
    return _guard(records.dataset_rows, runtime_of(request).warehouse, product, access, name, limit, equals)


@router.get("/v1/{scenario_slug}/records/{entity}")
def list_records(
    entity: str,
    request: Request,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(20, ge=1, le=100),
    product: DataProduct = Depends(get_product),
    access: DataAccess = Depends(get_access),
) -> list[dict[str, Any]]:
    """Search records as received (bronze) by text or key prefix, or list the latest."""
    return _guard(records.list_records, runtime_of(request).warehouse, product, access, entity, q, limit)


@router.get("/v1/{scenario_slug}/records/{entity}/{key}")
def get_record(
    entity: str,
    key: str,
    request: Request,
    product: DataProduct = Depends(get_product),
    access: DataAccess = Depends(get_access),
) -> dict[str, Any]:
    """One record with its versions and processing trail; also reports a 'served' stage event."""
    if len(key) > 128:
        raise HTTPException(status_code=422, detail="Key too long.")
    runtime = runtime_of(request)
    record = _guard(records.get_record, runtime.warehouse, product, access, entity, key)
    current = record.get("current") or {}
    filter_value = (current.get("payload") or {}).get(access.row_filter_column) if access.row_filter_column else None
    runtime.relay.publish_local(
        access.org_id,
        {
            "slug": product.slug,
            "stage": "served",
            "status": "ok",
            "entity": entity,
            "record_id": key,
            "source": "data-api",
            "ts": datetime.now(UTC).isoformat(),
            "latency_ms": None,
            "filter": filter_value,
            "detail": {"version": current.get("version", 1)},
        },
    )
    return record


@router.get("/v1/{scenario_slug}/lineage")
def get_lineage(
    request: Request, product: DataProduct = Depends(get_product), access: DataAccess = Depends(get_access)
) -> dict[str, Any]:
    """Sources → bronze → silver → gold → metrics (structure only: no data, any role)."""
    return lineage.build(product, runtime_of(request).store)


@router.get("/v1/{scenario_slug}/transform/runs")
def transform_runs(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    product: DataProduct = Depends(get_product),
    access: DataAccess = Depends(get_access),
) -> list[dict[str, Any]]:
    """The latest data-transform runs (ops layer only)."""
    _guard(access.require_layer, "ops")
    return runtime_of(request).warehouse.transform_runs(product, limit)


class SqlIn(BaseModel):
    """A governed SQL request."""

    sql: str = Field(max_length=governed_sql.MAX_SQL_LENGTH)


@router.post("/v1/{scenario_slug}/query")
def query(
    body: SqlIn, request: Request, product: DataProduct = Depends(get_product), access: DataAccess = Depends(get_access)
) -> dict[str, Any]:
    """Run one governed, read-only SELECT over the curated datasets (SQL roles only)."""
    return _guard(governed_sql.run, runtime_of(request).warehouse, product, access, body.sql)


@router.get("/v1/{scenario_slug}/quarantine")
def quarantine(
    request: Request,
    status: str = Query("open", pattern="^(open|replayed|discarded)$"),
    limit: int = Query(50, ge=1, le=200),
    product: DataProduct = Depends(get_product),
    access: DataAccess = Depends(get_access),
) -> list[dict[str, Any]]:
    """Quarantined records of one status (quarantine layer only)."""
    return _guard(records.quarantine_list, runtime_of(request).warehouse, product, access, status, limit)


class ReplayIn(BaseModel):
    """How to resolve a quarantined record: replay it (optionally fixed) or discard it."""

    payload: dict[str, Any] | None = None  # the steward's fixed record; None = replay as quarantined
    discard: bool = False


@router.post("/v1/{scenario_slug}/quarantine/{quarantine_id}")
def resolve_quarantined(
    quarantine_id: int,
    body: ReplayIn,
    request: Request,
    product: DataProduct = Depends(get_product),
    access: DataAccess = Depends(get_access),
) -> dict[str, Any]:
    """Replay a quarantined record through the pipeline (optionally fixed), or discard it."""
    _guard(access.require_layer, "quarantine")
    if not (access.is_platform or access.org_id == access.caller_org_id) or access.row_filter_values is not None:
        raise HTTPException(status_code=403, detail="Only a data steward of this tenant may resolve quarantine.")
    if len(json.dumps(body.payload or {})) > 200_000:
        raise HTTPException(status_code=413, detail="Payload too large.")
    runtime = runtime_of(request)
    with runtime.warehouse.engine.begin() as conn:
        row = conn.execute(
            text(
                f'SELECT source_id, payload FROM "{product.slug}".ops_quarantine '
                "WHERE quarantine_id = :id AND org_id = :org AND status = 'open' FOR UPDATE"
            ),
            {"id": quarantine_id, "org": access.org_id},
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="No open quarantined record with that id.")
        status = "discarded" if body.discard else "replayed"
        if not body.discard:
            payload = body.payload if body.payload is not None else row[1]
            if not isinstance(payload, dict):
                raise HTTPException(status_code=422, detail="Nothing to replay: the record was not a JSON object.")
            sim = runtime.simulation.tenant(product, access.org_id)
            sim.ctx.records.publish(row[0], payload)
            sim.ctx.records.flush()
        conn.execute(
            text(f'UPDATE "{product.slug}".ops_quarantine SET status = :s WHERE quarantine_id = :id'),
            {"s": status, "id": quarantine_id},
        )
    return {"quarantine_id": quarantine_id, "status": status}


class ActionItem(BaseModel):
    """One line of an `items` parameter (e.g. a web-shop cart line)."""

    id: str = Field(min_length=1, max_length=32)
    qty: int = Field(default=1, ge=1, le=50)


class ActionIn(BaseModel):
    """Parameters of one simulator action (only the action's declared ones are used)."""

    params: dict[str, Annotated[str, Field(max_length=120)] | int | bool | list[ActionItem] | None] = Field(
        default_factory=dict, max_length=20
    )


def _require_simulate(access: DataAccess) -> None:
    if not access.can_simulate:
        raise HTTPException(
            status_code=403,
            detail="Simulation writes data: only the platform admin, or a tenant "
            "on its own data product, may run it (never while viewing as).",
        )


@router.post("/v1/{scenario_slug}/simulator/actions/{action}")
def simulate(
    action: str,
    body: ActionIn,
    request: Request,
    product: DataProduct = Depends(get_product),
    access: DataAccess = Depends(get_access),
) -> dict[str, Any]:
    """Run one declared simulator action in the caller's data org."""
    _require_simulate(access)
    params = {k: [i.model_dump() for i in v] if isinstance(v, list) else v for k, v in body.params.items()}
    result = _guard(runtime_of(request).simulation.act, product, access.org_id, action, params)
    return result


class BackfillIn(BaseModel):
    """How many days of history to generate (default: the scenario's history_days)."""

    days: int | None = Field(default=None, ge=1, le=2000)


@router.post("/v1/{scenario_slug}/simulator/backfill", status_code=202)
def backfill(
    body: BackfillIn,
    request: Request,
    product: DataProduct = Depends(get_product),
    access: DataAccess = Depends(get_access),
) -> dict[str, Any]:
    """Start generating history through the real pipeline (background; progress in /overview)."""
    _require_simulate(access)
    return _guard(runtime_of(request).simulation.start_backfill, product, access.org_id, body.days)


class LiveIn(BaseModel):
    """Turn live trading on (for a bounded time) or off."""

    on: bool
    minutes: int = Field(default=30, ge=1, le=240)


@router.post("/v1/{scenario_slug}/simulator/live")
def live(
    body: LiveIn,
    request: Request,
    product: DataProduct = Depends(get_product),
    access: DataAccess = Depends(get_access),
) -> dict[str, Any]:
    """Start or stop the live simulator trickle."""
    _require_simulate(access)
    return _guard(runtime_of(request).simulation.set_live, product, access.org_id, body.on, body.minutes)


@router.get("/v1/{scenario_slug}/events")
def recent_events(
    request: Request,
    limit: int = Query(100, ge=1, le=300),
    product: DataProduct = Depends(get_product),
    access: DataAccess = Depends(get_access),
) -> list[dict[str, Any]]:
    """The latest pipeline events of this tenant and data product (the live view's first paint)."""
    return _visible_events(access, runtime_of(request).relay.recent(access.org_id, product.slug, limit))


def _visible_events(access: DataAccess, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Stage events carry ids, never payloads; a filtered role still only sees its own rows'
    # (data-ingest stamps each event with the record's row-filter value; batches have none).
    if access.row_filter_values is None:
        return events
    return [e for e in events if e.get("filter") in access.row_filter_values]


@router.get("/v1/{scenario_slug}/events/stream")
async def stream_events(
    request: Request, product: DataProduct = Depends(get_product), access: DataAccess = Depends(get_access)
) -> StreamingResponse:
    """Pipeline events as server-sent events, filtered for the role, bounded in time."""
    relay = runtime_of(request).relay
    try:
        sub = relay.subscribe(access.org_id, product.slug)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    async def body() -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SSE_MAX_SECONDS
        try:
            yield ": connected\n\n"
            while loop.time() < deadline and not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(sub.queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                for visible in _visible_events(access, [event]):
                    yield f"data: {json.dumps(visible)}\n\n"
        finally:
            relay.unsubscribe(sub)

    return StreamingResponse(
        body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
