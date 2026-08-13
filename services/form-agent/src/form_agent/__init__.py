"""form-agent: ai-circus-framework form-agent service

Main components:
- core: Core infrastructure (logger, system info, settings generator)
- data_model: Validated environment configuration (Pydantic Settings)
- tools: Command-line tools and utilities
"""

from __future__ import annotations

from form_agent.core.logger import get_logger
from form_agent.data_model import get_env_config

__version__ = "0.1.0"
__author__ = "ai-circus-framework contributors"
__all__: list[str] = [
    "get_env_config",
    "get_logger",
]
