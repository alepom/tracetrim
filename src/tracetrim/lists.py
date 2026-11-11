"""How much of a list does the agent use? A measure, not a cut.

`usage.leaves` collapses list indices (`items[].title`): right for deciding
which FIELDS to keep, blind to how many ELEMENTS are used. A tool that returns
100 results of which the agent reuses the first 3 looks, field by field, fully
used. This module measures, for every outermost list of a tool output:

  received   how many elements the tool returned
  reused     which positions hold a value the agent wrote afterwards

and, from the call's arguments, the ones that fix the size of the result.
Nothing here changes what the model receives: rows are never cut. The numbers
are there to decide later whether cutting rows would be worth studying.

Element reuse is detected exactly as field reuse is (see `usage`): textual
reuse after the call, with the same blind spots.
"""
from __future__ import annotations

import json
import re
from statistics import median

from .traces import Trace
from .usage import first_written, keys_of, leaves, parse_output, transformed_keys

# Arguments that usually fix how many elements come back. A heuristic on the
# NAME, at any depth of the arguments: it knows nothing of what a given tool
# means by it (`depth` is a result count for one API and a crawl depth for another).
SIZE_ARGUMENT = re.compile(
    r"^(limit|per_page|page_size|pagesize|max_results|maxresults|num_results|top_k|topk|top|count|size|first|rows|"
    r"depth|n|k)$", re.I)


def size_arguments(arguments) -> dict[str, float]:
    """`{name: value}` for the numeric arguments whose name says "how many"."""
    found: dict[str, float] = {}

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                p = f"{path}.{k}" if path else str(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and SIZE_ARGUMENT.match(str(k)):
                    found[p] = v
                else:
                    walk(v, p)
        elif isinstance(node, list):
            for v in node:
                walk(v, path + "[]")
    walk(arguments)
    return found


def outer_lists(payload) -> dict[str, list]:
    """The outermost list on every branch: `items[]`, `tasks[]`; not the lists inside their elements."""
    found: dict[str, list] = {}

    def walk(node, path=""):
        if isinstance(node, list):
            found[path + "[]"] = node
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
    walk(payload)
    return found


def element_usage(trace: Trace, normalize: bool = True, comma_grouping: bool = True) -> list[dict]:
    """One record per call with a structured output:
    `{"tool", "size_arguments", "lists": {path: {"received": n, "reused": [positions]}}}`."""
    pos, first, _ = first_written(trace)
    result = []
    for i, e in enumerate(trace.events):
        if e.kind != "call" or e.output is None:
            continue
        payload = parse_output(e.output)
        if not isinstance(payload, (dict, list)):
            continue
        try:
            arguments = json.loads(e.text) if e.text else {}
        except json.JSONDecodeError:
            arguments = {}
        lists = {}
        for path, items in outer_lists(payload).items():
            reused = []
            for n, item in enumerate(items):
                for leaf, value in leaves(item, path):
                    k = keys_of(value, comma_grouping) + (transformed_keys(value, leaf) if normalize else [])
                    seen = [first[x] for x in k if x in first]
                    if seen and min(seen) > pos[i]:
                        reused.append(n)
                        break
            lists[path] = {"received": len(items), "reused": reused}
        result.append({"tool": e.tool, "size_arguments": size_arguments(arguments), "lists": lists})
    return result


def _spread(values) -> dict:
    return {"median": median(values), "max": max(values)} if values else {"median": None, "max": None}


def summarise(per_trace: list[list[dict]]) -> dict:
    """Per tool and list: size asked for, elements received, elements reused,
    and whether the reused ones are at the top of the list."""
    tools: dict[str, dict] = {}
    for calls in per_trace:
        for c in calls:
            t = tools.setdefault(c["tool"], {"calls": 0, "size_arguments": {}, "lists": {}})
            t["calls"] += 1
            for name, value in c["size_arguments"].items():
                t["size_arguments"].setdefault(name, []).append(value)
            for path, m in c["lists"].items():
                t["lists"].setdefault(path, []).append(m)
    out = {}
    for tool, t in sorted(tools.items()):
        lists = {}
        for path, ms in sorted(t["lists"].items()):
            with_reuse = [m for m in ms if m["reused"]]
            lists[path] = {
                "calls": len(ms),
                "received": _spread([m["received"] for m in ms]),
                "reused": _spread([len(m["reused"]) for m in ms]),
                "calls_with_reuse": len(with_reuse),
                # The reused elements are exactly the first ones: positions 0..k-1.
                "calls_reused_from_top": sum(m["reused"] == list(range(len(m["reused"]))) for m in with_reuse),
                # How far down the list one must go to cover every reused element (1 = the first).
                "deepest_reused": _spread([m["reused"][-1] + 1 for m in with_reuse]),
            }
        out[tool] = {"calls": t["calls"],
                     "size_arguments": {n: {"calls": len(v), **_spread(v)} for n, v in sorted(t["size_arguments"].items())},
                     "lists": lists}
    return out
