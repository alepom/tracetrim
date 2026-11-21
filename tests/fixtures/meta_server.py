"""A minimal upstream MCP server whose results carry `_meta` and structured content,
to check that the proxy passes them through (tests/test_proxy_passthrough.py)."""
import asyncio
import json
import os

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

PAYLOAD = {"items": [{"keyword": "spa weekend", "search_volume": 1600,
                      **{f"note_{i}": f"internal remark number {i}" for i in range(8)}}]}


async def list_tools(ctx, params):
    return types.ListToolsResult(tools=[types.Tool(name="volumes", description="volumes", inputSchema={"type": "object"})])


async def call_tool(ctx, params):
    # META_GATE (a directory) and the argument `gate`: the call says it arrived (<gate>.arrived) and returns once
    # <gate>.go exists, to hold a call in the upstream (tests/test_background_relearn.py).
    gate = (params.arguments or {}).get("gate")
    if gate and os.environ.get("META_GATE"):
        folder = os.environ["META_GATE"]
        open(os.path.join(folder, f"{gate}.arrived"), "w").close()
        while not os.path.exists(os.path.join(folder, f"{gate}.go")):
            await asyncio.sleep(0.01)
    # META_INDENT: send the text indented, as many servers do, so that the proxy re-serialises
    # it even when it passes the result whole (tests/test_proxy_passthrough.py).
    indent = int(os.environ["META_INDENT"]) if os.environ.get("META_INDENT") else None
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(PAYLOAD, indent=indent))],
                                structuredContent=PAYLOAD, _meta={"trace_id": "abc-123"})


async def main():
    server = Server("meta", on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
