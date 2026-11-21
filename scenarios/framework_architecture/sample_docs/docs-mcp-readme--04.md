# MCP Lite Stack - Run it

Source: docs/mcp/README.md (this repository)

The demo tenant `lite-demo` has two users, `analyst@lite-demo.local` (all CRM tools, every shop) and
`manager@lite-demo.local` (store S012: no `top_customers`). Their password is `CHAT_UI_DEMO_PASSWORD` in `.env`,
generated with every other key by the identity step that each target below runs.

**In the full framework** (k3s):

```bash
make k3s-mcp-examples-up     # crm-mcp + crm-api, Level 3
make k3s-librechat-up        # or: make k3s-chat-ui-up (Open WebUI); one chat UI at a time on 16 GB
make k3s-obs-up              # optional: metrics, logs, traces, Grafana at grafana.localhost
```

Open `http://librechat.localhost`, sign in, pick **Customers CRM** under **MCP Servers** and ask for the top
customers: the analyst's answer runs `top_customers`, the store manager's cannot. MCP clients on this machine can use
the doors and the gateway directly with `make k3s-mcp-portforward` (`:8031`, `:8032`, gateway `:8033`).

**Alone, as the lite profile** (its own k3d cluster; pause the framework's first, both use port 80):

```bash
make k3s-pause
make k3s-mcp-lite                    # CHAT_UI=librechat for LibreChat; OBS=1 adds observability
make k3s-mcp-lite-verify             # sign-ins, grants, a granted and a refused call; with OBS=1 also the signals
make k3s-mcp-lite-down && make k3s-resume
```

`make k3s-mcp-lite-verify` needs no model: each demo user signs in through Keycloak's own login pages, then every
kit server's tools are listed and called through the gateway with that user's token and compared with the catalog.
With `OBS=1` it also finds the calls' metrics, audit lines and traces, follows a trace it sent through the gateway into
the server and its API, and fails if any stored log line carries a token or an email address.

<p align="center">
  <img src="../screenshots/mcp/librechat-analyst.webp" alt="LibreChat, analyst: ran crm-top_customers, three CRM tools listed" width="49%">
  <img src="../screenshots/mcp/librechat-store-manager.webp" alt="LibreChat, store manager: ran crm-search_customers, two CRM tools listed" width="49%">
</p>
