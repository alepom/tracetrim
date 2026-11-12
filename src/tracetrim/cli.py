"""tracetrim — learn from agent traces which tool-output fields are visibly reused.

  tracetrim learn   traces/*.jsonl -o projection.json [--mode prudent|aggressive] [--source full|calls]
  tracetrim apply   projection.json|report.config.json TOOL < output.json
  tracetrim measure traces/*.jsonl [--source full|calls] [--per-tool]
  tracetrim report  traces/*.jsonl -o report.html     (also report.json, .config.json, .projection.json)
  tracetrim login   --servers mcp.json                 (once, for servers that use OAuth)
  tracetrim proxy   --servers mcp.json | --upstream-cmd "python -m server" | --upstream-url URL [--header K=V]
                    [--config report.config.json | --auto workdir/] [--record traces/session.jsonl]
                    [--skill-strategy] [--no-skill]
                    [--promote [--promote-runs 2]] [--sample-rate 0.1 [--sample-max-share 0.2] [--sample-seed 0]]
                    [--switch-off-runs 2]
  tracetrim promotions WORKDIR [--revert [TOOL [FIELD]] | --restore TOOL FIELD]
  tracetrim skill   [--strategy] [--no-done] [--write DIR]   (the instructions the proxy publishes as a skill)

`measure` always starts by showing what it read: a parser that fails
silently produces plausible, wrong numbers.
"""
from __future__ import annotations

import argparse
import builtins
import json
import os
import sys
from pathlib import Path

from . import measure as m
from .evidence import Adaptive
from .projection import MODES, SOURCES, UNSEEN, Projection, learn
from .traces import load

# The default of --switch-off-runs, read from the library so that the two cannot drift apart.
SWITCH_OFF_RUNS = Adaptive.__dataclass_fields__["switch_off_runs"].default
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
    return traces, [analyse(t, normalize=not v01, comma_grouping=not v01) for t in traces]


def cmd_learn(a):
    _, usages = _read(a.traces, a.v01)
    p = learn(usages, a.mode, a.source, a.unseen, protect_ids=not a.v01)
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
    from . import turns
    d["turns"] = turns.summarise(traces, a.strategy_max_several, a.strategy_min_earlier, a.strategy_min_runs)
    sugg = rp.suggestions(d)
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    cfg_path, proj_path = out.with_suffix(".config.json"), out.with_suffix(".projection.json")
    config, projection = rp.build_config(d, sugg, usages, proj_path.name)
    projection.save(proj_path)
    cfg_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    d["suggestions"], d["config"], d["config_file"] = sugg, config, cfg_path.name
    from . import arguments, lists
    d["lists"] = lists.summarise([lists.element_usage(t, normalize=not a.v01, comma_grouping=not a.v01) for t in traces])
    d["arguments"] = arguments.cardinality(traces)
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
    defaults = Adaptive()
    adaptive = {"--promote": a.promote, "--promote-runs": a.promote_runs != defaults.promote_requested_runs,
                "--sample-rate": bool(a.sample_rate), "--sample-max-share": a.sample_max_share != defaults.sample_max_share,
                "--sample-seed": a.sample_seed != defaults.sample_seed,
                "--switch-off-runs": a.switch_off_runs != defaults.switch_off_runs}
    given = [name for name, on in adaptive.items() if on]
    if given and not a.auto:
        raise SystemExit(f"{', '.join(given)}: adaptive learning works in automatic mode only; add --auto WORKDIR")
    if adaptive["--promote-runs"] and not a.promote:
        raise SystemExit("--promote-runs sets the threshold of --promote: add --promote")
    if (adaptive["--sample-max-share"] or adaptive["--sample-seed"]) and not a.sample_rate:
        raise SystemExit("--sample-max-share and --sample-seed set up sampling: add --sample-rate")
    try:
        Adaptive(promote=a.promote, promote_requested_runs=a.promote_runs, sample_rate=a.sample_rate,
                 sample_max_share=a.sample_max_share, sample_seed=a.sample_seed, switch_off_runs=a.switch_off_runs)
    except ValueError as e:
        raise SystemExit(f"tracetrim proxy: {e}") from None
    o = Options(upstream_cmd=a.upstream_cmd, upstream_url=a.upstream_url,
                headers=dict(h.split("=", 1) for h in a.header), config=a.config, record=a.record,
                quiet=a.quiet, structural=a.structural, auto_dir=a.auto, min_runs=a.min_runs,
                skill=not a.no_skill, skill_strategy=a.skill_strategy,
                promote=a.promote, promote_runs=a.promote_runs, sample_rate=a.sample_rate,
                switch_off_runs=a.switch_off_runs,
                sample_max_share=a.sample_max_share, sample_seed=a.sample_seed,
                servers=load_servers(a.servers) if a.servers else {})
    if a.http:
        asyncio.run(serve_http(o, host=a.host, port=a.http, token=a.token, allowed_hosts=a.allowed_host))
    else:
        asyncio.run(serve(o))


def cmd_promotions(a):
    """List the fields promoted on evidence in a work directory, or take some back."""
    from . import evidence
    from .auto import Learner
    work = Path(a.workdir)
    path = work / "evidence.json"
    data = evidence.load(path)
    changed = []
    if a.revert is not None:
        if len(a.revert) > 2:
            raise SystemExit("--revert takes nothing (all), a TOOL, or a TOOL and a FIELD")
        changed = evidence.revert(data, *a.revert)
    elif a.restore:
        changed = evidence.restore(data, *a.restore)
    if changed:
        evidence.save(path, data)
        # Rewrite config and report now; a running proxy picks the change up at its next closing.
        # With the settings the work directory was learned with (kept in report.json), not the defaults:
        # a relearning with other thresholds could switch a tool off that the proxy would keep.
        from dataclasses import fields
        from .evidence import Adaptive
        config = json.loads((work / "report.config.json").read_text()) if (work / "report.config.json").exists() else {}
        report = json.loads((work / "report.json").read_text()) if (work / "report.json").exists() else {}
        saved = (report.get("adaptive") or {}).get("settings") or {}
        known = {f.name for f in fields(Adaptive)}
        adaptive = Adaptive(**{k: v for k, v in saved.items() if k in known})
        Learner(work, config.get("auto", {}).get("min_runs", 5), adaptive=adaptive).relearn()
    for p in data["promotions"]:
        mark = " <- changed" if p in changed else ""
        print(f"{p['status']:<9} {p['tool']}  {p['field']}  ({p['reason']} in {len(p['runs'])} run"
              f"{'' if len(p['runs']) == 1 else 's'}: {', '.join(p['runs'])}){mark}")
    if not data["promotions"]:
        print("no promotions")
    elif (a.revert is not None or a.restore) and not changed:
        print("nothing changed: no promotion matches")


def cmd_skill(a):
    """The skill as text: for the system prompt of an agent whose host does not
    load MCP skills, or (--write) as a SKILL.md folder for hosts that read
    skills from disk."""
    from . import skill
    kw = {"strategy": a.strategy, "done": not a.no_done}
    if a.write:
        path = Path(a.write) / skill.NAME / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(skill.skill_md(**kw), encoding="utf-8")
        print(path)
    else:
        sys.stdout.write(skill.text(**kw))


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
            group = getattr(builtins, "BaseExceptionGroup", None)       # Python 3.11+
            while group and isinstance(e, group) and e.exceptions:    # anyio wraps the real cause
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
    s.add_argument("--v01", action="store_true",
                   help="tracetrim 0.1 behaviour: no identifier protection, no transformed values")
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
    from .turns import MAX_SEVERAL, MIN_EARLIER, MIN_RUNS
    s.add_argument("--strategy-max-several", type=float, default=MAX_SEVERAL, metavar="F",
                   help="the hint on the skill's strategy is given when at most this share of the turns with calls "
                        f"make several calls (default {MAX_SEVERAL}) ...")
    s.add_argument("--strategy-min-earlier", type=float, default=MIN_EARLIER, metavar="F",
                   help=f"... and at least this share of the calls could have been made in an earlier turn (default {MIN_EARLIER}) ...")
    s.add_argument("--strategy-min-runs", type=int, default=MIN_RUNS, metavar="N",
                   help=f"... in at least this many runs that record their turns (default {MIN_RUNS})")
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
    s.add_argument("--token", default=os.environ.get("TRACETRIM_TOKEN"),
                   help="require 'Authorization: Bearer TOKEN' on every HTTP request "
                        "(default: $TRACETRIM_TOKEN, which keeps it out of the process list)")
    s.add_argument("--allowed-host", action="append", default=[],
                   help="Host header accepted over HTTP (repeatable), e.g. the tunnel's public host")
    s.add_argument("--quiet", action="store_true", help="no per-call log on stderr")
    s.add_argument("--auto", metavar="WORKDIR",
                   help="automatic mode: record runs, learn, apply safe actions, keep the report (see tracetrim.auto)")
    s.add_argument("--min-runs", type=int, default=5, help="complete runs before trimming starts (--auto; at least 2)")
    s.add_argument("--structural", choices=["toon", "headroom"],
                   help="re-encode outputs after the projection (headroom: experimental, needs tracetrim[headroom])")
    s.add_argument("--skill-strategy", action="store_true",
                   help="add to the published skill the optional notes on organising tool calls (off by default)")
    s.add_argument("--no-skill", action="store_true",
                   help="do not publish the tracetrim skill (MCP Skills): the tool descriptions remain")
    s.add_argument("--promote", action="store_true",
                   help="--auto: keep from then on a field the agent asked back with expand often enough "
                        "(off by default)")
    s.add_argument("--promote-runs", type=int, default=2, metavar="N",
                   help="runs in which a field must be asked for by name before it is promoted (default 2); "
                        "a field reused after recovery is promoted after one")
    s.add_argument("--sample-rate", type=float, default=0.0, metavar="F",
                   help="--auto: share of the results that would be trimmed, passed whole instead, to see whether "
                        "the agent uses what is removed (off by default)")
    s.add_argument("--sample-max-share", type=float, default=0.2, metavar="F",
                   help="cost limit of sampling: tokens it may add, as a share of the tokens trimming removed (default 0.2)")
    s.add_argument("--sample-seed", type=int, default=0, help="seed of the sampling sequence")
    s.add_argument("--switch-off-runs", type=int, default=SWITCH_OFF_RUNS, metavar="N",
                   help="--auto: stop trimming a tool only when its results were expanded in at least N distinct "
                        f"runs (default {SWITCH_OFF_RUNS}; 1 restores the behaviour up to 0.3.5)")
    s.set_defaults(func=cmd_proxy)

    s = sub.add_parser("promotions", help="list the fields promoted on evidence (--auto), or take them back")
    s.add_argument("workdir")
    s.add_argument("--revert", nargs="*", metavar="TOOL_OR_FIELD",
                   help="take promotions back: all of them, a TOOL's, or TOOL FIELD; the field is trimmed again")
    s.add_argument("--restore", nargs=2, metavar=("TOOL", "FIELD"), help="make a reverted promotion active again")
    s.set_defaults(func=cmd_promotions)

    s = sub.add_parser("skill", help="print the tracetrim skill, for agents whose host does not load MCP skills")
    s.add_argument("--strategy", action="store_true", help="include the notes on organising tool calls")
    s.add_argument("--no-done", action="store_true",
                   help="leave out tracetrim_done (function calling: the loop passes the answer itself)")
    s.add_argument("--write", metavar="DIR", help="write DIR/tracetrim/SKILL.md instead of printing the text")
    s.set_defaults(func=cmd_skill)

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
