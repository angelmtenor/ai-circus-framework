"""
- Title:    Deep-learning inference API
- Author:   ai-circus-framework contributors

Every scenario route resolves the caller through `resolve_identity` (Keycloak token /
ADMIN_API_KEY / engineering-demo key -> org_id + platform-registry entitlement check)
before touching a model, and every model/sample/image read is scoped to that org_id
(with the shared-baseline fallback — see core/model_cache.py). `/admin/runtime` is
admin-bearer only. ui-react calls this service directly from the browser
(dl-inference.localhost), so images are served as authenticated bytes the SPA turns
into blob URLs — never as unauthenticated static files.
"""

from __future__ import annotations

import os
import time
from typing import Any

import onnxruntime as ort
import psutil
from ai_circus_shared.auth import Identity, is_admin_bearer_token
from ai_circus_shared.scenario_schema import ScenarioDefinition
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, model_validator

from dl_inference import get_env_config
from dl_inference.core import inference
from dl_inference.core.identity import resolve_identity
from dl_inference.core.model_cache import DlModelCache, LoadedModel, release_freed_memory

router = APIRouter()

MAX_SIMILAR = 12


class PredictRequest(BaseModel):
    """Exactly one input: free `text`, an uploaded `image_base64`, or a published `sample_id`."""

    text: str | None = None
    image_base64: str | None = None
    sample_id: str | None = None
    explain: bool = True
    # Explain w.r.t. this class key instead of the predicted one ("why not X?").
    target: str | None = None
    similar: int = Field(default=5, ge=0, le=MAX_SIMILAR)

    @model_validator(mode="after")
    def _exactly_one_input(self) -> PredictRequest:
        given = [v for v in (self.text, self.image_base64, self.sample_id) if v is not None]
        if len(given) != 1:
            raise ValueError("Provide exactly one of text, image_base64 or sample_id.")
        return self


class ClassProbability(BaseModel):
    """One class's probability."""

    key: str
    label: str
    probability: float


class PredictResponse(BaseModel):
    """Prediction, explanation and supporting similar cases for one input."""

    predicted: str
    confidence: float
    probabilities: list[ClassProbability]
    explained_class: str | None = None
    explanation: dict[str, Any] | None = None
    similar: list[dict[str, Any]] = []
    # Uploaded images only: the exact (resized, converted) pixels the model saw.
    input_image_png: str | None = None
    latency_ms: float


def _cache(request: Request) -> DlModelCache:
    return request.app.state.model_cache


def _definition(scenario_slug: str, request: Request) -> ScenarioDefinition:
    definitions: dict[str, ScenarioDefinition] = request.app.state.definitions
    definition = definitions.get(scenario_slug)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Scenario {scenario_slug!r} is not served by this instance.")
    return definition


def _model(identity: Identity, definition: ScenarioDefinition, cache: DlModelCache) -> LoadedModel:
    assert identity.org_id is not None  # resolve_identity() 401s otherwise
    return cache.get(identity.org_id, definition.slug)


def require_admin(authorization: str | None = Header(default=None)) -> None:
    """Admin bearer token only (same check as every other service's admin routes)."""
    if not is_admin_bearer_token(authorization, get_env_config().ADMIN_API_KEY.get_secret_value()):
        raise HTTPException(status_code=401, detail="Admin bearer token required.")


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness check."""
    return {"status": "ok"}


@router.get("/models/{scenario_slug}")
def model_info(
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_definition),
    cache: DlModelCache = Depends(_cache),
) -> dict[str, Any]:
    """The deployed model's manifest: base model, device it was trained on, budget,
    learning curves and held-out evaluation (checksums omitted).
    """
    model = _model(identity, definition, cache)
    info = {k: v for k, v in model.metadata.items() if k != "checksums"}
    return {**info, "served_from_org": model.org_id, "runtime": "onnxruntime (CPU)"}


@router.get("/dataset/{scenario_slug}/samples")
def samples(
    label: str | None = Query(default=None, description="Only samples whose true label is this key"),
    limit: int = Query(default=500, ge=1, le=2000),
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_definition),
    cache: DlModelCache = Depends(_cache),
) -> dict[str, Any]:
    """Published held-out samples with their true label and the model's probabilities."""
    model = _model(identity, definition, cache)
    rows = [s for s in model.samples if label is None or s["label"] == label]
    return {"samples": rows[:limit], "total": len(rows)}


@router.get("/dataset/{scenario_slug}/images/{sample_id}")
def sample_image(
    sample_id: str,
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_definition),
    cache: DlModelCache = Depends(_cache),
) -> Response:
    """One published (gallery or reference) sample's PNG."""
    model = _model(identity, definition, cache)
    if sample_id not in model.samples_by_id and sample_id not in set(model.reference.get("ids", [])):
        raise HTTPException(status_code=404, detail=f"Unknown sample {sample_id!r}.")
    if model.metadata.get("modality") != "image":
        raise HTTPException(status_code=404, detail="This scenario has no images.")
    assert identity.org_id is not None
    data = cache.image(identity.org_id, definition.slug, sample_id)
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


def _text_input(body: PredictRequest, model: LoadedModel) -> str:
    """The text to score: a published sample's, or the request's own (validated)."""
    if body.image_base64 is not None:
        raise inference.InvalidInputError("This scenario scores text, not images.")
    if body.sample_id is not None:
        sample = model.samples_by_id.get(body.sample_id)
        if sample is None:
            raise HTTPException(status_code=404, detail=f"Unknown sample {body.sample_id!r}.")
        return sample["text"]
    return inference.clean_text(body.text or "")


@router.post("/predict/{scenario_slug}", response_model=PredictResponse)
def predict(
    body: PredictRequest,
    identity: Identity = Depends(resolve_identity),
    definition: ScenarioDefinition = Depends(_definition),
    cache: DlModelCache = Depends(_cache),
) -> PredictResponse:
    """Score one input; optionally explain it and retrieve similar training cases."""
    started = time.perf_counter()
    model = _model(identity, definition, cache)
    modality = model.metadata["modality"]
    labels: list[dict[str, str]] = model.metadata["labels"]
    keys = [label["key"] for label in labels]

    input_png = None
    try:
        if modality == "text":
            text = _text_input(body, model)
            scored = inference.score_text(model, text)

            def explain(target: int) -> dict[str, Any]:
                return inference.explain_text(model, text, target)

        else:
            if body.text is not None:
                raise inference.InvalidInputError("This scenario scores images, not text.")
            preprocessing = model.metadata["preprocessing"]
            if body.sample_id is not None:
                if body.sample_id not in model.samples_by_id:
                    raise HTTPException(status_code=404, detail=f"Unknown sample {body.sample_id!r}.")
                assert identity.org_id is not None
                raw = cache.image(identity.org_id, definition.slug, body.sample_id)
                image = inference.decode_image(raw, preprocessing)
            else:
                image = inference.decode_base64_image(body.image_base64 or "", preprocessing)
                input_png = inference.png_base64(image)
            scored = inference.score_image(model, image)

            def explain(target: int) -> dict[str, Any]:
                return inference.explain_image(model, image, target, scored.logits)

        predicted = int(scored.probs.argmax())
        target = predicted
        if body.target is not None:
            if body.target not in keys:
                raise inference.InvalidInputError(f"Unknown target class {body.target!r}.")
            target = keys.index(body.target)
        explanation = None
        if body.explain:
            # Published samples are deterministic inputs — reuse their explanation.
            explanation = model.cached_explanation(body.sample_id, target) if body.sample_id else None
            if explanation is None:
                explanation = explain(target)
                release_freed_memory()
                if body.sample_id:
                    model.remember_explanation(body.sample_id, target, explanation)
    except inference.InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return PredictResponse(
        predicted=keys[predicted],
        confidence=round(float(scored.probs[predicted]), 5),
        probabilities=[
            ClassProbability(key=label["key"], label=label["label"], probability=round(float(p), 5))
            for label, p in zip(labels, scored.probs, strict=True)
        ],
        explained_class=keys[target] if body.explain else None,
        explanation=explanation,
        similar=inference.similar_cases(model, scored.embedding, body.similar),
        input_image_png=input_png,
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
    )


@router.get("/admin/runtime", dependencies=[Depends(require_admin)])
def runtime(request: Request, cache: DlModelCache = Depends(_cache)) -> dict[str, Any]:
    """What this pod runs on — for the admin console's Deep Learning tab."""
    config = get_env_config()
    return {
        "onnxruntime_version": ort.__version__,
        "execution_providers": ["CPUExecutionProvider"],
        "available_providers": ort.get_available_providers(),
        "cpu_count": os.cpu_count(),
        "threads_per_session": int(config.ORT_THREADS),
        "rss_mb": round(psutil.Process().memory_info().rss / 1e6, 1),
        "scenarios": sorted(request.app.state.definitions),
        "cached_models": [{"org_id": org, "scenario_slug": slug} for org, slug in cache.cached_keys()],
    }
