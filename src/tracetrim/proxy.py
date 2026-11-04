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


class Live:
    """One line per call on stderr: what the tool sent, what the agent got.

    stderr, because in stdio mode stdout is the MCP channel. Tokens are counted
    with tiktoken when it is installed, otherwise characters are shown and
    labelled as such: never a token estimate.
    """

    def __init__(self, quiet: bool = False):
        self.quiet = quiet
        try:
            import tiktoken
            enc = tiktoken.get_encoding("o200k_base")
            self.count, self.unit = (lambda t: len(enc.encode(t or ""))), "tokens"
        except ImportError:
            self.count, self.unit = (lambda t: len(t or "")), "chars"
        self.raw = self.sent = self.calls = self.expands = 0

    def call(self, tool: str, raw: str, sent: str) -> None:
        r, s = self.count(raw), self.count(sent)
        self.raw, self.sent, self.calls = self.raw + r, self.sent + s, self.calls + 1
        hidden = sent.rsplit("\n[trimmed] ", 1)[1].split("hidden: ", 1)[1] if "\n[trimmed] " in sent else ""
        groups = len(hidden.split(", ")) if hidden else 0
        self._print(f"{tool:<34} {r:>8,} -> {s:>8,} {self.unit} ({self._pct(s, r)})"
                    + (f"  hidden: {groups} group{'s' if groups != 1 else ''}" if groups else ""))

    def expand(self, call_id: str, fields: list[str], got: str) -> None:
        self.expands += 1
        self._print(f"{'expand ' + call_id:<34} the agent asked for {', '.join(fields)[:60]}"
                    f" ({self.count(got):,} {self.unit})")

    def _pct(self, s, r):
        return f"{(s / r - 1) * 100:+.0f}%" if r else "  —"

    def _print(self, line: str) -> None:
        if self.quiet:
            return
        import sys
        total = f"session: {self.calls} calls, {self.raw:,} -> {self.sent:,} ({self._pct(self.sent, self.raw)}), {self.expands} expand"
        print(f"[tracetrim] {line}\n[tracetrim]   {total}", file=sys.stderr, flush=True)


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


def _server(upstream_cmd, upstream_url, headers, config, record, quiet=False, structural=None):
    import shlex

    from mcp import ClientSession, types
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
    from mcp.server.lowlevel import Server

    policy = Policy.load(config) if config else None
    if structural:
        # Override from the command line: "toon" or "headroom" after the projection
        # (or alone, when no config is given).
        policy = policy or Policy({"format": "tracetrim/config@1", "tools": {}}, None)
        policy.structural = structural
    trimmer = Trimmer(policy)
    rec = Path(record).open("a", encoding="utf-8") if record else None
    live = Live(quiet)

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
            fields = [str(f) for f in args.get("fields") or []]
            text = trimmer.expand(str(args.get("call_id", "")), fields)
            live.expand(str(args.get("call_id", "")), fields, text)
        else:
            r = await ctx.lifespan_context["session"].call_tool(params.name, args)
            raw = "".join(getattr(c, "text", "") or "" for c in (r.content or []))
            if rec:
                rec.write(json.dumps({"kind": "call", "tool": params.name, "arguments": args, "output": raw},
                                     ensure_ascii=False) + "\n")
                rec.flush()
            text = trimmer.output(params.name, raw)
            live.call(params.name, raw, text)
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

    return Server("tracetrim-proxy", lifespan=upstream, on_list_tools=list_tools, on_call_tool=call_tool)


async def serve(upstream_cmd: str | None, upstream_url: str | None, headers: dict[str, str],
                config: str | None, record: str | None, quiet: bool = False, structural: str | None = None) -> None:
    """stdio: the client launches the proxy as a local process."""
    from mcp.server.stdio import stdio_server
    server = _server(upstream_cmd, upstream_url, headers, config, record, quiet, structural)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


async def serve_http(upstream_cmd, upstream_url, headers, config, record, *, host: str, port: int,
                     token: str | None, allowed_hosts: list[str], quiet: bool = False,
                     structural: str | None = None) -> None:
    """Streamable HTTP at /mcp, for hosted clients (e.g. the OpenAI Playground)
    that call the server from their own infrastructure.

    Exposed through a tunnel, the proxy is reachable by anyone who knows the
    URL, and it holds the upstream credentials: `token` makes every request
    carry `Authorization: Bearer <token>`.
    """
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    server = _server(upstream_cmd, upstream_url, headers, config, record, quiet, structural)
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
