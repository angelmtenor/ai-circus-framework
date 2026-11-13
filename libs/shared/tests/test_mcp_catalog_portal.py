"""
test_mcp_catalog_portal.py
--------------------------

The local portal over mcp/catalog.yaml (docs/adr/0024): it publishes only what the generator accepts, writes
exactly the generated files, refuses to overwrite a catalog edited on disk meanwhile, and answers only its own
page (Host, per-run token, JSON body). Publishing runs against a copy of the repository, never the real one.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import http.client
import http.server
import importlib.util
import json
import shutil
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]


def _script(name: str) -> ModuleType:
    sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


portal = _script("mcp_catalog_portal")
gen = portal.gen


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A copy of every file the catalog reads or writes."""
    catalog = yaml.safe_load((REPO / gen.CATALOG).read_text())
    paths = [gen.CATALOG, gen.GATEWAY_CONFIG, gen.CHAT_UI_MANIFEST, gen.LIBRECHAT_CONFIG, gen.COMPOSE, gen.REALM]
    for name, server in catalog["servers"].items():
        if server["kind"] == "scenario_door":
            paths.append(Path("scenarios") / name / "scenario.yaml")
    for pyproject in (REPO / "services").glob("*/pyproject.toml"):
        if '"mcp-kit"' in pyproject.read_text():
            service = pyproject.parent
            paths += [
                p.relative_to(REPO) for p in [pyproject, service / "policy.yaml", *(service / "src").rglob("*.py")]
            ]
    for path in paths:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / path, tmp_path / path)
    return tmp_path


def test_tools_are_discovered_from_the_code_without_running_it() -> None:
    catalog = gen.load()
    for server in catalog.kit_servers():
        assert set(gen.discover_tools(server.service)) == set(server.tools)


def test_state_describes_a_valid_catalog_in_sync() -> None:
    state = portal.state()
    assert state["error"] is None and state["in_sync"]
    assert {s["service"] for s in state["kit_services"]} >= {s.service for s in gen.load().kit_servers()}
    assert {d["name"] for d in state["scenario_doors"]} >= {
        s.name for s in gen.load().servers if s.kind == "scenario_door"
    }


def test_publish_writes_the_catalog_and_every_generated_file(repo: Path) -> None:
    state = portal.state(repo)
    data = state["catalog"]
    group = next(iter(data["groups"]))
    server = next(iter(data["groups"][group]))
    data["groups"][group][server] = "*"

    preview = portal.review(data, repo)
    assert "mcp/catalog.yaml" in preview["changed"] and preview["diffs"]
    assert (repo / gen.CATALOG).read_text() == (REPO / gen.CATALOG).read_text(), "a review never writes"

    result = portal.publish(data, state["base"], repo)
    assert set(result["written"]) == set(preview["changed"])
    assert not gen.stale(gen.outputs(gen.load(repo), repo), repo)
    assert gen.load(repo).groups[group][server] == "*"
    kit = next(s for s in gen.load(repo).kit_servers() if s.name == server)
    assert yaml.safe_load((repo / "services" / kit.service / "policy.yaml").read_text())["roles"][group] == "*"


def test_publish_refuses_a_catalog_edited_on_disk_meanwhile(repo: Path) -> None:
    state = portal.state(repo)
    (repo / gen.CATALOG).write_text((repo / gen.CATALOG).read_text() + "\n")
    with pytest.raises(portal.ConflictError):
        portal.publish(state["catalog"], state["base"], repo)


def test_publish_refuses_what_the_generator_refuses(repo: Path) -> None:
    state = portal.state(repo)
    data = state["catalog"]
    data["groups"]["visitor"] = {}
    with pytest.raises(gen.CatalogError):
        portal.publish(data, state["base"], repo)
    assert (repo / gen.CATALOG).read_text() == (REPO / gen.CATALOG).read_text()


TOKEN = "test-token"  # a test-only portal token


@pytest.fixture
def served(repo: Path) -> Iterator[int]:
    server = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    server.RequestHandlerClass = portal.make_handler(TOKEN, server.server_port, repo)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()


def request(port: int, method: str, path: str, *, headers: dict[str, str], body: Any = None) -> tuple[int, Any]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    payload = json.dumps(body).encode() if body is not None else None
    conn.request(method, path, body=payload, headers={"Host": f"127.0.0.1:{port}", **headers})
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    is_json = response.getheader("Content-Type", "").startswith("application/json")
    return response.status, json.loads(raw) if is_json else raw.decode()


def test_the_page_carries_its_token_and_a_nonce_only_csp(served: int) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", served, timeout=10)
    conn.request("GET", "/", headers={"Host": f"localhost:{served}"})
    response = conn.getresponse()
    page = response.read().decode()
    csp = response.getheader("Content-Security-Policy", "")
    conn.close()
    assert response.status == 200 and f'"{TOKEN}"' in page and "__NONCE__" not in page
    assert "script-src 'nonce-" in csp and "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]


def test_the_api_answers_only_its_own_page(served: int) -> None:
    token = {"X-Portal-Token": TOKEN}
    assert request(served, "GET", "/api/state", headers={})[0] == 403
    assert request(served, "GET", "/api/state", headers={"X-Portal-Token": "guess"})[0] == 403
    assert request(served, "GET", "/api/state", headers={**token, "Host": "evil.example:80"})[0] == 421
    status, state = request(served, "GET", "/api/state", headers=token)
    assert status == 200 and state["in_sync"]
    form = {**token, "Content-Type": "text/plain"}
    assert request(served, "POST", "/api/publish", headers=form, body={"catalog": state["catalog"]})[0] == 415
    json_headers = {**token, "Content-Type": "application/json"}
    status, review = request(served, "POST", "/api/review", headers=json_headers, body={"catalog": state["catalog"]})
    assert status == 200 and review["changed"] == [] and "_files" not in review
    broken = {**state["catalog"], "servers": {"bad-name": {}}}
    assert request(served, "POST", "/api/review", headers=json_headers, body={"catalog": broken})[0] == 422
    stale = {"catalog": state["catalog"], "base": "0" * 64}
    assert request(served, "POST", "/api/publish", headers=json_headers, body=stale)[0] == 409
