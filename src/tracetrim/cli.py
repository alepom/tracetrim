"""tracetrim — learn from agent traces which tool-output fields are visibly reused.

  tracetrim learn   traces/*.jsonl -o projection.json [--mode prudent|aggressive] [--source full|calls]
  tracetrim apply   projection.json|report.config.json TOOL < output.json
  tracetrim measure traces/*.jsonl [--source full|calls] [--per-tool]
  tracetrim compare traces/*.jsonl --a protect_ids=all --b protect_ids=record [--json] [--html FILE]
  tracetrim report  traces/*.jsonl -o report.html     (also report.json, .config.json, .projection.json)
  tracetrim login   --servers mcp.json                 (once, for servers that use OAuth)
  tracetrim proxy   --servers mcp.json | --upstream-cmd "python -m server" | --upstream-url URL [--header K=V]
                    [--config report.config.json | --auto workdir/] [--record traces/session.jsonl]
                    [--skill-strategy] [--no-skill]
                    [--promote [--promote-runs 2]] [--sample-rate 0.1 [--sample-max-share 0.2] [--sample-seed 0]]
                    [--switch-off-runs 2]
  tracetrim promotions WORKDIR [--revert [TOOL [FIELD]] | --restore TOOL FIELD]
  tracetrim skill   [--strategy] [--no-done] [--write DIR]   (the instructions the proxy publishes as a skill)
  tracetrim init    --host claude-desktop|claude-code|cursor|vscode [--file PATH] [--write]   (dry run without --write)
  tracetrim init    --undo BACKUP

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
from .about import VERSION
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


def _read(paths, v01=False, learning="1.1"):
    traces = [load(p) for p in paths]
    from .usage import learning_flags
    flags = learning_flags("1.0" if v01 else learning)
    return traces, [analyse(t, normalize=not v01, comma_grouping=not v01, **flags) for t in traces]


def _protect(a):
    """--v01 means no protection; --protect-ids picks the rule otherwise (since 1.1)."""
    if a.v01 and a.protect_ids != "all":
        raise SystemExit("--v01 already turns identifier protection off: drop --protect-ids")
    # The values of 1.0 (true, false) where they existed, so that report.json keeps its meaning.
    return False if a.v01 else (True if a.protect_ids == "all" else a.protect_ids)


def cmd_learn(a):
    _, usages = _read(a.traces, a.v01, a.learning)
    p = learn(usages, a.mode, a.source, a.unseen, protect_ids=_protect(a))
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
    traces, usages = _read(a.traces, a.v01, a.learning)

    print("\n  TRACES READ\n")
    w = [28, 8, 8, 8, 10, 8]
    print(row(["trace", "calls", "json", "other", "leaves", "used"], w))
    print("  " + "-" * sum(w))
    for t, us in zip(traces, usages):
        js = sum(u.structured for u in us)
        lv = sum(sum(1 for _ in leaves(u.payload)) for u in us if u.structured)
        print(row([t.name[:27], len(us), js, len(us) - js, fmt(lv), sum(len(u.used) for u in us)], w))
    print("  json = structured output · other = prose, errors, empty")

    rows = m.leave_one_out([t.name for t in traces], usages, a.source, a.unseen, protect_ids=_protect(a))
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
    traces, usages = _read(a.traces, a.v01, a.learning)
    d = rp.compute([t.name for t in traces], usages, a.unseen, protect_ids=_protect(a),
                   runtime=not a.v01 and a.learning != "1.0")
    from . import grounding
    d["grounding"] = grounding.summarise(traces)
    from . import turns
    d["turns"] = turns.summarise(traces, a.strategy_max_several, a.strategy_min_earlier, a.strategy_min_runs)
    if a.tools:
        from . import definitions, measure
        published = definitions.read(a.tools)
        if published is None:
            raise SystemExit(f"{a.tools}: not a tool list (a JSON list of tools, or a tools/list result)")
        print(f"tool list: {len(published)} tools read from {a.tools}")
        # As in automatic mode, only complete runs count: those that end with the agent's answer.
        complete = [t for t in traces if t.events and t.events[-1].kind == "message"]
        d["definitions"] = definitions.summarise(published, [{c.tool for c in t.calls} for t in complete],
                                                 measure.tokenizer(), rp.MIN_TRACES)
    sugg = rp.suggestions(d)
    out = Path(a.output)
    if out.suffix.lower() == ".json":
        raise SystemExit("-o names the HTML page (report.html); report.json, the config and the projection are "
                         "written next to it")
    out.parent.mkdir(parents=True, exist_ok=True)
    cfg_path, proj_path = out.with_suffix(".config.json"), out.with_suffix(".projection.json")
    config, projection = rp.build_config(d, sugg, usages, proj_path.name)
    projection.save(proj_path)
    cfg_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    d["suggestions"], d["config"], d["config_file"] = sugg, config, cfg_path.name
    from . import arguments, lists
    from .usage import learning_flags
    rules = learning_flags("1.0" if a.v01 else a.learning)
    d["lists"] = lists.summarise([lists.element_usage(t, normalize=not a.v01, comma_grouping=not a.v01, **rules)
                                  for t in traces])
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
    if a.background_relearn and not a.auto:
        raise SystemExit("--background-relearn relearns in the background in automatic mode: add --auto WORKDIR")
    if a.isolate_sessions and not (a.http and a.auto):
        raise SystemExit("--isolate-sessions requires --http PORT and --auto WORKDIR")
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
    if a.learning != "1.1" and not a.auto:
        raise SystemExit("--learning sets the rules of learning: it works in automatic mode only; add --auto WORKDIR")
    if a.protect_ids != "all" and not a.auto:
        raise SystemExit("--protect-ids sets what learning keeps: it works in automatic mode only; add --auto WORKDIR")
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
                compact=False if a.no_compact else None,
                keep_positions=False if a.no_keep_positions else None,
                breakdown=not a.no_breakdown, log_client=a.log_client, isolate_sessions=a.isolate_sessions,
                background_relearn=a.background_relearn, skill_version=a.skill_version,
                promote=a.promote, promote_runs=a.promote_runs, sample_rate=a.sample_rate,
                switch_off_runs=a.switch_off_runs, protect_ids=a.protect_ids, learning=a.learning,
                sample_max_share=a.sample_max_share, sample_seed=a.sample_seed,
                servers=load_servers(a.servers) if a.servers else {})
    if a.http:
        asyncio.run(serve_http(o, host=a.host, port=a.http, token=a.token, allowed_hosts=a.allowed_host))
    else:
        asyncio.run(serve(o))


def cmd_promotions(a):
    """List the fields promoted on evidence in a work directory, or take some back."""
    import os
    from contextlib import nullcontext
    from datetime import datetime, timezone

    from . import evidence, store
    from .auto import Learner, config_disagrees, held, pending_mark
    work = Path(a.workdir)
    path = work / "evidence.json"
    editing = a.revert is not None or bool(a.restore)
    if a.revert is not None and len(a.revert) > 2:
        raise SystemExit("--revert takes nothing (all), a TOOL, or a TOOL and a FIELD")
    changed, in_use, owed, pending, applied = [], False, False, False, None
    mark = work / store.CHANGED
    # Read, changed, written and applied under the lock that a running proxy also takes to write the evidence (1.4):
    # until 1.3 a relearning or a closing of the proxy could write its own copy over the change, and a second
    # `tracetrim promotions` now waits for this one to end, then reads its change (REVISIONE-14b-quater). A listing
    # reads the evidence and the mark under it too, where it can write the lock file.
    exists = store.present(work)              # an error other than a missing directory is raised (store.present)
    locked = exists and (editing or os.access(work, os.W_OK))
    with store.write_lock(work) if locked else nullcontext():
        if editing and exists:
            # A closing left in its journal by a stopped process goes first: replayed after this change, it would
            # write the evidence of before.
            store.recover_close(work)
        data = evidence.load(path)
        # A change saved and not applied yet: a command stopped after saving it (or one that left it to the proxy).
        # A mark that names no saved change was left by a command stopped before saving: it goes (`pending_mark`).
        # Or a config behind the evidence with no mark at all: a command of 1.3 stopped before it relearned
        # (REVISIONE-14b-octies: repeated, it found nothing to change).
        owed = editing and (pending_mark(work) or config_disagrees(work, data))
        noted_before = store.changes(work) if store.present(mark) else []
        if noted_before is None:
            # A mark with no note (an earlier build of 1.4): what it was about is in the evidence as it stands, so
            # every promotion is named as it is now; the obligation is kept (REVISIONE-14b-septies).
            earlier = [{"tool": p["tool"], "field": p["field"], "status": p["status"]} for p in data["promotions"]]
        else:
            earlier = held(data, noted_before)
        pending = bool(earlier) or config_disagrees(work, data)     # for the listing: saved, not applied yet
        if a.revert is not None:
            changed = evidence.revert(data, *a.revert)
        elif a.restore:
            changed = evidence.restore(data, *a.restore)
        if changed:
            # The mark is written before the evidence: a command stopped at any point after the change is saved
            # leaves it, and the change is applied by the next relearning, here when the command is repeated, or by
            # a proxy, or at a Learner's next start (REVISIONE-14b-quinquies: repeated, the command found nothing
            # to change and left the config as it was). It names the changes not applied yet, the earlier ones
            # still saved and this one: a relearning removes it only once it has read each of them (REVISIONE-14b-
            # sexies: one that read the evidence of before removed it). Written again after the evidence, so that
            # its stamp also changes.
            # By tool, field and status: until the evidence is replaced, the earlier status of a field this command
            # changes is still the saved one, and must stay named (REVISIONE-14b-septies: a restore stopped before
            # the evidence left a mark naming only its own status, which was dropped with the revert still pending).
            noted = {(c["tool"], c["field"], c["status"]): c for c in (earlier if owed else [])}
            noted.update({(p["tool"], p["field"], p["status"]): {"tool": p["tool"], "field": p["field"],
                                                                 "status": p["status"]} for p in changed})
            note = json.dumps({"format": "tracetrim/evidence-changed@1",
                               "at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                               "changed": list(noted.values())}) + "\n"
            store.write_atomic(mark, note)
            evidence.save(path, data)
            store.write_atomic(mark, note)
        if changed or owed:
            with store.alone(work) as alone:
                if not alone:
                    # A proxy (or another Learner process) owns the directory: relearning from here would write state
                    # and report over its own. The mark makes it, or its next start, relearn with the change.
                    in_use = True
                else:
                    # Rewrite config and report now; the relearning removes the mark.
                    # With the settings the work directory was learned with (kept in report.json), not the defaults:
                    # a relearning with other thresholds could switch a tool off that the proxy would keep.
                    from dataclasses import fields
                    from .evidence import Adaptive
                    # Read, not checked first: a file that cannot be read is an error, never the defaults (store.present).
                    config = json.loads(store.read_text(work / "report.config.json") or "{}")
                    report = json.loads(store.read_text(work / "report.json") or "{}")
                    saved = (report.get("adaptive") or {}).get("settings") or {}
                    known = {f.name for f in fields(Adaptive)}
                    adaptive = Adaptive(**{k: v for k, v in saved.items() if k in known})
                    Learner(work, config.get("auto", {}).get("min_runs", 5), adaptive=adaptive,
                            protect_ids=config.get("auto", {}).get("protect_ids", "all"),
                            learning=config.get("auto", {}).get("learning", "1.1"),
                            compact=config.get("compact", True) is not False,
                            keep_positions=config.get("keep_positions", True) is not False,
                            relearn_at_start=False).relearn()
                    # Applied only if the config now says what the evidence on disk says (after the relearning's own
                    # promotions) and no mark waits: with no readable run the relearning writes no config
                    # (REVISIONE-14b-decies: the command said it applied it; REVISIONE-14b-undecies: compared with
                    # the evidence of before the commit, a new promotion made it say the opposite).
                    applied = not store.present(mark) and not config_disagrees(work, evidence.load(path))
    for p in data["promotions"]:
        flag = " <- changed" if p in changed else ""
        print(f"{p['status']:<9} {p['tool']}  {p['field']}  ({p['reason']} in {len(p['runs'])} run"
              f"{'' if len(p['runs']) == 1 else 's'}: {', '.join(p['runs'])}){flag}")
    if not data["promotions"]:
        print("no promotions")
    elif (a.revert is not None or a.restore) and not changed:
        print("nothing changed: no promotion matches")
    if applied is False:
        print("the change is saved, but config and report do not show it yet (no readable run, or a file that could "
              "not be read): the next relearning applies it")
    elif owed and not changed and not in_use:
        print("a change saved earlier was not applied yet: config and report now apply it")
    if in_use:
        print("the work directory is in use (a proxy is running on it): the change is saved, and the proxy applies it "
              "at its next relearning")
    elif not editing and pending:
        print("a change is saved and not applied yet: the next relearning applies it (repeat the command, or start "
              "the proxy)")


def cmd_skill(a):
    """The skill as text: for the system prompt of an agent whose host does not
    load MCP skills, or (--write) as a SKILL.md folder for hosts that read
    skills from disk."""
    from . import skill
    kw = {"strategy": a.strategy, "done": not a.no_done, "version": a.skill_version}
    if a.write:
        path = Path(a.write) / skill.NAME / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(skill.skill_md(**kw), encoding="utf-8")
        print(path)
    else:
        sys.stdout.write(skill.text(**kw))


def _port(text: str) -> int:
    """A TCP port for `init --http`: 0 would be taken for "no port" and rewrite the host."""
    try:
        port = int(text)
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"not a port between 1 and 65535: {text!r}")
    return port


def cmd_init(a):
    """Put tracetrim in front of the servers of a host's MCP configuration (tracetrim.init)."""
    from . import init
    init.run(a)


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
    p.add_argument("--version", action="version", version=f"tracetrim {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("learn", help="learn a projection from traces")
    s.add_argument("traces", nargs="+")
    s.add_argument("-o", "--output", default="projection.json")
    s.add_argument("--mode", choices=MODES, default="prudent")
    s.add_argument("--source", choices=SOURCES, default="full")
    s.add_argument("--unseen", choices=UNSEEN, default="keep")
    s.add_argument("--v01", action="store_true",
                   help="tracetrim 0.1 behaviour: no identifier protection, no transformed values")
    s.add_argument("--protect-ids", choices=("all", "record"), default="all",
                   help="which identifying fields are kept whatever the usage: all (every field named like an id, the default) or record (ids and links of the record itself, names and titles at any depth; since 1.1)")
    s.add_argument("--learning", choices=("1.1", "1.0"), default="1.1",
                   help="the learning rules: 1.1 (the default) or 1.0, the rules of tracetrim 1.0 (negative numbers never found, a long text cancelled by one piece written before the call, the report measured without compact JSON)")
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
    s.add_argument("--protect-ids", choices=("all", "record"), default="all",
                   help="which identifying fields are kept whatever the usage: all (every field named like an id, the default) or record (ids and links of the record itself, names and titles at any depth; since 1.1)")
    s.add_argument("--learning", choices=("1.1", "1.0"), default="1.1",
                   help="the learning rules: 1.1 (the default) or 1.0, the rules of tracetrim 1.0 (negative numbers never found, a long text cancelled by one piece written before the call, the report measured without compact JSON)")
    s.set_defaults(func=cmd_measure)

    from . import compare
    s = sub.add_parser("compare", help="compare two recipes on held-out runs offline",
                       description="Compare two prudent recipes on original tool outputs (leave-one-out). "
                                   "Replay uses deterministic annotation IDs. Random IDs in production can "
                                   "change the token count by a few tokens per annotation.")
    s.add_argument("traces", nargs="+", help="distinct runs of the same task (.jsonl or .py)")
    for side in ("a", "b"):
        s.add_argument("--" + side, type=compare.recipe, required=True, metavar="KEY=VALUE,...",
                       help="protect_ids=all|record, learning=1.1|1.0, keep_positions=true|false, "
                            "unseen=keep|drop, compact=true|false (defaults listed first)")
    s.add_argument("--json", action="store_true", help="write tracetrim/compare@1 JSON to stdout")
    s.add_argument("--html", metavar="FILE",
                   help="also write the comparison as one HTML page, with nothing loaded from the network")
    s.set_defaults(func=compare.command)

    s = sub.add_parser("report", help="telemetry report in five languages (HTML + JSON)")
    s.add_argument("traces", nargs="+")
    s.add_argument("-o", "--output", default="report.html")
    s.add_argument("--tools", metavar="FILE",
                   help="the tool list the model was offered (a tools/list result, or a JSON list of tools): "
                        "adds the section on what the definitions weigh and which tools were never called")
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
    s.add_argument("--protect-ids", choices=("all", "record"), default="all",
                   help="which identifying fields are kept whatever the usage: all (every field named like an id, the default) or record (ids and links of the record itself, names and titles at any depth; since 1.1)")
    s.add_argument("--learning", choices=("1.1", "1.0"), default="1.1",
                   help="the learning rules: 1.1 (the default) or 1.0, the rules of tracetrim 1.0 (negative numbers never found, a long text cancelled by one piece written before the call, the report measured without compact JSON)")
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
    s.add_argument("--quiet", action="store_true", help="suppress per-call output on stderr; session warnings are still printed")
    s.add_argument("--isolate-sessions", action="store_true",
                   help="keep a separate run for each HTTP transport session (--http --auto only)")
    s.add_argument("--skill-version", choices=["1", "2"], default="2",
                   help="text of the skill and of expand: 2 (default since 1.4) adds the check before declaring "
                        "information absent; 1 restores the text of 1.3")
    s.add_argument("--background-relearn", action="store_true",
                   help="reply to tracetrim_done once the run is recorded and relearn in the background; calls "
                        "meanwhile use the policy in place (--auto only)")
    s.add_argument("--auto", metavar="WORKDIR",
                   help="automatic mode: record runs, learn, apply safe actions, keep the report (see tracetrim.auto)")
    s.add_argument("--min-runs", type=int, default=5, help="complete runs before trimming starts (--auto; at least 2)")
    s.add_argument("--structural", choices=["toon", "headroom"],
                   help="re-encode outputs after the projection (headroom: experimental, needs tracetrim[headroom])")
    s.add_argument("--skill-strategy", action="store_true",
                   help="add to the published skill the optional notes on organising tool calls (off by default)")
    s.add_argument("--no-compact", action="store_true",
                   help="keep the spaces after ',' and ':' when re-writing JSON (the behaviour up to 0.3.5); "
                        "by default --auto writes compact JSON from the first call, and --config as the config says")
    s.add_argument("--no-keep-positions", action="store_true",
                   help="drop a list element left with no field, instead of keeping an empty one in its place "
                        "(the behaviour up to 1.1.1, when a position after it could point expand at another element)")
    s.add_argument("--no-breakdown", action="store_true",
                   help="do not count, call by call, what re-serialising saves apart from what removing fields "
                        "saves (the log's 'format' and 'fields', the report's section on where the saving comes from)")
    s.add_argument("--log-client", metavar="FILE",
                   help="diagnostics, off by default: append to FILE one JSON line per request of the client "
                        "(time, an anonymous session id, the method; for initialize what the client declares, "
                        "for a resource or a skill its uri, for a tool call its name). Never arguments, results, "
                        "headers or tokens")
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
    s.add_argument("--protect-ids", choices=("all", "record"), default="all",
                   help="--auto: " + "which identifying fields are kept whatever the usage: all (every field named like an id, the default) or record (ids and links of the record itself, names and titles at any depth; since 1.1)")
    s.add_argument("--switch-off-runs", type=int, default=SWITCH_OFF_RUNS, metavar="N",
                   help="--auto: stop trimming a tool only when its results were expanded in at least N distinct "
                        f"runs (default {SWITCH_OFF_RUNS}; 1 restores the behaviour up to 0.3.5)")
    s.add_argument("--learning", choices=("1.1", "1.0"), default="1.1",
                   help="the learning rules: 1.1 (the default) or 1.0, the rules of tracetrim 1.0 (negative numbers never found, a long text cancelled by one piece written before the call, the report measured without compact JSON)")
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
    s.add_argument("--skill-version", choices=["1", "2"], default="2", help="2 (default since 1.4), or 1 for the text of 1.3")
    s.set_defaults(func=cmd_skill)

    from .init import HOSTS
    s = sub.add_parser("init", help="put tracetrim in front of the MCP servers a host already uses (dry run unless --write)",
                       description="Reads the host's MCP configuration and shows what would change: which servers go "
                                   "behind tracetrim, which stay and why, the host's new entry, the servers file. "
                                   "Nothing is written without --write. No value of the file is ever printed.")
    s.add_argument("--host", choices=list(HOSTS), help="the host whose configuration is read")
    s.add_argument("--file", metavar="PATH", help="the host's file, when it is not in the documented place")
    s.add_argument("--scope", choices=["user", "project"],
                   help="which of the host's files: the user's or the project's in the current directory "
                        "(default: user for Claude Desktop and Cursor, project for Claude Code and VS Code)")
    s.add_argument("--workdir", metavar="DIR",
                   help="where servers.json, the traces and the report go (default: ~/.tracetrim/HOST for the user's "
                        "file, ~/.tracetrim/HOST/PROJECT-HASH for a project's: tracetrim learns per directory)")
    s.add_argument("--name", default="tracetrim", help="the name of the entry in the host (default: tracetrim)")
    s.add_argument("--exclude", action="append", default=[], metavar="NAME",
                   help="leave this server in the host (repeatable)")
    s.add_argument("--http", type=_port, metavar="PORT",
                   help="for a host that reaches tracetrim over HTTP: the host's file is not rewritten, the steps are printed")
    s.add_argument("--write", action="store_true",
                   help="do it: backup of the host's file next to it, servers moved to WORKDIR/servers.json (mode 0600), "
                        "one entry in the host")
    s.add_argument("--force", action="store_true",
                   help="write servers.json even inside a git repository that does not ignore it; with --undo, "
                        "restore even if the host's file changed since")
    s.add_argument("--no-check", action="store_true",
                   help="after --write, do not start the new entry once to see that it answers (it calls no tool)")
    s.add_argument("--undo", metavar="BACKUP", help="restore the host's file from the backup --write made, byte for byte")
    s.set_defaults(func=cmd_init)

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
