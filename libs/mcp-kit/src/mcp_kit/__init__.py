"""
mcp_kit
-------

The MCP server kit (docs/adr/0022): everything an MCP server in front of an existing API needs for
security, so its own code is its tools only.

    from mcp_kit import McpKit

    kit = McpKit(name="crm-mcp", instructions="…", verifier=…, policy=…, exchanger=…, api_base_url=…)

    @kit.tool()
    async def search_customers(query: str) -> list[dict]:
        ctx = kit.context()          # who is asking (read-only) + an API client holding their T2
        return await ctx.api.get("/customers", params={"q": query})

    app = kit.asgi_app()             # /healthz + the guarded /mcp

Author: Angel Martinez-Tenor
"""

from mcp_kit.exchange import ExchangeRefusedError, ExchangeSettings, TokenExchanger
from mcp_kit.identity import Identity, TokenRejectedError, TokenVerifier
from mcp_kit.policy import Policy, PolicyError
from mcp_kit.server import READ_ONLY, ApiClient, McpKit, ToolContext, configure_audit_output

__all__ = [
    "READ_ONLY",
    "ApiClient",
    "ExchangeRefusedError",
    "ExchangeSettings",
    "Identity",
    "McpKit",
    "Policy",
    "PolicyError",
    "TokenExchanger",
    "TokenRejectedError",
    "TokenVerifier",
    "ToolContext",
    "configure_audit_output",
]
