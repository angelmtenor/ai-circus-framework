"""
- Title:    Public-data download, verification and loading for deep_learning scenarios
- Author:   ai-circus-framework contributors

No dataset file is committed to this repo: each scenario.yaml pins a public source
(Hugging Face dataset files — JSON Lines text or Parquet images — at a commit + SHA-256,
or a MedMNIST .npz + MD5). The first
run downloads it, verifies the digest, and uploads it to the scenario's SeaweedFS bucket
under the tenant prefix (raw/…) — every later run (any host, any pod) reads it from
there. A local cache (DL_CACHE_DIR) avoids re-transferring a 200 MB archive between
runs on the same machine. Every path — cache, SeaweedFS, network — is digest-checked,
so a corrupted or tampered copy is never trained on.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
import numpy as np
from ai_circus_shared.deep_learning import DL_RAW_PREFIX
from ai_circus_shared.scenario_schema import DeepLearningConfig, HuggingFaceFilesSource, NpzImagesSource
from ai_circus_shared.storage import ObjectStore
from PIL import Image
from sklearn.model_selection import train_test_split

from dl_training.core.logger import get_logger

logger = get_logger(__name__)

DOWNLOAD_TIMEOUT_SECONDS = 600.0
_CHUNK = 1 << 20


class DataIntegrityError(RuntimeError):
    """A downloaded/cached/stored raw file doesn't match the digest pinned in scenario.yaml."""


@dataclass(frozen=True)
class RawFile:
    """One raw source file: its name, where to fetch it, and the pinned digest."""

    name: str
    url: str
    algorithm: str  # "sha256" | "md5"
    digest: str

    @property
    def object_key(self) -> str:
        """SeaweedFS key (inside the tenant prefix) the verified file is stored under."""
        return f"{DL_RAW_PREFIX}{self.name}"


def raw_files(dl: DeepLearningConfig) -> list[RawFile]:
    """Every file the scenario's source consists of, with its pinned digest."""
    source = dl.source
    if isinstance(source, HuggingFaceFilesSource):
        return [
            RawFile(
                name=name,
                url=f"https://huggingface.co/datasets/{source.repo}/resolve/{source.revision}/{name}",
                algorithm="sha256",
                digest=source.sha256[name],
            )
            for name in dict.fromkeys(source.files.values())
        ]
    assert isinstance(source, NpzImagesSource)
    name = Path(urlparse(source.url).path).name
    return [RawFile(name=name, url=source.url, algorithm="md5", digest=source.md5)]


def file_digest(path: Path, algorithm: str) -> str:
    """Hex digest of a file, streamed (the X-ray archive is ~200 MB)."""
    h = hashlib.new(algorithm)
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _verified(path: Path, raw: RawFile) -> bool:
    return path.is_file() and file_digest(path, raw.algorithm) == raw.digest


def http_download(url: str, dest: Path) -> None:
    """Stream `url` to `dest` (via a .part file, so an interrupted download never
    looks complete).
    """
    part = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        response.raise_for_status()
        with part.open("wb") as f:
            for chunk in response.iter_bytes(_CHUNK):
                f.write(chunk)
    part.replace(dest)


def ensure_raw(
    store: ObjectStore,
    org_id: str,
    dl: DeepLearningConfig,
    cache_dir: Path,
    download: Callable[[str, Path], None] = http_download,
) -> dict[str, Path]:
    """Make every raw file available locally (verified) and in SeaweedFS.

    Order of preference per file: local cache → the tenant's SeaweedFS copy → the
    public URL. Whatever the origin, the digest must match before it's used, and a
    verified file missing from SeaweedFS is uploaded there.

    Returns:
        {file name: local path} for every raw file.

    Raises:
        DataIntegrityError: when a freshly downloaded file doesn't match its pinned digest.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for raw in raw_files(dl):
        local = cache_dir / raw.name
        local.parent.mkdir(parents=True, exist_ok=True)  # Hub file names may hold folders
        in_store = store.exists(org_id, raw.object_key)
        if not _verified(local, raw):
            if in_store:
                logger.info("Fetching {} from SeaweedFS (org={})", raw.object_key, org_id)
                local.write_bytes(store.get(org_id, raw.object_key))
            if not _verified(local, raw):
                logger.info("Downloading {} from {}", raw.name, raw.url)
                download(raw.url, local)
                if not _verified(local, raw):
                    local.unlink(missing_ok=True)
                    raise DataIntegrityError(f"{raw.name}: {raw.algorithm} mismatch after download from {raw.url}.")
                in_store = False  # the stored copy (if any) was bad — replace it
        if not in_store:
            logger.info("Uploading verified {} to SeaweedFS (org={})", raw.object_key, org_id)
            with local.open("rb") as f:
                store.put(org_id, raw.object_key, f)
        paths[raw.name] = local
    return paths


@dataclass(frozen=True)
class Split:
    """One split. `inputs` is a list of strings (text) or a uint8 array of shape
    (N, H, W) / (N, H, W, 3) (image); `labels` holds class *indices* into
    `DeepLearningConfig.labels`; `masks` (image sources with a `mask_field` only) is a
    bool (N, H, W) array of ground-truth defect pixels — all False for good samples.
    """

    inputs: list[str] | np.ndarray
    labels: np.ndarray
    masks: np.ndarray | None = None

    def __len__(self) -> int:
        return len(self.labels)

    def take(self, indices: np.ndarray) -> Split:
        """A new Split with only the given rows."""
        masks = None if self.masks is None else self.masks[indices]
        if isinstance(self.inputs, list):
            return Split([self.inputs[i] for i in indices], self.labels[indices], masks)
        return Split(self.inputs[indices], self.labels[indices], masks)


@dataclass(frozen=True)
class Dataset:
    """Train/validation/test splits of one scenario."""

    train: Split
    val: Split
    test: Split


def stratified_indices(labels: np.ndarray, n: int | None, seed: int) -> np.ndarray:
    """Up to `n` row indices, class-stratified and reproducible (all rows when n is
    None or ≥ the split size).
    """
    all_rows = np.arange(len(labels))
    if n is None or n >= len(labels):
        return all_rows
    # A class with a single member can't be stratified — fall back to a plain sample.
    _, counts = np.unique(labels, return_counts=True)
    stratify = labels if counts.min() >= 2 and n >= len(counts) else None
    picked, _ = train_test_split(all_rows, train_size=n, random_state=seed, stratify=stratify)
    return np.sort(picked)


def _label_index(dl: DeepLearningConfig, split: str, value: object) -> int:
    key = str(value)
    for i, label in enumerate(dl.labels):
        if label.key == key:
            return i
    raise ValueError(f"{split}: label {key!r} is not in scenario.yaml's deep_learning.labels.")


def _with_validation(dl: DeepLearningConfig, source: HuggingFaceFilesSource, read: Callable[[str], Split]) -> Dataset:
    """train/test as published; validation as published, or carved out of train."""
    train, test = read("train"), read("test")
    if "validation" in source.files:
        return Dataset(train=train, val=read("validation"), test=test)
    val_rows = stratified_indices(train.labels, round(len(train) * dl.training.val_fraction), dl.training.seed)
    keep = np.setdiff1d(np.arange(len(train)), val_rows)
    return Dataset(train=train.take(keep), val=train.take(val_rows), test=test)


def _load_text(dl: DeepLearningConfig, source: HuggingFaceFilesSource, paths: dict[str, Path]) -> Dataset:
    assert source.text_field is not None

    def read(split: str) -> Split:
        texts: list[str] = []
        labels: list[int] = []
        for line in paths[source.files[split]].read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            labels.append(_label_index(dl, split, row[source.label_field]))
            texts.append(str(row[source.text_field]).strip())
        return Split(texts, np.asarray(labels, dtype=np.int64))

    return _with_validation(dl, source, read)


def decode_square(data: bytes, size: int, mode: str) -> np.ndarray:
    """Encoded image bytes -> uint8 array resized (not cropped) to size x size — the
    same squash dl-inference applies to an upload, so nothing at the border is lost.
    JPEGs decode straight at a reduced DCT scale (`draft`): ~4x faster on 1.5 MP photos.
    """
    with Image.open(io.BytesIO(data)) as img:
        img.draft(mode, (size, size))
        converted = img.convert(mode)
        return np.asarray(converted.resize((size, size), Image.Resampling.BILINEAR), dtype=np.uint8)


def _load_parquet_images(dl: DeepLearningConfig, source: HuggingFaceFilesSource, paths: dict[str, Path]) -> Dataset:
    """Hub Parquet image datasets: `{bytes, path}` image structs (+ optional masks),
    decoded as RGB at `image_size` — one pass, never the full-resolution originals in RAM.
    """
    import pyarrow.parquet as pq

    assert source.image_field is not None
    size = dl.image_size

    def read(split: str) -> Split:
        columns = [source.image_field, source.label_field] + ([source.mask_field] if source.mask_field else [])
        table = pq.read_table(paths[source.files[split]], columns=columns)  # type: ignore[arg-type]
        cells = table.column(source.image_field).to_pylist()
        images = np.stack([decode_square(cell["bytes"], size, "RGB") for cell in cells])
        values = table.column(source.label_field).to_pylist()
        labels = np.asarray([_label_index(dl, split, v) for v in values], dtype=np.int64)
        masks = None
        if source.mask_field:
            # Good samples have no mask (null) — all False. Thin scratches survive the
            # downscale: any pixel at least a quarter covered by the defect counts.
            empty = np.zeros((size, size), dtype=bool)
            masks = np.stack([
                decode_square(cell["bytes"], size, "L") >= 64 if cell and cell.get("bytes") else empty
                for cell in table.column(source.mask_field).to_pylist()
            ])
        return Split(images, labels, masks)

    return _with_validation(dl, source, read)


def _load_npz(dl: DeepLearningConfig, path: Path) -> Dataset:
    n_classes = len(dl.labels)
    # Labels in the pinned archive are class indices; scenario.yaml's keys are their
    # stringified values — check the two agree instead of trusting the order silently.
    if [label.key for label in dl.labels] != [str(i) for i in range(n_classes)]:
        raise ValueError("npz_images scenarios must list labels with keys '0'..'N-1' in order.")
    with np.load(path, allow_pickle=False) as archive:

        def read(split: str) -> Split:
            images = np.asarray(archive[f"{split}_images"], dtype=np.uint8)
            labels = np.asarray(archive[f"{split}_labels"], dtype=np.int64).reshape(-1)
            if labels.min() < 0 or labels.max() >= n_classes:
                raise ValueError(f"{split}: labels outside 0..{n_classes - 1}.")
            return Split(images, labels)

        return Dataset(train=read("train"), val=read("val"), test=read("test"))


def load_dataset(dl: DeepLearningConfig, paths: dict[str, Path]) -> Dataset:
    """Parse the verified raw files into train/val/test splits of class indices."""
    source = dl.source
    if isinstance(source, HuggingFaceFilesSource):
        if source.image_field is not None:
            return _load_parquet_images(dl, source, paths)
        return _load_text(dl, source, paths)
    (path,) = paths.values()
    return _load_npz(dl, path)
