"""
- Title:    Caller identity → data product access
- Author:   Angel Martinez-Tenor

Entitlement first (`ai_circus_shared.auth.resolve_caller_identity`, 401/403), then the data
product's policy (`ai_circus_shared.data_policy`): role, row filter, masks — and *whose* data:
the caller's own org when it has any, else `DATA_FALLBACK_ORG_ID` (the same "shared until you
have your own" rule tabular models follow).
"""

from __future__ import annotations

import time

from ai_circus_shared.auth import (
    AuthSettingsAdapter,
    Identity,
    TokenValidationError,
    is_admin_bearer_token,
    resolve_caller_identity,
)
from ai_circus_shared.data_policy import AccessDeniedError, DataAccess, resolve_access
from ai_circus_shared.entitlements import EntitlementDeniedError
from fastapi import Depends, Header, HTTPException, Request

from data_api import get_env_config
from data_api.core.products import DataProduct
from data_api.core.warehouse import Warehouse

_ORG_HAS_DATA_TTL = 30.0
_org_has_data: dict[tuple[str, str], tuple[float, bool]] = {}


def resolve_identity(scenario_slug: str, authorization: str | None = Header(default=None)) -> Identity:
    """FastAPI dependency: the entitlement-checked caller for `scenario_slug` (401/403)."""
    settings = AuthSettingsAdapter.from_config(get_env_config())
    try:
        return resolve_caller_identity(authorization=authorization, scenario_slug=scenario_slug, settings=settings)
    except TokenValidationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except EntitlementDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def is_platform_caller(authorization: str | None) -> bool:
    """Whether the bearer is one of the platform's API keys (or auth is disabled for dev)."""
    config = get_env_config()
    keys = [config.ADMIN_API_KEY, config.ENGINEERING_DEMO_API_KEY]
    return config.AUTH_DISABLED.lower() == "true" or any(
        key is not None and is_admin_bearer_token(authorization, key.get_secret_value()) for key in keys
    )


def get_product(request: Request, scenario_slug: str) -> DataProduct:
    """FastAPI dependency: the data product named by the path, or 404."""
    product = request.app.state.runtime.products.get(scenario_slug)
    if product is None:
        raise HTTPException(status_code=404, detail=f"{scenario_slug!r} is not a data product served here.")
    return product


def data_org(warehouse: Warehouse, product: DataProduct, org_id: str) -> str:
    """The caller's own org once it has data in this data product, else the fallback org."""
    fallback = get_env_config().DATA_FALLBACK_ORG_ID
    if org_id == fallback:
        return org_id
    key = (product.slug, org_id)
    cached = _org_has_data.get(key)
    if cached is None or time.monotonic() - cached[0] > _ORG_HAS_DATA_TTL:
        cached = (time.monotonic(), warehouse.org_has_data(product, org_id))
        if len(_org_has_data) > 10_000:
            _org_has_data.clear()
        _org_has_data[key] = cached
    return org_id if cached[1] else fallback


def get_access(
    request: Request,
    product: DataProduct = Depends(get_product),
    identity: Identity = Depends(resolve_identity),
    authorization: str | None = Header(default=None),
    x_data_view_as: str | None = Header(default=None, max_length=64),
    x_data_view_as_values: str | None = Header(default=None, max_length=400),
) -> DataAccess:
    """FastAPI dependency: the caller's effective access to this data product."""
    assert identity.org_id is not None
    warehouse: Warehouse = request.app.state.runtime.warehouse
    try:
        return resolve_access(
            identity,
            product.config.access,
            data_org_id=data_org(warehouse, product, identity.org_id),
            is_platform=is_platform_caller(authorization),
            view_as=x_data_view_as or None,
            view_as_values=tuple((x_data_view_as_values or "").split(",")),
        )
    except AccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
