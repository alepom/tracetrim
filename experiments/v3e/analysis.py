"""The analysis fixed in protocol-v3e.md (no spend).

  python -m experiments.v3e.analysis            # out/v3e/analysis.json (private; costs as ratios only)

Primary (H1): P2T - P2 on the share of hidden facts reported on the hard tasks, paired within repetition, averaged
within task, tasks equal, percentile bootstrap 95% of protocol v3b; «improved» if the lower end is above 0.
Secondary: all facts; hidden facts by kind, how they reached the model (list, detail, expand) and how many were
on a preview; process and cost P2T/P2 on hard and ordinary tasks; on the ordinary tasks gates and grounding.
Then the preregistered decision on the default.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

from experiments.checks import check, final_json
from experiments.paths import cost
from experiments.v3b import analysis as v3b
from experiments.v3b import facts
from experiments.v3b.hidden import matches
from experiments.v3c.analysis import contrast, paired
from tracetrim.engine import MARK, TEXT_LABEL
from tracetrim.grounding import unsupported
from tracetrim.traces import load

from .run import ARMS, HARD, ORDINARY, PRIVATE, slots, task_data, where

COST_LIMIT = 1.10          # ordinary tasks: P2T may cost at most 10% more than P2 to become the default


def on_preview(fact: dict, events: list[dict]) -> bool:
    """Whether the fact's value was readable on the previews of a [trimmed] line the model received."""
    for e in events:
        sent = e.get("sent") or ""
        if e.get("kind") == "call" and MARK in sent:
            tail = sent.split(MARK, 1)[1]
            if TEXT_LABEL in tail and matches(fact, tail.split(TEXT_LABEL, 1)[1]):
                return True
    return False


def hidden_facts(trace: Path, key: dict) -> list[dict]:
    answered = facts.tickets_of(final_json(load(trace)))
    events = [json.loads(x) for x in trace.read_text().splitlines() if x.strip()]
    return [{"kind": f"{k['template']}: {f['hidden'][1]}",
             "reported": k["id"] in answered and facts.found(f, answered[k["id"]]),
             "reached": v3b.reached(f, events), "on_preview": on_preview(f, events)}
            for k in key["ticket"] for f in k["facts"] if "hidden" in f]


def analyse() -> dict:
    w = where()
    gone = json.loads(w.missing.read_text()) if w.missing.exists() else {}
    present = [s for s in slots() if s not in gone and w.trace(s).exists()]
    hard = [s for s in present if s.startswith("C4b-")]
    ordinary = [s for s in present if not s.startswith("C4b-")]
    meta = {s: json.loads(w.trace(s).with_suffix(".meta.json").read_text()) for s in present}

    hidden, share, kinds = {}, {}, {}
    for s in hard:
        arm = s.rsplit("/", 1)[1]
        key = task_data(s.split("/")[0])["key"]
        sc = facts.score_trace(w.trace(s), key)
        hidden[s], share[s] = sc["hidden_share"], sc["share"]
        for row in hidden_facts(w.trace(s), key):
            k = kinds.setdefault(row["kind"], {a: {"facts": 0, "reported": 0, "reached": 0, "on_preview": 0,
                                                   "reported_from_preview_only": 0} for a in ARMS})
            k[arm]["facts"] += 1
            k[arm]["reported"] += row["reported"]
            k[arm]["reached"] += bool(row["reached"])
            k[arm]["on_preview"] += row["on_preview"]
            k[arm]["reported_from_preview_only"] += row["reported"] and row["on_preview"] and not row["reached"]
    for name, k in kinds.items():
        if name.startswith("replacement"):        # the reach check confuses this link (REPORT-13b, deviation 1)
            for v in k.values():
                v["reached"] = None

    def proc(ss):
        if not ss:
            return None
        m = [meta[s] for s in ss]
        return {"runs": len(m), "calls": mean(x["calls"] for x in m), "turns": mean(x["turns"] for x in m),
                "expand_calls": sum(x["expand_calls"] for x in m),
                "runs_with_expand": sum(x["expand_calls"] > 0 for x in m),
                "outputs_delivered_tokens": sum(sum(x["tokens"]["outputs_sent"].values()) + x["tokens"]["expand_replies"]
                                                for x in m),
                "flags": sorted({f for x in m for f in x["flags"]}), "_cost": sum(cost(x["usage"]) for x in m)}

    def costs(group):
        p = {a: proc([s for s in group if s.endswith("/" + a)]) for a in ARMS}
        ratio = p["P2T"]["_cost"] / p["P2"]["_cost"] if p["P2"] and p["P2T"] and p["P2"]["_cost"] else None
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
        ground[s] = len(unsupported(load(w.trace(s)))[1])
    gates_by = {a: sum(v for s, v in gates.items() if s.endswith("/" + a)) for a in ARMS}

    primary = contrast(paired(hidden, "P2T", "P2"))
    decision = {"h1": primary["verdict"] == "improved",
                "ordinary_cost_ratio": ord_cost, "ordinary_cost_within_limit": ord_cost is not None and ord_cost <= COST_LIMIT,
                "ordinary_gates": gates_by, "ordinary_gates_not_fewer": gates_by["P2T"] >= gates_by["P2"]}
    decision["recommend_default_on"] = all((decision["h1"], decision["ordinary_cost_within_limit"],
                                            decision["ordinary_gates_not_fewer"]))
    return {
        "protocol": "protocol-v3e.md", "runs": len(present),
        "missing": {a: sum(s.endswith("/" + a) for s in gone) for a in ARMS},
        "primary_hidden_fact_share_P2T_minus_P2": primary,
        "secondary_fact_share_P2T_minus_P2": contrast(paired(share, "P2T", "P2")),
        "hidden_fact_share_by_arm": {a: mean(v for s, v in hidden.items() if s.endswith("/" + a)) for a in ARMS
                                     if any(s.endswith("/" + a) for s in hidden)},
        "fact_share_by_arm": {a: mean(v for s, v in share.items() if s.endswith("/" + a)) for a in ARMS
                              if any(s.endswith("/" + a) for s in share)},
        "hidden_fields": kinds,
        "hard_process": hard_proc, "hard_cost_P2T_over_P2": hard_cost,
        "ordinary_process": ord_proc, "ordinary_cost_P2T_over_P2": ord_cost,
        "ordinary_gates": gates_by,
        "ordinary_unsupported": {a: sum(v for s, v in ground.items() if s.endswith("/" + a)) for a in ARMS},
        "decision": decision, "tasks": {"hard": HARD, "ordinary": ORDINARY},
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=PRIVATE / "analysis.json")
    a = p.parse_args(argv)
    res = analyse()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    h = res["primary_hidden_fact_share_P2T_minus_P2"]
    print(f"hidden facts by arm {res['hidden_fact_share_by_arm']}; P2T-P2 {h['point']} CI {h['ci95']} -> {h['verdict']}; "
          f"ordinary cost P2T/P2 {res['ordinary_cost_P2T_over_P2']}; recommend default on: "
          f"{res['decision']['recommend_default_on']}; written {a.out}")


if __name__ == "__main__":
    main()
