"""Free-text (`type: text`) features end to end in prediction: a real TF-IDF +
logistic-regression pipeline, as training builds it for toxic_leadership.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import shap
from ai_circus_shared.auth import Identity
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from prediction.api import _model_cache, _scenario_definition, router
from prediction.core.dataset import evaluate, shap_importance
from prediction.core.identity import resolve_identity
from prediction.core.model_cache import ModelArtifacts, ModelCache
from prediction.core.predict import explain_tokens, predict

BAD = ["micromanagement everywhere", "upper management lies", "toxic politics", "no direction at all"]
GOOD = ["supportive lead", "great mentoring", "transparent roadmap", "trust and autonomy"]


def _dataset(n: int = 240) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, size=n)
    return pd.DataFrame({
        "pay": rng.integers(1, 6, size=n).astype(float),
        "family": pd.Categorical(rng.choice(["Data", "Software"], size=n)),
        "Review": [f"{rng.choice(BAD if t else GOOD)}. The office is old." for t in y],
        "bad": y,
    })


@pytest.fixture(scope="module")
def artifacts() -> ModelArtifacts:
    """A fitted pipeline + LinearExplainer + metadata shaped exactly like training's."""
    df = _dataset()
    features = ["pay", "family", "Review"]
    pipeline = Pipeline([
        (
            "preprocessor",
            ColumnTransformer([
                ("num", StandardScaler(), ["pay"]),
                ("cat", OneHotEncoder(handle_unknown="ignore"), ["family"]),
                ("text_Review", TfidfVectorizer(ngram_range=(1, 2), stop_words=["the", "is", "at", "and"]), "Review"),
            ]),
        ),
        ("model", LogisticRegression(max_iter=1000)),
    ])
    pipeline.fit(df[features], df["bad"])
    transformed = pipeline.named_steps["preprocessor"].transform(df[features])
    explainer = shap.LinearExplainer(
        pipeline.named_steps["model"], (np.asarray(transformed.mean(axis=0)).ravel(), None)
    )
    return ModelArtifacts(
        pipeline=pipeline,
        explainer=explainer,
        metadata={
            "feature_columns": features,
            "transformed_feature_names": list(pipeline.named_steps["preprocessor"].get_feature_names_out()),
            "task_type": "classification",
            "target": "bad",
            "text_columns": ["Review"],
        },
    )


def test_text_terms_are_summed_into_one_contribution(artifacts: ModelArtifacts) -> None:
    records = pd.DataFrame([
        {"pay": 3, "family": "Data", "Review": "Toxic politics and upper management lies."},
        {"pay": 3, "family": "Data", "Review": None},  # missing text is scored as ""
    ])
    results = predict(artifacts, records)

    assert set(results[0].contributions) == {"num__pay", "cat__family_Data", "cat__family_Software", "Review"}
    assert results[0].contributions["Review"] > 0
    assert results[0].prediction > results[1].prediction
    assert results[0].text_explanations is None


def test_explain_text_splits_the_contribution_over_the_words(artifacts: ModelArtifacts) -> None:
    text = "Toxic politics, but a supportive lead."
    [result] = predict(artifacts, pd.DataFrame([{"pay": 3, "family": "Data", "Review": text}]), explain_text=True)
    assert result.text_explanations is not None
    tokens = result.text_explanations["Review"]

    assert [t["text"] for t in tokens] == ["Toxic", "politics", "but", "supportive", "lead"]
    assert all(text[t["start"] : t["end"]] == t["text"] for t in tokens)
    weight = {t["text"]: t["weight"] for t in tokens}
    assert weight["Toxic"] > 0 and weight["politics"] > 0
    assert weight["supportive"] < 0 and weight["lead"] < 0
    assert weight["but"] == 0  # not in the vocabulary
    # The words present explain part of the total; the rest is terms *absent* from it.
    assert abs(sum(weight.values())) <= abs(result.contributions["Review"]) + 1.0


def test_explain_tokens_shares_repeated_terms_between_occurrences(artifacts: ModelArtifacts) -> None:
    names = artifacts.metadata["transformed_feature_names"]
    row = np.zeros(len(names))
    row[names.index("text_Review__toxic")] = 0.6
    tokens = explain_tokens(artifacts, "Review", "toxic, toxic", row, names)
    assert [t["weight"] for t in tokens] == [0.3, 0.3]


def test_dataset_importance_rolls_terms_up_and_never_breaks_down_by_text(artifacts: ModelArtifacts) -> None:
    df = _dataset()
    importance, n = shap_importance(artifacts, df, 100)
    assert n == 100
    assert {item["feature"] for item in importance} == {"pay", "family", "Review"}
    assert importance[0]["feature"] == "Review"

    result = evaluate(artifacts, df, 1000)
    assert result.breakdown_feature == "family"


class _Cache(ModelCache):
    def __init__(self, artifacts: ModelArtifacts) -> None:
        self._artifacts = artifacts

    def get(self, org_id: str, scenario_slug: str) -> ModelArtifacts:
        return self._artifacts


def _client(artifacts: ModelArtifacts) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[resolve_identity] = lambda: Identity(subject="u", org_id="org-1", roles=frozenset())
    dataset = SimpleNamespace(
        text_columns=lambda: ["Review"],
        feature_schema={"Review": SimpleNamespace(type="text", max_length=60)},
        business_rules=None,
    )
    app.dependency_overrides[_scenario_definition] = lambda: SimpleNamespace(
        slug="toxic_leadership", dataset=dataset, model=SimpleNamespace(decision_policy=None)
    )
    cache = _Cache(artifacts)
    app.dependency_overrides[_model_cache] = lambda: cache
    return TestClient(app)


def test_api_returns_token_explanations_and_guards_text_requests(artifacts: ModelArtifacts) -> None:
    client = _client(artifacts)
    record = {"pay": 2, "family": "Software", "Review": "No direction at all."}

    body = client.post("/predict/toxic_leadership", json={"records": [record], "explain_text": True}).json()
    tokens = body["predictions"][0]["text_explanations"]["Review"]
    assert [t["text"] for t in tokens] == ["No", "direction", "at", "all"]
    assert "Review" in body["predictions"][0]["contributions"]

    plain = client.post("/predict/toxic_leadership", json={"records": [record]}).json()
    assert plain["predictions"][0]["text_explanations"] is None

    too_long = {**record, "Review": "x" * 61}
    assert client.post("/predict/toxic_leadership", json={"records": [too_long]}).status_code == 422
    bulk = {"records": [record] * 51, "explain_text": True}
    assert client.post("/predict/toxic_leadership", json=bulk).status_code == 422


def test_model_card_serves_text_term_importance(artifacts: ModelArtifacts) -> None:
    metadata = {
        **artifacts.metadata,
        "model_name": "logistic_regression",
        "test_score": 0.8,
        "text_term_importance": {
            "Review": {"positive": [{"term": "politics", "weight": 0.21, "docs": 40}], "negative": []}
        },
    }
    card = _client(ModelArtifacts(pipeline=artifacts.pipeline, explainer=artifacts.explainer, metadata=metadata))
    body = card.get("/model/toxic_leadership/card").json()
    assert body["text_columns"] == ["Review"]
    assert body["text_term_importance"]["Review"]["positive"][0] == {"term": "politics", "weight": 0.21, "docs": 40}


def test_explain_false_returns_probabilities_without_shap(artifacts: ModelArtifacts) -> None:
    records = pd.DataFrame([{"pay": 3, "family": "Data", "Review": "Toxic politics."}] * 3)
    explained = predict(artifacts, records)
    fast = predict(artifacts, records, explain=False)
    assert [r.prediction for r in fast] == [r.prediction for r in explained]
    assert all(r.contributions == {} for r in fast)

    body = (
        _client(artifacts)
        .post("/predict/toxic_leadership", json={"records": records.to_dict(orient="records"), "explain": False})
        .json()
    )
    assert body["predictions"][0]["contributions"] == {}


# --- sentence-embedding text challenger ---


def _embed(texts: list[str]) -> list[list[float]]:
    """Fake gateway: 3-d vectors separating toxic from healthy phrases."""
    return [
        [
            sum(w in t.lower() for w in ("toxic", "lies", "micromanagement", "direction")),
            sum(w in t.lower() for w in ("supportive", "mentoring", "trust", "transparent")),
            len(t) / 100,
        ]
        for t in texts
    ]


@pytest.fixture(scope="module")
def challenger_artifacts(artifacts: ModelArtifacts) -> ModelArtifacts:
    """`artifacts` plus a challenger fitted on fake embeddings, shaped like training's."""
    from lightgbm import LGBMClassifier

    df = _dataset()
    emb = pd.DataFrame(_embed(list(df["Review"])), columns=["Review__emb_0", "Review__emb_1", "Review__emb_2"])
    x = pd.concat([df[["pay", "family"]], emb], axis=1)
    pipeline = Pipeline([
        (
            "preprocessor",
            ColumnTransformer([
                ("num", StandardScaler(), ["pay", *emb.columns]),
                ("cat", OneHotEncoder(handle_unknown="ignore"), ["family"]),
            ]),
        ),
        ("model", LGBMClassifier(n_estimators=20, min_child_samples=5, verbosity=-1)),
    ])
    pipeline.fit(x, df["bad"])
    transformed = pipeline.named_steps["preprocessor"].transform(x)
    explainer = shap.TreeExplainer(
        pipeline.named_steps["model"],
        data=transformed,
        feature_perturbation="interventional",
        model_output="probability",
    )
    metadata = {
        **artifacts.metadata,
        "model_name": "lightgbm_small_data",
        "test_score": 0.8,
        "challenger": {
            "name": "lightgbm_small_data",
            "label": "fake embeddings",
            "embedding_model": "local-embed",
            "hf_model_id": "org/model",
            "embedding_dim": 3,
            "text_column": "Review",
            "input_columns": list(x.columns),
            "transformed_feature_names": list(pipeline.named_steps["preprocessor"].get_feature_names_out()),
            "selection_score": 0.9,
            "test_score": 0.85,
            "metrics": {"cv_roc_auc_mean": 0.9},
            "gain_over_deployed": 0.004,
            "would_be_adopted": False,
        },
    }
    return ModelArtifacts(
        pipeline=artifacts.pipeline,
        explainer=artifacts.explainer,
        metadata=metadata,
        challenger_pipeline=pipeline,
        challenger_explainer=explainer,
    )


def test_challenger_embeds_the_text_and_sums_dimensions_into_the_column(challenger_artifacts: ModelArtifacts) -> None:
    from prediction.core.predict import predict_challenger

    records = pd.DataFrame([
        {"pay": 3, "family": "Data", "Review": "Toxic politics, no direction."},
        {"pay": 3, "family": "Data", "Review": "Supportive lead, great mentoring."},
    ])
    toxic, healthy = predict_challenger(challenger_artifacts, records, _embed)

    assert toxic.prediction > healthy.prediction
    assert set(toxic.contributions) == {"num__pay", "cat__family_Data", "cat__family_Software", "Review"}
    assert toxic.contributions["Review"] > 0 > healthy.contributions["Review"]


def test_challenger_endpoint_card_and_missing_challenger(
    challenger_artifacts: ModelArtifacts, artifacts: ModelArtifacts, monkeypatch: pytest.MonkeyPatch
) -> None:
    import prediction.api as api

    monkeypatch.setattr(api, "_gateway_embedder", lambda request, model: _embed)
    client = _client(challenger_artifacts)
    record = {"pay": 2, "family": "Software", "Review": "Toxic politics everywhere."}

    body = client.post("/predict/toxic_leadership", json={"records": [record], "model": "challenger"}).json()
    assert 0 <= body["predictions"][0]["prediction"] <= 1
    card = client.get("/model/toxic_leadership/card").json()
    assert card["challenger"]["label"] == "fake embeddings" and card["challenger"]["would_be_adopted"] is False

    no_challenger = _client(artifacts).post(
        "/predict/toxic_leadership", json={"records": [record], "model": "challenger"}
    )
    assert no_challenger.status_code == 404
    bulk = {"records": [record] * 51, "model": "challenger"}
    assert client.post("/predict/toxic_leadership", json=bulk).status_code == 422
