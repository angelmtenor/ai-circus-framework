"""Shared MinIO object-key conventions for tabular_ml scenarios.

A single source of truth for the keys etl-tabular writes to and training/prediction
read from, so the three services can't drift out of sync on where artifacts live.
"""

from __future__ import annotations

NORMALIZED_DATASET_KEY = "processed/normalized.parquet"
MODEL_PIPELINE_KEY = "model/pipeline.joblib"
MODEL_EXPLAINER_KEY = "model/explainer.joblib"
MODEL_METADATA_KEY = "model/metadata.json"
