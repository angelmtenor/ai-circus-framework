"""
server.py
---------

crm-mcp's tools: business logic only. The kit has already checked the caller's token, applied the
policy and holds the per-API token; a tool asks `kit.context().api` and never sees a token, a user id
or a tenant id (rule R8, docs/adr/0022). Tools are designed for a model, not one per endpoint.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp.exceptions import ToolError
from mcp_kit import McpKit
from pydantic import Field

INSTRUCTIONS = (
    "Customers of the organisation's shops, as the signed-in user may see them: an analyst sees every shop, "
    "a store manager only their own. E-mails come masked and names shortened on purpose. "
    "Use search_customers to find customers, get_customer for one customer's details, and top_customers "
    "for the most valuable ones."
)


def register_tools(kit: McpKit) -> None:
    """crm-mcp's three read-only tools."""

    def api() -> Any:
        client = kit.context().api
        if client is None:  # only if the server was built without an API (tests of the guard alone)
            raise ToolError("This server has no API configured.")
        return client

    @kit.tool()
    async def search_customers(
        query: Annotated[
            str, Field(max_length=100, description="Name, city, store id (e.g. S012) or tier; empty for all.")
        ] = "",
        limit: Annotated[int, Field(ge=1, le=50, description="How many customers to return.")] = 10,
    ) -> dict[str, Any]:
        """Find customers by name, city, home store or tier. Returns masked contact details."""
        return await api().get("/customers", params={"q": query, "limit": limit})

    @kit.tool()
    async def get_customer(
        customer_id: Annotated[str, Field(pattern=r"^C\d{3}$", description="A customer id such as C012.")],
    ) -> dict[str, Any]:
        """One customer's details, if the signed-in user may see that customer."""
        return await api().get(f"/customers/{customer_id}")

    @kit.tool()
    async def top_customers(
        limit: Annotated[int, Field(ge=1, le=20, description="How many customers to return.")] = 5,
    ) -> dict[str, Any]:
        """The customers with the highest lifetime value, across the shops the user may see."""
        return await api().get("/customers", params={"sort": "lifetime_value", "limit": limit})
