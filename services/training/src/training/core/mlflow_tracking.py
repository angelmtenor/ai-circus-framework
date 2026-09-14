"""
mlflow_tracking.py
------------------

Mirrors each training run into the platform's MLflow tracking server (the MLOps
monitor — k8s/base/mlflow.yaml, http://mlflow.localhost for the admin), so an operator
can see per-scenario/per-tenant run history, which candidates were evaluated, their
held-out scores, and which model was selected — without reading SeaweedFS by hand.

MLflow is an *audit mirror*, never the source of truth: the pipeline/explainer/
metadata.json that `prediction` serves stay exactly where ai_circus_shared.tabular_ml
puts them (SeaweedFS, tenant-prefixed). Only the small metadata.json is uploaded as a
run artifact — the joblib pipelines are referenced by SeaweedFS key + checksum instead,
keeping the tracker's footprint tiny on a laptop-class cluster. And because it's a
mirror, a tracker that is down or unset never fails training: MLFLOW_TRACKING_URI empty
means "skip", any error is logged and swallowed.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from training.core.logger import get_logger

logger = get_logger(__name__)


class ScoredCandidate(Protocol):
    """The two fields of training.core.training.TrainedCandidate this module reads."""

    @property
    def name(self) -> str: ...  # ruff: ignore[undocumented-public-method]

    @property
    def test_score(self) -> float: ...  # ruff: ignore[undocumented-public-method]


def experiment_name(scenario_slug: str) -> str:
    """One MLflow experiment per scenario; the tenant is a run tag, not a separate experiment."""
    return f"scenario/{scenario_slug}"


def log_training_run(
    tracking_uri: str | None,
    *,
    org_id: str,
    scenario_slug: str,
    candidates: Sequence[ScoredCandidate],
    selected: ScoredCandidate,
    metadata: dict[str, Any],
    dataset_rows: int,
    accuracy_gain_threshold: float,
) -> None:
    """Record one finished training run in MLflow; a no-op when `tracking_uri` is unset.

    Never raises: the tracker being unreachable is an observability gap, not a training
    failure — the artifacts in SeaweedFS are already written by the time this runs.
    """
    if not tracking_uri:
        logger.info("MLFLOW_TRACKING_URI unset — not mirroring the run for scenario={} org={}", scenario_slug, org_id)
        return

    try:
        import mlflow

        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment_name(scenario_slug))
        with mlflow.start_run(
            run_name=f"{org_id}/{selected.name}",
            tags={
                "org_id": org_id,
                "scenario_slug": scenario_slug,
                "task_type": str(metadata.get("task_type", "")),
                "selected_model": selected.name,
                "has_intervals": str(bool(metadata.get("has_intervals"))).lower(),
            },
        ):
            mlflow.log_params({
                "org_id": org_id,
                "scenario_slug": scenario_slug,
                "candidates": ",".join(c.name for c in candidates),
                "accuracy_gain_threshold_for_complexity": accuracy_gain_threshold,
                "dataset_rows": dataset_rows,
                "feature_count": len(metadata.get("feature_columns", [])),
                "target": str(metadata.get("target", "")),
                # Where the served artifacts actually live (tenant-scoped SeaweedFS
                # keys, see ai_circus_shared.tabular_ml) + the checksums prediction
                # verifies — enough to tie a run to exactly the bytes in production.
                **{f"checksum.{name}": value for name, value in (metadata.get("checksums") or {}).items()},
            })
            for candidate in candidates:
                mlflow.log_metric(f"test_score.{candidate.name}", candidate.test_score)
            mlflow.log_metric("test_score", selected.test_score)
            mlflow.log_dict(metadata, "metadata.json")
        logger.success("Run mirrored to MLflow ({}) for scenario={} org={}", tracking_uri, scenario_slug, org_id)
    except Exception:
        logger.warning(
            "Could not mirror the run to MLflow at {} for scenario={} org={} — training artifacts are unaffected",
            tracking_uri,
            scenario_slug,
            org_id,
            exc_info=True,
        )
