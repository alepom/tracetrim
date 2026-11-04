#!/usr/bin/env python3
"""Post-hoc re-check of the Phase C editorial-plan answers (deviation 13).

  PYTHONPATH=src .venv/bin/python -m experiments.recheck_phase_c

The frozen gate of the editorial plan checks each (keyword, volume) the agent
reports against the tool outputs of its own run, and skips a keyword that does
not appear there with a volume. This script checks every reported pair also
against the raw outputs of all the other runs of the same case, collected the
same day with the same tool (Phase A runs A1-A8 and the Phase C raw runs):

  own        verified against the run's own outputs (what the gate counted)
  other      not in the run's own outputs, verified against the other runs
  mismatch   the keyword has volumes in the outputs, none equal to the one written
  unknown    the keyword has a volume in no output: cannot be verified

For each mismatch the volume written and those in the other runs are kept,
not the keyword (search data are not redistributed). The outputs are read with
experiments.checks.outputs, which cuts the `[trimmed]` line of projected
outputs: without that cut they do not parse (the review found this).
"""
import json
from pathlib import Path

from tracetrim.traces import load

from .checks import final_json, outputs
from tracetrim.usage import parse_output

ROOT = Path(__file__).resolve().parent.parent
# The editorial-plan traces are not redistributable (search-data terms): this
# runs only where they were recorded; its output is in results/.
C1 = ROOT / "traces" / "C1"


def truth_of(outs):
    truth = {}
    for _, o in outs:
        p = parse_output(o)

        def visit(n):
            if isinstance(n, dict):
                if isinstance(n.get("keyword"), str):
                    for v in (n.get("search_volume"), (n.get("keyword_info") or {}).get("search_volume")):
                        if isinstance(v, int):
                            truth.setdefault(n["keyword"].lower().strip(), set()).add(v)
                for v in n.values():
                    visit(v)
            elif isinstance(n, list):
                for v in n:
                    visit(v)
        if isinstance(p, (dict, list)):
            visit(p)
    return truth


def pairs(ans):
    for v in (ans.get("piano_editoriale_brevissimo_termine") or []) + (ans.get("piano_editoriale_medio_periodo") or []):
        for k in v.get("keyword_correlate") or []:
            kw = str(k.get("keyword", "")).lower().strip()
            try:
                vol = int(str(k.get("volume_ricerca")).replace(".", "").replace(",", ""))
            except ValueError:
                vol = None
            yield kw, vol


def main():
    if not (C1 / "C-raw-1.jsonl").exists():
        print("\n  the editorial-plan traces are not here: results/phase_c_recheck.json left as it is\n")
        return
    reference = [C1 / f"A{i}.jsonl" for i in range(1, 9)] + [C1 / f"C-raw-{i}.jsonl" for i in (1, 2, 3)]
    res = {"reference_runs": [p.stem for p in reference], "runs": {}}
    print(f"\n  {'run':<14}{'pairs':>6}{'own':>6}{'other':>7}{'mismatch':>10}{'unknown':>9}   mismatches")
    for arm in ("raw", "prudent"):
        for i in (1, 2, 3):
            path = C1 / f"C-{arm}-{i}.jsonl"
            trace = load(path)
            own = truth_of(outputs(trace))
            other = {}
            for ref in reference:
                if ref == path:
                    continue
                for kw, vols in truth_of(outputs(load(ref))).items():
                    other.setdefault(kw, set()).update(vols)
            counts = {"pairs": 0, "own": 0, "other": 0, "mismatch": 0, "unknown": 0, "mismatches": []}
            for kw, vol in pairs(final_json(trace) or {}):
                counts["pairs"] += 1
                if kw in own and vol in own[kw]:
                    counts["own"] += 1
                elif vol in other.get(kw, set()):
                    counts["other"] += 1
                elif kw in own or kw in other:
                    counts["mismatch"] += 1
                    counts["mismatches"].append({"written": vol, "in_own_outputs": sorted(own.get(kw, [])),
                                                 "in_other_runs": sorted(other.get(kw, []))})
                else:
                    counts["unknown"] += 1
            res["runs"][path.stem] = counts
            print(f"  {path.stem:<14}" + "".join(f"{counts[k]:>{w}}" for k, w in
                                                    (("pairs", 6), ("own", 6), ("other", 7), ("mismatch", 10), ("unknown", 9)))
                  + f"   {counts['mismatches'] or ''}")
    (ROOT / "results" / "phase_c_recheck.json").write_text(json.dumps(res, indent=2) + "\n")
    print("\n  written: results/phase_c_recheck.json\n")


if __name__ == "__main__":
    main()
