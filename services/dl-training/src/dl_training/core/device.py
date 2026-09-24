"""
- Title:    Compute device detection and training-budget selection
- Author:   ai-circus-framework contributors

A deep-learning fine-tune is ~100x cheaper on a GPU, so the device decides the whole
run's shape: CUDA gets the scenario's full `training.gpu` budget, CPU gets its reduced
`training.cpu` budget (subset / fewer epochs / frozen lower layers — see
ai_circus_shared.scenario_schema.DlTrainBudget). Both land in metadata.json, so the
admin console can show exactly where and how each deployed model was trained.
"""

from __future__ import annotations

import platform
from dataclasses import asdict, dataclass
from typing import Literal

import torch
from ai_circus_shared.scenario_schema import DlTrainBudget, DlTraining

from dl_training.core.logger import get_logger

logger = get_logger(__name__)

DeviceKind = Literal["cuda", "cpu"]


@dataclass(frozen=True)
class DeviceInfo:
    """Where a training run executes — recorded verbatim in the model's metadata."""

    kind: DeviceKind
    name: str  # e.g. "NVIDIA GeForce RTX 4070 Laptop GPU" or the CPU model
    torch_version: str
    cuda_version: str | None
    memory_gb: float | None  # GPU memory; None on CPU

    @property
    def torch_device(self) -> torch.device:
        """The torch.device tensors/models should be moved to."""
        return torch.device(self.kind)

    def as_dict(self) -> dict[str, object]:
        """JSON-serializable form for metadata.json."""
        return asdict(self)


def _cpu_name() -> str:
    try:
        import cpuinfo

        return str(cpuinfo.get_cpu_info().get("brand_raw") or platform.processor() or "CPU")
    except Exception:  # cpuinfo can fail inside restricted containers — cosmetic only
        return platform.processor() or "CPU"


def resolve_device(requested: str) -> DeviceInfo:
    """Resolve DL_DEVICE ("auto" | "cuda" | "cpu") to the device this run will use.

    Raises:
        ValueError: for an unknown value.
        RuntimeError: if "cuda" is explicitly requested but no CUDA device is usable
            (e.g. the CPU-only torch wheel of the Docker image) — failing loudly beats
            silently running a GPU budget on a CPU for hours.
    """
    requested = requested.strip().lower()
    if requested not in {"auto", "cuda", "cpu"}:
        raise ValueError(f"DL_DEVICE must be auto, cuda or cpu — got {requested!r}.")
    cuda_ok = torch.cuda.is_available()
    if requested == "cuda" and not cuda_ok:
        raise RuntimeError(
            "DL_DEVICE=cuda but torch sees no CUDA device (CPU-only torch build, or no NVIDIA driver visible)."
        )
    if cuda_ok and requested != "cpu":
        props = torch.cuda.get_device_properties(0)
        return DeviceInfo(
            kind="cuda",
            name=props.name,
            torch_version=torch.__version__,
            cuda_version=torch.version.cuda,
            memory_gb=round(props.total_memory / 1024**3, 1),
        )
    return DeviceInfo(kind="cpu", name=_cpu_name(), torch_version=torch.__version__, cuda_version=None, memory_gb=None)


def budget_for(training: DlTraining, device: DeviceInfo) -> DlTrainBudget:
    """The scenario's budget for this device class, with a loud warning on CPU."""
    if device.kind == "cuda":
        return training.gpu
    logger.warning(
        "No CUDA device — training with the scenario's reduced CPU budget ({}). "
        "Expect a slower run and a less accurate model than a GPU run (`make dl-train-*` on a GPU host).",
        training.cpu.model_dump(exclude_none=True),
    )
    return training.cpu
