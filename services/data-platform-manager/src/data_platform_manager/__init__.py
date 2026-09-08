"""data-platform-manager: ai-circus-framework data-platform-manager service

Main components:
- core: Core infrastructure (logger, system info, settings generator)
- data_model: Validated environment configuration (Pydantic Settings)
- tools: Command-line tools and utilities
"""

from __future__ import annotations

from data_platform_manager.core.logger import get_logger
from data_platform_manager.data_model import get_env_config

__version__ = "0.1.0"
__author__ = "ai-circus-framework contributors"
__all__: list[str] = [
    "get_env_config",
    "get_logger",
]
