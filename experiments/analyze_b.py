#!/usr/bin/env python3
"""Phase B analysis: RQ3 / H2. Does the answer hold when tool outputs are trimmed?

  .venv/bin/python -m experiments.analyze_b          # tables + results/phase_b.json
  .venv/bin/python -m experiments.analyze_b --study D   # Study 2: H5-H8 + results/study2.json

H2, as frozen: an arm holds when it passes every gate in at least as many
runs as the raw arm, and every thermometer median lies inside the raw arm's
[min, max]. With 2 runs per arm only large losses are detectable: a "holds"
here means "no large loss seen", not "no loss".
"""
from __future__ import annotations

import argparse
import json
import statistics

from .checks import check
from .quality import quality
from .replay import PREFIX
from .run import ROOT, TRACES

CASES = ("C1", "C2", "C3", "C4", "C5")
# Thermometers added after the freeze (deviation 11). They are reported, but
# never enter the band of H2, which is defined on the frozen ones only.
POSTHOC = {"correct_diagnoses_strict"}
OUT = {"B": ROOT / "results" / "phase_b.json", "D": ROOT / "results" / "study2.json"}


def study2_hypotheses(res) -> dict:
    """H5-H8 exactly as frozen in protocol-v3, on the v02 arm."""
    c = res["cases"]
    v = lambda case: c[case]["arms"]["v02"]
    raw_tok = lambda case: c[case]["arms"]["raw"]["tool_tokens"]
    h = {
        "H5_C4_all_tickets_right": all(t.get("correct_diagnoses") == 5 for t in v("C4")["thermometers"]),
        # Post hoc (deviation 11), reported beside the frozen test, never instead of it.
        "H5_strict_posthoc": all(t.get("correct_diagnoses_strict") == 5 for t in v("C4")["thermometers"]),
        "H6_C3_gates_pass_every_run": v("C3")["gates_passed"] == len(v("C3")["runs"]),
        "H7_C5_all_links_found": all(t.get("links_found") == 4 for t in v("C5")["thermometers"]),
        "H8_saving_at_least_30pct": {k: v(k)["tool_tokens"] / raw_tok(k) <= 0.70 for k in ("C1", "C3", "C4", "C5")},
    }
    h["H8_saving_at_least_30pct_all"] = all(h["H8_saving_at_least_30pct"].values())
    return h


def gate_pass(r) -> bool:
    return r["json"] and r["structure"] and r["facts"] > 0 and r["facts_ok"] == r["facts"]


def main():
    a_ = argparse.ArgumentParser()
    a_.add_argument("--study", choices=list(PREFIX), default="B")
    study = a_.parse_args().study
    ARMS = PREFIX[study]
    res = {"study": study, "rule": "band rule against the raw arm", "cases": {}}
    headroom = {}
    hp = ROOT / "results" / "headroom_baseline.json"
    if hp.exists():
        headroom = json.loads(hp.read_text())["cases"]
    for case in CASES:
        arms = {}
        for arm in ARMS:
            # "-[0-9]*": "D-v02-*" would also match "D-v02-structural-*".
            paths = sorted((TRACES / case).glob(f"{study}-{arm}-[0-9]*.jsonl"))
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
            outside = [k for k in nums if k not in POSTHOC and not band[k][0] <= med[k] <= band[k][1]]
            a["medians"], a["outside_band"] = med, outside
            a["holds"] = a["gates_passed"] >= raw["gates_passed"] and not outside
            # The post-hoc thermometers, judged by the same rule, kept apart.
            a["outside_band_posthoc"] = [k for k in nums if k in POSTHOC and not band[k][0] <= med[k] <= band[k][1]]
        res["cases"][case] = {"arms": arms, "band": band, "headroom": headroom.get(case)}

    print(f"\n  {'PHASE B' if study == 'B' else 'STUDY 2'} — replay of A3, only tool messages changed\n")
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
    if study == "D" and all(k in res["cases"] for k in ("C1", "C3", "C4", "C5")):
        res["hypotheses"] = study2_hypotheses(res)
        print("  HYPOTHESES (protocol-v3, v02 arm)")
        for k, v in res["hypotheses"].items():
            print(f"    {k:<34} {v}")
        print()
    OUT[study].write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(f"  written: {OUT[study].relative_to(ROOT)}\n")


if __name__ == "__main__":
    main()
