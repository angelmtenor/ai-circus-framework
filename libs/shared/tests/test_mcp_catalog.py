"""
test_mcp_catalog.py
-------------------

mcp/catalog.yaml is the single source of truth for the MCP path (docs/adr/0024): the gateway's routes, the chat
UI's connections, each kit server's policy and the Keycloak exchange clients are generated from it. These tests
fail CI when a generated file drifted from the catalog (regenerate: `make mcp-catalog`), and pin what the
generator refuses: the mistakes that would route, grant or exchange something the catalog does not say.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


generator = _script("generate_mcp_catalog")
RAW = yaml.safe_load((REPO / "mcp" / "catalog.yaml").read_text())


def raw() -> dict[str, Any]:
    """A fresh copy of the committed catalog's mapping, to break in one place."""
    return copy.deepcopy(RAW)


def test_every_generated_file_matches_the_catalog() -> None:
    """Regenerate with: make mcp-catalog (or python3 scripts/generate_mcp_catalog.py)."""
    stale = generator.stale(generator.outputs(generator.load()))
    assert not stale, f"{[str(p) for p in stale]} drifted from mcp/catalog.yaml: run `make mcp-catalog`"


def test_the_canonical_layout_reads_back_as_the_same_catalog() -> None:
    catalog = generator.load()
    assert generator.build(yaml.safe_load(generator.render_catalog(catalog))) == catalog


def test_the_gateway_routes_every_server_and_nothing_else() -> None:
    catalog = generator.load()
    config = yaml.safe_load((REPO / "services" / "llm-gateway" / "litellm_config.yaml").read_text())
    assert list(config["mcp_servers"]) == [s.name for s in catalog.servers]
    for server in catalog.servers:
        assert config["mcp_servers"][server.name]["url"] == server.upstream


def test_librechat_reaches_every_server_through_the_gateway_with_the_users_own_token() -> None:
    """docs/adr/0026: one LibreChat MCP server per catalog server, each through the gateway with the signed-in user's
    own Keycloak token in its own Authorization header, and the gateway as the only private address it may reach."""
    catalog = generator.load()
    config = yaml.safe_load((REPO / generator.LIBRECHAT_CONFIG).read_text())
    assert list(config["mcpServers"]) == [s.name for s in catalog.servers]
    for server in catalog.servers:
        entry = config["mcpServers"][server.name]
        assert entry["type"] == "streamable-http"
        assert entry["url"] == f"{catalog.gateway}/mcp/{server.name}"
        assert entry["headers"] == {"Authorization": "Bearer {{LIBRECHAT_OPENID_ACCESS_TOKEN}}"}
        assert entry["requiresOAuth"] is False
        assert "obo" not in entry and "apiKey" not in entry  # no LibreChat-side exchange or shared key
    assert config["mcpSettings"] == {"allowedAddresses": [catalog.gateway.removeprefix("http://")]}


def test_the_lite_profile_lists_only_the_kit_servers_it_deploys() -> None:
    """docs/adr/0027: the MCP lite profile runs no scenario door, so its chat UIs list the kit servers only, each
    exactly as the full profile lists it; LibreChat's file is otherwise the full profile's."""
    catalog = generator.load()
    kits = [s.name for s in catalog.kit_servers()]
    assert kits, "the catalog has no kit server for the lite profile"
    patch = yaml.safe_load((REPO / generator.LITE_OPEN_WEBUI_PATCH).read_text())
    (container,) = patch["spec"]["template"]["spec"]["containers"]
    (variable,) = container["env"]
    lite = json.loads(variable["value"])
    assert [c["info"]["id"] for c in lite] == kits
    assert lite == [c for c in generator.chat_ui_connections(catalog) if c["info"]["id"] in kits]

    full = yaml.safe_load((REPO / generator.LIBRECHAT_CONFIG).read_text())
    lite_config = yaml.safe_load((REPO / generator.LITE_LIBRECHAT_CONFIG).read_text())
    assert list(lite_config["mcpServers"]) == kits
    assert lite_config["mcpServers"] == {name: full["mcpServers"][name] for name in kits}
    assert {k: v for k, v in lite_config.items() if k != "mcpServers"} == {
        k: v for k, v in full.items() if k != "mcpServers"
    }


def _librechat_accepts(title: str) -> bool:
    """LibreChat's MCP_SERVER_TITLE_PATTERN: a letter or digit first, then letters, digits, marks, apostrophes
    (straight or typographic), spaces and hyphens."""
    return bool(title) and title[0].isalnum() and all(c.isalnum() or c in "'\u2019 -" for c in title)


@pytest.mark.parametrize(
    ("title", "shown"),
    [
        ("Customers (CRM)", "Customers CRM"),
        ("Angel's Surf Co.", "Angel's Surf Co"),
        ("AI Circus reference notes", "AI Circus reference notes"),
        ("(beta) Ledger: v2", "beta Ledger v2"),
        ("...", "fallback_name"),
    ],
)
def test_librechat_titles_keep_the_words_and_drop_what_it_refuses(title: str, shown: str) -> None:
    assert generator.librechat_title(title, "fallback_name") == shown
    assert _librechat_accepts(shown) or shown == "fallback_name"


def test_every_gateway_route_forwards_the_callers_trace_context_and_nothing_spoofable() -> None:
    """docs/adr/0028: LiteLLM injects no trace context into MCP calls, so each route forwards the caller's W3C trace
    headers (one trace from the chat UI through the server). Never baggage, which a client could use to spoof."""
    config = yaml.safe_load((REPO / "services" / "llm-gateway" / "litellm_config.yaml").read_text())
    for name, entry in config["mcp_servers"].items():
        forwarded = [h.lower() for h in entry["extra_headers"]]
        assert {"traceparent", "tracestate"} <= set(forwarded), name
        assert "baggage" not in forwarded, name


def test_a_kit_server_policy_holds_exactly_its_groups_grants() -> None:
    catalog = generator.load()
    for server in catalog.kit_servers():
        policy = yaml.safe_load((REPO / "services" / server.service / "policy.yaml").read_text())
        assert policy["roles"] == {
            group: grant if grant == "*" else list(grant) for group, grant in catalog.grants(server).items()
        }
        assert policy.get("tenants") == (list(server.tenants) if server.tenants is not None else None)


def test_the_identity_script_finds_each_kit_servers_secret_by_the_same_name() -> None:
    identity = _script("chat_ui_identity")
    realm = json.loads((REPO / "infra" / "keycloak" / "realm-export.json").read_text())
    secrets = identity.client_secrets(realm)
    for server in generator.load().kit_servers():
        assert secrets[server.service] == generator.client_secret_env(server.service)
    assert generator.client_secret_env("crm-mcp") == "CRM_MCP_CLIENT_SECRET"  # the existing .env key


def test_the_identity_script_generates_librechats_keys_at_the_lengths_it_needs(tmp_path: Path) -> None:
    """LibreChat refuses to start on a credential key that is not 32 bytes or an IV that is not 16 (docs/adr/0026)."""
    identity = _script("chat_ui_identity")
    realm = json.loads((REPO / "infra" / "keycloak" / "realm-export.json").read_text())
    env_file = tmp_path / ".env"
    env_file.write_text("LIBRECHAT_CREDS_IV=\nOTHER=kept\n")
    env = identity.ensure_generated_secrets(env_file, realm)
    assert identity.client_secrets(realm)["librechat"] == "LIBRECHAT_OIDC_CLIENT_SECRET"
    assert len(bytes.fromhex(env["LIBRECHAT_CREDS_KEY"])) == 32
    assert len(bytes.fromhex(env["LIBRECHAT_CREDS_IV"])) == 16  # an empty value is filled too
    assert len(bytes.fromhex(env["LIBRECHAT_JWT_SECRET"])) >= 32
    assert len(bytes.fromhex(env["LIBRECHAT_JWT_REFRESH_SECRET"])) >= 32
    assert env["OTHER"] == "kept"
    keys = [line.partition("=")[0] for line in env_file.read_text().splitlines() if "=" in line]
    assert len(keys) == len(set(keys)), "a key twice makes kubectl refuse the whole Secret"
    assert identity.ensure_generated_secrets(env_file, realm) == env  # idempotent


def test_removing_a_kit_server_removes_its_keycloak_entries_only() -> None:
    data = raw()
    kits = [name for name, entry in data["servers"].items() if entry["kind"] == "kit_server"]
    for name in kits:
        del data["servers"][name]
    for grants in data["groups"].values():
        for name in kits:
            grants.pop(name, None)
    current = (REPO / "infra" / "keycloak" / "realm-export.json").read_text()
    before, after = json.loads(current), json.loads(generator.realm_file(generator.build(data), current))
    managed = {c["clientId"] for c in before["clients"] if generator.REALM_MARK in c.get("description", "")}
    assert {c["clientId"] for c in after["clients"]} == {c["clientId"] for c in before["clients"]} - managed
    (servers_scope,) = [s for s in after["clientScopes"] if s["name"] == "mcp-servers"]
    assert servers_scope["protocolMappers"] == []
    assert not any(s["name"].endswith("-audience") for s in after["clientScopes"])
    assert [s for s in after["clientScopes"] if generator.REALM_MARK not in s.get("description", "")] == [
        s for s in before["clientScopes"] if generator.REALM_MARK not in s.get("description", "")
    ]


def _kit(data: dict[str, Any]) -> dict[str, Any]:
    (entry,) = [e for e in data["servers"].values() if e["kind"] == "kit_server"]
    return entry


def _door(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return next((n, e) for n, e in data["servers"].items() if e["kind"] == "scenario_door")


def _rename_first_server(data: dict[str, Any], new: str) -> None:
    name, entry = next(iter(data["servers"].items()))
    data["servers"] = {new: entry, **{k: v for k, v in data["servers"].items() if k != name}}


def _break(data: dict[str, Any], how: str) -> None:
    if how == "dash_in_name":
        _rename_first_server(data, "surf-retail")
    elif how == "unknown_key":
        _kit(data)["token"] = "x"
    elif how == "dollar_in_text":
        _kit(data)["description"] = "Costs $(HOME)"
    elif how == "placeholder_in_text":
        _kit(data)["title"] = "Hello {{LIBRECHAT_OPENID_ACCESS_TOKEN}}"
    elif how == "door_without_scenario":
        name, entry = _door(data)
        data["servers"]["no_such_scenario"] = {**entry, "url": entry["url"].replace(name, "no_such_scenario")}
    elif how == "door_url_not_its_slug":
        name, entry = _door(data)
        entry["url"] = entry["url"].replace(f"/mcp/{name}", "/mcp/other")
    elif how == "credential_header":
        _door(data)[1]["forward_headers"] = ["X-Api-Key"]
    elif how == "service_without_kit":
        _kit(data)["service"] = "rag-agent"
    elif how == "api_is_a_sign_in_client":
        _kit(data)["api"] = "ui-react"
    elif how == "group_not_a_realm_role":
        data["groups"]["visitor"] = {}
    elif how == "group_grants_a_door":
        data["groups"][next(iter(data["groups"]))][_door(data)[0]] = "*"
    elif how == "tool_not_in_the_code":
        _kit(data)["tools"] = [*_kit(data)["tools"], "drop_tables"]
    elif how == "unknown_tool":
        group = next(iter(data["groups"]))
        name = next(iter(data["groups"][group]))
        data["groups"][group][name] = ["drop_tables"]


@pytest.mark.parametrize(
    "how",
    [
        "dash_in_name",
        "unknown_key",
        "dollar_in_text",
        "placeholder_in_text",
        "door_without_scenario",
        "door_url_not_its_slug",
        "credential_header",
        "service_without_kit",
        "api_is_a_sign_in_client",
        "group_not_a_realm_role",
        "group_grants_a_door",
        "tool_not_in_the_code",
        "unknown_tool",
    ],
)
def test_the_generator_refuses_a_catalog_that_would_route_or_grant_by_mistake(how: str) -> None:
    data = raw()
    _break(data, how)
    with pytest.raises(generator.CatalogError):
        generator.build(data)


def test_every_tool_grant_renders_as_a_wildcard_policy() -> None:
    data = raw()
    group = next(iter(data["groups"]))
    name = next(iter(data["groups"][group]))
    data["groups"][group][name] = "*"
    catalog = generator.build(data)
    (server,) = catalog.kit_servers()
    assert yaml.safe_load(generator.policy_file(catalog, server))["roles"][group] == "*"


def _realm_after(data: dict[str, Any]) -> dict[str, Any]:
    current = (REPO / "infra" / "keycloak" / "realm-export.json").read_text()
    return json.loads(generator.realm_file(generator.build(data), current))


@pytest.mark.parametrize("level", [2, 3])
def test_the_level_reaches_keycloak_as_explicit_flags(level: int) -> None:
    # Keycloak merges attribute updates: a flag left out would stay on in a live realm (docs/adr/0025).
    data = raw()
    _kit(data)["level"] = level
    realm = _realm_after(data)
    kit = _kit(data)
    (client,) = [c for c in realm["clients"] if c["clientId"] == kit["service"]]
    expected = "true" if level == 3 else "false"
    assert client["attributes"]["dpop.bound.access.tokens"] == expected
    assert client["attributes"]["client.use.lightweight.access.token.enabled"] == expected
    (scope,) = [s for s in realm["clientScopes"] if s["name"] == f"{kit['api']}-audience"]
    assert scope["protocolMappers"][0]["config"]["lightweight.claim"] == expected
    policy = yaml.safe_load(generator.policy_file(generator.build(data), generator.build(data).kit_servers()[0]))
    assert policy.get("level", 2) == level


def test_a_level_other_than_2_or_3_is_refused() -> None:
    for bad in (1, 4, "3", True):
        data = raw()
        _kit(data)["level"] = bad
        with pytest.raises(generator.CatalogError):
            generator.build(data)


def test_a_lightweight_per_api_token_keeps_its_tenant() -> None:
    # Level 3 per-API tokens carry ids only; the tenant is one of them (the API reads roles from introspection).
    realm = json.loads((REPO / "infra" / "keycloak" / "realm-export.json").read_text())
    (scope,) = [s for s in realm["clientScopes"] if s["name"] == "organization"]
    assert all(m["config"].get("lightweight.claim") == "true" for m in scope["protocolMappers"])


def test_every_catalog_client_gets_a_secret_name() -> None:
    identity = _script("chat_ui_identity")
    realm = json.loads((REPO / "infra" / "keycloak" / "realm-export.json").read_text())
    secrets = identity.client_secrets(realm)
    for server in generator.load().kit_servers():
        assert secrets[server.api] == generator.client_secret_env(server.api)  # the API introspects at level 3
