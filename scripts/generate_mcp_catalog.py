#!/usr/bin/env python3
"""Generate the MCP path's configuration from mcp/catalog.yaml, its single source of truth (docs/adr/0024).

One catalog entry per MCP server, one entry per group (a realm role in the user's token). The generator writes:

- llm-gateway's `mcp_servers` block (services/llm-gateway/litellm_config.yaml): one passthrough route per server;
- the chat UI's `TOOL_SERVER_CONNECTIONS` (k8s/chat-ui/open-webui.yaml and docker-compose.yml): one connection per
  server, each with the signed-in user's own token;
- each kit server's `policy.yaml` (services/<service>/policy.yaml): tools per group, optionally per tenant;
- the Keycloak entries that let a kit server exchange a user's token for its own API only
  (infra/keycloak/realm-export.json): the server's confidential client, its API's client and audience scope, and
  one audience mapper per server in the `mcp-servers` scope;
- the catalog itself, in its one canonical layout (so the portal and a hand edit produce the same file).

Hand-written files keep their own comments: a generated block sits between BEGIN/END marker comments. Run from the
repo root (PyYAML is the only dependency; `make mcp-catalog` runs it in libs/shared's environment):

    python3 scripts/generate_mcp_catalog.py           # write what changed
    python3 scripts/generate_mcp_catalog.py --check   # CI: exit 1 if any generated file drifted
    python3 scripts/generate_mcp_catalog.py --matrix  # print who may run what
    python3 scripts/generate_mcp_catalog.py --kit-services  # the kit servers' services (make k3s-mcp-catalog-apply)

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent
CATALOG = Path("mcp/catalog.yaml")
GATEWAY_CONFIG = Path("services/llm-gateway/litellm_config.yaml")
CHAT_UI_MANIFEST = Path("k8s/chat-ui/open-webui.yaml")
COMPOSE = Path("docker-compose.yml")
REALM = Path("infra/keycloak/realm-export.json")

BEGIN = "# BEGIN generated from mcp/catalog.yaml (scripts/generate_mcp_catalog.py): edit the catalog, not this block"
END = "# END generated from mcp/catalog.yaml"
# Keycloak entries the generator owns carry this in their description, so a server removed from the catalog
# loses its clients and scope too.
REALM_MARK = "Generated from mcp/catalog.yaml."
MCP_SERVERS_SCOPE = "mcp-servers"
KIT_PORT = 8000  # the service template's port; the kit serves MCP at /mcp/
EVERY_TOOL = "*"
SCENARIO_DOOR = "scenario_door"
KIT_SERVER = "kit_server"

NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")  # gateway path segment and tool prefix: never "-"
SLUG = re.compile(r"^[a-z][a-z0-9-]{0,39}$")  # a service, an API, a Keycloak client id
TENANT = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
HEADER = re.compile(r"^X-[A-Za-z0-9-]{1,60}$")
GATEWAY_URL = re.compile(r"^http://[a-z][a-z0-9-]{0,62}:[0-9]{2,5}$")
DOOR_URL = re.compile(r"^http://[a-z][a-z0-9-]{0,62}:[0-9]{2,5}/mcp/[a-z][a-z0-9_]{0,39}$")
CREDENTIAL_HEADERS = {"authorization", "cookie", "x-api-key"}
MAX_TEXT = 160

HEADER_COMMENT = """\
# The MCP catalog (docs/adr/0024): the single source of truth for the MCP path. It decides what the MCP
# gateway routes, what the chat UI connects to, which tools each group may run on each kit server, and the
# Keycloak clients that let a kit server exchange a user's token for its own API only.
#
# Edit it here or in the portal (make mcp-catalog-portal), then regenerate and commit both:
#     make mcp-catalog          # writes every generated file (this one too, in its canonical layout)
#     make mcp-catalog-check    # what CI runs: fails when a generated file drifted from the catalog
#
# gateway                  the MCP gateway's in-cluster address, as the chat UI reaches it
# servers.<name>           the gateway path (/mcp/<name>) and tool prefix: lowercase, digits, "_" (never "-")
#   kind: scenario_door    a scenario's own door (ADR 0019): url, title, description, optional forward_headers.
#                          The tenant's entitlement and the scenario's own role rules decide its tools, so no
#                          group lists it.
#   kind: kit_server       an MCP server in front of an API, built with libs/mcp-kit (ADR 0022): service
#                          (services/<service>, also its Keycloak client), api (the only audience it may
#                          obtain), title, description, optional tenants (organization aliases), optional
#                          level (2, the default: per-API exchange; 3: regulated, ADR 0025), tools.
# groups.<realm role>      tools per kit server, or "*" for all of them. A role that is not listed gets none.
"""


class CatalogError(ValueError):
    """The catalog is malformed; the message says where."""


@dataclass(frozen=True)
class Server:
    name: str
    kind: str
    title: str
    description: str
    url: str = ""
    forward_headers: tuple[str, ...] = ()
    service: str = ""
    api: str = ""
    tenants: tuple[str, ...] | None = None
    tools: tuple[str, ...] = ()
    level: int = 2

    @property
    def upstream(self) -> str:
        """Where the gateway sends this server's calls."""
        return self.url if self.kind == SCENARIO_DOOR else f"http://{self.service}:{KIT_PORT}/mcp/"


@dataclass(frozen=True)
class Catalog:
    gateway: str
    servers: tuple[Server, ...]
    groups: Mapping[str, Mapping[str, tuple[str, ...] | str]] = field(default_factory=dict)

    def kit_servers(self) -> list[Server]:
        return [s for s in self.servers if s.kind == KIT_SERVER]

    def grants(self, server: Server) -> dict[str, tuple[str, ...] | str]:
        """Each group's grant on one kit server, tools in the server's own order."""
        out: dict[str, tuple[str, ...] | str] = {}
        for group, grants in self.groups.items():
            grant = grants.get(server.name)
            if grant == EVERY_TOOL:
                out[group] = EVERY_TOOL
            elif grant:
                out[group] = tuple(t for t in server.tools if t in grant)
        return out


# ── loading and validation ──────────────────────────────────────────────────────────────────────────────


def _text(where: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise CatalogError(f"{where}: expected a one-line text of at most {MAX_TEXT} characters")
    # `$` would be expanded by docker compose and kubelet in the chat UI's environment.
    if "\n" in value or "$" in value or value != value.strip():
        raise CatalogError(f"{where}: no line breaks, no '$' and no surrounding spaces")
    return value


def _names(where: str, value: object, pattern: re.Pattern[str]) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and pattern.match(v) for v in value):
        raise CatalogError(f"{where}: expected a list of names matching {pattern.pattern}")
    if len(set(value)) != len(value):
        raise CatalogError(f"{where}: a name appears twice")
    return tuple(value)


def _only(where: str, entry: Mapping[str, object], required: set[str], optional: set[str] = frozenset()) -> None:
    missing = required - set(entry)
    unknown = set(entry) - required - optional
    if missing or unknown:
        raise CatalogError(f"{where}: missing {sorted(missing)}, unknown {sorted(unknown)}")


def discover_tools(service: str, repo: Path = REPO) -> list[str]:
    """The tools a kit server registers (`@kit.tool()`, `@kit.tool("name")`), read from its source, never run."""
    found: list[str] = []
    for path in sorted((repo / "services" / service / "src").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "tool"
                ):
                    named = [a.value for a in decorator.args[:1] if isinstance(a, ast.Constant)]
                    named += [
                        k.value.value
                        for k in decorator.keywords
                        if k.arg == "name" and isinstance(k.value, ast.Constant)
                    ]
                    name = named[0] if named and isinstance(named[0], str) else node.name
                    if name not in found:
                        found.append(name)
    return found


def _server(name: str, entry: object, repo: Path) -> Server:
    where = f"servers.{name}"
    if not NAME.match(name):
        raise CatalogError(f"{where}: a name is lowercase letters, digits and '_' (LiteLLM's tool prefix uses '-')")
    if not isinstance(entry, Mapping):
        raise CatalogError(f"{where}: expected a mapping")
    kind = entry.get("kind")
    common = {"kind", "title", "description"}
    if kind == SCENARIO_DOOR:
        _only(where, entry, common | {"url"}, {"forward_headers"})
        url = entry["url"]
        if not isinstance(url, str) or not DOOR_URL.match(url) or not url.endswith(f"/mcp/{name}"):
            raise CatalogError(f"{where}.url: expected http://<service>:<port>/mcp/{name} (an in-cluster door)")
        scenario_file = repo / "scenarios" / name / "scenario.yaml"
        scenario = yaml.safe_load(scenario_file.read_text()) if scenario_file.is_file() else None
        if not isinstance(scenario, Mapping) or not (scenario.get("mcp") or {}).get("expose"):
            raise CatalogError(f"{where}: scenarios/{name}/scenario.yaml declares no MCP door (mcp.expose)")
        headers = _names(f"{where}.forward_headers", entry.get("forward_headers", []), HEADER)
        if {h.lower() for h in headers} & CREDENTIAL_HEADERS:
            raise CatalogError(f"{where}.forward_headers: the gateway never forwards a credential by name")
        return Server(
            name=name,
            kind=kind,
            title=_text(f"{where}.title", entry["title"]),
            description=_text(f"{where}.description", entry["description"]),
            url=url,
            forward_headers=headers,
        )
    if kind == KIT_SERVER:
        _only(where, entry, common | {"service", "api", "tools"}, {"tenants", "level"})
        service, api = entry["service"], entry["api"]
        for key, value in (("service", service), ("api", api)):
            if not isinstance(value, str) or not SLUG.match(value):
                raise CatalogError(f"{where}.{key}: expected a lowercase service name")
        if service == api:
            raise CatalogError(f"{where}: the server and its API are two Keycloak clients")
        pyproject = repo / "services" / service / "pyproject.toml"
        if not pyproject.is_file() or '"mcp-kit"' not in pyproject.read_text():
            raise CatalogError(f"{where}.service: services/{service} is not an MCP server built with libs/mcp-kit")
        tools = _names(f"{where}.tools", entry["tools"], NAME)
        if not tools:
            raise CatalogError(f"{where}.tools: a server lists at least one tool")
        # A stale name would grant nothing and a missing one could never be granted. Discovery reads the
        # `@kit.tool()` decorators; a server that registers tools some other way is checked by its own tests.
        in_code = discover_tools(service, repo)
        if in_code and set(in_code) != set(tools):
            raise CatalogError(f"{where}.tools: services/{service} registers {in_code}, not {list(tools)}")
        tenants = entry.get("tenants")
        level = entry.get("level", 2)
        if level not in (2, 3) or isinstance(level, bool):
            raise CatalogError(f"{where}.level: 2 (per-API exchange, the default) or 3 (regulated, ADR 0025)")
        return Server(
            name=name,
            kind=kind,
            title=_text(f"{where}.title", entry["title"]),
            description=_text(f"{where}.description", entry["description"]),
            service=service,
            api=api,
            tenants=None if tenants is None else _names(f"{where}.tenants", tenants, TENANT),
            tools=tools,
            level=int(level),
        )
    raise CatalogError(f"{where}.kind: expected {SCENARIO_DOOR} or {KIT_SERVER}")


def build(data: object, repo: Path = REPO) -> Catalog:
    """Validate a catalog mapping (as read from YAML or sent by the portal) against the repository."""
    if not isinstance(data, Mapping):
        raise CatalogError("the catalog is a mapping with gateway, servers and groups")
    _only("catalog", data, {"gateway", "servers"}, {"groups"})
    gateway = data["gateway"]
    if not isinstance(gateway, str) or not GATEWAY_URL.match(gateway):
        raise CatalogError("gateway: expected http://<service>:<port>, the gateway's in-cluster address")
    raw_servers = data["servers"] or {}
    if not isinstance(raw_servers, Mapping):
        raise CatalogError("servers: expected a mapping of server name to entry")
    servers = tuple(_server(str(name), entry, repo) for name, entry in raw_servers.items())
    clients = [c for s in servers if s.kind == KIT_SERVER for c in (s.service, s.api)]
    if len(set(clients)) != len(clients):
        raise CatalogError("servers: two kit servers share a service or an API (one server, one API)")

    realm = json.loads((repo / REALM).read_text())
    realm_roles = {r["name"] for r in realm.get("roles", {}).get("realm", [])}
    handwritten = {c["clientId"] for c in realm["clients"]} - _managed_client_ids(realm)
    handwritten |= {s["name"] for s in realm["clientScopes"] if REALM_MARK not in s.get("description", "")}
    wanted = set(clients) | {f"{s.api}-audience" for s in servers if s.kind == KIT_SERVER}
    if clashing := handwritten & wanted:
        raise CatalogError(f"servers: {sorted(clashing)} would replace a hand-written Keycloak client or scope")

    by_name = {s.name: s for s in servers}
    raw_groups = data.get("groups") or {}
    if not isinstance(raw_groups, Mapping):
        raise CatalogError("groups: expected a mapping of realm role to grants")
    groups: dict[str, dict[str, tuple[str, ...] | str]] = {}
    for group, grants in raw_groups.items():
        where = f"groups.{group}"
        if not isinstance(group, str) or group not in realm_roles:
            raise CatalogError(f"{where}: not a realm role of infra/keycloak/realm-export.json")
        if not isinstance(grants, Mapping):
            raise CatalogError(f"{where}: expected a mapping of server name to tools")
        groups[group] = {}
        for name, tools in grants.items():
            server = by_name.get(name)
            if server is None:
                raise CatalogError(f"{where}.{name}: no such server")
            if server.kind != KIT_SERVER:
                raise CatalogError(
                    f"{where}.{name}: a scenario door's tools follow its entitlements and roles, not the catalog"
                )
            if tools == EVERY_TOOL:
                groups[group][name] = EVERY_TOOL
                continue
            granted = _names(f"{where}.{name}", tools, NAME)
            if unknown := set(granted) - set(server.tools):
                raise CatalogError(f"{where}.{name}: {sorted(unknown)} are not tools of {name}")
            if granted:
                groups[group][name] = granted
    return Catalog(gateway=gateway, servers=servers, groups=groups)


def load(repo: Path = REPO) -> Catalog:
    return build(yaml.safe_load((repo / CATALOG).read_text()), repo)


# ── rendering ───────────────────────────────────────────────────────────────────────────────────────────


def _q(value: str) -> str:
    """A YAML double-quoted scalar (a JSON string is one)."""
    return json.dumps(value, ensure_ascii=False)


def _flow(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(values) + "]"


def _grant(grant: tuple[str, ...] | str) -> str:
    return _q(EVERY_TOOL) if grant == EVERY_TOOL else _flow(grant)  # type: ignore[arg-type]


def render_catalog(catalog: Catalog) -> str:
    """The catalog in its canonical layout: what the portal writes and what a hand edit is checked against."""
    lines = [HEADER_COMMENT, f"gateway: {catalog.gateway}", "", "servers:"]
    for s in catalog.servers:
        lines += [f"  {s.name}:", f"    kind: {s.kind}"]
        if s.kind == SCENARIO_DOOR:
            lines.append(f"    url: {s.url}")
        else:
            lines += [f"    service: {s.service}", f"    api: {s.api}"]
        lines += [f"    title: {_q(s.title)}", f"    description: {_q(s.description)}"]
        if s.forward_headers:
            lines.append(f"    forward_headers: {_flow(s.forward_headers)}")
        if s.tenants is not None:
            lines.append(f"    tenants: {_flow(s.tenants)}")
        if s.level != 2:
            lines.append(f"    level: {s.level}")
        if s.tools:
            lines.append(f"    tools: {_flow(s.tools)}")
    lines += ["", "groups:" if catalog.groups else "groups: {}"]
    for group, grants in catalog.groups.items():
        lines.append(f"  {group}:" if grants else f"  {group}: {{}}")
        lines += [f"    {name}: {_grant(grant)}" for name, grant in grants.items()]
    return "\n".join(lines) + "\n"


def gateway_block(catalog: Catalog, indent: str) -> list[str]:
    """llm-gateway's `mcp_servers`: one passthrough route per server, no credential (docs/adr/0020)."""
    lines = ["mcp_servers:" if catalog.servers else "mcp_servers: {}"]
    for s in catalog.servers:
        lines += [
            f"  {s.name}:",
            f"    url: {s.upstream}",
            "    transport: http",
            "    auth_type: true_passthrough",
        ]
        if s.forward_headers:
            lines.append(f"    extra_headers: {_flow(s.forward_headers)}")
        lines.append(f"    description: {_q(s.description)}")
    return [indent + line for line in lines]


def chat_ui_connections(catalog: Catalog) -> list[dict[str, Any]]:
    """One chat UI connection per server, each sending the signed-in user's own token (docs/adr/0021).

    Visible to every signed-in user: Open WebUI grants a connection to a group by its internal id, which only
    exists after that group's first sign-in. Visibility is not the control; the door or the server's policy is.
    """
    return [
        {
            "type": "mcp",
            "url": f"{catalog.gateway}/mcp/{s.name}",
            "auth_type": "system_oauth",
            "key": "",
            "config": {
                "enable": True,
                "access_grants": [{"principal_type": "user", "principal_id": "*", "permission": "read"}],
            },
            "info": {"id": s.name, "name": s.title, "description": s.description},
        }
        for s in catalog.servers
    ]


def _connections_json(catalog: Catalog, indent: str) -> list[str]:
    """The connections as a folded block: one connection over three lines, as a person would write it."""
    connections = chat_ui_connections(catalog)
    if not connections:
        return [indent + "[]"]
    lines: list[str] = []
    for i, c in enumerate(connections):
        head = {k: c[k] for k in ("type", "url", "auth_type", "key")}
        first = "[" if i == 0 else " "
        last = "]" if i == len(connections) - 1 else ","
        lines += [
            f"{indent}{first}{json.dumps(head, ensure_ascii=False)[:-1]},",
            f'{indent}  "config": {json.dumps(c["config"], ensure_ascii=False)},',
            f'{indent}  "info": {json.dumps(c["info"], ensure_ascii=False)}}}{last}',
        ]
    return lines


def k8s_connections_block(catalog: Catalog, indent: str) -> list[str]:
    return [
        f"{indent}- name: TOOL_SERVER_CONNECTIONS",
        f"{indent}  value: >-",
        *_connections_json(catalog, indent + "    "),
    ]


def compose_connections_block(catalog: Catalog, indent: str) -> list[str]:
    return [f"{indent}TOOL_SERVER_CONNECTIONS: >-", *_connections_json(catalog, indent + "  ")]


def policy_file(catalog: Catalog, server: Server) -> str:
    """A kit server's policy.yaml (libs/mcp-kit's Policy): tools per realm role, optionally per tenant."""
    lines = [
        f"# {server.service}'s access policy (docs/adr/0022): which tools each tenant and realm role may list and run.",
        f"# The token decides tenant and roles; {server.api} still applies its own rules on every call.",
        "# Generated from mcp/catalog.yaml by scripts/generate_mcp_catalog.py: edit the catalog, not this file.",
    ]
    if server.tenants is not None:
        lines.append(f"tenants: {_flow(server.tenants)}")
    if server.level != 2:
        lines.append(f"level: {server.level}  # DPoP-bound per-API token, mutual TLS, live token check (ADR 0025)")
    grants = catalog.grants(server)
    lines.append("roles:" if grants else "roles: {}")
    lines += [f"  {group}: {_grant(grant)}" for group, grant in grants.items()]
    return "\n".join(lines) + "\n"


def client_secret_env(client_id: str) -> str:
    """The .env key holding a kit server's Keycloak client secret (scripts/chat_ui_identity.py generates it)."""
    return client_id.upper().replace("-", "_") + "_CLIENT_SECRET"


def _flag(on: bool) -> str:
    """Keycloak merges attribute updates: a flag is always written, "true" or "false", never left out."""
    return "true" if on else "false"


def _audience_mapper(name: str, client_id: str, *, lightweight: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "protocol": "openid-connect",
        "protocolMapper": "oidc-audience-mapper",
        "consentRequired": False,
        "config": {
            "included.client.audience": client_id,
            "access.token.claim": "true",
            "id.token.claim": "false",
            "introspection.token.claim": "true",
            # A lightweight per-API token (level 3) keeps only claims flagged for it: its audience must be one.
            "lightweight.claim": _flag(lightweight),
        },
    }


def _scope(name: str, description: str, mappers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "protocol": "openid-connect",
        "attributes": {"include.in.token.scope": "false", "display.on.consent.screen": "false"},
        "protocolMappers": mappers,
    }


def _client(
    client_id: str, name: str, description: str, *, exchanges_for: str | None, level: int = 2
) -> dict[str, Any]:
    return {
        "clientId": client_id,
        "name": name,
        "description": description,
        "enabled": True,
        "publicClient": False,
        "clientAuthenticatorType": "client-secret",
        "protocol": "openid-connect",
        "standardFlowEnabled": False,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        # Level 3 (ADR 0025): the per-API token must be bound to the server's key (DPoP) and carry ids only;
        # the API reads roles and row filters from introspection.
        "attributes": {
            "standard.token.exchange.enabled": "true",
            "dpop.bound.access.tokens": _flag(level >= 3),
            "client.use.lightweight.access.token.enabled": _flag(level >= 3),
        }
        if exchanges_for
        else {},
        # The token's subject, tenant, roles and row-filter claims, and exactly one audience: its API's.
        "defaultClientScopes": ["basic", "organization", "roles", "data-access", f"{exchanges_for}-audience"]
        if exchanges_for
        else [],
        "optionalClientScopes": [],
    }


def _managed_client_ids(realm: Mapping[str, Any]) -> set[str]:
    return {c["clientId"] for c in realm["clients"] if REALM_MARK in c.get("description", "")}


def realm_file(catalog: Catalog, current: str) -> str:
    """realm-export.json with the catalog's Keycloak entries in place; every other entry is left as it is."""
    realm = json.loads(current)
    kits = catalog.kit_servers()
    servers_scope = _scope(
        MCP_SERVERS_SCOPE,
        "Puts every MCP server client in the user token's audience (T1, docs/adr/0022): a server can only exchange "
        f"a token it is an audience of. One audience mapper per kit server. {REALM_MARK}",
        [_audience_mapper(f"{s.service} audience", s.service) for s in kits],
    )
    api_scopes = [
        _scope(
            f"{s.api}-audience",
            f"The only audience {s.service} may obtain in a token exchange (T2 for {s.api}, docs/adr/0022). "
            + REALM_MARK,
            [_audience_mapper(f"{s.api} audience", s.api, lightweight=s.level >= 3)],
        )
        for s in kits
    ]
    clients = []
    for s in kits:
        clients.append(
            _client(
                s.service,
                f"{s.service}: MCP server for {s.title}",
                f"MCP server built with libs/mcp-kit (docs/adr/0022). Confidential; exchanges a user's token for one "
                f"valid only at {s.api}. Its secret is {client_secret_env(s.service)} in .env, never in this file. "
                f"{REALM_MARK}",
                exchanges_for=s.api,
                level=s.level,
            )
        )
        clients.append(
            _client(
                s.api,
                f"{s.api}: the API behind {s.service}",
                f"Audience of the T2 tokens {s.service} obtains (docs/adr/0022). Signs no one in; its secret "
                f"({client_secret_env(s.api)} in .env) only introspects its own tokens at level 3. {REALM_MARK}",
                exchanges_for=None,
            )
        )

    managed_scopes = {MCP_SERVERS_SCOPE} | {
        s["name"] for s in realm["clientScopes"] if REALM_MARK in s.get("description", "")
    }
    managed_scopes |= {scope["name"] for scope in api_scopes}
    kept = [s for s in realm["clientScopes"] if s["name"] not in managed_scopes]
    names = [s["name"] for s in realm["clientScopes"]]
    at = names.index(MCP_SERVERS_SCOPE) if MCP_SERVERS_SCOPE in names else len(names)
    at = len([s for s in realm["clientScopes"][:at] if s["name"] not in managed_scopes])
    realm["clientScopes"] = [*kept[:at], servers_scope, *api_scopes, *kept[at:]]

    managed_clients = _managed_client_ids(realm) | {c["clientId"] for c in clients}
    realm["clients"] = [c for c in realm["clients"] if c["clientId"] not in managed_clients] + clients
    return json.dumps(realm, indent=2, ensure_ascii=False) + "\n"


def splice(text: str, path: Path, render: Any) -> str:
    """Replace the lines between the BEGIN/END markers, rendered at the BEGIN marker's indentation."""
    lines = text.split("\n")
    starts = [i for i, line in enumerate(lines) if line.strip() == BEGIN]
    ends = [i for i, line in enumerate(lines) if line.strip() == END]
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise CatalogError(f"{path}: expected one BEGIN/END pair of generated-block markers")
    start, end = starts[0], ends[0]
    indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
    return "\n".join([*lines[: start + 1], *render(indent), *lines[end:]])


def outputs(catalog: Catalog, repo: Path = REPO) -> dict[Path, str]:
    """Every file the catalog decides, with its full expected content (paths relative to the repo)."""
    files = {
        CATALOG: render_catalog(catalog),
        GATEWAY_CONFIG: splice(
            (repo / GATEWAY_CONFIG).read_text(), GATEWAY_CONFIG, lambda i: gateway_block(catalog, i)
        ),
        CHAT_UI_MANIFEST: splice(
            (repo / CHAT_UI_MANIFEST).read_text(), CHAT_UI_MANIFEST, lambda i: k8s_connections_block(catalog, i)
        ),
        COMPOSE: splice((repo / COMPOSE).read_text(), COMPOSE, lambda i: compose_connections_block(catalog, i)),
        REALM: realm_file(catalog, (repo / REALM).read_text()),
    }
    for server in catalog.kit_servers():
        files[Path("services") / server.service / "policy.yaml"] = policy_file(catalog, server)
    return files


def stale(files: Mapping[Path, str], repo: Path = REPO) -> list[Path]:
    """The files whose content differs from what the catalog generates."""
    return [path for path, text in files.items() if not (repo / path).is_file() or (repo / path).read_text() != text]


def matrix(catalog: Catalog) -> str:
    """Who may run what, as plain text."""
    out = [f"MCP gateway: {catalog.gateway}/mcp/<server>", ""]
    for s in catalog.servers:
        if s.kind == SCENARIO_DOOR:
            out.append(f"{s.name:<22} scenario door → {s.url}  (tools: tenant entitlement + the scenario's roles)")
        else:
            tenants = ", ".join(s.tenants) if s.tenants is not None else "any tenant"
            out.append(f"{s.name:<22} kit server {s.service} → API {s.api}  ({tenants}, level {s.level})")
            grants = catalog.grants(s)
            for group, grant in grants.items():
                tools = ", ".join(s.tools) if grant == EVERY_TOOL else ", ".join(grant)  # type: ignore[arg-type]
                out.append(f"{'':<24}{group:<24} {tools}")
            if not grants:
                out.append(f"{'':<24}(no group may run a tool)")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the MCP path's configuration from mcp/catalog.yaml.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="exit 1 if a generated file drifted (CI)")
    mode.add_argument("--matrix", action="store_true", help="print which group may run which tools")
    mode.add_argument("--kit-services", action="store_true", help="print the kit servers' service names")
    mode.add_argument("--kit-apis", action="store_true", help="print the APIs behind the kit servers")
    args = parser.parse_args()
    try:
        catalog = load()
        files = outputs(catalog)
    except (CatalogError, OSError, yaml.YAMLError) as exc:
        print(f"❌ mcp/catalog.yaml: {exc}", file=sys.stderr)
        return 2
    if args.matrix:
        print(matrix(catalog))
        return 0
    if args.kit_services:
        print(" ".join(s.service for s in catalog.kit_servers()))
        return 0
    if args.kit_apis:
        print(" ".join(s.api for s in catalog.kit_servers()))
        return 0
    changed = stale(files)
    if args.check:
        for path in changed:
            why = "is not in the catalog's canonical layout" if path == CATALOG else "differs from mcp/catalog.yaml"
            print(f"❌ {path} {why}: run `make mcp-catalog`", file=sys.stderr)
        if not changed:
            print(f"✓ {len(files)} files match mcp/catalog.yaml")
        return 1 if changed else 0
    for path in changed:
        (REPO / path).write_text(files[path])
        print(f"✓ wrote {path}")
    if not changed:
        print(f"✓ {len(files)} files already match mcp/catalog.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
