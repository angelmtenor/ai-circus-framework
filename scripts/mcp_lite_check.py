#!/usr/bin/env python3
"""
mcp_lite_check.py
-----------------

`make k3s-mcp-lite-verify` (docs/adr/0027): checks the MCP lite profile end to end without a model in the loop.

For each demo user it signs in through Keycloak's own login pages (authorization code + PKCE, as the chat UI
does, with the chat UI's client) and then, through the MCP gateway with that user's token, against every kit
server of mcp/catalog.yaml:

- the tools listed are exactly the ones the catalog grants the user's role;
- a granted tool that needs no arguments runs, and a tool the role is not granted is refused;
- without a token the server lists no tools.

With the observability bundle deployed (--metrics/--logs/--traces, make k3s-mcp-lite OBS=1), it then checks that those
calls reached it: the kit's tool-call metrics with both outcomes (ok and denied), each kit server's audit lines in the
logs and its traces, that the trace context the check sent through the gateway was continued by the kit server (one
trace, docs/adr/0028), and that no stored log line of the cluster carries a token (a JWT) or an email address.

Each server exchanges the token for its own API (Level 3: DPoP, live checks, mutual TLS), so a passing call covers
that path too. Tokens and passwords are never printed. Needs Traefik on 127.0.0.1:80 (the lite cluster) and the
gateway forwarded to --gateway (scripts/k3s_mcp_lite.sh verify does both).

Usage: python3 scripts/mcp_lite_check.py --gateway http://127.0.0.1:18033 [--client open-webui|librechat]
       [--metrics http://…:8428 --logs http://…:3100 --traces http://…:3200]

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import http.cookies
import importlib.util
import json
import re
import secrets
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

REPO = Path(__file__).resolve().parents[1]
REALM = "ai-circus"
KEYCLOAK = "http://keycloak.localhost"
TRAEFIK = "http://127.0.0.1:80"  # *.localhost resolves in browsers only: send the name as the Host header instead
CLIENTS = {
    "open-webui": ("CHAT_UI_OIDC_CLIENT_SECRET", "http://chat.localhost/oauth/oidc/callback"),
    "librechat": ("LIBRECHAT_OIDC_CLIENT_SECRET", "http://librechat.localhost/oauth/openid/callback"),
}
SCOPE = "openid profile email organization"
PROTOCOL_VERSION = "2025-06-18"


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class CheckError(AssertionError):
    pass


def via_traefik(url: str) -> tuple[str, dict[str, str]]:
    """The same request sent to Traefik on loopback, with the *.localhost name as its Host header."""
    parts = urlsplit(url)
    target = f"{TRAEFIK}{parts.path}" + (f"?{parts.query}" if parts.query else "")
    return target, {"Host": parts.netloc}


class Browserish:
    """HTTP through Traefik that carries Keycloak's cookies as a browser does on a *.localhost name.

    Keycloak marks its session cookies Secure; browsers treat http://*.localhost as a secure origin and send them,
    an HTTP library does not over plain http, and Keycloak then answers 400 (no login session).
    """

    def __init__(self, http: httpx.Client) -> None:
        self.http, self.cookies = http, {}

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        target, headers = via_traefik(url)
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        response = self.http.request(method, target, headers=headers, **kwargs)
        self.http.cookies.clear()  # only this class keeps cookies
        for header in response.headers.get_list("set-cookie"):
            jar: http.cookies.SimpleCookie = http.cookies.SimpleCookie()
            jar.load(header)
            for name, morsel in jar.items():
                if morsel["max-age"] == "0" or not morsel.value:
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = morsel.value
        return response


def sign_in(username: str, password: str, client_id: str, client_secret: str, redirect_uri: str) -> str:
    """A user access token from Keycloak's real login pages (identity-first: username, then password)."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "state": secrets.token_urlsafe(16),
            "nonce": secrets.token_urlsafe(16),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    with httpx.Client(follow_redirects=False, timeout=30) as client:
        browser = Browserish(client)
        response = browser.request("GET", f"{KEYCLOAK}/realms/{REALM}/protocol/openid-connect/auth?{query}")
        for _ in range(4):  # the login form, maybe a second page for the password, then the redirect
            if response.status_code in (302, 303):
                location = response.headers["location"]
                if location.startswith(redirect_uri):
                    code = parse_qs(urlsplit(location).query).get("code", [""])[0]
                    if not code:
                        raise CheckError(f"{username}: Keycloak redirected without a code")
                    break
                response = browser.request("GET", location)
                continue
            form = re.search(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', response.text)
            if response.status_code != 200 or form is None:
                raise CheckError(f"{username}: no Keycloak login form (HTTP {response.status_code})")
            fields = {"username": username}
            if 'name="password"' in response.text:
                fields["password"] = password
            response = browser.request("POST", html.unescape(form.group(1)), data=fields)
        else:
            raise CheckError(f"{username}: the sign-in did not finish (wrong password?)")
        token = browser.request(
            "POST",
            f"{KEYCLOAK}/realms/{REALM}/protocol/openid-connect/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
                "code_verifier": verifier,
            },
        )
        if token.status_code != 200:
            raise CheckError(f"{username}: token request refused (HTTP {token.status_code})")
        return str(token.json()["access_token"])


class McpSession:
    """A minimal streamable-HTTP MCP client: initialize, then JSON-RPC calls on the session it opened."""

    def __init__(self, http: httpx.Client, url: str, token: str | None, traceparent: str | None = None) -> None:
        self.http, self.url, self.next_id = http, url, 0
        self.headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        if traceparent:  # W3C trace context, as an instrumented chat UI sends it
            self.headers["traceparent"] = traceparent
        reply = self._post({"method": "initialize", "params": self._init_params()}, expect_reply=True)
        if session := reply.get("_session"):
            self.headers["mcp-session-id"] = session
        self._post({"method": "notifications/initialized"}, expect_reply=False)

    @staticmethod
    def _init_params() -> dict[str, Any]:
        return {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "mcp-lite-check"}}

    def _post(self, message: dict[str, Any], *, expect_reply: bool) -> dict[str, Any]:
        body: dict[str, Any] = {"jsonrpc": "2.0", **message}
        if expect_reply:
            self.next_id += 1
            body["id"] = self.next_id
        response = self.http.post(self.url, headers=self.headers, json=body)
        if not expect_reply:
            return {}
        if response.status_code != 200:
            return {"_status": response.status_code}
        reply = self._parse(response)
        if session := response.headers.get("mcp-session-id"):
            reply["_session"] = session
        return reply

    @staticmethod
    def _parse(response: httpx.Response) -> dict[str, Any]:
        if "text/event-stream" not in response.headers.get("content-type", ""):
            return dict(response.json())
        events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
        replies = [e for e in events if "id" in e]
        return dict(replies[-1]) if replies else {}

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._post({"method": method, "params": params}, expect_reply=True)

    def close(self) -> None:
        if "mcp-session-id" in self.headers:
            self.http.delete(self.url, headers=self.headers)


def listed_tools(reply: dict[str, Any], server: str) -> tuple[dict[str, dict[str, Any]], str]:
    """Tool name (without the gateway's `<server>-` prefix) -> its definition, and the prefix the gateway used."""
    tools = (reply.get("result") or {}).get("tools") or []
    prefix = f"{server}-" if tools and all(t["name"].startswith(f"{server}-") for t in tools) else ""
    return {t["name"].removeprefix(prefix): t for t in tools}, prefix


def refused(reply: dict[str, Any]) -> bool:
    result = reply.get("result") or {}
    return "error" in reply or bool(result.get("isError")) or "_status" in reply


def new_traceparent() -> tuple[str, str]:
    """A sampled W3C traceparent and its trace id."""
    trace_id = secrets.token_hex(16)
    return f"00-{trace_id}-{secrets.token_hex(8)}-01", trace_id


def check_user(
    http: httpx.Client,
    gateway: str,
    catalog: Any,
    user: dict[str, Any],
    tenant: str,
    token: str,
    sent_traces: dict[str, str] | None = None,
) -> list[str]:
    """`sent_traces` collects, per kit server, the trace id this check sent through the gateway."""
    lines = []
    for server in catalog.kit_servers():
        url = f"{gateway}/mcp/{server.name}"
        wanted: set[str] = set()
        if server.tenants is None or tenant in server.tenants:
            for role in user["roles"]:
                grant = catalog.grants(server).get(role)
                wanted |= set(server.tools) if grant == "*" else set(grant or ())
        traceparent, trace_id = new_traceparent()
        session = McpSession(http, url, token, traceparent)
        if sent_traces is not None:
            sent_traces.setdefault(server.service, trace_id)
        try:
            tools, prefix = listed_tools(session.call("tools/list", {}), server.name)
            if set(tools) != wanted:
                raise CheckError(
                    f"{user['username']} on {server.name}: listed {sorted(tools)}, catalog grants {sorted(wanted)}"
                )
            runnable = [n for n, t in tools.items() if not (t.get("inputSchema") or {}).get("required")]
            if runnable:
                name = sorted(runnable)[0]
                if refused(session.call("tools/call", {"name": prefix + name, "arguments": {}})):
                    raise CheckError(f"{user['username']} on {server.name}: granted tool {name} failed")
            denied = sorted(set(server.tools) - wanted)
            # The listed names show the gateway's naming, so a refusal of a denied tool is the policy's.
            denied_call = {"name": prefix + denied[0], "arguments": {}} if denied else {}
            if denied and tools and not refused(session.call("tools/call", denied_call)):
                raise CheckError(f"{user['username']} on {server.name}: {denied[0]} ran without a grant")
            ran = f", ran {sorted(runnable)[0]}" if runnable else ""
            no = f", {denied[0]} refused" if denied and tools else ""
            lines.append(f"✓ {user['username']} on {server.name}: {len(tools)} tools as the catalog grants{ran}{no}")
        finally:
            session.close()
    return lines


# Signals arrive on the scrape interval (metrics) and in batches (logs, traces): poll before calling one missing.
SIGNAL_WAIT_SECONDS = 120
SIGNAL_WINDOW_SECONDS = 15 * 60
# A JWT is three base64url parts; its first two always start with `eyJ` ('{"' encoded). Never in a log line, and
# neither is an email address: the collector masks both (<token>, <email>) before anything is stored.
JWT_IN_LOGS = r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}"
EMAIL_IN_LOGS = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"


def trace_services(reply: dict[str, Any]) -> set[str]:
    """The service names in a Tempo trace (OTLP JSON; `batches` in older Tempo versions)."""
    names = set()
    for batch in reply.get("batches") or reply.get("resourceSpans") or []:
        for attribute in (batch.get("resource") or {}).get("attributes") or []:
            if attribute.get("key") == "service.name":
                names.add((attribute.get("value") or {}).get("stringValue", ""))
    return names


def eventually(what: str, probe: Callable[[], str | None]) -> str:
    """Poll `probe` until it returns a description of what it found, or fail after SIGNAL_WAIT_SECONDS."""
    deadline = time.monotonic() + SIGNAL_WAIT_SECONDS
    while True:
        found = probe()
        if found:
            return found
        if time.monotonic() > deadline:
            raise CheckError(f"{what}: nothing after {SIGNAL_WAIT_SECONDS} s")
        time.sleep(5)


def check_signals(
    http: httpx.Client, catalog: Any, metrics: str, logs: str, traces: str, sent_traces: dict[str, str]
) -> list[str]:
    """The calls just made reached the observability bundle, the kit servers continued the trace context sent through
    the gateway, and no stored log line carries a token or an email address."""
    lines = []

    def outcomes() -> str | None:
        reply = http.get(f"{metrics}/api/v1/query", params={"query": "sum by (outcome) (mcp_kit_tool_calls_total)"})
        seen = {r["metric"].get("outcome"): float(r["value"][1]) for r in reply.json()["data"]["result"]}
        if seen.get("ok", 0) > 0 and seen.get("denied", 0) > 0:
            return ", ".join(f"{k} {int(v)}" for k, v in sorted(seen.items()))
        return None

    lines.append(f"✓ metrics: mcp_kit_tool_calls_total by outcome: {eventually('tool-call metrics', outcomes)}")
    now = time.time()
    window = {"start": str(int((now - SIGNAL_WINDOW_SECONDS) * 1e9)), "end": str(int((now + 60) * 1e9))}
    for server in catalog.kit_servers():

        def audit(service: str = server.service) -> str | None:
            query = f'{{service_name="{service}"}} | logger="mcp_kit.audit"'
            reply = http.get(f"{logs}/loki/api/v1/query_range", params={"query": query, "limit": 200, **window})
            count = sum(len(stream["values"]) for stream in reply.json()["data"]["result"])
            return f"{count} lines" if count else None

        def spans(service: str = server.service) -> str | None:
            params = {"tags": f"service.name={service}", "limit": 20, "start": int(now - SIGNAL_WINDOW_SECONDS)}
            reply = http.get(f"{traces}/api/search", params={**params, "end": int(time.time()) + 60})
            found = reply.json().get("traces") or []
            return f"{len(found)} traces" if found else None

        lines.append(f"✓ logs: {server.service} audit lines: {eventually(f'{server.service} audit lines', audit)}")
        lines.append(f"✓ traces: {server.service}: {eventually(f'{server.service} traces', spans)}")
        if server.service in sent_traces:

            def continued(service: str = server.service, trace_id: str = sent_traces[server.service]) -> str | None:
                reply = http.get(f"{traces}/api/traces/{trace_id}")
                found = trace_services(reply.json()) if reply.status_code == 200 else set()
                return ", ".join(sorted(found)) if service in found else None

            found = eventually(f"{server.service} continuing the trace sent through the gateway", continued)
            lines.append(f"✓ traces: the trace sent through the gateway continues in {found}")
    for what, pattern in (("a token (JWT)", JWT_IN_LOGS), ("an email address", EMAIL_IN_LOGS)):
        leak = http.get(
            f"{logs}/loki/api/v1/query_range",
            params={"query": f'{{service_name=~".+"}} |~ `{pattern}`', "limit": 5, **window},
        )
        leaked = [s["stream"].get("service_name", "?") for s in leak.json()["data"]["result"]]
        if leaked:
            raise CheckError(f"{what} appears in the stored logs of {sorted(set(leaked))}")
    masked = http.get(
        f"{logs}/loki/api/v1/query",
        params={
            "query": f'sum(count_over_time({{service_name=~".+"}} |~ `<email>|<token>` [{SIGNAL_WINDOW_SECONDS}s]))'
        },
    )
    counts = masked.json()["data"]["result"]
    count = int(float(counts[0]["value"][1])) if counts else 0
    lines.append(f"✓ logs: no stored line carries a token or an email address ({count} masked by the collector)")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the MCP lite profile end to end (docs/adr/0027).")
    parser.add_argument("--gateway", required=True, help="the MCP gateway, e.g. http://127.0.0.1:18033")
    parser.add_argument("--client", choices=sorted(CLIENTS), default="open-webui", help="the chat UI's client")
    parser.add_argument("--env-file", type=Path, default=REPO / ".env")
    parser.add_argument("--metrics", help="VictoriaMetrics (the observability bundle), e.g. http://127.0.0.1:8428")
    parser.add_argument("--logs", help="Loki, e.g. http://127.0.0.1:3100")
    parser.add_argument("--traces", help="Tempo, e.g. http://127.0.0.1:3200")
    args = parser.parse_args()
    signals = (args.metrics, args.logs, args.traces)
    if any(signals) and not all(signals):
        parser.error("--metrics, --logs and --traces go together")

    identity = _script("chat_ui_identity")
    generator = _script("generate_mcp_catalog")
    env = identity.read_env(args.env_file)
    catalog = generator.load()
    secret_key, redirect_uri = CLIENTS[args.client]
    if not catalog.kit_servers():
        print("❌ mcp/catalog.yaml has no kit server to check", file=sys.stderr)
        return 1
    sent_traces: dict[str, str] = {}
    try:
        with httpx.Client(timeout=60) as http:
            for server in catalog.kit_servers():
                session = McpSession(http, f"{args.gateway}/mcp/{server.name}", None)
                try:
                    if listed_tools(session.call("tools/list", {}), server.name)[0]:
                        raise CheckError(f"{server.name}: tools listed without a token")
                finally:
                    session.close()
                print(f"✓ {server.name}: no token, no tools")
            for user in identity.DEMO_USERS:
                password = env["CHAT_UI_DEMO_PASSWORD"]
                token = sign_in(user["username"], password, args.client, env[secret_key], redirect_uri)
                print(f"✓ {user['username']} signed in through Keycloak ({args.client} client)")
                for line in check_user(http, args.gateway, catalog, user, identity.ORG["alias"], token, sent_traces):
                    print(line)
            if all(signals):
                for line in check_signals(http, catalog, args.metrics, args.logs, args.traces, sent_traces):
                    print(line)
    except (CheckError, httpx.HTTPError, KeyError, ValueError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    print("✓ MCP lite stack verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
