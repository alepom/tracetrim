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

How to use `expand` and `tracetrim_done` is also published as an Agent Skill
(MCP Skills extension, see `publish_skill`), for hosts that load skills; the
tool descriptions stay enough for the others.

This module is an adapter: it translates MCP requests into the contract of
tracetrim.contract and leaves every decision to tracetrim.engine.
"""
from __future__ import annotations

import hmac
import json
import os
import shlex
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import skill
from .about import VERSION
from .contract import ToolCall, ToolResult
from .contract import FILTERED
from .engine import EXPAND, MARK, MAX_GROUPS, MAX_ORIGINALS, Engine, Trimmer, requested_fields  # noqa: F401  (Trimmer & co. lived here until 0.3.5)
from .runtime import Policy

MAX_SSE_EVENT = 16 * 1024 * 1024
SKILL_TTL_MS = 300_000       # how long a host may reuse the skill entry: it changes only when the proxy restarts


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
        hidden = sent.rsplit(MARK, 1)[1].split("hidden: ", 1)[1] if MARK in sent else ""
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
    # Adaptive learning, with --auto; all off by default (see tracetrim.evidence.Adaptive).
    promote: bool = False
    promote_runs: int = 2
    switch_off_runs: int = 2
    shared: bool = False             # set by serve_http: several clients may reach the proxy
    sample_rate: float = 0.0
    sample_max_share: float = 0.2
    sample_seed: int = 0
    skill: bool = True               # publish the tracetrim skill (MCP Skills), whenever expand is offered
    skill_strategy: bool = False     # ... with the optional notes on organising tool calls

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


def transport_for(name: str, spec: dict):
    """The MCP client transport for one upstream: stdio, HTTP with fixed
    headers, or HTTP with OAuth (tracetrim.oauth)."""
    from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    if "url" in spec:
        from .oauth import provider, wants_oauth
        auth = provider(name, spec["url"]) if wants_oauth(spec) else None
        http = create_mcp_http_client(headers=spec.get("headers") or {}, auth=auth)
        # The SDK's default (1 MB per event) is too small for some tools: a SERP
        # with many items exceeds it. 16 MB is what the study's client used.
        return streamable_http_client(spec["url"], http_client=http, max_sse_event_size=MAX_SSE_EVENT)
    env = {**get_default_environment(), **spec["env"]} if spec.get("env") else None
    return stdio_client(StdioServerParameters(command=spec["command"], args=spec.get("args") or [], env=env,
                                              cwd=spec.get("cwd")))


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
        if spec.get("auth") not in (None, "oauth", "none"):
            raise SystemExit(f"server {name!r}: 'auth' is 'oauth' or 'none'")
        if spec.get("type") == "sse":
            raise SystemExit(f"server {name!r}: SSE transport is not supported, use streamable HTTP")
    return servers


OWN_TOOLS = {"expand", "tracetrim_done"}


def route_names(listed: dict[str, list]) -> dict[str, tuple[str, str]]:
    """Exposed tool name -> (server, upstream name). Names stay as they are,
    unless two servers share one: then each copy is prefixed with its server."""
    count: dict[str, int] = {}
    for tools in listed.values():
        for t in tools:
            count[t] = count.get(t, 0) + 1
    # A server's own `expand` or `tracetrim_done` would be shadowed by the
    # proxy's: it is prefixed like a clash between servers.
    routes: dict[str, tuple[str, str]] = {}
    for server, tools in listed.items():
        for t in tools:
            name = t if count[t] == 1 and t not in OWN_TOOLS else f"{server}__{t}"
            # A prefixed name can still meet another server's own tool name
            # (a: search, b: search, c: a__search): never let one hide the other.
            if name in routes or name in OWN_TOOLS:
                raise SystemExit(f"tool name clash on {name!r} ({routes.get(name, ('?',))[0]} and {server}): "
                                 "rename one of the servers in mcp.json")
            routes[name] = (server, t)
    return routes


def publish_skill(server, *, strategy: bool, done: bool) -> dict:
    """Serve the tracetrim skill as the MCP Skills extension asks (SEP-2640,
    `io.modelcontextprotocol/skills`): `skills/list` and `skills/get` return
    the entry (frontmatter and file manifest with digests), and the SKILL.md
    itself is a resource, read with `resources/read`. Returns the entry.

    The SDK this was written against (mcp 2.3.0) has no Skills support yet:
    the two methods are registered as plain request handlers. The proxy's
    resources are this one file; upstream resources are not proxied.

    Extensions are announced in `server/discover` (protocol 2026-07-28). Hosts
    on the earlier protocol negotiate them in the `initialize` handshake
    instead (MCP Inspector 2.13.0 does), in a `capabilities.extensions` field
    that is outside the published schema of those versions: the SDK drops it
    from the reply, so a middleware puts it back, and only for a client that
    declared the extension itself, the one case in which the field is expected.
    """
    from mcp import types
    from mcp.shared.exceptions import MCPError

    invalid = types.INVALID_PARAMS
    entry = skill.entry(strategy=strategy, done=done)
    content = skill.skill_md(strategy=strategy, done=done)
    cache = {"ttlMs": SKILL_TTL_MS, "cacheScope": "public"}

    class GetSkillParams(types.RequestParams):
        uri: str

    async def list_resources(ctx, params):
        if params is not None and params.cursor:
            raise MCPError(invalid, "Invalid cursor.")
        fm = entry["frontmatter"]
        return types.ListResourcesResult(
            resources=[types.Resource(uri=skill.URI, name=fm["name"], description=fm["description"],
                                      mime_type="text/markdown", size=entry["resources"][0]["size"])],
            ttl_ms=SKILL_TTL_MS, cache_scope="public")

    async def read_resource(ctx, params):
        if params.uri != skill.URI:
            raise MCPError(invalid, f"Unknown resource: {params.uri}")
        return types.ReadResourceResult(
            contents=[types.TextResourceContents(uri=skill.URI, mime_type="text/markdown", text=content)],
            ttl_ms=SKILL_TTL_MS, cache_scope="public")

    async def skills_list(ctx, params):
        if params.cursor:
            raise MCPError(invalid, "Invalid cursor.")
        return {"resultType": "complete", "skills": [entry], **cache}

    async def skills_get(ctx, params):
        if params.uri != skill.URI:
            raise MCPError(invalid, f"Unknown skill: {params.uri}")
        return {"resultType": "complete", "skill": entry, **cache}

    server.add_request_handler("resources/list", types.PaginatedRequestParams, list_resources)
    server.add_request_handler("resources/read", types.ReadResourceRequestParams, read_resource)
    server.add_request_handler("skills/list", types.PaginatedRequestParams, skills_list)
    server.add_request_handler("skills/get", GetSkillParams, skills_get)
    server.extensions[skill.EXTENSION] = {}          # no optional feature: `directoryRead` stays off

    async def announce_in_handshake(ctx, call_next):
        result = await call_next(ctx)
        if ctx.method == "initialize" and isinstance(result, dict):
            asked = ((ctx.params or {}).get("capabilities") or {}).get("extensions") or {}
            if skill.EXTENSION in asked and isinstance(result.get("capabilities"), dict):
                result["capabilities"].setdefault("extensions", {})[skill.EXTENSION] = {}
        return result

    server.middleware.append(announce_in_handshake)
    return entry


def _server(o: Options):
    import asyncio
    import time

    from mcp import ClientSession, types
    from mcp.server.lowlevel import Server

    from .auto import DONE, DONE_REPLY, Learner
    from .evidence import Adaptive

    adaptive = Adaptive(promote=o.promote, promote_requested_runs=o.promote_runs, sample_rate=o.sample_rate,
                        switch_off_runs=o.switch_off_runs,
                        sample_max_share=o.sample_max_share, sample_seed=o.sample_seed)
    learner = (Learner(o.auto_dir, o.min_runs, idle_seconds=o.idle_seconds, adaptive=adaptive)
               if o.auto_dir else None)
    # `--structural` overrides the config: "toon" or "headroom" after the
    # projection (or alone, when there is no config yet).
    engine = Engine(learner, Policy.load(o.config) if o.config else None, o.structural, shared=o.shared)
    offers_expand = engine.policy is not None or learner is not None
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
            f" · config {'applied' if engine.trimming else 'not applied yet'}")

    closing = asyncio.Lock()

    async def close_run(answer) -> bool:
        """Close the run, if one is open. One closing at a time: a second
        request waits, then finds nothing left to close."""
        async with closing:
            if engine.done(answer) is None:
                return False
            run_total = live.new_run()
            await asyncio.to_thread(engine.relearn)
            st = learner.state
            log(f"run {st['runs']} closed ({'with answer' if answer else 'no answer: telemetry only'}): {run_total}")
            log(f"  {st['complete_runs']} complete runs · report updated · "
                + (f"trimming {len(engine.policy.tools)} tools" if engine.trimming
                   else f"learning: {max(0, o.min_runs - st['complete_runs'])} more complete runs before trimming")
                + (f" · switched off: {', '.join(st['off'])}" if st["off"] else ""))
            return True

    routes: dict[str, tuple[str, str]] = {}

    @asynccontextmanager
    async def upstream(_server):
        async with AsyncExitStack() as stack:
            sessions = {}
            for name, spec in o.upstreams().items():
                read, write, *_ = await stack.enter_async_context(transport_for(name, spec))
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
        if offers_expand:
            tools.append(types.Tool(**EXPAND))
        if learner:
            tools.append(types.Tool(**DONE))
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx, params):
        args = params.arguments or {}
        if learner and params.name == "tracetrim_done":
            answer = args.get("answer")
            if not isinstance(answer, str):
                answer = "" if answer is None else json.dumps(answer, ensure_ascii=False)
            if not answer.strip():
                # Not a closing: the run stays open for the call that carries the answer.
                engine.refuse()
                return types.CallToolResult(isError=True, content=[types.TextContent(
                    type="text", text="Error: tracetrim_done needs your final answer in `answer`. Nothing was recorded.")])
            if not await close_run(answer):
                log("tracetrim_done with no tool call since the last closing (a repeat or a retry): nothing to record")
            # The same reply whether the run was closed now or already: it asks for nothing more.
            text = DONE_REPLY
        elif params.name == "expand":
            fields = requested_fields(args.get("fields"))
            cid = str(args.get("call_id", ""))
            text = engine.expand(cid, fields)
            live.expand(cid, fields, text)
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
            call = ToolCall(params.name, args, server=server, upstream_tool=name)
            r = await sessions[server].call_tool(name, args)
            content = list(r.content or [])
            # An error, or anything that is not plain text (images, resources),
            # goes back exactly as the server sent it: nothing to learn or trim.
            is_error = bool(getattr(r, "is_error", None) or getattr(r, "isError", None))
            structured = getattr(r, "structured_content", None)
            if is_error or any(getattr(c, "type", "text") != "text" for c in content):
                # Passed back exactly as sent, and still recorded: errors are
                # what the report's error analysis is about.
                text_part = "".join(getattr(c, "text", "") or "" for c in content if getattr(c, "type", "") == "text")
                kinds = sorted({getattr(c, "type", "?") for c in content if getattr(c, "type", "") != "text"})
                note = text_part if text_part else f"[non-text content: {', '.join(kinds)}]"
                if is_error and not note.lower().startswith("error"):
                    note = "Error: " + note
                if rec:
                    rec.write(json.dumps({"kind": "call", "tool": params.name, "arguments": args, "output": note},
                                         ensure_ascii=False) + "\n")
                    rec.flush()
                engine.observe(call, ToolResult(note, verbatim=True))
                live.call(params.name, note, note)
                return r                     # exactly as the server sent it, metadata included
            raw = "".join(getattr(c, "text", "") or "" for c in content)
            if rec:
                rec.write(json.dumps({"kind": "call", "tool": params.name, "arguments": args, "output": raw},
                                     ensure_ascii=False) + "\n")
                rec.flush()
            obs = engine.observe(call, ToolResult(raw))
            text = obs.text
            live.call(params.name, raw, text)
            if text == raw:
                # Nothing trimmed: the server's result goes back untouched,
                # structured content and metadata included.
                return r
            if structured is not None and engine.policy is not None and obs.exposure == FILTERED:
                # The structured copy is trimmed like the text, or it would carry
                # back everything the projection removed. A result passed whole on
                # purpose (sampled, or cheaper whole) keeps it whole as well.
                projected = engine.policy.apply(params.name, json.dumps(structured, ensure_ascii=False))
                try:
                    structured = json.loads(projected)
                except ValueError:
                    structured = None
            meta = getattr(r, "meta", None)
            return types.CallToolResult(content=[types.TextContent(type="text", text=text)],
                                        structuredContent=structured, **({"_meta": meta} if meta else {}))
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

    # The skill explains expand and tracetrim_done: published whenever expand is offered.
    publish = o.skill and offers_expand
    server = Server("tracetrim-proxy", version=VERSION, lifespan=upstream, on_list_tools=list_tools,
                    on_call_tool=call_tool,
                    # One sentence, for hosts that show a server's instructions and do not load skills.
                    instructions=skill.pointer(done=learner is not None) if publish else None)
    if publish:
        entry = publish_skill(server, strategy=o.skill_strategy, done=learner is not None)
        log(f"skill: {entry['uri']} · {entry['resources'][0]['digest'][:19]}… · strategy "
            f"{'on' if o.skill_strategy else 'off'}")
    elif o.skill_strategy:
        log("skill not published: " + ("--no-skill" if not o.skill else "nothing is trimmed without --auto or --config"))
    return server, learner


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
    from dataclasses import replace

    from mcp.server.transport_security import TransportSecuritySettings

    # Over HTTP the proxy may be reached by several clients: never name a call_id in an error.
    server, learner = _server(replace(o, shared=True))
    # Always on: without it a web page could rebind its domain to 127.0.0.1 and
    # call the upstream tools with the credentials the proxy holds. Local
    # addresses on this port are always accepted; public ones (a tunnel) are
    # added with --allowed-host.
    local = [f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"]
    hosts = list(dict.fromkeys(list(allowed_hosts) + local))
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts,
        # Local browser clients (MCP Inspector, a local web UI) run on their own
        # port: any local origin is accepted, no remote one unless allowed.
        allowed_origins=["http://localhost:*", "http://127.0.0.1:*", "http://[::1]:*"]
                        + [f"https://{h}" for h in allowed_hosts])

    def same(given: str, expected: str) -> bool:
        return hmac.compare_digest(given.encode(), expected.encode())
    app = server.streamable_http_app(host=host, transport_security=security)

    async def respond(send, status, body, ctype=b"text/plain; charset=utf-8"):
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", ctype)]})
        await send({"type": "http.response.body", "body": body})

    async def guarded(scope, receive, send):
        if scope["type"] == "http" and learner and scope.get("path") == "/report":
            host = dict(scope.get("headers") or []).get(b"host", b"").decode("latin-1")
            if host not in hosts:
                return await respond(send, 421, b"invalid Host header: add it with --allowed-host")
            q = parse_qs(scope.get("query_string", b"").decode())
            if token and not same(q.get("token", [""])[0], token):
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
            auth = dict(scope.get("headers") or []).get(b"authorization", b"").decode("latin-1")
            if not same(auth, f"Bearer {token}"):
                return await respond(send, 401, b"unauthorized")
        await app(scope, receive, send)

    if allowed_hosts and not token:
        print("[tracetrim] WARNING: reachable through " + ", ".join(allowed_hosts) + " with no token: anyone with the "
              "URL can call your tools with your credentials. Set $TRACETRIM_TOKEN or --token.",
              file=sys.stderr, flush=True)
    if learner:
        # The token is not printed: it would end up in logs and terminal recordings.
        print(f"[tracetrim] live report: http://{host}:{port}/report" + ("?token=<your token>" if token else ""),
              file=sys.stderr, flush=True)
    class FlushingServer(uvicorn.Server):
        # uvicorn re-raises SIGTERM/SIGINT once it has shut down, which ends the
        # process before any `finally`: the unfinished run is kept here instead.
        async def shutdown(self, sockets=None):
            await super().shutdown(sockets=sockets)
            if learner:
                learner.flush()

    await FlushingServer(uvicorn.Config(guarded, host=host, port=port, log_level="warning")).serve()
