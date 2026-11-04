#!/usr/bin/env python3
"""Phase C analysis: RQ6. Live runs with the projection and `expand`.

  .venv/bin/python -m experiments.analyze_c          # tables + results/phase_c.json

What a deployment would see end to end: billed input tokens (cache
included, from the API usage), turns, how often the agent asked for hidden
fields, and whether the answers still pass the gates and thermometers.
"""
from __future__ import annotations

import json
import statistics

from .analyze_b import gate_pass
from .checks import check
from .quality import quality
from .run import ROOT, TRACES, cost

OUT = ROOT / "results" / "phase_c.json"


def main():
    res = {"cases": {}}
    print("\n  PHASE C — live runs: raw tools vs prudent projection + expand (median [min-max])\n")
    for case in ("C1", "C4"):
        arms = {}
        for arm in ("raw", "prudent"):
            paths = sorted((TRACES / case).glob(f"C-{arm}-*.jsonl"))
            if not paths:
                continue
            metas = [json.loads(p.with_suffix(".meta.json").read_text()) for p in paths]
            gates = [{k: v for k, v in check(p).items() if k != "failed"} for p in paths]
            arms[arm] = {
                "runs": [p.stem for p in paths],
                "billed_input": [m["usage"]["input"] for m in metas],
                "uncached_input": [m["usage"]["input"] - m["usage"]["cached"] for m in metas],
                "cost_usd": [m["cost_usd"] for m in metas],
                "turns": [m["turns"] for m in metas], "calls": [m["calls"] for m in metas],
                "expand_calls": [m.get("expand_calls") or 0 for m in metas],
                "expanded_fields": [m.get("expanded_fields") or 0 for m in metas],
                "flags": [m["flags"] for m in metas],
                "gates_passed": sum(gate_pass(g) for g in gates), "gates": gates,
                "thermometers": [quality(p) for p in paths],
            }
        res["cases"][case] = arms

        def mm(v):
            return f"{statistics.median(v):,.0f} [{min(v):,.0f}-{max(v):,.0f}]" if v else "—"
        print(f"  {case}")
        print(f"    {'':<14}" + "".join(f"{a:>30}" for a in arms))
        for key, label in (("billed_input", "input tokens"), ("uncached_input", "uncached input"),
                           ("turns", "turns"), ("calls", "tool calls"), ("expand_calls", "expand calls")):
            print(f"    {label:<14}" + "".join(f"{mm(a[key]):>30}" for a in arms.values()))
        print(f"    {'cost (USD)':<14}" + "".join(
            f"{statistics.median(a['cost_usd']):>30.3f}" for a in arms.values()))
        print(f"    {'gates passed':<14}" + "".join(f"{str(a['gates_passed']) + '/' + str(len(a['runs'])):>30}" for a in arms.values()))
        for a_name, a in arms.items():
            for p, t in zip(a["runs"], a["thermometers"]):
                print(f"      {a_name:<8}{p:<14}{json.dumps(t, ensure_ascii=False)}")
        print()
    OUT.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  written: {OUT.relative_to(ROOT)}  (prices assumed: experiments/prices.json)\n")


if __name__ == "__main__":
    main()
