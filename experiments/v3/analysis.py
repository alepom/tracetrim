"""The analysis fixed in protocol-v3.md ("Outcomes and analysis"), run after the blind scores.

  python -m experiments.v3.analysis                      # on out/v3/judged.json (after `judging finish`)
  python -m experiments.v3.analysis --fake SEED --out F  # on invented scores, to test the script

Computes, from the private campaign directory (out/v3):
  H1        P minus R, paired within repetition, averaged within task, tasks equal within
            case, cases equal; percentile bootstrap 95% (10,000 draws, seed 311, tasks
            resampled within case with both repetitions and all arms); verdict at -0.5
  secondary the same verdict at -0.25 (descriptive) and I minus R with the same estimator
  bounds    0/4 worst and best case for slots missing for infrastructure reasons
  gates     experiments.checks.check on final answer + original tool returns, paired failures
  grounding tracetrim.grounding.unsupported, per arm and paired
  agreement of the two blind passes (from the coordinator), adjudicated answers
  process   calls, turns, recovery requests, raw/delivered tokens, cost relative to R
Writes a JSON results file. Costs appear only relative to R, never as amounts.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from statistics import mean

from experiments.checks import check
from experiments.paths import cost
from tracetrim.grounding import unsupported
from tracetrim.traces import load

from . import coordinator
from .live import Where

DIMS = coordinator.DIMS
CASES = ("C4", "C5")
MARGIN, SENSITIVITY = -0.5, -0.25
DRAWS, SEED = 10_000, 311


def composite(row: dict) -> float:
    return mean(row[d] for d in DIMS)


def units(scores: dict[str, float], contrast: str) -> dict[str, dict[str, float]]:
    """case -> task -> mean over repetitions of (arm minus R); repetitions without both are left out."""
    out: dict[str, dict[str, list]] = {c: {} for c in CASES}
    for slot, value in scores.items():
        task, rep, arm = slot.split("/")
        if arm != contrast:
            continue
        ref = scores.get(f"{task}/{rep}/R")
        if ref is not None:
            out[task[:2]].setdefault(task, []).append(value - ref)
    return {c: {t: mean(v) for t, v in tasks.items()} for c, tasks in out.items()}


def estimate(per_task: dict[str, dict[str, float]]) -> dict:
    by_case = {c: mean(t.values()) if t else None for c, t in per_task.items()}
    combined = mean(by_case.values()) if all(v is not None for v in by_case.values()) else None
    return {"combined": combined, "by_case": by_case}


def bootstrap(per_task: dict[str, dict[str, float]], draws: int = DRAWS, seed: int = SEED) -> dict:
    rng = random.Random(seed)
    lists = {c: list(t.values()) for c, t in per_task.items()}
    combined, by_case = [], {c: [] for c in CASES}
    for _ in range(draws):
        means = {}
        for c in CASES:                                     # fixed case order: the draws are reproducible
            v = lists[c]
            means[c] = mean(rng.choice(v) for _ in v) if v else None
            if means[c] is not None:
                by_case[c].append(means[c])
        if all(m is not None for m in means.values()):
            combined.append(mean(means.values()))

    def interval(xs):
        if not xs:
            return None
        xs = sorted(xs)
        return [xs[int(0.025 * len(xs))], xs[min(len(xs) - 1, int(0.975 * len(xs)))]]
    return {"combined": interval(combined), "by_case": {c: interval(by_case[c]) for c in CASES}}


def verdict(ci, margin: float) -> str:
    if ci is None:
        return "unavailable"
    if ci[0] > margin:
        return "preserved"
    if ci[1] < margin:
        return "material loss"
    return "inconclusive"


def contrast(scores: dict[str, float], arm: str) -> dict:
    per_task = units(scores, arm)
    ci = bootstrap(per_task)
    return {"point": estimate(per_task), "ci95": ci, "tasks": {c: len(t) for c, t in per_task.items()},
            "verdict_0.5": verdict(ci["combined"], MARGIN), "verdict_0.25": verdict(ci["combined"], SENSITIVITY),
            "per_task": per_task}


def gate(trace: Path, case: str) -> dict:
    g = check(trace, case)
    passed = g["json"] and g["structure"] and g["facts"] > 0 and g["facts_ok"] == g["facts"]
    return {"pass": bool(passed), "zero_denominator": g["json"] and g["structure"] and g["facts"] == 0,
            "json": g["json"], "structure": g["structure"], "facts": g["facts"], "facts_ok": g["facts_ok"]}


def paired(per_slot: dict[str, bool], arm: str) -> dict:
    """Blocks where R and the arm both ran: how the binary outcome splits."""
    out = {"both": 0, "only_R": 0, "only_" + arm: 0, "neither": 0}
    for slot, v in per_slot.items():
        task, rep, a = slot.split("/")
        r = per_slot.get(f"{task}/{rep}/R")
        if a != arm or r is None:
            continue
        out["both" if v and r else "only_R" if r else "only_" + arm if v else "neither"] += 1
    return out


def analyse(where: Where, judged: dict, missing: dict) -> dict:
    schedule = coordinator.schedule()
    scores = {s: composite(r) for s, r in judged["scores"].items()}
    present = [s for s in schedule if s not in missing]
    if set(scores) != set(present):
        raise ValueError("the scores do not cover exactly the slots with a trace")
    gates, grounding, process = {}, {}, {}
    for s in present:
        trace = where.trace(s)
        gates[s] = gate(trace, s[:2])
        n, unsup = unsupported(load(trace))
        grounding[s] = {"values": n, "unsupported": len(unsup)}
        process[s] = json.loads(trace.with_suffix(".meta.json").read_text())

    def by_arm(f):
        return {c: {a: f([s for s in present if s.startswith(c) and s.endswith("/" + a)]) for a in "RPI"}
                for c in CASES}

    def dims(slots):
        return {d: mean(judged["scores"][s][d] for s in slots) if slots else None for d in DIMS} | {
            "composite": mean(scores[s] for s in slots) if slots else None, "answers": len(slots)}

    def proc(slots):
        if not slots:
            return None
        m = [process[s] for s in slots]
        sent = lambda x: sum(x["tokens"]["outputs_sent"].values()) + x["tokens"]["expand_replies"]
        return {"runs": len(m), "calls": mean(x["calls"] for x in m), "turns": mean(x["turns"] for x in m),
                "expand_calls": sum(x["expand_calls"] for x in m),
                "runs_with_expand": sum(x["expand_calls"] > 0 for x in m),
                "outputs_original_tokens": sum(x["tokens"]["outputs_original"] for x in m),
                "outputs_delivered_tokens": sum(sent(x) for x in m),
                "input_tokens": sum(x["usage"]["input"] for x in m), "output_tokens": sum(x["usage"]["output"] for x in m),
                "flags": sorted({f for x in m for f in x["flags"]}),
                "_cost": sum(cost(x["usage"]) for x in m)}

    process_arm = by_arm(proc)
    for c in CASES:                      # costs only relative to R (list prices), never as amounts
        ref = (process_arm[c]["R"] or {}).get("_cost")
        for a in "RPI":
            p = process_arm[c][a]
            if p is not None:
                p["cost_relative_to_R"] = p.pop("_cost") / ref if ref else None

    def bounds(arm):
        worst = dict(scores) | {s: (0.0 if s.endswith("/" + arm) else 4.0) for s in missing}
        best = dict(scores) | {s: (4.0 if s.endswith("/" + arm) else 0.0) for s in missing}
        return {"worst": contrast(worst, arm)["ci95"]["combined"], "best": contrast(best, arm)["ci95"]["combined"]}

    return {
        "protocol": "protocol-v3.md",
        "answers": len(present), "missing": {a: sum(s.endswith("/" + a) for s in missing) for a in "RPI"},
        "H1_P_minus_R": contrast(scores, "P") | ({"missing_bounds": bounds("P")} if missing else {}),
        "secondary_I_minus_R": contrast(scores, "I") | ({"missing_bounds": bounds("I")} if missing else {}),
        "dimensions": by_arm(dims),
        "gates": {"by_arm": by_arm(lambda ss: {"pass": sum(gates[s]["pass"] for s in ss),
                                               "zero_denominator": sum(gates[s]["zero_denominator"] for s in ss),
                                               "answers": len(ss)}),
                  "paired": {a: paired({s: g["pass"] for s, g in gates.items()}, a) for a in "PI"}},
        "grounding": {"by_arm": by_arm(lambda ss: {"values": sum(grounding[s]["values"] for s in ss),
                                                   "unsupported": sum(grounding[s]["unsupported"] for s in ss),
                                                   "answers_with_unsupported": sum(grounding[s]["unsupported"] > 0
                                                                                   for s in ss)}),
                      "paired": {a: paired({s: g["unsupported"] == 0 for s, g in grounding.items()}, a)
                                 for a in "PI"}},
        "agreement": judged.get("agreement"), "adjudicated": judged.get("adjudicated"),
        "process": process_arm,
    }


def fake(where: Where, seed: int, missing: dict) -> dict:
    """Invented scores for the slots that have a trace: tests the script, never a result."""
    rng = random.Random(seed)
    slots = [s for s in coordinator.schedule() if where.trace(s).exists() and s not in missing]
    return {"scores": {s: {d: rng.randint(0, 4) for d in DIMS} for s in slots},
            "agreement": {d: {"exact": None, "within_one": None, "quadratic_kappa": None} for d in DIMS},
            "adjudicated": 0, "fake": True}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fake", type=int, metavar="SEED", help="invented scores for the slots run so far")
    p.add_argument("--out", type=Path, default=coordinator.PRIVATE / "analysis.json")
    a = p.parse_args(argv)
    where = Where()
    gone = json.loads(where.missing.read_text()) if where.missing.exists() else {}
    if a.fake is not None:
        if a.out == coordinator.PRIVATE / "analysis.json":
            raise SystemExit("--fake needs its own --out")
        judged = fake(where, a.fake, gone)
        gone = gone | {s: "not run yet (fake analysis)" for s in coordinator.schedule()
                       if s not in judged["scores"] and s not in gone}
    else:
        judged = coordinator.load(coordinator.PRIVATE / "judged.json")
    res = analyse(where, judged, gone) | ({"FAKE_SCORES": True} if a.fake is not None else {})
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    h = res["H1_P_minus_R"]
    print(f"H1 P-R: {h['point']['combined']} CI {h['ci95']['combined']} -> {h['verdict_0.5']} "
          f"(at -0.25: {h['verdict_0.25']}); tasks {h['tasks']}; written {a.out}")


if __name__ == "__main__":
    main()
