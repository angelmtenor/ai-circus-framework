"""
policy.py
---------

Which tools a caller may list and run, decided from the token's tenant and roles only. A file by
default (`policy.yaml` next to the server, generated from the catalog later); the same decision
could come from OPA or an external governance tool behind the same `allowed_tools` call.

    tenants: [lite-demo]          # optional: organization aliases allowed; omitted = any tenant
    roles:
      retail_analyst: [search_customers, get_customer]
      retail_store_manager: [search_customers]
      platform_admin: "*"

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from mcp_kit.identity import Identity

EVERY_TOOL = "*"


class PolicyError(ValueError):
    """The policy file is malformed."""


@dataclass(frozen=True)
class Policy:
    """Tools per realm role, optionally limited to some tenants. Unknown roles get nothing."""

    roles: Mapping[str, frozenset[str] | str] = field(default_factory=dict)
    tenants: frozenset[str] | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> Policy:
        """Validate and build a policy from its YAML mapping."""
        roles_raw = data.get("roles") or {}
        if not isinstance(roles_raw, Mapping):
            raise PolicyError("`roles` must map a role to a list of tools or '*'.")
        roles: dict[str, frozenset[str] | str] = {}
        for role, tools in roles_raw.items():
            if tools == EVERY_TOOL:
                roles[str(role)] = EVERY_TOOL
            elif isinstance(tools, list) and all(isinstance(t, str) for t in tools):
                roles[str(role)] = frozenset(tools)
            else:
                raise PolicyError(f"Role {role!r}: expected a list of tool names or '*'.")
        tenants_raw = data.get("tenants")
        if tenants_raw is not None and not (
            isinstance(tenants_raw, list) and all(isinstance(t, str) for t in tenants_raw)
        ):
            raise PolicyError("`tenants` must be a list of organization aliases.")
        return cls(roles=roles, tenants=frozenset(tenants_raw) if tenants_raw is not None else None)

    @classmethod
    def from_file(cls, path: Path) -> Policy:
        """Load `policy.yaml`."""
        return cls.from_mapping(yaml.safe_load(path.read_text()) or {})

    def allowed_tools(self, identity: Identity, all_tools: Iterable[str]) -> frozenset[str]:
        """The tools this caller may list and run; empty means the server stays hidden (403)."""
        if self.tenants is not None and identity.org_alias not in self.tenants:
            return frozenset()
        every = frozenset(all_tools)
        allowed: set[str] = set()
        for role in identity.roles:
            grant = self.roles.get(role)
            if grant == EVERY_TOOL:
                return every
            if isinstance(grant, frozenset):
                allowed |= grant & every
        return frozenset(allowed)
