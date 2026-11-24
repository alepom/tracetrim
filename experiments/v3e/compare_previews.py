"""What the text previews of 1.5 add to the tool-output tokens of the public traces of Study 1 (no spend, no model).

  python -m experiments.v3e.compare_previews      # results/v3e/compare_previews.json; reads dataset/traces

`tracetrim compare` on the eight Phase A runs of each public case (C3, C4, C5), leave-one-out, prudent projection with
the defaults of 1.5, once with `text_preview=0` (the line of 1.4) and once with the default previews. The fields
hidden are the same (the misses do not change); the difference is the length of the [trimmed] lines.
"""
from __future__ import annotations

import json

from experiments.paths import ROOT
from tracetrim import compare

CASES = ("C3", "C4", "C5")


def main():
    out = {}
    for case in CASES:
        paths = sorted((ROOT / "dataset" / "traces" / case).glob("A*.jsonl"))
        d = compare.compute([str(p) for p in paths], compare.recipe("text_preview=0"), compare.recipe("text_preview=40"))
        a, b = d["totals"]["a"], d["totals"]["b"]
        out[case] = {"runs": d["eligible_runs"], "calls": d["totals"]["calls"], "tokens_without": a["tokens"],
                     "tokens_with": b["tokens"], "added": round(b["tokens"] / a["tokens"] - 1, 4),
                     "miss_calls_without": a["miss_calls"], "miss_calls_with": b["miss_calls"]}
        print(f"{case}: {a['tokens']:>9} -> {b['tokens']:>9} tool-output tokens served ({out[case]['added']:+.1%}); "
              f"misses {a['miss_calls']} -> {b['miss_calls']}")
    (ROOT / "results/v3e/compare_previews.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
