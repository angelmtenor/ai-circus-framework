"""
test_server.py
--------------

crm-mcp end to end in-process: its real policy file, its tools, the kit's guard, a stand-in identity
provider for the exchange and a stand-in crm-api that only accepts a T2 issued for it.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
import yaml
from fastapi import FastAPI
from mcp_kit import ExchangeSettings, McpKit, Policy, TokenExchanger
from mcp_kit.testing import FakeIdentityProvider, TokenFactory

from crm_mcp.app import create_app
from crm_mcp.server import INSTRUCTIONS, register_tools

POLICY = Path(__file__).resolve().parents[1] / "policy.yaml"
SECRET = "test-secret"  # ruff: ignore[hardcoded-password-string] - a test-only client secret
TOKEN_URL = "http://idp.test/token"  # ruff: ignore[hardcoded-password-string] - a URL, not a password


@pytest.fixture
def factory() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def api_calls() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def app(factory: TokenFactory, api_calls: list[dict[str, Any]]) -> FastAPI:
    def crm_api(request: httpx.Request) -> httpx.Response:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        try:
            claims = jwt.decode(
                token, factory.verifier("crm-api").key_for(""), algorithms=["RS256"], audience="crm-api"
            )
        except jwt.PyJWTError:
            return httpx.Response(401, json={"detail": "not for this API"})
        api_calls.append({"path": request.url.path, "params": dict(request.url.params), "sub": claims["sub"]})
        if request.url.path == "/customers/C999":
            return httpx.Response(404, json={"detail": "Customer not found."})
        return httpx.Response(200, json={"customers": [], "count": 0, "visible_stores": "all"})

    idp = FakeIdentityProvider(factory, client_id="crm-mcp", client_secret=SECRET, allowed_audience="crm-api")
    kit = McpKit(
        name="crm-mcp",
        instructions=INSTRUCTIONS,
        verifier=factory.verifier("crm-mcp"),
        policy=Policy.from_file(POLICY),
        exchanger=TokenExchanger(
            ExchangeSettings(token_url=TOKEN_URL, client_id="crm-mcp", client_secret=SECRET, api_audience="crm-api"),
            transport=idp.transport(),
        ),
        api_base_url="http://crm-api.test",
        api_transport=httpx.MockTransport(crm_api),
    )
    register_tools(kit)
    return create_app(kit)


def call(app: FastAPI, token: str | None, method: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
    async def go() -> tuple[int, Any]:
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            response = await c.post("/mcp/", headers=headers, content=json.dumps(body))
            return response.status_code, response.json()

    return asyncio.run(go())


def names(body: dict[str, Any]) -> set[str]:
    return {t["name"] for t in body["result"]["tools"]}


def test_the_policy_file_gives_each_role_its_tools(app: FastAPI, factory: TokenFactory) -> None:
    _, analyst = call(
        app, factory.issue(audience="crm-mcp", org_alias="lite-demo", roles=["retail_analyst"]), "tools/list"
    )
    _, manager = call(
        app, factory.issue(audience="crm-mcp", org_alias="lite-demo", roles=["retail_store_manager"]), "tools/list"
    )
    assert names(analyst) == {"search_customers", "get_customer", "top_customers"}
    assert names(manager) == {"search_customers", "get_customer"}


def test_other_tenants_and_roles_see_no_server(app: FastAPI, factory: TokenFactory) -> None:
    assert (
        call(app, factory.issue(audience="crm-mcp", org_alias="globex", roles=["retail_analyst"]), "tools/list")[0]
        == 403
    )
    assert (
        call(app, factory.issue(audience="crm-mcp", org_alias="lite-demo", roles=["visitor"]), "tools/list")[0] == 403
    )
    assert call(app, None, "tools/list")[0] == 401


def test_tools_call_crm_api_as_the_user_with_a_t2(
    app: FastAPI, factory: TokenFactory, api_calls: list[dict[str, Any]]
) -> None:
    token = factory.issue(audience="crm-mcp", subject="u-7", org_alias="lite-demo", roles=["retail_analyst"])
    call(app, token, "tools/call", {"name": "search_customers", "arguments": {"query": "S012", "limit": 5}})
    call(app, token, "tools/call", {"name": "top_customers", "arguments": {"limit": 3}})
    assert api_calls == [
        {"path": "/customers", "params": {"q": "S012", "limit": "5"}, "sub": "u-7"},
        {"path": "/customers", "params": {"sort": "lifetime_value", "limit": "3"}, "sub": "u-7"},
    ]


def test_a_store_manager_cannot_call_the_analyst_tool(
    app: FastAPI, factory: TokenFactory, api_calls: list[Any]
) -> None:
    token = factory.issue(audience="crm-mcp", org_alias="lite-demo", roles=["retail_store_manager"])
    _, body = call(app, token, "tools/call", {"name": "top_customers", "arguments": {}})
    assert body["result"]["isError"] and api_calls == []


def test_arguments_are_bounded_and_not_found_is_a_tool_error(app: FastAPI, factory: TokenFactory) -> None:
    token = factory.issue(audience="crm-mcp", org_alias="lite-demo", roles=["retail_analyst"])
    _, bad = call(app, token, "tools/call", {"name": "get_customer", "arguments": {"customer_id": "../admin"}})
    _, missing = call(app, token, "tools/call", {"name": "get_customer", "arguments": {"customer_id": "C999"}})
    assert bad["result"]["isError"]
    assert missing["result"]["isError"] and "Not found" in missing["result"]["content"][0]["text"]


def test_healthz(app: FastAPI) -> None:
    async def go() -> int:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return (await c.get("/healthz")).status_code

    assert asyncio.run(go()) == 200


def test_the_catalog_lists_exactly_the_tools_this_server_registers(factory: TokenFactory) -> None:
    # mcp/catalog.yaml (docs/adr/0024) grants these tools to groups and generates policy.yaml from them:
    # a tool missing there could never be granted, and a stale name would grant nothing.
    catalog = yaml.safe_load((POLICY.parents[2] / "mcp" / "catalog.yaml").read_text())
    (entry,) = [s for s in catalog["servers"].values() if s.get("service") == "crm-mcp"]
    kit = McpKit(
        name="crm-mcp", instructions=INSTRUCTIONS, verifier=factory.verifier("crm-mcp"), policy=Policy.from_file(POLICY)
    )
    register_tools(kit)
    assert set(entry["tools"]) == kit.tool_names
    assert entry["api"] == "crm-api"
