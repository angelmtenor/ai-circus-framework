#!/usr/bin/env python3
"""A small local portal over mcp/catalog.yaml (docs/adr/0024), for people who should not edit YAML.

Tick which tools each group may run on each kit server, add or remove a server, review the generated changes
as diffs, then publish: publishing validates with the generator's own rules and writes the catalog and every
file generated from it, exactly as `make mcp-catalog` does. Nothing is deployed or committed: review with
`git diff`, commit, then `make k3s-mcp-catalog-apply`. Experts keep the files (and LiteLLM's own admin UI).

Local only: it binds 127.0.0.1, answers only to its own Host, and every API call carries a per-run token that
only its own page knows (a page of another origin can neither read it nor send a JSON POST without it).

    make mcp-catalog-portal                       # http://127.0.0.1:8040/
    python3 scripts/mcp_catalog_portal.py --port 8040

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import http.server
import json
import secrets
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_mcp_catalog as gen  # the generator is this portal's only rule book

PAGE = gen.REPO / "mcp" / "portal.html"
MAX_BODY = 256 * 1024
MAX_DIFF_LINES = 400
# The service that serves a scenario kind's MCP door (docs/adr/0019), to pre-fill a new door's address.
DOOR_SERVICE_BY_KIND = {"conversational_rag": "rag-agent", "data_product": "data-api"}
DOOR_PORT = 8000


def catalog_text(repo: Path = gen.REPO) -> str:
    return (repo / gen.CATALOG).read_text()


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def kit_services(repo: Path = gen.REPO) -> list[dict[str, Any]]:
    """Every service built with libs/mcp-kit, with the tools found in its code."""
    services = []
    for pyproject in sorted((repo / "services").glob("*/pyproject.toml")):
        if '"mcp-kit"' in pyproject.read_text():
            service = pyproject.parent.name
            services.append({"service": service, "tools": gen.discover_tools(service, repo)})
    return services


def scenario_doors(repo: Path = gen.REPO) -> list[dict[str, Any]]:
    """Every scenario that opens an MCP door (mcp.expose), with its door's in-cluster address."""
    doors = []
    for path in sorted((repo / "scenarios").glob("*/scenario.yaml")):
        scenario = yaml.safe_load(path.read_text()) or {}
        expose = (scenario.get("mcp") or {}).get("expose") or []
        service = DOOR_SERVICE_BY_KIND.get(scenario.get("kind", ""))
        if expose and service:
            slug = path.parent.name
            doors.append(
                {
                    "name": slug,
                    "title": str(scenario.get("title", slug)),
                    "kind": scenario["kind"],
                    "url": f"http://{service}:{DOOR_PORT}/mcp/{slug}",
                    "tools": list(expose),
                }
            )
    return doors


def realm_roles(repo: Path = gen.REPO) -> list[dict[str, str]]:
    realm = json.loads((repo / gen.REALM).read_text())
    return [
        {"name": r["name"], "description": r.get("description", "")} for r in realm.get("roles", {}).get("realm", [])
    ]


def state(repo: Path = gen.REPO) -> dict[str, Any]:
    """What the page needs: the catalog as data, what it may refer to, and whether it is valid and in sync."""
    text = catalog_text(repo)
    out: dict[str, Any] = {
        "catalog": yaml.safe_load(text),
        "base": fingerprint(text),
        "realm_roles": realm_roles(repo),
        "kit_services": kit_services(repo),
        "scenario_doors": scenario_doors(repo),
        "error": None,
        "in_sync": False,
    }
    try:
        catalog = gen.build(out["catalog"], repo)
        out["in_sync"] = not gen.stale(gen.outputs(catalog, repo), repo)
    except gen.CatalogError as exc:
        out["error"] = str(exc)
    return out


def _diff(path: Path, new: str, repo: Path) -> list[str]:
    old = (repo / path).read_text() if (repo / path).is_file() else ""
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{path}", f"b/{path}", n=2, lineterm=""))
    if len(lines) > MAX_DIFF_LINES:
        lines = [*lines[:MAX_DIFF_LINES], f"… {len(lines) - MAX_DIFF_LINES} more lines (see git diff after publishing)"]
    return lines


def _warnings(catalog: gen.Catalog) -> list[str]:
    return [
        f"{server.name}: no group may run any of its tools"
        for server in catalog.kit_servers()
        if not catalog.grants(server)
    ]


def review(data: Any, repo: Path = gen.REPO) -> dict[str, Any]:
    """Validate a catalog sent by the page and show what publishing it would change. Raises CatalogError."""
    catalog = gen.build(data, repo)
    files = gen.outputs(catalog, repo)
    changed = gen.stale(files, repo)
    return {
        "changed": [str(p) for p in changed],
        "diffs": {str(p): _diff(p, files[p], repo) for p in changed},
        "matrix": gen.matrix(catalog),
        "warnings": _warnings(catalog),
        "_files": {p: files[p] for p in changed},
    }


def publish(data: Any, base: str, repo: Path = gen.REPO) -> dict[str, Any]:
    """Write the catalog and its generated files, unless the catalog changed on disk since the page loaded it."""
    if base != fingerprint(catalog_text(repo)):
        raise ConflictError("mcp/catalog.yaml changed on disk since this page loaded it: reload, then redo the change")
    result = review(data, repo)
    for path, text in result.pop("_files").items():
        (repo / path).write_text(text)
    result["written"] = result.pop("changed")
    result["base"] = fingerprint(catalog_text(repo))
    return result


class ConflictError(RuntimeError):
    """The catalog on disk is not the one the page edited."""


def make_handler(token: str, port: int, repo: Path = gen.REPO) -> type[http.server.BaseHTTPRequestHandler]:
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "mcp-catalog-portal"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the base class's name
            sys.stderr.write(f"  {self.command} {self.path.split('?')[0]} → {args[1] if len(args) > 1 else ''}\n")

        def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _trusted(self, api: bool) -> bool:
            if self.headers.get("Host") not in allowed_hosts:
                self._json(421, {"error": "unexpected Host"})
                return False
            if api and not secrets.compare_digest(self.headers.get("X-Portal-Token", ""), token):
                self._json(403, {"error": "missing or wrong portal token"})
                return False
            return True

        def do_GET(self) -> None:
            path = self.path.split("?")[0]
            if path == "/":
                if not self._trusted(api=False):
                    return
                nonce = secrets.token_urlsafe(16)
                page = PAGE.read_text().replace("__PORTAL_TOKEN__", token).replace("__NONCE__", nonce)
                csp = (
                    f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; connect-src 'self'; "
                    "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
                )
                self._send(200, page.encode(), "text/html; charset=utf-8", {"Content-Security-Policy": csp})
            elif path == "/api/state":
                if self._trusted(api=True):
                    self._json(200, state(repo))
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            path = self.path.split("?")[0]
            if path not in ("/api/review", "/api/publish") or not self._trusted(api=True):
                if path not in ("/api/review", "/api/publish"):
                    self._json(404, {"error": "not found"})
                return
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                self._json(415, {"error": "expected application/json"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                self._json(413, {"error": f"the body must be 1 to {MAX_BODY} bytes"})
                return
            try:
                body = json.loads(self.rfile.read(length))
                if path == "/api/review":
                    result = review(body.get("catalog"), repo)
                    result.pop("_files")
                else:
                    result = publish(body.get("catalog"), str(body.get("base", "")), repo)
                    print(f"✓ published: {', '.join(result['written']) or 'nothing changed'}", file=sys.stderr)
                self._json(200, result)
            except (json.JSONDecodeError, AttributeError):
                self._json(400, {"error": 'expected {"catalog": {...}}'})
            except gen.CatalogError as exc:
                self._json(422, {"error": str(exc)})
            except ConflictError as exc:
                self._json(409, {"error": str(exc)})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="A local portal over mcp/catalog.yaml.")
    parser.add_argument("--port", type=int, default=8040)
    parser.add_argument("--repo", type=Path, default=gen.REPO, help="the checkout to edit (default: this one)")
    args = parser.parse_args()
    token = secrets.token_urlsafe(32)
    server = http.server.HTTPServer(("127.0.0.1", args.port), make_handler(token, args.port, args.repo.resolve()))
    print(
        f"✓ MCP catalog portal on http://127.0.0.1:{args.port}/ for {args.repo.resolve()} (local only; Ctrl+C to stop)",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
