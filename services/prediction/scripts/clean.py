#!/usr/bin/env python3
"""
Cleanup script for prediction.
Author: ai-circus-framework contributors

NOTE: This script intentionally uses only stdlib — no project imports — so it
works even when .venv is absent or broken.
"""

from __future__ import annotations

import logging
import os
import shutil
from contextlib import suppress
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


BACKUP_DIR = Path("backups")
MAX_ENV_BACKUPS = 3


def backup_env() -> None:
    """Backup .env to backups/ folder, keeping only the most recent MAX_ENV_BACKUPS."""
    if os.path.exists(".env"):
        BACKUP_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f".env.bak_{timestamp}"
        shutil.copy(".env", backup_path)
        logger.info(f"📦 Backed up .env to {backup_path}")

        old_backups = sorted(BACKUP_DIR.glob(".env.bak_*"))
        for stale in old_backups[:-MAX_ENV_BACKUPS]:
            stale.unlink()


def clean_project() -> None:
    """Remove build artifacts and caches."""
    logger.info("🧹 Cleaning project...")

    # Paths to remove recursively
    dirs_to_remove = [
        ".venv",
        "build",
        "dist",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "*.egg-info",
        "htmlcov",
        ".coverage",
    ]

    # Patterns to remove
    file_patterns = [
        "*.pyc",
        ".coverage*",
        "coverage.xml",
        "coverage.json",
    ]

    # Recursive directory removal
    for root, dirs, files in os.walk(".", topdown=False):
        for name in dirs:
            dir_path = Path(root) / name
            if any(dir_path.match(p) for p in dirs_to_remove) or name == "__pycache__":
                with suppress(OSError):
                    shutil.rmtree(dir_path)

        for name in files:
            file_path = Path(root) / name
            if any(file_path.match(p) for p in file_patterns):
                with suppress(OSError):
                    file_path.unlink()

    logger.info("✓ clean complete")


if __name__ == "__main__":
    backup_env()
    clean_project()
