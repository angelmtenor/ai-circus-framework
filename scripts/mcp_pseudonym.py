#!/usr/bin/env python3
"""
mcp_pseudonym.py
----------------

Maps the pseudonyms an API behind an MCP server sees back to users, for an audit (docs/adr/0029).

Each kit server's Keycloak client gives its API a pairwise subject instead of the user's Keycloak id: a stable UUID
per user and per server, computed by Keycloak from the client's sector (its redirect URI's host), the user's id and a
salt Keycloak generated and keeps. Only someone with Keycloak's admin access can recompute it, which is the point:
the API's logs name pseudonyms, and the identity provider's administrator alone can say who they are.

    python3 scripts/mcp_pseudonym.py --user analyst@lite-demo.local   # the pseudonym each API sees for this user
    python3 scripts/mcp_pseudonym.py --find 7d183d3f-…                 # which user (and API) a pseudonym belongs to

Runs kcadm inside the Keycloak pod with its bootstrap admin (as scripts/chat_ui_identity.py). Never prints a salt.
Keycloak's algorithm (SHA256PairwiseSubMapper): UUID.nameUUIDFromBytes(sha256(sector + user id + salt)).

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chat_ui_identity import Kcadm

PAIRWISE_MAPPER = "oidc-sha256-pairwise-sub-mapper"
PAGE = 100


def pseudonym(sector: str, user_id: str, salt: str) -> str:
    """Keycloak's pairwise subject: Java's UUID.nameUUIDFromBytes (an MD5-based version-3 UUID) of the SHA-256."""
    digest = hashlib.sha256(sector.encode() + user_id.encode() + salt.encode()).digest()
    # Java's name-based UUID is MD5 of the digest: a format, not a security use.
    raw = bytearray(hashlib.md5(digest).digest())
    raw[6] = (raw[6] & 0x0F) | 0x30
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def pseudonymous_clients(kc: Kcadm) -> list[tuple[str, str, str]]:
    """(client id, sector, salt) of every client with a pairwise subject mapper."""
    found = []
    for client in kc("get", "clients", "--fields", "id,clientId,redirectUris"):
        for mapper in kc("get", f"clients/{client['id']}/protocol-mappers/models"):
            if mapper.get("protocolMapper") != PAIRWISE_MAPPER:
                continue
            config = mapper.get("config", {})
            sector_uri = config.get("sectorIdentifierUri") or ""
            hosts = {urlsplit(u).hostname for u in client.get("redirectUris") or [] if urlsplit(u).hostname}
            if sector_uri or len(hosts) != 1:
                print(f"⚠ {client['clientId']}: sector not derivable from one redirect host, skipped", file=sys.stderr)
                continue
            salt = config.get("pairwiseSubAlgorithmSalt", "")
            if not salt:
                print(
                    f"⚠ {client['clientId']}: no salt yet (a fresh import), so its pseudonyms are guessable: run"
                    " scripts/chat_ui_identity.py, which has Keycloak generate one",
                    file=sys.stderr,
                )
            found.append((client["clientId"], hosts.pop(), salt))
    return found


def users(kc: Kcadm, username: str | None) -> list[dict[str, Any]]:
    if username:
        return kc("get", "users", "-q", f"username={username}", "-q", "exact=true", "--fields", "id,username")
    everyone: list[dict[str, Any]] = []
    while page := kc("get", "users", "-q", f"first={len(everyone)}", "-q", f"max={PAGE}", "--fields", "id,username"):
        everyone += page
        if len(page) < PAGE:
            break
    return everyone


def main() -> int:
    parser = argparse.ArgumentParser(description="Map API pseudonyms to users for an audit (docs/adr/0029).")
    parser.add_argument("--cluster", default="ai-circus")
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument("--user", help="a username: print the pseudonym each API sees for it")
    what.add_argument("--find", help="a pseudonym: print the user and the API it belongs to")
    args = parser.parse_args()

    kc = Kcadm(args.cluster)
    clients = pseudonymous_clients(kc)
    if not clients:
        print("❌ no client gives a pseudonym (no pairwise subject mapper)", file=sys.stderr)
        return 1
    people = users(kc, args.user)
    if args.user and not people:
        print(f"❌ no user {args.user}", file=sys.stderr)
        return 1
    target = (args.find or "").strip().lower()
    hits = 0
    for person in people:
        for client_id, sector, salt in clients:
            value = pseudonym(sector, person["id"], salt)
            if args.user:
                print(f"{person['username']} at {client_id}'s API: {value}")
            elif value == target:
                print(f"{target} is {person['username']} (Keycloak id {person['id']}) at {client_id}'s API")
                hits += 1
    if args.find and not hits:
        print(f"❌ {target} matches no user at {', '.join(c for c, _, _ in clients)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
