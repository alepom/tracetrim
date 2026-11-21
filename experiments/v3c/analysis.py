"""The analysis fixed in protocol-v3c.md.

  python -m experiments.v3c.analysis                       # after the campaign (and the judge, if run)
  python -m experiments.v3c.analysis --out F               # elsewhere (a check before the judge has run)

Primary: P2 minus P1 on the hidden-fact share of the hard tasks, paired within repetition, averaged within
task, tasks equal, percentile bootstrap 95% (10,000 draws, seed 311); improvement if the lower end is above 0.
Secondary: all facts, the two free-text facts, expand and how hidden values reached the model, cost P2/P1;
P1 and P2 against R of protocol v3b (historical, descriptive); on the ordinary tasks cost P2/P1, calls, turns,
expand, gates and grounding; the judged composite if judged. Then the preregistered decision on the default.
Costs only as ratios, never as amounts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

from experiments.checks import check, final_json
from experiments.paths import cost
from experiments.v3.coordinator import DIMS
from experiments.v3b import analysis as v3b
from experiments.v3b import facts
from experiments.v3b import run as v3b_run
from tracetrim.grounding import unsupported
from tracetrim.traces import load

from .run import HARD, ORDINARY, PRIVATE, slots, task_data, where

TEXT_FACTS = ("balance: data[].description", "memo: data[].description")
COST_LIMIT = 1.10


def paired(values: dict[str, float], a: str = "P2", b: str = "P1") -> dict[str, float]:
    diffs: dict[str, list] = {}
    for slot, v in values.items():
        task, rep, arm = slot.split("/")
        if arm == a and f"{task}/{rep}/{b}" in values:
            diffs.setdefault(task, []).append(v - values[f"{task}/{rep}/{b}"])
    return {t: mean(d) for t, d in diffs.items()}


def contrast(per_task: dict[str, float]) -> dict:
    ci = v3b.bootstrap(per_task)
    verdict = ("unavailable" if ci is None else "improved" if ci[0] > 0 else "worse" if ci[1] < 0
               else "no improvement shown")
    return {"point": mean(per_task.values()) if per_task else None, "ci95": ci, "tasks": len(per_task),
            "verdict": verdict, "per_task": per_task}


def hidden_facts(trace: Path, key: dict) -> list[dict]:
    """One row per hidden fact of the key: its kind and whether the answer reports it."""
    answered = facts.tickets_of(final_json(load(trace)))
    events = [json.loads(x) for x in trace.read_text().splitlines() if x.strip()]
    rows = []
    for k in key["ticket"]:
        for f in k["facts"]:
            if "hidden" in f:
                rows.append({"kind": f"{k['template']}: {f['hidden'][1]}",
                             "reported": k["id"] in answered and facts.found(f, answered[k["id"]]),
                             "reached": v3b.reached(f, events)})
    return rows


def analyse(judged: dict | None) -> dict:
    w = where()
    gone = json.loads(w.missing.read_text()) if w.missing.exists() else {}
    present = [s for s in slots() if s not in gone]
    hard = [s for s in present if s.startswith("C4b-")]
    ordinary = [s for s in present if not s.startswith("C4b-")]
    meta = {s: json.loads(w.trace(s).with_suffix(".meta.json").read_text()) for s in present}

    hidden, share, kinds = {}, {}, {}
    for s in hard:
        key = task_data(s.split("/")[0])["key"]
        sc = facts.score_trace(w.trace(s), key)
        hidden[s], share[s] = sc["hidden_share"], sc["share"]
        for row in hidden_facts(w.trace(s), key):
            arm = s.rsplit("/", 1)[1]
            k = kinds.setdefault(row["kind"], {a: {"facts": 0, "reported": 0, "reached": 0, "via": {}} for a in ("P1", "P2")})
            k[arm]["facts"] += 1
            k[arm]["reported"] += row["reported"]
            k[arm]["reached"] += bool(row["reached"])
            for h in row["reached"]:
                k[arm]["via"][h] = k[arm]["via"].get(h, 0) + 1
    for name, k in kinds.items():
        if name.startswith("replacement"):        # the reach check confuses this link (REPORT-13b, deviation 1)
            for arm in k.values():
                arm["reached"], arm["via"] = None, None

    # R of protocol v3b, the same tasks and keys: historical, descriptive.
    r_hidden = {}
    vw = v3b_run.where(v3b_run.PRIVATE)
    for t in HARD:
        vals = [facts.score_trace(vw.trace(f"{t}/{r}/R"), task_data(t)["key"])["hidden_share"]
                for r in (1, 2, 3) if vw.trace(f"{t}/{r}/R").exists()]
        if vals:
            r_hidden[t] = mean(vals)

    def by_task(arm):
        out = {}
        for s in hard:
            t, _, a = s.split("/")
            if a == arm:
                out.setdefault(t, []).append(hidden[s])
        return {t: mean(v) for t, v in out.items()}

    def vs_r(arm):
        mine = by_task(arm)
        return contrast({t: mine[t] - r_hidden[t] for t in mine if t in r_hidden})

    def proc(ss):
        if not ss:
            return None
        m = [meta[s] for s in ss]
        return {"runs": len(m), "calls": mean(x["calls"] for x in m), "turns": mean(x["turns"] for x in m),
                "expand_calls": sum(x["expand_calls"] for x in m), "runs_with_expand": sum(x["expand_calls"] > 0 for x in m),
                "outputs_delivered_tokens": sum(sum(x["tokens"]["outputs_sent"].values()) + x["tokens"]["expand_replies"]
                                                for x in m),
                "flags": sorted({f for x in m for f in x["flags"]}), "_cost": sum(cost(x["usage"]) for x in m)}

    def costs(group):
        p = {a: proc([s for s in group if s.endswith("/" + a)]) for a in ("P1", "P2")}
        ratio = p["P2"]["_cost"] / p["P1"]["_cost"] if p["P1"] and p["P2"] and p["P1"]["_cost"] else None
        for v in p.values():
            if v:
                v.pop("_cost")
        return p, ratio

    hard_proc, hard_cost = costs(hard)
    ord_proc, ord_cost = costs(ordinary)
    gates, ground = {}, {}
    for s in ordinary:
        g = check(w.trace(s), "C4")
        gates[s] = bool(g["json"] and g["structure"] and g["facts"] > 0 and g["facts_ok"] == g["facts"])
        n, unsup = unsupported(load(w.trace(s)))
        ground[s] = len(unsup)
    gates_by = {a: sum(v for s, v in gates.items() if s.endswith("/" + a)) for a in ("P1", "P2")}

    primary = contrast(paired(hidden))
    decision = {"h1c": primary["verdict"] == "improved",
                "ordinary_cost_ratio": ord_cost, "ordinary_cost_within_limit": ord_cost is not None and ord_cost <= COST_LIMIT,
                "ordinary_gates": gates_by, "ordinary_gates_not_fewer": gates_by["P2"] >= gates_by["P1"]}
    decision["default_version"] = "2" if all((decision["h1c"], decision["ordinary_cost_within_limit"],
                                               decision["ordinary_gates_not_fewer"])) else "1"
    composite = None
    if judged:
        comp = {s: mean(judged["scores"][s][d] for d in DIMS) for s in hard if s in judged["scores"]}
        composite = contrast(paired(comp)) | {
            "by_arm": {a: mean(v for s, v in comp.items() if s.endswith("/" + a)) for a in ("P1", "P2")},
            "dimensions": {a: {d: mean(judged["scores"][s][d] for s in comp if s.endswith("/" + a)) for d in DIMS}
                           for a in ("P1", "P2")},
            "agreement": judged.get("agreement"), "adjudicated": judged.get("adjudicated")}
    return {
        "protocol": "protocol-v3c.md", "runs": len(present), "missing": {a: sum(s.endswith("/" + a) for s in gone)
                                                                           for a in ("P1", "P2")},
        "primary_hidden_fact_share_P2_minus_P1": primary,
        "secondary_fact_share_P2_minus_P1": contrast(paired(share)),
        "hidden_fact_share_by_arm": {a: mean(v for s, v in hidden.items() if s.endswith("/" + a)) for a in ("P1", "P2")},
        "fact_share_by_arm": {a: mean(v for s, v in share.items() if s.endswith("/" + a)) for a in ("P1", "P2")},
        "hidden_fields": kinds, "text_facts": {k: kinds.get(k) for k in TEXT_FACTS},
        "historical_R_hidden_share": mean(r_hidden.values()) if r_hidden else None,
        "P1_minus_R_historical": vs_r("P1"), "P2_minus_R_historical": vs_r("P2"),
        "hard_process": hard_proc, "hard_cost_P2_over_P1": hard_cost,
        "ordinary_process": ord_proc, "ordinary_cost_P2_over_P1": ord_cost,
        "ordinary_gates": gates_by, "ordinary_answers": {a: sum(s.endswith("/" + a) for s in ordinary) for a in ("P1", "P2")},
        "ordinary_unsupported": {a: sum(v for s, v in ground.items() if s.endswith("/" + a)) for a in ("P1", "P2")},
        "secondary_composite_P2_minus_P1": composite,
        "decision": decision, "tasks": {"hard": HARD, "ordinary": ORDINARY},
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=PRIVATE / "analysis.json")
    a = p.parse_args(argv)
    judged_path = PRIVATE / "judged.json"
    judged = json.loads(judged_path.read_text()) if judged_path.exists() else None
    res = analyse(judged)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    h = res["primary_hidden_fact_share_P2_minus_P1"]
    print(f"hidden facts P2-P1: {h['point']} CI {h['ci95']} -> {h['verdict']}; "
          f"ordinary cost P2/P1 {res['ordinary_cost_P2_over_P1']}; default -> version {res['decision']['default_version']}; "
          f"written {a.out}")


if __name__ == "__main__":
    main()
