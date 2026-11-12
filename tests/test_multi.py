"""One proxy in front of several MCP servers (0.3.1).

The end-to-end test starts two simulated servers (billing, Stripe-shaped, and
code hosting, GitHub-shaped) behind one proxy in automatic mode, and replays
recorded runs of both agents as if one agent used both servers: no model, no
spend.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from tracetrim.proxy import Options, load_servers, route_names

ROOT = Path(__file__).resolve().parent.parent

# The proxy runs from this checkout's source, whatever tracetrim the interpreter
# has installed.
CHECKOUT_ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def test_names_are_kept_unless_two_servers_share_one():
    routes = route_names({"billing": ["list_charges", "search"], "code": ["list_issues", "search"]})
    assert routes["list_charges"] == ("billing", "list_charges")
    assert routes["billing__search"] == ("billing", "search")
    assert routes["code__search"] == ("code", "search")
    assert "search" not in routes


def test_servers_file_is_the_clients_mcpServers_block(tmp_path, monkeypatch):
    monkeypatch.setenv("BILLING_KEY", "s3cret")
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"mcpServers": {
        "billing": {"url": "https://billing.example/mcp", "headers": {"Authorization": "Bearer ${BILLING_KEY}"}},
        "code": {"command": "python", "args": ["-m", "code_server"]}}}))
    up = Options(servers=load_servers(f)).upstreams()
    assert up["billing"]["headers"]["Authorization"] == "Bearer s3cret"
    assert up["code"] == {"command": "python", "args": ["-m", "code_server"]}


def _runs(n, traces):
    """Run i = the calls of C4 run Ai, then those of C5 run Ai; the answer is both answers."""
    runs = []
    for i in range(1, n + 1):
        events = [json.loads(line) for case in ("C4", "C5")
                  for line in (traces / case / f"A{i}.jsonl").open(encoding="utf-8")]
        calls = [e for e in events if e["kind"] == "call"]
        answer = "\n".join(e["text"] for e in events if e["kind"] == "message")
        runs.append((calls, answer))
    return runs


def test_one_proxy_learns_and_trims_tools_of_two_servers(tmp_path, traces, specs):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    servers = tmp_path / "mcp.json"
    servers.write_text(json.dumps({"mcpServers": {
        "billing": {"command": sys.executable, "args": ["-m", "experiments.simulated.server_c4"], "cwd": str(ROOT)},
        "code": {"command": sys.executable, "args": ["-m", "experiments.simulated.server_c5"], "cwd": str(ROOT)}}}))
    work = tmp_path / "auto"
    params = StdioServerParameters(command=sys.executable, cwd=str(ROOT), env=CHECKOUT_ENV, args=[
        "-m", "tracetrim.cli", "proxy", "--servers", str(servers), "--auto", str(work), "--min-runs", "2", "--quiet"])

    async def main():
        last = []
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            for calls, answer in _runs(3, traces):
                last = []
                for c in calls:
                    args = c["arguments"]
                    res = await s.call_tool(c["tool"], json.loads(args) if isinstance(args, str) else args)
                    last.append((c["tool"], res.content[0].text))
                await s.call_tool("tracetrim_done", {"answer": answer})
            cid = next(t.split("call_id=")[1].split()[0] for _, t in last if "\n[trimmed] " in t)
            expanded = (await s.call_tool("expand", {"call_id": cid, "fields": ["*"]})).content[0].text
            # An upstream error comes back as an error, untouched.
            bad = await s.call_tool("stripe_search_customers", {"unexpected": True})
            assert getattr(bad, "is_error", None) or getattr(bad, "isError", None)
        return names, last, expanded

    names, last, expanded = asyncio.run(main())
    assert names.count("expand") == 1 and names.count("tracetrim_done") == 1
    assert any(n.startswith("stripe_") for n in names) and any(n.startswith("gh_") for n in names)
    trimmed = {tool for tool, text in last if "\n[trimmed] " in text}
    assert any(t.startswith("stripe_") for t in trimmed) and any(t.startswith("gh_") for t in trimmed)
    # "*" brings back every hidden field of that call, not an empty object.
    assert not expanded.startswith("Error") and len(json.loads(expanded)) > 0
    report = json.loads((work / "report.json").read_text())
    assert report["auto"]["applied"] is True
