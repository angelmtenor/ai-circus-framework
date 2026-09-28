"""
- Title:    Prediction API
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import httpx
import pandas as pd
from ai_circus_shared.auth import Identity
from ai_circus_shared.embeddings import GatewayEmbeddingProvider
from ai_circus_shared.scenario_schema import ScenarioDefinition
from ai_circus_shared.tabular_ml import MAX_DATASET_ROWS
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from prediction import get_env_config
from prediction.core import dataset as dataset_core
from prediction.core.identity import resolve_identity
from prediction.core.model_cache import ModelCache
from prediction.core.predict import (
    MAX_TEXT_EXPLAIN_RECORDS,
    ChallengerUnavailableError,
    MissingFeatureColumnsError,
    predict_challenger,
)
from prediction.core.predict import predict as run_predict

router = APIRouter()

# One standardized ceiling for every row-limited dataset endpoint below (sample,
# evaluation, explainability) — matches etl-tabular's own dataset row cap
# (ai_circus_shared.tabular_ml.MAX_DATASET_ROWS), so a caller can never request more
# rows than a tenant's dataset could ever actually contain.
MAX_ROWS = MAX_DATASET_ROWS
# One text challenger call embeds up to MAX_TEXT_EXPLAIN_RECORDS short texts on the
# gateway's CPU (~0.6 s each).
GATEWAY_TIMEOUT_SECONDS = 60.0


class PredictRequest(BaseModel):
    """One or more records to score, each a mapping of feature name -> value. Capped at
    MAX_ROWS — every record is scored and SHAP-explained, so an unbounded batch would
    let one request pin this service's CPU and memory.
    """

    records: list[dict[str, object]] = Field(max_length=MAX_ROWS)
    # Also return, per free-text feature, how its contribution splits over the words
    # of each record's text — for a person reading one review (at most
    # MAX_TEXT_EXPLAIN_RECORDS records), not for bulk scoring.
    explain_text: bool = False
    # False = probabilities only, no SHAP (empty contributions) — for scoring a whole
    # dataset at once (e.g. ui-react's Voyage/Office scenes), which explains a person
    # only when they are clicked.
    explain: bool = True
    # "challenger" = the scenario's sentence-embedding text challenger instead of the
    # deployed model (at most MAX_TEXT_EXPLAIN_RECORDS records: each is embedded live
    # by llm-gateway) — see ai_circus_shared.scenario_schema.TextChallenger.
    model: Literal["champion", "challenger"] = "champion"


class TokenWeightOut(BaseModel):
    """One word of a text feature's value and its share of that feature's SHAP
    contribution (`start`/`end` are character offsets into the submitted text).
    """

    text: str
    start: int
    end: int
    weight: float


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
    text_explanations: dict[str, list[TokenWeightOut]] | None = None


class PredictResponse(BaseModel):
    """Response body for POST /predict/{scenario_slug}."""

    predictions: list[PredictionOut]


class DatasetSampleOut(BaseModel):
    """Response body for GET /dataset/{scenario_slug}/sample. `columns` starts with
    `id_column` (the scenario's row identifier) and then `display_columns` (e.g. a
    name) when the dataset has them — never model inputs, always shown first.
    """

    columns: list[str]
    rows: list[dict[str, object]]
    total_rows: int
    id_column: str | None = None
    display_columns: list[str] = []


class FeatureImportanceOut(BaseModel):
    """A single feature's global importance score."""

    feature: str
    importance: float


class CandidateScoreOut(BaseModel):
    """One training candidate's selection score and every metric recorded for it."""

    name: str
    selection_score: float
    metrics: dict[str, float]


class RocCurveOut(BaseModel):
    """ROC curve points (false-positive rate vs true-positive rate)."""

    fpr: list[float]
    tpr: list[float]


class HoldoutEvaluationOut(BaseModel):
    """The selected classifier on the untouched hold-out, scored before the final
    refit — see training's core/training.py holdout_evaluation().
    """

    n: int
    threshold: float
    positive_rate: float
    roc_curve: RocCurveOut
    confusion_matrix: dict[str, int]


class TextTermOut(BaseModel):
    """One vocabulary term of a text feature: its mean SHAP value over the training
    documents containing it, and how many documents that is.
    """

    term: str
    weight: float
    docs: int


class TextTermsOut(BaseModel):
    """A text feature's strongest terms in each direction (see training's
    text_term_importance()).
    """

    positive: list[TextTermOut] = []
    negative: list[TextTermOut] = []


class ChallengerCardOut(BaseModel):
    """The sentence-embedding text challenger, scored on the deployed model's split."""

    name: str
    label: str
    embedding_model: str
    hf_model_id: str
    embedding_dim: int
    selection_score: float
    metrics: dict[str, float] = {}
    holdout_evaluation: HoldoutEvaluationOut | None = None
    global_feature_importance: list[FeatureImportanceOut] = []
    gain_over_deployed: float | None = None
    would_be_adopted: bool = False


class ModelCardOut(BaseModel):
    """Response body for GET /model/{scenario_slug}/card — how the deployed model was
    chosen and how it scores on data it never saw (from training's metadata.json).
    Fields added after a model was trained are None/empty until it is retrained.
    """

    model_name: str
    task_type: str
    target: str
    test_score: float
    selection_metric: str | None = None
    cv_folds: int | None = None
    accuracy_gain_threshold_for_complexity: float | None = None
    candidates: list[CandidateScoreOut] = []
    metrics: dict[str, float] = {}
    holdout_evaluation: HoldoutEvaluationOut | None = None
    global_feature_importance: list[FeatureImportanceOut] = []
    training_rows: int | None = None
    holdout_rows: int | None = None
    text_columns: list[str] = []
    text_term_importance: dict[str, TextTermsOut] = {}
    challenger: ChallengerCardOut | None = None


class BreakdownItemOut(BaseModel):
    """Evaluation score for one category of a breakdown feature."""

    category: str
    score: float
    n: int


class DatasetEvaluationOut(BaseModel):
    """Response body for GET /dataset/{scenario_slug}/evaluation — a held-out
    evaluation of the deployed pipeline, ready to render as a metrics/predicted-vs-
    actual dashboard. Feature importance lives separately (see
    /dataset/{slug}/explainability) to avoid showing two different notions of
    "importance" in the same place.

    `feature_values` are the same held-out rows' raw feature columns, aligned
    index-for-index with `actuals`/`predictions` (see EvaluationResult's docstring).
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
    feature_values: dict[str, list[object]]


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
    request: Request,
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
    model_cache: ModelCache = Depends(_model_cache),
) -> PredictResponse:
    """Score one or more records for the caller's tenant; org_id comes from their token."""
    # resolve_identity() already guarantees org_id is set (401s otherwise).
    assert identity.org_id is not None
    _check_text_values(body, definition)
    artifacts = model_cache.get(identity.org_id, definition.slug)
    records = pd.DataFrame(body.records)
    try:
        if body.model == "challenger":
            embedding_model = (artifacts.metadata.get("challenger") or {}).get("embedding_model", "local-embed")
            predictions = predict_challenger(artifacts, records, _gateway_embedder(request, embedding_model))
        else:
            predictions = run_predict(artifacts, records, explain=body.explain, explain_text=body.explain_text)
    except MissingFeatureColumnsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ChallengerUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PredictResponse(
        predictions=[
            PredictionOut(
                prediction=p.prediction,
                contributions=p.contributions,
                prediction_lower=p.prediction_lower,
                prediction_upper=p.prediction_upper,
                text_explanations=(
                    {column: [TokenWeightOut(**t) for t in tokens] for column, tokens in p.text_explanations.items()}
                    if p.text_explanations is not None
                    else None
                ),
            )
            for p in predictions
        ]
    )


def _gateway_embedder(request: Request, embedding_model: str) -> Callable[[list[str]], list[list[float]]]:
    """llm-gateway's `embedding_model` as a texts -> vectors function, one pooled client
    per model (no start-up probe: a request must fail fast, not wait for the gateway).
    503 when this instance has no gateway configured.
    """
    config = get_env_config()
    if not config.LLM_GATEWAY_URL or config.LLM_GATEWAY_API_KEY is None:
        raise HTTPException(status_code=503, detail="The text challenger needs LLM_GATEWAY_URL/LLM_GATEWAY_API_KEY.")
    clients: dict[str, GatewayEmbeddingProvider] = request.app.state.embedding_clients
    if embedding_model not in clients:
        clients[embedding_model] = GatewayEmbeddingProvider(
            config.LLM_GATEWAY_URL,
            config.LLM_GATEWAY_API_KEY.get_secret_value(),
            embedding_model,
            probe=False,
            timeout=GATEWAY_TIMEOUT_SECONDS,
        )
    provider = clients[embedding_model]

    def embed(texts: list[str]) -> list[list[float]]:
        try:
            return provider.encode_documents(texts)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"llm-gateway embedding failed: {exc}") from exc

    return embed


def _check_text_values(body: PredictRequest, definition: ScenarioDefinition) -> None:
    """422 on a free-text value longer than its schema's `max_length` (TF-IDF + SHAP
    cost grows with the text), or on `explain_text` for a bulk request.
    """
    assert definition.dataset is not None
    if (body.explain_text or body.model == "challenger") and len(body.records) > MAX_TEXT_EXPLAIN_RECORDS:
        raise HTTPException(
            status_code=422,
            detail=f"explain_text / the challenger support at most {MAX_TEXT_EXPLAIN_RECORDS} records per request.",
        )
    for column in definition.dataset.text_columns():
        max_length = definition.dataset.feature_schema[column].max_length  # type: ignore[union-attr]
        for record in body.records:
            value = record.get(column)
            if value is not None and len(str(value)) > max_length:
                raise HTTPException(status_code=422, detail=f"{column!r} is longer than {max_length} characters.")


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
    df = model_cache.dataset(identity.org_id, definition.slug)
    columns = [*definition.dataset.feature_columns, definition.dataset.target]
    sample = dataset_core.sample_rows(
        df,
        columns,
        limit,
        id_column=definition.dataset.index_col,
        display_columns=definition.dataset.display_columns,
    )
    id_column = definition.dataset.index_col if definition.dataset.index_col in sample.columns else None
    return DatasetSampleOut(
        columns=sample.columns,
        rows=sample.rows,
        total_rows=sample.total_rows,
        id_column=id_column,
        display_columns=[c for c in definition.dataset.display_columns if c in sample.columns],
    )


@router.get("/model/{scenario_slug}/card", response_model=ModelCardOut)
def model_card_endpoint(
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_scenario_definition),
    model_cache: ModelCache = Depends(_model_cache),
) -> ModelCardOut:
    """The caller's deployed model card: candidates, selection protocol, leakage-free
    hold-out metrics/ROC/confusion matrix, and global SHAP importance.
    """
    assert identity.org_id is not None
    metadata = model_cache.get(identity.org_id, definition.slug).metadata
    return ModelCardOut(
        model_name=metadata["model_name"],
        task_type=metadata["task_type"],
        target=metadata["target"],
        test_score=metadata["test_score"],
        selection_metric=metadata.get("selection_metric"),
        cv_folds=metadata.get("cv_folds"),
        accuracy_gain_threshold_for_complexity=metadata.get("accuracy_gain_threshold_for_complexity"),
        candidates=[CandidateScoreOut(**c) for c in metadata.get("candidate_scores") or []],
        metrics=metadata.get("metrics") or {},
        holdout_evaluation=metadata.get("holdout_evaluation"),
        global_feature_importance=[FeatureImportanceOut(**f) for f in metadata.get("global_feature_importance") or []],
        training_rows=metadata.get("training_rows"),
        holdout_rows=metadata.get("holdout_rows"),
        text_columns=metadata.get("text_columns") or [],
        text_term_importance={
            column: TextTermsOut(**terms) for column, terms in (metadata.get("text_term_importance") or {}).items()
        },
        challenger=ChallengerCardOut(**metadata["challenger"]) if metadata.get("challenger") else None,
    )


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
    df = model_cache.dataset(identity.org_id, definition.slug)
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
        feature_values=result.feature_values,
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
    df = model_cache.dataset(identity.org_id, definition.slug)
    feature_importance, sample_size = dataset_core.shap_importance(artifacts, df, limit)
    return DatasetExplainabilityOut(
        feature_importance=[FeatureImportanceOut(**f) for f in feature_importance],
        sample_size=sample_size,
    )
