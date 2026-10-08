"""Tests for the http_frame_sequences source: per-camera tgz archives of `<ts>_<offset>.jpg` frames.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import numpy as np
import pytest
from ai_circus_shared.scenario_schema import FrameSequenceArchive, HttpFrameSequencesSource
from PIL import Image

from dl_training.core import artifacts, data
from tests.fixtures import MemoryStore, image_config

OFFSETS = [-120, -60, 0, 60, 120, 600, 660, 720]  # 8 frames a camera; 0..540 are ambiguous (600 s)


def _jpeg(shade: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (shade, shade, shade)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _archive(offsets: list[int], extra: dict[str, bytes] | None = None) -> bytes:
    """A tgz shaped like FIgLib's: a directory, the frames (shuffled), and a time-lapse .mp4."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        directory = tarfile.TarInfo("seq/")
        directory.type = tarfile.DIRTYPE
        tar.addfile(directory)
        members = {f"seq/1500000000_{o:+06d}.jpg": _jpeg(90 + i) for i, o in enumerate(offsets)}
        members["seq/timelapse.mp4"] = b"not a frame"
        members.update(extra or {})
        for name, blob in reversed(list(members.items())):
            info = tarfile.TarInfo(name)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
    return buffer.getvalue()


def _config(blobs: dict[str, bytes], **overrides: object):  # type: ignore[no-untyped-def]
    splits = {"a.tgz": "train", "b.tgz": "train", "v.tgz": "validation", "t1.tgz": "test", "t2.tgz": "test"}
    source = HttpFrameSequencesSource(
        base_url="https://example.org/Tar",
        archives=[FrameSequenceArchive(file=f, split=s, camera=f[:-4]) for f, s in splits.items()],
        sha256={f: hashlib.sha256(blobs[f]).hexdigest() for f in splits},
        negative_label="0",
        positive_label="1",
        **overrides,  # type: ignore[arg-type]
    )
    return image_config().model_copy(update={"source": source})


def _blobs() -> dict[str, bytes]:
    return {f: _archive(OFFSETS) for f in ("a.tgz", "b.tgz", "v.tgz", "t1.tgz", "t2.tgz")}


def _fetch(blobs: dict[str, bytes], calls: list[str] | None = None):  # type: ignore[no-untyped-def]
    def download(url: str, dest: Path) -> None:
        if calls is not None:
            calls.append(url)
        dest.write_bytes(blobs[url.rsplit("/", 1)[1]])

    return {"download": download}


def test_each_archive_is_downloaded_digest_checked_and_reused(tmp_path: Path) -> None:
    blobs, calls, store = _blobs(), [], MemoryStore()
    dl = _config(blobs)

    paths = data.ensure_raw(store, "demo", dl, tmp_path, **_fetch(blobs, calls))  # type: ignore[arg-type]

    assert sorted(paths) == sorted(blobs)
    assert calls and all(url.startswith("https://example.org/Tar/") for url in calls)
    assert all(store.exists("demo", f"raw/{name}") for name in blobs)
    calls.clear()
    data.ensure_raw(store, "demo", dl, tmp_path / "other-host", **_fetch(blobs, calls))  # type: ignore[arg-type]
    assert calls == []  # every later run reads SeaweedFS


def test_a_changed_archive_fails_loudly(tmp_path: Path) -> None:
    blobs = _blobs()
    dl = _config(blobs)
    blobs["t1.tgz"] = _archive(OFFSETS[:-1])  # the CDN now serves different bytes

    with pytest.raises(data.DataIntegrityError, match="t1.tgz"):
        data.ensure_raw(MemoryStore(), "demo", dl, tmp_path, **_fetch(blobs))  # type: ignore[arg-type]


def test_frames_are_labelled_by_filename_offset_ordered_and_tagged_with_their_camera(tmp_path: Path) -> None:
    blobs = _blobs()
    dl = _config(blobs)
    paths = data.ensure_raw(MemoryStore(), "demo", dl, tmp_path, **_fetch(blobs))  # type: ignore[arg-type]

    dataset = data.load_dataset(dl, paths)

    test = dataset.test
    assert len(test) == 2 * len(OFFSETS)  # the test split keeps every frame of both cameras
    assert test.cameras == ["t1"] * len(OFFSETS) + ["t2"] * len(OFFSETS)
    assert test.offsets is not None and list(test.offsets[: len(OFFSETS)]) == sorted(OFFSETS)
    # offset >= 0 is the event; the .mp4 and the directory entry are ignored
    assert list(test.labels[: len(OFFSETS)]) == [0, 0, 1, 1, 1, 1, 1, 1]
    assert np.asarray(test.inputs).shape == (2 * len(OFFSETS), 32, 32, 3)
    assert len(dataset.val) == len(OFFSETS)  # the validation archive is its own camera, not carved from train


def test_ambiguous_seconds_are_dropped_from_train_and_validation_but_kept_in_test(tmp_path: Path) -> None:
    blobs = _blobs()
    dl = _config(blobs, ambiguous_seconds=600)
    paths = data.ensure_raw(MemoryStore(), "demo", dl, tmp_path, **_fetch(blobs))  # type: ignore[arg-type]

    dataset = data.load_dataset(dl, paths)

    kept = [o for o in OFFSETS if not 0 <= o < 600]  # 0, 60 are inside the band; 600 is the first clear positive
    assert sorted(set(dataset.val.offsets.tolist())) == sorted(kept)  # type: ignore[union-attr]
    assert len(dataset.train) == 2 * len(kept)
    assert len(dataset.test) == 2 * len(OFFSETS)
    assert 0 in dataset.test.offsets.tolist()  # type: ignore[union-attr]


def test_an_archive_without_frames_or_with_too_many_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blobs = _blobs()
    blobs["a.tgz"] = _archive([])
    dl = _config(blobs)
    paths = data.ensure_raw(MemoryStore(), "demo", dl, tmp_path, **_fetch(blobs))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="no <timestamp>_<offset>.jpg frames"):
        data.load_dataset(dl, paths)

    blobs = _blobs()
    dl = _config(blobs)
    paths = data.ensure_raw(MemoryStore(), "demo", dl, tmp_path / "2", **_fetch(blobs))  # type: ignore[arg-type]
    monkeypatch.setattr(data, "MAX_FRAMES_PER_SEQUENCE", 3)
    with pytest.raises(ValueError, match="more than 3 frames"):
        data.load_dataset(dl, paths)


def test_the_gallery_is_published_in_recording_order_with_camera_and_offset(tmp_path: Path) -> None:
    blobs = _blobs()
    dl = _config(blobs)
    paths = data.ensure_raw(MemoryStore(), "demo", dl, tmp_path, **_fetch(blobs))  # type: ignore[arg-type]
    test = data.load_dataset(dl, paths).test
    shuffled = np.random.default_rng(0).permutation(len(test))

    ordered = test.recording_order(shuffled)
    gallery = test.take(ordered)
    document = artifacts.samples_document(
        artifacts.Published(gallery, np.full((len(gallery), 2), 0.5), gallery, np.zeros((len(gallery), 2))),
        ["0", "1"],
    )

    samples = document["samples"]
    assert [(s["group"], s["seq"]) for s in samples] == [(c, o) for c in ("t1", "t2") for o in sorted(OFFSETS)]
    assert samples[0]["id"] == "s-0"


def test_a_split_without_frame_sequences_is_untouched() -> None:
    split = data.Split(np.zeros((3, 4, 4, 3), dtype=np.uint8), np.array([0, 1, 0]))
    indices = np.array([2, 0, 1])

    assert split.recording_order(indices) is indices
    assert split.take(indices).cameras is None
    assert "group" not in artifacts.samples_document(
        artifacts.Published(split, np.full((3, 2), 0.5), split, np.zeros((3, 2))), ["0", "1"]
    )["samples"][0]


def test_a_clear_only_control_keeps_just_the_frames_before_the_event(tmp_path: Path) -> None:
    blobs = _blobs()
    dl = _config(blobs)
    archives = [a.model_copy(update={"clear_only": a.file == "t2.tgz"}) for a in dl.source.archives]  # type: ignore[union-attr]
    dl = dl.model_copy(update={"source": dl.source.model_copy(update={"archives": archives})})
    paths = data.ensure_raw(MemoryStore(), "demo", dl, tmp_path, **_fetch(blobs))  # type: ignore[arg-type]

    test = data.load_dataset(dl, paths).test

    control = [i for i, camera in enumerate(test.cameras or []) if camera == "t2"]
    assert [int(test.offsets[i]) for i in control] == [-120, -60]  # type: ignore[index]
    assert {int(test.labels[i]) for i in control} == {0}  # a feed in which no frame is the event
    assert sum(camera == "t1" for camera in test.cameras or []) == len(OFFSETS)  # the fire camera is whole
