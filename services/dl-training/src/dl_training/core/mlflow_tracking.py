"""
mlflow_tracking.py
------------------

Mirrors each deep-learning run into the platform's MLflow tracking server — same
contract as the tabular `training` service's mirror: an audit copy, never the source of
truth (the ONNX model and manifest dl-inference serves stay in SeaweedFS), a no-op when
MLFLOW_TRACKING_URI is unset, and never able to fail the run it describes.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

from typing import Any

from dl_training.core.logger import get_logger

logger = get_logger(__name__)


def experiment_name(scenario_slug: str) -> str:
    """One MLflow experiment per scenario (shared naming with the tabular runs)."""
    return f"scenario/{scenario_slug}"


def log_training_run(tracking_uri: str | None, *, org_id: str, scenario_slug: str, metadata: dict[str, Any]) -> None:
    """Record one finished run (params, per-epoch curves, test metrics, manifest)."""
    if not tracking_uri:
        logger.info("MLFLOW_TRACKING_URI unset — not mirroring the run for scenario={} org={}", scenario_slug, org_id)
        return
    try:
        import mlflow

        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment_name(scenario_slug))
        device = metadata.get("device", {})
        with mlflow.start_run(
            run_name=f"{org_id}/{metadata.get('base_model', 'model')}",
            tags={
                "org_id": org_id,
                "scenario_slug": scenario_slug,
                "task_type": str(metadata.get("task_type", "")),
                "device": str(device.get("kind", "")),
                "device_name": str(device.get("name", "")),
            },
        ):
            mlflow.log_params({
                "base_model": metadata.get("base_model"),
                "base_model_revision": metadata.get("base_model_revision"),
                "budget_kind": metadata.get("budget_kind"),
                **{f"budget.{k}": v for k, v in (metadata.get("budget") or {}).items()},
                "train_size": metadata.get("train_size"),
                "quantized": metadata.get("quantized"),
                **{f"checksum.{k}": v for k, v in (metadata.get("checksums") or {}).items()},
            })
            for record in metadata.get("history", []):
                for key in ("train_loss", "val_loss", "val_accuracy", "val_macro_f1"):
                    mlflow.log_metric(key, record[key], step=record["epoch"])
            for key, value in (metadata.get("evaluation", {}).get("metrics") or {}).items():
                mlflow.log_metric(f"test_{key}", value)
            mlflow.log_dict(metadata, "metadata.json")
        logger.success("Run mirrored to MLflow ({}) for scenario={} org={}", tracking_uri, scenario_slug, org_id)
    except Exception:
        logger.warning(
            "Could not mirror the run to MLflow at {} for scenario={} org={} — model artifacts are unaffected",
            tracking_uri,
            scenario_slug,
            org_id,
            exc_info=True,
        )
