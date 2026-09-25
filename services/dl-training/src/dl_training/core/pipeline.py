"""
- Title:    One deep_learning scenario, end to end
- Author:   ai-circus-framework contributors

download/verify data -> pick device + budget -> fine-tune (label-smoothed) -> export
ONNX (+quantize) -> calibrate a temperature on the exported model's validation logits
-> evaluate the calibrated, *exported* model on the held-out test split -> embed a
reference set for similar-case retrieval -> upload artifacts + manifest -> MLflow.

task: anomaly_detection swaps only the "fine-tune" step: a memory bank of normal patch
features is built instead (core/anomaly.py), P(anomalous) is fitted on the calibration
hold-out before export, and pixel-level localization is scored against the masks.
Everything after the export is the same code path as for a classifier.
"""

from __future__ import annotations

import gc
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import torch
from ai_circus_shared.scenario_schema import DeepLearningConfig, ScenarioDefinition
from ai_circus_shared.storage import ObjectStore

from dl_training.core import artifacts, calibration, data, export, metrics, mlflow_tracking
from dl_training.core.anomaly import AnomalyTask, pixel_auroc
from dl_training.core.device import DeviceInfo, budget_for
from dl_training.core.logger import get_logger
from dl_training.core.tasks import Task, TextTask, build_task, freeze_lower_layers
from dl_training.core.trainer import fine_tune

logger = get_logger(__name__)

TaskBuilder = Callable[[DeepLearningConfig, Path, "np.ndarray | list[str]"], Task]


def _latency_ms(task: Task, session: Any, split: data.Split) -> float:
    """Median single-sample latency of the deployed graph (CPU, as served)."""
    one = split.take(np.arange(1))
    timings = []
    for _ in range(5):
        started = time.perf_counter()
        task.onnx_predict(session, one)
        timings.append((time.perf_counter() - started) * 1000)
    return round(float(np.median(timings)), 1)


def _calibration_and_test_splits(dl: DeepLearningConfig, dataset: data.Dataset) -> tuple[data.Split, data.Split]:
    """(calibration set, evaluation test set) — see DlTraining.calibration_holdout_fraction."""
    fraction = dl.training.calibration_holdout_fraction
    if not fraction:
        return dataset.val, dataset.test
    labels = dataset.test.labels
    held = data.stratified_indices(labels, max(1, round(len(labels) * fraction)), dl.training.seed)
    rest = np.setdiff1d(np.arange(len(labels)), held)
    return dataset.test.take(held), dataset.test.take(rest)


def train_scenario(
    definition: ScenarioDefinition,
    store: ObjectStore,
    *,
    org_id: str,
    device: DeviceInfo,
    cache_dir: Path,
    tracking_uri: str | None = None,
    task_builder: TaskBuilder = build_task,
) -> dict[str, Any]:
    """Train, evaluate and publish one scenario's model. Returns the uploaded manifest."""
    dl = definition.deep_learning
    assert dl is not None  # guaranteed by resolve_scenarios(kind="deep_learning")
    started = time.monotonic()
    label_keys = [label.key for label in dl.labels]

    raw_paths = data.ensure_raw(store, org_id, dl, cache_dir)
    dataset = data.load_dataset(dl, raw_paths)
    budget = budget_for(dl.training, device)
    pool = dataset.train
    if dl.anomaly is not None:  # one-class: the memory bank must only ever see normal samples
        pool = pool.take(np.flatnonzero(pool.labels == label_keys.index(dl.anomaly.normal_label)))
    train = pool.take(data.stratified_indices(pool.labels, budget.max_train_samples, dl.training.seed))
    logger.info(
        "{}: train={} (of {}) val={} test={} on {} ({})",
        definition.slug,
        len(train),
        len(dataset.train),
        len(dataset.val),
        len(dataset.test),
        device.kind,
        device.name,
    )

    task = task_builder(dl, cache_dir / "hf", dataset.train.inputs)
    calibration_set, test = _calibration_and_test_splits(dl, dataset)
    train_started = time.monotonic()
    history: list[Any] = []
    anomaly_summary: dict[str, Any] | None = None
    if isinstance(task, AnomalyTask):
        trainable, total = 0, sum(p.numel() for p in task.model.parameters())
        fit = task.fit_memory_bank(train, dataset.val, calibration_set, budget, device, seed=dl.training.seed)
        anomaly_summary = task.summary(fit)
    else:
        trainable, total = freeze_lower_layers(task.model, budget.trainable_layers)
        logger.info("{}: training {:,} of {:,} parameters", definition.slug, trainable, total)
        history = fine_tune(
            task,
            train,
            dataset.val,
            budget,
            device,
            n_classes=len(label_keys),
            weighted_loss=dl.training.class_weighted_loss,
            seed=dl.training.seed,
            label_smoothing=dl.training.label_smoothing,
        )
    training_seconds = time.monotonic() - train_started
    # The model is back on CPU: release cached CUDA blocks and the optimizer's garbage
    # before the memory-hungry ONNX export/quantization.
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    with tempfile.TemporaryDirectory(prefix="dl-export-") as tmp:
        out_dir = Path(tmp)
        model_path = export.export_onnx(task, dataset.val, out_dir, quantize=dl.quantize)
        preprocessing = task.save_preprocessing(out_dir)
        session = export.open_session(model_path)

        # Calibrate on the deployed artifact's own logits — on the validation split, or on
        # a held-out slice of the test pool when that pool is distribution-shifted — then
        # evaluate the calibrated probabilities on test data never used for either.
        calib_logits, _ = task.onnx_predict(session, calibration_set)
        temperature = calibration.fit_temperature(calib_logits, calibration_set.labels)
        test_maps = None
        if isinstance(task, AnomalyTask):
            test_logits, _, test_maps = task.onnx_predict_with_maps(session, test)
        else:
            test_logits, _ = task.onnx_predict(session, test)
        test_probs = calibration.calibrated_probs(test_logits, temperature)
        uncalibrated_ece = metrics.calibration(calibration.calibrated_probs(test_logits, 1.0), test.labels)[0]
        logger.info("{}: temperature {} (test ECE uncalibrated {})", definition.slug, temperature, uncalibrated_ece)
        evaluation = metrics.evaluate_predictions(test_probs, test.labels, label_keys)
        evaluation["metrics"]["ece_uncalibrated"] = uncalibrated_ece
        if test_maps is not None and (localization := pixel_auroc(test_maps, test.masks)) is not None:
            evaluation["metrics"]["pixel_auroc"] = localization
        logger.info("{}: deployed-model test metrics {}", definition.slug, evaluation["metrics"])

        gallery_rows = data.stratified_indices(test.labels, dl.gallery_size, dl.training.seed)
        # Similar-case search: for an anomaly detector the reference set is the (normal)
        # training pool — "the closest known-good parts".
        reference = pool.take(data.stratified_indices(pool.labels, dl.reference_size, dl.training.seed))
        _, reference_embeddings = task.onnx_predict(session, reference)
        published = artifacts.Published(
            gallery=test.take(gallery_rows),
            gallery_probs=test_probs[gallery_rows],
            reference=reference,
            reference_embeddings=reference_embeddings,
        )
        metadata: dict[str, Any] = {
            "scenario_slug": definition.slug,
            "org_id": org_id,
            "modality": dl.modality,
            "task": dl.task,
            "task_type": (
                "image_anomaly_detection"
                if dl.task == "anomaly_detection"
                else f"{'text' if dl.modality == 'text' else 'image'}_classification"
            ),
            "base_model": dl.base_model,
            "base_model_revision": dl.base_model_revision,
            "base_model_params": dl.base_model_params,
            "labels": [{"key": label.key, "label": label.label} for label in dl.labels],
            "device": device.as_dict(),
            "budget_kind": "gpu" if device.kind == "cuda" else "cpu",
            "budget": budget.model_dump(),
            "trainable_params": trainable,
            "total_params": total,
            "train_size": len(train),
            "train_size_available": len(pool),
            "val_size": len(dataset.val),
            "test_size": len(test),
            "calibration_set": (
                f"held-out {len(calibration_set)} of the test pool"
                if dl.training.calibration_holdout_fraction
                else f"validation split ({len(calibration_set)})"
            ),
            "history": [record.__dict__ for record in history],
            "anomaly": anomaly_summary,
            "evaluation": evaluation,
            "quantized": dl.quantize,
            # dl-inference divides every logit by this before softmax (see core/calibration.py).
            "temperature": temperature,
            "label_smoothing": dl.training.label_smoothing,
            "latency_ms": _latency_ms(task, session, test),
            "preprocessing": preprocessing,
            "occlusion_grid": dl.occlusion_grid,
            "training_seconds": round(training_seconds, 1),
            "total_seconds": round(time.monotonic() - started, 1),
            "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "data_source": dl.source.model_dump(),
        }
        final = artifacts.upload(
            store,
            org_id,
            model_path=model_path,
            tokenizer_path=out_dir / "tokenizer.json" if isinstance(task, TextTask) else None,
            published=published,
            label_keys=label_keys,
            metadata=metadata,
        )

    mlflow_tracking.log_training_run(tracking_uri, org_id=org_id, scenario_slug=definition.slug, metadata=final)
    return final
