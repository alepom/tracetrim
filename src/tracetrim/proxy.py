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
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path

from . import skill
from .about import VERSION
from .skill import VERSION as SKILL_VERSION
from .contract import ToolCall, ToolResult
from .contract import FILTERED
from .engine import EXPAND, MARK, MAX_GROUPS, MAX_ORIGINALS, Engine, Trimmer, expand_tool, requested_fields  # noqa: F401  (Trimmer & co. lived here until 0.3.5)
from .runtime import Policy

MAX_SSE_EVENT = 16 * 1024 * 1024
SKILL_TTL_MS = 300_000       # how long a host may reuse the skill entry: it changes only when the proxy restarts


class Live:
    """One line per call on stderr: what the tool sent, what the agent got.

    stderr, because in stdio mode stdout is the MCP channel. Tokens are counted
    with tiktoken when it is installed, otherwise characters are shown and
    labelled as such: never a token estimate.
    """

    def __init__(self, quiet: bool = False, *, process: Live | None = None):
        self.process = process
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
        # The outputs re-written with nothing removed, when the engine counts them
        # (contract.Sizes): they split a saving into "format" and "fields". None
        # from the first call that comes without the count.
        self.whole: int | None = 0
        self.run_whole: int | None = 0

    def new_run(self) -> str:
        c, r, s, e = self.run
        w, self.run, self.run_whole = self.run_whole, [0, 0, 0, 0], 0
        return f"{c} calls, {r:,} -> {s:,} {self.unit} ({self._pct(s, r, w)}), {e} expand"

    def call(self, tool: str, raw: str, sent: str, sizes=None) -> None:
        if sizes is not None:
            r, w, s = sizes.raw, sizes.whole, sizes.sent     # already counted by the engine, the same way
        else:
            r, w, s = self.count(raw), None, self.count(sent)
        totals = self.process or self
        totals.whole = None if w is None or totals.whole is None else totals.whole + w
        self.run_whole = None if w is None or self.run_whole is None else self.run_whole + w
        totals.raw += r
        totals.sent += s
        totals.calls += 1
        self.run = [self.run[0] + 1, self.run[1] + r, self.run[2] + s, self.run[3]]
        # A tool's own text may hold a line that starts like ours: a log, a trace, an echo. Only count what
        # follows "hidden: " when it is there; this is a log line, never a reason to fail a call.
        tail = sent.rsplit(MARK, 1)[1] if MARK in sent else ""
        hidden = tail.split("hidden: ", 1)[1] if "hidden: " in tail else ""
        groups = len(hidden.split(", ")) if hidden else 0
        self._print(f"{tool:<34} {r:>8,} -> {s:>8,} {self.unit} ({self._pct(s, r)})"
                    + (f"  hidden: {groups} group{'s' if groups != 1 else ''}" if groups else ""))

    def expand(self, call_id: str, fields: list[str], got: str) -> None:
        totals = self.process or self
        totals.expands += 1
        self.run[3] += 1
        self._print(f"{'expand ' + call_id:<34} the agent asked for {', '.join(fields)[:60]}"
                    f" ({self.count(got):,} {self.unit})")

    def _pct(self, s, r, w=None):
        """The change from `r` to `s`; with `w`, also its two parts, both as shares
        of `r`: re-writing the output (format) and removing fields."""
        if not r:
            return "  —"
        def pct(x):
            return "0%" if x == 0 else f"{x / r * 100:+.0f}%"
        if w is None or s == r:
            return f"{(s / r - 1) * 100:+.0f}%"
        return f"{pct(s - r)}: {pct(w - r)} format, {pct(s - w)} fields"

    def _print(self, line: str) -> None:
        if self.quiet:
            return
        import sys
        totals = self.process or self
        label = "process" if self.process else "session"
        total = (f"{label}: {totals.calls} calls, {totals.raw:,} -> {totals.sent:,} "
                 f"({self._pct(totals.sent, totals.raw, totals.whole)}), {totals.expands} expand")
        if self.per_run:
            c, r, s, e = self.run
            total = f"this run: {c} calls, {r:,} -> {s:,} ({self._pct(s, r, self.run_whole)}), {e} expand · {total}"
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
    compact: bool | None = None      # compact JSON; None: from the first call with --auto, else as the config says
    breakdown: bool = True           # count what re-serialising saves apart from what removing fields saves
    log_client: str | None = None    # a file for one line per client request (tracetrim.clientlog); off by default
    protect_ids: str = "all"         # --auto: which identifying fields learning keeps (tracetrim.projection.PROTECT_IDS)
    learning: str = "1.1"            # --auto: the learning rules (tracetrim.usage.LEARNING); "1.0" restores those of 1.0
    keep_positions: bool | None = None  # a list element left with no field keeps its place; None: as the config says (on)

    isolate_sessions: bool = False    # HTTP automatic mode only
    # --auto: reply to tracetrim_done once the run is on disk, and relearn in the background (1.4, off by default:
    # see BackgroundLearning). Off, the reply waits for the relearning, as in 1.3.
    background_relearn: bool = False
    # The text of the skill and of `expand` (1.4: version 2, the check before declaring information absent);
    # "1" restores the text of 1.3. See tracetrim.skill.VERSIONS.
    skill_version: str = SKILL_VERSION   # not `skill.VERSION`: in this class `skill` is the field above

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


def publish_skill(server, *, strategy: bool, done: bool, version: str = skill.VERSION) -> dict:
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
    entry = skill.entry(strategy=strategy, done=done, version=version)
    content = skill.skill_md(strategy=strategy, done=done, version=version)
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


def _warn_mixed(learner):
    if learner.closed_sessions > 1:
        print(f"[tracetrim] WARNING: run-{learner.state['runs']:04d} contains calls from "
              f"{learner.closed_sessions} HTTP sessions", file=sys.stderr, flush=True)


class BackgroundLearning:
    """`--background-relearn`: the closing replies once the run is on disk, and the relearning runs after it.

    - The only coordinator of relearning while the proxy runs: closings, finished interrupted closings, the idle
      expiry of isolated sessions, the runs kept at shutdown and the recovery at start all ask it; one relearning
      at a time, always in a thread, never on the event loop.
    - The order of requests and relearnings. A request that arrives while a relearning runs (a tool call, an
      `expand`, a closing, a refused closing, a tool listing) waits for it (`settled`) and is served with the new
      policy. A tool call already on its way to the upstream when a relearning is asked for (another client's) is
      recorded first: the relearning reads its inputs once no call is in flight (`call`). So nothing the proxy
      learns from changes while a relearning reads and computes.
    - With one client at a time, whose requests follow one another, every request comes after a reply: without
      the option the reply to a closing comes after its relearning, with it before, and the next request waits.
      So what the model receives and what the proxy learns are the same in both modes (tests/test_background_
      relearn.py compares every file byte for byte), and the pauses that close a run by inactivity, and the one
      after an answer, are the client's: measured from a reply, or after a wait, never across one. With several
      clients the modes differ by design: without the option another client's tool call, `expand` or tool listing
      is served at once, with the policy in place, while a closing relearns (closings wait for one another in both
      modes). A tool call waits for the relearning only once it is recorded: a client that disappears without
      cancelling its call keeps the relearning waiting until the upstream answers it.
    - The run is recorded on disk as pending in the same write that records it (`Learner._write_run`); it stays
      pending until a relearning has covered it, so a process stopped earlier relearns at its next start.
      Every product of the report is written atomically.
    - On shutdown (`drain`) the relearning in progress is completed; from then on every request for one is
      awaited before it returns, still through this coordinator, so two relearnings never run at once. The
      open runs are then kept through it as well (`flush`).
    - The report (and so the live report) is updated when the relearning ends.
    """

    def __init__(self, engine, after, warn):
        import asyncio
        self.engine, self.after, self.warn = engine, after, warn
        self.task = None
        self.again = False
        self.stopping = False
        self.in_flight = 0                   # tool calls past `settled`, not yet recorded
        self._no_call = asyncio.Event()
        self._no_call.set()

    @contextmanager
    def call(self):
        """Around a tool call, from `settled` to its record: a relearning reads its inputs once none is in flight.
        Entered with no await after `settled`, so a relearning cannot start in between."""
        self.in_flight += 1
        self._no_call.clear()
        try:
            yield
        finally:
            self.in_flight -= 1
            if not self.in_flight:
                self._no_call.set()

    def schedule(self) -> None:
        import asyncio
        if self.task is not None and not self.task.done():
            self.again = True
            return
        self.task = asyncio.get_running_loop().create_task(self._loop())

    async def request(self) -> None:
        """A relearning is needed: started, or folded into the one running; awaited once the proxy stops."""
        self.schedule()
        if self.stopping:
            await self.settled()

    async def _loop(self) -> None:
        import asyncio
        while True:
            self.again = False
            await self._no_call.wait()       # the calls already on their way are recorded first
            try:
                self.engine.install(await asyncio.to_thread(self.engine.learn_policy))
                self.after()
            except Exception as e:           # noqa: BLE001 - the closing stays pending on disk; the next relearns
                self.warn(f"background relearning failed ({type(e).__name__}: {e}); the policy in place is kept")
            if not self.again:
                return

    async def settled(self) -> None:
        """Wait until no relearning is running or asked for."""
        import asyncio
        while self.task is not None and not self.task.done():
            await asyncio.shield(self.task)

    async def drain(self) -> None:
        """On shutdown: from now on requests are awaited; wait for the relearning in progress to end."""
        self.stopping = True
        await self.settled()

    async def flush(self, run=None):
        """On shutdown, after `drain`: the open run (of `run`, an isolated session's) is kept for telemetry, as
        `Learner.flush` does, and relearned through this coordinator: in a thread, one relearning at a time."""
        learner = self.engine.learner
        if learner.finish_pending():
            await self.request()
        with learner.use_run(run) if run is not None else nullcontext():
            path = learner.flush(relearn=False)
        if path is not None or learner.relearn_owed:
            await self.request()
        return path


def _server(o: Options):
    import asyncio
    import time

    from mcp import ClientSession, types
    from mcp.server.lowlevel import Server

    from .auto import DONE, DONE_REPLY, Learner, Run
    from .clientlog import ClientLog, SessionKeys
    from .evidence import Adaptive

    adaptive = Adaptive(promote=o.promote, promote_requested_runs=o.promote_runs, sample_rate=o.sample_rate,
                        switch_off_runs=o.switch_off_runs,
                        sample_max_share=o.sample_max_share, sample_seed=o.sample_seed)
    learner = (Learner(o.auto_dir, o.min_runs, idle_seconds=o.idle_seconds, adaptive=adaptive,
                       protect_ids=o.protect_ids, learning=o.learning, compact=o.compact is not False,
                       keep_positions=o.keep_positions is not False,
                       # With the option, a run left out of the report is relearned by the coordinator, in a thread.
                       relearn_at_start=not o.background_relearn)
               if o.auto_dir else None)
    if learner:
        learner.arrivals_reported = True         # on arrival, before any wait (`call_tool`)
    # `--structural` overrides the config: "toon" or "headroom" after the
    # projection (or alone, when there is no config yet).
    engine = Engine(learner, Policy.load(o.config) if o.config else None, o.structural, shared=o.shared,
                    compact=o.compact, breakdown=o.breakdown, keep_positions=o.keep_positions)
    offers_expand = engine.policy is not None or learner is not None
    rec = Path(o.record).open("a", encoding="utf-8") if o.record else None
    live = Live(o.quiet)
    live.per_run = bool(learner)
    last_call = [0.0]
    session_keys = SessionKeys() if o.shared and learner else None

    def log(line):
        if not o.quiet:
            print(f"[tracetrim] {line}", file=sys.stderr, flush=True)

    if learner:
        st = learner.state
        log(f"auto mode: {learner.dir} · {st['complete_runs']} complete runs (learning from {o.min_runs})"
            f" · config {'applied' if engine.trimming else 'not applied yet'}")

    closing = asyncio.Lock()
    isolated = None
    if o.isolate_sessions:
        from .sessions import HttpRuns
        if not (learner and o.shared):
            raise ValueError("--isolate-sessions requires --http and --auto")
        isolated = HttpRuns(engine, lambda: Live(o.quiet, process=live), o.idle_seconds,
                            lambda msg: print(f"[tracetrim] WARNING: {msg}", file=sys.stderr, flush=True))

    def after_learning():
        # What the relearning just published, from its own snapshot (`Learner.published`), not the state now,
        # which a later closing may already have moved on. Without a snapshot (nothing to learn from), the state.
        pub = learner.published
        with learner.lock:
            complete, off = (pub["complete_runs"], pub["off"]) if pub else \
                (learner.state["complete_runs"], list(learner.state["off"]))
        log(f"  {complete} complete runs · report updated · "
            + (f"trimming {len(engine.policy.tools)} tools" if engine.trimming
               else f"learning: {max(0, o.min_runs - complete)} more complete runs before trimming")
            + (f" · switched off: {', '.join(off)}" if off else ""))

    background = None
    if o.background_relearn:
        if not learner:
            raise ValueError("--background-relearn requires --auto")
        background = BackgroundLearning(engine, after_learning,
                                        lambda msg: print(f"[tracetrim] WARNING: {msg}", file=sys.stderr, flush=True))
        learner.background = background          # for the shutdown paths of serve and serve_http

    async def flush_session(session):
        """At shutdown, an isolated session's open run: through the coordinator with the option (in a thread),
        as in 1.3 without it."""
        if background is not None:
            await background.flush(session.run)
        else:
            with learner.use_run(session.run):
                learner.flush()

    async def learn_in_thread():
        """Learnt in a thread (it is slow), installed here, on the event loop: a call being trimmed on the loop never
        sees the policy change halfway through. A caller that goes away (a cancelled request) does not stop the
        worker, which keeps writing: it is awaited, and its policy installed, before the closing lock is given
        back to another writer (REVISIONE-14b-quinquies; until then only isolated sessions waited)."""
        import anyio
        learning = asyncio.ensure_future(asyncio.to_thread(engine.learn_policy))
        cancelled = False
        # Shielded from the cancellation of the request's scope (the SDK cancels a handler when its client cancels
        # the request), and from a cancellation of the task itself, which is delivered once the policy is installed.
        with anyio.CancelScope(shield=True):
            while True:
                try:
                    policy = await asyncio.shield(learning)
                    break
                except asyncio.CancelledError:
                    if learning.cancelled():
                        raise
                    cancelled = True
        engine.install(policy)
        if cancelled:
            raise asyncio.CancelledError

    async def relearn_now(quiet=False):
        """A relearning is owed (an interrupted publication was finished, or the last relearning failed): relearn,
        through the coordinator when there is one (two relearnings never run at once), and before going on.
        `quiet`: a failure is only logged, as the coordinator does, and the request is served with the policy in
        place; the relearning stays owed, and the next closing makes it. Learning never fails a tool call."""
        if background is not None:
            await background.request()
            await background.settled()
            return
        try:
            await learn_in_thread()
        except Exception as e:                   # noqa: BLE001
            if not quiet:
                raise
            print(f"[tracetrim] WARNING: relearning failed ({type(e).__name__}: {e}); the policy in place is kept",
                  file=sys.stderr, flush=True)

    async def close_run(answer, session=None) -> bool:
        """Close the run, if one is open. One closing at a time: a second
        request waits, then finds nothing left to close."""
        async with closing:
            if background is not None:
                await background.settled()        # a closing never overlaps a relearning, as without the option
            # An interrupted publication (closings go through a journal in every mode since 1.4) is finished, and
            # relearned, before this closing writes; if that relearning fails, the one of this closing covers it.
            if learner.finish_pending():
                await relearn_now(quiet=True)
            previous = learner.run.last_number
            try:
                path = engine.done(answer)
            finally:
                # Once the trace is on disk, its original handles must keep
                # pointing to that closed run even if a secondary write failed.
                if session and session.run.last_number != previous:
                    closed = session.run
                    session.run = Run(last_number=closed.last_number, answered_at=closed.answered_at)
            if path is None:
                # Nothing left to close: a repeat, or the retry of a closing that failed. If a relearning is owed (its
                # own failed, or another request finished its publication), it is made before the reply: the retry
                # is confirmed with the policy that covers its run (REVISIONE-14b-quinquies: it replied with the
                # policy of before). Without the option a failure is the reply, and the client can try again.
                if learner.relearn_owed:
                    await relearn_now()
                return False
            # No await since the closing: until its reply, no call counts as following this answer (`replied`).
            answered = bool(answer and answer.strip())
            if answered:
                (session.run if session else learner.run).reply_pending = True
            _warn_mixed(learner)
            run_total = (session.live if session else live).new_run()
            if background is not None:
                # Pending on disk with the run (Learner._write_run); the relearning follows (BackgroundLearning).
                with learner.lock:
                    number = learner.state["runs"]
                log(f"run {number} closed ({'with answer' if answer else 'no answer: telemetry only'}): "
                    f"{run_total} · relearning in the background")
                _log_unsupported()
                await background.request()
                replied(session, answered)
                return True
            await learn_in_thread()
            st = learner.state
            log(f"run {st['runs']} closed ({'with answer' if answer else 'no answer: telemetry only'}): {run_total}")
            after_learning()
            _log_unsupported()
            replied(session, answered)
            return True

    def replied(session, answered):
        """The pause after an answer starts at the reply, as the agent sees it: without the option the reply came
        after the relearning, with it before (until 1.3 the pause was counted from the closing, relearning included)."""
        run = session.run if session else learner.run
        if answered:
            with learner.lock:
                run.answered_at = time.time()
                run.reply_pending = False

    def _log_unsupported():
        if learner.unsupported:
            shown = ", ".join(learner.unsupported[:5]) + (" …" if len(learner.unsupported) > 5 else "")
            one = len(learner.unsupported) == 1
            log(f"  {len(learner.unsupported)} value{'' if one else 's'} of the answer "
                f"appear{'s' if one else ''} in no tool output of the run: {shown} (see the report)")

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
            if isolated:
                await stack.enter_async_context(isolated.lifespan(close_run, flush_session))
            if background is not None:
                # Runs first on exit: the relearning in progress ends before the sessions are drained.
                stack.push_async_callback(background.drain)
                if learner.relearn_owed:
                    background.schedule()        # the recovery at start; requests wait for it
            yield {"sessions": sessions}

    async def all_tools(session):
        """Every tool of one upstream: a server may list them in pages (`nextCursor`), and up to 1.1.4 only the
        first page was read. A cursor seen twice ends the listing, so that a faulty server cannot loop it."""
        tools, cursor, seen = [], None, set()
        while True:
            page = await session.list_tools(params=types.PaginatedRequestParams(cursor=cursor) if cursor else None)
            tools.extend(page.tools)
            cursor = page.next_cursor
            if not cursor or cursor in seen:
                return tools
            seen.add(cursor)

    async def upstream_tools(sessions):
        # The servers are asked together; the names come out in the order of the servers file, whichever
        # answers first, so that the routing never depends on timing.
        pages = await asyncio.gather(*(all_tools(s) for s in sessions.values()))
        listed = dict(zip(sessions, pages))
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
            tools.append(types.Tool(**expand_tool(o.skill_version)))
        if learner:
            tools.append(types.Tool(**DONE))
            if background is not None:
                await background.settled()        # recorded after the relearning in progress, which reads the list
            if learner.finish_pending():
                await relearn_now(quiet=True)
            # The list as published, for the report's section on tool definitions. A record
            # that fails costs the section, never the listing.
            try:
                learner.record_tools([t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools])
            except OSError as e:
                log(f"tool list not recorded: {e}")
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx, params):
        received = time.time()                    # before any wait: the pause after an answer ends here
        if isolated:
            connection = getattr(ctx.session, "_connection", None)
            session = isolated.session(session_keys.transport_key(connection))
        if learner and params.name not in ("tracetrim_done", "expand"):
            # Now, in the order of arrival: the first call after the reply to an answer is the one the pause is
            # measured to, whichever result comes back first (REVISIONE-14b-quinquies).
            learner.arrived(received, session.run if isolated else None)
        if isolated:
            # The event loop may deliver a request before the expiry task gets
            # CPU (e.g. while it is publishing another session's report).
            # On arrival: the pause is the client's, from the reply to its last request.
            expired = (bool(session.run.events) and not session.pending
                       and asyncio.get_running_loop().time() - session.activity >= o.idle_seconds)
            async with isolated.request(session, closing=expired or params.name == "tracetrim_done"):
                # Inside the request: while it waits for a relearning the session is busy, never idle.
                if background is not None:
                    await background.settled()
                if expired:
                    with learner.use_run(session.run):
                        await close_run(None, session)
                with learner.use_run(session.run):
                    return await handle_tool(ctx, params, session, session.live)
        return await handle_tool(ctx, params, None, live)

    async def handle_tool(ctx, params, session, live):
        args = params.arguments or {}
        if background is not None:
            # Every request waits for the relearning in progress (see BackgroundLearning): without the option it
            # came after the reply to the closing that started it.
            await background.settled()
        if learner and learner.finish_pending():
            # An interrupted publication, finished before this request is served; its relearning follows at once.
            await relearn_now(quiet=True)
        if learner and params.name == "tracetrim_done":
            answer = args.get("answer")
            if not isinstance(answer, str):
                answer = "" if answer is None else json.dumps(answer, ensure_ascii=False)
            if not answer.strip():
                # Not a closing: the run stays open for the call that carries the answer.
                engine.refuse()
                return types.CallToolResult(isError=True, content=[types.TextContent(
                    type="text", text="Error: tracetrim_done needs your final answer in `answer`. Nothing was recorded.")])
            if not await close_run(answer, session):
                log("tracetrim_done with no tool call since the last closing (a repeat or a retry): nothing to record")
            # The same reply whether the run was closed now or already: it asks for nothing more.
            text = DONE_REPLY
        elif params.name == "expand":
            fields = requested_fields(args.get("fields"))
            cid = str(args.get("call_id", ""))
            text = isolated.expand(session, cid, fields) if isolated else engine.expand(cid, fields)
            live.expand(cid, fields, text)
        else:
            if not isolated:
                if learner and learner.events and time.time() - last_call[0] > o.idle_seconds:
                    await close_run(None)
                    if background is not None:
                        await background.settled()    # served after the relearning of that closing, as without
                # After any wait: the next call measures the client's pause, never the relearning (REVISIONE-14b-bis).
                last_call[0] = time.time()
            # No await since `settled`: from here until it is recorded, a relearning waits for this call.
            with background.call() if background is not None else nullcontext():
                return await upstream_call(ctx, params, args, session, live)
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

    async def upstream_call(ctx, params, args, session, live):
        """A tool call to its upstream server: the result recorded, trimmed when the policy says so."""
        sessions = ctx.lifespan_context["sessions"]
        if params.name not in routes:           # a client that calls before listing
            await upstream_tools(sessions)
        if params.name not in routes:
            return types.CallToolResult(content=[types.TextContent(type="text", text=f"Error: unknown tool {params.name}.")],
                                        isError=True)
        server, name = routes[params.name]
        r = await sessions[server].call_tool(name, args)
        key = session_keys.for_call(getattr(ctx.session, "_connection", None)) if session_keys and not isolated else None
        call = ToolCall(params.name, args, server=server, upstream_tool=name, session_key=key)
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
            seen = engine.observe(call, ToolResult(note, verbatim=True))
            live.call(params.name, note, note, seen.sizes)
            return r                     # exactly as the server sent it, metadata included
        raw = "".join(getattr(c, "text", "") or "" for c in content)
        if rec:
            rec.write(json.dumps({"kind": "call", "tool": params.name, "arguments": args, "output": raw},
                                 ensure_ascii=False) + "\n")
            rec.flush()
        obs = engine.observe(call, ToolResult(raw))
        if isolated:
            isolated.remember(session, obs.call_id)
        text = obs.text
        live.call(params.name, raw, text, obs.sizes)
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

    # The skill explains expand and tracetrim_done: published whenever expand is offered.
    publish = o.skill and offers_expand
    server = Server("tracetrim-proxy", version=VERSION, lifespan=upstream, on_list_tools=list_tools,
                    on_call_tool=call_tool,
                    # One sentence, for hosts that show a server's instructions and do not load skills.
                    instructions=skill.pointer(done=learner is not None) if publish else None)
    if o.log_client:
        client_log = ClientLog(o.log_client, session_keys)

        async def log_request(ctx, call_next):
            # Requests only (a notification has no id), written before they are served:
            # a method the proxy does not know is a line too. Diagnostics never fail a request.
            if ctx.request_id is not None:
                try:
                    client_log.record(getattr(ctx.session, "_connection", None), ctx.method, ctx.params)
                except Exception as e:  # noqa: BLE001
                    log(f"--log-client: line not written ({type(e).__name__})")
            return await call_next(ctx)

        server.middleware.append(log_request)
        log(f"client requests logged to {o.log_client} (methods, declared capabilities, names: no arguments or results)")
    if publish:
        entry = publish_skill(server, strategy=o.skill_strategy, done=learner is not None, version=o.skill_version)
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
    # SIGTERM (how a host, or `tracetrim init`'s check, stops the proxy) closes it in order: the upstream servers,
    # started in sessions of their own, are stopped with it instead of being left running (until 1.0).
    import asyncio
    import signal
    task = asyncio.current_task()
    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    except (NotImplementedError, RuntimeError, AttributeError):   # Windows: no signal handlers on the loop
        pass
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    except asyncio.CancelledError:
        pass
    finally:
        if learner:
            if getattr(learner, "background", None) is not None:
                await learner.background.drain()
                await learner.background.flush()      # relearned in a thread, through the coordinator
            else:
                learner.flush()


LIVE_SECONDS = 5      # how often the live report asks whether it changed


def report_stamp(page: Path) -> str:
    """What changes when the report is written again: its size and modification time. "none" before the first."""
    try:
        st = page.stat()
    except OSError:
        return "none"
    return f"{st.st_mtime_ns:x}-{st.st_size:x}"


async def _respond(send, status, body, ctype=b"text/plain; charset=utf-8"):
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", ctype), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})


def _same(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode(), expected.encode())


def guard(app, live, token: str | None):
    """The HTTP front: the live report (when there is one), then the bearer token on everything else."""
    async def guarded(scope, receive, send):
        if live is not None and await live(scope, send):
            return
        if token and scope["type"] == "http":
            auth = dict(scope.get("headers") or []).get(b"authorization", b"").decode("latin-1")
            if not _same(auth, f"Bearer {token}"):
                return await _respond(send, 401, b"unauthorized")
        await app(scope, receive, send)
    return guarded


def live_report(workdir: Path, hosts: list[str], token: str | None, seconds: int = LIVE_SECONDS):
    """The live report, over HTTP: `/report` serves the page, `/report/stamp` says whether it changed.

    Until 1.0 the page reloaded itself every 10 seconds (a refresh meta): scroll position and open sections were
    lost at every reload. Since 1.1 the page asks for the stamp, a few bytes, and when it changed fetches the page
    again and swaps its content in place (the script is in the page: tracetrim.report). Both paths check the Host
    header and the token as the page always did. Returns an ASGI-style handler: `await handle(scope, send)` is True
    when it answered the request."""
    from urllib.parse import parse_qs

    page = Path(workdir) / "report.html"
    respond, same = _respond, _same

    async def handle(scope, send) -> bool:
        if scope.get("type") != "http" or scope.get("path") not in ("/report", "/report/stamp"):
            return False
        host = dict(scope.get("headers") or []).get(b"host", b"").decode("latin-1")
        # As the SDK accepts them for /mcp: an exact host, or `name:*` for any port.
        if host not in hosts and not any(h.endswith(":*") and host.rsplit(":", 1)[0] == h[:-2] for h in hosts):
            await respond(send, 421, b"invalid Host header: add it with --allowed-host")
            return True
        q = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        if token and not same(q.get("token", [""])[0], token):
            await respond(send, 401, b"unauthorized")
            return True
        stamp = report_stamp(page)
        if scope["path"] == "/report/stamp":
            await respond(send, 200, stamp.encode())
            return True
        live = f'<meta name="tracetrim-live" content="{seconds}"><meta name="tracetrim-stamp" content="{stamp}">'.encode()
        if stamp == "none":
            html = (b"<!doctype html><html><head><meta charset=utf-8>" + live + b"<title>tracetrim</title></head>"
                    b"<body style='font:16px system-ui;padding:24px'>tracetrim is observing: the report appears "
                    b"after the first run.<script>" + WAITING_JS + b"</script></body></html>")
        else:
            try:
                html = page.read_bytes().replace(b"<head>", b"<head>" + live, 1)
            except OSError:                  # replaced between the stat and the read: the next request has it
                await respond(send, 503, b"the report is being written: try again")
                return True
        await respond(send, 200, html, b"text/html; charset=utf-8")
        return True

    return handle


# Before the first report there is no page to update in place: the placeholder reloads once the stamp appears.
WAITING_JS = b"""(function(){var m=document.querySelector('meta[name="tracetrim-live"]');var s=(+m.content||5)*1000;
function tick(){fetch(location.pathname+'/stamp'+location.search,{cache:'no-store'}).then(function(r){return r.text();})
.then(function(t){if(t&&t!=='none')location.reload();else setTimeout(tick,s);},function(){setTimeout(tick,s*2);});}
setTimeout(tick,s);})();"""


async def serve_http(o: Options, *, host: str, port: int, token: str | None, allowed_hosts: list[str]) -> None:
    """Streamable HTTP at /mcp, for hosted clients (e.g. the OpenAI Playground)
    that call the server from their own infrastructure. In automatic mode the
    live report is served at /report?token=..., updating itself in place (`live_report`).

    Exposed through a tunnel, the proxy is reachable by anyone who knows the
    URL, and it holds the upstream credentials: `token` makes every request
    carry `Authorization: Bearer <token>` (the report page, opened in a
    browser, takes it as a query parameter instead).
    """
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

    app = server.streamable_http_app(host=host, transport_security=security)
    guarded = guard(app, live_report(learner.dir, hosts, token) if learner else None, token)

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
            background = getattr(learner, "background", None) if learner else None
            if background is not None:
                await background.drain()
            if learner and not o.isolate_sessions:
                # With the option the run is relearned through the coordinator, in a thread (REVISIONE-14b-bis).
                kept = await background.flush() if background is not None else learner.flush()
                if kept is not None:
                    _warn_mixed(learner)

    await FlushingServer(uvicorn.Config(guarded, host=host, port=port, log_level="warning")).serve()
