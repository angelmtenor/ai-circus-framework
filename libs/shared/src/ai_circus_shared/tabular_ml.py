"""Shared SeaweedFS object-key conventions for tabular_ml scenarios.

A single source of truth for the keys etl-tabular writes to and training/prediction
read from, so the three services can't drift out of sync on where artifacts live.
"""

from __future__ import annotations

import hashlib

NORMALIZED_DATASET_KEY = "processed/normalized.parquet"
# Single shared ceiling on how many rows of any one tenant's dataset ever get
# processed — etl-tabular downsamples to this at the source (see
# etl_tabular.core.etl.clean()), so training/evaluation/SHAP can never see more than
# this regardless of how big the raw uploaded/seed CSV is.
MAX_DATASET_ROWS = 30000
MODEL_PIPELINE_KEY = "model/pipeline.joblib"
MODEL_EXPLAINER_KEY = "model/explainer.joblib"
MODEL_METADATA_KEY = "model/metadata.json"
# Regression-only: LightGBM quantile-objective pipelines giving a 90% prediction
# interval around MODEL_PIPELINE_KEY's point estimate — absent for classification
# scenarios (see training/core/training.py's fit_quantile_pipelines()).
MODEL_PIPELINE_LOWER_KEY = "model/pipeline_lower.joblib"
MODEL_PIPELINE_UPPER_KEY = "model/pipeline_upper.joblib"

# Key under which MODEL_METADATA_KEY's JSON stores each artifact's checksum (see
# artifact_checksum below) — training writes it, prediction's model_cache verifies it
# before joblib.load()'ing anything, so a partially-overwritten or corrupted artifact
# is rejected loudly instead of silently deserialized.
MODEL_CHECKSUMS_METADATA_FIELD = "checksums"


def artifact_checksum(data: bytes) -> str:
    """Return a SHA-256 hex digest identifying one serialized model artifact's bytes."""
    return hashlib.sha256(data).hexdigest()
