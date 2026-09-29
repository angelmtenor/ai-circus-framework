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


def _definition(slug: str) -> SimpleNamespace:
    """A stand-in ScenarioDefinition with a tabular dataset and no text features."""
    return SimpleNamespace(slug=slug, dataset=SimpleNamespace(text_columns=list, feature_schema={}))


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
    def shap_values(x: object, *, check_additivity: bool = True) -> np.ndarray:
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
        metadata={
            "feature_columns": ["CreditScore", "Geography"],
            "transformed_feature_names": ["f1", "f2"],
            "task_type": "classification",
        },
    )

    class FakeModelCache(ModelCache):
        def __init__(self) -> None:
            pass

        def get(self, org_id: str, scenario_slug: str) -> ModelArtifacts:
            return artifacts

    app.dependency_overrides[resolve_identity] = lambda: Identity(
        subject="user-1", org_id="org-1", roles=frozenset({"scenario:churn"})
    )
    app.dependency_overrides[_scenario_definition] = lambda: _definition("churn")
    fake_model_cache = FakeModelCache()
    app.dependency_overrides[_model_cache] = lambda: fake_model_cache
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_healthz(client: TestClient) -> None:
    """/healthz reports ok."""
    assert client.get("/healthz").json() == {"status": "ok"}


def test_predict_returns_probability_and_contributions(client: TestClient) -> None:
    """POST /predict/{scenario_slug} returns one prediction+contributions entry per record."""
    response = client.post("/predict/churn", json={"records": [{"CreditScore": 600, "Geography": "France"}]})

    assert response.status_code == 200
    body = response.json()
    assert len(body["predictions"]) == 1
    assert body["predictions"][0]["prediction"] == pytest.approx(0.6)
    assert body["predictions"][0]["contributions"] == {"f1": 0.1, "f2": -0.2}


def test_predict_returns_422_for_missing_feature_column(client: TestClient) -> None:
    """A record missing a required feature_column is a 422 validation error, not a 500."""
    response = client.post("/predict/churn", json={"records": [{"CreditScore": 600}]})

    assert response.status_code == 422
    assert "Geography" in response.json()["detail"]


def test_predict_matches_real_predict_function(client: TestClient) -> None:
    """Sanity check that the fakes used here satisfy predict()'s real call contract."""
    import pandas as pd

    artifacts = ModelArtifacts(
        pipeline=FakePipeline(),
        explainer=FakeExplainer(),
        metadata={
            "feature_columns": ["CreditScore", "Geography"],
            "transformed_feature_names": ["f1", "f2"],
            "task_type": "classification",
        },
    )
    records = pd.DataFrame([{"CreditScore": 600, "Geography": "France"}])

    results = real_predict(artifacts, records)

    assert results == [PredictionResult(prediction=0.6, contributions={"f1": 0.1, "f2": -0.2})]


def test_predict_rejects_a_batch_larger_than_max_rows(client: TestClient) -> None:
    """Every record is scored and SHAP-explained — an unbounded batch is refused up front."""
    from prediction.api import MAX_ROWS

    response = client.post("/predict/churn", json={"records": [{}] * (MAX_ROWS + 1)})

    assert response.status_code == 422


def test_model_card_serves_training_metadata() -> None:
    """GET /model/{slug}/card exposes the model card fields training wrote."""
    from fastapi import FastAPI

    metadata = {
        "model_name": "lightgbm_small_data",
        "task_type": "classification",
        "target": "Survived",
        "test_score": 0.82,
        "selection_metric": "roc_auc",
        "cv_folds": 5,
        "candidate_scores": [
            {"name": "lightgbm_small_data", "selection_score": 0.88, "metrics": {"cv_roc_auc_mean": 0.88}}
        ],
        "metrics": {"holdout_roc_auc": 0.87},
        "holdout_evaluation": {
            "n": 4,
            "threshold": 0.5,
            "positive_rate": 0.5,
            "roc_curve": {"fpr": [0.0, 1.0], "tpr": [0.0, 1.0]},
            "confusion_matrix": {"tn": 1, "fp": 1, "fn": 1, "tp": 1},
        },
        "global_feature_importance": [{"feature": "Sex", "importance": 0.2}],
        "training_rows": 712,
        "holdout_rows": 179,
    }

    class CardModelCache(ModelCache):
        def __init__(self) -> None:
            pass

        def get(self, org_id: str, scenario_slug: str) -> ModelArtifacts:
            return ModelArtifacts(pipeline=FakePipeline(), explainer=FakeExplainer(), metadata=metadata)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[resolve_identity] = lambda: Identity(subject="u", org_id="org-1", roles=frozenset())
    app.dependency_overrides[_scenario_definition] = lambda: SimpleNamespace(slug="titanic")
    cache = CardModelCache()
    app.dependency_overrides[_model_cache] = lambda: cache

    body = TestClient(app).get("/model/titanic/card").json()

    assert body["selection_metric"] == "roc_auc"
    assert body["candidates"][0]["metrics"] == {"cv_roc_auc_mean": 0.88}
    assert body["holdout_evaluation"]["confusion_matrix"]["tp"] == 1
    assert body["global_feature_importance"] == [{"feature": "Sex", "importance": 0.2}]


def test_model_card_tolerates_metadata_from_before_the_model_card() -> None:
    """A model trained before these fields existed still returns a (sparser) card."""
    from fastapi import FastAPI

    legacy = {"model_name": "lightgbm", "task_type": "classification", "target": "Exited", "test_score": 0.86}

    class LegacyModelCache(ModelCache):
        def __init__(self) -> None:
            pass

        def get(self, org_id: str, scenario_slug: str) -> ModelArtifacts:
            return ModelArtifacts(pipeline=FakePipeline(), explainer=FakeExplainer(), metadata=legacy)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[resolve_identity] = lambda: Identity(subject="u", org_id="org-1", roles=frozenset())
    app.dependency_overrides[_scenario_definition] = lambda: SimpleNamespace(slug="churn")
    cache = LegacyModelCache()
    app.dependency_overrides[_model_cache] = lambda: cache

    body = TestClient(app).get("/model/churn/card").json()

    assert body["model_name"] == "lightgbm"
    assert body["candidates"] == [] and body["holdout_evaluation"] is None


def _graph_app(
    definition: SimpleNamespace, artifacts: ModelArtifacts | None = None, graph: object = None
) -> tuple[TestClient, list[str]]:
    """A TestClient serving `definition`, whose cache returns `artifacts` / `graph` and records graph() calls."""
    from fastapi import FastAPI

    calls: list[str] = []

    class GraphModelCache(ModelCache):
        def __init__(self) -> None:
            pass

        def get(self, org_id: str, scenario_slug: str) -> ModelArtifacts:
            assert artifacts is not None
            return artifacts

        def graph(self, org_id: str, scenario_slug: str) -> object:  # type: ignore[override]
            calls.append(org_id)
            return graph

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[resolve_identity] = lambda: Identity(subject="u", org_id="org-1", roles=frozenset())
    app.dependency_overrides[_scenario_definition] = lambda: definition
    cache = GraphModelCache()
    app.dependency_overrides[_model_cache] = lambda: cache
    return TestClient(app), calls


def test_graph_endpoint_serves_the_tenants_graph() -> None:
    from ai_circus_shared.network_graph import NetworkGraph

    graph = NetworkGraph.model_validate({
        "nodes": [{"id": "A", "kind": "row"}, {"id": "e", "kind": "entity", "label": "LJM"}],
        "edges": [{"source": "A", "target": "e", "kind": "role"}],
    })
    definition = SimpleNamespace(slug="enron", dataset=SimpleNamespace(graph=SimpleNamespace(seed_file="g.json")))
    client, calls = _graph_app(definition, graph=graph)

    body = client.get("/graph/enron").json()

    assert calls == ["org-1"]
    assert body["nodes"][1] == {"id": "e", "kind": "entity", "label": "LJM"}  # None fields left out
    assert body["edges"] == [{"source": "A", "target": "e", "kind": "role", "weight": 1.0, "series": {}}]


def test_graph_endpoint_404s_for_a_scenario_without_a_graph() -> None:
    client, calls = _graph_app(SimpleNamespace(slug="churn", dataset=SimpleNamespace(graph=None)))
    response = client.get("/graph/churn")
    assert response.status_code == 404 and calls == []


def test_graph_endpoint_checks_the_entitlement_before_touching_the_graph() -> None:
    from fastapi import HTTPException

    client, calls = _graph_app(SimpleNamespace(slug="enron", dataset=SimpleNamespace(graph=SimpleNamespace())))

    def deny() -> Identity:
        raise HTTPException(status_code=403, detail="not entitled")

    client.app.dependency_overrides[resolve_identity] = deny  # type: ignore[attr-defined]
    assert client.get("/graph/enron").status_code == 403
    assert calls == []


def test_out_of_fold_endpoint_serves_every_rows_cross_fitted_score() -> None:
    scores = {
        "model_name": "lightgbm_small_data",
        "folds": 5,
        "roc_auc": 0.81,
        "rows": {"LAY KENNETH L": {"probability": 0.61, "fold": 2, "contributions": {"num__salary": 0.05}}},
    }
    artifacts = ModelArtifacts(pipeline=FakePipeline(), explainer=FakeExplainer(), metadata={}, out_of_fold=scores)
    client, _ = _graph_app(SimpleNamespace(slug="enron"), artifacts=artifacts)

    body = client.get("/model/enron/out-of-fold").json()

    assert body == {
        "model_name": "lightgbm_small_data",
        "folds": 5,
        "roc_auc": 0.81,
        "rows": [{"id": "LAY KENNETH L", "probability": 0.61, "fold": 2, "contributions": {"num__salary": 0.05}}],
    }


def test_out_of_fold_endpoint_404s_when_the_model_has_none() -> None:
    artifacts = ModelArtifacts(pipeline=FakePipeline(), explainer=FakeExplainer(), metadata={})
    client, _ = _graph_app(SimpleNamespace(slug="churn"), artifacts=artifacts)
    assert client.get("/model/churn/out-of-fold").status_code == 404
