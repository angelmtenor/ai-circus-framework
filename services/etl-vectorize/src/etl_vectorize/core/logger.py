"""
- Title:    Custom Logger
- Author:   ai-circus-framework contributors
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field, ValidationError

# === Constants ===
LOG_DIR = Path("log")
FILENAME_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
DEFAULT_LOG_LEVEL = "INFO"

# === Log Format Templates ===
# {extra[name]} is now included so get_logger() bindings are visible
CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level:<8}</level> | "
    "<cyan>{file.name}:{line}</cyan> | "
    "<cyan>{extra[name]}</cyan> | "
    "<level>{message}</level>"
)
FILE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {file.name}:{line} | {extra[name]} | {message}"

_configured = False

# Global extra defaults — prevents KeyError when format uses {extra[name]}
# Any unbound logger.info(...) call will show "-" instead of crashing.
logger.configure(extra={"name": "-"})


# === Config — BaseModel instead of Pydantic dataclass ===
class LoggerConfig(BaseModel):
    """Configuration for the logger."""

    level: str = Field(default=DEFAULT_LOG_LEVEL, pattern=r"^(TRACE|DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    save_to_file: bool = False
    subfolder: str | None = None
    filename_modifier: str = ""
    filepath: Path | None = None


def configure_logger(config: LoggerConfig | None = None, **kwargs: Any) -> Any:
    """Configure and return the Loguru logger.

    Idempotent: calling this more than once resets and reconfigures handlers,
    but a module-level guard prevents accidental re-configuration across imports.

    Args:
        config: Logger configuration. If provided, kwargs are ignored.
        **kwargs: Keyword arguments for LoggerConfig (e.g., level, save_to_file).

    Returns:
        Configured Loguru logger instance.

    Raises:
        ValueError: If log file creation fails or configuration is invalid.
    """
    global _configured

    if _configured:
        logger.bind(name="logger").debug("Logger already configured — skipping reconfiguration.")
        return logger

    logger.remove()  # Reset existing handlers

    # Build config from kwargs if no config object provided
    if config is None:
        try:
            config = LoggerConfig(**kwargs)
        except ValidationError as e:
            raise ValueError(f"Invalid logger configuration: {e}") from e

    # Bind a default name so the format never breaks
    bound = logger.bind(name="root")

    # Console handler
    logger.add(sys.stdout, level=config.level, format=CONSOLE_FORMAT)

    # File handler
    if config.save_to_file:
        log_filepath = _resolve_log_filepath(
            subfolder=config.subfolder,
            filename_modifier=config.filename_modifier,
            force_filepath=config.filepath,
        )
        try:
            log_filepath.parent.mkdir(parents=True, exist_ok=True)
            logger.add(log_filepath, level=config.level, format=FILE_FORMAT)
            bound.debug(f"Logging to file: {log_filepath}")
        except OSError as e:
            logger.bind(name="logger").error(f"Failed to create log file: {e}")
            raise ValueError(f"Could not create log file: {e}") from e

    _configured = True
    return logger


def get_logger(name: str) -> Any:
    """Return a logger bound to a descriptive name (visible in log output).

    Args:
        name: Module or component name to tag log messages.

    Returns:
        Bound Loguru logger instance.
    """
    return logger.bind(name=name)


def _resolve_log_filepath(
    subfolder: str | None,
    filename_modifier: str,
    force_filepath: Path | None,
) -> Path:
    """Determine the log file path."""
    if force_filepath:
        return Path(force_filepath)

    timestamp = datetime.now(tz=UTC).strftime(FILENAME_TIMESTAMP_FORMAT)
    filename = f"{timestamp}{f'_{filename_modifier}' if filename_modifier else ''}.log"

    return LOG_DIR / (Path(subfolder) / filename if subfolder else filename)


# =============================================================================
# Example: usage inside any module
# =============================================================================
#
#   from etl_vectorize.core.logger import get_logger
#
#   log = get_logger(__name__)          # tags output with the module name
#
#   log.info("Job started")
#   log.debug("Batch size: 32")
#   log.error("Job failed")
