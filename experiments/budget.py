#!/usr/bin/env python3
"""Budget of the study, from the pilot's MEASURED usage, not from guesses.

  .venv/bin/python -m experiments.budget
  .venv/bin/python -m experiments.budget --c3-scale 0.5     # e.g. OpenAlex at 5 results per page

Live run cost  = the pilot run's billed usage (cache included, as it happened).
Replay cost    = the pilot's LAST turn: its input is the whole conversation
                 the replay re-sends; no cache discount assumed (conservative:
                 the 2nd and 3rd replay of the same arm usually hit the cache).
Cases without a pilot yet (C4, C5) are assumed to cost like C1, and say so.
"""
import argparse
import json

from .paths import PRICES, TRACES, cost


def pilot(case):
    # pilot2 is the verification pilot after the system-prompt fix: its
    # usage is the one the study will see.
    for name in ("pilot2", "pilot"):
        p = TRACES / case / f"{name}.meta.json"
        if p.exists():
            return json.loads(p.read_text())
    return None


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--c3-scale", type=float, default=1.0, help="payload scale for C3 (per_page 5 -> 0.5)")
    a.add_argument("--live", type=int, default=10, help="Phase A runs per case")
    a.add_argument("--arms", type=int, default=5, help="Phase B arms")
    a.add_argument("--replays", type=int, default=3, help="Phase B runs per arm and trace")
    a.add_argument("--traces", type=int, default=2, help="Phase B traces per case")
    a.add_argument("--phase-c", default="C1,C2,C4", help="cases with live expand runs")
    a.add_argument("--phase-c-runs", type=int, default=5, help="Phase C runs per arm")
    o = a.parse_args()

    cases = ["C1", "C2", "C3", "C4", "C5"]
    live, replay, note = {}, {}, {}
    for c in cases:
        m = pilot(c) or pilot("C1")
        note[c] = "pilot" if pilot(c) else "assumed = C1 pilot"
        last = m["usage_per_turn"][-1]
        scale = o.c3_scale if c == "C3" else 1.0
        # C3's pilot2 already ran at 5 results per page: no scaling.
        scale = 1.0 if m["run"] == "pilot2" or c != "C3" else scale
        live[c] = m["cost_usd"] * scale
        replay[c] = cost({"input": last["input"] * scale, "cached": 0, "output": last["output"]})

    print(f"\n  {'case':<6}{'live run':>10}{'replay run':>12}   source")
    print("  " + "-" * 52)
    for c in cases:
        print(f"  {c:<6}{live[c]:>10.3f}{replay[c]:>12.3f}   {note[c]}")

    a_cost = sum(live[c] * o.live for c in cases)
    b_cost = sum(replay[c] * o.traces * o.replays * o.arms for c in cases)
    pc = [c for c in o.phase_c.split(",") if c]
    c_cost = sum(live[c] * o.phase_c_runs * 2 for c in pc)
    pilots_left = sum(live[c] for c in cases if note[c] != "pilot")
    total = a_cost + b_cost + c_cost + pilots_left
    print(f"\n  pilots still to run   {pilots_left:>7.2f}")
    print(f"  Phase A               {a_cost:>7.2f}   {o.live} runs x {len(cases)} cases")
    print(f"  Phase B               {b_cost:>7.2f}   {o.traces} traces x {o.replays} runs x {o.arms} arms x {len(cases)} cases, no cache")
    print(f"  Phase C               {c_cost:>7.2f}   {o.phase_c_runs} runs x 2 arms on {', '.join(pc)}")
    print(f"  {'TOTAL to spend':<22}{total:>7.2f}   prices assumed: experiments/prices.json\n")


if __name__ == "__main__":
    main()
