"""tracetrim — learn from agent traces which tool-output fields are used.

  tracetrim learn   traces/*.jsonl -o projection.json [--mode prudent|aggressive] [--source full|calls]
  tracetrim apply   projection.json|report.config.json TOOL < output.json
  tracetrim measure traces/*.jsonl [--source full|calls] [--per-tool]
  tracetrim report  traces/*.jsonl -o report.html     (also report.json, .config.json, .projection.json)
  tracetrim login   --servers mcp.json                 (once, for servers that use OAuth)
  tracetrim proxy   --servers mcp.json | --upstream-cmd "python -m server" | --upstream-url URL [--header K=V]
                    [--config report.config.json | --auto workdir/] [--record traces/session.jsonl]

`measure` always starts by showing what it read: a parser that fails
silently produces plausible, wrong numbers.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import measure as m
from .projection import MODES, SOURCES, UNSEEN, Projection, learn
from .traces import load
from .usage import analyse, leaves


def fmt(n) -> str:
    return f"{int(n):,}"


def pct(a, b) -> str:
    return f"{(a / b - 1) * 100:+.0f}%" if b else "—"


def row(cols, widths) -> str:
    return "  " + "".join(f"{c:<{w}}" if i == 0 else f"{c:>{w}}"
                          for i, (c, w) in enumerate(zip(cols, widths)))


def _read(paths, v01=False):
    traces = [load(p) for p in paths]
    return traces, [analyse(t, normalize=not v01) for t in traces]


def cmd_learn(a):
    _, usages = _read(a.traces)
    p = learn(usages, a.mode, a.source, a.unseen)
    p.save(a.output)
    fields = sum(len(v) for v in p.keep.values())
    print(f"{a.output}: {len(p.keep)} tools, {fields} fields kept ({a.mode}, source={a.source})")


def cmd_apply(a):
    fmt_ = json.loads(Path(a.projection).read_text(encoding="utf-8")).get("format", "")
    if fmt_.startswith("tracetrim/config"):
        from .runtime import Policy
        sys.stdout.write(Policy.load(a.projection).apply(a.tool, sys.stdin.read()))
    else:
        sys.stdout.write(Projection.load(a.projection).apply(a.tool, sys.stdin.read()))


def cmd_measure(a):
    traces, usages = _read(a.traces, a.v01)

    print("\n  TRACES READ\n")
    w = [28, 8, 8, 8, 10, 8]
    print(row(["trace", "calls", "json", "other", "leaves", "used"], w))
    print("  " + "-" * sum(w))
    for t, us in zip(traces, usages):
        js = sum(u.structured for u in us)
        lv = sum(sum(1 for _ in leaves(u.payload)) for u in us if u.structured)
        print(row([t.name[:27], len(us), js, len(us) - js, fmt(lv), sum(len(u.used) for u in us)], w))
    print("  json = structured output · other = prose, errors, empty")

    rows = m.leave_one_out([t.name for t in traces], usages, a.source, a.unseen, protect_ids=not a.v01)
    seen = "everything the agent wrote" if a.source == "full" else "tool calls only (MCP proxy)"
    print(f"\n\n  LEAVE-ONE-OUT PROJECTION   learned from: {seen} · unseen fields: {a.unseen}\n")
    w = [28, 12, 12, 7, 12, 7, 9, 7]
    print(row(["", "", "prudent", "", "aggressive", "", "misses", ""], w))
    print(row(["trace", "raw", "", "", "", "", "all", "text"], w))
    print("  " + "-" * sum(w))
    tot = m.Row("TOTAL")
    for r in rows:
        print(row([r.name[:27], fmt(r.raw), fmt(r.prudent), pct(r.prudent, r.raw),
                   fmt(r.aggressive), pct(r.aggressive, r.raw),
                   f"{r.misses}/{r.trimmed_calls}", r.misses_text], w))
        for k in ("raw", "prudent", "aggressive", "trimmed_calls", "misses", "misses_text", "misses_prudent"):
            setattr(tot, k, getattr(tot, k) + getattr(r, k))
    print("  " + "-" * sum(w))
    print(row(["TOTAL", fmt(tot.raw), fmt(tot.prudent), pct(tot.prudent, tot.raw),
               fmt(tot.aggressive), pct(tot.aggressive, tot.raw),
               f"{tot.misses}/{tot.trimmed_calls}", tot.misses_text], w))
    print(f"\n  tokens: o200k_base, each output counted once · prudent misses: "
          f"{tot.misses_prudent}/{tot.trimmed_calls}")
    print("  misses: trimmed calls hiding a field this trace uses later · "
          "text: confirmed by a string, not a number")

    if a.per_tool:
        agg = {}
        for r in rows:
            for t, v in r.per_tool.items():
                agg[t] = [x + y for x, y in zip(agg.get(t, [0] * 6), v)]
        print("\n\n  PER TOOL\n")
        w = [44, 7, 12, 12, 7, 12, 7, 9]
        print(row(["tool", "calls", "raw", "prudent", "", "aggressive", "", "misses"], w))
        print("  " + "-" * sum(w))
        for t, (n, raw, pr, ag, tr, ms) in sorted(agg.items(), key=lambda x: -x[1][1]):
            print(row([t[:43], n, fmt(raw), fmt(pr), pct(pr, raw), fmt(ag), pct(ag, raw),
                       f"{ms}/{tr}" if tr else "—"], w))
    print()


def cmd_report(a):
    from . import report as rp
    traces, usages = _read(a.traces, a.v01)
    d = rp.compute([t.name for t in traces], usages, a.unseen, protect_ids=not a.v01)
    sugg = rp.suggestions(d)
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    cfg_path, proj_path = out.with_suffix(".config.json"), out.with_suffix(".projection.json")
    config, projection = rp.build_config(d, sugg, usages, proj_path.name)
    projection.save(proj_path)
    cfg_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    d["suggestions"], d["config"], d["config_file"] = sugg, config, cfg_path.name
    out.write_text(rp.render_html(d, sugg), encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    auto = sum(s["action"]["kind"] == "auto" for s in sugg)
    print(f"{out}, {out.with_suffix('.json').name}, {cfg_path.name}, {proj_path.name}: "
          f"{d['totals']['traces']} traces, {d['totals']['calls']} calls, "
          f"{len(sugg)} suggestions ({auto} automatic)")


def cmd_proxy(a):
    import asyncio
    from .proxy import Options, serve, serve_http
    from .proxy import load_servers
    if sum(map(bool, (a.upstream_cmd, a.upstream_url, a.servers))) != 1:
        raise SystemExit("give exactly one of --servers, --upstream-cmd or --upstream-url")
    if a.auto and a.config:
        raise SystemExit("--auto manages its own config: do not pass --config")
    o = Options(upstream_cmd=a.upstream_cmd, upstream_url=a.upstream_url,
                headers=dict(h.split("=", 1) for h in a.header), config=a.config, record=a.record,
                quiet=a.quiet, structural=a.structural, auto_dir=a.auto, min_runs=a.min_runs,
                servers=load_servers(a.servers) if a.servers else {})
    if a.http:
        asyncio.run(serve_http(o, host=a.host, port=a.http, token=a.token, allowed_hosts=a.allowed_host))
    else:
        asyncio.run(serve(o))


def _upstreams(a) -> dict:
    from .proxy import Options, load_servers
    if bool(a.servers) == bool(a.upstream_url):
        raise SystemExit("give --servers FILE or --upstream-url URL")
    o = Options(upstream_url=a.upstream_url, servers=load_servers(a.servers) if a.servers else {})
    ups = {n: s for n, s in o.upstreams().items() if (not a.server or n in a.server)}
    from .oauth import wants_oauth
    return {n: s for n, s in ups.items() if wants_oauth(s)}


def cmd_login(a):
    """Log in to every OAuth server now, so the proxy never waits for a browser."""
    import asyncio
    from mcp import ClientSession
    from .oauth import token_path
    from .proxy import transport_for

    async def one(name, spec):
        async with transport_for(name, spec) as (read, write, *_), ClientSession(read, write) as s:
            await s.initialize()
            return len((await s.list_tools()).tools)

    ups = _upstreams(a)
    if not ups:
        print("no OAuth server (a remote server with 'url' and no 'headers', or \"auth\": \"oauth\")")
    for name, spec in ups.items():
        try:
            n = asyncio.run(one(name, spec))
            print(f"{name}: logged in, {n} tools · token in {token_path(name, spec['url'])}")
        except Exception as e:
            while isinstance(e, BaseExceptionGroup) and e.exceptions:    # anyio wraps the real cause
                e = e.exceptions[0]
            raise SystemExit(f"{name}: login failed: {e}")


def cmd_logout(a):
    from .oauth import token_path
    for name, spec in _upstreams(a).items():
        p = token_path(name, spec["url"])
        print(f"{name}: {'token removed' if p.exists() else 'no token'}")
        p.unlink(missing_ok=True)


def main(argv=None):
    p = argparse.ArgumentParser(prog="tracetrim", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("learn", help="learn a projection from traces")
    s.add_argument("traces", nargs="+")
    s.add_argument("-o", "--output", default="projection.json")
    s.add_argument("--mode", choices=MODES, default="prudent")
    s.add_argument("--source", choices=SOURCES, default="full")
    s.add_argument("--unseen", choices=UNSEEN, default="keep")
    s.set_defaults(func=cmd_learn)

    s = sub.add_parser("apply", help="trim one tool output read from stdin")
    s.add_argument("projection")
    s.add_argument("tool")
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("measure", help="leave-one-out evaluation on traces")
    s.add_argument("traces", nargs="+")
    s.add_argument("--source", choices=SOURCES, default="full")
    s.add_argument("--per-tool", action="store_true")
    s.add_argument("--unseen", choices=UNSEEN, default="keep",
                   help="drop reproduces the original measurements")
    s.add_argument("--v01", action="store_true",
                   help="tracetrim 0.1 behaviour: no identifier protection, no transformed values")
    s.set_defaults(func=cmd_measure)

    s = sub.add_parser("report", help="bilingual telemetry report (HTML + JSON)")
    s.add_argument("traces", nargs="+")
    s.add_argument("-o", "--output", default="report.html")
    s.add_argument("--unseen", choices=UNSEEN, default="keep")
    s.add_argument("--v01", action="store_true",
                   help="tracetrim 0.1 behaviour (for comparisons): no identifier protection, no transformed values")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("proxy", help="MCP proxy: upstream tools with trimmed outputs and expand")
    s.add_argument("--servers", metavar="FILE",
                   help="JSON with your mcpServers (as in Claude Desktop / Claude Code / Cursor): one proxy for all")
    s.add_argument("--upstream-cmd", help="command that starts the upstream MCP server over stdio")
    s.add_argument("--upstream-url", help="URL of an upstream streamable-HTTP MCP server")
    s.add_argument("--header", action="append", default=[], help="K=V header for the upstream URL")
    s.add_argument("--config", help="runtime config written by `tracetrim report`")
    s.add_argument("--record", help="append every call (raw output) to this JSONL trace")
    s.add_argument("--http", type=int, metavar="PORT", help="serve streamable HTTP at /mcp instead of stdio")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--token", help="require 'Authorization: Bearer TOKEN' on every HTTP request")
    s.add_argument("--allowed-host", action="append", default=[],
                   help="Host header accepted over HTTP (repeatable), e.g. the tunnel's public host")
    s.add_argument("--quiet", action="store_true", help="no per-call log on stderr")
    s.add_argument("--auto", metavar="WORKDIR",
                   help="automatic mode: record runs, learn, apply safe actions, keep the report (see tracetrim.auto)")
    s.add_argument("--min-runs", type=int, default=5, help="complete runs before trimming starts (--auto)")
    s.add_argument("--structural", choices=["toon", "headroom"],
                   help="re-encode outputs after the projection (headroom: experimental, needs tracetrim[headroom])")
    s.set_defaults(func=cmd_proxy)

    for cmd, func, text in (("login", cmd_login, "log in to the OAuth servers (browser), once"),
                            ("logout", cmd_logout, "forget the OAuth tokens")):
        s = sub.add_parser(cmd, help=text)
        s.add_argument("--servers", metavar="FILE", help="the same mcpServers file given to the proxy")
        s.add_argument("--upstream-url", help="a single OAuth server")
        s.add_argument("--server", action="append", help="only this server of the file (repeatable)")
        s.set_defaults(func=func)

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
