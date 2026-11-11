"""
- Title:    MCP door: a data product's governed data tools (docs/adr/0019, ADR 0007)
- Author:   Angel Martinez-Tenor

One FastMCP server serves every data product that declares `mcp:`, at `/mcp/{scenario_slug}`
(Streamable HTTP, stateless, JSON responses). `McpDoor`, mounted at `/mcp`, settles everything a
REST route's dependencies settle, in the same order and with the same code, before the MCP layer
runs: the data product (404 without a door), the caller (401/403 from the shared entitlement
check), then the caller's access (`identity.access_for`: role, row filter, masks, whose data; a
platform key may "view as" a role with the X-Data-View-As headers, as in the UI). The tools call
the same core functions as the REST routes with that access, so MCP and REST cannot drift apart
(ADR 0007), and the tenant and role never come from a tool argument.

Tools are listed per caller: the scenario's `mcp.expose`, minus `run_sql` for a role that may not
run SQL. Logs carry the tool name and outcome only, never arguments or results.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from ai_circus_shared.auth import AuthSettingsAdapter, Identity, TokenValidationError, resolve_caller_identity
from ai_circus_shared.data_policy import AccessDeniedError, DataAccess
from ai_circus_shared.entitlements import EntitlementDeniedError
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.http import StarletteWithLifespan
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.routing import get_route_path
from starlette.types import ASGIApp, Receive, Scope, Send

from data_api import get_env_config
from data_api.core import governed_sql, metrics, records
from data_api.core.identity import access_for
from data_api.core.logger import get_logger
from data_api.core.metrics import MetricError, MetricRequest
from data_api.core.products import DataProduct
from data_api.core.records import NotFoundError
from data_api.core.runtime import Runtime

logger = get_logger(__name__)
# FastMCP's own handler writes plain (rich) lines, outside LOG_FORMAT=json; ScopedTools logs each
# call's tool and outcome instead.
_fastmcp_logger = logging.getLogger("fastmcp")
_fastmcp_logger.handlers = [logging.NullHandler()]
_fastmcp_logger.propagate = False

_STATE_KEY = "mcp_caller"
_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_VIEW_AS_MAX = 64
_VIEW_AS_VALUES_MAX = 400


@dataclass(frozen=True)
class McpCaller:
    """The data product, the caller's access to it and the runtime the tools read through."""

    product: DataProduct
    access: DataAccess
    runtime: Runtime

    def tools(self) -> tuple[str, ...]:
        """The tools this caller may list and run: the door's, minus SQL for a role without it."""
        mcp = self.product.definition.mcp
        exposed = tuple(mcp.expose) if mcp is not None else ()
        may_sql = self.access.access.sql and self.access.row_filter_values is None
        return tuple(t for t in exposed if t != "run_sql" or may_sql)


def _caller() -> McpCaller:
    caller = getattr(get_http_request().state, _STATE_KEY, None)
    if not isinstance(caller, McpCaller):  # only reachable if the server is served without McpDoor
        raise ToolError("This MCP server is only reachable through /mcp/{scenario_slug}.")
    return caller


async def _governed(fn: Any, *args: Any) -> Any:
    """Run one core call in the threadpool; a policy or request error becomes the tool's error."""
    try:
        return await run_in_threadpool(fn, *args)
    except KeyError, IndexError:
        raise  # a bug, not a bad request: FastMCP masks it, so its text never reaches the client
    except (
        AccessDeniedError,
        NotFoundError,
        MetricError,
        governed_sql.GovernedSqlError,
        LookupError,
        ValueError,
    ) as exc:
        raise ToolError(str(exc)) from exc


class ScopedTools(Middleware):
    """Each caller lists and runs only the tools its door and role allow."""

    async def on_list_tools(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        """Drop the tools this caller may not use."""
        allowed = _caller().tools()
        return [tool for tool in await call_next(context) if tool.name in allowed]

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        """Refuse a tool this caller may not use; log each call's tool and outcome."""
        name = context.message.name
        if name not in _caller().tools():
            raise ToolError(f"Unknown tool: {name}")
        try:
            result = await call_next(context)
        except Exception as exc:
            logger.info("MCP tool {} failed: {}", name, type(exc).__name__)
            raise
        logger.info("MCP tool {} ok", name)
        return result


def build_mcp_server() -> FastMCP:
    """The governed data tools: each one is a REST route's core call, with the caller's access."""
    server = FastMCP(
        name="ai-circus-data",
        instructions=(
            "Governed access to one data product for your organisation and role. Call list_metrics first, "
            "prefer query_metric over SQL, and say which metric and period each number is. Results are "
            "already restricted to what your role may see. Money with format currency_cents is in cents."
        ),
        middleware=[ScopedTools()],
        mask_error_details=True,
    )

    @server.tool(annotations=_READ_ONLY)
    async def list_metrics() -> dict[str, Any]:
        """The governed metrics (name, definition, format, dimensions) and record entities of this data product."""
        caller = _caller()
        return {
            "metrics": await _governed(metrics.catalogue, caller.product, caller.access),
            "records": [
                {"entity": r.entity, "label": r.label, "summary": list(r.summary)}
                for r in caller.product.config.records
            ],
        }

    @server.tool(annotations=_READ_ONLY)
    async def query_metric(
        metric: Annotated[str, Field(max_length=64, description="A metric name from list_metrics.")],
        dims: Annotated[
            list[str] | None, Field(max_length=3, description="Dimensions to split by (the metric's own).")
        ] = None,
        period: Annotated[
            str, Field(max_length=20, description="e.g. today, last_7_days, last_30_days.")
        ] = "last_7_days",
        compare: Annotated[
            str, Field(max_length=30, description="none, previous_period or same_period_last_week.")
        ] = "none",
        filters: Annotated[
            dict[str, list[str]] | None, Field(max_length=4, description="Dimension -> allowed values.")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 20,
    ) -> dict[str, Any]:
        """Compute a governed metric for a period, optionally split, filtered and compared (change_pct)."""
        caller = _caller()
        request = MetricRequest(
            metric=metric,
            dimensions=tuple(dims or ())[:3],
            period=period,
            compare=compare,
            filters=tuple((k[:64], tuple(str(v)[:120] for v in values[:20])) for k, values in (filters or {}).items()),
            limit=limit,
        )
        today = datetime.now(UTC).date()
        return await _governed(
            metrics.query_metric, caller.runtime.warehouse, caller.product, caller.access, request, today
        )

    @server.tool(annotations=_READ_ONLY)
    async def search_records(
        entity: Annotated[str, Field(max_length=64, description="A record entity from list_metrics' records.")],
        query: Annotated[str, Field(max_length=100, description="Free text or a key prefix; empty = the latest.")] = "",
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> dict[str, Any]:
        """Search records as received (bronze) by text or key prefix, or list the latest ones."""
        caller = _caller()
        found = await _governed(
            records.list_records, caller.runtime.warehouse, caller.product, caller.access, entity, query or None, limit
        )
        return {"records": found}

    @server.tool(annotations=_READ_ONLY)
    async def get_record(
        entity: Annotated[str, Field(max_length=64, description="A record entity from list_metrics' records.")],
        key: Annotated[str, Field(min_length=1, max_length=128, description="The record's business key.")],
    ) -> dict[str, Any]:
        """One record by its key, as received, with its processing trail and any quarantine."""
        from data_api.api import publish_served  # the REST route's own event, so the live flow shows it

        caller = _caller()
        record = await _governed(
            records.get_record, caller.runtime.warehouse, caller.product, caller.access, entity, key
        )
        publish_served(caller.runtime, caller.product, caller.access, entity, key, record)
        return record

    @server.tool(annotations=_READ_ONLY)
    async def data_health() -> dict[str, Any]:
        """Freshness of every dataset vs its SLO, the last transform run, ops health, and the caller's role."""
        caller = _caller()
        warehouse, product, access = caller.runtime.warehouse, caller.product, caller.access
        return {
            "access": access.describe(),
            "datasets": await _governed(records.datasets, warehouse, product, access),
            "transform": await _governed(records.last_transform, warehouse, product),
            "ops": await _governed(records.ops_summary, warehouse, product, access),
        }

    @server.tool(annotations=_READ_ONLY)
    async def run_sql(
        sql: Annotated[str, Field(max_length=governed_sql.MAX_SQL_LENGTH, description="One read-only SELECT.")],
    ) -> dict[str, Any]:
        """Governed read-only SQL over the curated dataset names, for questions no metric answers."""
        caller = _caller()
        return await _governed(governed_sql.run, caller.runtime.warehouse, caller.product, caller.access, sql)

    return server


def build_mcp_app() -> StarletteWithLifespan:
    """The MCP endpoint at the root of its mount; run its lifespan inside the FastAPI app's."""
    # Host/Origin checks off: every request needs a bearer token (McpDoor) and browsers are held
    # to CORS_ALLOWED_ORIGINS by the app's own CORS middleware.
    return build_mcp_server().http_app(path="/", stateless_http=True, json_response=True, host_origin_protection=False)


def _identity(authorization: str | None, scenario_slug: str) -> Identity:
    settings = AuthSettingsAdapter.from_config(get_env_config())
    return resolve_caller_identity(authorization=authorization, scenario_slug=scenario_slug, settings=settings)


async def _refuse(scope: Scope, receive: Receive, send: Send, status: int, detail: str) -> None:
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    await JSONResponse({"detail": detail}, status_code=status, headers=headers)(scope, receive, send)


class McpDoor:
    """ASGI app mounted at `/mcp`: `/{scenario_slug}[/…]` → product, caller, access → the MCP app."""

    def __init__(self, mcp_app: ASGIApp) -> None:
        """Wrap the MCP app that serves every door."""
        self.mcp_app = mcp_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Settle product, caller and access, then hand the request to the MCP app at the door's root."""
        if scope["type"] != "http":
            await self.mcp_app(scope, receive, send)
            return
        runtime: Runtime | None = getattr(scope["app"].state, "runtime", None)
        if runtime is None:
            await _refuse(scope, receive, send, 503, "data-api is still starting.")
            return
        slug, _, rest = get_route_path(scope).lstrip("/").partition("/")
        product = runtime.products.get(slug)
        if product is None or product.definition.mcp is None or not product.definition.mcp.expose:
            await _refuse(scope, receive, send, 404, "No MCP door for this data product.")
            return
        headers = Headers(scope=scope)
        authorization = headers.get("authorization")
        view_as = headers.get("x-data-view-as")
        view_as_values = headers.get("x-data-view-as-values")
        if len(view_as or "") > _VIEW_AS_MAX or len(view_as_values or "") > _VIEW_AS_VALUES_MAX:
            await _refuse(scope, receive, send, 422, "X-Data-View-As header too long.")
            return
        try:
            identity = await run_in_threadpool(_identity, authorization, slug)
            access = await run_in_threadpool(
                access_for, runtime.warehouse, product, identity, authorization, view_as, view_as_values
            )
        except TokenValidationError as exc:
            await _refuse(scope, receive, send, 401, str(exc))
            return
        except (EntitlementDeniedError, AccessDeniedError) as exc:
            await _refuse(scope, receive, send, 403, str(exc))
            return
        scope.setdefault("state", {})[_STATE_KEY] = McpCaller(product=product, access=access, runtime=runtime)
        root_path = f"{scope.get('root_path', '')}/{slug}"
        await self.mcp_app({**scope, "root_path": root_path, "path": f"{root_path}/{rest}"}, receive, send)
