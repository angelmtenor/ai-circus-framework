"""
- Title:    Info
- Author:   ai-circus-framework contributors
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from importlib.metadata import distributions
from pathlib import Path
from typing import Any, TypeVar

import cpuinfo
import psutil

from agui_voice.core.logger import get_logger

F = TypeVar("F", bound=Callable[..., Any])
logger = get_logger(__name__)

# Cached installed packages
INSTALLED_PACKAGES = {dist.metadata["Name"]: dist.version for dist in distributions()}

# Default modules to log
DEFAULT_MODULES = ["httpx"]


def info_os() -> None:
    """Log operating system version and architecture."""
    logger.info(f"{'OS':<25}{platform.platform()}")


def info_software(modules: list[str] | None = None) -> None:
    """
    Log Python version and versions of specified modules.

    Args:
        modules (list[str] | None): List of module names to log. If None, uses DEFAULT_MODULES.
    """
    logger.info(f"{'ENV':<25}{sys.prefix}")
    logger.info(f"{'PYTHON':<25}{platform.python_version()}")

    for module in modules or DEFAULT_MODULES:
        version = INSTALLED_PACKAGES.get(module, "N/A")
        logger.info(f" - {module:<22}{version}")


def info_hardware() -> None:
    """Log CPU model, core count, and RAM size."""
    cpu = cpuinfo.get_cpu_info().get("brand_raw", "Unknown CPU")
    cores = psutil.cpu_count(logical=True)
    ram_gb = round(psutil.virtual_memory().total / (1024**3))
    logger.info(f"{'MACHINE':<25}{cpu} ({cores} cores, {ram_gb} GB RAM)")


def info_gpu() -> None:
    """Log GPU details using nvidia-smi, if available."""
    try:
        # Check if nvidia-smi is available
        nvidia_smi_path = shutil.which("nvidia-smi")
        if nvidia_smi_path is None:
            logger.info(f"{'GPU':<25}nvidia-smi not found")
            return

        # Validate nvidia-smi path to ensure it's a known executable
        if not Path(nvidia_smi_path).is_file():
            logger.info(f"{'GPU':<25}Invalid nvidia-smi path")
            return

        # Run nvidia-smi command to get GPU info using full path
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [nvidia_smi_path, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=True,
        )
        gpu_name = result.stdout.strip()
        if gpu_name:
            logger.info(f"{'GPU':<25}{gpu_name}")
        else:
            logger.info(f"{'GPU':<25}No GPU detected")
    except subprocess.CalledProcessError:
        logger.info(f"{'GPU':<25}Error querying GPU (nvidia-smi failed)")
    except Exception as e:
        logger.info(f"{'GPU':<25}No GPU available ({e!s})")


def info_system(modules: list[str] | None = None) -> None:
    """
    Log full system information including OS, hardware, and software.

    Args:
        modules (list[str] | None): List of module names to log versions for. Defaults to DEFAULT_MODULES.
    """
    info_hardware()
    info_gpu()
    info_os()
    info_software(modules)
    logger.info(f"{'EXECUTION PATH':<25}{Path().absolute()}")
    logger.info(f"{'EXECUTION DATE':<25}{time.ctime()}")


def get_memory_usage(obj: object) -> float:
    """
    Calculate and return memory usage of an object in megabytes.

    Note: This function uses `sys.getsizeof`, which only accounts for the memory
    usage of the object itself, not including referenced objects. For a more
    accurate measurement, consider using a library like `pympler`.

    Args:
        obj (object): The object to analyze.

    Returns:
        float: Approximate memory usage in MB.
    """
    return round(sys.getsizeof(obj) / 1024**2, 3)
