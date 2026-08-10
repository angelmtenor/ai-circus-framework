"""
- Title:    Prediction API
- Author:   ai-circus-framework contributors
"""

from __future__ import annotations

import pandas as pd
from ai_circus_shared.auth import Identity
from ai_circus_shared.scenario_schema import ScenarioDefinition
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from prediction.core import dataset as dataset_core
from prediction.core.identity import resolve_identity
from prediction.core.model_cache import ModelCache
from prediction.core.predict import MissingFeatureColumnsError
from prediction.core.predict import predict as run_predict

router = APIRouter()

# One standardized ceiling for every row-limited dataset endpoint below (sample,
# evaluation, explainability) — previously two different, much smaller caps
# (20000/1000) with inconsistent per-endpoint defaults (100/300/200).
MAX_ROWS = 10000


class PredictRequest(BaseModel):
    """One or more records to score, each a mapping of feature name -> value."""

    records: list[dict[str, object]]


class PredictionOut(BaseModel):
    """A single record's prediction (probability for classification, raw value for
    regression) and per-feature SHAP contributions.

    `prediction_lower`/`prediction_upper` bound a 90% prediction interval — only set
    for regression scenarios with trained quantile models.
    """

    prediction: float
    contributions: dict[str, float]
    prediction_lower: float | None = None
    prediction_upper: float | None = None


class PredictResponse(BaseModel):
    """Response body for POST /predict/{scenario_slug}."""

    predictions: list[PredictionOut]


class DatasetSampleOut(BaseModel):
    """Response body for GET /dataset/{scenario_slug}/sample."""

    columns: list[str]
    rows: list[dict[str, object]]
    total_rows: int


class FeatureImportanceOut(BaseModel):
    feature: str
    importance: float


class BreakdownItemOut(BaseModel):
    category: str
    score: float
    n: int


class DatasetEvaluationOut(BaseModel):
    """Response body for GET /dataset/{scenario_slug}/evaluation — a held-out
    evaluation of the deployed pipeline, ready to render as a metrics/predicted-vs-
    actual dashboard. Feature importance lives separately (see
    /dataset/{slug}/explainability) to avoid showing two different notions of
    "importance" in the same place.
    """

    task_type: str
    target: str
    n: int
    metrics: dict[str, float]
    breakdown_feature: str | None
    breakdown: list[BreakdownItemOut]
    actuals: list[float]
    predictions: list[float]
    prediction_lower: list[float] | None = None
    prediction_upper: list[float] | None = None


class DatasetExplainabilityOut(BaseModel):
    """Response body for GET /dataset/{scenario_slug}/explainability — dataset-wide
    global feature importance via mean(|SHAP value|), not a single estimator's
    built-in importances (see core/dataset.py's shap_importance() docstring).
    """

    feature_importance: list[FeatureImportanceOut]
    sample_size: int


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
    try:
        predictions = run_predict(artifacts, records)
    except MissingFeatureColumnsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PredictResponse(
        predictions=[
            PredictionOut(
                prediction=p.prediction,
                contributions=p.contributions,
                prediction_lower=p.prediction_lower,
                prediction_upper=p.prediction_upper,
            )
            for p in predictions
        ]
    )


@router.get("/dataset/{scenario_slug}/sample", response_model=DatasetSampleOut)
def dataset_sample_endpoint(
    limit: int = Query(default=1000, ge=1, le=MAX_ROWS),
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
    model_cache: ModelCache = Depends(_model_cache),
) -> DatasetSampleOut:
    """A real, evenly-spaced sample of the caller's tenant dataset (not the raw file —
    the same cleaned/typed parquet training reads), for "explore the data" UIs.
    """
    assert identity.org_id is not None
    assert definition.dataset is not None  # guaranteed by kind="tabular_ml" filter
    store = model_cache.store_for(definition.slug)
    df = dataset_core.load_normalized(store, identity.org_id)
    columns = [*definition.dataset.feature_columns, definition.dataset.target]
    sample = dataset_core.sample_rows(df, columns, limit)
    return DatasetSampleOut(columns=sample.columns, rows=sample.rows, total_rows=sample.total_rows)


@router.get("/dataset/{scenario_slug}/evaluation", response_model=DatasetEvaluationOut)
def dataset_evaluation_endpoint(
    limit: int = Query(default=1000, ge=1, le=MAX_ROWS),
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
    model_cache: ModelCache = Depends(_model_cache),
) -> DatasetEvaluationOut:
    """A held-out evaluation (metrics, feature importance, predicted-vs-actual) of the
    caller's tenant's deployed pipeline — see core/dataset.py for the reference-vs-
    deployed-weights caveat.
    """
    assert identity.org_id is not None
    artifacts = model_cache.get(identity.org_id, definition.slug)
    store = model_cache.store_for(definition.slug)
    df = dataset_core.load_normalized(store, identity.org_id)
    result = dataset_core.evaluate(artifacts, df, limit)
    return DatasetEvaluationOut(
        task_type=result.task_type,
        target=result.target,
        n=result.n,
        metrics=result.metrics,
        breakdown_feature=result.breakdown_feature,
        breakdown=[BreakdownItemOut(**b) for b in result.breakdown],
        actuals=result.actuals,
        predictions=result.predictions,
        prediction_lower=result.prediction_lower,
        prediction_upper=result.prediction_upper,
    )


@router.get("/dataset/{scenario_slug}/explainability", response_model=DatasetExplainabilityOut)
def dataset_explainability_endpoint(
    # Unlike sample/evaluation, SHAP explanation cost scales ~linearly with row count
    # (≈50s at 10000 rows on churn) — default stays small; `le=MAX_ROWS` still lets a
    # caller opt into a bigger, slower sample explicitly.
    limit: int = Query(default=500, ge=1, le=MAX_ROWS),
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
    model_cache: ModelCache = Depends(_model_cache),
) -> DatasetExplainabilityOut:
    """Dataset-wide global SHAP feature importance for the caller's deployed pipeline."""
    assert identity.org_id is not None
    artifacts = model_cache.get(identity.org_id, definition.slug)
    store = model_cache.store_for(definition.slug)
    df = dataset_core.load_normalized(store, identity.org_id)
    feature_importance, sample_size = dataset_core.shap_importance(artifacts, df, limit)
    return DatasetExplainabilityOut(
        feature_importance=[FeatureImportanceOut(**f) for f in feature_importance],
        sample_size=sample_size,
    )
