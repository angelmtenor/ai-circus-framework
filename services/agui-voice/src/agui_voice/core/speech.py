"""
- Title:    Whether a recording holds any speech
- Author:   Angel Martinez-Tenor

Whisper always writes something: given a click, a breath or a second of near-silence, it answers "Thank you." or a
subtitle credit in another language, and that reached the agent as the user's question. So nothing is transcribed
before Silero's voice-activity model, which faster-whisper bundles (about 5 ms a turn), finds speech in it, and only the
part that holds speech is. It never decides when a turn starts or ends: the user does (docs/adr/0033). Level alone
could not tell them apart: a mouse click peaks at -9 dBFS, clear quiet speech at -36.
"""

from __future__ import annotations

import io
import wave

import numpy as np
from faster_whisper.vad import VadOptions, get_speech_timestamps

SAMPLE_RATE = 16_000
"""The one rate the browser records at, Whisper expects and the model below takes."""

SPEECH_PAD_SECONDS = 0.3
"""Kept around the speech, so its first and last sounds are not cut."""

_VAD = VadOptions(
    threshold=0.3,  # lenient: losing quiet speech is worse than transcribing a noise
    min_speech_duration_ms=100,
    min_silence_duration_ms=500,
    speech_pad_ms=0,
)


def pcm_samples(pcm: bytes) -> np.ndarray:
    """16-bit little-endian mono PCM as floats in [-1, 1]."""
    return np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0


def wav_pcm(audio: bytes) -> bytes:
    """The PCM of a mono 16-bit WAV at `SAMPLE_RATE`; ValueError for anything else."""
    try:
        with wave.open(io.BytesIO(audio)) as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, SAMPLE_RATE):
                raise ValueError("expected mono 16-bit PCM at 16 kHz")
            return wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError("not a WAV file") from exc


def samples_wav(samples: np.ndarray) -> bytes:
    """Float samples as a mono 16-bit WAV, the form a hosted transcription API takes."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes((np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes())
    return buffer.getvalue()


def peak_level(samples: np.ndarray) -> float:
    """The loudest sample (0 when there are none)."""
    return float(np.abs(samples).max()) if samples.size else 0.0


def trim_to_speech(samples: np.ndarray) -> np.ndarray | None:
    """The part of a recording from its first speech to its last, or None when it holds none. CPU work: off the loop."""
    if not samples.size:
        return None
    spans = get_speech_timestamps(samples, _VAD)
    if not spans:
        return None
    pad = int(SPEECH_PAD_SECONDS * SAMPLE_RATE)
    return samples[max(spans[0]["start"] - pad, 0) : spans[-1]["end"] + pad]


def warm() -> None:
    """Load the voice-activity model, so the first turn does not."""
    trim_to_speech(np.zeros(SAMPLE_RATE, dtype=np.float32))
