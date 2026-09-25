"""Unit tests for dl-training's data/device/tasks/trainer/metrics/mlflow modules."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
import torch

from dl_training.core import calibration, data, device, metrics, mlflow_tracking, tasks, trainer
from tests.fixtures import (
    MemoryStore,
    image_config,
    image_npz_bytes,
    seed_cache,
    text_config,
    text_raw_files,
    tiny_image_task,
    tiny_text_task,
)

# ── data ──────────────────────────────────────────────────────────────────────


def test_raw_files_pin_urls_and_digests() -> None:
    text = data.raw_files(text_config())
    assert [r.name for r in text] == ["train.jsonl", "test.jsonl"]
    assert text[0].url == "https://huggingface.co/datasets/org/data/resolve/abc/train.jsonl"
    assert text[0].object_key == "raw/train.jsonl"
    (image,) = data.raw_files(image_config())
    assert image.name == "tiny.npz" and image.algorithm == "md5"


def test_ensure_raw_downloads_verifies_uploads_then_reuses(tmp_path: Path) -> None:
    files = text_raw_files()
    calls: list[str] = []

    def fake_download(url: str, dest: Path) -> None:
        calls.append(url)
        dest.write_bytes(files[dest.name])

    store = MemoryStore()
    paths = data.ensure_raw(store, "demo", text_config(), tmp_path / "a", download=fake_download)  # type: ignore[arg-type]
    assert len(calls) == 2
    assert store.objects["tenant-demo/raw/test.jsonl"] == files["test.jsonl"]
    assert paths["train.jsonl"].read_bytes() == files["train.jsonl"]

    # A second host with an empty cache gets it from SeaweedFS, not the internet.
    data.ensure_raw(store, "demo", text_config(), tmp_path / "b", download=fake_download)  # type: ignore[arg-type]
    assert len(calls) == 2


def test_ensure_raw_replaces_a_corrupt_stored_copy(tmp_path: Path) -> None:
    files = text_raw_files()
    store = MemoryStore()
    store.put("demo", "raw/train.jsonl", b"corrupt")
    store.put("demo", "raw/test.jsonl", files["test.jsonl"])
    data.ensure_raw(
        store,  # type: ignore[arg-type]
        "demo",
        text_config(),
        tmp_path,
        download=lambda url, dest: dest.write_bytes(files[dest.name]),
    )
    assert store.objects["tenant-demo/raw/train.jsonl"] == files["train.jsonl"]


def test_load_text_dataset_carves_a_stratified_validation_split(tmp_path: Path) -> None:
    seed_cache(tmp_path, text_raw_files())
    dataset = data.load_dataset(text_config(), {n: tmp_path / n for n in text_raw_files()})
    assert (len(dataset.train), len(dataset.val), len(dataset.test)) == (22, 2, 6)
    assert sorted(dataset.val.labels.tolist()) == [0, 1]
    assert dataset.test.inputs[0] == "fever rash"


def test_load_text_dataset_rejects_an_unknown_label(tmp_path: Path) -> None:
    (tmp_path / "train.jsonl").write_text('{"input_text": "x", "output_text": "flu"}\n')
    (tmp_path / "test.jsonl").write_text("")
    with pytest.raises(ValueError, match="'flu'"):
        data.load_dataset(
            text_config(), {"train.jsonl": tmp_path / "train.jsonl", "test.jsonl": tmp_path / "test.jsonl"}
        )


def test_load_npz_dataset(tmp_path: Path) -> None:
    (tmp_path / "tiny.npz").write_bytes(image_npz_bytes())
    dataset = data.load_dataset(image_config(), {"tiny.npz": tmp_path / "tiny.npz"})
    assert dataset.train.inputs.shape == (24, 32, 32)
    assert dataset.test.labels.tolist() == [0, 1] * 4


def test_stratified_indices() -> None:
    labels = np.array([0] * 50 + [1] * 50)
    picked = data.stratified_indices(labels, 10, seed=1)
    assert len(picked) == 10 and labels[picked].sum() == 5
    assert (data.stratified_indices(labels, None, 1) == np.arange(100)).all()
    assert len(data.stratified_indices(np.array([0, 1, 1]), 2, 1)) == 2  # singleton class: plain sample


def test_file_digest(tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"abc")
    assert data.file_digest(tmp_path / "f", "md5") == hashlib.md5(b"abc").hexdigest()  # ruff: ignore[hashlib-insecure-hash-function]


# ── device ────────────────────────────────────────────────────────────────────


def test_resolve_device(monkeypatch: pytest.MonkeyPatch) -> None:
    assert device.resolve_device("cpu").kind == "cpu"
    with pytest.raises(ValueError, match="DL_DEVICE"):
        device.resolve_device("tpu")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert device.resolve_device("auto").kind == "cpu"
    with pytest.raises(RuntimeError, match="no CUDA device"):
        device.resolve_device("cuda")


def test_budget_follows_the_device() -> None:
    training = text_config().training.model_copy(
        update={"cpu": text_config().training.cpu.model_copy(update={"epochs": 1})}
    )
    cpu = device.DeviceInfo(kind="cpu", name="c", torch_version="t", cuda_version=None, memory_gb=None)
    gpu = device.DeviceInfo(kind="cuda", name="g", torch_version="t", cuda_version="13", memory_gb=8.0)
    assert device.budget_for(training, cpu).epochs == 1
    assert device.budget_for(training, gpu).epochs == 2
    assert gpu.as_dict()["name"] == "g"


# ── tasks ─────────────────────────────────────────────────────────────────────


def test_freeze_lower_layers_keeps_only_the_top_blocks_and_head_trainable() -> None:
    task = tiny_text_task(text_config())
    trainable, total = tasks.freeze_lower_layers(task.model, 1)
    assert 0 < trainable < total
    names = {n for n, p in task.model.named_parameters() if p.requires_grad}
    assert any(n.startswith("model.layers.1.") for n in names)
    assert not any(n.startswith("model.layers.0.") or "embeddings" in n for n in names)
    assert any("classifier" in n for n in names)
    fresh = tiny_text_task(text_config())
    assert tasks.freeze_lower_layers(fresh.model, None)[0] == sum(p.numel() for p in fresh.model.parameters())


def test_text_batches_are_dynamically_padded() -> None:
    task = tiny_text_task(text_config())
    split = data.Split(["fever", "i have fever and rash"], np.array([0, 1]))
    inputs, labels = next(task.batches(split, 8, shuffle=False, augment=True, seed=0))
    assert inputs["input_ids"].shape == (2, 7)  # [CLS] + 5 words + [SEP]
    assert labels.tolist() == [0, 1]


def test_image_preprocessing_and_augmentation() -> None:
    prep = tasks.ImagePreprocessing(size=16, mean=(0.5,) * 3, std=(0.5,) * 3, source_mode="L")
    pixels = prep.to_pixels(np.full((2, 32, 32), 255, dtype=np.uint8))
    assert pixels.shape == (2, 3, 16, 16) and np.allclose(pixels, 1.0)
    rgb = prep.to_pixels(np.zeros((1, 16, 16, 3), dtype=np.uint8))
    assert rgb.shape == (1, 3, 16, 16) and np.allclose(rgb, -1.0)
    augmented = tasks.augment_images(torch.zeros(3, 3, 16, 16), np.random.default_rng(0))
    assert augmented.shape == (3, 3, 16, 16)
    task = tiny_image_task(image_config())
    split = data.Split(np.zeros((5, 32, 32), dtype=np.uint8), np.array([0, 1, 0, 1, 0]))
    batch = list(task.batches(split, 2, shuffle=True, augment=True, seed=0))
    assert [len(labels) for _, labels in batch] == [2, 2, 1]


# ── calibration ──────────────────────────────────────────────────────────────


def test_calibrated_probs_rows_sum_to_one_even_for_huge_logits() -> None:
    probs = calibration.calibrated_probs(np.array([[1000.0, 0.0], [1.0, 1.0]]), 2.0)
    assert np.allclose(probs.sum(axis=1), 1.0)
    assert probs[1].tolist() == pytest.approx([0.5, 0.5])


def test_fit_temperature_recovers_the_true_temperature() -> None:
    rng = np.random.default_rng(0)
    true_logits = rng.normal(size=(4000, 3)) * 2.0
    labels = np.array([rng.choice(3, p=p) for p in calibration.calibrated_probs(true_logits, 1.0)])
    overconfident = true_logits * 4.0  # what an over-trained network reports
    temperature = calibration.fit_temperature(overconfident, labels)
    assert temperature == pytest.approx(4.0, rel=0.1)
    assert calibration.nll(overconfident, labels, temperature) < calibration.nll(overconfident, labels, 1.0)
    # Argmax (the prediction itself) never changes with temperature.
    assert (calibration.calibrated_probs(overconfident, temperature).argmax(1) == overconfident.argmax(1)).all()
    assert calibration.fit_temperature(np.zeros((0, 3)), np.zeros(0, dtype=int)) == pytest.approx(1.0)


# ── trainer ───────────────────────────────────────────────────────────────────


def test_class_weights_are_inverse_frequency_with_mean_one() -> None:
    weights = trainer.class_weights(np.array([0, 0, 0, 1]), 3)
    assert weights[1] > weights[0]
    assert float(weights.mean()) == pytest.approx(1.0)


def test_fine_tune_learns_the_tiny_image_task(tmp_path: Path) -> None:
    seed_cache(tmp_path, {"tiny.npz": image_npz_bytes()})
    dataset = data.load_dataset(image_config(), {"tiny.npz": tmp_path / "tiny.npz"})
    task = tiny_image_task(image_config())
    budget = image_config().training.cpu.model_copy(update={"epochs": 6})
    seen = []
    history = trainer.fine_tune(
        task,
        dataset.train,
        dataset.val,
        budget,
        device.resolve_device("cpu"),
        n_classes=2,
        weighted_loss=True,
        seed=0,
        on_epoch=seen.append,
    )
    assert len(history) == 6 == len(seen)
    assert max(r.val_accuracy for r in history) == pytest.approx(1.0)  # the corner signal is trivially learnable
    assert not task.model.training


# ── metrics ───────────────────────────────────────────────────────────────────


def test_binary_evaluation_includes_roc_and_calibration() -> None:
    probs = np.array([[0.9, 0.1], [0.2, 0.8], [0.6, 0.4], [0.3, 0.7]])
    result = metrics.evaluate_predictions(probs, np.array([0, 1, 1, 1]), ["0", "1"])
    assert result["metrics"]["accuracy"] == pytest.approx(0.75)
    assert result["metrics"]["auroc"] == pytest.approx(1.0)
    assert result["confusion_matrix"] == [[1, 0], [1, 2]]
    assert result["roc_curve"][0]["fpr"] == pytest.approx(0.0)
    assert [c["key"] for c in result["per_class"]] == ["0", "1"]
    curve = result["coverage_curve"]
    assert curve[0] == {"threshold": 0.0, "coverage": 1.0, "accuracy": 0.75}
    assert curve[-1]["coverage"] == pytest.approx(0.0)
    assert curve[-1]["accuracy"] == pytest.approx(1.0)


def test_multiclass_evaluation_has_macro_auroc_only_when_every_class_is_present() -> None:
    probs = np.eye(3)[[0, 1, 2, 0]] * 0.8 + 0.2 / 3
    assert "auroc" in metrics.evaluate_predictions(probs, np.array([0, 1, 2, 0]), ["a", "b", "c"])["metrics"]
    assert "auroc" not in metrics.evaluate_predictions(probs[:2], np.array([0, 1]), ["a", "b", "c"])["metrics"]


# ── mlflow mirror ─────────────────────────────────────────────────────────────


def test_mlflow_mirror_is_a_no_op_when_unset_and_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    mlflow_tracking.log_training_run(None, org_id="demo", scenario_slug="s", metadata={})
    import mlflow

    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda uri: (_ for _ in ()).throw(RuntimeError("down")))
    mlflow_tracking.log_training_run("http://mlflow:5000", org_id="demo", scenario_slug="s", metadata={})
    assert mlflow_tracking.experiment_name("s") == "scenario/s"


def test_mlflow_mirror_logs_curves_and_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    import contextlib

    import mlflow

    logged: dict[str, list] = {"metric": [], "params": [], "dict": []}
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda uri: None)
    monkeypatch.setattr(mlflow, "set_experiment", lambda name: None)
    monkeypatch.setattr(mlflow, "start_run", lambda **kw: contextlib.nullcontext())
    monkeypatch.setattr(mlflow, "log_params", lambda p: logged["params"].append(p))
    monkeypatch.setattr(mlflow, "log_metric", lambda k, v, step=None: logged["metric"].append((k, v, step)))
    monkeypatch.setattr(mlflow, "log_dict", lambda d, name: logged["dict"].append(name))
    metadata = {
        "device": {"kind": "cuda"},
        "budget": {"epochs": 1},
        "history": [{"epoch": 1, "train_loss": 1.0, "val_loss": 0.5, "val_accuracy": 0.9, "val_macro_f1": 0.8}],
        "evaluation": {"metrics": {"accuracy": 0.95}},
        "checksums": {"model": "abc"},
    }
    mlflow_tracking.log_training_run("http://mlflow:5000", org_id="demo", scenario_slug="s", metadata=metadata)
    assert ("test_accuracy", 0.95, None) in logged["metric"]
    assert ("val_loss", 0.5, 1) in logged["metric"]
    assert logged["params"][0]["checksum.model"] == "abc"
    assert logged["dict"] == ["metadata.json"]


# ── artifacts upload retry ───────────────────────────────────────────────────


def test_put_with_retry_survives_transient_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    from dl_training.core import artifacts

    monkeypatch.setattr(artifacts.time, "sleep", lambda s: None)
    store = MemoryStore()
    calls = {"n": 0}
    real_put = store.put

    def flaky(org: str, path: str, data: object) -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("tunnel down")
        return real_put(org, path, data)

    store.put = flaky  # type: ignore[method-assign]
    artifacts.put_with_retry(store, "demo", "model/x", b"1")  # type: ignore[arg-type]
    assert calls["n"] == 3 and store.objects["tenant-demo/model/x"] == b"1"

    store.put = lambda *a: (_ for _ in ()).throw(ConnectionError("gone"))  # type: ignore[method-assign]
    with pytest.raises(ConnectionError):
        artifacts.put_with_retry(store, "demo", "model/y", b"1", attempts=2)  # type: ignore[arg-type]


def test_temperature_is_bounded_when_the_calibration_set_is_perfectly_classified() -> None:
    """85/85 correct must not license ~100% certainty (NLL alone would drive T -> 0)."""
    rng = np.random.default_rng(1)
    labels = rng.integers(0, 22, size=85)
    logits = rng.normal(size=(85, 22))
    logits[np.arange(85), labels] += 4.0  # every prediction right, moderately confident
    temperature = calibration.fit_temperature(logits, labels)
    assert temperature > calibration.T_MIN
    assert calibration.mean_confidence(logits, temperature) <= (85 + 1) / (85 + 2) + 1e-6


def test_calibration_holdout_carves_a_disjoint_stratified_slice_of_the_test_pool(tmp_path: Path) -> None:
    from dl_training.core.pipeline import _calibration_and_test_splits

    (tmp_path / "tiny.npz").write_bytes(image_npz_bytes())
    dl = image_config()
    dataset = data.load_dataset(dl, {"tiny.npz": tmp_path / "tiny.npz"})
    calib, test = _calibration_and_test_splits(dl, dataset)
    assert calib is dataset.val and test is dataset.test  # default: validation split

    dl = dl.model_copy(update={"training": dl.training.model_copy(update={"calibration_holdout_fraction": 0.25})})
    calib, test = _calibration_and_test_splits(dl, dataset)
    assert (len(calib), len(test)) == (2, 6)
    assert sorted(calib.labels.tolist()) == [0, 1]
    assert len(calib) + len(test) == len(dataset.test)


# ── task: anomaly_detection ───────────────────────────────────────────────────


def test_load_parquet_image_dataset_decodes_resizes_and_keeps_masks(tmp_path: Path) -> None:
    from tests.fixtures import anomaly_config, anomaly_parquet_files

    seed_cache(tmp_path, anomaly_parquet_files())
    dl = anomaly_config()
    dataset = data.load_dataset(dl, data.ensure_raw(MemoryStore(), "demo", dl, tmp_path))  # type: ignore[arg-type]
    assert isinstance(dataset.test.inputs, np.ndarray)
    assert dataset.test.inputs.shape == (16, 32, 32, 3) and dataset.test.inputs.dtype == np.uint8
    assert (len(dataset.train), len(dataset.val)) == (12, 4)  # validation carved out of train
    assert dataset.test.masks is not None and dataset.test.masks.dtype == bool
    defective = dataset.test.labels == 1
    assert dataset.test.masks[defective].any(axis=(1, 2)).all()
    assert not dataset.test.masks[~defective].any()
    # take() keeps images, labels and masks aligned.
    subset = dataset.test.take(np.array([15, 0]))
    assert subset.masks is not None and subset.masks[0].any() and not subset.masks[1].any()


def test_greedy_coreset_covers_every_cluster() -> None:
    from dl_training.core.anomaly import greedy_coreset

    rng = np.random.default_rng(0)
    centers = rng.normal(size=(4, 32)) * 10
    points = np.concatenate([c + rng.normal(size=(200, 32)) * 0.1 for c in centers])
    chosen = greedy_coreset(torch.from_numpy(points).half(), 4, torch.device("cpu"), seed=3).numpy()
    assert sorted(set(chosen // 200)) == [0, 1, 2, 3]  # one point per cluster, none duplicated
    assert len(greedy_coreset(torch.zeros(5, 8), 10, torch.device("cpu"), seed=0)) == 5


def test_pixel_auroc_scores_localization_against_the_masks() -> None:
    from dl_training.core.anomaly import pixel_auroc

    masks = np.zeros((2, 64, 64), dtype=bool)
    masks[0, :16, :16] = True
    good_maps = np.zeros((2, 4, 4), dtype=np.float32)
    good_maps[0, 0, 0] = 1.0
    assert pixel_auroc(good_maps, masks) > 0.95
    assert pixel_auroc(good_maps[:, ::-1, ::-1].copy(), masks) < 0.6  # heat in the wrong corner
    assert pixel_auroc(good_maps, None) is None
    assert pixel_auroc(good_maps, np.zeros_like(masks)) is None


def test_baked_position_grid_matches_the_resampled_one() -> None:
    from dl_training.core.anomaly import bake_position_embeddings
    from tests.fixtures import anomaly_config, tiny_anomaly_task

    task = tiny_anomaly_task(anomaly_config())  # already baked for 32 px
    pixels = torch.randn(1, 3, 32, 32)
    with torch.no_grad():
        baked = task.model.backbone(pixel_values=pixels).last_hidden_state
    assert bake_position_embeddings(torch.nn.Linear(2, 2), 32) == 0  # nothing to bake
    from transformers import Dinov2WithRegistersModel

    fresh = Dinov2WithRegistersModel(task.model.backbone.config).eval()
    fresh.load_state_dict(task.model.backbone.state_dict(), strict=False)
    with torch.no_grad():
        resampled = fresh(pixel_values=pixels).last_hidden_state
    assert torch.allclose(baked, resampled, atol=1e-5)
