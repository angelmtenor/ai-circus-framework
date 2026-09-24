"""
- Title:    Modality-specific pieces of a deep_learning fine-tune (text / image)
- Author:   ai-circus-framework contributors

The training loop (core/trainer.py), export (core/export.py) and artifact code are
modality-agnostic; everything that differs between "BioClinical ModernBERT on symptom
text" and "ConvNeXt V2 on chest X-rays" lives here behind one small interface:
build the Hugging Face model, turn a Split into batches, wrap the model for ONNX export
(logits + L2-normalized embedding), and run the exported ONNX graph with *exactly* the
preprocessing dl-inference uses — so every metric in metadata.json is a metric of the
deployed artifact, not of the PyTorch model it came from.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import onnxruntime as ort
import torch
from ai_circus_shared.deep_learning import ONNX_EMBEDDING_OUTPUT, ONNX_IMAGE_INPUT, ONNX_LOGITS_OUTPUT, ONNX_TEXT_INPUTS
from ai_circus_shared.scenario_schema import DeepLearningConfig
from PIL import Image
from tokenizers import Tokenizer
from torch import nn

from dl_training.core.data import Split

Batch = tuple[dict[str, torch.Tensor], torch.Tensor]
PREDICT_BATCH_SIZE = 32


def _encoder_blocks(model: nn.Module) -> tuple[str, nn.ModuleList]:
    """The backbone's repeated block list (transformer layers / ConvNeXt stages): the
    ModuleList holding the most parameters. Generic over HF architectures, so a
    scenario can swap its base model without code changes.
    """
    best: tuple[str, nn.ModuleList] | None = None
    best_params = -1
    for name, module in model.named_modules():
        if isinstance(module, nn.ModuleList) and len(module) > 1:
            params = sum(p.numel() for p in module.parameters())
            if params > best_params:
                best, best_params = (name, module), params
    if best is None:
        raise ValueError(f"No repeated encoder block list found in {type(model).__name__}.")
    return best


def freeze_lower_layers(model: nn.Module, trainable_layers: int | None) -> tuple[int, int]:
    """Freeze the embeddings and all but the top `trainable_layers` encoder blocks
    (None = train everything). Everything after the blocks — final norm, pooler,
    classification head — always stays trainable.

    Returns:
        (trainable parameter count, total parameter count)
    """
    total = sum(p.numel() for p in model.parameters())
    if trainable_layers is None:
        return total, total
    prefix, blocks = _encoder_blocks(model)
    frozen_blocks = {f"{prefix}.{i}." for i in range(max(len(blocks) - trainable_layers, 0))}
    seen_blocks = False
    for name, param in model.named_parameters():
        in_blocks = name.startswith(f"{prefix}.")
        seen_blocks = seen_blocks or in_blocks
        # Parameters registered before the first block are the embeddings/stem.
        if not seen_blocks or any(name.startswith(p) for p in frozen_blocks):
            param.requires_grad_(False)
    return sum(p.numel() for p in model.parameters() if p.requires_grad), total


def _mean_pool(hidden: torch.Tensor, attention_mask: torch.Tensor | None = None) -> torch.Tensor:
    if hidden.dim() == 4:  # CNN feature map (B, C, H, W)
        return hidden.mean(dim=(2, 3))
    if attention_mask is None:  # ViT tokens (B, T, C)
        return hidden.mean(dim=1)
    mask = attention_mask.unsqueeze(-1).to(hidden.dtype)
    return (hidden * mask).sum(1) / mask.sum(1).clamp(min=1.0)


class TextExportWrapper(nn.Module):
    """(input_ids, attention_mask) -> (logits, L2-normalized mean-pooled embedding)."""

    def __init__(self, model: nn.Module) -> None:
        """Wrap a Hugging Face sequence-classification model."""
        super().__init__()
        self.model = model

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """Logits and the embedding used for similar-case retrieval."""
        out = self.model(input_ids=input_ids, attention_mask=attention_mask, output_hidden_states=True)
        embedding = _mean_pool(out.hidden_states[-1], attention_mask)
        return out.logits, nn.functional.normalize(embedding, dim=-1)


class ImageExportWrapper(nn.Module):
    """pixel_values -> (logits, L2-normalized globally pooled last feature map)."""

    def __init__(self, model: nn.Module) -> None:
        """Wrap a Hugging Face image-classification model."""
        super().__init__()
        self.model = model

    def forward(self, pixel_values: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """Logits and the embedding used for similar-case retrieval."""
        out = self.model(pixel_values=pixel_values, output_hidden_states=True)
        embedding = _mean_pool(out.hidden_states[-1])
        return out.logits, nn.functional.normalize(embedding, dim=-1)


class Task(Protocol):
    """What the modality-agnostic pipeline needs from a modality."""

    model: nn.Module

    def batches(self, split: Split, batch_size: int, *, shuffle: bool, augment: bool, seed: int) -> Iterator[Batch]:
        """Yield (model kwargs, labels) batches of `split`."""
        ...

    def export_spec(self, example: Split) -> tuple[nn.Module, tuple[torch.Tensor, ...], list[str], dict[str, Any]]:
        """(wrapper, example inputs, input names, dynamic axes) for torch.onnx.export."""
        ...

    def onnx_predict(self, session: ort.InferenceSession, split: Split) -> tuple[np.ndarray, np.ndarray]:
        """(raw logits, embeddings) of `split` through the exported ONNX graph."""
        ...

    def save_preprocessing(self, out_dir: Path) -> dict[str, Any]:
        """Write any extra runtime file (tokenizer.json) and return metadata's
        `preprocessing` block.
        """
        ...


# ── text ──────────────────────────────────────────────────────────────────────


@dataclass
class TextTask:
    """Sequence classification over free text (e.g. BioClinical ModernBERT)."""

    model: nn.Module
    tokenizer: Any  # transformers PreTrainedTokenizerFast
    max_length: int

    def _encode(self, texts: list[str]) -> dict[str, torch.Tensor]:
        encoded = self.tokenizer(texts, padding=True, truncation=True, max_length=self.max_length, return_tensors="pt")
        return {"input_ids": encoded["input_ids"], "attention_mask": encoded["attention_mask"]}

    def batches(self, split: Split, batch_size: int, *, shuffle: bool, augment: bool, seed: int) -> Iterator[Batch]:
        """Dynamically padded token batches (no augmentation for text)."""
        del augment
        assert isinstance(split.inputs, list)
        order = np.random.default_rng(seed).permutation(len(split)) if shuffle else np.arange(len(split))
        for start in range(0, len(order), batch_size):
            rows = order[start : start + batch_size]
            yield self._encode([split.inputs[i] for i in rows]), torch.as_tensor(split.labels[rows])

    def export_spec(self, example: Split) -> tuple[nn.Module, tuple[torch.Tensor, ...], list[str], dict[str, Any]]:
        """Dynamic batch and sequence axes, so dl-inference can pad per request."""
        assert isinstance(example.inputs, list)
        encoded = self._encode(example.inputs[:2])
        dynamic = {name: {0: "batch", 1: "sequence"} for name in ONNX_TEXT_INPUTS}
        dynamic |= {ONNX_LOGITS_OUTPUT: {0: "batch"}, ONNX_EMBEDDING_OUTPUT: {0: "batch"}}
        return (
            TextExportWrapper(self.model),
            (encoded["input_ids"], encoded["attention_mask"]),
            list(ONNX_TEXT_INPUTS),
            dynamic,
        )

    def runtime_tokenizer(self) -> Tokenizer:
        """The exact `tokenizers` object dl-inference loads from tokenizer.json."""
        tokenizer = Tokenizer.from_str(self.tokenizer.backend_tokenizer.to_str())
        tokenizer.enable_truncation(self.max_length)
        tokenizer.no_padding()
        return tokenizer

    def onnx_predict(self, session: ort.InferenceSession, split: Split) -> tuple[np.ndarray, np.ndarray]:
        """Raw logits + embeddings; tokenized with the runtime tokenizer (not the HF wrapper)."""
        assert isinstance(split.inputs, list)
        tokenizer = self.runtime_tokenizer()
        pad_id = int(self.tokenizer.pad_token_id)
        all_logits, embeddings = [], []
        for start in range(0, len(split), PREDICT_BATCH_SIZE):
            encodings = tokenizer.encode_batch(split.inputs[start : start + PREDICT_BATCH_SIZE])
            width = max(len(e.ids) for e in encodings)
            ids = np.full((len(encodings), width), pad_id, dtype=np.int64)
            mask = np.zeros((len(encodings), width), dtype=np.int64)
            for row, e in enumerate(encodings):
                ids[row, : len(e.ids)] = e.ids
                mask[row, : len(e.ids)] = 1
            logits, embedding = session.run(None, {"input_ids": ids, "attention_mask": mask})
            all_logits.append(np.asarray(logits))
            embeddings.append(np.asarray(embedding))
        return np.concatenate(all_logits), np.concatenate(embeddings)

    def save_preprocessing(self, out_dir: Path) -> dict[str, Any]:
        """tokenizer.json + the ids dl-inference needs for padding and word occlusion."""
        self.runtime_tokenizer().save(str(out_dir / "tokenizer.json"))
        mask_id = self.tokenizer.mask_token_id
        return {
            "max_length": self.max_length,
            "pad_token_id": int(self.tokenizer.pad_token_id),
            "mask_token_id": int(mask_id) if mask_id is not None else None,
        }


# ── image ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ImagePreprocessing:
    """Exactly what dl-inference does to an image before the ONNX graph: convert to
    `source_mode` ("L" grayscale / "RGB"), resize to `size`, scale to [0, 1], replicate
    grayscale to 3 channels, normalize with the base model's own mean/std.
    """

    size: int
    mean: tuple[float, float, float]
    std: tuple[float, float, float]
    source_mode: str

    def to_pixels(self, images: np.ndarray) -> np.ndarray:
        """uint8 (N, H, W) or (N, H, W, 3) -> float32 (N, 3, size, size), normalized."""
        if images.shape[1] != self.size or images.shape[2] != self.size:
            images = np.stack([
                np.asarray(Image.fromarray(img).resize((self.size, self.size), Image.Resampling.BILINEAR))
                for img in images
            ])
        x = images.astype(np.float32) / 255.0
        x = np.repeat(x[:, None, :, :], 3, axis=1) if x.ndim == 3 else x.transpose(0, 3, 1, 2)
        mean = np.asarray(self.mean, dtype=np.float32).reshape(1, 3, 1, 1)
        std = np.asarray(self.std, dtype=np.float32).reshape(1, 3, 1, 1)
        return (x - mean) / std


def augment_images(pixels: torch.Tensor, rng: np.random.Generator) -> torch.Tensor:
    """Mild, radiograph-safe augmentation: random 85-100% crop resized back, small
    brightness/contrast jitter. No flips — a mirrored chest X-ray is anatomically
    wrong (heart on the right), so it would teach the model an impossible image.
    """
    b, _, h, w = pixels.shape
    out = torch.empty_like(pixels)
    for i in range(b):
        scale = rng.uniform(0.85, 1.0)
        ch, cw = int(h * scale), int(w * scale)
        top, left = int(rng.integers(0, h - ch + 1)), int(rng.integers(0, w - cw + 1))
        crop = pixels[i : i + 1, :, top : top + ch, left : left + cw]
        out[i] = nn.functional.interpolate(crop, size=(h, w), mode="bilinear", align_corners=False)[0]
    contrast = torch.as_tensor(rng.uniform(0.9, 1.1, size=(b, 1, 1, 1)), dtype=pixels.dtype)
    brightness = torch.as_tensor(rng.uniform(-0.1, 0.1, size=(b, 1, 1, 1)), dtype=pixels.dtype)
    return out * contrast + brightness


@dataclass
class ImageTask:
    """Image classification (e.g. ConvNeXt V2 on chest X-rays)."""

    model: nn.Module
    preprocessing: ImagePreprocessing

    def batches(self, split: Split, batch_size: int, *, shuffle: bool, augment: bool, seed: int) -> Iterator[Batch]:
        """Normalized pixel batches, optionally augmented."""
        assert isinstance(split.inputs, np.ndarray)
        rng = np.random.default_rng(seed)
        order = rng.permutation(len(split)) if shuffle else np.arange(len(split))
        for start in range(0, len(order), batch_size):
            rows = order[start : start + batch_size]
            pixels = torch.from_numpy(self.preprocessing.to_pixels(split.inputs[rows]))
            if augment:
                pixels = augment_images(pixels, rng)
            yield {"pixel_values": pixels}, torch.as_tensor(split.labels[rows])

    def export_spec(self, example: Split) -> tuple[nn.Module, tuple[torch.Tensor, ...], list[str], dict[str, Any]]:
        """Dynamic batch axis only — dl-inference always resizes to `size`."""
        assert isinstance(example.inputs, np.ndarray)
        pixels = torch.from_numpy(self.preprocessing.to_pixels(example.inputs[:2]))
        dynamic = {
            ONNX_IMAGE_INPUT: {0: "batch"},
            ONNX_LOGITS_OUTPUT: {0: "batch"},
            ONNX_EMBEDDING_OUTPUT: {0: "batch"},
        }
        return ImageExportWrapper(self.model), (pixels,), [ONNX_IMAGE_INPUT], dynamic

    def onnx_predict(self, session: ort.InferenceSession, split: Split) -> tuple[np.ndarray, np.ndarray]:
        """Raw logits + embeddings, with the same preprocessing dl-inference applies."""
        assert isinstance(split.inputs, np.ndarray)
        all_logits, embeddings = [], []
        for start in range(0, len(split), PREDICT_BATCH_SIZE):
            pixels = self.preprocessing.to_pixels(split.inputs[start : start + PREDICT_BATCH_SIZE])
            logits, embedding = session.run(None, {ONNX_IMAGE_INPUT: pixels})
            all_logits.append(np.asarray(logits))
            embeddings.append(np.asarray(embedding))
        return np.concatenate(all_logits), np.concatenate(embeddings)

    def save_preprocessing(self, out_dir: Path) -> dict[str, Any]:
        """Image preprocessing is pure metadata — no extra file."""
        del out_dir
        p = self.preprocessing
        return {"size": p.size, "mean": list(p.mean), "std": list(p.std), "source_mode": p.source_mode}


# ── factories ─────────────────────────────────────────────────────────────────


def _labels_maps(dl: DeepLearningConfig) -> tuple[dict[int, str], dict[str, int]]:
    id2label = {i: label.label for i, label in enumerate(dl.labels)}
    return id2label, {v: k for k, v in id2label.items()}


def build_text_task(dl: DeepLearningConfig, cache_dir: Path) -> TextTask:
    """Download (once, cached) the pinned base model and add a fresh classification head."""
    from transformers import AutoConfig, AutoModelForSequenceClassification, AutoTokenizer

    id2label, label2id = _labels_maps(dl)
    tokenizer = AutoTokenizer.from_pretrained(dl.base_model, revision=dl.base_model_revision, cache_dir=cache_dir)
    config = AutoConfig.from_pretrained(
        dl.base_model,
        revision=dl.base_model_revision,
        cache_dir=cache_dir,
        num_labels=len(dl.labels),
        id2label=id2label,
        label2id=label2id,
    )
    # ModernBERT torch.compile()s its embeddings on CUDA by default — that needs Triton +
    # Python headers on the host and can't be exported to ONNX anyway.
    if hasattr(config, "reference_compile"):
        config.reference_compile = False
    model = AutoModelForSequenceClassification.from_pretrained(
        dl.base_model,
        revision=dl.base_model_revision,
        cache_dir=cache_dir,
        config=config,
        ignore_mismatched_sizes=True,
        # sdpa exports to plain ONNX ops; flash-attention kernels would not.
        attn_implementation="sdpa",
    )
    return TextTask(model=model, tokenizer=tokenizer, max_length=dl.max_length)


def build_image_task(dl: DeepLearningConfig, cache_dir: Path, source_mode: str) -> ImageTask:
    """Download (once, cached) the pinned base model and replace its ImageNet head."""
    from huggingface_hub import hf_hub_download
    from transformers import AutoModelForImageClassification

    id2label, label2id = _labels_maps(dl)
    # Only the normalization constants are needed — read them from the pinned
    # preprocessor_config.json rather than AutoImageProcessor (which pulls torchvision).
    processor_path = hf_hub_download(
        dl.base_model, "preprocessor_config.json", revision=dl.base_model_revision, cache_dir=cache_dir
    )
    processor = json.loads(Path(processor_path).read_text())
    model = AutoModelForImageClassification.from_pretrained(
        dl.base_model,
        revision=dl.base_model_revision,
        cache_dir=cache_dir,
        num_labels=len(dl.labels),
        id2label=id2label,
        label2id=label2id,
        ignore_mismatched_sizes=True,
    )
    mean = tuple(float(v) for v in processor.get("image_mean", (0.485, 0.456, 0.406)))
    std = tuple(float(v) for v in processor.get("image_std", (0.229, 0.224, 0.225)))
    preprocessing = ImagePreprocessing(size=dl.image_size, mean=mean, std=std, source_mode=source_mode)  # type: ignore[arg-type]
    return ImageTask(model=model, preprocessing=preprocessing)


def build_task(dl: DeepLearningConfig, cache_dir: Path, sample_inputs: np.ndarray | list[str]) -> Task:
    """The Task for this scenario's modality (grayscale sources keep "L" mode)."""
    if dl.modality == "text":
        return build_text_task(dl, cache_dir)
    assert isinstance(sample_inputs, np.ndarray)
    return build_image_task(dl, cache_dir, source_mode="L" if sample_inputs.ndim == 3 else "RGB")
