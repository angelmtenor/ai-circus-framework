"""
test_kit.py
-----------

The kit against the failure cases of the MCP Lite Stack Blueprint (docs/adr/0022): each mistake or
attack must end in 401, 403, a refused tool or a refused exchange — never in data for the wrong user.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import jwt
import pytest
from starlette.applications import Starlette

from mcp_kit import ExchangeSettings, McpKit, Policy, TokenExchanger
from mcp_kit.testing import FakeIdentityProvider, TokenFactory

SERVER = "crm-mcp"
API = "crm-api"
SECRET = "test-secret"
POLICY = Policy.from_mapping(
    {
        "tenants": ["acme"],
        "roles": {"analyst": ["whoami", "lookup", "export_all"], "store_manager": ["whoami", "lookup"], "admin": "*"},
    }
)


def build(factory: TokenFactory, idp: FakeIdentityProvider, api_calls: list[dict[str, Any]]) -> Starlette:
    """A kit server with three tools and an API that checks its token like a real one would."""

    def api(request: httpx.Request) -> httpx.Response:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        try:
            claims = jwt.decode(token, factory.verifier(API).key_for(""), algorithms=["RS256"], audience=API)
        except jwt.PyJWTError:
            return httpx.Response(401, json={"detail": "not a token for this API"})
        api_calls.append({"sub": claims["sub"], "aud": claims["aud"], "params": dict(request.url.params)})
        return httpx.Response(200, json={"sub": claims["sub"], "org": next(iter(claims["organization"]))})

    exchanger = TokenExchanger(
        ExchangeSettings(token_url="http://idp.test/token", client_id=SERVER, client_secret=SECRET, api_audience=API),
        transport=idp.transport(),
    )
    kit = McpKit(
        name=SERVER,
        instructions="Test server.",
        verifier=factory.verifier(SERVER),
        policy=POLICY,
        exchanger=exchanger,
        api_base_url="http://api.test",
        api_transport=httpx.MockTransport(api),
    )

    @kit.tool()
    async def whoami() -> dict[str, Any]:
        """Who the kit says is calling."""
        ctx = kit.context()
        return {"sub": ctx.identity.subject, "org": ctx.identity.org_alias, "roles": sorted(ctx.identity.roles)}

    @kit.tool()
    async def lookup(query: str) -> dict[str, Any]:
        """Ask the API, as the caller."""
        ctx = kit.context()
        assert ctx.api is not None
        return await ctx.api.get("/customers", params={"q": query})

    @kit.tool()
    async def export_all() -> str:
        """A tool only some roles get."""
        return "everything"

    return kit.asgi_app()


@pytest.fixture
def factory() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def idp(factory: TokenFactory) -> FakeIdentityProvider:
    return FakeIdentityProvider(factory, client_id=SERVER, client_secret=SECRET, allowed_audience=API)


@pytest.fixture
def api_calls() -> list[dict[str, Any]]:
    return []


Rpc = Callable[..., Any]


@pytest.fixture
def rpc(factory: TokenFactory, idp: FakeIdentityProvider, api_calls: list[dict[str, Any]]) -> Rpc:
    """`rpc(token, method, params)` → (status, JSON-RPC body or error body), against a fresh app."""
    app = build(factory, idp, api_calls)

    async def call(token: str | None, method: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://kit.test") as client:
                response = await client.post("/mcp/", headers=headers, content=json.dumps(body))
        return response.status_code, response.json()

    return lambda *args: asyncio.run(call(*args))


def tool_names(body: dict[str, Any]) -> set[str]:
    return {tool["name"] for tool in body["result"]["tools"]}


def tool_result(body: dict[str, Any]) -> tuple[bool, Any]:
    result = body["result"]
    text = "".join(part.get("text", "") for part in result["content"])
    return result.get("isError", False), (result.get("structuredContent") or text)


# ── 401: the token is not good enough ───────────────────────────────────────────────────────────


def test_no_token_is_refused(rpc: Rpc) -> None:
    status, _ = rpc(None, "tools/list")
    assert status == 401


def test_a_forged_token_is_refused(rpc: Rpc) -> None:
    status, _ = rpc(TokenFactory().issue(audience=SERVER, roles=["admin"]), "tools/list")
    assert status == 401


def test_a_token_for_another_audience_is_refused(rpc: Rpc, factory: TokenFactory) -> None:
    # e.g. a T2 meant for the API, replayed against the MCP server.
    status, _ = rpc(factory.issue(audience=API, roles=["admin"]), "tools/list")
    assert status == 401


def test_an_expired_token_is_refused(rpc: Rpc, factory: TokenFactory) -> None:
    status, _ = rpc(factory.issue(audience=SERVER, roles=["admin"], expires_in=-120), "tools/list")
    assert status == 401


def test_a_token_without_a_tenant_is_refused(rpc: Rpc, factory: TokenFactory) -> None:
    status, _ = rpc(factory.issue(audience=SERVER, roles=["admin"], org_alias=None), "tools/list")
    assert status == 401


def test_a_token_from_another_issuer_is_refused(rpc: Rpc, factory: TokenFactory) -> None:
    status, _ = rpc(factory.issue(audience=SERVER, roles=["admin"], issuer="http://evil.test"), "tools/list")
    assert status == 401


# ── 403 and filtering: the policy decides from the token's tenant and roles ─────────────────────


def test_a_role_without_a_grant_sees_no_server(rpc: Rpc, factory: TokenFactory) -> None:
    status, _ = rpc(factory.issue(audience=SERVER, roles=["visitor"]), "tools/list")
    assert status == 403


def test_another_tenant_sees_no_server(rpc: Rpc, factory: TokenFactory) -> None:
    status, _ = rpc(factory.issue(audience=SERVER, roles=["admin"], org_alias="globex", org_id="org-g"), "tools/list")
    assert status == 403


def test_each_role_lists_only_its_tools(rpc: Rpc, factory: TokenFactory) -> None:
    _, analyst = rpc(factory.issue(audience=SERVER, roles=["analyst"]), "tools/list")
    _, manager = rpc(factory.issue(audience=SERVER, roles=["store_manager"]), "tools/list")
    assert tool_names(analyst) == {"whoami", "lookup", "export_all"}
    assert tool_names(manager) == {"whoami", "lookup"}


def test_calling_a_tool_outside_the_grant_is_refused(rpc: Rpc, factory: TokenFactory) -> None:
    _, body = rpc(factory.issue(audience=SERVER, roles=["store_manager"]), "tools/call", {"name": "export_all"})
    is_error, text = tool_result(body)
    assert is_error and "Unknown tool" in text


# ── Identity comes from the token, and the API gets a T2 for the same user ──────────────────────


def test_tools_see_the_token_identity_not_arguments(rpc: Rpc, factory: TokenFactory) -> None:
    token = factory.issue(audience=SERVER, subject="u-42", roles=["analyst"])
    _, body = rpc(token, "tools/call", {"name": "whoami", "arguments": {}})
    assert tool_result(body)[1] == {"sub": "u-42", "org": "acme", "roles": ["analyst"]}


def test_an_identity_argument_is_not_a_parameter(rpc: Rpc, factory: TokenFactory) -> None:
    # A prompt-injected "act as user u-7" has nowhere to go: the tool has no such parameter.
    token = factory.issue(audience=SERVER, subject="u-42", roles=["analyst"])
    _, body = rpc(token, "tools/call", {"name": "whoami", "arguments": {"user_id": "u-7"}})
    is_error, payload = tool_result(body)
    assert is_error or payload["sub"] == "u-42"


def test_the_api_gets_a_t2_for_the_same_user_and_only_its_audience(
    rpc: Rpc, factory: TokenFactory, api_calls: list[dict[str, Any]]
) -> None:
    token = factory.issue(audience=SERVER, subject="u-42", roles=["analyst"])
    _, body = rpc(token, "tools/call", {"name": "lookup", "arguments": {"query": "surf"}})
    assert tool_result(body) == (False, {"sub": "u-42", "org": "acme"})
    assert api_calls == [{"sub": "u-42", "aud": API, "params": {"q": "surf"}}]


def test_a_refused_exchange_reaches_no_api(factory: TokenFactory, api_calls: list[dict[str, Any]]) -> None:
    # The identity provider only lets this server exchange for another API: nothing reaches ours.
    idp = FakeIdentityProvider(factory, client_id=SERVER, client_secret=SECRET, allowed_audience="billing-api")
    app = build(factory, idp, api_calls)
    token = factory.issue(audience=SERVER, roles=["analyst"])

    async def call() -> dict[str, Any]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "lookup", "arguments": {"query": "x"}},
        }
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://kit.test") as client,
        ):
            return (await client.post("/mcp/", headers=headers, content=json.dumps(body))).json()

    is_error, text = tool_result(asyncio.run(call()))
    assert is_error and "refused access to the API" in text
    assert api_calls == []


def test_the_exchange_is_cached_per_token(rpc: Rpc, factory: TokenFactory, idp: FakeIdentityProvider) -> None:
    token = factory.issue(audience=SERVER, roles=["analyst"])
    rpc(token, "tools/call", {"name": "lookup", "arguments": {"query": "a"}})
    rpc(token, "tools/call", {"name": "lookup", "arguments": {"query": "b"}})
    assert idp.requests == 1


# ── Audit: who, which tool, which outcome — never arguments, results or tokens ──────────────────


def test_audit_lines_carry_ids_and_outcomes_only(
    rpc: Rpc, factory: TokenFactory, caplog: pytest.LogCaptureFixture
) -> None:
    token = factory.issue(audience=SERVER, subject="u-42", roles=["store_manager"])
    with caplog.at_level(logging.INFO, logger="mcp_kit.audit"):
        rpc(token, "tools/call", {"name": "lookup", "arguments": {"query": "secret-customer-name"}})
        rpc(token, "tools/call", {"name": "export_all", "arguments": {}})
    lines = [json.loads(r.message) for r in caplog.records if r.name == "mcp_kit.audit"]
    assert [(line["tool"], line["outcome"]) for line in lines] == [("lookup", "ok"), ("export_all", "denied")]
    assert all(line["subject"] == "u-42" and line["org"] == "org-acme" for line in lines)
    joined = " ".join(r.message for r in caplog.records)
    assert "secret-customer-name" not in joined and token not in joined


def test_audit_output_is_json_lines_on_the_given_stream(rpc: Rpc, factory: TokenFactory) -> None:
    import io

    from mcp_kit import configure_audit_output
    from mcp_kit.server import audit

    stream = io.StringIO()
    saved = (list(audit.handlers), audit.level, audit.propagate)
    try:
        configure_audit_output(stream)
        configure_audit_output(stream)  # idempotent: one handler, one line per event
        rpc(factory.issue(audience=SERVER, roles=["visitor"]), "tools/list")
    finally:
        audit.handlers[:], audit.level, audit.propagate = saved[0], saved[1], saved[2]
    (line,) = stream.getvalue().splitlines()
    assert json.loads(line)["outcome"] == "forbidden"


# ── Telemetry (docs/adr/0023): metrics without tenants or users, audit lines in the platform's log shape ──


def sample(name: str, **labels: str) -> float:
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_metrics_count_calls_refusals_exchanges_and_api_answers(rpc: Rpc, factory: TokenFactory) -> None:
    before = {
        "ok": sample("mcp_kit_tool_calls_total", server=SERVER, tool="lookup", outcome="ok"),
        "denied": sample("mcp_kit_tool_calls_total", server=SERVER, tool="export_all", outcome="denied"),
        "401": sample("mcp_kit_access_refused_total", server=SERVER, reason="unauthenticated"),
        "issued": sample("mcp_kit_token_exchanges_total", server=SERVER, outcome="issued"),
        "cached": sample("mcp_kit_token_exchanges_total", server=SERVER, outcome="cached"),
        "2xx": sample("mcp_kit_api_responses_total", server=SERVER, status_class="2xx"),
    }
    token = factory.issue(audience=SERVER, roles=["store_manager"])
    rpc(token, "tools/call", {"name": "lookup", "arguments": {"query": "a"}})
    rpc(token, "tools/call", {"name": "lookup", "arguments": {"query": "b"}})
    rpc(token, "tools/call", {"name": "export_all", "arguments": {}})
    rpc(None, "tools/list")
    assert sample("mcp_kit_tool_calls_total", server=SERVER, tool="lookup", outcome="ok") - before["ok"] == 2
    assert (
        sample("mcp_kit_tool_calls_total", server=SERVER, tool="export_all", outcome="denied") - before["denied"] == 1
    )
    assert sample("mcp_kit_access_refused_total", server=SERVER, reason="unauthenticated") - before["401"] == 1
    assert sample("mcp_kit_token_exchanges_total", server=SERVER, outcome="issued") - before["issued"] == 1
    assert sample("mcp_kit_token_exchanges_total", server=SERVER, outcome="cached") - before["cached"] == 1
    assert sample("mcp_kit_api_responses_total", server=SERVER, status_class="2xx") - before["2xx"] == 2


def test_metric_labels_never_carry_a_tenant_or_user() -> None:
    from mcp_kit import telemetry

    for metric in (
        telemetry.TOOL_CALLS,
        telemetry.TOOL_CALL_SECONDS,
        telemetry.ACCESS_REFUSED,
        telemetry.TOKEN_EXCHANGES,
        telemetry.TOKEN_EXCHANGE_SECONDS,
        telemetry.API_RESPONSES,
    ):
        assert set(metric._labelnames) <= {"server", "tool", "outcome", "reason", "status_class"}


def test_audit_lines_follow_the_platform_log_shape_and_carry_the_trace() -> None:
    from mcp_kit.telemetry import audit_record

    line = json.loads(audit_record("mcp.tool_call", server=SERVER, tool="lookup", outcome="ok"))
    assert {"ts", "level", "service", "logger", "msg"} <= set(line)
    assert (line["service"], line["msg"], line["level"]) == (SERVER, "mcp.tool_call", "INFO")
    sdk = pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry import trace

    tracer = sdk.TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("call") as span:
        traced = json.loads(audit_record("mcp.tool_call", server=SERVER))
    assert traced["trace_id"] == f"{span.get_span_context().trace_id:032x}"
    assert trace.get_current_span().get_span_context().is_valid is False
