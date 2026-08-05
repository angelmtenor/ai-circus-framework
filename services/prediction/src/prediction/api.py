"""
- Title:    Prediction API
- Author:   ai-circus-framework contributors
"""

from __future__ import annotations

import pandas as pd
from ai_circus_shared.auth import Identity
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from prediction.core.identity import resolve_identity
from prediction.core.model_cache import ModelCache
from prediction.core.predict import predict as run_predict

router = APIRouter()


class PredictRequest(BaseModel):
    """One or more records to score, each a mapping of feature name -> value."""

    records: list[dict[str, object]]


class PredictionOut(BaseModel):
    """A single record's churn probability and per-feature SHAP contributions."""

    probability: float
    contributions: dict[str, float]


class PredictResponse(BaseModel):
    """Response body for POST /predict."""

    predictions: list[PredictionOut]


def _model_cache(request: Request) -> ModelCache:
    return request.app.state.model_cache


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness check."""
    return {"status": "ok"}


@router.post("/predict", response_model=PredictResponse)
def predict_endpoint(
    body: PredictRequest,
    identity: Identity = Depends(resolve_identity),
    model_cache: ModelCache = Depends(_model_cache),
) -> PredictResponse:
    """Score one or more records for the caller's tenant; org_id comes from their token."""
    # resolve_identity() already guarantees org_id is set (401s otherwise).
    assert identity.org_id is not None
    artifacts = model_cache.get(identity.org_id)
    records = pd.DataFrame(body.records)
    predictions = run_predict(artifacts, records)
    return PredictResponse(
        predictions=[PredictionOut(probability=p.probability, contributions=p.contributions) for p in predictions]
    )
