"""tracetrim — learn from agent traces which tool-output fields are used.

  tracetrim learn   traces/*.jsonl -o projection.json [--mode prudent|aggressive] [--source full|calls]
  tracetrim apply   projection.json TOOL < output.json
  tracetrim measure traces/*.jsonl [--source full|calls] [--per-tool]

`measure` always starts by showing what it read: a parser that fails
silently produces plausible, wrong numbers.
"""
from __future__ import annotations

import argparse
import sys

from . import measure as m
from .projection import MODES, SOURCES, Projection, learn
from .traces import load
from .usage import analyse, leaves


def fmt(n) -> str:
    return f"{int(n):,}"


def pct(a, b) -> str:
    return f"{(a / b - 1) * 100:+.0f}%" if b else "—"


def row(cols, widths) -> str:
    return "  " + "".join(f"{c:<{w}}" if i == 0 else f"{c:>{w}}"
                          for i, (c, w) in enumerate(zip(cols, widths)))


def _read(paths):
    traces = [load(p) for p in paths]
    return traces, [analyse(t) for t in traces]


def cmd_learn(a):
    _, usages = _read(a.traces)
    p = learn(usages, a.mode, a.source)
    p.save(a.output)
    fields = sum(len(v) for v in p.keep.values())
    print(f"{a.output}: {len(p.keep)} tools, {fields} fields kept ({a.mode}, source={a.source})")


def cmd_apply(a):
    sys.stdout.write(Projection.load(a.projection).apply(a.tool, sys.stdin.read()))


def cmd_measure(a):
    traces, usages = _read(a.traces)

    print("\n  TRACES READ\n")
    w = [28, 8, 8, 8, 10, 8]
    print(row(["trace", "calls", "json", "other", "leaves", "used"], w))
    print("  " + "-" * sum(w))
    for t, us in zip(traces, usages):
        js = sum(u.structured for u in us)
        lv = sum(sum(1 for _ in leaves(u.payload)) for u in us if u.structured)
        print(row([t.name[:27], len(us), js, len(us) - js, fmt(lv), sum(len(u.used) for u in us)], w))
    print("  json = structured output · other = prose, errors, empty")

    rows = m.leave_one_out([t.name for t in traces], usages, a.source)
    seen = "everything the agent wrote" if a.source == "full" else "tool calls only (MCP proxy)"
    print(f"\n\n  LEAVE-ONE-OUT PROJECTION   learned from: {seen}\n")
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


def main(argv=None):
    p = argparse.ArgumentParser(prog="tracetrim", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("learn", help="learn a projection from traces")
    s.add_argument("traces", nargs="+")
    s.add_argument("-o", "--output", default="projection.json")
    s.add_argument("--mode", choices=MODES, default="prudent")
    s.add_argument("--source", choices=SOURCES, default="full")
    s.set_defaults(func=cmd_learn)

    s = sub.add_parser("apply", help="trim one tool output read from stdin")
    s.add_argument("projection")
    s.add_argument("tool")
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("measure", help="leave-one-out evaluation on traces")
    s.add_argument("traces", nargs="+")
    s.add_argument("--source", choices=SOURCES, default="full")
    s.add_argument("--per-tool", action="store_true")
    s.set_defaults(func=cmd_measure)

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
