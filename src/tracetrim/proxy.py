"""An MCP proxy: the same tools as the upstream servers, with trimmed outputs.

  tracetrim proxy --servers mcp.json --auto workdir/        # all your servers, learns on its own
  tracetrim proxy --upstream-cmd "python -m my_server" --config report.config.json
  tracetrim proxy --upstream-url https://example.test/mcp --header "Authorization=Bearer ..." \
                  --config report.config.json --record traces/session.jsonl

`--servers` reads the `mcpServers` block clients already use (Claude Desktop,
Claude Code, Cursor): `command`/`args`/`env` for local servers, `url`/`headers`
for remote ones, `${VAR}` expanded from the environment. One proxy stands in
for all of them, so the agent sees one `expand`, one `tracetrim_done`, and the
report covers every tool. Tool names are kept; only when two servers expose the
same name are both prefixed with their server name (`billing__search`).

The client launches (or connects to) the proxy instead of the servers: no
code changes. The proxy

  - lists the upstream tools with their original names and schemas,
  - applies the runtime config to every output (projection, unescape),
  - adds an `expand` tool, so a hidden field is one call away,
  - optionally records each call to a trace file for `tracetrim report`.

Learning needs the agent's final answer, which tool calls alone do not show:
in automatic mode (tracetrim.auto) the agent passes it to `tracetrim_done`.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
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
        # In automatic mode the session mixes runs before and after learning:
        # the current run is shown too, and reset when the run closes.
        self.per_run = False
        self.run = [0, 0, 0, 0]          # calls, raw, sent, expands

    def new_run(self) -> str:
        c, r, s, e = self.run
        self.run = [0, 0, 0, 0]
        return f"{c} calls, {r:,} -> {s:,} {self.unit} ({self._pct(s, r)}), {e} expand"

    def call(self, tool: str, raw: str, sent: str) -> None:
        r, s = self.count(raw), self.count(sent)
        self.raw, self.sent, self.calls = self.raw + r, self.sent + s, self.calls + 1
        self.run = [self.run[0] + 1, self.run[1] + r, self.run[2] + s, self.run[3]]
        hidden = sent.rsplit("\n[trimmed] ", 1)[1].split("hidden: ", 1)[1] if "\n[trimmed] " in sent else ""
        groups = len(hidden.split(", ")) if hidden else 0
        self._print(f"{tool:<34} {r:>8,} -> {s:>8,} {self.unit} ({self._pct(s, r)})"
                    + (f"  hidden: {groups} group{'s' if groups != 1 else ''}" if groups else ""))

    def expand(self, call_id: str, fields: list[str], got: str) -> None:
        self.expands += 1
        self.run[3] += 1
        self._print(f"{'expand ' + call_id:<34} the agent asked for {', '.join(fields)[:60]}"
                    f" ({self.count(got):,} {self.unit})")

    def _pct(self, s, r):
        return f"{(s / r - 1) * 100:+.0f}%" if r else "  —"

    def _print(self, line: str) -> None:
        if self.quiet:
            return
        import sys
        total = f"session: {self.calls} calls, {self.raw:,} -> {self.sent:,} ({self._pct(self.sent, self.raw)}), {self.expands} expand"
        if self.per_run:
            c, r, s, e = self.run
            total = f"this run: {c} calls, {r:,} -> {s:,} ({self._pct(s, r)}), {e} expand · {total}"
        print(f"[tracetrim] {line}\n[tracetrim]   {total}", file=sys.stderr, flush=True)


class Trimmer:
    """The part of the proxy that does not depend on MCP: testable alone."""

    def __init__(self, policy: Policy | None):
        self.policy = policy
        self.originals: dict[str, object] = {}
        self.tool_of: dict[str, str] = {}
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
        self.tool_of[cid] = tool
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


@dataclass
class Options:
    upstream_cmd: str | None = None
    upstream_url: str | None = None
    headers: dict = field(default_factory=dict)
    config: str | None = None
    record: str | None = None
    quiet: bool = False
    structural: str | None = None
    auto_dir: str | None = None      # automatic mode: learn, apply, report (see tracetrim.auto)
    min_runs: int = 5
    idle_seconds: int = 300          # a run without tracetrim_done is closed after this pause
    servers: dict = field(default_factory=dict)   # name -> {command, args, env} | {url, headers}

    def upstreams(self) -> dict:
        """Every upstream as a named spec, `${VAR}` expanded."""
        if self.servers:
            servers = self.servers
        elif self.upstream_cmd:
            argv = shlex.split(self.upstream_cmd)
            servers = {"upstream": {"command": argv[0], "args": argv[1:]}}
        else:
            servers = {"upstream": {"url": self.upstream_url, "headers": self.headers}}
        return {name: _expand_vars(spec) for name, spec in servers.items()}


def _expand_vars(x):
    if isinstance(x, str):
        return os.path.expandvars(x)
    if isinstance(x, list):
        return [_expand_vars(v) for v in x]
    if isinstance(x, dict):
        return {k: _expand_vars(v) for k, v in x.items()}
    return x


def load_servers(path: str | Path) -> dict:
    """The `mcpServers` block of a client config, or the bare mapping."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    servers = data.get("mcpServers", data)
    for name, spec in servers.items():
        if not ("command" in spec or "url" in spec):
            raise SystemExit(f"server {name!r}: needs 'command' (stdio) or 'url' (streamable HTTP)")
        if spec.get("type") == "sse":
            raise SystemExit(f"server {name!r}: SSE transport is not supported, use streamable HTTP")
    return servers


def route_names(listed: dict[str, list]) -> dict[str, tuple[str, str]]:
    """Exposed tool name -> (server, upstream name). Names stay as they are,
    unless two servers share one: then each copy is prefixed with its server."""
    count: dict[str, int] = {}
    for tools in listed.values():
        for t in tools:
            count[t] = count.get(t, 0) + 1
    return {(t if count[t] == 1 else f"{server}__{t}"): (server, t)
            for server, tools in listed.items() for t in tools}


def _server(o: Options):
    import asyncio
    import time

    from mcp import ClientSession, types
    from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
    from mcp.server.lowlevel import Server

    from .auto import DONE, Learner

    learner = Learner(o.auto_dir, o.min_runs) if o.auto_dir else None
    policy = Policy.load(o.config) if o.config else (learner.initial_policy() if learner else None)
    if o.structural:
        # Override from the command line: "toon" or "headroom" after the projection
        # (or alone, when no config is given).
        policy = policy or Policy({"format": "tracetrim/config@1", "tools": {}}, None)
        policy.structural = o.structural
    trimmer = Trimmer(policy)
    rec = Path(o.record).open("a", encoding="utf-8") if o.record else None
    live = Live(o.quiet)
    live.per_run = bool(learner)
    last_call = [0.0]

    def log(line):
        if not o.quiet:
            print(f"[tracetrim] {line}", file=sys.stderr, flush=True)

    if learner:
        st = learner.state
        log(f"auto mode: {learner.dir} · {st['complete_runs']} complete runs (learning from {o.min_runs})"
            f" · config {'applied' if policy and policy.tools else 'not applied yet'}")

    async def close_run(answer):
        path = learner.done(answer)
        if path is None:
            return
        run_total = live.new_run()
        new = await asyncio.to_thread(learner.relearn)
        st = learner.state
        if new is not None:
            if o.structural:
                new.structural = o.structural
            trimmer.policy = new
        applied = trimmer.policy is not None and bool(trimmer.policy.tools)
        log(f"run {st['runs']} closed ({'with answer' if answer else 'no answer: telemetry only'}): {run_total}")
        log(f"  {st['complete_runs']} complete runs · report updated · "
            + (f"trimming {len(trimmer.policy.tools)} tools" if applied
               else f"learning: {max(0, o.min_runs - st['complete_runs'])} more complete runs before trimming")
            + (f" · switched off: {', '.join(st['off'])}" if st["off"] else ""))

    routes: dict[str, tuple[str, str]] = {}

    @asynccontextmanager
    async def upstream(_server):
        async with AsyncExitStack() as stack:
            sessions = {}
            for name, spec in o.upstreams().items():
                if "url" in spec:
                    transport = streamable_http_client(
                        spec["url"], http_client=create_mcp_http_client(headers=spec.get("headers") or {}))
                else:
                    env = {**get_default_environment(), **spec["env"]} if spec.get("env") else None
                    transport = stdio_client(StdioServerParameters(
                        command=spec["command"], args=spec.get("args") or [], env=env, cwd=spec.get("cwd")))
                read, write, *_ = await stack.enter_async_context(transport)
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                sessions[name] = session
            log(f"upstream: {', '.join(sessions)}")
            yield {"sessions": sessions}

    async def upstream_tools(sessions):
        listed = {name: (await s.list_tools()).tools for name, s in sessions.items()}
        routes.clear()
        routes.update(route_names({n: [t.name for t in ts] for n, ts in listed.items()}))
        exposed = {v: k for k, v in routes.items()}
        return [t.model_copy(update={"name": exposed[(n, t.name)]}) for n, ts in listed.items() for t in ts]

    async def list_tools(ctx, params):
        up = await upstream_tools(ctx.lifespan_context["sessions"])
        # The proxy rewrites outputs, so an upstream output schema no longer
        # describes them: clients that validate results would reject them.
        tools = [t.model_copy(update={"output_schema": None}) for t in up]
        # In automatic mode expand is listed from the start: clients may cache
        # the tool list, and trimming begins only after the first runs.
        if trimmer.policy is not None or learner:
            tools.append(types.Tool(**EXPAND))
        if learner:
            tools.append(types.Tool(**DONE))
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx, params):
        args = params.arguments or {}
        if learner and params.name == "tracetrim_done":
            await close_run(str(args.get("answer", "")))
            text = "Recorded."
        elif params.name == "expand":
            fields = [str(f) for f in args.get("fields") or []]
            cid = str(args.get("call_id", ""))
            text = trimmer.expand(cid, fields)
            live.expand(cid, fields, text)
            if learner and cid in trimmer.tool_of:
                learner.expanded(trimmer.tool_of[cid])
        else:
            if learner and learner.events and time.time() - last_call[0] > o.idle_seconds:
                await close_run(None)
            last_call[0] = time.time()
            sessions = ctx.lifespan_context["sessions"]
            if params.name not in routes:           # a client that calls before listing
                await upstream_tools(sessions)
            if params.name not in routes:
                return types.CallToolResult(content=[types.TextContent(type="text", text=f"Error: unknown tool {params.name}.")],
                                            isError=True)
            server, name = routes[params.name]
            r = await sessions[server].call_tool(name, args)
            raw = "".join(getattr(c, "text", "") or "" for c in (r.content or []))
            if rec:
                rec.write(json.dumps({"kind": "call", "tool": params.name, "arguments": args, "output": raw},
                                     ensure_ascii=False) + "\n")
                rec.flush()
            text = trimmer.output(params.name, raw)
            if learner:
                learner.call(params.name, args, raw)
                if "\n[trimmed] " in text:
                    learner.trimmed(params.name)
            live.call(params.name, raw, text)
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

    return Server("tracetrim-proxy", lifespan=upstream, on_list_tools=list_tools, on_call_tool=call_tool), learner


async def serve(o: Options) -> None:
    """stdio: the client launches the proxy as a local process."""
    from mcp.server.stdio import stdio_server
    server, learner = _server(o)
    if learner:
        print(f"[tracetrim] report: {(learner.dir / 'report.html').resolve()}", file=sys.stderr, flush=True)
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        if learner:
            learner.flush()


async def serve_http(o: Options, *, host: str, port: int, token: str | None, allowed_hosts: list[str]) -> None:
    """Streamable HTTP at /mcp, for hosted clients (e.g. the OpenAI Playground)
    that call the server from their own infrastructure. In automatic mode the
    live report is served at /report?token=..., refreshing itself.

    Exposed through a tunnel, the proxy is reachable by anyone who knows the
    URL, and it holds the upstream credentials: `token` makes every request
    carry `Authorization: Bearer <token>` (the report page, opened in a
    browser, takes it as a query parameter instead).
    """
    from urllib.parse import parse_qs

    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    server, learner = _server(o)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(allowed_hosts),
        allowed_hosts=allowed_hosts, allowed_origins=[])
    app = server.streamable_http_app(host=host, transport_security=security)

    async def respond(send, status, body, ctype=b"text/plain; charset=utf-8"):
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", ctype)]})
        await send({"type": "http.response.body", "body": body})

    async def guarded(scope, receive, send):
        if scope["type"] == "http" and learner and scope.get("path") == "/report":
            q = parse_qs(scope.get("query_string", b"").decode())
            if token and q.get("token", [""])[0] != token:
                return await respond(send, 401, b"unauthorized")
            page = learner.dir / "report.html"
            if not page.exists():
                html = ("<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=10>"
                        "<body style='font:16px system-ui;padding:24px'>tracetrim is observing: the report appears "
                        "after the first run.</body>").encode()
            else:
                html = page.read_bytes().replace(b"<head>", b"<head><meta http-equiv=\"refresh\" content=\"10\">", 1)
            return await respond(send, 200, html, b"text/html; charset=utf-8")
        if token and scope["type"] == "http":
            auth = dict(scope.get("headers") or []).get(b"authorization", b"").decode()
            if auth != f"Bearer {token}":
                return await respond(send, 401, b"unauthorized")
        await app(scope, receive, send)

    if learner:
        print(f"[tracetrim] live report: http://{host}:{port}/report" + (f"?token={token}" if token else ""),
              file=sys.stderr, flush=True)
    class FlushingServer(uvicorn.Server):
        # uvicorn re-raises SIGTERM/SIGINT once it has shut down, which ends the
        # process before any `finally`: the unfinished run is kept here instead.
        async def shutdown(self, sockets=None):
            await super().shutdown(sockets=sockets)
            if learner:
                learner.flush()

    await FlushingServer(uvicorn.Config(guarded, host=host, port=port, log_level="warning")).serve()
