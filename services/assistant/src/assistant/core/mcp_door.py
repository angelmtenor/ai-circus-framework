"""
- Title:    MCP door of a tabular scenario: read-only model and drought tools (docs/adr/0031)
- Author:   Angel Martinez-Tenor

One FastMCP server serves every tabular_ml scenario that declares `mcp:`, at `/mcp/{scenario_slug}` (Streamable HTTP,
stateless, JSON responses), the same shape as rag-agent's document door and data-api's data door (docs/adr/0019).
`McpDoor`, the ASGI app mounted at `/mcp`, settles the scenario and the caller *before* the MCP layer runs: 404 for a
scenario this instance does not serve or that has no door, then 401/403 from the shared entitlement check. The caller's
own bearer token travels in the request scope and is forwarded unchanged to `prediction`, which checks it again, so the
tenant always comes from the token, never from a tool argument.

The tools are the ones the chat already has, without a model in between: the MCP client's own model reasons over what
they return. `describe_model` and `score_record` work for any tabular scenario; the drought tools read the same
`DroughtToolkit` as the chat and the control room. Nothing is written. Logs carry the tool name and outcome only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from ai_circus_shared.auth import AuthSettingsAdapter, Identity, TokenValidationError, resolve_caller_identity
from ai_circus_shared.entitlements import EntitlementDeniedError
from ai_circus_shared.scenario_schema import CategoricalFeatureUI, FeatureUI, NumericFeatureUI, ScenarioDefinition
from ai_circus_shared.tabular_ml import original_feature
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

from assistant import get_env_config
from assistant.core import drought_tools
from assistant.core.logger import get_logger
from assistant.core.prediction_client import PredictionServiceClient

logger = get_logger(__name__)
_fastmcp_logger = logging.getLogger("fastmcp")
_fastmcp_logger.handlers = [logging.NullHandler()]
_fastmcp_logger.propagate = False

MAX_RECORD_FIELDS = 64
MAX_TEXT_CHARS = 2000
TOP_DRIVERS = 8
_STATE_KEY = "mcp_caller"
_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_NOTE = "Illustrative decision support: a person decides. Figures come from the scenario's model and data."


@dataclass(frozen=True)
class McpCaller:
    """Who is calling (their own bearer token, forwarded), for which scenario."""

    org_id: str
    authorization: str
    definition: ScenarioDefinition
    prediction: PredictionServiceClient

    @property
    def exposed(self) -> tuple[str, ...]:
        """The tools this scenario's `mcp.expose` lists."""
        return tuple(self.definition.mcp.expose) if self.definition.mcp is not None else ()

    def toolkit(self) -> drought_tools.DroughtToolkit:
        """The scenario's drought toolkit (the schema only lets a drought_watch scenario expose these tools)."""
        return drought_tools.get_toolkit(str(Path(get_env_config().SCENARIOS_DIR) / self.definition.slug))


def _caller() -> McpCaller:
    caller = getattr(get_http_request().state, _STATE_KEY, None)
    if not isinstance(caller, McpCaller):  # only reachable if the server is served without McpDoor
        raise ToolError("This MCP server is only reachable through /mcp/{scenario_slug}.")
    return caller


async def _drought(method: str, *args: Any) -> dict[str, Any]:
    caller = _caller()
    try:
        toolkit = await run_in_threadpool(caller.toolkit)
        return {**await run_in_threadpool(getattr(toolkit, method), *args), "note": _NOTE}
    except drought_tools.ToolError as exc:
        raise ToolError(str(exc)) from exc


class ExposedToolsOnly(Middleware):
    """A scenario lists and runs only the tools its `mcp.expose` names."""

    async def on_list_tools(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        """Drop the tools this scenario does not expose."""
        exposed = _caller().exposed
        return [tool for tool in await call_next(context) if tool.name in exposed]

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        """Refuse a tool this scenario does not expose; log each call's tool and outcome."""
        name = context.message.name
        if name not in _caller().exposed:
            raise ToolError(f"Unknown tool: {name}")
        try:
            result = await call_next(context)
        except Exception as exc:
            logger.info("MCP tool {} failed: {}", name, type(exc).__name__)
            raise
        logger.info("MCP tool {} ok", name)
        return result


def _record(definition: ScenarioDefinition, given: dict[str, Any]) -> dict[str, Any]:
    """The record to score: the scenario's defaults, overridden by the caller's known features."""
    assert definition.dataset is not None  # score_record is tabular-only (schema)
    schema = definition.dataset.feature_schema
    unknown = sorted(set(given) - set(schema))
    if unknown:
        raise ToolError(f"Unknown features {unknown}. Call describe_model for the inputs.")
    record: dict[str, Any] = {
        name: spec.default for name, spec in schema.items() if name in definition.dataset.feature_columns
    }
    for name, value in given.items():
        record[name] = _checked(name, schema[name], value)
    return record


def _checked(name: str, spec: FeatureUI, value: str | float | bool) -> str | float:
    """One input as the model expects it, or a ToolError that tells the client's model how to fix it."""
    if isinstance(spec, NumericFeatureUI):
        try:
            number = float(value)
        except ValueError:
            number = float("nan")
        if not spec.min <= number <= spec.max:  # also refuses NaN and "Yes"
            raise ToolError(f"{name} is a number from {spec.min:g} to {spec.max:g}.")
        return number
    text = str(value)
    if isinstance(spec, CategoricalFeatureUI):
        if text not in spec.options:
            raise ToolError(f"{name} is one of {spec.options}.")
        return text
    if len(text) > min(spec.max_length, MAX_TEXT_CHARS):
        raise ToolError(f"{name}: at most {min(spec.max_length, MAX_TEXT_CHARS)} characters.")
    return text


def _drivers(definition: ScenarioDefinition, contributions: dict[str, float]) -> list[dict[str, Any]]:
    """The largest SHAP contributions by input feature: a one-hot or text column's parts are summed back into it."""
    assert definition.dataset is not None
    features, texts = definition.dataset.feature_columns, definition.dataset.text_columns()
    totals: dict[str, float] = {}
    for name, value in contributions.items():
        feature = original_feature(name, features, texts)
        totals[feature] = totals.get(feature, 0.0) + value
    top = sorted(totals.items(), key=lambda kv: -abs(kv[1]))[:TOP_DRIVERS]
    return [{"feature": name, "contribution": round(value, 4)} for name, value in top]


def _meaning(definition: ScenarioDefinition) -> str:
    """What `prediction` is: a classifier's probability of the positive class, or a regressor's value."""
    model = definition.model
    assert model is not None  # score_record needs a classifier or a regressor (schema)
    target = model.target_label or "the target"
    if model.task_type == "classification":
        positive = (model.target_value_labels or {}).get("1", "the positive class")
        return f"Probability (0 to 1) of '{positive}' for {target}."
    return f"Predicted value of {target}."


def build_mcp_server() -> FastMCP:
    """The model tools any tabular scenario can expose, and the drought tools of a drought-watch scenario."""
    server = FastMCP(
        name="ai-circus-models",
        instructions=(
            "Read-only access to one scenario's model (and, for a drought-watch scenario, its forecast, drought plan "
            "and data quality) for your organisation. Call describe_model first. Every result is decision support: "
            "state its uncertainty and that a person decides."
        ),
        middleware=[ExposedToolsOnly()],
        mask_error_details=True,
    )

    @server.tool(annotations=_READ_ONLY)
    async def describe_model() -> dict[str, Any]:
        """What the scenario's model predicts, from which inputs, how well, and what it is for (its model card)."""
        caller = _caller()
        definition = caller.definition
        try:
            card = await run_in_threadpool(
                caller.prediction.model_card, scenario_slug=definition.slug, authorization=caller.authorization
            )
        except httpx.HTTPError as exc:
            raise ToolError("The model card is not available: is the model trained for your organisation?") from exc
        inputs = {}
        if definition.dataset is not None:
            for name in definition.dataset.feature_columns:
                spec = definition.dataset.feature_schema[name].model_dump()
                inputs[name] = {
                    k: v for k, v in spec.items() if k in ("type", "label", "info", "min", "max", "options", "default")
                }
        return {
            "scenario": definition.title,
            "description": " ".join(definition.description.split()),
            "inputs": inputs,
            "card": card,
        }

    @server.tool(annotations=_READ_ONLY)
    async def score_record(
        record: Annotated[
            dict[str, str | float | int | bool],
            Field(
                max_length=MAX_RECORD_FIELDS, description="Feature name -> value; inputs left out take their default."
            ),
        ],
    ) -> dict[str, Any]:
        """Score one record with the deployed model and explain it: the prediction and what pushed it (SHAP)."""
        caller = _caller()
        full = _record(caller.definition, record)
        try:
            response = await run_in_threadpool(
                caller.prediction.predict,
                scenario_slug=caller.definition.slug,
                authorization=caller.authorization,
                records=[full],
            )
        except httpx.HTTPStatusError as exc:
            raise ToolError(f"The model refused the record ({exc.response.status_code}).") from exc
        result = response["predictions"][0]
        drivers = _drivers(caller.definition, result.get("contributions") or {})
        out: dict[str, Any] = {
            "prediction": result.get("prediction"),
            "meaning": _meaning(caller.definition),
            "drivers": drivers,
            "record": full,
            "note": _NOTE,
        }
        if result.get("prediction_lower") is not None:
            out["interval_90"] = [result["prediction_lower"], result.get("prediction_upper")]
        if result.get("rules") is not None:
            # A record a business rule blocks is still scored, but the decision is the rule's, not the model's.
            out["gate"] = result.get("gate")
            out["decision"] = result.get("decision")
            out["rules_fired"] = [
                {"rule": r.get("label"), "outcome": r.get("outcome"), "message": r.get("message")}
                for r in result["rules"]
                if r.get("fired")
            ]
        return out

    @server.tool(annotations=_READ_ONLY)
    async def drought_state(
        system: Annotated[str, Field(max_length=80, description="A supply system, by id or name.")],
        on: Annotated[date | None, Field(description="A past date; omit for now.")] = None,
        horizon: Annotated[
            int | None, Field(ge=1, le=180, description="Forecast state at this many days ahead.")
        ] = None,
    ) -> dict[str, Any]:
        """A supply system's state against the drought plan: now, on a past date, or forecast at a horizon."""
        return await _drought("drought_state", system, on, horizon)

    @server.tool(annotations=_READ_ONLY)
    async def storage_forecast(
        series: Annotated[str, Field(max_length=80, description="A supply system or a reservoir.")],
        horizon: Annotated[int | None, Field(ge=1, le=180, description="7, 30, 90 or 180 days; omit for all.")] = None,
    ) -> dict[str, Any]:
        """Storage forecast (p10, p50, p90) of a system or a reservoir, with what drives the median."""
        return await _drought("forecast_of", series, horizon)

    @server.tool(annotations=_READ_ONLY)
    async def what_if(
        system: Annotated[str, Field(max_length=80)],
        scenario: Literal["dry", "normal", "wet"] = "normal",
        savings_pct: Annotated[float, Field(ge=0, le=100)] = 0,
        horizon: Annotated[int, Field(ge=1, le=180)] = 90,
    ) -> dict[str, Any]:
        """Storage under a rainfall scenario and demand savings, with the chance and days to each threshold."""
        return await _drought("what_if", system, scenario, savings_pct, horizon)

    @server.tool(annotations=_READ_ONLY)
    async def least_restrictive_measures(
        system: Annotated[str, Field(max_length=80)],
        target_probability_pct: Annotated[float, Field(gt=0, lt=100)] = 10,
        horizon: Annotated[int, Field(ge=1, le=180)] = 180,
        scenario: Literal["dry", "normal", "wet"] = "dry",
    ) -> dict[str, Any]:
        """The least restrictive package of the plan keeping the chance of emergency at or below the target."""
        return await _drought("optimise_measures", system, target_probability_pct, horizon, scenario)

    @server.tool(annotations=_READ_ONLY)
    async def search_drought_plan(
        query: Annotated[str, Field(min_length=2, max_length=300)],
        lang: Literal["en", "es"] = "en",
    ) -> dict[str, Any]:
        """The drought plan's paragraphs that best match a question, each with its article citation."""
        return await _drought("search_rulebook", query, lang)

    @server.tool(annotations=_READ_ONLY)
    async def data_quality(
        reservoir: Annotated[str, Field(max_length=80)],
        start: date | None = None,
        end: date | None = None,
    ) -> dict[str, Any]:
        """Readings that need review for a reservoir over a period (default: the last 30 days): flags, not errors."""
        return await _drought("dq_report", reservoir, start, end)

    @server.tool(annotations=_READ_ONLY)
    async def weekly_bulletin(lang: Literal["en", "es"] = "en") -> dict[str, Any]:
        """This week's drought bulletin, as markdown, every figure from the tools above."""
        return await _drought("bulletin", None, lang)

    return server


def build_mcp_app() -> StarletteWithLifespan:
    """The MCP endpoint at the root of its mount; run its lifespan inside the FastAPI app's."""
    return build_mcp_server().http_app(path="/", stateless_http=True, json_response=True, host_origin_protection=False)


def _identity(authorization: str | None, scenario_slug: str) -> Identity:
    settings = AuthSettingsAdapter.from_config(get_env_config())
    return resolve_caller_identity(authorization=authorization, scenario_slug=scenario_slug, settings=settings)


class McpDoor:
    """ASGI app mounted at `/mcp`: `/{scenario_slug}[/…]` → entitlement check → the MCP app."""

    def __init__(self, mcp_app: ASGIApp) -> None:
        """Wrap the MCP app that serves every door."""
        self.mcp_app = mcp_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Settle scenario and caller, then hand the request to the MCP app at the door's root."""
        if scope["type"] != "http":
            await self.mcp_app(scope, receive, send)
            return
        slug, _, rest = get_route_path(scope).lstrip("/").partition("/")
        definitions: dict[str, ScenarioDefinition] = scope["app"].state.definitions
        definition = definitions.get(slug)
        if definition is None or definition.kind != "tabular_ml" or definition.mcp is None or not definition.mcp.expose:
            await JSONResponse({"detail": "No MCP door for this scenario."}, status_code=404)(scope, receive, send)
            return
        authorization = Headers(scope=scope).get("authorization")
        try:
            identity = await run_in_threadpool(_identity, authorization, slug)
        except TokenValidationError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
            await response(scope, receive, send)
            return
        except EntitlementDeniedError as exc:
            await JSONResponse({"detail": str(exc)}, status_code=403)(scope, receive, send)
            return
        assert identity.org_id is not None and authorization is not None  # resolve_caller_identity 401s otherwise
        scope.setdefault("state", {})[_STATE_KEY] = McpCaller(
            org_id=identity.org_id,
            authorization=authorization,
            definition=definition,
            prediction=PredictionServiceClient(base_url=get_env_config().PREDICTION_SERVICE_URL),
        )
        root_path = f"{scope.get('root_path', '')}/{slug}"
        child = {**scope, "root_path": root_path, "path": f"{root_path}/{rest}"}
        await self.mcp_app(child, receive, send)
