"""The data MCP door (docs/adr/0019): the same access policy as REST, settled before any MCP handling."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from ai_circus_shared.auth import Identity, TokenValidationError
from ai_circus_shared.entitlements import EntitlementDeniedError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from data_api.core import identity, mcp_door
from data_api.core.events_relay import EventsRelay
from data_api.core.mcp_door import McpDoor, build_mcp_app
from tests.conftest_products import retail
from tests.fakes import FakeWarehouse

HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
EVERY_TOOL = {"list_metrics", "query_metric", "search_records", "get_record", "data_health", "run_sql"}

# bearer → (identity, is a platform key)
CALLERS = {
    "Bearer platform": (Identity(subject="admin", org_id="acme", roles=frozenset()), True),
    "Bearer manager": (
        Identity(
            subject="m", org_id="acme", roles=frozenset({"retail_store_manager"}), attributes=(("stores", ("S012",)),)
        ),
        False,
    ),
    "Bearer analyst": (Identity(subject="a", org_id="acme", roles=frozenset({"retail_analyst"})), False),
}


def _fake_identity(authorization: str | None, scenario_slug: str) -> Identity:
    if authorization == "Bearer denied":
        raise EntitlementDeniedError(f"Not entitled to {scenario_slug}.")
    if authorization not in CALLERS:
        raise TokenValidationError("Missing or invalid bearer token.")
    return CALLERS[authorization][0]


def handler(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows shaped like the warehouse's for the queries these tools make (as tests/test_api.py)."""
    if "count(*) FILTER" in sql:
        return [{"open": 1, "today": 1}]
    if "count(*) AS n" in sql:
        return [{"n": 10, "latest": "2026-10-28"}]
    if "AS value" in sql:
        return [{"value": 100}]
    return []


@pytest.fixture
def warehouse() -> FakeWarehouse:
    return FakeWarehouse(handler)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, warehouse: FakeWarehouse) -> Iterator[TestClient]:
    monkeypatch.setattr(mcp_door, "_identity", _fake_identity)
    # The real access_for and resolve_access run; only "is this a platform key" and "whose data" are stubbed.
    monkeypatch.setattr(identity, "is_platform_caller", lambda auth: auth in CALLERS and CALLERS[auth][1])
    monkeypatch.setattr(identity, "data_org", lambda _w, _p, org_id: org_id)
    mcp_app = build_mcp_app()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        async with mcp_app.lifespan(mcp_app):
            yield

    app = FastAPI(lifespan=lifespan)
    product = retail()
    no_door = SimpleNamespace(slug="no_door", definition=SimpleNamespace(mcp=None))
    app.state.runtime = SimpleNamespace(
        products={"surf_retail": product, "no_door": no_door},
        warehouse=warehouse,
        relay=EventsRelay("localhost:1"),
    )
    app.mount("/mcp", McpDoor(mcp_app))
    with TestClient(app) as test_client:
        yield test_client


def _rpc(
    client: TestClient, method: str, params: dict | None = None, auth: str = "Bearer platform", **extra: str
) -> Any:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post("/mcp/surf_retail", json=body, headers={**HEADERS, "Authorization": auth, **extra})


def _tools(client: TestClient, auth: str = "Bearer platform", **extra: str) -> set[str]:
    response = _rpc(client, "tools/list", auth=auth, **extra)
    assert response.status_code == 200, response.text
    return {t["name"] for t in response.json()["result"]["tools"]}


def _call(client: TestClient, tool: str, arguments: dict, auth: str = "Bearer platform", **extra: str) -> dict:
    response = _rpc(client, "tools/call", {"name": tool, "arguments": arguments}, auth=auth, **extra)
    assert response.status_code == 200, response.text
    return response.json()["result"]


MANAGER_VIEW = {"X-Data-View-As": "retail_store_manager", "X-Data-View-As-Values": "S012"}


def test_no_token_is_401_and_a_tenant_without_the_scenario_is_403(client: TestClient) -> None:
    assert _rpc(client, "tools/list", auth="").status_code == 401
    assert _rpc(client, "tools/list", auth="Bearer denied").status_code == 403


def test_a_product_without_a_door_is_404(client: TestClient) -> None:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    response = client.post("/mcp/no_door", json=body, headers={**HEADERS, "Authorization": "Bearer platform"})
    assert response.status_code == 404


def test_an_unknown_view_as_role_is_403(client: TestClient) -> None:
    assert _rpc(client, "tools/list", **{"X-Data-View-As": "ceo"}).status_code == 403


def test_a_role_that_may_not_run_sql_does_not_see_run_sql(client: TestClient) -> None:
    assert _tools(client) == EVERY_TOOL
    assert _tools(client, auth="Bearer analyst") == EVERY_TOOL
    assert _tools(client, **MANAGER_VIEW) == EVERY_TOOL - {"run_sql"}
    assert _tools(client, auth="Bearer manager") == EVERY_TOOL - {"run_sql"}
    assert _call(client, "run_sql", {"sql": "SELECT 1"}, **MANAGER_VIEW)["isError"] is True


@pytest.mark.parametrize(
    ("auth", "extra"),
    [("Bearer manager", {}), ("Bearer platform", MANAGER_VIEW)],
    ids=["manager-token", "platform-viewing-as-manager"],
)
def test_a_store_managers_metric_is_filtered_to_their_store(
    client: TestClient, warehouse: FakeWarehouse, auth: str, extra: dict[str, str]
) -> None:
    result = _call(client, "query_metric", {"metric": "revenue"}, auth=auth, **extra)
    assert result["isError"] is False, result
    assert result["structuredContent"]["rows"] == [{"value": 100}]
    sql, params = warehouse.queries[-1]
    assert '"store_id"::text = ANY' in sql
    assert ["S012"] in params.values()


def test_the_admin_view_is_not_filtered(client: TestClient, warehouse: FakeWarehouse) -> None:
    assert _call(client, "query_metric", {"metric": "revenue"})["isError"] is False
    sql, params = warehouse.queries[-1]
    assert "store_id" not in sql
    assert "acme" in params.values()  # always the caller's org


def test_list_metrics_names_the_records_and_data_health_names_the_role(client: TestClient) -> None:
    catalogue = _call(client, "list_metrics", {})["structuredContent"]
    assert "revenue" in [m["name"] for m in catalogue["metrics"]]
    assert catalogue["records"]
    health_result = _call(client, "data_health", {}, **MANAGER_VIEW)
    assert health_result["isError"] is False, health_result
    health = health_result["structuredContent"]
    assert health["access"]["role"] == "retail_store_manager"
    assert health["access"]["row_filter"] == {"column": "store_id", "values": ["S012"]}


def test_a_bad_metric_is_a_tool_error_not_a_crash(client: TestClient) -> None:
    result = _call(client, "query_metric", {"metric": "revenue", "dims": ["loyalty_id"]})
    assert result["isError"] is True
    assert "cannot be split" in result["content"][0]["text"]
