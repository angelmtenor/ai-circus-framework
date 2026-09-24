"""Offline, tiny stand-ins for dl-training's tests: scenario configs pointing at local
raw files, a memory ObjectStore, and genuinely tiny Hugging Face models (ModernBERT /
ConvNeXt V2 built from configs, a real `tokenizers` vocabulary) — so the whole
download -> fine-tune -> ONNX export -> evaluate -> publish pipeline runs for real,
in seconds, with no network.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from ai_circus_shared.scenario_schema import (
    ChatConfig,
    DeepLearningConfig,
    DeepLearningServices,
    DlLabel,
    DlTrainBudget,
    DlTraining,
    HuggingFaceFilesSource,
    NpzImagesSource,
    ScenarioDefinition,
)
from tokenizers import Tokenizer, models, pre_tokenizers, processors

from dl_training.core.tasks import ImagePreprocessing, ImageTask, TextTask

VOCAB = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "fever", "rash", "sneezing", "nose", "i", "have", "a", "and"]
BUDGET = DlTrainBudget(epochs=2, batch_size=8, learning_rate=5e-3)
TEXT_ROWS = {
    "train": [("i have fever and rash", "dengue"), ("sneezing and nose", "cold")] * 12,
    "test": [("fever rash", "dengue"), ("nose sneezing", "cold")] * 3,
}


class MemoryStore:
    """In-memory stand-in for ai_circus_shared.storage.ObjectStore."""

    def __init__(self, bucket: str = "test-bucket") -> None:
        """Empty store."""
        self.bucket = bucket
        self.objects: dict[str, bytes] = {}

    def put(self, org_id: str, path: str, data: object) -> str:
        """Store bytes or a file-like object's content under the tenant prefix."""
        key = f"tenant-{org_id}/{path}"
        self.objects[key] = data if isinstance(data, bytes) else data.read()  # type: ignore[attr-defined]
        return key

    def get(self, org_id: str, path: str) -> bytes:
        """Read bytes (KeyError when absent)."""
        return self.objects[f"tenant-{org_id}/{path}"]

    def exists(self, org_id: str, path: str) -> bool:
        """Whether an object exists."""
        return f"tenant-{org_id}/{path}" in self.objects


def sha256(data: bytes) -> str:
    """Hex SHA-256."""
    return hashlib.sha256(data).hexdigest()


def text_raw_files() -> dict[str, bytes]:
    """JSONL train/test files in the gretelai/symptom_to_diagnosis layout."""
    return {
        f"{split}.jsonl": "\n".join(
            json.dumps({"input_text": text, "output_text": label}) for text, label in rows
        ).encode()
        for split, rows in TEXT_ROWS.items()
    }


def text_config(**training: object) -> DeepLearningConfig:
    """A deep_learning text config whose pinned digests match text_raw_files()."""
    files = text_raw_files()
    return DeepLearningConfig(
        modality="text",
        bucket="b",
        source=HuggingFaceFilesSource(
            repo="org/data",
            revision="abc",
            files={"train": "train.jsonl", "test": "test.jsonl"},
            sha256={name: sha256(data) for name, data in files.items()},
            text_field="input_text",
            label_field="output_text",
        ),
        labels=[DlLabel(key="dengue", label="Dengue"), DlLabel(key="cold", label="Common cold")],
        base_model="tiny/text",
        base_model_revision="r1",
        base_model_params="6K",
        input_label="Symptoms",
        target_label="Condition",
        max_length=16,
        quantize=True,
        training=DlTraining(gpu=BUDGET, cpu=BUDGET, **training),  # type: ignore[arg-type]
        gallery_size=4,
        reference_size=6,
    )


def image_npz_bytes(size: int = 32) -> bytes:
    """A MedMNIST-layout .npz: class 1 images are bright in the top-left corner."""
    import io

    rng = np.random.default_rng(0)

    def split(n: int) -> tuple[np.ndarray, np.ndarray]:
        labels = np.arange(n) % 2
        images = rng.integers(0, 40, size=(n, size, size), dtype=np.uint8)
        images[labels == 1, : size // 2, : size // 2] = 220
        return images, labels.reshape(-1, 1)

    arrays = {}
    for name, n in (("train", 24), ("val", 8), ("test", 8)):
        arrays[f"{name}_images"], arrays[f"{name}_labels"] = split(n)
    buffer = io.BytesIO()
    np.savez(buffer, **arrays)
    return buffer.getvalue()


def image_config() -> DeepLearningConfig:
    """A deep_learning image config whose pinned MD5 matches image_npz_bytes()."""
    return DeepLearningConfig(
        modality="image",
        bucket="b",
        source=NpzImagesSource(
            url="https://example.org/files/tiny.npz?download=1",
            md5=hashlib.md5(image_npz_bytes(), usedforsecurity=False).hexdigest(),
        ),
        labels=[DlLabel(key="0", label="Normal"), DlLabel(key="1", label="Pneumonia")],
        base_model="tiny/image",
        base_model_revision="r1",
        base_model_params="5K",
        input_label="X-ray",
        target_label="Finding",
        image_size=32,
        occlusion_grid=4,
        training=DlTraining(gpu=BUDGET, cpu=BUDGET, class_weighted_loss=True),
        gallery_size=4,
        reference_size=6,
    )


def scenario(dl: DeepLearningConfig, slug: str = "tiny") -> ScenarioDefinition:
    """Wrap a config in a full ScenarioDefinition."""
    return ScenarioDefinition(
        slug=slug,
        kind="deep_learning",
        title="Tiny",
        description="d",
        role_required=f"scenario:{slug}",
        icon="🧪",
        industry="healthcare",
        chat=ChatConfig(context="c"),
        deep_learning=dl,
        services=DeepLearningServices(training="dl-training", inference="dl-inference"),
    )


def seed_cache(cache_dir: Path, files: dict[str, bytes]) -> None:
    """Pre-populate the local raw-data cache (so no download is attempted)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (cache_dir / name).write_bytes(data)


def tiny_text_task(dl: DeepLearningConfig, *_: object) -> TextTask:
    """A 2-layer, 16-dim ModernBERT with a real WordLevel tokenizer."""
    from transformers import ModernBertConfig, ModernBertForSequenceClassification, PreTrainedTokenizerFast

    tokenizer = Tokenizer(models.WordLevel({w: i for i, w in enumerate(VOCAB)}, unk_token="[UNK]"))  # ruff: ignore[hardcoded-password-func-arg]
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 2), ("[SEP]", 3)]
    )
    hf_tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=tokenizer,
        pad_token="[PAD]",  # ruff: ignore[hardcoded-password-func-arg]
        unk_token="[UNK]",  # ruff: ignore[hardcoded-password-func-arg]
        cls_token="[CLS]",  # ruff: ignore[hardcoded-password-func-arg]
        sep_token="[SEP]",  # ruff: ignore[hardcoded-password-func-arg]
        mask_token="[MASK]",  # ruff: ignore[hardcoded-password-func-arg]
    )
    config = ModernBertConfig(
        vocab_size=len(VOCAB),
        hidden_size=16,
        intermediate_size=32,
        num_hidden_layers=2,
        num_attention_heads=2,
        pad_token_id=0,
        cls_token_id=2,
        sep_token_id=3,
        bos_token_id=2,
        eos_token_id=3,
        num_labels=len(dl.labels),
        reference_compile=False,
        global_attn_every_n_layers=1,
    )
    return TextTask(model=ModernBertForSequenceClassification(config), tokenizer=hf_tokenizer, max_length=dl.max_length)


def tiny_image_task(dl: DeepLearningConfig, *_: object) -> ImageTask:
    """A 2-stage, 8/16-channel ConvNeXt V2."""
    from transformers import ConvNextV2Config, ConvNextV2ForImageClassification

    config = ConvNextV2Config(
        num_channels=3, hidden_sizes=[8, 16], depths=[1, 1], num_stages=2, image_size=dl.image_size, num_labels=2
    )
    return ImageTask(
        model=ConvNextV2ForImageClassification(config),
        preprocessing=ImagePreprocessing(
            size=dl.image_size, mean=(0.5, 0.5, 0.5), std=(0.5, 0.5, 0.5), source_mode="L"
        ),
    )


def tiny_task(dl: DeepLearningConfig, cache_dir: Path, sample_inputs: object) -> TextTask | ImageTask:
    """task_builder for pipeline.train_scenario that never touches the Hub."""
    return tiny_text_task(dl) if dl.modality == "text" else tiny_image_task(dl)
