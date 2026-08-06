"""Tests for the /predict FastAPI endpoint, with identity + model cache dependencies overridden."""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from typing import ClassVar

import numpy as np
import pytest
from ai_circus_shared.auth import Identity
from fastapi.testclient import TestClient

from prediction.api import _model_cache, _scenario_definition, router
from prediction.core.identity import resolve_identity
from prediction.core.model_cache import ModelArtifacts, ModelCache
from prediction.core.predict import PredictionResult
from prediction.core.predict import predict as real_predict


class FakePipeline:
    """Stand-in whose predict_proba/named_steps satisfy predict()'s interface."""

    class _Preprocessor:
        @staticmethod
        def transform(x: object) -> object:
            return x

    named_steps: ClassVar = {"preprocessor": _Preprocessor()}

    @staticmethod
    def predict_proba(x: object) -> np.ndarray:
        """Return a fixed [P(0), P(1)] pair for every record."""
        return np.array([[0.4, 0.6] for _ in range(len(x))])


class FakeExplainer:
    """Stand-in whose shap_values() satisfies predict()'s interface."""

    @staticmethod
    def shap_values(x: object) -> np.ndarray:
        """Return fixed per-feature contributions for every record."""
        return np.array([[0.1, -0.2] for _ in range(len(x))])


@pytest.fixture
def client() -> Generator[TestClient]:
    """A TestClient with identity + model cache dependencies overridden by fakes."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    artifacts = ModelArtifacts(
        pipeline=FakePipeline(),
        explainer=FakeExplainer(),
        metadata={"feature_columns": ["CreditScore", "Geography"], "transformed_feature_names": ["f1", "f2"]},
    )

    class FakeModelCache(ModelCache):
        def __init__(self) -> None:
            pass

        def get(self, org_id: str, scenario_slug: str) -> ModelArtifacts:
            return artifacts

    app.dependency_overrides[resolve_identity] = lambda: Identity(
        subject="user-1", org_id="org-1", roles=frozenset({"scenario:churn"})
    )
    app.dependency_overrides[_scenario_definition] = lambda: SimpleNamespace(slug="churn")
    fake_model_cache = FakeModelCache()
    app.dependency_overrides[_model_cache] = lambda: fake_model_cache
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_healthz(client: TestClient) -> None:
    """/healthz reports ok."""
    assert client.get("/healthz").json() == {"status": "ok"}


def test_predict_returns_probability_and_contributions(client: TestClient) -> None:
    """POST /predict/{scenario_slug} returns one probability+contributions entry per record."""
    response = client.post("/predict/churn", json={"records": [{"CreditScore": 600, "Geography": "France"}]})

    assert response.status_code == 200
    body = response.json()
    assert len(body["predictions"]) == 1
    assert body["predictions"][0]["probability"] == pytest.approx(0.6)
    assert body["predictions"][0]["contributions"] == {"f1": 0.1, "f2": -0.2}


def test_predict_matches_real_predict_function(client: TestClient) -> None:
    """Sanity check that the fakes used here satisfy predict()'s real call contract."""
    import pandas as pd

    artifacts = ModelArtifacts(
        pipeline=FakePipeline(),
        explainer=FakeExplainer(),
        metadata={"feature_columns": ["CreditScore", "Geography"], "transformed_feature_names": ["f1", "f2"]},
    )
    records = pd.DataFrame([{"CreditScore": 600, "Geography": "France"}])

    results = real_predict(artifacts, records)

    assert results == [PredictionResult(probability=0.6, contributions={"f1": 0.1, "f2": -0.2})]
