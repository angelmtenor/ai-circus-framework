"""
- Title:    Backend tools giving the chat agent real data/prediction/document access
- Author:   Angel Martinez-Tenor

Three LangChain tools built fresh per request (same per-request-construction shape as
`rag_agent.core.agent.build_retrieve_tool`), each closing over the scenario_slug and the
caller's forwarded Authorization header so every call to `prediction` is scoped and
entitlement-checked exactly like it would be if `ui-react` called `prediction` directly
— see core/prediction_client.py. Complements, not replaces, the frontend's
`render_chart`/`render_table` generative-UI tools (ui-react/src/chatGenerativeUi.tsx):
these fetch real numbers, the frontend tools render them.

A scenario with `documents.tool` (e.g. the regulations its applications are judged by)
gets a fourth: a search over the tenant's etl-vectorize collection, named and described
by the scenario itself — see `build_document_tool`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from ai_circus_shared.embeddings import EmbeddingProvider
from ai_circus_shared.retrieval import VectorSearchClient, format_retrieved, retrieve
from ai_circus_shared.scenario_schema import ScenarioDefinition
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from assistant.core.prediction_client import PredictionServiceClient

# Deliberately small: the model has to read a tool's result and then transcribe values
# into its own render_chart/render_table call arguments — a large sample would blow the
# context budget and invite transcription errors, not just cost.
_MAX_SAMPLE_LIMIT = 100
_MAX_EVALUATION_LIMIT = 500
# Free-text feature values (e.g. a 1,000-character review) are clipped in tool output:
# 100 full reviews would cost ~25k tokens per call for text the model rarely needs whole.
_MAX_SAMPLE_TEXT_CHARS = 280
_MAX_EVALUATION_TEXT_CHARS = 120


def _clip_text(value: Any, max_chars: int) -> Any:
    """Recursively shorten every string longer than `max_chars` (with an ellipsis)."""
    if isinstance(value, str):
        return value if len(value) <= max_chars else f"{value[:max_chars]}…"
    if isinstance(value, dict):
        return {k: _clip_text(v, max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip_text(v, max_chars) for v in value]
    return value


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


class _DocumentSearchArgs(BaseModel):
    query: str = Field(
        min_length=3,
        max_length=500,
        description="What to look up, in the documents' language — a short, specific question or topic.",
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
        return json.dumps(_clip_text(result, _MAX_SAMPLE_TEXT_CHARS))

    def _get_predictions_vs_actuals(limit: int = 200) -> str:
        try:
            result = client.evaluation(scenario_slug=scenario_slug, authorization=authorization, limit=limit)
        except httpx.HTTPError as exc:
            return _error_result("fetch predictions vs. actuals", exc)
        return json.dumps(_clip_text(result, _MAX_EVALUATION_TEXT_CHARS))

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


def build_document_tool(
    qdrant: VectorSearchClient,
    embedder: EmbeddingProvider,
    definition: ScenarioDefinition,
    *,
    org_id: str,
) -> BaseTool:
    """The scenario's `documents.tool`: top-k chunks of the caller's tenant collection.

    Same per-request closure shape as rag_agent.core.agent.build_retrieve_tool (the
    org is closed over, never a tool argument). The result is `<retrieved_document
    source="…">`-delimited text, which is also where ui-react reads an answer's sources
    from. Calls past `max_calls_per_run` return a stop notice instead of more text: each
    result is prompt paid for again on every following model call.
    """
    documents, vector_store = definition.documents, definition.vector_store
    assert documents is not None and documents.tool is not None and vector_store is not None
    config = documents.tool
    calls = [0]

    def _search(query: str) -> tuple[str, list[dict[str, Any]]]:
        calls[0] += 1
        if calls[0] > config.max_calls_per_run:
            return "Search limit reached for this question: answer from the excerpts already returned.", []
        try:
            chunks = retrieve(qdrant, embedder, vector_store, org_id, query)
        except (httpx.HTTPError, ResponseHandlingException, UnexpectedResponse) as exc:
            # Like the prediction tools: an outage becomes a tool result the model can
            # report, not an exception that aborts the whole answer.
            return f"Could not search the documents: the document index is unavailable ({exc}).", []
        sources = [{"source": c.source, "score": c.score} for c in chunks]
        if not chunks:
            return "No relevant documents were found for this query.", sources
        return format_retrieved(chunks), sources

    tool = StructuredTool.from_function(
        func=_search,
        name=config.name,
        description=config.description,
        args_schema=_DocumentSearchArgs,
        response_format="content_and_artifact",
    )
    return tool
