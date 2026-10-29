"""
- Title:    Data product access policy — role, row filter and column masks of one caller
- Author:   Angel Martinez-Tenor

Tenancy is not decided here: `org_id` comes from the entitlement-checked identity and is the
first predicate of every query (docs/adr/0008). *Inside* a tenant, a data product's `access`
block decides what a caller sees:

- the **role**: the first of the data product's roles the caller's token holds (YAML order),
  else `default_role`; the platform's own API-key identities act as `admin_role`, and may
  "view as" any other role — the demo of the restrictions, without a second login;
- the **row filter**: a `rows: filtered` role only sees rows whose filter column is among the
  values of its token claim (e.g. `store_id in token.stores`); no claim means no rows;
- the **masks**: `hidden` fields never leave the API, at any depth of a payload.

data-api applies the result to every door (REST, SQL, MCP, the assistant's tools), so the
rules cannot drift apart between them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_circus_shared.auth import Identity
from ai_circus_shared.data_product import AccessConfig, RoleAccess


class AccessDeniedError(Exception):
    """The caller's role may not use this layer, dataset or operation."""


@dataclass(frozen=True)
class DataAccess:
    org_id: str  # whose data is read: the caller's own, or the fallback org's
    caller_org_id: str
    role: str
    access: RoleAccess
    row_filter_column: str | None
    row_filter_values: tuple[str, ...] | None  # None = every row
    viewing_as: bool
    is_platform: bool

    @property
    def hidden(self) -> frozenset[str]:
        return frozenset(self.access.hidden)

    @property
    def can_simulate(self) -> bool:
        """Writing (simulator actions) is for the platform identity, or a tenant on its own data."""
        return not self.viewing_as and (self.is_platform or self.org_id == self.caller_org_id)

    def require_layer(self, layer: str) -> None:
        if layer not in self.access.layers:
            raise AccessDeniedError(f"Role {self.role!r} has no access to the {layer} layer.")

    def require_sql(self) -> None:
        if not self.access.sql or self.row_filter_values is not None:
            raise AccessDeniedError(f"Role {self.role!r} may not run SQL.")

    def require_columns(self, columns: set[str] | list[str]) -> None:
        """A filtered role can only read a dataset that carries its filter column — anything
        else could not be restricted to its rows."""
        if self.row_filter_values is not None and self.row_filter_column not in columns:
            raise AccessDeniedError(
                f"Role {self.role!r} only sees rows by {self.row_filter_column}; this dataset has no such column."
            )

    def mask(self, value: Any) -> Any:
        """Drop every hidden key, at any depth."""
        hidden = self.hidden
        if not hidden:
            return value
        if isinstance(value, dict):
            return {k: self.mask(v) for k, v in value.items() if k not in hidden}
        if isinstance(value, list):
            return [self.mask(v) for v in value]
        return value

    def describe(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "role": self.role,
            "role_label": self.access.label,
            "layers": list(self.access.layers),
            "sql": self.access.sql and self.row_filter_values is None,
            "row_filter": None
            if self.row_filter_values is None
            else {"column": self.row_filter_column, "values": list(self.row_filter_values)},
            "hidden": sorted(self.hidden),
            "viewing_as": self.viewing_as,
            "can_simulate": self.can_simulate,
            "fallback_data": self.org_id != self.caller_org_id,
        }


def resolve_access(
    identity: Identity,
    config: AccessConfig,
    *,
    data_org_id: str,
    is_platform: bool,
    view_as: str | None = None,
    view_as_values: tuple[str, ...] = (),
) -> DataAccess:
    """The caller's effective access to one data product.

    Args:
        identity: The entitlement-checked caller.
        config: The data product's `access` block.
        data_org_id: Whose data this request reads (caller's org, or the fallback org).
        is_platform: True for the platform's API-key identities (admin, scoped demo tenants).
        view_as: A role to impersonate — honoured for platform identities only.
        view_as_values: The row-filter values to impersonate with (e.g. ("S012",)).
    """
    assert identity.org_id is not None  # resolve_caller_identity guarantees it
    viewing_as = False
    if is_platform:
        role = config.admin_role
        if view_as:
            if view_as not in config.roles:
                raise AccessDeniedError(f"Unknown role {view_as!r}.")
            role, viewing_as = view_as, view_as != config.admin_role
    else:
        role = next((name for name in config.roles if identity.has_role(name)), config.default_role)
    access = config.roles[role]
    values: tuple[str, ...] | None = None
    column = config.row_filter.column if config.row_filter else None
    if access.rows == "filtered" and config.row_filter is not None:
        raw = view_as_values if viewing_as else identity.attribute(config.row_filter.claim)
        values = tuple(sorted({v.strip() for v in raw if v and v.strip()}))[:100]
    return DataAccess(
        org_id=data_org_id,
        caller_org_id=identity.org_id,
        role=role,
        access=access,
        row_filter_column=column,
        row_filter_values=values,
        viewing_as=viewing_as,
        is_platform=is_platform,
    )
