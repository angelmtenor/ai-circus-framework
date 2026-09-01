"""Tools module for platform-registry.

This module provides command-line tools and utilities:
- hello_world: Basic demonstration tool
- sync_keycloak_entitlements: Pull-sync of Keycloak realm-role assignments into local entitlements
"""

from __future__ import annotations

from .hello_world import main as hello_world
from .sync_keycloak_entitlements import main as sync_keycloak_entitlements

__all__ = [
    "hello_world",
    "sync_keycloak_entitlements",
]
