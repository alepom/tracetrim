#!/usr/bin/env python3
"""Budget of the study, from the pilot's MEASURED usage, not from guesses.

  .venv/bin/python -m experiments.budget
  .venv/bin/python -m experiments.budget --c3-scale 0.5     # e.g. OpenAlex at 5 results per page
  .venv/bin/python -m experiments.budget --v04 [--json]     # the measures of protocol-v04.md (E1, E2)
  .venv/bin/python -m experiments.budget --v06 [--json]     # the DRAFT of protocol-v06.md (E4): printed, never written

Live run cost  = the pilot run's billed usage (cache included, as it happened).
Replay cost    = the pilot's LAST turn: its input is the whole conversation
                 the replay re-sends; no cache discount assumed (conservative:
                 the 2nd and 3rd replay of the same arm usually hit the cache).
Cases without a pilot yet (C4, C5) are assumed to cost like C1, and say so.
"""
import argparse
import json

import statistics
import sys

from .caps import caps
from .paths import PRICES, STUDY_TRACES, TRACES, cost

# protocol-v04.md: the design the estimate is computed for. Changing a number
# here is changing the protocol.
V04 = {
    # C2 has no trimmed arm: the configuration tracetrim builds on its traces trims no tool.
    "e1": {"cases": ["C1", "C2", "C4"], "arms": {"C1": ["R", "S", "T", "ST"], "C2": ["R", "S"],
                                                 "C4": ["R", "S", "T", "ST"]}, "runs_per_arm": 5},
    # E2's real case is C1, not C2: the configuration tracetrim builds on the C2 traces trims no tool.
    "e2": {"cases": ["C1", "C4"], "arms": ["base", "promotion", "sampling", "promotion+sampling"],
           "sequences_per_arm": {"C1": 2, "C4": 2}, "runs_per_sequence": 5,
           # The variant asks for more than the original task: assumed cost of a run, relative to
           # the original task's. An assumption, not a measure: the first runs will correct it.
           "variant_factor": 1.25,
           "sample_rate": 0.25, "sample_max_share": 0.5},
    # DataForSEO is billed apart and its price per call is not known here: after this many runs
    # that call it, everything that calls it stops until the author has read the spend.
    "dataforseo_checkpoint_runs": 10,
}


# protocol-v06.md (a DRAFT until frozen): deferred tool definitions, on C1 with every tool of the
# server enabled. Changing a number here is changing the protocol.
V06 = {
    "case": "C1",
    "pilot": {"R": 1, "D": 1},                       # used in no result: does a run with 79 tools work at all?
    "e4a": {"arms": ["R", "D"], "runs_per_arm": 5},  # the task as it is: independent runs, a fixed list in D
    # the variant needs a tool that D hides: R as independent runs, D as one sequence that learns
    "e4b": {"runs": {"R": 3, "D": 5}, "variant_factor": 1.25},
    "dataforseo_checkpoint_runs": 4,
}


def v06() -> dict:
    """The estimate for the draft of protocol-v06.md. Two figures per run, both from measured runs:

    expected      a recorded run of C1 (E1, arm R: median) times what the counterfactual of
                  `experiments.definitions` gives for 79 tools at a cold start;
    conservative  the dearest recorded run, plus the added definitions at the uncached price on
                  every turn of the longest run: no cache for them at all.

    Arm D is costed like R in both: its searches may add turns, and the estimate must not count on
    the saving it is there to measure. Nothing is written: the amounts go in the report only."""
    import os
    from pathlib import Path

    from .paths import ROOT
    res = ROOT / "results" / "v06" / "definitions.json"
    if not res.exists():
        sys.exit("results/v06/definitions.json is needed: python -m experiments.definitions")
    sc = json.loads(res.read_text())["scenario_all_server_tools"]
    traces = Path(os.environ.get("TRACETRIM_V04_TRACES") or ROOT / "traces")
    metas = [json.loads(p.read_text()) for p in sorted((traces / V06["case"]).glob("E1-R-*.meta.json"))]
    if not metas:
        sys.exit(f"{traces / V06['case']}: the E1 runs of arm R are needed (set $TRACETRIM_V04_TRACES)")
    added = sc["definitions"]["all_server_tools"] - sc["definitions"]["case_tools_12"]
    expected = statistics.median(m["cost_usd"] for m in metas) * sc["cache"]["cold"]["R79_cost_vs_recorded"]["median"]
    conservative = (max(m["cost_usd"] for m in metas)
                    + added * max(m["turns"] for m in metas) * PRICES["input_per_mtok"] / 1e6)
    calls, calls_max = statistics.median(m["calls"] for m in metas), max(m["calls"] for m in metas)
    lines = []
    for arm, n in V06["pilot"].items():
        lines.append({"part": "pilot", "arm": arm, "runs": n, "factor": 1.0})
    for arm in V06["e4a"]["arms"]:
        lines.append({"part": "E4a", "arm": arm, "runs": V06["e4a"]["runs_per_arm"], "factor": 1.0})
    for arm, n in V06["e4b"]["runs"].items():
        lines.append({"part": "E4b", "arm": arm, "runs": n, "factor": V06["e4b"]["variant_factor"]})
    for x in lines:
        x["usd"], x["usd_conservative"] = x["runs"] * x["factor"] * expected, x["runs"] * x["factor"] * conservative
    runs = sum(x["runs"] for x in lines)
    return {"prices": {k: PRICES[k] for k in ("model", "input_per_mtok", "cached_input_per_mtok", "output_per_mtok")},
            "measured": {"runs": len(metas), "source": "E1, arm R, C1 (12 tools)", "definitions_added_per_turn": added,
                         "largest_request": sc["largest_request_R79"]},
            "per_run": {"expected": expected, "conservative": conservative},
            "design": V06, "lines": lines,
            "totals": {"runs": runs, "usd": sum(x["usd"] for x in lines),
                       "usd_conservative": sum(x["usd_conservative"] for x in lines),
                       "tuning": caps("v06", "tuning")},
            "dataforseo_calls": {"runs": runs, "calls": runs * calls, "calls_max": runs * calls_max}}


def print_v06(r: dict) -> None:
    print(f"\n  DRAFT of protocol-v06.md (E4). From {r['measured']['runs']} recorded runs: {r['measured']['source']}; "
          f"{r['measured']['definitions_added_per_turn']:,} tokens of definitions added per turn")
    print(f"  per run: expected {r['per_run']['expected']:.3f} USD, conservative {r['per_run']['conservative']:.3f}")
    print(f"\n  {'part':<7}{'arm':<5}{'runs':>5}{'USD':>8}{'conservative':>14}")
    for x in r["lines"]:
        print(f"  {x['part']:<7}{x['arm']:<5}{x['runs']:>5}{x['usd']:>8.2f}{x['usd_conservative']:>14.2f}")
    t = r["totals"]
    print(f"\n  {t['runs']} runs: {t['usd']:.2f} USD expected, {t['usd_conservative']:.2f} conservative"
          f" (+ tuning {t['tuning']:.2f}; cap {caps('v06')[1]:.0f}, warning at {caps('v06')[0]:.0f}: local, never published)")
    d = r["dataforseo_calls"]
    print(f"  DataForSEO calls: {d['runs']} runs x {d['calls'] / d['runs']:g} = {d['calls']:g}"
          f" (at the maximum seen per run: {d['calls_max']:g}); billed apart, price per call not known here")
    print(f"  largest request with 79 tools: {r['measured']['largest_request']:,} tokens (the price list changes above 272,000)\n")


def measured(case: str) -> dict:
    """What a live run of the case cost in the original study: its raw live runs
    (Phase A and the raw arm of Phase C), from their meta files."""
    metas = [json.loads(p.read_text()) for pattern in ("A*.meta.json", "C-raw-*.meta.json")
             for p in sorted((STUDY_TRACES / case).glob(pattern))]
    if not metas:
        sys.exit(f"{case}: no run of the original study in {STUDY_TRACES} (set $TRACETRIM_TRACES)")
    return {"runs": len(metas),
            "cost": statistics.median(m["cost_usd"] for m in metas), "cost_max": max(m["cost_usd"] for m in metas),
            "turns": statistics.median(m["turns"] for m in metas),
            "calls": statistics.median(m["calls"] for m in metas), "calls_max": max(m["calls"] for m in metas)}


def v04() -> dict:
    """The estimate for protocol-v04.md, from measured run costs.

    Conservative on purpose: every arm is costed like a raw run of the case,
    though in Phase C the trimmed arm cost less; the skill text is added at the
    uncached input price for every turn, though most of it is cached.
    """
    from experiments.skill_tokens import rows
    skill = rows()
    m = {c: measured(c) for c in sorted(set(V04["e1"]["cases"]) | set(V04["e2"]["cases"]))}
    per_token = PRICES["input_per_mtok"] / 1e6
    # Instructions an arm adds to the system prompt: T the expand part, S the strategy, ST both.
    added = {"R": 0, "S": skill["text_strategy_added"], "T": skill["function_calling_base"],
             "ST": skill["function_calling_base_strategy"]}
    e1, e2 = [], []
    for c in V04["e1"]["cases"]:
        for arm in V04["e1"]["arms"][c]:
            n = V04["e1"]["runs_per_arm"]
            extra = added[arm] * m[c]["turns"] * per_token
            e1.append({"case": c, "arm": arm, "runs": n, "usd": n * (m[c]["cost"] + extra),
                       "usd_max": n * (m[c]["cost_max"] + extra)})
    for c in V04["e2"]["cases"]:
        for arm in V04["e2"]["arms"]:
            n = V04["e2"]["sequences_per_arm"][c] * V04["e2"]["runs_per_sequence"]
            f = V04["e2"]["variant_factor"]
            extra = added["T"] * m[c]["turns"] * per_token
            e2.append({"case": c, "arm": arm, "runs": n, "usd": n * (m[c]["cost"] * f + extra),
                       "usd_max": n * (m[c]["cost_max"] * f + extra)})
    total = lambda rows_, k: sum(r[k] for r in rows_)     # noqa: E731
    dfs = {c: {"runs": sum(r["runs"] for r in e1 + e2 if r["case"] == c)} for c in ("C1", "C2")}
    for c, d in dfs.items():
        d["calls"], d["calls_max"] = d["runs"] * m[c]["calls"], d["runs"] * m[c]["calls_max"]
    return {
        "prices": {k: PRICES[k] for k in ("model", "input_per_mtok", "cached_input_per_mtok", "output_per_mtok")},
        "measured_per_run": m, "design": V04, "skill_tokens_added": added, "e1": e1, "e2": e2,
        "totals": {"e1": total(e1, "usd"), "e2": total(e2, "usd"), "tuning": caps("v04", "tuning"),
                   "all": total(e1, "usd") + total(e2, "usd") + caps("v04", "tuning"),
                   "all_if_every_run_costs_the_maximum_seen": total(e1, "usd_max") + total(e2, "usd_max") + caps("v04", "tuning"),
                   "runs": sum(r["runs"] for r in e1 + e2)},
        "dataforseo_calls": dfs,
    }


def print_v04(r: dict) -> None:
    print(f"\n  measured in the original study, raw live runs ({r['prices']['model']})")
    print(f"  {'case':<6}{'runs':>5}{'USD/run':>9}{'max':>8}{'turns':>7}{'calls':>7}")
    for c, m in r["measured_per_run"].items():
        print(f"  {c:<6}{m['runs']:>5}{m['cost']:>9.3f}{m['cost_max']:>8.3f}{m['turns']:>7g}{m['calls']:>7g}")
    for name in ("e1", "e2"):
        print(f"\n  {name.upper()}   {'case':<5}{'arm':<20}{'runs':>5}{'USD':>8}")
        for x in r[name]:
            print(f"       {x['case']:<5}{x['arm']:<20}{x['runs']:>5}{x['usd']:>8.2f}")
    t = r["totals"]
    print(f"\n  E1 {t['e1']:.2f} + E2 {t['e2']:.2f} + tuning {t['tuning']:.2f} = {t['all']:.2f} USD over {t['runs']} runs"
          f"   (cap {caps('v04')[1]:.0f}, warning at {caps('v04')[0]:.0f}: local, never published)")
    print(f"  if every run cost the maximum seen: {t['all_if_every_run_costs_the_maximum_seen']:.2f} USD")
    for c, d in r["dataforseo_calls"].items():
        print(f"  DataForSEO calls, {c}: {d['runs']} runs x {d['calls'] / d['runs']:g} = {d['calls']:g}"
              f" (at the maximum seen per run: {d['calls_max']:g})")
    print()


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
    a.add_argument("--v04", action="store_true", help="estimate for protocol-v04.md (E1, E2)")
    a.add_argument("--v06", action="store_true", help="estimate for the draft of protocol-v06.md (E4)")
    a.add_argument("--json", action="store_true")
    o = a.parse_args()
    if o.v06:
        r = v06()
        print(json.dumps(r, indent=2)) if o.json else print_v06(r)
        return
    if o.v04:
        r = v04()
        print(json.dumps(r, indent=2)) if o.json else print_v04(r)
        return

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
