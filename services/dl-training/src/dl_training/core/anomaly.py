"""
- Title:    One-class visual anomaly detection (task: anomaly_detection)
- Author:   ai-circus-framework contributors

Industrial inspection rarely has a labelled defect for every failure mode — defects are
rare and never quite the same twice — so the model learns what *normal* looks like and
flags anything else. No gradient step is taken:

1. A frozen self-supervised ViT (DINOv2 with registers) embeds every 14x14-pixel patch
   of the normal training images (L2-normalized last-layer patch tokens).
2. A greedy k-center coreset (PatchCore, Roth et al., CVPR 2022) keeps
   `memory_bank_size` of those patch features — the fewest points that still cover the
   whole normal distribution, so a 16k-patch bank stands in for ~800k patches.
3. A patch's anomaly score is its cosine distance to the nearest bank patch
   (AnomalyDINO, Damm et al., WACV 2025); the image score is the mean of its top
   `top_k_fraction` patch scores.
4. The image score is mapped to P(anomalous) by a logistic (Platt) fit on the labelled
   calibration hold-out — the training data being normal-only, that's the only place
   both classes exist.

`PatchKnnDetector` does all of it in one forward pass — backbone, bank, kNN, top-k,
logistic — so it exports to a single ONNX graph with the contract's `logits`/`embedding`
outputs plus `anomaly_map`: dl-inference serves it exactly like a classifier, and the
anomaly map (not occlusion) is its explanation. It implements the same `Task` interface
as tasks.py, so the pipeline's export/calibration/evaluation/publishing are shared.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
import torch
from ai_circus_shared.deep_learning import (
    ONNX_ANOMALY_MAP_OUTPUT,
    ONNX_EMBEDDING_OUTPUT,
    ONNX_IMAGE_INPUT,
    ONNX_LOGITS_OUTPUT,
)
from ai_circus_shared.scenario_schema import DeepLearningConfig, DlTrainBudget
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from torch import nn

from dl_training.core.data import Split
from dl_training.core.device import DeviceInfo
from dl_training.core.logger import get_logger
from dl_training.core.tasks import Batch, ImagePreprocessing, ImageTask, read_normalization

logger = get_logger(__name__)

CORESET_PROJECTION_DIM = 128
# Rows of the (patches x bank) similarity matrix computed at once — bounds peak memory.
KNN_CHUNK_IMAGES = 4
PIXEL_EVAL_SIZE = 112
# Normal-patch distance quantile below which the explanation map shows nothing.
MAP_FLOOR_QUANTILE = 0.99


class PatchKnnDetector(nn.Module):
    """pixel_values -> (logits, embedding, anomaly_map); see the module docstring."""

    def __init__(
        self, backbone: nn.Module, *, prefix_tokens: int, grid: int, top_k: int, anomaly_index: int, dim: int
    ) -> None:
        """Wrap a Hugging Face ViT backbone (AutoModel); the bank is filled by `fit_memory_bank`."""
        super().__init__()
        self.backbone = backbone
        self.prefix_tokens = prefix_tokens  # [CLS] + register tokens before the patch tokens
        self.grid = grid
        self.top_k = top_k
        self.anomaly_index = anomaly_index
        self.memory_bank: torch.Tensor
        self.score_scale: torch.Tensor
        self.score_bias: torch.Tensor
        self.register_buffer("memory_bank", torch.zeros(1, dim))
        self.register_buffer("score_scale", torch.ones(()))
        self.register_buffer("score_bias", torch.zeros(()))

    def features(self, pixel_values: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """(L2-normalized patch tokens (B, grid*grid, C), L2-normalized [CLS] token (B, C))."""
        hidden = self.backbone(pixel_values=pixel_values).last_hidden_state
        patches = nn.functional.normalize(hidden[:, self.prefix_tokens :], dim=-1)
        return patches, nn.functional.normalize(hidden[:, 0], dim=-1)

    def patch_distances(self, patches: torch.Tensor) -> torch.Tensor:
        """Cosine distance of every patch to its nearest memory-bank patch: (B, N)."""
        return 1.0 - (patches @ self.memory_bank.T).amax(dim=-1)

    def image_scores(self, distances: torch.Tensor) -> torch.Tensor:
        """Mean of the top-k patch distances: (B,)."""
        return distances.topk(self.top_k, dim=-1).values.mean(dim=-1)

    def forward(self, pixel_values: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Two-class logits whose softmax is sigmoid(scale * score + bias) for the
        anomalous class, the [CLS] embedding (similar-case search) and the patch map.
        """
        patches, embedding = self.features(pixel_values)
        distances = self.patch_distances(patches)
        half = (self.image_scores(distances) * self.score_scale + self.score_bias) / 2
        columns = [-half, -half]
        columns[self.anomaly_index] = half
        return torch.stack(columns, dim=-1), embedding, distances.reshape(-1, self.grid, self.grid)


def greedy_coreset(features: torch.Tensor, size: int, device: torch.device, seed: int) -> torch.Tensor:
    """Indices of a greedy k-center coreset of `features` (N, C): each step adds the
    point farthest from everything chosen so far. Distances use a random projection to
    128 dims (Johnson-Lindenstrauss, as PatchCore does) — the loop is memory-bound, and
    it never syncs with the host, so on a GPU 16k steps over ~800k points take seconds.
    """
    n = len(features)
    if size >= n:
        return torch.arange(n)
    generator = torch.Generator().manual_seed(seed)
    projection = torch.randn(features.shape[1], CORESET_PROJECTION_DIM, generator=generator)
    projection = (projection / math.sqrt(CORESET_PROJECTION_DIM)).to(device)
    z = torch.cat([chunk.to(device, torch.float32) @ projection for chunk in features.split(65536)])
    nearest = torch.full((n,), float("inf"), device=device)
    chosen = torch.empty(size, dtype=torch.long, device=device)
    last = torch.randint(n, (1,), generator=generator).to(device)[0]
    for i in range(size):
        chosen[i] = last
        nearest = torch.minimum(nearest, ((z - z[last]) ** 2).sum(dim=1))
        last = nearest.argmax()
    return chosen.cpu()


@dataclass(frozen=True)
class AnomalyFit:
    """What `fit_memory_bank` did — recorded in metadata.json's `anomaly` block."""

    normal_images: int
    patches_seen: int
    memory_bank_size: int
    map_floor: float
    map_ceiling: float
    score_scale: float
    score_bias: float
    fit_seconds: float


@dataclass
class AnomalyTask:
    """The anomaly_detection `Task`: image batches/preprocessing as ImageTask, a
    `PatchKnnDetector` as the model, and three ONNX outputs instead of two.
    """

    model: PatchKnnDetector
    preprocessing: ImagePreprocessing
    patch_size: int
    method: str

    def _images(self) -> ImageTask:
        return ImageTask(model=self.model, preprocessing=self.preprocessing)

    def batches(self, split: Split, batch_size: int, *, shuffle: bool, augment: bool, seed: int) -> Iterator[Batch]:
        """Normalized pixel batches (never augmented: the bank must hold real normal patches)."""
        del augment
        return self._images().batches(split, batch_size, shuffle=shuffle, augment=False, seed=seed)

    # ── fitting (no gradients) ────────────────────────────────────────────────

    def _patch_batches(self, split: Split, batch_size: int, device: DeviceInfo) -> Iterator[torch.Tensor]:
        autocast = torch.autocast(device_type=device.kind, dtype=torch.bfloat16, enabled=device.kind == "cuda")
        with torch.no_grad():
            for inputs, _ in self.batches(split, batch_size, shuffle=False, augment=False, seed=0):
                with autocast:
                    patches, _ = self.model.features(inputs["pixel_values"].to(device.torch_device))
                yield nn.functional.normalize(patches.float(), dim=-1)

    def _distances(self, split: Split, device: DeviceInfo) -> torch.Tensor:
        """Patch distances (N, grid*grid) of `split` to the current bank, on CPU."""
        out = [self.model.patch_distances(p).cpu() for p in self._patch_batches(split, KNN_CHUNK_IMAGES, device)]
        return torch.cat(out) if out else torch.empty(0, self.model.grid**2)

    def fit_memory_bank(
        self,
        train: Split,
        val: Split,
        calibration: Split,
        budget: DlTrainBudget,
        device: DeviceInfo,
        *,
        seed: int,
    ) -> AnomalyFit:
        """Fill the bank from `train` (normal images), set the explanation map's display
        range from held-out normals (`val`) and fit P(anomalous) on `calibration`.
        """
        assert budget.memory_bank_size is not None  # enforced by DeepLearningConfig
        started = time.monotonic()
        self.model.to(device.torch_device).eval()
        batches = self._patch_batches(train, budget.batch_size, device)
        features = torch.cat([p.reshape(-1, p.shape[-1]).half().cpu() for p in batches])
        logger.info("Embedded {:,} patches of {} normal images", len(features), len(train))
        chosen = greedy_coreset(features, budget.memory_bank_size, device.torch_device, seed)
        self.model.memory_bank = features[chosen].float().to(device.torch_device)
        logger.info("Memory bank: {:,} coreset patches ({:.1f}s)", len(chosen), time.monotonic() - started)
        del features

        # Explanation range: patches of unseen *normal* images score at most `floor` 99%
        # of the time — shown as no heat; `ceiling` is a typical defect's peak.
        normal = self._distances(val, device) if len(val) else torch.zeros(1, 1)
        floor = float(torch.quantile(normal.flatten().float()[:1_000_000], MAP_FLOOR_QUANTILE))
        calib = self._distances(calibration, device)
        scores = self.model.image_scores(calib).numpy().astype(np.float64)
        is_anomalous = (calibration.labels == self.model.anomaly_index).astype(int)
        if len(set(is_anomalous.tolist())) < 2:
            raise ValueError("The calibration hold-out needs both normal and anomalous images.")
        peaks = calib.amax(dim=1).numpy()[is_anomalous == 1]
        ceiling = max(float(np.median(peaks)), floor + 1e-3)

        # Platt scaling on the standardized score (L2-regularized, so a perfectly
        # separable hold-out can't drive the slope — and every probability — to infinity).
        mean, std = float(scores.mean()), float(scores.std()) or 1.0
        logistic = LogisticRegression(C=1.0).fit(((scores - mean) / std)[:, None], is_anomalous)
        slope = float(logistic.coef_[0, 0]) / std
        bias = float(logistic.intercept_[0]) - slope * mean
        self.model.score_scale = torch.tensor(slope, device=device.torch_device)
        self.model.score_bias = torch.tensor(bias, device=device.torch_device)
        self.model.to("cpu").eval()
        logger.info(
            "Calibration: AUROC {:.3f} on {} held-out images; P(anomalous)=0.5 at score {:.4f}",
            roc_auc_score(is_anomalous, scores),
            len(scores),
            -bias / slope if slope else float("nan"),
        )
        return AnomalyFit(
            normal_images=len(train),
            patches_seen=len(train) * self.model.grid**2,
            memory_bank_size=len(chosen),
            map_floor=round(floor, 5),
            map_ceiling=round(ceiling, 5),
            score_scale=round(slope, 5),
            score_bias=round(bias, 5),
            fit_seconds=round(time.monotonic() - started, 1),
        )

    def summary(self, fit: AnomalyFit) -> dict[str, Any]:
        """metadata.json's `anomaly` block (dl-inference reads map_floor/map_ceiling)."""
        return {
            "method": self.method,
            "patch_grid": self.model.grid,
            "patch_size_px": self.patch_size,
            "top_k": self.model.top_k,
            "feature_dim": int(self.model.memory_bank.shape[1]),
            **fit.__dict__,
            "decision_score": round(-fit.score_bias / fit.score_scale, 5) if fit.score_scale else None,
        }

    # ── export / ONNX (the Task interface) ────────────────────────────────────

    def export_spec(
        self, example: Split
    ) -> tuple[nn.Module, tuple[torch.Tensor, ...], list[str], list[str], dict[str, Any]]:
        """The detector itself is the export wrapper; fixed input size, dynamic batch."""
        assert isinstance(example.inputs, np.ndarray)
        pixels = torch.from_numpy(self.preprocessing.to_pixels(example.inputs[:2]))
        outputs = [ONNX_LOGITS_OUTPUT, ONNX_EMBEDDING_OUTPUT, ONNX_ANOMALY_MAP_OUTPUT]
        dynamic = {name: {0: "batch"} for name in [ONNX_IMAGE_INPUT, *outputs]}
        return self.model, (pixels,), [ONNX_IMAGE_INPUT], outputs, dynamic

    def onnx_predict_with_maps(
        self, session: ort.InferenceSession, split: Split
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(raw logits, embeddings, anomaly maps) of `split` through the exported graph."""
        assert isinstance(split.inputs, np.ndarray)
        logits, embeddings, maps = [], [], []
        names = [ONNX_LOGITS_OUTPUT, ONNX_EMBEDDING_OUTPUT, ONNX_ANOMALY_MAP_OUTPUT]
        for start in range(0, len(split), KNN_CHUNK_IMAGES):
            pixels = self.preprocessing.to_pixels(split.inputs[start : start + KNN_CHUNK_IMAGES])
            out = session.run(names, {ONNX_IMAGE_INPUT: pixels})
            logits.append(np.asarray(out[0]))
            embeddings.append(np.asarray(out[1]))
            maps.append(np.asarray(out[2]))
        return np.concatenate(logits), np.concatenate(embeddings), np.concatenate(maps)

    def onnx_predict(self, session: ort.InferenceSession, split: Split) -> tuple[np.ndarray, np.ndarray]:
        """(raw logits, embeddings) — the Task interface."""
        logits, embeddings, _ = self.onnx_predict_with_maps(session, split)
        return logits, embeddings

    def save_preprocessing(self, out_dir: Path) -> dict[str, Any]:
        """Same image preprocessing block as ImageTask."""
        return self._images().save_preprocessing(out_dir)


def bake_position_embeddings(backbone: nn.Module, image_size: int) -> int:
    """Freeze a ViT's position grid at the (fixed) input size. HF ViTs resample their
    pre-training grid (37x37 for DINOv2's 518 px) to the input's grid on every forward,
    with antialiased bicubic interpolation — which has no ONNX op. Resampling once here
    gives the same numbers and an exportable graph.

    Returns:
        How many embedding modules were baked (0 = the backbone has none to bake).
    """
    baked = 0
    for module in backbone.modules():
        table = getattr(module, "position_embeddings", None)
        if not (hasattr(module, "interpolate_pos_encoding") and isinstance(table, nn.Parameter)):
            continue
        patch_size = int(module.config.patch_size)  # type: ignore[union-attr]
        tokens = (image_size // patch_size) ** 2 + 1
        with torch.no_grad():
            dummy = torch.zeros(1, tokens, table.shape[-1], dtype=table.dtype)
            fixed = module.interpolate_pos_encoding(dummy, image_size, image_size).detach().clone()  # type: ignore[operator]
        module.register_buffer("baked_position_embeddings", fixed)
        module.interpolate_pos_encoding = lambda *_, _m=module: _m.baked_position_embeddings  # type: ignore[method-assign]
        baked += 1
    return baked


def pixel_auroc(maps: np.ndarray, masks: np.ndarray | None) -> float | None:
    """Localization quality: AUROC of every pixel's (bilinearly upsampled) patch score
    against the ground-truth defect masks, at PIXEL_EVAL_SIZE² per image. None without
    masks or without any defect pixel.
    """
    if masks is None or not masks.any():
        return None
    size = PIXEL_EVAL_SIZE
    scores = nn.functional.interpolate(torch.from_numpy(maps)[:, None].float(), size=(size, size), mode="bilinear")
    # A cell is "defect" when the downscaled mask has any defect pixel in it.
    truth = nn.functional.adaptive_max_pool2d(torch.from_numpy(masks)[:, None].float(), size) > 0
    return round(float(roc_auc_score(truth.flatten().numpy(), scores.flatten().numpy())), 4)


def build_anomaly_task(dl: DeepLearningConfig, cache_dir: Path) -> AnomalyTask:
    """Download (once, cached) the pinned self-supervised backbone and wrap it."""
    from transformers import AutoConfig, AutoModel

    assert dl.anomaly is not None  # enforced by DeepLearningConfig
    config = AutoConfig.from_pretrained(dl.base_model, revision=dl.base_model_revision, cache_dir=cache_dir)
    patch_size = int(config.patch_size)
    if dl.image_size % patch_size:
        raise ValueError(f"image_size {dl.image_size} must be a multiple of the backbone's patch size {patch_size}.")
    backbone = AutoModel.from_pretrained(
        dl.base_model, revision=dl.base_model_revision, cache_dir=cache_dir, attn_implementation="sdpa"
    )
    backbone.requires_grad_(False)
    keys = [label.key for label in dl.labels]
    return anomaly_task_for(
        dl,
        backbone,
        patch_size=patch_size,
        prefix_tokens=1 + int(getattr(config, "num_register_tokens", 0) or 0),
        dim=int(config.hidden_size),
        anomaly_index=1 - keys.index(dl.anomaly.normal_label),
        normalization=read_normalization(dl, cache_dir),
    )


def anomaly_task_for(
    dl: DeepLearningConfig,
    backbone: nn.Module,
    *,
    patch_size: int,
    prefix_tokens: int,
    dim: int,
    anomaly_index: int,
    normalization: tuple[tuple[float, float, float], tuple[float, float, float]],
) -> AnomalyTask:
    """An AnomalyTask around any ViT-style backbone (also used by the tests' tiny ViT)."""
    assert dl.anomaly is not None
    grid = dl.image_size // patch_size
    top_k = max(1, math.ceil(dl.anomaly.top_k_fraction * grid * grid))
    bake_position_embeddings(backbone, dl.image_size)
    detector = PatchKnnDetector(
        backbone, prefix_tokens=prefix_tokens, grid=grid, top_k=top_k, anomaly_index=anomaly_index, dim=dim
    )
    mean, std = normalization
    return AnomalyTask(
        model=detector,
        preprocessing=ImagePreprocessing(size=dl.image_size, mean=mean, std=std, source_mode="RGB"),
        patch_size=patch_size,
        method=(
            f"kNN over a {grid}x{grid} grid of frozen {dl.base_model.split('/')[-1]} patch features, "
            f"greedy-coreset memory bank of normal patches (PatchCore / AnomalyDINO)"
        ),
    )
