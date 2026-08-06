"""
- Title:    Prediction API
- Author:   ai-circus-framework contributors
"""

from __future__ import annotations

import pandas as pd
from ai_circus_shared.auth import Identity
from ai_circus_shared.scenario_schema import ScenarioDefinition
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from prediction.core.identity import resolve_identity
from prediction.core.model_cache import ModelCache
from prediction.core.predict import predict as run_predict

router = APIRouter()


class PredictRequest(BaseModel):
    """One or more records to score, each a mapping of feature name -> value."""

    records: list[dict[str, object]]


class PredictionOut(BaseModel):
    """A single record's prediction (probability for classification, raw value for
    regression) and per-feature SHAP contributions.
    """

    prediction: float
    contributions: dict[str, float]


class PredictResponse(BaseModel):
    """Response body for POST /predict/{scenario_slug}."""

    predictions: list[PredictionOut]


def _model_cache(request: Request) -> ModelCache:
    return request.app.state.model_cache


def _scenario_definition(scenario_slug: str, request: Request) -> ScenarioDefinition:
    """Look up `scenario_slug` among the scenarios this instance loaded at startup.

    A scenario can be a real, entitled scenario in platform-registry yet still 404
    here if this specific instance's SCENARIOS env var doesn't include it — that's a
    "not served here" condition, distinct from (and checked after) the 401/403s
    `resolve_identity` raises for auth/entitlement failures.
    """
    definitions: dict[str, ScenarioDefinition] = request.app.state.definitions
    definition = definitions.get(scenario_slug)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Scenario {scenario_slug!r} is not served by this instance.")
    return definition


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness check."""
    return {"status": "ok"}


@router.post("/predict/{scenario_slug}", response_model=PredictResponse)
def predict_endpoint(
    body: PredictRequest,
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
    model_cache: ModelCache = Depends(_model_cache),
) -> PredictResponse:
    """Score one or more records for the caller's tenant; org_id comes from their token."""
    # resolve_identity() already guarantees org_id is set (401s otherwise).
    assert identity.org_id is not None
    artifacts = model_cache.get(identity.org_id, definition.slug)
    records = pd.DataFrame(body.records)
    predictions = run_predict(artifacts, records)
    return PredictResponse(
        predictions=[PredictionOut(prediction=p.prediction, contributions=p.contributions) for p in predictions]
    )
