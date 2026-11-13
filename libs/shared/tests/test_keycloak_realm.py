"""
test_keycloak_realm.py
----------------------

The realm bootstrap (infra/keycloak/realm-export.json) must issue the tokens `ai_circus_shared.auth`
reads. Imported as written, it once issued tokens with no `sub`, no realm roles and a list-shaped
`organization` claim, so a real Keycloak user could never pass `validate_token` (docs/adr/0021):
an imported realm gets no built-in scopes, and the organization mapper lists aliases unless asked
for ids. These tests pin each claim to the scope and mapper that produce it.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from ai_circus_shared.auth import ORGANIZATION_CLAIM, REALM_ACCESS_CLAIM

REPO = Path(__file__).resolve().parents[3]
REALM = json.loads((REPO / "infra" / "keycloak" / "realm-export.json").read_text())
SCOPES = {scope["name"]: scope for scope in REALM["clientScopes"]}
# Every token a service validates needs these: sub (basic), tenant (organization), audience
# (platform-backend) and the realm roles a data product's access policy reads (roles).
REQUIRED_DEFAULT_SCOPES = {"basic", "organization", "platform-backend", "roles"}
# Open WebUI refreshes a token that expires within 5 minutes; a shorter life would refresh it on
# every request, and the MCP gateway binds a session to the token it was opened with.
CHAT_UI_REFRESH_BUFFER_SECONDS = 300
# The chat UIs (docs/adr/0021, 0026): each signs users in with its own confidential client and sends the user's own
# token to the MCP gateway.
CHAT_UI_CLIENTS = ("open-webui", "librechat")


def mappers(scope: str, kind: str) -> list[dict[str, Any]]:
    """The protocol mappers of one type in one client scope."""
    return [m for m in SCOPES[scope]["protocolMappers"] if m["protocolMapper"] == kind]


def test_tokens_carry_a_subject() -> None:
    assert mappers("basic", "oidc-sub-mapper"), "without the basic scope's sub mapper, access tokens have no `sub`"


def test_tokens_carry_realm_roles_where_auth_reads_them() -> None:
    (realm_roles,) = mappers("roles", "oidc-usermodel-realm-role-mapper")
    assert realm_roles["config"]["claim.name"] == f"{REALM_ACCESS_CLAIM}.roles"
    assert realm_roles["config"]["access.token.claim"] == "true"


def test_organization_claim_is_keyed_by_alias_with_the_id() -> None:
    (organization,) = mappers("organization", "oidc-organization-membership-mapper")
    assert organization["config"]["claim.name"] == ORGANIZATION_CLAIM
    assert organization["config"]["addOrganizationId"] == "true", "without it the claim is a list of aliases"
    assert organization["config"]["access.token.claim"] == "true"


SIGN_IN_CLIENTS = [c for c in REALM["clients"] if c.get("standardFlowEnabled")]


@pytest.mark.parametrize("client", SIGN_IN_CLIENTS, ids=lambda c: c["clientId"])
def test_every_sign_in_client_issues_the_claims_the_services_need(client: dict[str, Any]) -> None:
    missing = REQUIRED_DEFAULT_SCOPES - set(client["defaultClientScopes"])
    assert not missing, f"{client['clientId']} lacks default scopes {missing}"
    assert set(client["defaultClientScopes"]) <= set(SCOPES) | {"web-origins", "acr"}, "a default scope is undeclared"


def test_every_data_product_row_filter_claim_is_mapped() -> None:
    mapped = {m["config"]["claim.name"] for m in SCOPES["data-access"]["protocolMappers"]}
    for path in sorted((REPO / "scenarios").glob("*/scenario.yaml")):
        scenario = yaml.safe_load(path.read_text())
        claim = (((scenario.get("data_product") or {}).get("access") or {}).get("row_filter") or {}).get("claim")
        if claim:
            assert claim in mapped, f"{path.parent.name}: row filter claim {claim!r} is not in the data-access scope"


@pytest.mark.parametrize("client_id", CHAT_UI_CLIENTS)
def test_chat_ui_client_is_confidential_and_outlives_its_refresh_buffer(client_id: str) -> None:
    (chat,) = [c for c in REALM["clients"] if c["clientId"] == client_id]
    assert chat["publicClient"] is False
    assert chat["attributes"]["pkce.code.challenge.method"] == "S256"
    assert "secret" not in chat, "the chat UI's secret lives in .env, never in this file"
    assert not chat["directAccessGrantsEnabled"]
    assert int(chat["attributes"]["access.token.lifespan"]) > 2 * CHAT_UI_REFRESH_BUFFER_SECONDS


def audiences(scope: str) -> set[str]:
    """The client ids one scope's audience mappers add."""
    return {m["config"]["included.client.audience"] for m in mappers(scope, "oidc-audience-mapper")}


@pytest.mark.parametrize("client_id", CHAT_UI_CLIENTS)
def test_mcp_servers_are_in_the_user_token_audience(client_id: str) -> None:
    # Keycloak only lets a client exchange a token it is an audience of (docs/adr/0022).
    (chat,) = [c for c in REALM["clients"] if c["clientId"] == client_id]
    assert "mcp-servers" in chat["defaultClientScopes"]
    exchangers = {
        c["clientId"] for c in REALM["clients"] if c["attributes"].get("standard.token.exchange.enabled") == "true"
    }
    assert exchangers and exchangers <= audiences("mcp-servers")


@pytest.mark.parametrize(
    "client",
    [c for c in REALM["clients"] if c["attributes"].get("standard.token.exchange.enabled") == "true"],
    ids=lambda c: c["clientId"],
)
def test_an_mcp_server_may_exchange_for_its_own_api_only(client: dict[str, Any]) -> None:
    assert client["publicClient"] is False and "secret" not in client
    assert not client["standardFlowEnabled"] and not client["directAccessGrantsEnabled"]
    reachable = set().union(
        *(audiences(name) for name in client["defaultClientScopes"] + client["optionalClientScopes"])
    )
    assert len(reachable) == 1, f"{client['clientId']} could obtain tokens for {reachable}"
    assert "platform-backend" not in client["defaultClientScopes"], "a T2 must not be valid at the platform's services"
