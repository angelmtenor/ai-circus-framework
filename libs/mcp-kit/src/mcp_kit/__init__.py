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

from mcp_kit.dpop import DpopKey
from mcp_kit.exchange import ExchangeRefusedError, ExchangeSettings, TokenExchanger
from mcp_kit.identity import Identity, TokenRejectedError, TokenVerifier
from mcp_kit.introspection import (
    IntrospectionSettings,
    IntrospectionUnavailableError,
    TokenInactiveError,
    TokenIntrospector,
)
from mcp_kit.policy import Policy, PolicyError
from mcp_kit.server import READ_ONLY, ApiClient, ApiTls, Level3ConfigError, McpKit, ToolContext, configure_audit_output

__all__ = [
    "READ_ONLY",
    "ApiClient",
    "ApiTls",
    "DpopKey",
    "ExchangeRefusedError",
    "ExchangeSettings",
    "Identity",
    "IntrospectionSettings",
    "IntrospectionUnavailableError",
    "Level3ConfigError",
    "McpKit",
    "Policy",
    "PolicyError",
    "TokenExchanger",
    "TokenInactiveError",
    "TokenIntrospector",
    "TokenRejectedError",
    "TokenVerifier",
    "ToolContext",
    "configure_audit_output",
]
