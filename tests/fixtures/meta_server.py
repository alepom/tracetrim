"""A minimal upstream MCP server whose results carry `_meta` and structured content,
to check that the proxy passes them through (tests/test_proxy_passthrough.py)."""
import asyncio
import json

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

PAYLOAD = {"items": [{"keyword": "spa weekend", "search_volume": 1600,
                      **{f"note_{i}": f"internal remark number {i}" for i in range(8)}}]}


async def list_tools(ctx, params):
    return types.ListToolsResult(tools=[types.Tool(name="volumes", description="volumes", inputSchema={"type": "object"})])


async def call_tool(ctx, params):
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(PAYLOAD))],
                                structuredContent=PAYLOAD, _meta={"trace_id": "abc-123"})


async def main():
    server = Server("meta", on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
