#!/usr/bin/env python3
"""
chat_ui_identity.py
-------------------

Idempotently brings the live Keycloak realm up to what the optional chat UIs need
(Open WebUI, docs/adr/0021; LibreChat, docs/adr/0026), then grants the demo tenant its scenarios.
`make k3s-chat-ui-up` and `make k3s-librechat-up` run it.

`--import-realm` only applies infra/keycloak/realm-export.json on a realm's first boot, so a
cluster created before a realm change never sees it. This script makes the live realm match
that file, which stays the single source of truth:

- every client scope, realm role and client it declares (missing ones are created, missing
  default client scopes are attached); confidential clients' secrets come from .env: each chat
  UI's, and each MCP server's from mcp/catalog.yaml as <CLIENT_ID>_CLIENT_SECRET (crm-mcp ->
  CRM_MCP_CLIENT_SECRET, docs/adr/0022, 0024);
- the admin-only `stores` user attribute that the `data-access` scope maps into tokens;
- a demo tenant (Keycloak Organization `lite-demo`) with two users: an analyst and the
  S012 store manager of surf_retail, sharing one generated password;
- the tenant's entitlements to the scenarios the chat UI reaches, through platform-registry.

Secrets (CHAT_UI_OIDC_CLIENT_SECRET, CHAT_UI_SECRET_KEY, CHAT_UI_DEMO_PASSWORD, LibreChat's LIBRECHAT_*
keys, each MCP server's <CLIENT_ID>_CLIENT_SECRET) are generated into .env when missing and are never
printed; they reach Keycloak over stdin, never as arguments.
Needs the platform-registry port-forward (make k3s-portforward) for the entitlements step.

Usage: python3 scripts/chat_ui_identity.py [--cluster ai-circus] [--env-file .env]

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
REALM_EXPORT = REPO / "infra" / "keycloak" / "realm-export.json"
REALM = "ai-circus"
NAMESPACE = "ai-circus"
KCADM = "/opt/keycloak/bin/kcadm.sh"
KCADM_CONFIG = "/tmp/kcadm-chat-ui.config"
CHAT_UI_SECRETS = {"open-webui": "CHAT_UI_OIDC_CLIENT_SECRET", "librechat": "LIBRECHAT_OIDC_CLIENT_SECRET"}
CHAT_UI_GENERATED = ("CHAT_UI_OIDC_CLIENT_SECRET", "CHAT_UI_SECRET_KEY", "CHAT_UI_DEMO_PASSWORD")
# LibreChat's own keys (docs/adr/0026), in random bytes: its credential encryption needs exactly a 32-byte key and a
# 16-byte IV, its JWT secrets at least 32 bytes. Anything else gets 24 bytes (48 hex characters).
LIBRECHAT_GENERATED = {
    "LIBRECHAT_OIDC_CLIENT_SECRET": 24,
    "LIBRECHAT_OPENID_SESSION_SECRET": 32,
    "LIBRECHAT_CREDS_KEY": 32,
    "LIBRECHAT_CREDS_IV": 16,
    "LIBRECHAT_JWT_SECRET": 32,
    "LIBRECHAT_JWT_REFRESH_SECRET": 32,
    "LIBRECHAT_MONGO_PASSWORD": 24,
}
DEFAULT_SECRET_BYTES = 24


def client_secret_env(client_id: str) -> str:
    """The .env key of an MCP server's client secret: crm-mcp -> CRM_MCP_CLIENT_SECRET (docs/adr/0024)."""
    return client_id.upper().replace("-", "_") + "_CLIENT_SECRET"


CATALOG_MARK = "Generated from mcp/catalog.yaml."


def client_secrets(export: dict[str, Any]) -> dict[str, str]:
    """Each confidential client's .env key: the chat UI's, every MCP server's that exchanges tokens, and every
    client generated from mcp/catalog.yaml (an API introspects its own tokens at level 3, docs/adr/0022, 0025),
    so a new server in the catalog needs no edit here."""
    generated = {
        c["clientId"]: client_secret_env(c["clientId"])
        for c in export.get("clients", [])
        if c.get("attributes", {}).get("standard.token.exchange.enabled") == "true"
        or CATALOG_MARK in c.get("description", "")
    }
    return {**CHAT_UI_SECRETS, **generated}

ORG = {
    "name": "Lite Demo",
    "alias": "lite-demo",
    "enabled": True,
    "domains": [{"name": "lite-demo.local"}],
}
DEMO_USERS = (
    {
        "username": "analyst@lite-demo.local",
        "firstName": "Ana",
        "lastName": "Analyst",
        "roles": ["retail_analyst"],
        "stores": [],
    },
    {
        "username": "manager@lite-demo.local",
        "firstName": "Marco",
        "lastName": "Manager",
        "roles": ["retail_store_manager"],
        "stores": ["S012"],
    },
)
# Scenarios the chat UI's MCP connections reach (llm-gateway's mcp_servers, docs/adr/0020).
DEMO_SCENARIOS = ("ai_circus_reference", "surf_retail")
STORES_ATTRIBUTE = {
    "name": "stores",
    "displayName": "Stores",
    "multivalued": True,
    "permissions": {"view": ["admin"], "edit": ["admin"]},
    "validations": {"length": {"max": 20}},
}


def read_env(env_file: Path) -> dict[str, str]:
    """KEY=value pairs of the .env file (values unquoted), never printed."""
    values = {}
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("'\"")
    return values


def ensure_generated_secrets(env_file: Path, export: dict[str, Any]) -> dict[str, str]:
    """Generate any missing chat UI or MCP server secret into .env (random hex); return the file's values.

    A key left empty (`KEY=`, as .env.example ships LibreChat's) is filled on its own line: a second line with the
    same key would make `kubectl create secret --from-env-file` refuse the whole Secret.
    """
    env = read_env(env_file)
    wanted = dict.fromkeys([*CHAT_UI_GENERATED, *LIBRECHAT_GENERATED, *client_secrets(export).values()])
    missing = [key for key in wanted if not env.get(key)]
    if missing:
        values = {key: secrets.token_hex(LIBRECHAT_GENERATED.get(key, DEFAULT_SECRET_BYTES)) for key in missing}
        lines = env_file.read_text().splitlines()
        for i, line in enumerate(lines):
            key, sep, value = line.partition("=")
            if sep and key.strip() in values and not value.strip().strip("'\""):
                lines[i] = f"{key.strip()}={values.pop(key.strip())}"
        if values:
            lines += [
                "",
                "# Optional chat UIs (make k3s-chat-ui-up / k3s-librechat-up, docs/adr/0021, 0026): generated by"
                " scripts/chat_ui_identity.py",
                *(f"{key}={value}" for key, value in values.items()),
            ]
        env_file.write_text("\n".join(lines) + "\n")
        print(f"✓ generated {', '.join(missing)} in {env_file.name}")
    return read_env(env_file)


class Kcadm:
    """kcadm.sh inside the Keycloak pod, logged in with the pod's own bootstrap admin."""

    def __init__(self, cluster: str) -> None:
        self.base = [
            "kubectl",
            "--context",
            f"k3d-{cluster}",
            "-n",
            NAMESPACE,
            "exec",
            "-i",
            "deploy/keycloak",
            "--",
        ]
        login = (
            f"{KCADM} config credentials --server http://localhost:8080 --realm master "
            f'--user "$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" --config {KCADM_CONFIG}'
        )
        self._run([*self.base, "bash", "-c", login])

    @staticmethod
    def _run(argv: list[str], stdin: str | None = None) -> str:
        result = subprocess.run(
            argv, input=stdin, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"kcadm failed ({argv[-3:]}): {result.stderr.strip()[:300]}"
            )
        return result.stdout

    def __call__(self, *args: str, body: Any = None) -> Any:
        argv = [*self.base, KCADM, *args, "-r", REALM, "--config", KCADM_CONFIG]
        stdin = None
        if body is not None:
            argv += ["-f", "-"]
            stdin = json.dumps(body)
        out = self._run(argv, stdin).strip()
        return json.loads(out) if out.startswith(("[", "{")) else out


def sync_mappers(kc: Kcadm, scope_id: str, scope: dict) -> None:
    """Make an existing scope's protocol mappers carry the file's config (a missing mapper is added)."""
    path = f"client-scopes/{scope_id}/protocol-mappers/models"
    live = {m["name"]: m for m in kc("get", path)}
    for mapper in scope.get("protocolMappers", []):
        current = live.get(mapper["name"])
        if current is None:
            kc("create", path, body=mapper)
            print(f"✓ {scope['name']}: mapper {mapper['name']} added")
        elif any(
            current.get("config", {}).get(key) != value
            for key, value in mapper.get("config", {}).items()
        ):
            current["config"] = {**current.get("config", {}), **mapper["config"]}
            kc("update", f"{path}/{current['id']}", body=current)
            print(f"✓ {scope['name']}: mapper {mapper['name']} updated")


def sync_realm(kc: Kcadm, export: dict, env: dict[str, str]) -> None:
    """Create what realm-export.json declares and the live realm lacks."""
    secret_keys = client_secrets(export)
    live_scopes = {
        s["name"]: s["id"] for s in kc("get", "client-scopes", "--fields", "id,name")
    }
    for scope in export.get("clientScopes", []):
        if scope["name"] not in live_scopes:
            kc("create", "client-scopes", body=scope)
            print(f"✓ client scope {scope['name']} created")
    live_scopes = {
        s["name"]: s["id"] for s in kc("get", "client-scopes", "--fields", "id,name")
    }
    for scope in export.get("clientScopes", []):
        sync_mappers(kc, live_scopes[scope["name"]], scope)

    live_roles = {r["name"] for r in kc("get", "roles", "--fields", "name")}
    for role in export.get("roles", {}).get("realm", []):
        if role["name"] not in live_roles:
            kc("create", "roles", body=role)
            print(f"✓ realm role {role['name']} created")

    profile = kc("get", "users/profile")
    if not any(
        a["name"] == STORES_ATTRIBUTE["name"] for a in profile.get("attributes", [])
    ):
        profile["attributes"].append(STORES_ATTRIBUTE)
        kc("update", "users/profile", body=profile)
        print("✓ user profile attribute `stores` declared (admin-only)")

    for client in export.get("clients", []):
        found = kc(
            "get", "clients", "-q", f"clientId={client['clientId']}", "--fields", "id"
        )
        if not found:
            body = dict(client)
            if client["clientId"] in secret_keys:
                body["secret"] = env[secret_keys[client["clientId"]]]
            kc("create", "clients", body=body)
            print(f"✓ client {client['clientId']} created")
            found = kc(
                "get",
                "clients",
                "-q",
                f"clientId={client['clientId']}",
                "--fields",
                "id",
            )
        client_uuid = found[0]["id"]
        # Attributes (token lifespan, PKCE…) follow the file; the chat UI's secret follows .env.
        update: dict[str, Any] = {"attributes": client.get("attributes", {})}
        if client["clientId"] in secret_keys:
            update["secret"] = env[secret_keys[client["clientId"]]]
        kc("update", f"clients/{client_uuid}", body=update)
        attached = {
            s["name"] for s in kc("get", f"clients/{client_uuid}/default-client-scopes")
        }
        for name in client.get("defaultClientScopes", []):
            if name not in attached and name in live_scopes:
                kc(
                    "update",
                    f"clients/{client_uuid}/default-client-scopes/{live_scopes[name]}",
                )
                print(f"✓ {client['clientId']}: default scope {name} attached")


def sync_demo_tenant(kc: Kcadm, password: str) -> str:
    """The demo Organization, its two users, their roles and store attribute. Returns the org id."""
    orgs = [
        o
        for o in kc("get", "organizations", "-q", f"search={ORG['alias']}")
        if o.get("alias") == ORG["alias"]
    ]
    if not orgs:
        kc("create", "organizations", body=ORG)
        print(f"✓ organization {ORG['alias']} created")
        orgs = [
            o
            for o in kc("get", "organizations", "-q", f"search={ORG['alias']}")
            if o.get("alias") == ORG["alias"]
        ]
    org_id = orgs[0]["id"]
    members = {m["id"] for m in kc("get", f"organizations/{org_id}/members")}

    for user in DEMO_USERS:
        attributes = {"stores": user["stores"]} if user["stores"] else {}
        found = kc(
            "get", "users", "-q", f"username={user['username']}", "-q", "exact=true"
        )
        if not found:
            kc(
                "create",
                "users",
                body={
                    "username": user["username"],
                    "email": user["username"],
                    "firstName": user["firstName"],
                    "lastName": user["lastName"],
                    "enabled": True,
                    "emailVerified": True,
                    "attributes": attributes,
                    "credentials": [
                        {"type": "password", "value": password, "temporary": False}
                    ],
                },
            )
            print(f"✓ user {user['username']} created")
            found = kc(
                "get", "users", "-q", f"username={user['username']}", "-q", "exact=true"
            )
        user_id = found[0]["id"]
        current = kc("get", f"users/{user_id}")
        if current.get("attributes", {}).get("stores", []) != user["stores"]:
            current.setdefault("attributes", {})["stores"] = user["stores"]
            kc("update", f"users/{user_id}", body=current)
        kc(
            "update",
            f"users/{user_id}/reset-password",
            body={"type": "password", "value": password, "temporary": False},
        )
        for role in user["roles"]:
            kc("add-roles", "--uid", user_id, "--rolename", role)
        if user_id not in members:
            kc("create", f"organizations/{org_id}/members", "-b", json.dumps(user_id))
            print(f"✓ {user['username']} joined {ORG['alias']}")
    return org_id


def grant_entitlements(org_id: str, admin_key: str, registry: str) -> None:
    """Entitle the demo tenant to the chat UI's scenarios (idempotent PUTs, admin key)."""
    for slug in DEMO_SCENARIOS:
        request = urllib.request.Request(
            f"{registry}/entitlements/{org_id}/{slug}",
            method="PUT",
            headers={"Authorization": f"Bearer {admin_key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                assert response.status == 204
        except urllib.error.URLError as exc:
            sys.exit(
                f"❌ entitlement {slug}: {exc} — is the platform-registry port-forward up (make k3s-portforward)?"
            )
    print(f"✓ {ORG['alias']} entitled to {', '.join(DEMO_SCENARIOS)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--cluster", default="ai-circus")
    parser.add_argument("--env-file", type=Path, default=REPO / ".env")
    parser.add_argument("--registry", default="http://localhost:8010")
    args = parser.parse_args()

    export = json.loads(REALM_EXPORT.read_text())
    env = ensure_generated_secrets(args.env_file, export)
    if not env.get("ADMIN_API_KEY"):
        sys.exit("❌ ADMIN_API_KEY is not set in .env")
    kc = Kcadm(args.cluster)
    sync_realm(kc, export, env)
    org_id = sync_demo_tenant(kc, env["CHAT_UI_DEMO_PASSWORD"])
    grant_entitlements(org_id, env["ADMIN_API_KEY"], args.registry)
    print(
        f"✓ chat UI identity ready: users {', '.join(u['username'] for u in DEMO_USERS)} (password: CHAT_UI_DEMO_PASSWORD in .env)"
    )


if __name__ == "__main__":
    main()
