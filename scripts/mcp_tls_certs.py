#!/usr/bin/env python3
"""Certificates for mutual TLS between each Level 3 MCP server and its API (docs/adr/0025).

For every kit server of mcp/catalog.yaml with `level: 3`, one small certificate authority signs the API's server
certificate and the MCP server's client certificate; each side trusts only that authority, so the API accepts its
own MCP server and nobody else. Files live in infra/mcp-tls/<server>/ (gitignored): ca.crt, ca.key, server.crt,
server.key, client.crt, client.key. A valid authority is kept; a certificate is re-issued when it is missing, has
less than 30 days left, or its authority changed. Keys are never printed.

With --k8s it also applies one Secret per workload, each holding only that workload's own key and the authority's
certificate (`<api>-tls`, `<service>-tls`: tls.crt, tls.key, ca.crt). docker compose mounts the files directly.

    make mcp-tls        # files only (compose)
    make k3s-mcp-tls    # files + Secrets (k3s)

Run in libs/mcp-kit's environment (it holds mcp_kit.pki and PyYAML).

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path

from mcp_kit import pki

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_mcp_catalog as catalog  # the catalog's own loader and rules

OUT = catalog.REPO / "infra" / "mcp-tls"
NAMESPACE = "ai-circus"
RENEW_BEFORE = dt.timedelta(days=30)
CA_RENEW_BEFORE = dt.timedelta(days=180)


def _valid(directory: Path, name: str, minimum: dt.timedelta) -> pki.Issued | None:
    cert, key = directory / f"{name}.crt", directory / f"{name}.key"
    if not (cert.is_file() and key.is_file()):
        return None
    issued = pki.load(cert, key)
    return issued if issued.expires_in() > minimum else None


def ensure(server: catalog.Server, out: Path = OUT) -> dict[str, Path]:
    """The pair's authority and both certificates, issued or renewed as needed. Returns the file paths."""
    directory = out / server.name
    ca = _valid(directory, "ca", CA_RENEW_BEFORE)
    renewed_ca = ca is None
    if ca is None:
        ca = pki.issue_ca(f"{server.name} MCP pair CA")
        pki.write(ca, directory / "ca.crt", directory / "ca.key")
    dns = (server.api, f"{server.api}.{NAMESPACE}", f"{server.api}.{NAMESPACE}.svc", "localhost")
    leaves = {
        "server": (server.api, pki.SERVER, dns),
        "client": (server.service, pki.CLIENT, ()),
    }
    for kind, (common_name, usage, names) in leaves.items():
        current = None if renewed_ca else _valid(directory, kind, RENEW_BEFORE)
        if current is None or current.certificate.issuer != ca.certificate.subject:
            pki.write(
                pki.issue_leaf(ca, common_name, usage=usage, dns_names=names),
                directory / f"{kind}.crt",
                directory / f"{kind}.key",
            )
    return {name: directory / name for name in ("ca.crt", "server.crt", "server.key", "client.crt", "client.key")}


def apply_secret(name: str, cert: Path, key: Path, ca: Path) -> None:
    """One workload's Secret: its own certificate and key, and the pair's authority (never printed)."""
    manifest = subprocess.run(
        [
            "kubectl", "create", "secret", "generic", name, "-n", NAMESPACE,
            f"--from-file=tls.crt={cert}", f"--from-file=tls.key={key}", f"--from-file=ca.crt={ca}",
            "--dry-run=client", "-o", "yaml",
        ],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    subprocess.run(["kubectl", "apply", "-f", "-"], input=manifest, capture_output=True, text=True, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Mutual-TLS certificates for Level 3 MCP servers.")
    parser.add_argument("--k8s", action="store_true", help="also apply the per-workload Secrets")
    args = parser.parse_args()
    servers = [s for s in catalog.load().kit_servers() if s.level >= 3]
    if not servers:
        print("✓ no level 3 kit server in mcp/catalog.yaml: nothing to issue")
        return 0
    for server in servers:
        files = ensure(server)
        until = pki.load(files["client.crt"], files["client.key"]).certificate.not_valid_after_utc.date()
        print(f"✓ {server.name}: {server.api} (server) and {server.service} (client) certificates, valid to {until}")
        if args.k8s:
            apply_secret(f"{server.api}-tls", files["server.crt"], files["server.key"], files["ca.crt"])
            apply_secret(f"{server.service}-tls", files["client.crt"], files["client.key"], files["ca.crt"])
            print(f"✓ Secrets {server.api}-tls and {server.service}-tls applied in {NAMESPACE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
