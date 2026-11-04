#!/usr/bin/env python3
"""How much of the saving is re-serialisation alone, with no field removed.

  PYTHONPATH=src .venv/bin/python -m experiments.reserialize_baseline

tracetrim writes projected outputs as compact JSON without ASCII escapes
(json.dumps(payload, ensure_ascii=False)), while tools often return indented
JSON. Part of any saving is therefore lossless re-serialisation: this script
measures it on the same bases as the paper, so that the projection is credited
only with what it removes beyond that.

  phase_a   every tool output of the Phase A runs (the held-out total of Table 1)
  replay    the tool messages of the replayed conversations (Phase B, Study 2,
            Headroom: the basis of Figure 5)

Each total is checked against the one already in results/ before anything is
written: a parser that silently skipped outputs would produce a plausible,
wrong baseline.
"""
import json

from tracetrim.measure import tokenizer
from tracetrim.traces import load
from tracetrim.usage import parse_output

from .run import ROOT, TRACES

RES = ROOT / "results"


def reserialize(output: str) -> str:
    p = parse_output(output)
    if not isinstance(p, (dict, list)):
        return output                      # prose and logs are left as they are, as the projection does
    return json.dumps(p, ensure_ascii=False)


def totals(paths, count):
    raw = res = 0
    for path in paths:
        for e in load(path).events:
            if e.kind == "call" and e.output is not None:
                raw += count(e.output)
                res += count(reserialize(e.output))
    return raw, res


def main():
    count = tokenizer()
    phase_a = json.loads((RES / "phase_a.json").read_text())
    phase_b = json.loads((RES / "phase_b.json").read_text())
    old = json.loads((RES / "reserialize.json").read_text()) if (RES / "reserialize.json").exists() else {"cases": {}}
    out = {"serialiser": "json.dumps(payload, ensure_ascii=False)", "cases": {}}
    print(f"\n  {'case':<6}{'Phase A raw':>14}{'re-serialised':>15}{'':>7}{'replay raw':>13}{'re-serialised':>15}")
    for case in ("C1", "C2", "C3", "C4", "C5"):
        runs = [TRACES / case / f"{r}.jsonl" for r in phase_a["cases"][case]["runs"]]
        if not all(p.exists() for p in runs):
            out["cases"][case] = old["cases"].get(case)
            print(f"  {case:<6}traces not released: kept from results/reserialize.json")
            continue
        a_raw, a_res = totals(runs, count)
        assert a_raw == phase_a["cases"][case]["loo_full"]["raw"], f"{case}: Phase A total differs from results"
        b_raw, b_res = totals([TRACES / case / "A3.jsonl"], count)
        assert b_raw == phase_b["cases"][case]["arms"]["raw"]["tool_tokens"], f"{case}: replay total differs"
        out["cases"][case] = {"phase_a": {"raw": a_raw, "reserialized": a_res, "saving": 1 - a_res / a_raw},
                              "replay": {"raw": b_raw, "reserialized": b_res, "saving": 1 - b_res / b_raw}}
        print(f"  {case:<6}{a_raw:>14,}{a_res:>15,}{f'-{100 * (1 - a_res / a_raw):.1f}%':>7}"
              f"{b_raw:>13,}{b_res:>15,}{f'-{100 * (1 - b_res / b_raw):.1f}%':>8}")
    (RES / "reserialize.json").write_text(json.dumps(out, indent=2) + "\n")
    print("\n  written: results/reserialize.json\n")


if __name__ == "__main__":
    main()
