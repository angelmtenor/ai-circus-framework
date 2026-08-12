"""
- Title:    Backend tools giving the chat agent real data/prediction access
- Author:   ai-circus-framework contributors

Three LangChain tools built fresh per request (same per-request-construction shape as
`rag_agent.core.agent.build_retrieve_tool`), each closing over the scenario_slug and the
caller's forwarded Authorization header so every call to `prediction` is scoped and
entitlement-checked exactly like it would be if `ui-react` called `prediction` directly
— see core/prediction_client.py. Complements, not replaces, the frontend's
`render_chart`/`render_table` generative-UI tools (ui-react/src/chatGenerativeUi.tsx):
these fetch real numbers, the frontend tools render them.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from assistant.core.prediction_client import PredictionServiceClient

# Deliberately small: the model has to read a tool's result and then transcribe values
# into its own render_chart/render_table call arguments — a large sample would blow the
# context budget and invite transcription errors, not just cost.
_MAX_SAMPLE_LIMIT = 100
_MAX_EVALUATION_LIMIT = 500


def _error_result(action: str, exc: httpx.HTTPError) -> str:
    return f"Could not {action}: the prediction service is unavailable ({exc})."


class _SampleArgs(BaseModel):
    limit: int = Field(20, ge=1, le=_MAX_SAMPLE_LIMIT, description="How many real dataset rows to return.")


class _EvaluationArgs(BaseModel):
    limit: int = Field(
        200,
        ge=1,
        le=_MAX_EVALUATION_LIMIT,
        description="How many held-out rows' actual/predicted values to return.",
    )


class _PredictArgs(BaseModel):
    records: list[dict[str, Any]] = Field(
        description="One or more records to score, each a mapping of feature name to value."
    )


def build_prediction_tools(
    client: PredictionServiceClient, *, scenario_slug: str, authorization: str | None
) -> list[BaseTool]:
    """Build the three prediction-service-backed tools for one chat request.

    `scenario_slug`/`authorization` are closed over rather than exposed as tool
    arguments — the model shouldn't (and via AG-UI's per-request tool declarations,
    couldn't) name another scenario or caller.
    """

    def _get_dataset_sample(limit: int = 20) -> str:
        try:
            result = client.sample(scenario_slug=scenario_slug, authorization=authorization, limit=limit)
        except httpx.HTTPError as exc:
            return _error_result("fetch dataset rows", exc)
        return json.dumps(result)

    def _get_predictions_vs_actuals(limit: int = 200) -> str:
        try:
            result = client.evaluation(scenario_slug=scenario_slug, authorization=authorization, limit=limit)
        except httpx.HTTPError as exc:
            return _error_result("fetch predictions vs. actuals", exc)
        return json.dumps(result)

    def _predict_records(records: list[dict[str, Any]]) -> str:
        try:
            result = client.predict(scenario_slug=scenario_slug, authorization=authorization, records=records)
        except httpx.HTTPError as exc:
            return _error_result("run the model", exc)
        return json.dumps(result)

    return [
        StructuredTool.from_function(
            func=_get_dataset_sample,
            name="get_dataset_sample",
            description=(
                "Fetch real rows from this scenario's dataset — use before drawing a chart or table from raw values."
            ),
            args_schema=_SampleArgs,
        ),
        StructuredTool.from_function(
            func=_get_predictions_vs_actuals,
            name="get_predictions_vs_actuals",
            description=(
                "Fetch real held-out actual vs. predicted values and evaluation metrics for the trained model — "
                "use this for any 'target vs. predictions' or accuracy question. The response also includes "
                "feature_values: a dict of feature name -> list of real values, aligned index-for-index with "
                "actuals/predictions — use these (not get_dataset_sample's, which is a different, unaligned row "
                "sample) to color or facet an actual-vs-predicted chart by a real feature."
            ),
            args_schema=_EvaluationArgs,
        ),
        StructuredTool.from_function(
            func=_predict_records,
            name="predict_records",
            description=(
                "Run the trained model on one or more hypothetical/what-if records and get back its prediction "
                "and per-feature contributions for each."
            ),
            args_schema=_PredictArgs,
        ),
    ]
