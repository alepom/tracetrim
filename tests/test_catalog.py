"""The proxy reads the whole tool catalogue of every upstream (1.1.5).

Up to 1.1.4 it asked each server for one page of tools: a server that lists its tools in pages (`nextCursor`)
lost every tool after the first page, which the client never saw and could not call. The servers are also
listed together now, and the names do not depend on which one answers first.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "tests" / "fixtures" / "paged_server.py"
CHECKOUT_ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def upstream(name, tools, size=2, delay=0.0):
    return {"command": sys.executable, "args": [str(SERVER)],
            "env": {**os.environ, "PAGED_NAME": name, "PAGED_TOOLS": ",".join(tools),
                    "PAGED_SIZE": str(size), "PAGED_DELAY": str(delay)}}


def through_proxy(tmp_path, servers, calls):
    """The tool names the proxy lists, and the replies to `calls`."""
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"mcpServers": servers}))
    params = StdioServerParameters(command=sys.executable, cwd=str(ROOT), env=CHECKOUT_ENV,
                                   args=["-m", "tracetrim.cli", "proxy", "--servers", str(f), "--quiet"])

    async def main():
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            replies = [json.loads((await s.call_tool(c, {})).content[0].text) for c in calls]
            return names, replies
    return asyncio.run(asyncio.wait_for(main(), 60))


def test_a_tool_on_a_later_page_is_listed_and_can_be_called(tmp_path):
    names, replies = through_proxy(tmp_path, {"one": upstream("one", ["a", "b", "c", "d", "e"])}, ["e"])
    assert names == ["a", "b", "c", "d", "e"]
    assert replies == [{"server": "one", "tool": "e"}]


def test_the_names_do_not_depend_on_which_server_answers_first(tmp_path):
    def run(slow):
        servers = {"alpha": upstream("alpha", ["search", "only_alpha", "late_alpha"], delay=0.8 if slow == "alpha" else 0),
                   "beta": upstream("beta", ["search", "only_beta"], delay=0.8 if slow == "beta" else 0)}
        return through_proxy(tmp_path / slow, servers, ["alpha__search", "beta__search", "late_alpha"])
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    first, second = run("alpha"), run("beta")
    assert first == second
    names, replies = first
    assert sorted(names) == ["alpha__search", "beta__search", "late_alpha", "only_alpha", "only_beta"]
    assert replies == [{"server": "alpha", "tool": "search"}, {"server": "beta", "tool": "search"},
                       {"server": "alpha", "tool": "late_alpha"}]
