"""Tools module for platform-registry.

This module provides command-line tools and utilities:
- hello_world: Basic demonstration tool
- sync_logto_entitlements: Pull-sync of Logto Organization roles into local entitlements
"""

from __future__ import annotations

from .hello_world import main as hello_world
from .sync_logto_entitlements import main as sync_logto_entitlements

__all__ = [
    "hello_world",
    "sync_logto_entitlements",
]
