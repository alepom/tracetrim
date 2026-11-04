#!/usr/bin/env python3
"""Study 1 recomputed with usage compared by model turn (deviation 14).

  PYTHONPATH=src .venv/bin/python -m experiments.turns_sensitivity

The published analysis compares positions in the trace: when the model emits
several calls in one turn, the arguments of a later call count as written
"after" the outputs of the earlier ones, although they were generated before.
This recomputes the Phase A figures with turns (experiments/turns.py) and
writes them beside the published ones, case by case.
"""
import json

from tracetrim.traces import load
from tracetrim.usage import analyse

from .analyze_a import loo_totals, rq1_used_share
from .paths import ROOT, TRACES
from tracetrim.measure import tokenizer
from .turns import with_turns


def main():
    a = json.loads((ROOT / "results" / "phase_a.json").read_text())
    count = tokenizer()
    out = {}
    print(f"\n  {'case':<5}{'':>4}{'used':>7}{'prudent':>9}{'misses':>10}{'calls-only':>12}{'parallel turns':>16}")
    for case in ("C1", "C2", "C3", "C4", "C5"):
        paths = sorted((TRACES / case).glob("A*.jsonl"))
        if not paths:
            print(f"  {case:<5}traces not available")
            continue
        names = [p.stem for p in paths]
        traces = [with_turns(load(p), p.with_suffix(".meta.json")) for p in paths]
        multi = sum(sum(1 for t in json.loads(p.with_suffix(".meta.json").read_text())["usage_per_turn"] if t["calls"] > 1)
                    for p in paths)
        usages = [analyse(t, normalize=False, comma_grouping=False) for t in traces]
        by_position = [analyse(load(p), normalize=False, comma_grouping=False) for p in paths]
        # Calls whose detected usage changes when compared by turn.
        changed = sum(x.used != y.used or x.used_by_calls != y.used_by_calls
                      for a_, b_ in zip(by_position, usages) for x, y in zip(a_, b_))
        n_calls = sum(len(u) for u in usages)
        full, calls = loo_totals(names, usages, "full", "keep"), loo_totals(names, usages, "calls", "keep")
        rq1 = rq1_used_share(usages, count)
        ref = a["cases"][case]
        out[case] = {"turn_aware": {"used_share": rq1["share"], "prudent_saving": full["prudent_saving"],
                                    "misses": [full["misses"], full["trimmed_calls"]],
                                    "misses_calls": [calls["misses"], calls["trimmed_calls"]]},
                     "published": {"used_share": ref["rq1"]["share"], "prudent_saving": ref["loo_full"]["prudent_saving"],
                                   "misses": [ref["loo_full"]["misses"], ref["loo_full"]["trimmed_calls"]],
                                   "misses_calls": [ref["loo_calls"]["misses"], ref["loo_calls"]["trimmed_calls"]]},
                     "turns_with_several_calls": multi, "calls": n_calls, "calls_changed": changed}
        for label, d in (("pub", out[case]["published"]), ("turn", out[case]["turn_aware"])):
            print(f"  {case if label == 'pub' else '':<5}{label:>4}{d['used_share']:>7.1%}{d['prudent_saving']:>9.1%}"
                  f"{'/'.join(map(str, d['misses'])):>10}{'/'.join(map(str, d['misses_calls'])):>12}"
                  + (f"{multi:>16}" if label == "pub" else ""))
    (ROOT / "results" / "turns_sensitivity.json").write_text(json.dumps(out, indent=2) + "\n")
    print("\n  written: results/turns_sensitivity.json\n")


if __name__ == "__main__":
    main()
