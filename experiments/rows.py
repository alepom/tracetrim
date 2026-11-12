#!/usr/bin/env python3
"""What agents asked of rows, and how many rows they reuse. The numbers of docs/rows.md.

  python -m experiments.rows            # results/v04/rows.json

Offline, from the runs already recorded. Two things:

  requests   the `expand` requests of E1 and E2 that gave nothing back, by what they asked for
  elements   for every list a tool returns: elements received, elements the run reused (a value
             written afterwards, as `tracetrim.lists` detects it), and what a cut by position
             would have removed: the calls in which a reused element sits below the first K

Elements are measured on the runs in which the model saw whole results (Phase A, E1 arms R and S,
E3): where results were trimmed, an element can be reused through a field the model did not see.
On C1 and C2 the outputs of E3 are recorded ones; they are what the model read, so they count.
Nothing here cuts a row: tracetrim never does.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from statistics import median

from tracetrim import lists
from tracetrim.engine import Trimmer
from tracetrim.traces import load
from tracetrim.usage import parse_output

from .modes import WHOLE, study_traces
from .paths import ROOT
from .v04 import calls as read_calls

OUT = ROOT / "results" / "v04" / "rows.json"
CUTS = (3, 5)
ROWS = re.compile(r"^[\w.\[\]]*\[\d*:\d*\]|^(items|data|results|rows)$")       # a slice, or a whole list by its name
INDEX = re.compile(r"\[\d+\]")


def requests() -> dict:
    """The `expand` requests of E1 and E2 that returned an error."""
    kinds, total, per_request = Counter(), 0, []
    for trace in sorted((ROOT / "traces").glob("*/E[12]-*.jsonl")):
        known = {}                                       # call_id -> the original result
        for c in read_calls(trace):
            if c["tool"] != "expand":
                if "[trimmed] call_id=" in c["sent"]:
                    known[c["sent"].rsplit("call_id=", 1)[1].split()[0]] = parse_output(c["original"])
                continue
            total += 1
            if c["outcome"] == "ok":
                continue
            fields = [str(f) for f in c["arguments"].get("fields") or []]
            row = {"run": f"{trace.parent.name}/{trace.stem}", "call_id_was_valid": c["arguments"].get("call_id") in known,
                   "fields": fields, "asks_for_rows": any(ROWS.search(f) for f in fields),
                   "uses_an_index": any(INDEX.search(f) for f in fields)}
            if row["call_id_was_valid"]:                 # the same request, with `expand` as it is now
                t = Trimmer(None)
                t.originals["x"] = known[c["arguments"]["call_id"]]
                row["answered_now"] = bool(t.recover("x", fields)[1])
            per_request.append(row)
            kinds["unknown call_id" if not row["call_id_was_valid"] else "no field matches"] += 1
    return {"expand_requests": total, "gave_nothing_back": len(per_request), "by_reply": dict(kinds),
            "unknown_call_id_asking_for_rows": sum(not r["call_id_was_valid"] and r["asks_for_rows"] for r in per_request),
            "with_an_index_on_a_valid_call_id": sum(r["call_id_was_valid"] and r["uses_an_index"] for r in per_request),
            "of_which_answered_now": sum(bool(r.get("answered_now")) for r in per_request),
            "per_request": per_request}


def elements() -> dict:
    out = {}
    for case in sorted(p.name for p in study_traces().iterdir() if p.is_dir()):
        files = sorted(study_traces().joinpath(case).glob("A*.jsonl")) + sorted((ROOT / "traces" / case).glob("E*.jsonl"))
        files = [p for p in files if WHOLE.match(p.stem)]
        tools: dict[str, dict] = {}
        for p in files:
            for c in lists.element_usage(load(p)):
                for path, m in c["lists"].items():
                    if m["received"]:
                        tools.setdefault(c["tool"], {}).setdefault(path, []).append(m)
        table = {}
        for tool, by_list in sorted(tools.items()):
            for path, ms in sorted(by_list.items()):
                used = [m for m in ms if m["reused"]]
                if not used:
                    continue
                table[f"{tool} {path}"] = {
                    "calls": len(ms), "received_median": median(m["received"] for m in ms),
                    "received_max": max(m["received"] for m in ms),
                    "calls_with_reuse": len(used), "reused_median": median(len(m["reused"]) for m in used),
                    "share_of_elements_reused": round(sum(len(m["reused"]) for m in used) / sum(m["received"] for m in used), 4),
                    "calls_reused_from_the_top": sum(m["reused"] == list(range(len(m["reused"]))) for m in used),
                    "deepest_reused_median": median(m["reused"][-1] + 1 for m in used),
                    "calls_reusing_the_last_element": sum(m["reused"][-1] + 1 == m["received"] for m in used),
                    **{f"calls_losing_a_reused_element_if_cut_to_{k}": sum(m["reused"][-1] + 1 > k for m in used) for k in CUTS}}
        out[case] = {"runs": len(files), "lists": table}
    return out


def analyse() -> dict:
    el = elements()
    rows = [m for c in el.values() for m in c["lists"].values()]
    total = sum(m["calls_with_reuse"] for m in rows)
    def together(cases):
        ms = [m for c in cases for m in el[c]["lists"].values()]
        return {"cases": list(cases), "lists": len(ms),
                **{k: sum(m[k] for m in ms) for k in ("calls_with_reuse", "calls_reused_from_the_top", "calls_reusing_the_last_element",
                                                      *(f"calls_losing_a_reused_element_if_cut_to_{k}" for k in CUTS))}}
    return {"what": "what agents asked of rows with expand, and the list elements they reuse; nothing is cut",
            "requests": requests(),
            "search_data_api": together(("C1", "C2")), "other_apis": together(tuple(c for c in el if c not in ("C1", "C2"))),
            "elements_summary": {"lists_with_reuse": len(rows), "calls_with_reuse": total,
                                 "calls_reused_from_the_top": sum(m["calls_reused_from_the_top"] for m in rows),
                                 **{f"calls_losing_a_reused_element_if_cut_to_{k}":
                                    sum(m[f"calls_losing_a_reused_element_if_cut_to_{k}"] for m in rows) for k in CUTS}},
            "elements": el}


if __name__ == "__main__":
    res = analyse()
    OUT.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    r = res["requests"]
    print(f"expand requests in E1 and E2: {r['expand_requests']}, gave nothing back: {r['gave_nothing_back']} {r['by_reply']}; "
          f"unknown id asking for rows: {r['unknown_call_id_asking_for_rows']}; index on a valid id: {r['with_an_index_on_a_valid_call_id']}")
    print(res["elements_summary"])
    print(f"{'case':<4} {'tool and list':<66}{'calls':>6}{'recv':>6}{'reuse':>6}{'used':>6}{'share':>7}{'top':>5}{'deep':>6}{'last':>5}{'cut3':>5}{'cut5':>5}")
    for case, c in res["elements"].items():
        for name, m in c["lists"].items():
            print(f"{case:<4} {name[:65]:<66}{m['calls']:>6}{m['received_median']:>6g}{m['calls_with_reuse']:>6}{m['reused_median']:>6g}"
                  f"{m['share_of_elements_reused']:>7.0%}{m['calls_reused_from_the_top']:>5}{m['deepest_reused_median']:>6g}"
                  f"{m['calls_reusing_the_last_element']:>5}{m['calls_losing_a_reused_element_if_cut_to_3']:>5}{m['calls_losing_a_reused_element_if_cut_to_5']:>5}")
    print(OUT)
