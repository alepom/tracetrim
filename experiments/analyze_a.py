#!/usr/bin/env python3
"""Phase A analysis: RQ1, RQ2, RQ4, RQ5, plus gates and thermometers per run.

  .venv/bin/python -m experiments.analyze_a              # tables + results/phase_a.json

Everything here is recomputed from the traces in traces/<case>/A*.jsonl (or
dataset/traces/ from the Zenodo dataset; a case whose traces are not there
keeps its entry in the results file).
"""
from __future__ import annotations

import json
from pathlib import Path

from tracetrim.measure import leave_one_out, removes, tokenizer
from tracetrim.projection import learn
from tracetrim.traces import load
from tracetrim.usage import analyse

from .checks import check
from .quality import quality
from .run import ROOT, TRACES, keep_missing

CASES = ("C1", "C2", "C3", "C4", "C5")
REAL, SIMULATED = ("C1", "C2", "C3"), ("C4", "C5")
OUT = ROOT / "results" / "phase_a.json"


def traces_of(case):
    paths = sorted((TRACES / case).glob("A*.jsonl"))
    # Study 1 measured tracetrim 0.1: pinned, whatever the library defaults.
    return paths, [analyse(load(p), normalize=False, comma_grouping=False) for p in paths]


def loo_totals(names, usages, source, unseen):
    rows = leave_one_out(names, usages, source=source, unseen=unseen, protect_ids=False)
    t = {k: sum(getattr(r, k) for r in rows) for k in
         ("raw", "prudent", "aggressive", "trimmed_calls", "misses", "misses_text", "misses_prudent")}
    t["prudent_saving"] = 1 - t["prudent"] / t["raw"] if t["raw"] else 0
    t["aggressive_saving"] = 1 - t["aggressive"] / t["raw"] if t["raw"] else 0
    return t


def rq1_used_share(usages, count):
    """Share of tool-output tokens left by the in-sample aggressive projection:
    what the agent visibly used, plus record keys (a lower bound of use)."""
    p = learn(usages, "aggressive", protect_ids=False)
    raw = kept = 0
    for calls in usages:
        for c in calls:
            raw += count(c.output)
            kept += count(p.apply(c.tool, c.output))
    return {"raw": raw, "used_and_keys": kept, "share": kept / raw if raw else 0}


def rq5_transfer(count):
    """Learn on all C1 traces, apply to C2 traces, on the tools both use;
    compare with C2's own leave-one-out projection on the same tools."""
    _, u1 = traces_of("C1")
    _, u2 = traces_of("C2")
    shared = {c.tool for calls in u1 for c in calls} & {c.tool for calls in u2 for c in calls}
    cross = learn(u1, "aggressive", protect_ids=False)
    out = {"shared_tools": sorted(shared), "transfer": {"calls": 0, "misses": 0, "raw": 0, "kept": 0},
           "within": {"calls": 0, "misses": 0, "raw": 0, "kept": 0}}
    for i, calls in enumerate(u2):
        own = learn(u2[:i] + u2[i + 1:], "aggressive", protect_ids=False)
        for c in calls:
            if c.tool not in shared or not c.structured:
                continue
            for name, p in (("transfer", cross), ("within", own)):
                if c.tool not in p.keep:
                    continue
                o = out[name]
                o["calls"] += 1
                o["raw"] += count(c.output)
                o["kept"] += count(p.apply(c.tool, c.output))
                o["misses"] += any(removes(p, c.tool, f) for f in c.used)
    return out


def main():
    count = tokenizer()
    res = {"cases": {}}
    print(f"\n  PHASE A — leave-one-out, unseen fields kept (sensitivity: dropped)\n")
    hdr = f"  {'case':<5}{'runs':>5}{'calls':>7}{'raw tokens':>13}{'RQ1 used':>10}{'prudent':>9}{'aggr.':>8}{'misses':>9}{'calls-only misses':>19}{'strict':>8}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for case in CASES:
        paths, usages = traces_of(case)
        if len(paths) < 2:
            print(f"  {case:<5} not enough traces ({len(paths)})")
            continue
        names = [p.stem for p in paths]
        full = loo_totals(names, usages, "full", "keep")
        calls_only = loo_totals(names, usages, "calls", "keep")
        strict = loo_totals(names, usages, "full", "drop")
        rq1 = rq1_used_share(usages, count)
        # Counts only: the unverified items can be third-party SERP URLs,
        # which are never published in clear (protocol, Data and publication).
        gates = [{k: v for k, v in check(p).items() if k != "failed"} for p in paths]
        therm = [quality(p) for p in paths]
        res["cases"][case] = {"runs": names, "loo_full": full, "loo_calls": calls_only, "loo_strict": strict,
                              "rq1": rq1, "gates": gates, "thermometers": therm,
                              "calls": sum(len(u) for u in usages)}
        # Sensitivity (deviation 6): the same, without runs in which a tool call
        # failed after the retries.
        failed = [n for n, p in zip(names, paths)
                  if json.loads(p.with_suffix(".meta.json").read_text()).get("tool_failures")]
        if failed:
            keep = [i for i, n in enumerate(names) if n not in failed]
            res["cases"][case]["sensitivity_no_failures"] = {
                "excluded": failed,
                "loo_full": loo_totals([names[i] for i in keep], [usages[i] for i in keep], "full", "keep")}
        print(f"  {case:<5}{len(paths):>5}{res['cases'][case]['calls']:>7}{full['raw']:>13,}{rq1['share']:>10.0%}"
              f"{-full['prudent_saving']:>9.0%}{-full['aggressive_saving']:>8.0%}"
              f"{full['misses']:>5}/{full['trimmed_calls']:<3}{calls_only['misses']:>12}/{calls_only['trimmed_calls']:<6}"
              f"{strict['misses']:>4}/{strict['trimmed_calls']}")
    print("\n  RQ1 used = tokens left by the in-sample aggressive projection (used fields + record keys)")
    print("  misses   = held-out calls where the aggressive projection removes a field used later")

    print("\n  GATES (Phase A runs): json · structure · facts verified")
    for case, r in res["cases"].items():
        g = r["gates"]
        facts_ok = sum(x["facts_ok"] for x in g)
        facts = sum(x["facts"] for x in g)
        print(f"  {case:<5} json {sum(x['json'] for x in g)}/{len(g)} · structure {sum(x['structure'] for x in g)}/{len(g)}"
              f" · facts {facts_ok}/{facts}" + (f" ({facts_ok / facts:.0%})" if facts else ""))

    have_c1_c2 = "C1" in res["cases"] and "C2" in res["cases"]
    res = keep_missing(res, OUT, CASES)
    if have_c1_c2:
        t = rq5_transfer(count)
        res["rq5"] = t
        print(f"\n  RQ5 transfer C1 → C2 on shared tools {t['shared_tools']}")
        for k in ("within", "transfer"):
            o = t[k]
            print(f"    {k:<9} calls {o['calls']:>3} · misses {o['misses']:>3} · kept {o['kept'] / o['raw']:.0%} of {o['raw']:,} tokens"
                  if o["raw"] else f"    {k:<9} no calls")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(f"\n  written: {OUT.relative_to(ROOT)}\n")


if __name__ == "__main__":
    main()
