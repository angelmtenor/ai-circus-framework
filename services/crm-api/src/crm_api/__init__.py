"""crm-api: ai-circus-framework crm-api service

Main components:
- core: Core infrastructure (logger, system info, settings generator)
- data_model: Validated environment configuration (Pydantic Settings)
- tools: Command-line tools and utilities
"""

from __future__ import annotations

from crm_api.core.logger import get_logger
from crm_api.data_model import get_env_config

__version__ = "0.1.0"
__author__ = "Angel Martinez-Tenor"
__all__: list[str] = [
    "get_env_config",
    "get_logger",
]
