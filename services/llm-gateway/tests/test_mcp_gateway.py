"""
test_mcp_gateway.py
-------------------

Pins the MCP gateway's security contract (docs/adr/0020): every door behind it is
`true_passthrough`, so the gateway forwards the caller's own token, holds no credential
and never decides access. The contract tests load the real config into LiteLLM's own MCP
server manager, so an upgrade that changes how LiteLLM reads or gates these entries fails here.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import yaml
from litellm.proxy._experimental.mcp_server.auth.user_api_key_auth_mcp import MCPRequestHandler
from litellm.proxy._experimental.mcp_server.mcp_server_manager import (
    _caller_authorization_fans_out,
    global_mcp_server_manager,
)

from tests.conftest import LITELLM_LOOP

SERVICE_DIR = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = SERVICE_DIR.parents[1] / "scenarios"
# What an entry may carry: routing and description only. Any credential field (a token, a client
# secret, static headers) would make the gateway hold a door's credential.
ALLOWED_KEYS = {"url", "transport", "auth_type", "description", "extra_headers"}
CREDENTIAL_HEADERS = {"authorization", "cookie", "x-api-key"}


def mcp_servers() -> dict[str, dict[str, Any]]:
    """The `mcp_servers` block of the committed litellm_config.yaml."""
    config = yaml.safe_load((SERVICE_DIR / "litellm_config.yaml").read_text())
    return config["mcp_servers"]


def test_every_door_is_passthrough_and_carries_no_credential() -> None:
    servers = mcp_servers()
    assert servers, "the MCP gateway has no doors"
    for name, entry in servers.items():
        assert entry["auth_type"] == "true_passthrough", name
        assert set(entry) <= ALLOWED_KEYS, f"{name}: unexpected keys {set(entry) - ALLOWED_KEYS}"
        forwarded = {header.lower() for header in entry.get("extra_headers", [])}
        assert not forwarded & CREDENTIAL_HEADERS, f"{name} forwards a credential header by name"


def test_every_door_is_an_in_cluster_scenario_door() -> None:
    for name, entry in mcp_servers().items():
        url = urlparse(entry["url"])
        assert url.scheme == "http" and url.hostname and "." not in url.hostname, (
            f"{name} must be an in-cluster service"
        )
        assert url.path == f"/mcp/{name}", f"{name}: the entry name must be the door's scenario slug"
        assert "-" not in name, f"{name}: LiteLLM uses '-' as the tool prefix separator"
        scenario = yaml.safe_load((SCENARIOS_DIR / name / "scenario.yaml").read_text())
        assert scenario.get("mcp", {}).get("expose"), f"scenario {name} declares no MCP door"


@pytest.fixture
def loaded_gateway() -> Iterator[None]:
    """LiteLLM's own MCP server manager, loaded with the committed config, restored afterwards."""
    saved = dict(global_mcp_server_manager.config_mcp_servers)
    LITELLM_LOOP.run_until_complete(global_mcp_server_manager.load_servers_from_config(mcp_servers()))
    try:
        yield
    finally:
        global_mcp_server_manager.config_mcp_servers.clear()
        global_mcp_server_manager.config_mcp_servers.update(saved)


@pytest.mark.usefixtures("loaded_gateway")
def test_litellm_loads_every_door_as_passthrough() -> None:
    for name in mcp_servers():
        server = global_mcp_server_manager.get_mcp_server_by_name(name)
        assert server is not None, name
        assert server.is_true_passthrough, name


@pytest.mark.usefixtures("loaded_gateway")
def test_door_paths_skip_the_gateway_key_and_others_do_not() -> None:
    every_door = ",".join(mcp_servers())
    skips = MCPRequestHandler._target_servers_are_true_passthrough
    assert skips(path=f"/mcp/{every_door}", mcp_servers=None, client_ip=None)
    for name in mcp_servers():
        assert skips(path=f"/mcp/{name}", mcp_servers=None, client_ip=None)
    # No server in the path, or an unknown one: LiteLLM's own key check stays in force.
    assert not skips(path="/mcp", mcp_servers=None, client_ip=None)
    assert not skips(path="/mcp/not_a_door", mcp_servers=None, client_ip=None)


@pytest.mark.usefixtures("loaded_gateway")
def test_one_authorization_is_never_fanned_out_to_several_doors() -> None:
    # A listing over several doors withholds the request-wide Authorization (RFC 9700 cross-resource
    # replay): the client binds one credential per door with `x-mcp-{slug}-authorization`. A single
    # door, or a call addressed to one tool, gets the caller's Authorization as is.
    doors = [global_mcp_server_manager.get_mcp_server_by_name(name) for name in mcp_servers()]
    assert len(doors) >= 2, "this contract needs at least two doors"
    for door in doors:
        assert _caller_authorization_fans_out(door, doors)
        assert not _caller_authorization_fans_out(door, [door])
        assert not _caller_authorization_fans_out(door, None)
