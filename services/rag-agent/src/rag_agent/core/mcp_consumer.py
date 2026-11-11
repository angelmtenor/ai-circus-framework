"""
- Title:    The chat as an MCP client: tools loaded from MCP doors (docs/adr/0019)
- Author:   Angel Martinez-Tenor

When the user picks "via MCP", the chat's agent gets its tools from the MCP doors its scenario's
`mcp.consume` lists, instead of calling the reads directly. Each door is one on this same
rag-agent instance (a scenario it serves, with `mcp.expose`), reached over loopback: that list is
the allow-list, so no URL ever comes from a scenario file or a user. The caller's own bearer token
goes with every request, so each door checks the caller's entitlement to its own scenario again.

Tools are named `mcp__<server>__<tool>`, so two servers can never clash and the UI can say a reply
came over MCP. Results that carry passages are wrapped in `<retrieved_document>` tags like
retrieve_docs' (the injection guard, and how the UI lists sources); anything else is JSON, capped.
"""

from __future__ import annotations

import json
from typing import Any

from ai_circus_shared.retrieval import wrap_untrusted
from ai_circus_shared.scenario_schema import ScenarioDefinition
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from langchain_core.tools import BaseTool, StructuredTool

from rag_agent.core.logger import get_logger

logger = get_logger(__name__)

MCP_TOOL_PREFIX = "mcp__"
MAX_RESULT_CHARS = 20_000
TIMEOUT_SECONDS = 20.0


def _client(base_url: str, server: str, authorization: str) -> Client:
    transport = StreamableHttpTransport(f"{base_url}/mcp/{server}", headers={"Authorization": authorization})
    return Client(transport, timeout=TIMEOUT_SECONDS)


def format_result(structured: Any, text: str, is_error: bool) -> str:
    """An MCP tool result as the model reads it: passages tagged as untrusted documents."""
    if is_error:
        return f"The tool failed: {text[:500]}"
    if isinstance(structured, dict) and isinstance(structured.get("passages"), list):
        passages = structured["passages"]
        if passages and all(isinstance(p, dict) for p in passages):
            return "\n\n".join(
                wrap_untrusted("retrieved_document", str(p.get("text", "")), str(p.get("source", ""))) for p in passages
            )
        if structured.get("source"):
            return wrap_untrusted(
                "retrieved_document", "\n\n".join(str(p) for p in passages), str(structured["source"])
            )
        return "No relevant documents were found for this query."
    body = json.dumps(structured, ensure_ascii=False) if structured is not None else text
    return body[:MAX_RESULT_CHARS]


def consumable_servers(definition: ScenarioDefinition, served: dict[str, ScenarioDefinition]) -> list[str]:
    """The doors this scenario's chat may use: listed in `mcp.consume`, served here, and exposing tools."""
    if definition.mcp is None:
        return []
    return [
        slug
        for slug in definition.mcp.consume
        if (door := served.get(slug)) is not None and door.mcp is not None and door.mcp.expose
    ]


def _tool(
    base_url: str, server: str, authorization: str, name: str, description: str, schema: dict[str, Any]
) -> BaseTool:
    async def _call(**arguments: Any) -> str:
        async with _client(base_url, server, authorization) as client:
            result = await client.call_tool(name, arguments, raise_on_error=False)
        text = "\n".join(getattr(block, "text", "") for block in result.content)
        return format_result(result.structured_content, text, result.is_error)

    return StructuredTool(
        name=f"{MCP_TOOL_PREFIX}{server}__{name}",
        description=f"[MCP server {server}] {description}",
        args_schema=schema,
        coroutine=_call,
    )


async def load_mcp_tools(servers: list[str], base_url: str, authorization: str | None) -> list[BaseTool]:
    """Every tool each server lists for this caller (tools/list), as LangChain tools; a door that
    refuses or fails contributes none, so the chat still answers, with what it has.
    """
    if not authorization:
        return []
    tools: list[BaseTool] = []
    for server in servers:
        try:
            async with _client(base_url, server, authorization) as client:
                listed = await client.list_tools()
        except Exception as exc:  # a door down or refusing must not break the chat
            logger.warning("MCP server {} unavailable: {}", server, type(exc).__name__)
            continue
        tools.extend(
            _tool(base_url, server, authorization, t.name, t.description or "", dict(t.input_schema)) for t in listed
        )
    return tools
