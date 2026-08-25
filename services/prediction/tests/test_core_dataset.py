"""Tests for load_normalized's per-tenant-with-fallback dataset loading and evaluate()'s
held-out actual-vs-predicted evaluation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from ai_circus_shared.tabular_ml import NORMALIZED_DATASET_KEY
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from prediction.core.dataset import evaluate, load_normalized
from prediction.core.model_cache import ModelArtifacts


class FakeObjectStore:
    """In-memory stand-in for ai_circus_shared.storage.ObjectStore."""

    def __init__(self) -> None:
        """Start with an empty object map."""
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_dataframe(self, org_id: str, df: pd.DataFrame) -> None:
        """Store a DataFrame as parquet bytes under a tenant-scoped key."""
        self.objects[org_id, NORMALIZED_DATASET_KEY] = df.to_parquet()

    def get(self, org_id: str, path: str) -> bytes:
        """Retrieve previously stored bytes."""
        return self.objects[org_id, path]

    def exists(self, org_id: str, path: str) -> bool:
        """Mirror ObjectStore.exists() against the in-memory object map."""
        return (org_id, path) in self.objects


def test_load_normalized_loads_the_tenants_own_dataset() -> None:
    """A tenant with its own normalized dataset in SeaweedFS gets exactly that."""
    store = FakeObjectStore()
    store.put_dataframe("org-1", pd.DataFrame({"a": [1, 2]}))
    store.put_dataframe("fallback-org", pd.DataFrame({"a": [99]}))

    df = load_normalized(store, "org-1", fallback_org_id="fallback-org")

    assert df["a"].tolist() == [1, 2]


def test_load_normalized_falls_back_to_shared_baseline_org_when_tenant_has_no_dataset() -> None:
    """A tenant with no dataset of its own (e.g. the admin/engineering-demo bypass
    orgs) gets the fallback org's dataset instead of a KeyError — this is what makes
    the Data tab actually work for any tenant besides the one training/etl ran for.
    """
    store = FakeObjectStore()
    store.put_dataframe("fallback-org", pd.DataFrame({"a": [99]}))

    df = load_normalized(store, "new-tenant-with-no-dataset", fallback_org_id="fallback-org")

    assert df["a"].tolist() == [99]


@pytest.fixture
def regression_artifacts() -> ModelArtifacts:
    """A tiny real regression pipeline trained on synthetic data, plus the metadata
    evaluate() needs. `explainer` is unused by evaluate() so it's left as None rather
    than pulling in shap here.
    """
    rng = np.random.default_rng(0)
    n = 200
    df = pd.DataFrame({
        "numeric_feature": rng.normal(size=n),
        "category_feature": rng.choice(["A", "B"], size=n),
    })
    target = df["numeric_feature"] * 2 + rng.normal(scale=0.01, size=n)

    preprocessor = ColumnTransformer([
        ("num", StandardScaler(), ["numeric_feature"]),
        ("cat", OneHotEncoder(handle_unknown="ignore"), ["category_feature"]),
    ])
    pipeline = Pipeline([("preprocessor", preprocessor), ("model", RandomForestRegressor(random_state=0))])
    pipeline.fit(df, target)

    metadata = {
        "feature_columns": ["numeric_feature", "category_feature"],
        "target": "target",
        "task_type": "regression",
    }
    return ModelArtifacts(pipeline=pipeline, explainer=None, metadata=metadata)


def test_evaluate_feature_values_align_with_actuals_and_predictions(regression_artifacts: ModelArtifacts) -> None:
    """`feature_values` must be the *same* held-out rows as `actuals`/`predictions`,
    index-for-index — a chart coloring an actual-vs-predicted plot by a real feature
    (e.g. torque) needs the value from that exact row, not just any real value for
    that feature. Reconstructing the held-out rows from feature_values and re-running
    the same pipeline should reproduce `predictions` exactly, which only holds if the
    alignment is correct.
    """
    rng = np.random.default_rng(1)
    n = 200
    df = pd.DataFrame({
        "numeric_feature": rng.normal(size=n),
        "category_feature": rng.choice(["A", "B"], size=n),
    })
    df["target"] = df["numeric_feature"] * 2 + rng.normal(scale=0.01, size=n)

    result = evaluate(regression_artifacts, df, limit=1000)

    assert set(result.feature_values) == set(regression_artifacts.metadata["feature_columns"])
    for column in result.feature_values.values():
        assert len(column) == len(result.actuals) == len(result.predictions)

    reconstructed = pd.DataFrame(result.feature_values)
    recomputed = regression_artifacts.pipeline.predict(reconstructed)
    assert np.allclose(recomputed, result.predictions, atol=1e-3)


def test_evaluate_feature_values_respects_limit(regression_artifacts: ModelArtifacts) -> None:
    """A small `limit` subsamples feature_values the same way it subsamples actuals/predictions."""
    rng = np.random.default_rng(1)
    n = 200
    df = pd.DataFrame({
        "numeric_feature": rng.normal(size=n),
        "category_feature": rng.choice(["A", "B"], size=n),
    })
    df["target"] = df["numeric_feature"] * 2 + rng.normal(scale=0.01, size=n)

    result = evaluate(regression_artifacts, df, limit=10)

    assert result.n == 10
    assert len(result.actuals) == 10
    for column in result.feature_values.values():
        assert len(column) == 10
