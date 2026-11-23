"""Preregistered analysis of protocol-v3d.md (no spend).

  python -m experiments.v3d.analysis            # out/v3d/analysis.json (private: includes costs as ratios only)

H1: P2 - P1 on the share of hidden facts reported, paired by task, bootstrap of protocol v3b. Descriptive: P1 - R
and P2 - R, the share of all facts, gates, process and cost relative to R, hidden facts by kind.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

from experiments.checks import check
from experiments.v3b import facts
from experiments.v3c.analysis import contrast, hidden_facts, paired

from .run import ARMS, HARD, PRIVATE, cost, slots, task_data, where


def analyse() -> dict:
    w = where()
    gone = json.loads(w.missing.read_text()) if w.missing.exists() else {}
    present = [s for s in slots() if s not in gone and w.trace(s).exists()]
    meta = {s: json.loads(w.trace(s).with_suffix(".meta.json").read_text()) for s in present}
    hidden, share, gates, kinds = {}, {}, {}, {}
    for s in present:
        task, _, arm = s.split("/")
        key = task_data(task)["key"]
        sc = facts.score_trace(w.trace(s), key)
        hidden[s], share[s] = sc["hidden_share"], sc["share"]
        g = check(w.trace(s), "C4")
        gates[s] = bool(g["json"] and g["structure"] and g["facts"] > 0 and g["facts_ok"] == g["facts"])
        for row in hidden_facts(w.trace(s), key):
            k = kinds.setdefault(row["kind"], {a: {"facts": 0, "reported": 0, "reached": 0} for a in ARMS})
            k[arm]["facts"] += 1
            k[arm]["reported"] += row["reported"]
            k[arm]["reached"] += bool(row["reached"])
    for name, k in kinds.items():
        if name.startswith("replacement"):        # the reach check confuses this link (REPORT-13b, deviation 1)
            for v in k.values():
                v["reached"] = None

    def by_arm(values):
        return {a: mean(v for s, v in values.items() if s.endswith("/" + a)) for a in ARMS
                if any(s.endswith("/" + a) for s in values)}

    def proc(arm):
        m = [meta[s] for s in present if s.endswith("/" + arm)]
        if not m:
            return None
        return {"runs": len(m), "calls": mean(x["calls"] for x in m), "turns": mean(x["turns"] for x in m),
                "expand_calls": sum(x["expand_calls"] for x in m), "runs_with_expand": sum(x["expand_calls"] > 0 for x in m),
                "_cost": sum(cost(x["usage"]) for x in m) / len(m)}

    process = {a: proc(a) for a in ARMS}
    r_cost = process["R"]["_cost"] if process["R"] else None
    for v in process.values():
        if v:
            v["cost_per_run_over_R"] = v["_cost"] / r_cost if r_cost else None
            v.pop("_cost")
    complete = {t for t in HARD if all(any(s.startswith(f"{t}/") and s.endswith("/" + a) for s in present) for a in ARMS)}
    primary = contrast({t: v for t, v in paired(hidden, "P2", "P1").items() if t in complete})
    return {
        "protocol": "protocol-v3d.md", "model": "gemini-3.8-flash", "runs": len(present),
        "missing": {a: sum(s.endswith("/" + a) for s in gone) for a in ARMS},
        "primary_hidden_fact_share_P2_minus_P1": primary,
        "P1_minus_R": contrast(paired(hidden, "P1", "R")), "P2_minus_R": contrast(paired(hidden, "P2", "R")),
        "fact_share_P2_minus_P1": contrast(paired(share, "P2", "P1")),
        "hidden_fact_share_by_arm": by_arm(hidden), "fact_share_by_arm": by_arm(share),
        "gates_passed_by_arm": {a: sum(v for s, v in gates.items() if s.endswith("/" + a)) for a in ARMS},
        "process": process, "hidden_fields": kinds, "tasks": HARD,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=PRIVATE / "analysis.json")
    a = p.parse_args(argv)
    res = analyse()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    h = res["primary_hidden_fact_share_P2_minus_P1"]
    print(f"hidden facts by arm {res['hidden_fact_share_by_arm']}; P2-P1 {h['point']} CI {h['ci95']} -> {h['verdict']}; "
          f"written {a.out}")


if __name__ == "__main__":
    main()
