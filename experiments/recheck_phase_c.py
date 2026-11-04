#!/usr/bin/env python3
"""Post-hoc re-check of the Phase C editorial-plan answers (deviation 13).

  PYTHONPATH=src .venv/bin/python -m experiments.recheck_phase_c

The frozen gate of the editorial plan checks each (keyword, volume) the agent
reports against the tool outputs stored in the trace. In the prudent arm of
Phase C those outputs are the trimmed ones the agent saw (plus what `expand`
returned), so a keyword whose volume had been hidden had no truth to be checked
against, and the gate skipped it. This script checks every reported pair
against the raw outputs of all the other runs of the same case, collected the
same day with the same tool (Phase A runs A1-A8 and the Phase C raw runs):

  own        verified against the run's own outputs (what the gate counted)
  other      not in the run's own outputs, verified against the other runs
  mismatch   the keyword appears in the raw outputs with different volumes only
  unknown    the keyword appears in no raw output: cannot be verified

For the unknown pairs, two more columns say whether the keyword and the number
both appear somewhere in the run's own outputs, and whether the number appears
in what `expand` returned.

The same is done for the raw arm, as a control. Nothing here changes the
frozen result; it reports what that result did not cover.
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
    print(f"\n  {'run':<14}{'pairs':>6}{'own':>6}{'other':>7}{'mismatch':>10}{'unknown':>9}{'in own text':>13}{'in expand':>11}")
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
            counts = {"pairs": 0, "own": 0, "other": 0, "mismatch": 0, "unknown": 0,
                      "unverified_in_own_text": 0, "unverified_volume_in_expand": 0}
            own_text = " ".join(o for _, o in outputs(trace)).lower()
            expand_text = " ".join(o for t, o in outputs(trace) if t == "expand")
            for kw, vol in pairs(final_json(trace) or {}):
                counts["pairs"] += 1
                if kw in own and vol in own[kw]:
                    counts["own"] += 1
                elif vol in other.get(kw, set()):
                    counts["other"] += 1
                else:
                    known = kw in own or kw in other
                    counts["mismatch" if known else "unknown"] += 1
                    if not known:
                        counts["unverified_in_own_text"] += kw in own_text and str(vol) in own_text
                        counts["unverified_volume_in_expand"] += str(vol) in expand_text
            res["runs"][path.stem] = counts
            print(f"  {path.stem:<14}" + "".join(f"{counts[k]:>{w}}" for k, w in
                                                    (("pairs", 6), ("own", 6), ("other", 7), ("mismatch", 10), ("unknown", 9),
                                                     ("unverified_in_own_text", 13), ("unverified_volume_in_expand", 11))))
    (ROOT / "results" / "phase_c_recheck.json").write_text(json.dumps(res, indent=2) + "\n")
    print("\n  written: results/phase_c_recheck.json\n")


if __name__ == "__main__":
    main()
