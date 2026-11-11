"""What protocol-v04.md takes for granted, computed from the traces of the original study. No model, no spend.

  TRACETRIM_TRACES=... python -m experiments.v04_preconditions      # writes results/v04/preconditions.json

For each case: which tools the configuration tracetrim builds from the eight
Phase A traces would trim (the configuration the T arms and E2 start from),
and, for the fields E2's task variants make necessary, that the projection
removes them and in how many recorded calls they are present. Field paths and
counts only: no value of any tool output is written.
"""
import json

from tracetrim import analyse, load
from tracetrim import report as rp
from tracetrim.usage import leaves

from .paths import ROOT, STUDY_TRACES

OUT = ROOT / "results" / "v04" / "preconditions.json"
CASES = ("C1", "C2", "C4")
# E2: the fields each variant of the task needs (experiments/prompts/c1v-user.md, c4v-user.md).
NEEDED = {"C1": {"serp_youtube_organic_live_advanced": ["items[].channel_name", "items[].views_count"]},
          "C4": {"stripe_list_invoices": ["data[].hosted_invoice_url", "data[].collection_method"]}}


def case(name: str) -> dict:
    paths = sorted((STUDY_TRACES / name).glob("A*.jsonl"))
    usages = [analyse(load(p)) for p in paths]
    d = rp.compute([p.name for p in paths], usages)
    config, projection = rp.build_config(d, rp.suggestions(d), usages, "report.projection.json")
    total = d["totals"]["raw"]
    tools = {t: {"trimmed": t in config["tools"], "calls": v["calls"], "share_of_tokens": round(v["raw"] / total, 4),
                 "held_out_saving": round(1 - v["loo_prudent"] / v["loo_raw"], 4) if v["loo_raw"] else None}
             for t, v in d["tools"].items()}
    needed = {}
    for tool, fields in NEEDED.get(name, {}).items():
        calls = [c for u in usages for c in u if c.tool == tool and c.structured]
        per_run = [sum(c.tool == tool and c.structured for c in u) for u in usages]
        dropped = projection.seen.get(tool, set()) - projection.keep.get(tool, set())
        needed[tool] = {"calls": len(calls), "calls_per_run": per_run, "fields": {
            f: {"removed_by_the_projection": f in dropped,
                "present_in_calls": sum(any(p == f for p, _ in leaves(c.payload)) for c in calls),
                "used_in_runs": sum(any(c.tool == tool and f in c.used for c in u) for u in usages)}
            for f in fields}}
    return {"traces": len(paths), "tool_output_tokens": total,
            "held_out_saving": round(1 - d["totals"]["loo_prudent"] / total, 4),
            "tools_trimmed": sorted(config["tools"]), "tools": tools, "fields_needed_by_the_variant": needed}


if __name__ == "__main__":
    res = {"what": "preconditions of protocol-v04.md, from the Phase A traces of the original study",
           "tokens": "tiktoken o200k_base", "cases": {c: case(c) for c in CASES}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    for c, r in res["cases"].items():
        print(f"{c}: {r['traces']} traces, held-out saving {r['held_out_saving']:.0%}, trimmed: "
              f"{', '.join(r['tools_trimmed']) or 'no tool'}")
        for tool, n in r["fields_needed_by_the_variant"].items():
            for f, v in n["fields"].items():
                print(f"   {tool} {f}: removed {v['removed_by_the_projection']}, in {v['present_in_calls']}/{n['calls']} "
                      f"calls, used in {v['used_in_runs']} runs; calls per run {n['calls_per_run']}")
