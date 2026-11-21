"""The analysis fixed in protocol-v3b.md, version 2.

  python -m experiments.v3b.analysis                       # after the campaign (and the judge, if planned)
  python -m experiments.v3b.analysis --fake SEED --out F   # invented judge scores, to test the script

Primary: P minus R on the share of the facts P hides that the answer reports correctly
(experiments/v3b/facts.py), paired within repetition, averaged within task, tasks equal; percentile
bootstrap 95% (10,000 draws, seed 311, tasks resampled with all repetitions and both arms);
preserved if the lower endpoint is above -0.10. Secondary: the same estimator on the share of all
key facts (no margin) and, when judged, on the Gemini composite (described at -0.5 and -0.25);
gates, grounding, expand and how each hidden value reached the model in P, process measures with
cost relative to R, agreement of the passes. Costs only relative to R, never as amounts.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from statistics import mean

from experiments.checks import check, final_json
from experiments.paths import cost
from experiments.v3.coordinator import DIMS
from tracetrim.grounding import unsupported
from tracetrim.traces import load
from tracetrim.usage import leaves, parse_output

from . import facts
from .hidden import matches
from .run import PRIVATE, campaign_slots, where
from .tools import task_data
from .worlds import DATA

PRIMARY_MARGIN = -0.10
MARGIN, SENSITIVITY = -0.5, -0.25          # the judged composite, secondary in version 2
DRAWS, SEED = 10_000, 311
DETAIL = {"stripe_retrieve_invoice", "stripe_retrieve_charge", "stripe_list_refunds"}


def per_task(values: dict[str, float]) -> dict[str, float]:
    diffs: dict[str, list] = {}
    for slot, v in values.items():
        task, rep, arm = slot.split("/")
        if arm == "P" and f"{task}/{rep}/R" in values:
            diffs.setdefault(task, []).append(v - values[f"{task}/{rep}/R"])
    return {t: mean(d) for t, d in diffs.items()}


def bootstrap(tasks: dict[str, float], draws: int = DRAWS, seed: int = SEED):
    v = [tasks[t] for t in sorted(tasks)]
    if not v:
        return None
    rng = random.Random(seed)
    xs = sorted(mean(rng.choice(v) for _ in v) for _ in range(draws))
    return [xs[int(0.025 * draws)], xs[min(draws - 1, int(0.975 * draws))]]


def verdict(ci, margin):
    if ci is None:
        return "unavailable"
    return "preserved" if ci[0] > margin else "material loss" if ci[1] < margin else "inconclusive"


def contrast(values: dict[str, float], margins=(MARGIN, SENSITIVITY)) -> dict:
    t = per_task(values)
    ci = bootstrap(t)
    return {"point": mean(t.values()) if t else None, "ci95": ci, "tasks": len(t),
            **{f"verdict_{abs(m):g}": verdict(ci, m) for m in margins},
            "wholly_below_zero": ci is not None and ci[1] < 0, "per_task": t}


def reached(fact: dict, events: list[dict]) -> list[str]:
    """How a hidden fact's value reached the model in one run: list (trimmed), detail, expand."""
    tail = fact["hidden"][1].split(".")[-1]
    how = set()
    for e in events:
        if e.get("kind") != "call":
            continue
        text = e.get("sent", e["output"])
        payload = parse_output(text.split("\n[trimmed]")[0])
        if not isinstance(payload, (dict, list)):
            continue
        if any(p.split(".")[-1] == tail and matches(fact, v) for p, v in leaves(payload)):
            how.add("expand" if e["tool"] == "expand" else "detail" if e["tool"] in DETAIL else "list")
    return sorted(how)


def analyse(judged: dict, gone: dict) -> dict:
    camp = where(PRIVATE)
    slots = [s for s in campaign_slots() if s not in gone]
    composite = {s: mean(judged["scores"][s][d] for d in DIMS) for s in slots} if judged else None
    shares, hidden, gates, ground, meta, recovery = {}, {}, {}, {}, {}, []
    for s in slots:
        task, rep, arm = s.split("/")
        key = task_data(task)["key"]
        trace = camp.trace(s)
        sc = facts.score_trace(trace, key)
        shares[s], hidden[s] = sc["share"], sc["hidden_share"]
        g = check(trace, "C4")
        gates[s] = bool(g["json"] and g["structure"] and g["facts"] > 0 and g["facts_ok"] == g["facts"])
        n, unsup = unsupported(load(trace))
        ground[s] = {"values": n, "unsupported": len(unsup)}
        meta[s] = json.loads(trace.with_suffix(".meta.json").read_text())
        if arm == "P":
            events = [json.loads(x) for x in trace.read_text().splitlines() if x.strip()]
            answered = facts.tickets_of(final_json(load(trace)))
            for k in key["ticket"]:
                for f in k["facts"]:
                    if "hidden" in f:
                        ok = k["id"] in answered and facts.found(f, answered[k["id"]])
                        recovery.append({"slot": s, "ticket": k["id"], "template": k["template"],
                                         "reached": reached(f, events), "reported": ok})

    def arm_rows(arm, f):
        return f([s for s in slots if s.endswith("/" + arm)])

    def proc(ss):
        m = [meta[s] for s in ss]
        return {"runs": len(m), "calls": mean(x["calls"] for x in m), "turns": mean(x["turns"] for x in m),
                "expand_calls": sum(x["expand_calls"] for x in m), "runs_with_expand": sum(x["expand_calls"] > 0 for x in m),
                "outputs_original_tokens": sum(x["tokens"]["outputs_original"] for x in m),
                "outputs_delivered_tokens": sum(sum(x["tokens"]["outputs_sent"].values()) + x["tokens"]["expand_replies"]
                                                for x in m),
                "flags": sorted({f for x in m for f in x["flags"]}), "_cost": sum(cost(x["usage"]) for x in m)}

    process = {a: arm_rows(a, proc) for a in "RP"}
    ref = process["R"].pop("_cost")
    process["P"]["cost_relative_to_R"] = process["P"].pop("_cost") / ref if ref else None
    process["R"]["cost_relative_to_R"] = 1.0

    def bounds(values, lo, hi, margins=(MARGIN, SENSITIVITY)):
        worst = dict(values) | {s: (lo if s.endswith("/P") else hi) for s in gone}
        best = dict(values) | {s: (hi if s.endswith("/P") else lo) for s in gone}
        return {"worst": contrast(worst, margins)["ci95"], "best": contrast(best, margins)["ci95"]}

    def paired(flags):
        out = {"both": 0, "only_R": 0, "only_P": 0, "neither": 0}
        for s, v in flags.items():
            task, rep, arm = s.split("/")
            r = flags.get(f"{task}/{rep}/R")
            if arm == "P" and r is not None:
                out["both" if v and r else "only_R" if r else "only_P" if v else "neither"] += 1
        return out

    by_template = {}
    for r in recovery:
        b = by_template.setdefault(r["template"], {"facts": 0, "reached": 0, "reported": 0, "via": {}})
        b["facts"] += 1
        b["reached"] += bool(r["reached"])
        b["reported"] += r["reported"]
        for h in r["reached"]:
            b["via"][h] = b["via"].get(h, 0) + 1
    return {
        "protocol": "protocol-v3b.md", "answers": len(slots), "missing": {a: sum(s.endswith("/" + a) for s in gone) for a in "RP"},
        "primary_hidden_fact_share_P_minus_R": contrast(hidden, (PRIMARY_MARGIN,))
        | ({"missing_bounds": bounds(hidden, 0.0, 1.0, (PRIMARY_MARGIN,))} if gone else {}),
        "secondary_fact_share_P_minus_R": contrast(shares, ())
        | ({"missing_bounds": bounds(shares, 0.0, 1.0, ())} if gone else {}),
        "secondary_composite_P_minus_R": (contrast(composite) | ({"missing_bounds": bounds(composite, 0.0, 4.0)}
                                                                if gone else {})) if judged else None,
        "composite_by_arm": {a: arm_rows(a, lambda ss: mean(composite[s] for s in ss)) for a in "RP"} if judged else None,
        "dimensions_by_arm": {a: arm_rows(a, lambda ss: {d: mean(judged["scores"][s][d] for s in ss) for d in DIMS})
                              for a in "RP"} if judged else None,
        "fact_share_by_arm": {a: arm_rows(a, lambda ss: mean(shares[s] for s in ss)) for a in "RP"},
        "hidden_fact_share_by_arm": {a: arm_rows(a, lambda ss: mean(hidden[s] for s in ss)) for a in "RP"},
        "gates": {"by_arm": {a: arm_rows(a, lambda ss: sum(gates[s] for s in ss)) for a in "RP"}, "paired": paired(gates)},
        "grounding": {a: arm_rows(a, lambda ss: {"values": sum(ground[s]["values"] for s in ss),
                                                 "unsupported": sum(ground[s]["unsupported"] for s in ss),
                                                 "answers_with_unsupported": sum(ground[s]["unsupported"] > 0 for s in ss)})
                      for a in "RP"},
        "hidden_recovery_P": {"facts": len(recovery), "reached": sum(bool(r["reached"]) for r in recovery),
                              "reported": sum(r["reported"] for r in recovery),
                              "reported_when_reached": sum(r["reported"] for r in recovery if r["reached"]),
                              "reported_when_not_reached": sum(r["reported"] for r in recovery if not r["reached"]),
                              "by_template": by_template},
        "agreement": judged.get("agreement") if judged else None,
        "adjudicated": judged.get("adjudicated") if judged else None,
        "process": process,
    }


def fake(seed: int, gone: dict) -> dict:
    rng = random.Random(seed)
    camp = where(PRIVATE)
    slots = [s for s in campaign_slots() if s not in gone and camp.trace(s).exists()]
    return {"scores": {s: {d: rng.randint(0, 4) for d in DIMS} for s in slots}, "agreement": None, "adjudicated": 0}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fake", type=int, metavar="SEED")
    p.add_argument("--out", type=Path, default=PRIVATE / "analysis.json")
    a = p.parse_args(argv)
    camp = where(PRIVATE)
    gone = json.loads(camp.missing.read_text()) if camp.missing.exists() else {}
    if a.fake is not None:
        if a.out == PRIVATE / "analysis.json":
            raise SystemExit("--fake needs its own --out")
        judged = fake(a.fake, gone)
        gone = gone | {s: "not run (fake analysis)" for s in campaign_slots() if s not in judged["scores"] and s not in gone}
    else:
        planned = json.loads((DATA / "selection.json").read_text()).get("judge")
        judged = json.loads((PRIVATE / "judged.json").read_text()) if planned else None
    res = analyse(judged, gone) | ({"FAKE_SCORES": True} if a.fake is not None else {})
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    h, c = res["primary_hidden_fact_share_P_minus_R"], res["secondary_fact_share_P_minus_R"]
    j = res["secondary_composite_P_minus_R"]
    print(f"hidden facts P-R: {h['point']} CI {h['ci95']} -> {h['verdict_0.1']}; all facts P-R: {c['point']} "
          f"CI {c['ci95']}; composite P-R: {j and j['point']} CI {j and j['ci95']}; written {a.out}")


if __name__ == "__main__":
    main()
