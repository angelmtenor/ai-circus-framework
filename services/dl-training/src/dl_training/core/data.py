"""
- Title:    Public-data download, verification and loading for deep_learning scenarios
- Author:   Angel Martinez-Tenor

No dataset file is committed to this repo: each scenario.yaml pins a public source
(Hugging Face dataset files — JSON Lines text or Parquet images — at a commit + SHA-256;
a Hub folder of one-file-per-image classes at a commit + one manifest SHA-256, packed
into a single tar; a MedMNIST .npz + MD5; or per-camera .tgz frame sequences, one
SHA-256 each). The first
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
import re
import tarfile
import tempfile
from collections.abc import Callable, Collection, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
import numpy as np
from ai_circus_shared.deep_learning import DL_RAW_PREFIX
from ai_circus_shared.scenario_schema import (
    DeepLearningConfig,
    HttpFrameSequencesSource,
    HuggingFaceFilesSource,
    HuggingFaceImageFolderSource,
    NpzImagesSource,
)
from ai_circus_shared.storage import ObjectStore
from PIL import Image
from sklearn.model_selection import train_test_split

from dl_training.core.logger import get_logger

logger = get_logger(__name__)

DOWNLOAD_TIMEOUT_SECONDS = 600.0
_CHUNK = 1 << 20
HUB = "https://huggingface.co"
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"})
# Digest "algorithm" of a packed image folder: the SHA-256 of its file manifest
# (folder_manifest_digest), not of the tar bytes — so it is independent of how it was packed.
MANIFEST = "sha256-manifest"
_PARALLEL_DOWNLOADS = 8
# Frame sequence archives (http_frame_sequences): `<unix_ts>_<signed offset seconds>.jpg`, anything
# else in the archive (the time-lapse .mp4, directory entries) is ignored. Bounded: a hostile or
# broken archive can't make the trainer decode an unbounded number of / unboundedly large frames.
_FRAME_NAME = re.compile(r"^(\d{9,11})_([+-]\d{1,6})\.(?:jpe?g|png)$")
MAX_FRAMES_PER_SEQUENCE = 400
MAX_FRAME_BYTES = 16 << 20


class DataIntegrityError(RuntimeError):
    """A downloaded/cached/stored raw file doesn't match the digest pinned in scenario.yaml."""


@dataclass(frozen=True)
class RawFile:
    """One raw source file: its name, where to fetch it, and the pinned digest."""

    name: str
    url: str
    algorithm: str  # "sha256" | "md5" | MANIFEST
    digest: str

    @property
    def object_key(self) -> str:
        """SeaweedFS key (inside the tenant prefix) the verified file is stored under."""
        return f"{DL_RAW_PREFIX}{self.name}"


def raw_files(dl: DeepLearningConfig) -> list[RawFile]:
    """Every file the scenario's source consists of, with its pinned digest."""
    source = dl.source
    if isinstance(source, HuggingFaceImageFolderSource):
        return [
            RawFile(
                name=f"image-folder-{source.manifest_sha256[:16]}.tar",
                url=f"{HUB}/datasets/{source.repo}/tree/{source.revision}",
                algorithm=MANIFEST,
                digest=source.manifest_sha256,
            )
        ]
    if isinstance(source, HuggingFaceFilesSource):
        return [
            RawFile(
                name=name,
                url=f"{HUB}/datasets/{source.repo}/resolve/{source.revision}/{name}",
                algorithm="sha256",
                digest=source.sha256[name],
            )
            for name in dict.fromkeys(source.files.values())
        ]
    if isinstance(source, HttpFrameSequencesSource):
        return [
            RawFile(
                name=archive.file,
                url=f"{source.base_url.rstrip('/')}/{quote(archive.file)}",
                algorithm="sha256",
                digest=source.sha256[archive.file],
            )
            for archive in source.archives
        ]
    assert isinstance(source, NpzImagesSource)
    name = Path(urlparse(source.url).path).name
    return [RawFile(name=name, url=source.url, algorithm="md5", digest=source.md5)]


def file_digest(path: Path, algorithm: str) -> str:
    """Hex digest of a file, streamed (the X-ray archive is ~200 MB) — or, for a packed
    image folder (MANIFEST), the digest of its members' manifest ("" when unreadable).
    """
    if algorithm == MANIFEST:
        return archive_manifest_digest(path)
    h = hashlib.new(algorithm)
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def folder_manifest_digest(files: Iterable[tuple[str, str]]) -> str:
    """SHA-256 of the sorted `"<path> <sha256>"` lines (newline-terminated) of (path, file SHA-256) pairs —
    the one value a huggingface_image_folder source pins for all its files.
    """
    return hashlib.sha256("".join(f"{path} {digest}\n" for path, digest in sorted(files)).encode()).hexdigest()


def archive_manifest_digest(path: Path) -> str:
    """folder_manifest_digest of every file packed in a tar ("" when it isn't a readable tar)."""
    try:
        with tarfile.open(path) as tar:
            files = []
            for member in tar:
                handle = tar.extractfile(member) if member.isfile() else None
                if handle is not None:
                    files.append((member.name, hashlib.sha256(handle.read()).hexdigest()))
            return folder_manifest_digest(files)
    except OSError, tarfile.TarError:
        return ""


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


def hub_list_files(repo: str, revision: str, folder: str) -> list[str]:
    """Every file path under `folder` of a Hub dataset repo at `revision` (recursive,
    following the tree API's pagination).
    """
    url: str | None = f"{HUB}/api/datasets/{repo}/tree/{revision}/{quote(folder.strip('/'))}?recursive=true"
    paths: list[str] = []
    while url:
        response = httpx.get(url, follow_redirects=True, timeout=60.0)
        response.raise_for_status()
        paths += [entry["path"] for entry in response.json() if entry.get("type") == "file"]
        url = response.links.get("next", {}).get("url")
    return paths


def _class_files(paths: Iterable[str], folder: str) -> list[str]:
    """Image files exactly one class-subfolder below `folder` (`<folder>/<class>/<file>`)."""
    prefix = folder.strip("/") + "/"
    return [
        p
        for p in paths
        if p.startswith(prefix) and p[len(prefix) :].count("/") == 1 and Path(p).suffix.lower() in IMAGE_SUFFIXES
    ]


def fetch_image_folder(
    source: HuggingFaceImageFolderSource,
    dest: Path,
    download: Callable[[str, Path], None] = http_download,
    list_files: Callable[[str, str, str], list[str]] = hub_list_files,
) -> None:
    """Download every image (and mask) of the source's folders at its pinned revision and
    pack them, keyed by repo path, into one tar at `dest` — a single object to cache and
    store, verified by its manifest like any other raw file.
    """
    folders = [*source.folders.values(), *source.mask_folders.values()]
    listed = {folder: list_files(source.repo, source.revision, folder) for folder in folders}
    paths = sorted({p for folder, files in listed.items() for p in _class_files(files, folder)})
    if not paths:
        raise DataIntegrityError(f"{source.repo}@{source.revision}: no image files under {folders}.")
    unsafe = [p for p in paths if p.startswith("/") or ".." in Path(p).parts]
    if unsafe:  # written to disk before the manifest check — never outside the temp dir
        raise DataIntegrityError(f"{source.repo}: unsafe file paths in the listing: {unsafe[:3]}.")
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        root = Path(tmp)

        def fetch(path: str) -> None:
            local = root / path
            local.parent.mkdir(parents=True, exist_ok=True)
            download(f"{HUB}/datasets/{source.repo}/resolve/{source.revision}/{quote(path)}", local)

        logger.info("Downloading {} files from {}@{}", len(paths), source.repo, source.revision[:12])
        with ThreadPoolExecutor(max_workers=_PARALLEL_DOWNLOADS) as pool:
            list(pool.map(fetch, paths))
        part = dest.with_suffix(dest.suffix + ".part")
        with tarfile.open(part, "w") as tar:
            for path in paths:
                info = tarfile.TarInfo(path)
                info.size = (root / path).stat().st_size
                with (root / path).open("rb") as f:
                    tar.addfile(info, f)
        part.replace(dest)


def ensure_raw(
    store: ObjectStore,
    org_id: str,
    dl: DeepLearningConfig,
    cache_dir: Path,
    download: Callable[[str, Path], None] = http_download,
    list_files: Callable[[str, str, str], list[str]] = hub_list_files,
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
                if isinstance(dl.source, HuggingFaceImageFolderSource):
                    fetch_image_folder(dl.source, local, download, list_files)
                else:
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
    `cameras` / `offsets` (frame-sequence sources only) say which feed each row came from
    and its frame offset in seconds from the recorded event, so the gallery can be
    published in recording order.
    """

    inputs: list[str] | np.ndarray
    labels: np.ndarray
    masks: np.ndarray | None = None
    cameras: list[str] | None = None
    offsets: np.ndarray | None = None

    def __len__(self) -> int:
        return len(self.labels)

    def take(self, indices: np.ndarray) -> Split:
        """A new Split with only the given rows."""
        masks = None if self.masks is None else self.masks[indices]
        cameras = None if self.cameras is None else [self.cameras[i] for i in indices]
        offsets = None if self.offsets is None else self.offsets[indices]
        if isinstance(self.inputs, list):
            return Split([self.inputs[i] for i in indices], self.labels[indices], masks, cameras, offsets)
        return Split(self.inputs[indices], self.labels[indices], masks, cameras, offsets)

    def recording_order(self, indices: np.ndarray) -> np.ndarray:
        """`indices` re-sorted camera by camera, each camera in frame order (unchanged when
        this split has no frame sequence).
        """
        if self.cameras is None or self.offsets is None:
            return indices
        return np.asarray(sorted(indices, key=lambda i: (self.cameras[i], int(self.offsets[i]))), dtype=np.int64)


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


def _label_index(dl: DeepLearningConfig, split: str, value: object, label_map: dict[str, str] | None = None) -> int:
    """Class index of a raw label value, through the source's `label_map` when it has one."""
    key = str(value)
    if label_map:
        key = label_map.get(key, label_map.get("*", key))
    for i, label in enumerate(dl.labels):
        if label.key == key:
            return i
    raise ValueError(f"{split}: label {key!r} is not in scenario.yaml's deep_learning.labels.")


def _with_validation(dl: DeepLearningConfig, splits: Collection[str], read: Callable[[str], Split]) -> Dataset:
    """train/test as published; validation as published, or carved out of train."""
    train, test = read("train"), read("test")
    if "validation" in splits:
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
            labels.append(_label_index(dl, split, row[source.label_field], source.label_map))
            texts.append(str(row[source.text_field]).strip())
        return Split(texts, np.asarray(labels, dtype=np.int64))

    return _with_validation(dl, source.files, read)


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
        labels = np.asarray([_label_index(dl, split, v, source.label_map) for v in values], dtype=np.int64)
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

    return _with_validation(dl, source.files, read)


def _load_image_folder(dl: DeepLearningConfig, source: HuggingFaceImageFolderSource, path: Path) -> Dataset:
    """A packed huggingface_image_folder: class = subfolder (-> label_map), masks matched
    by class and file stem, decoded like the Parquet images (RGB at `image_size`).
    """
    size = dl.image_size
    empty = np.zeros((size, size), dtype=bool)
    with tarfile.open(path) as tar:
        members = {m.name: m for m in tar if m.isfile()}

        def blob(name: str) -> bytes:
            handle = tar.extractfile(members[name])
            assert handle is not None
            return handle.read()

        def read(split: str) -> Split:
            folder = source.folders[split].strip("/")
            masks: dict[tuple[str, str], str] = {}
            if split in source.mask_folders:
                mask_folder = source.mask_folders[split].strip("/")
                for name in _class_files(members, mask_folder):
                    cls, stem = name[len(mask_folder) + 1 :].split("/")[0], Path(name).stem
                    masks[cls, stem.removesuffix(source.mask_suffix)] = name
            names = sorted(_class_files(members, folder))
            if not names:
                raise ValueError(f"{split}: no images under {folder!r}.")
            images, labels, mask_arrays = [], [], []
            for name in names:
                cls = name[len(folder) + 1 :].split("/")[0]
                key = source.label_map.get(cls, source.label_map.get("*"))
                if key is None:
                    raise ValueError(f"{split}: class folder {cls!r} is not in label_map.")
                labels.append(_label_index(dl, split, key))
                images.append(decode_square(blob(name), size, "RGB"))
                mask = masks.get((cls, Path(name).stem))
                mask_arrays.append(decode_square(blob(mask), size, "L") >= 64 if mask else empty)
            return Split(
                np.stack(images),
                np.asarray(labels, dtype=np.int64),
                np.stack(mask_arrays) if source.mask_folders else None,
            )

        return _with_validation(dl, source.folders, read)


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


def _load_frame_sequences(dl: DeepLearningConfig, source: HttpFrameSequencesSource, paths: dict[str, Path]) -> Dataset:
    """http_frame_sequences: one tgz per camera. A frame's label comes from the offset in its
    file name (>= positive_from_offset is the event); rows keep their camera + offset.
    """
    size = dl.image_size
    positive = _label_index(dl, "frames", source.positive_label)
    negative = _label_index(dl, "frames", source.negative_label)

    def read_archive(path: Path, camera: str) -> tuple[list[np.ndarray], list[int], list[int]]:
        frames: dict[int, np.ndarray] = {}
        with tarfile.open(path, "r:gz") as tar:
            for member in tar:
                match = _FRAME_NAME.match(Path(member.name).name) if member.isfile() else None
                if match is None:
                    continue
                if member.size > MAX_FRAME_BYTES:
                    raise ValueError(f"{camera}: frame {member.name!r} is {member.size} bytes (cap {MAX_FRAME_BYTES}).")
                handle = tar.extractfile(member)
                assert handle is not None
                frames[int(match.group(2))] = decode_square(handle.read(), size, "RGB")
                if len(frames) > MAX_FRAMES_PER_SEQUENCE:
                    raise ValueError(f"{camera}: more than {MAX_FRAMES_PER_SEQUENCE} frames in {path.name}.")
        if not frames:
            raise ValueError(f"{camera}: no <timestamp>_<offset>.jpg frames in {path.name}.")
        offsets = sorted(frames)
        labels = [positive if offset >= source.positive_from_offset else negative for offset in offsets]
        return [frames[o] for o in offsets], labels, offsets

    def read(split: str) -> Split:
        images: list[np.ndarray] = []
        labels: list[int] = []
        offsets: list[int] = []
        cameras: list[str] = []
        for archive in (a for a in source.archives if a.split == split):
            frames, frame_labels, frame_offsets = read_archive(paths[archive.file], archive.camera)
            if archive.clear_only:  # a no-event control: nothing at/after the event is ever shown
                before = [o < source.positive_from_offset for o in frame_offsets]
                frames = [f for f, keep in zip(frames, before, strict=True) if keep]
                frame_labels = [x for x, keep in zip(frame_labels, before, strict=True) if keep]
                frame_offsets = [x for x, keep in zip(frame_offsets, before, strict=True) if keep]
            if split != "test" and source.ambiguous_seconds:
                clear = [
                    not source.positive_from_offset <= o < source.positive_from_offset + source.ambiguous_seconds
                    for o in frame_offsets
                ]
                frames = [f for f, keep in zip(frames, clear, strict=True) if keep]
                frame_labels = [x for x, keep in zip(frame_labels, clear, strict=True) if keep]
                frame_offsets = [x for x, keep in zip(frame_offsets, clear, strict=True) if keep]
            images += frames
            labels += frame_labels
            offsets += frame_offsets
            cameras += [archive.camera] * len(frames)
        if not images:
            raise ValueError(f"{split}: no frame sequences.")
        return Split(
            np.stack(images),
            np.asarray(labels, dtype=np.int64),
            cameras=cameras,
            offsets=np.asarray(offsets, dtype=np.int64),
        )

    return _with_validation(dl, {a.split for a in source.archives}, read)


def load_dataset(dl: DeepLearningConfig, paths: dict[str, Path]) -> Dataset:
    """Parse the verified raw files into train/val/test splits of class indices."""
    source = dl.source
    if isinstance(source, HttpFrameSequencesSource):
        return _load_frame_sequences(dl, source, paths)
    if isinstance(source, HuggingFaceImageFolderSource):
        (path,) = paths.values()
        return _load_image_folder(dl, source, path)
    if isinstance(source, HuggingFaceFilesSource):
        if source.image_field is not None:
            return _load_parquet_images(dl, source, paths)
        return _load_text(dl, source, paths)
    (path,) = paths.values()
    return _load_npz(dl, path)
