"""Tests for core/speech.py: what holds no speech is never transcribed (Whisper answers it with invented words)."""

from __future__ import annotations

import io
import wave

import numpy as np
import pytest

from agui_voice.core import speech

RATE = speech.SAMPLE_RATE


def _noise(seconds: float, dbfs: float) -> np.ndarray:
    return np.random.default_rng(0).normal(0, 10 ** (dbfs / 20), int(RATE * seconds)).astype(np.float32)


def _clicks() -> np.ndarray:
    """The turn that came out as "Thank you.": a quiet room with two loud clicks (peak about -2 dBFS)."""
    samples = _noise(1.3, -75)
    click = 0.35 * np.exp(-np.arange(1200) / 200) * np.random.default_rng(1).normal(0, 1, 1200)
    for start in (3000, 15000):
        samples[start : start + 1200] += click.astype(np.float32)
    return samples


@pytest.mark.parametrize(
    "samples",
    [
        np.zeros(0, dtype=np.float32),
        np.zeros(RATE, dtype=np.float32),
        _noise(1.0, -75),  # the near-silent second that came out in Russian
        _noise(2.0, -40),
        _clicks(),
        (0.01 * np.sin(2 * np.pi * 50 * np.arange(2 * RATE) / RATE)).astype(np.float32),  # mains hum
    ],
    ids=["nothing", "zeros", "near silence", "noise", "clicks", "hum"],
)
def test_what_holds_no_speech_is_not_transcribed(samples: np.ndarray) -> None:
    assert speech.trim_to_speech(samples) is None


def test_the_pauses_around_the_speech_are_cut_and_its_edges_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """From the first speech to the last, a pause between two included; Whisper invents words in a long silence."""
    spans = [{"start": 2 * RATE, "end": 3 * RATE}, {"start": 5 * RATE, "end": 6 * RATE}]
    monkeypatch.setattr(speech, "get_speech_timestamps", lambda *_args: spans)

    kept = speech.trim_to_speech(_noise(10.0, -40))

    assert kept is not None
    assert len(kept) == pytest.approx(int(4.6 * RATE), abs=2)


def test_wav_round_trip() -> None:
    samples = _noise(0.5, -20)
    back = speech.pcm_samples(speech.wav_pcm(speech.samples_wav(samples)))
    assert np.allclose(back, samples, atol=1e-3)


@pytest.mark.parametrize(("channels", "width", "rate"), [(2, 2, RATE), (1, 1, RATE), (1, 2, 44_100)])
def test_another_wav_format_is_refused(channels: int, width: int, rate: int) -> None:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(bytes(400))
    with pytest.raises(ValueError, match="16 kHz"):
        speech.wav_pcm(buffer.getvalue())


@pytest.mark.parametrize("data", [b"", b"RIFF", b"not audio at all, nothing like it", b"RIFF\x00\x00\x00\x00WAVEfmt "])
def test_what_is_not_a_wav_is_refused(data: bytes) -> None:
    with pytest.raises(ValueError, match="not a WAV file"):
        speech.wav_pcm(data)


def test_warm_loads_the_model() -> None:
    speech.warm()
