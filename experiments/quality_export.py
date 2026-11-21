#!/usr/bin/env python3
"""The publishable results of protocols v3 and v3b, from their private campaign directories.

  .venv/bin/python -m experiments.quality_export      # writes results/v3/quality.json, results/v3b/quality.json

Reads out/v3 and out/v3b (git-ignored: traces, ledgers, blind scores, analyses) and writes only
what the paper reports: estimates and intervals, levels by arm, agreement, gates, grounding,
process measures and the hidden-field breakdown. Token counts appear; costs appear only as a
ratio to the raw arm R. No amount, no ledger line, no key or answer text is copied. Every task is
synthetic, so task ids may appear.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .paths import ROOT

OUT = ROOT / "out"
RES = ROOT / "results"
JUDGE = {"model": "gemini-3.8-flash", "endpoint": "Vertex AI, global", "thinking_level": "HIGH",
         "grouping": "one call per task and pass: the evidence once, the task's answers under neutral labels"}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def no_amounts(obj, where=""):
    """Refuse any key that could carry money; only ratios to R are allowed."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if ("cost" in k or "usd" in k.lower() or "spend" in k) and k != "cost_relative_to_R":
                raise ValueError(f"amount-like key {where}/{k}")
            no_amounts(v, f"{where}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            no_amounts(v, f"{where}[{i}]")


def ratings(directory: Path, n: int = 2) -> dict:
    c = Counter()
    for i in range(1, n + 1):
        for row in load(directory / f"scores-{i}.json").values():
            c.update(row.values())
    return {str(k): c[k] for k in range(5)}


def models(directory: Path) -> list[str]:
    seen = set()
    for meta in (directory / "traces").rglob("*.meta.json"):
        seen.update(load(meta)["model_returned"])
    return sorted(seen)


def strip(contrast: dict) -> dict:
    return {k: v for k, v in contrast.items()}


def v3() -> dict:
    d = OUT / "v3"
    a = load(d / "analysis.json")
    exposure = load(OUT / "v3-exposure" / "final.json")
    full_cheaper = Counter()
    for meta in (d / "traces").rglob("*.meta.json"):
        m = load(meta)
        full_cheaper[(m["task"][:2], m["arm"])] += m["exposure"].get("full_cheaper", 0)
    return {
        "protocol": "protocol-v3.md", "deviations": "experiments/v3/DEVIATIONS.md",
        "agent_models_returned": models(d), "judge": JUDGE,
        "design": {"cases": ["C4", "C5"], "tasks_per_case": 10, "repetitions": 2, "arms": ["R", "P", "I"],
                   "runs": a["answers"] + sum(a["missing"].values())},
        "answers": a["answers"], "missing": a["missing"],
        "H1_P_minus_R": a["H1_P_minus_R"], "secondary_I_minus_R": a["secondary_I_minus_R"],
        "dimensions": a["dimensions"], "ratings_both_passes": ratings(d),
        "agreement": a["agreement"], "adjudicated": a["adjudicated"],
        "answers_with_cues": load(d / "cues.json")["answers_with_cues"],
        "gates": a["gates"], "grounding": a["grounding"], "process": a["process"],
        "full_cheaper_calls": {c: {arm: full_cheaper[(c, arm)] for arm in "RPI"} for c in ("C4", "C5")},
        "exposure_replay": {c: {arm: exposure[c]["pooled"][arm] for arm in "RPI"} for c in ("C4", "C5")},
    }


def v3b() -> dict:
    import experiments.v3b.analysis as an
    from experiments.checks import final_json
    from experiments.v3b import facts, run
    from experiments.v3b.tools import task_data
    from tracetrim.traces import load as load_trace
    d = OUT / "v3b"
    a = load(d / "analysis.json")
    selection = load(ROOT / "experiments/v3b/material/selection.json")
    v1 = load(d / "calibration" / "scores-v1.json")
    w = run.where(run.PRIVATE)
    rows: dict[str, Counter] = {}
    for slot in run.campaign_slots():
        task, rep, arm = slot.split("/")
        trace = w.trace(slot)
        answered = facts.tickets_of(final_json(load_trace(trace)))
        events = [json.loads(x) for x in trace.read_text().splitlines() if x.strip()]
        for k in task_data(task)["key"]["ticket"]:
            for f in k["facts"]:
                if "hidden" not in f:
                    continue
                name = f"{k['template']}: {f['hidden'][1]}"
                c = rows.setdefault(name, Counter())
                c[f"{arm}_facts"] += 1
                c[f"{arm}_reported"] += k["id"] in answered and facts.found(f, answered[k["id"]])
                if arm == "P":
                    via = an.reached(f, events)
                    c["P_reached"] += bool(via)
                    for h in via:
                        c[f"P_via_{h}"] += 1
    breakdown = {}
    for name, c in sorted(rows.items()):
        # The preregistered reach check confuses the replacement link with `lines.data[].invoice`
        # (REPORT-13b, deviation 1): its reach counts are not reported.
        reach_ok = not name.startswith("replacement")
        breakdown[name] = {"R": [c["R_reported"], c["R_facts"]], "P": [c["P_reported"], c["P_facts"]],
                           "P_reached": c["P_reached"] if reach_ok else None,
                           "P_via": ({h: c[f"P_via_{h}"] for h in ("expand", "detail") if c[f"P_via_{h}"]}
                                     if reach_ok else None)}
    return {
        "protocol": "protocol-v3b.md (version 2)", "agent_models_returned": models(d), "judge": JUDGE,
        "calibration": {"candidates": len(selection["calibration_share_R"]),
                        "eligible_version_1": 0, "version_1_rule": "0.40 <= R fact share <= 0.90",
                        "R_fact_share_version_1_scorer": {t: s["share"] for t, s in sorted(v1.items())},
                        "R_fact_share_version_2_scorer": selection["calibration_share_R"],
                        "R_hidden_share": selection["calibration_hidden_share_R"]},
        "design": {"case": "C4", "tasks": selection["kept"], "repetitions": selection["repetitions"],
                   "arms": ["R", "P"], "runs": 2 * selection["repetitions"] * len(selection["kept"]),
                   "hidden_facts_per_task": {t: sum("hidden" in f for k in task_data(t)["key"]["ticket"]
                                                    for f in k["facts"]) for t in selection["kept"]},
                   "judge_kept_by_budget_rule": selection["judge"]},
        "answers": a["answers"], "missing": a["missing"],
        "primary_hidden_fact_share_P_minus_R": a["primary_hidden_fact_share_P_minus_R"],
        "secondary_fact_share_P_minus_R": a["secondary_fact_share_P_minus_R"],
        "secondary_composite_P_minus_R": a["secondary_composite_P_minus_R"],
        "levels": {"hidden_fact_share": a["hidden_fact_share_by_arm"], "fact_share": a["fact_share_by_arm"],
                   "composite": a["composite_by_arm"], "dimensions": a["dimensions_by_arm"]},
        "hidden_fields": breakdown,
        "construction_check": hidden_check(),
        "ratings_both_passes": ratings(d), "agreement": a["agreement"], "adjudicated": a["adjudicated"],
        "answers_with_cues": load(d / "cues.json")["answers_with_cues"],
        "gates": a["gates"], "grounding": a["grounding"], "process": a["process"],
    }


def hidden_check() -> dict:
    """experiments.v3b.hidden on every candidate: hidden facts present in the original, absent from what P delivers."""
    import asyncio
    from experiments.v3b import hidden
    from experiments.v3b.worlds import CANDIDATES

    async def run_all():
        return [await hidden.check(f"C4b-{i:02}") for i in range(1, CANDIDATES + 1)]
    rows = asyncio.run(run_all())
    return {"candidates": len(rows), "hidden_facts": sum(r["hidden_facts"] for r in rows),
            "verified": sum(r["verified"] for r in rows),
            "per_task_min": min(r["hidden_facts"] for r in rows), "per_task_max": max(r["hidden_facts"] for r in rows)}


def main():
    for name, build in (("v3", v3), ("v3b", v3b)):
        data = build()
        no_amounts(data)
        path = RES / name / "quality.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{path.relative_to(ROOT)}: {len(json.dumps(data))} bytes")


if __name__ == "__main__":
    main()
