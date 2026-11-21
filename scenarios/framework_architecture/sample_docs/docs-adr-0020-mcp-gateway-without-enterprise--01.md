# 0020 — One MCP gateway in front of the doors, open-source features only

Source: docs/adr/0020-mcp-gateway-without-enterprise.md (this repository)

Builds on ADR 0019 (MCP doors) and on llm-gateway being the platform's only path to models.

**Context.** Each MCP door has its own URL (`rag-agent`'s `/mcp/{slug}`, `data-api`'s `/mcp/{slug}`).
A client that wants several doors needs several connections, and a small deployment (a chat UI, an AI
gateway, an identity provider and one MCP server per API) wants one address for every tool. llm-gateway
already runs LiteLLM, whose open-source code (MIT, outside its `enterprise/` folder) includes an MCP
gateway. The LiteLLM features that validate JWTs, map token claims to teams and MCP access groups, or
exchange tokens inside the gateway are Enterprise features and need its database: none is used here.

**Decision.**
- **llm-gateway is the AI gateway**: `/v1` for models (unchanged) and `/mcp/{server}[,{server}…]` for
  tools, one address for every door it lists.
- **Every door entry is `auth_type: true_passthrough`.** For a path that names only such servers, LiteLLM
  skips its own key check and forwards the caller's `Authorization` header unchanged, so each door still
  validates the token and decides tenant, entitlement, role and tools (ADR 0019). The gateway holds no
  credential for a door and never decides access.
- **Pinned by tests** (`services/llm-gateway/tests/test_mcp_gateway.py`): every entry is passthrough,
  carries no credential field and forwards no credential header; every entry is an in-cluster door of a
  scenario that declares `mcp.expose`; and the config, loaded into LiteLLM's own MCP manager, makes door
  paths skip the gateway key while `/mcp` and unknown names keep it. LiteLLM's MCP module is marked
  experimental, so these contract tests are what make an upgrade safe.
- **Entries are named after the scenario slug**: the name is the path segment and the tool prefix
  (`surf_retail-query_metric`), and cannot contain `-`. The list is static in `litellm_config.yaml`;
  generating it from the scenarios is a later step.
- **One credential per door.** LiteLLM never fans a single `Authorization` header out to several
  upstreams (RFC 9700 cross-resource replay): when a URL names several doors, the client sends one
  `x-mcp-{slug}-authorization: Bearer …` header per door. With one door in the URL, or for a tool call
  (always addressed to one door), the plain `Authorization` header is forwarded. A test pins this too.
- **A door that refuses drops out**: in a combined `tools/list`, a door answering 401/403 contributes no
  tools and the other doors still answer.
- **Headers a door honours are forwarded by name** (`extra_headers`): `data-api`'s `X-Data-View-As`
  pair for platform keys. Never a credential header.
- **A path that names no server** (`/mcp`), or mixes in a server of another auth type, falls back to
  LiteLLM's own key check, which needs its database for an aggregate listing (here it answers 400):
  clients name the doors they want. Naming only some doors is also how a group of users sees only its
  doors. The MCP handshake on a door path needs no token; every listing and call reaches the doors,
  which refuse without a valid one.
- **Exposure is unchanged**: llm-gateway keeps no Traefik router, since it holds the provider keys.
  MCP clients on this machine use `make k3s-mcp-portforward` (`localhost:8033`). Exposing the gateway
  beyond the machine waits for OAuth for external clients (ADR 0019, step 1) and for a decision on
  serving the MCP face from a gateway instance without provider keys.
