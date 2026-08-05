"""Tests for core system/environment info utilities.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import training.core.info as info


def test_get_memory_usage_returns_positive_megabytes_for_larger_objects() -> None:
    """get_memory_usage should grow with object size and never be negative."""
    small = info.get_memory_usage([])
    large = info.get_memory_usage(list(range(100_000)))

    assert large > small >= 0


def test_info_os_logs_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """info_os() should log the platform string."""
    messages: list[str] = []
    monkeypatch.setattr(info.platform, "platform", lambda: "Linux-test")
    monkeypatch.setattr(info.logger, "info", lambda msg: messages.append(msg))

    info.info_os()

    assert any("Linux-test" in msg for msg in messages)


def test_info_software_logs_requested_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """info_software() should report the version of each requested module, or N/A if missing."""
    messages: list[str] = []
    monkeypatch.setattr(info, "INSTALLED_PACKAGES", {"httpx": "1.2.3"})
    monkeypatch.setattr(info.logger, "info", lambda msg: messages.append(msg))

    info.info_software(["httpx", "not-installed"])

    assert any("1.2.3" in msg for msg in messages)
    assert any("N/A" in msg for msg in messages)


def test_info_hardware_logs_cpu_and_ram(monkeypatch: pytest.MonkeyPatch) -> None:
    """info_hardware() should log CPU brand, core count, and RAM size."""
    messages: list[str] = []
    monkeypatch.setattr(info.cpuinfo, "get_cpu_info", lambda: {"brand_raw": "Test CPU"})
    monkeypatch.setattr(info.psutil, "cpu_count", lambda logical=True: 8)
    monkeypatch.setattr(info.psutil, "virtual_memory", lambda: type("Mem", (), {"total": 16 * 1024**3})())
    monkeypatch.setattr(info.logger, "info", lambda msg: messages.append(msg))

    info.info_hardware()

    assert any("Test CPU" in msg and "8 cores" in msg and "16 GB" in msg for msg in messages)


def test_info_gpu_reports_missing_nvidia_smi(monkeypatch: pytest.MonkeyPatch) -> None:
    """info_gpu() should report nvidia-smi not found when it's absent from PATH."""
    messages: list[str] = []
    monkeypatch.setattr(info.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(info.logger, "info", lambda msg: messages.append(msg))

    info.info_gpu()

    assert any("not found" in msg for msg in messages)


def test_info_gpu_reports_detected_gpu(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """info_gpu() should log the GPU name when nvidia-smi succeeds."""
    fake_smi = tmp_path / "nvidia-smi"
    fake_smi.write_text("")
    messages: list[str] = []

    monkeypatch.setattr(info.shutil, "which", lambda cmd: str(fake_smi))
    monkeypatch.setattr(
        info.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="Test GPU\n", stderr=""),
    )
    monkeypatch.setattr(info.logger, "info", lambda msg: messages.append(msg))

    info.info_gpu()

    assert any("Test GPU" in msg for msg in messages)
