"""Tests for the dl-inference HTTP API with identity/scenario/cache dependencies
overridden — models themselves are real (tiny) ONNX graphs from tests/fixtures.py.
"""

from __future__ import annotations

import base64
from collections.abc import Generator
from types import SimpleNamespace

import pytest
from ai_circus_shared.auth import Identity
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dl_inference import api
from dl_inference.core.identity import resolve_identity
from dl_inference.core.model_cache import DlModelCache
from tests.conftest import FakeSecret
from tests.fixtures import MemoryStore, bright_quadrant_image, png, publish


def _client(modality: str) -> TestClient:
    store = MemoryStore()
    publish(store, "demo", modality)
    app = FastAPI()
    app.include_router(api.router)
    cache = DlModelCache({"scn": store}, fallback_org_id="demo", threads=1)  # type: ignore[dict-item]
    app.state.definitions = {"scn": SimpleNamespace(slug="scn")}
    app.state.model_cache = cache
    app.dependency_overrides[resolve_identity] = lambda: Identity(subject="u", org_id="acme", roles=frozenset())
    return TestClient(app)


@pytest.fixture
def text_client() -> Generator[TestClient]:
    yield _client("text")


@pytest.fixture
def image_client() -> Generator[TestClient]:
    yield _client("image")


def test_healthz(text_client: TestClient) -> None:
    assert text_client.get("/healthz").json() == {"status": "ok"}


def test_unknown_scenario_is_404(text_client: TestClient) -> None:
    assert text_client.get("/models/nope").status_code == 404


def test_model_info_hides_checksums_and_reports_the_source_org(text_client: TestClient) -> None:
    body = text_client.get("/models/scn").json()
    assert body["modality"] == "text"
    assert body["served_from_org"] == "demo"
    assert "checksums" not in body


def test_samples_filter_by_label(text_client: TestClient) -> None:
    assert text_client.get("/dataset/scn/samples").json()["total"] == 2
    body = text_client.get("/dataset/scn/samples", params={"label": "dengue"}).json()
    assert [s["id"] for s in body["samples"]] == ["s-0"]


def test_predict_text_with_explanation_and_similar_cases(text_client: TestClient) -> None:
    body = text_client.post("/predict/scn", json={"text": "i have fever and rash", "similar": 1}).json()
    assert body["predicted"] == "dengue"
    assert [p["key"] for p in body["probabilities"]] == ["dengue", "common cold"]
    assert body["explanation"]["type"] == "tokens"
    assert body["explained_class"] == "dengue"
    assert body["similar"][0]["id"] == "r-0"
    assert body["input_image_png"] is None


def test_predict_text_why_not_another_class(text_client: TestClient) -> None:
    body = text_client.post("/predict/scn", json={"text": "fever and nose", "target": "common cold"}).json()
    assert body["explained_class"] == "common cold"


def test_predict_published_sample_caches_its_explanation(text_client: TestClient) -> None:
    first = text_client.post("/predict/scn", json={"sample_id": "s-1"}).json()
    assert first["predicted"] == "common cold"
    model = text_client.app.state.model_cache.get("acme", "scn")  # type: ignore[attr-defined]
    assert model.cached_explanation("s-1", 1) == first["explanation"]


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({}, 422),
        ({"text": "a", "sample_id": "s-0"}, 422),
        ({"text": "   "}, 422),
        ({"image_base64": "AAAA"}, 422),
        ({"text": "fever", "target": "nope"}, 422),
        ({"sample_id": "s-99"}, 404),
    ],
)
def test_predict_text_rejects_bad_requests(text_client: TestClient, payload: dict, status: int) -> None:
    assert text_client.post("/predict/scn", json=payload).status_code == status


def test_text_scenario_serves_no_images(text_client: TestClient) -> None:
    assert text_client.get("/dataset/scn/images/s-0").status_code == 404


def test_image_endpoints(image_client: TestClient) -> None:
    response = image_client.get("/dataset/scn/images/s-0")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert image_client.get("/dataset/scn/images/r-0").status_code == 200
    assert image_client.get("/dataset/scn/images/s-7").status_code == 404


def test_predict_uploaded_image_returns_heatmap_and_processed_pixels(image_client: TestClient) -> None:
    payload = {"image_base64": base64.b64encode(png(bright_quadrant_image())).decode(), "similar": 1}
    body = image_client.post("/predict/scn", json=payload).json()
    assert body["predicted"] == "1"
    assert body["explanation"]["type"] == "heatmap"
    assert len(body["explanation"]["grid"]) == 4
    assert base64.b64decode(body["input_image_png"]).startswith(b"\x89PNG")
    assert body["similar"][0]["id"] == "r-0"


def test_predict_image_sample_and_modality_mismatch(image_client: TestClient) -> None:
    assert image_client.post("/predict/scn", json={"sample_id": "s-0", "explain": False}).json()["explanation"] is None
    assert image_client.post("/predict/scn", json={"text": "fever"}).status_code == 422
    assert image_client.post("/predict/scn", json={"sample_id": "s-9"}).status_code == 404


def test_admin_runtime_requires_the_admin_token(text_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    config = SimpleNamespace(ADMIN_API_KEY=FakeSecret("adm"), ORT_THREADS="2")
    monkeypatch.setattr(api, "get_env_config", lambda: config)
    assert text_client.get("/admin/runtime").status_code == 401
    body = text_client.get("/admin/runtime", headers={"Authorization": "Bearer adm"}).json()
    assert body["execution_providers"] == ["CPUExecutionProvider"]
    assert body["scenarios"] == ["scn"]
    assert body["threads_per_session"] == 2
