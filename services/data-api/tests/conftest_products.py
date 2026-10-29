"""The real surf_retail data product and access fixtures."""

from __future__ import annotations

from pathlib import Path

from ai_circus_shared.auth import Identity
from ai_circus_shared.data_policy import DataAccess, resolve_access

from data_api.core.products import DataProduct, load_products

SCENARIOS = Path(__file__).resolve().parents[3] / "scenarios"


def retail() -> DataProduct:
    return load_products(SCENARIOS, "surf_retail")["surf_retail"]


def access_as(role: str | None = None, *, values: tuple[str, ...] = (), org: str = "acme") -> DataAccess:
    identity = Identity(subject="admin", org_id=org, roles=frozenset())
    return resolve_access(
        identity, retail().config.access, data_org_id=org, is_platform=True, view_as=role, view_as_values=values
    )
