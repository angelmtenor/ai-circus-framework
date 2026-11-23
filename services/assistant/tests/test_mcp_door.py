"""The assistant's MCP door (docs/adr/0031): entitlement before MCP, the caller's own token forwarded, only the
exposed tools, read-only.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest
from ai_circus_shared.auth import Identity, TokenValidationError
from ai_circus_shared.entitlements import EntitlementDeniedError
from ai_circus_shared.scenario_schema import ScenarioDefinition
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistant.core import mcp_door

SCENARIOS = Path(__file__).resolve().parents[3] / "scenarios"
HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
TOKENS = {"Bearer t1": "org-1"}
pytestmark = pytest.mark.skipif(
    not (SCENARIOS / "water_reservoirs_drought" / "forecast.json").exists(), reason="no scenarios"
)


def _with_door(slug: str, expose: list[str]) -> ScenarioDefinition:
    definition = ScenarioDefinition.load(SCENARIOS / slug / "scenario.yaml")
    return ScenarioDefinition.model_validate({**definition.model_dump(), "mcp": {"expose": expose}})


def _identity(authorization: str | None, scenario_slug: str) -> Identity:
    if authorization == "Bearer denied":
        raise EntitlementDeniedError(f"Not entitled to {scenario_slug}.")
    if authorization not in TOKENS:
        raise TokenValidationError("Missing or invalid bearer token.")
    return Identity(subject="u", org_id=TOKENS[authorization], roles=frozenset())


class FakePrediction:
    """prediction's client, recording each call (and the token it carried)."""

    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, base_url: str) -> None:
        """Like the real client: one base URL."""
        self.base_url = base_url

    def forecast(self, *, scenario_slug: str, authorization: str | None, **_: Any) -> dict[str, Any]:
        """No model trained for the tenant yet (503): the drought tools use the committed reference."""
        FakePrediction.calls.append({"forecast": scenario_slug, "authorization": authorization})
        raise httpx.HTTPStatusError("503", request=httpx.Request("GET", "http://p"), response=httpx.Response(503))

    def model_card(self, *, scenario_slug: str, authorization: str | None) -> dict[str, Any]:
        """A small card."""
        FakePrediction.calls.append({"card": scenario_slug, "authorization": authorization})
        return {"task_type": "classification", "metrics": {"roc_auc": 0.78}}

    def predict(
        self, *, scenario_slug: str, authorization: str | None, records: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """One scored record with two contributions."""
        FakePrediction.calls.append({"predict": scenario_slug, "authorization": authorization, "records": records})
        scored: dict[str, Any] = {
            "prediction": 0.42,
            "prediction_lower": None,
            "prediction_upper": None,
            "contributions": {"num__PremiumChangePct": 0.2, "num__TenureYears": -0.1, "cat__Channel_Agent": -0.04},
        }
        if scenario_slug == "financial_crime_desk":  # a scenario with business rules: one fires and blocks
            scored["gate"], scored["decision"] = "block", "block"
            scored["rules"] = [
                {"key": "s", "label": "Sanctioned country", "outcome": "block", "fired": True, "message": "Blocked."},
                {"key": "a", "label": "Large amount", "outcome": "review", "fired": False, "message": "Review."},
            ]
        return {"predictions": [scored]}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(mcp_door, "_identity", _identity)
    monkeypatch.setattr(mcp_door, "PredictionServiceClient", FakePrediction)
    monkeypatch.setattr(
        mcp_door,
        "get_env_config",
        lambda: SimpleNamespace(SCENARIOS_DIR=str(SCENARIOS), PREDICTION_SERVICE_URL="http://prediction"),
    )
    FakePrediction.calls = []
    mcp_app = mcp_door.build_mcp_app()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        async with mcp_app.lifespan(mcp_app):
            yield

    app = FastAPI(lifespan=lifespan)
    app.state.definitions = {
        "water_reservoirs_drought": _with_door(
            "water_reservoirs_drought", ["describe_model", "drought_state", "what_if", "search_drought_plan"]
        ),
        "insurance_portfolio": _with_door("insurance_portfolio", ["describe_model", "score_record"]),
        "financial_crime_desk": _with_door("financial_crime_desk", ["describe_model", "score_record"]),
        "churn": ScenarioDefinition.load(SCENARIOS / "churn" / "scenario.yaml"),
    }
    app.mount("/mcp", mcp_door.McpDoor(mcp_app))
    with TestClient(app) as test_client:
        yield test_client


def _rpc(client: TestClient, slug: str, method: str, params: dict | None = None, auth: str = "Bearer t1") -> Any:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post(f"/mcp/{slug}", json=body, headers={**HEADERS, "Authorization": auth})


def _call(client: TestClient, slug: str, tool: str, arguments: dict) -> dict[str, Any]:
    response = _rpc(client, slug, "tools/call", {"name": tool, "arguments": arguments})
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_the_door_checks_the_caller_before_any_mcp(client: TestClient) -> None:
    assert _rpc(client, "insurance_portfolio", "tools/list", auth="").status_code == 401
    assert _rpc(client, "insurance_portfolio", "tools/list", auth="Bearer denied").status_code == 403
    assert _rpc(client, "churn", "tools/list").status_code == 404  # no mcp block
    assert _rpc(client, "unknown", "tools/list").status_code == 404


def test_each_scenario_lists_only_its_exposed_read_only_tools(client: TestClient) -> None:
    tools = _rpc(client, "water_reservoirs_drought", "tools/list").json()["result"]["tools"]
    assert {t["name"] for t in tools} == {"describe_model", "drought_state", "what_if", "search_drought_plan"}
    assert all(t["annotations"]["readOnlyHint"] for t in tools)
    refused = _call(client, "water_reservoirs_drought", "score_record", {"record": {}})
    assert refused["isError"] is True


def test_score_record_fills_defaults_forwards_the_callers_token_and_explains(client: TestClient) -> None:
    result = _call(client, "insurance_portfolio", "score_record", {"record": {"PremiumChangePct": 12}})
    assert result["isError"] is False
    body = result["structuredContent"]
    assert body["prediction"] == pytest.approx(0.42) and body["drivers"][0]["feature"] == "PremiumChangePct"
    assert [d["feature"] for d in body["drivers"]][1] == "TenureYears"  # pipeline prefixes rolled back to the inputs
    assert body["meaning"].startswith("Probability (0 to 1) of 'Lapsed'")
    assert "interval_90" not in body and "gate" not in body  # a classifier without business rules
    call = FakePrediction.calls[-1]
    assert call["authorization"] == "Bearer t1" and call["records"][0]["PremiumChangePct"] == 12
    assert len(call["records"][0]) > 1  # the other inputs took their defaults
    unknown = _call(client, "insurance_portfolio", "score_record", {"record": {"Surname": "x"}})
    assert unknown["isError"] is True and "Unknown features" in str(unknown)


@pytest.mark.parametrize(
    ("record", "hint"),
    [
        ({"PremiumChangePct": "Yes"}, "is a number from"),
        ({"PremiumChangePct": 10_000}, "is a number from"),
        ({"Channel": "Carrier pigeon"}, "is one of"),
    ],
)
def test_score_record_refuses_a_value_of_the_wrong_kind_before_the_model(
    client: TestClient, record: dict[str, Any], hint: str
) -> None:
    calls = len(FakePrediction.calls)
    result = _call(client, "insurance_portfolio", "score_record", {"record": record})
    assert result["isError"] is True and hint in str(result)
    assert len(FakePrediction.calls) == calls  # prediction never saw it


def test_score_record_takes_a_number_written_as_text(client: TestClient) -> None:
    assert (
        _call(client, "insurance_portfolio", "score_record", {"record": {"PremiumChangePct": "12.5"}})["isError"]
        is False
    )
    assert FakePrediction.calls[-1]["records"][0]["PremiumChangePct"] == pytest.approx(12.5)


def test_drought_tools_read_the_same_toolkit_as_the_chat(client: TestClient) -> None:
    from assistant.core import drought_tools

    drought_tools._tenant_forecasts.clear()
    state = _call(client, "water_reservoirs_drought", "drought_state", {"system": "Central"})
    assert state["isError"] is False and state["structuredContent"]["system"] == "central"
    # the tenant's trained forecast is asked for with the caller's own token (none here: the committed reference)
    assert {"forecast": "water_reservoirs_drought", "authorization": "Bearer t1"} in FakePrediction.calls
    plan = _call(client, "water_reservoirs_drought", "search_drought_plan", {"query": "alert measures agriculture"})
    assert plan["structuredContent"]["results"]
    bad = _call(client, "water_reservoirs_drought", "what_if", {"system": "atlantis"})
    assert bad["isError"] is True and "Unknown system" in str(bad)


def test_score_record_says_when_a_business_rule_decides_instead_of_the_model(client: TestClient) -> None:
    result = _call(client, "financial_crime_desk", "score_record", {"record": {}})
    body = result["structuredContent"]
    assert body["gate"] == "block" and body["decision"] == "block"
    assert body["rules_fired"] == [{"rule": "Sanctioned country", "outcome": "block", "message": "Blocked."}]
