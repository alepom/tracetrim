"""How many different values does each argument of a tool take?

Recorded so that one can later ask whether a tool is used in a few stable
modes (an argument with two or three values, such as a result type) or with
arguments that are different every time (an invoice id). Only counts are kept:
the values themselves stay in the traces.
"""
from __future__ import annotations

import json

from .traces import Trace
from .usage import leaves


def cardinality(traces: list[Trace]) -> dict:
    """`{tool: {"calls": n, "arguments": {path: {"calls": present_in, "distinct": values}}}}`.

    A path is a top-level argument (`keywords`, compared as a whole) or one of
    its leaves when it is nested (`keywords[]`, `filters[].field`)."""
    tools: dict[str, dict] = {}
    for trace in traces:
        for e in trace.events:
            if e.kind != "call":
                continue
            try:
                args = json.loads(e.text) if e.text else {}
            except json.JSONDecodeError:
                continue
            if not isinstance(args, dict):
                continue
            t = tools.setdefault(e.tool, {"calls": 0, "arguments": {}})
            t["calls"] += 1
            present: dict[str, set] = {}
            for name, value in args.items():
                present.setdefault(str(name), set()).add(json.dumps(value, sort_keys=True, ensure_ascii=False))
                if isinstance(value, (dict, list)):
                    for path, leaf in leaves(value, str(name)):
                        present.setdefault(path, set()).add(json.dumps(leaf, ensure_ascii=False))
            for path, values in present.items():
                a = t["arguments"].setdefault(path, {"calls": 0, "values": set()})
                a["calls"] += 1
                a["values"] |= values
    return {tool: {"calls": t["calls"],
                   "arguments": {p: {"calls": a["calls"], "distinct": len(a["values"])}
                                 for p, a in sorted(t["arguments"].items())}}
            for tool, t in sorted(tools.items())}
