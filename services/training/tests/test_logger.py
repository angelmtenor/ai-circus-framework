"""Tests for the logger module.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import pytest

from training.core.logger import LoggerConfig, configure_logger


class TestLoggerConfig:
    """Tests for LoggerConfig validation."""

    def test_valid_log_levels(self) -> None:
        """Test that valid log levels are accepted."""
        for level in ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            config = LoggerConfig(level=level)
            assert config.level == level

    def test_invalid_log_level(self) -> None:
        """Test that invalid log levels are rejected."""
        with pytest.raises(ValueError):
            LoggerConfig(level="INVALID")

    def test_config_defaults(self) -> None:
        """Test default configuration values."""
        config = LoggerConfig()
        assert config.level == "INFO"
        assert config.save_to_file is False
        assert config.subfolder is None
        assert config.filename_modifier == ""
        assert config.filepath is None


class TestConfigureLogger:
    """Tests for configure_logger function."""

    def test_configure_logger_kwargs(self) -> None:
        """Test configuring logger with keyword arguments."""
        logger = configure_logger(level="DEBUG")
        assert logger is not None

    def test_configure_logger_object(self) -> None:
        """Test configuring logger with LoggerConfig object."""
        config = LoggerConfig(level="ERROR")
        logger = configure_logger(config=config)
        assert logger is not None

    def test_configure_logger_with_valid_levels(self) -> None:
        """Test that valid log levels can be configured."""
        for level in ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            logger = configure_logger(level=level)
            assert logger is not None
