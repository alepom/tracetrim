"""An MCP proxy: the same tools as an upstream server, with trimmed outputs.

  tracetrim proxy --upstream-cmd "python -m my_server" --config report.config.json
  tracetrim proxy --upstream-url https://example.test/mcp --header "Authorization=Bearer ..." \
                  --config report.config.json --record traces/session.jsonl

The client (Claude Desktop, Claude Code, Cursor, an agent SDK) launches the
proxy instead of the server: no code changes. The proxy

  - lists the upstream tools with their original names and schemas,
  - applies the runtime config to every output (projection, unescape),
  - adds an `expand` tool, so a hidden field is one call away,
  - optionally records each call to a trace file for `tracetrim report`.

What it cannot do is LEARN: it never sees the agent's final answer, and the
study behind this library found that learning from tool calls alone misses
most of what agents use. Learn from full traces; apply here.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from .projection import project
from .runtime import Policy
from .usage import leaves, parse_output

EXPAND = {
    "name": "expand",
    "description": ("Some tool results are trimmed to save context: the fields removed are listed in the line "
                    "'[trimmed] call_id=... hidden: ...' after the result. Call expand with that call_id and "
                    "the fields or groups you need, to get their values from the original result."),
    "inputSchema": {"type": "object", "required": ["call_id", "fields"], "properties": {
        "call_id": {"type": "string"},
        "fields": {"type": "array", "items": {"type": "string"}}}},
}
MAX_GROUPS = 25


class Trimmer:
    """The part of the proxy that does not depend on MCP: testable alone."""

    def __init__(self, policy: Policy | None):
        self.policy = policy
        self.originals: dict[str, object] = {}
        self.n = 0

    def output(self, tool: str, out: str) -> str:
        if self.policy is None:
            return out
        trimmed = self.policy.apply(tool, out)
        proj = self.policy.projection
        payload = parse_output(out)
        if proj is None or tool not in proj.keep or not isinstance(payload, (dict, list)):
            return trimmed
        drop = proj.seen.get(tool, set()) - proj.keep[tool]
        hidden = sorted({p for p, _ in leaves(payload) if p in drop})
        if not hidden or not self.policy.expand_enabled(tool) and not self.policy.config.get("expand_all"):
            return trimmed
        self.n += 1
        cid = f"c{self.n}"
        self.originals[cid] = payload
        groups: dict[str, int] = {}
        for h in hidden:
            g = ".".join(h.split(".")[:2])
            groups[g] = groups.get(g, 0) + 1
        ordered = sorted(groups.items(), key=lambda x: (-x[1], x[0]))
        items = [g if n == 1 and g in hidden else f"{g} ({n} field{'' if n == 1 else 's'})" for g, n in ordered]
        listed = ", ".join(items[:MAX_GROUPS]) + (f" (+{len(items) - MAX_GROUPS} more groups)" if len(items) > MAX_GROUPS else "")
        return f"{trimmed}\n[trimmed] call_id={cid} hidden: {listed}"

    def expand(self, call_id: str, wanted: list[str]) -> str:
        payload = self.originals.get(call_id)
        if payload is None:
            return "Error: unknown call_id."
        fields = {p for p, _ in leaves(payload)
                  if any(p == w or p.startswith(w + ".") or p.startswith(w + "[]") for w in wanted)}
        got = project(payload, fields)
        return json.dumps(got if isinstance(got, (dict, list)) else {}, ensure_ascii=False)


def _server(upstream_cmd, upstream_url, headers, config, record):
    import shlex

    from mcp import ClientSession, types
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
    from mcp.server.lowlevel import Server

    trimmer = Trimmer(Policy.load(config) if config else None)
    rec = Path(record).open("a", encoding="utf-8") if record else None

    @asynccontextmanager
    async def upstream(_server):
        if upstream_cmd:
            argv = shlex.split(upstream_cmd)
            transport = stdio_client(StdioServerParameters(command=argv[0], args=argv[1:]))
        else:
            transport = streamable_http_client(upstream_url, http_client=create_mcp_http_client(headers=headers))
        async with transport as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            yield {"session": session}

    async def list_tools(ctx, params):
        up = await ctx.lifespan_context["session"].list_tools()
        # The proxy rewrites outputs, so an upstream output schema no longer
        # describes them: clients that validate results would reject them.
        tools = [t.model_copy(update={"output_schema": None}) for t in up.tools]
        if trimmer.policy is not None:
            tools.append(types.Tool(**EXPAND))
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx, params):
        args = params.arguments or {}
        if params.name == "expand":
            text = trimmer.expand(str(args.get("call_id", "")), [str(f) for f in args.get("fields") or []])
        else:
            r = await ctx.lifespan_context["session"].call_tool(params.name, args)
            raw = "".join(getattr(c, "text", "") or "" for c in (r.content or []))
            if rec:
                rec.write(json.dumps({"kind": "call", "tool": params.name, "arguments": args, "output": raw},
                                     ensure_ascii=False) + "\n")
                rec.flush()
            text = trimmer.output(params.name, raw)
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

    return Server("tracetrim-proxy", lifespan=upstream, on_list_tools=list_tools, on_call_tool=call_tool)


async def serve(upstream_cmd: str | None, upstream_url: str | None, headers: dict[str, str],
                config: str | None, record: str | None) -> None:
    """stdio: the client launches the proxy as a local process."""
    from mcp.server.stdio import stdio_server
    server = _server(upstream_cmd, upstream_url, headers, config, record)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


async def serve_http(upstream_cmd, upstream_url, headers, config, record, *, host: str, port: int,
                     token: str | None, allowed_hosts: list[str]) -> None:
    """Streamable HTTP at /mcp, for hosted clients (e.g. the OpenAI Playground)
    that call the server from their own infrastructure.

    Exposed through a tunnel, the proxy is reachable by anyone who knows the
    URL, and it holds the upstream credentials: `token` makes every request
    carry `Authorization: Bearer <token>`.
    """
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    server = _server(upstream_cmd, upstream_url, headers, config, record)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(allowed_hosts),
        allowed_hosts=allowed_hosts, allowed_origins=[])
    app = server.streamable_http_app(host=host, transport_security=security)

    async def guarded(scope, receive, send):
        if token and scope["type"] == "http":
            auth = dict(scope.get("headers") or []).get(b"authorization", b"").decode()
            if auth != f"Bearer {token}":
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await app(scope, receive, send)

    await uvicorn.Server(uvicorn.Config(guarded, host=host, port=port, log_level="warning")).serve()
