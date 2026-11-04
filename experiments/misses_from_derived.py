#!/usr/bin/env python3
"""Misses of Study 1 recomputed from per-call records only (no payloads).

  PYTHONPATH=src .venv/bin/python -m experiments.misses_from_derived

For the two cases built on the search-data API only derived records are
released (dataset/derived/): for each call, the field paths it carries and
which of them the agent used, echoed or could have been judged by. That is
enough to redo the leave-one-out of Table 1 for misses (not for token savings,
which need the payloads). The same computation is run on the cases released in
full, from their traces, and checked against results/phase_a.json, so that this
re-implementation is tested on data anyone can inspect.
"""
import json
from collections import defaultdict

from tracetrim.traces import load
from tracetrim.usage import analyse, leaves

from .run import ROOT, TRACES

DERIVED = ROOT / "dataset" / "derived"


def records_from_derived(case):
    runs = []
    for p in sorted((DERIVED / case).glob("A*.jsonl")):
        runs.append([json.loads(line) for line in p.open(encoding="utf-8")])
    return runs


def records_from_traces(case):
    runs = []
    for p in sorted((TRACES / case).glob("A*.jsonl")):
        recs = []
        for u in analyse(load(p), normalize=False, comma_grouping=False):
            recs.append({"tool": u.tool, "structured": u.structured, "used": sorted(u.used),
                         "used_by_calls": sorted(u.used_by_calls), "echo": sorted(u.echo),
                         "judgeable": sorted(u.judgeable),
                         "seen": sorted({q for q, _ in leaves(u.payload)}) if u.structured else []})
        runs.append(recs)
    return runs


def misses(runs, source):
    """Held-out calls in which the aggressive projection (Study 1: no identifier
    protection, unseen fields kept) removes a field the run used later."""
    total = trimmed = 0
    for i, held in enumerate(runs):
        keep, seen = defaultdict(set), defaultdict(set)
        for j, recs in enumerate(runs):
            if j == i:
                continue
            for r in recs:
                if r["structured"]:
                    keep[r["tool"]] |= set(r["used"] if source == "full" else r["used_by_calls"]) | set(r["echo"])
                    seen[r["tool"]] |= set(r["seen"])
        for r in held:
            if not (r["structured"] and r["tool"] in seen):
                continue
            trimmed += 1
            total += any(f not in keep[r["tool"]] and f in seen[r["tool"]] for f in r["used"])
    return total, trimmed


def main():
    a = json.loads((ROOT / "results" / "phase_a.json").read_text())
    print(f"\n  {'case':<6}{'source':<10}{'misses (full)':>15}{'misses (calls)':>16}   vs results/phase_a.json")
    for case in ("C1", "C2", "C3", "C4", "C5"):
        if (DERIVED / case).exists():
            runs, src = records_from_derived(case), "derived"
        elif (TRACES / case).exists():
            runs, src = records_from_traces(case), "traces"
        else:
            print(f"  {case:<6}not available")
            continue
        full, calls = misses(runs, "full"), misses(runs, "calls")
        ref = a["cases"][case]
        ok = (full == (ref["loo_full"]["misses"], ref["loo_full"]["trimmed_calls"])
              and calls == (ref["loo_calls"]["misses"], ref["loo_calls"]["trimmed_calls"]))
        print(f"  {case:<6}{src:<10}{f'{full[0]}/{full[1]}':>15}{f'{calls[0]}/{calls[1]}':>16}   "
              + ("identical" if ok else "DIFFERENT"))
        assert ok, f"{case}: the records do not reproduce the published misses"
    print()


if __name__ == "__main__":
    main()
