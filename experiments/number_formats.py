#!/usr/bin/env python3
"""Did any agent write a number with comma thousands separators?

  PYTHONPATH=src .venv/bin/python -m experiments.number_formats

Usage detection in the studies matched numbers written plainly ("1600") or
with dot separators ("1.600"), not with commas ("1,600"). This counts, in every
Phase A run, the comma-grouped numbers the agent wrote (in tool-call arguments
and messages) and how many equal an integer of the tool outputs: those are the
uses the studies could have missed.
"""
import json
import re
from tracetrim.traces import load
from tracetrim.usage import leaves, parse_output

from .run import ROOT, TRACES
COMMA = re.compile(r"\b\d{1,3}(?:,\d{3})+\b")


def main():
    res = {}
    print(f"\n  {'case':<6}{'runs':>5}{'comma-grouped written':>24}{'equal to an output integer':>28}")
    for case in ("C1", "C2", "C3", "C4", "C5"):
        written = matched = 0
        paths = sorted((TRACES / case).glob("A*.jsonl"))
        if not paths:
            print(f"  {case:<6}traces not released")
            continue
        for p in paths:
            t = load(p)
            ints = set()
            for e in t.events:
                if e.kind == "call" and e.output:
                    pl = parse_output(e.output)
                    if isinstance(pl, (dict, list)):
                        ints |= {v for _, v in leaves(pl) if isinstance(v, int) and not isinstance(v, bool)}
            for e in t.events:
                if e.kind in ("message", "call"):
                    for m in COMMA.findall(e.text or ""):
                        written += 1
                        matched += int(m.replace(",", "")) in ints
        res[case] = {"runs": len(paths), "written": written, "matched": matched}
        print(f"  {case:<6}{len(paths):>5}{written:>24}{matched:>28}")
    if len(res) < 5:
        print("\n  not every case is available: results/number_formats.json left as it is\n")
        return
    (ROOT / "results" / "number_formats.json").write_text(json.dumps(res, indent=2) + "\n")
    print("\n  written: results/number_formats.json\n")


if __name__ == "__main__":
    main()
