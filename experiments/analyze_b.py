#!/usr/bin/env python3
"""Phase B analysis: RQ3 / H2. Does the answer hold when tool outputs are trimmed?

  .venv/bin/python -m experiments.analyze_b          # tables + results/phase_b.json

H2, as frozen: an arm holds when it passes every gate in at least as many
runs as the raw arm, and every thermometer median lies inside the raw arm's
[min, max]. With 2 runs per arm only large losses are detectable: a "holds"
here means "no large loss seen", not "no loss".
"""
from __future__ import annotations

import json
import statistics

from .checks import check
from .quality import quality
from .replay import ARMS
from .run import ROOT, TRACES

CASES = ("C1", "C2", "C3", "C4", "C5")
OUT = ROOT / "results" / "phase_b.json"


def gate_pass(r) -> bool:
    return r["json"] and r["structure"] and r["facts"] > 0 and r["facts_ok"] == r["facts"]


def main():
    res = {"rule": "H2 band rule; 2 runs per arm", "cases": {}}
    headroom = {}
    hp = ROOT / "results" / "headroom_baseline.json"
    if hp.exists():
        headroom = json.loads(hp.read_text())["cases"]
    for case in CASES:
        arms = {}
        for arm in ARMS:
            paths = sorted((TRACES / case).glob(f"B-{arm}-*.jsonl"))
            if not paths:
                continue
            metas = [json.loads(p.with_suffix(".meta.json").read_text()) for p in paths]
            gates = [{k: v for k, v in check(p).items() if k != "failed"} for p in paths]
            therm = [quality(p) for p in paths]
            arms[arm] = {"runs": [p.stem for p in paths], "tool_tokens": metas[0]["tool_message_tokens"],
                         "billed_input": [m["usage"]["input"] for m in metas], "gates": gates, "thermometers": therm,
                         "gates_passed": sum(gate_pass(g) for g in gates)}
        if "raw" not in arms:
            continue
        raw = arms["raw"]
        nums = [k for k, v in raw["thermometers"][0].items() if isinstance(v, (int, float))] if raw["thermometers"] and raw["thermometers"][0] else []
        band = {k: (min(t[k] for t in raw["thermometers"]), max(t[k] for t in raw["thermometers"])) for k in nums}
        for arm, a in arms.items():
            med = {k: statistics.median(t[k] for t in a["thermometers"] if k in t) for k in nums}
            outside = [k for k in nums if not band[k][0] <= med[k] <= band[k][1]]
            a["medians"], a["outside_band"] = med, outside
            a["holds"] = a["gates_passed"] >= raw["gates_passed"] and not outside
        res["cases"][case] = {"arms": arms, "band": band, "headroom": headroom.get(case)}

    print("\n  PHASE B — replay of A3, only tool messages changed (2 runs per arm)\n")
    for case, r in res["cases"].items():
        arms = r["arms"]
        raw_tok = arms["raw"]["tool_tokens"]
        print(f"  {case}   thermometer band of the raw arm: " +
              ", ".join(f"{k} [{lo}-{hi}]" for k, (lo, hi) in r["band"].items()))
        print(f"    {'arm':<11}{'tool tokens':>12}{'vs raw':>8}{'gates':>7}   medians")
        for arm, a in arms.items():
            rel = a["tool_tokens"] / raw_tok - 1
            meds = ", ".join(f"{k} {v:g}" + ("*" if k in a["outside_band"] else "") for k, v in a["medians"].items())
            mark = "" if arm == "raw" else ("  holds" if a["holds"] else "  DOES NOT HOLD")
            print(f"    {arm:<11}{a['tool_tokens']:>12,}{rel:>+8.0%}{a['gates_passed']:>5}/{len(a['runs'])}   {meds}{mark}")
        if r["headroom"]:
            h = r["headroom"]
            print(f"    {'headroom':<11}{h['headroom']:>12,}{h['headroom'] / h['tool_tokens'] - 1:>+8.0%}   (tokens only, not replayed)")
        print()
    print("  * = median outside the raw arm's [min, max]\n")
    OUT.write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(f"  written: {OUT.relative_to(ROOT)}\n")


if __name__ == "__main__":
    main()
