"""An upstream MCP server that lists its tools in pages, as the protocol allows, to check that the proxy reads
the whole catalogue (tests/test_catalog.py). PAGED_NAME names the server in its replies, PAGED_TOOLS lists its
tools (comma separated), PAGED_SIZE is the page size, PAGED_DELAY a pause before every listing reply."""
import asyncio
import json
import os

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

NAME = os.environ.get("PAGED_NAME", "paged")
TOOLS = os.environ.get("PAGED_TOOLS", "a,b,c").split(",")
SIZE = int(os.environ.get("PAGED_SIZE", "2"))
DELAY = float(os.environ.get("PAGED_DELAY", "0"))


async def list_tools(ctx, params):
    await asyncio.sleep(DELAY)
    start = int(params.cursor) if params is not None and params.cursor else 0
    page = TOOLS[start:start + SIZE]
    more = start + SIZE < len(TOOLS)
    return types.ListToolsResult(
        tools=[types.Tool(name=t, description=f"{t} of {NAME}", inputSchema={"type": "object"}) for t in page],
        next_cursor=str(start + SIZE) if more else None)


async def call_tool(ctx, params):
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps({"server": NAME, "tool": params.name}))])


async def main():
    server = Server(NAME, on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
