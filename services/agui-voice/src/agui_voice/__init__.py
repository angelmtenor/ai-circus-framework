"""agui-voice: ai-circus-framework agui-voice service

Main components:
- core: Core infrastructure (logger, system info, settings generator)
- data_model: Validated environment configuration (Pydantic Settings)
- tools: Command-line tools and utilities
"""

from __future__ import annotations

from agui_voice.core.logger import get_logger
from agui_voice.data_model import get_env_config

__version__ = "0.1.0"
__author__ = "ai-circus-framework contributors"
__all__: list[str] = [
    "get_env_config",
    "get_logger",
]
