#!/usr/bin/env python3
"""The tables of protocol-v04.md, from the traces and meta files of its runs.

  python -m experiments.analyze_v04                # this checkout's runs -> results/v04/e1.json, e2.json
  python -m experiments.analyze_v04 --root DIR     # another campaign (a dry run): DIR/results-v04.json

Every number is recomputed from the files of the runs; nothing is typed.
Per arm and case the tables give the median and the range [min-max]: with
five runs per arm no test of significance is claimed. A reduction in calls or
turns is printed beside success and coverage of the same runs, never alone.

The result files hold counts, shares and field paths: no value of any tool output.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import median

from tracetrim import load
from tracetrim.lists import element_usage

from .checks import check
from .coverage import calls_of, coverage, requirement
from .paths import ROOT
from .v04 import NEEDS, Where


def row(meta_path: Path) -> dict:
    """Everything the protocol measures on one run."""
    m = json.loads(meta_path.read_text())
    trace = meta_path.with_name(meta_path.name.replace(".meta.json", ".jsonl"))
    case = m["case"]
    gates = check(trace, case)
    calls = calls_of(trace)
    per_turn = [t["calls"] for t in m["usage_per_turn"] if t["calls"]]
    repeated = sum(n - 1 for n in Counter((c["tool"], json.dumps(c["arguments"], sort_keys=True)) for c in calls).values())
    lists = {}
    for c in element_usage(load(trace)):
        for path, v in c["lists"].items():
            lists.setdefault(f"{c['tool']} {path}", []).append(v)
    r = {
        "experiment": m["experiment"], "case": case, "arm": m["arm"], "run": m["run"], "sequence": m["sequence"],
        "n": m["n"], "dry_run": m["dry_run"], "flags": m["flags"],
        "gates_passed": bool(gates["json"] and gates["structure"] and gates["facts_ok"] == gates["facts"]),
        "facts": [gates["facts_ok"], gates["facts"]],
        "coverage": coverage(trace, case),
        "turns": m["turns"], "calls": m["calls"], "expand_calls": m["expand_calls"],
        "calls_per_turn": {"median": median(per_turn) if per_turn else 0, "max": max(per_turn, default=0)},
        "repeated_calls": repeated, "error_calls": sum(c["output"].startswith("Error") for c in calls),
        "billed": m["usage"], "cost_usd": m["cost_usd"], "tokens": m["tokens"], "exposure": m["exposure"],
        "dataforseo_calls": m["dataforseo_calls"], "domain_rejections": m["domain_rejections"],
        "skill": m["skill"], "tools_trimmed": m["tools_trimmed"],
        # Descriptive: elements received and reused, per list (median over the calls of the run).
        "lists": {k: {"calls": len(v), "received": median(x["received"] for x in v),
                      "reused": median(len(x["reused"]) for x in v),
                      "reused_from_top": sum(x["reused"] == list(range(len(x["reused"]))) for x in v if x["reused"])}
                  for k, v in sorted(lists.items())},
    }
    if m["experiment"] == "E2":
        a = m["adaptive"]
        sent = sum(m["tokens"]["outputs_sent"].values()) + m["tokens"]["expand_replies"]
        r["e2"] = {"requirement": requirement(trace, case), "reached": a["reached"], "kept_before": a["kept_before"],
                   "kept_after": a["kept_after"], "evidence": a["evidence"], "promotions": a["promotions"],
                   "switched_off": a["switched_off"],
                   # Tool-output tokens the model received, `expand` replies included, against the originals.
                   "saving": round(1 - sent / m["tokens"]["outputs_original"], 4) if m["tokens"]["outputs_original"] else None}
    return r


def spread(values) -> dict | None:
    values = [v for v in values if v is not None]
    return {"median": median(values), "min": min(values), "max": max(values)} if values else None


def e1(rows: list[dict]) -> dict:
    out = {}
    for case in sorted({r["case"] for r in rows}):
        for arm in ("R", "S", "T", "ST"):
            rs = [r for r in rows if (r["case"], r["arm"]) == (case, arm)]
            if not rs:
                continue
            out.setdefault(case, {})[arm] = {
                "runs": len(rs), "flagged": sum(bool(r["flags"]) for r in rs),
                "gates_passed": sum(r["gates_passed"] for r in rs),
                "coverage": spread(r["coverage"]["share"] for r in rs),
                "turns": spread(r["turns"] for r in rs), "calls": spread(r["calls"] for r in rs),
                "calls_per_turn_max": spread(r["calls_per_turn"]["max"] for r in rs),
                "repeated_calls": spread(r["repeated_calls"] for r in rs),
                "error_calls": spread(r["error_calls"] for r in rs),
                "expand_calls": spread(r["expand_calls"] for r in rs),
                "billed_input": spread(r["billed"]["input"] for r in rs),
                "billed_cached": spread(r["billed"]["cached"] for r in rs),
                "billed_output": spread(r["billed"]["output"] for r in rs),
                "cost_usd": spread(r["cost_usd"] for r in rs),
                "tokens_tool_definitions": spread(r["tokens"]["tool_definitions"] for r in rs),
                "tokens_instructions_added": spread(r["tokens"]["instructions_added"] for r in rs),
                "tokens_outputs_original": spread(r["tokens"]["outputs_original"] for r in rs),
                "tokens_outputs_sent": spread(sum(r["tokens"]["outputs_sent"].values()) for r in rs),
                "tokens_expand_replies": spread(r["tokens"]["expand_replies"] for r in rs),
                "tokens_final_answer": spread(r["tokens"]["final_answer"] for r in rs),
                "dataforseo_calls": spread(sum(r["dataforseo_calls"].values()) for r in rs),
            }
    return out


def e2(rows: list[dict]) -> list[dict]:
    """One entry per sequence: what happened to the needed fields along it."""
    out = []
    for key in sorted({(r["case"], r["arm"], r["sequence"]) for r in rows}):
        rs = sorted((r for r in rows if (r["case"], r["arm"], r["sequence"]) == key), key=lambda r: r["n"])
        fields = [f for fs in NEEDS[key[0]].values() for f in fs]
        kept_from = {}
        for f in fields:
            # The first run from which the field reaches the model in every filtered result, with no expand.
            ok = [r["e2"]["kept_before"][f] and "expand" not in r["e2"]["reached"][f] for r in rs]
            kept_from[f] = next((r["n"] for i, r in enumerate(rs) if all(ok[i:])), None)
        out.append({
            "case": key[0], "arm": key[1], "sequence": key[2], "runs": len(rs),
            "kept_from_run": kept_from,
            "reached": {f: [r["e2"]["reached"][f] for r in rs] for f in fields},
            "expand_calls": [r["expand_calls"] for r in rs],
            "requirement_met": [r["e2"]["requirement"]["met"] for r in rs],
            "gates_passed": [r["gates_passed"] for r in rs],
            "promotions": rs[-1]["e2"]["promotions"], "switched_off": rs[-1]["e2"]["switched_off"],
            "saving_per_run": [r["e2"]["saving"] for r in rs], "exposure": [r["exposure"] for r in rs],
            "cost_usd": round(sum(r["cost_usd"] for r in rs), 4),
            "dataforseo_calls": sum(sum(r["dataforseo_calls"].values()) for r in rs),
        })
    return out


def analyse(where: Where) -> dict:
    rows = [row(p) for p in sorted(where.traces.glob("*/E[12]-*.meta.json"))]
    per_tool = Counter()
    for r in rows:
        per_tool.update(r["dataforseo_calls"])
    return {"protocol": "protocol-v04.md", "tokens": "tiktoken o200k_base; billed tokens from the API usage",
            "dry_run": any(r["dry_run"] for r in rows), "runs": len(rows),
            "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
            "dataforseo": {"runs": sum(bool(r["dataforseo_calls"]) for r in rows), "calls": sum(per_tool.values()),
                           "calls_per_tool": dict(sorted(per_tool.items()))},
            "e1": e1([r for r in rows if r["experiment"] == "E1"]),
            "e2": e2([r for r in rows if r["experiment"] == "E2"]),
            "per_run": rows}


def _s(x, fmt="{:g}") -> str:
    return "—" if x is None else f"{fmt.format(x['median'])} [{fmt.format(x['min'])}–{fmt.format(x['max'])}]"


def tables(res: dict) -> str:
    lines = [f"{res['runs']} runs · {res['cost_usd']:.2f} USD · DataForSEO: {res['dataforseo']['calls']} calls in "
             f"{res['dataforseo']['runs']} runs" + (" · DRY RUN (stub in place of the model)" if res["dry_run"] else "")]
    lines.append(f"\nE1   {'case':<5}{'arm':<4}{'gates':>6}{'coverage':>18}{'turns':>12}{'calls':>12}{'calls/turn max':>16}"
                 f"{'billed input':>26}{'USD':>20}")
    for case, arms in res["e1"].items():
        for arm, a in arms.items():
            lines.append(f"     {case:<5}{arm:<4}{a['gates_passed']:>3}/{a['runs']:<2}{_s(a['coverage'], '{:.0%}'):>18}"
                         f"{_s(a['turns']):>12}{_s(a['calls']):>12}{_s(a['calls_per_turn_max']):>16}"
                         f"{_s(a['billed_input'], '{:,.0f}'):>26}{_s(a['cost_usd'], '{:.3f}'):>20}")
    lines.append(f"\nE2   {'case':<5}{'arm':<20}{'seq':>4}  kept from run (per field) · expand per run · requirement met · "
                 f"saving per run · switched off")
    for s in res["e2"]:
        kept = ", ".join(f"{f.rsplit('.', 1)[-1]}: {n or 'never'}" for f, n in s["kept_from_run"].items())
        lines.append(f"     {s['case']:<5}{s['arm']:<20}{s['sequence']:>4}  {kept} · {s['expand_calls']} · "
                     f"{['yes' if m else 'no' for m in s['requirement_met']]} · "
                     f"{[f'{v:.0%}' if v is not None else '—' for v in s['saving_per_run']]} · {s['switched_off'] or 'no'}")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="the directory of another campaign, e.g. a dry run")
    a = ap.parse_args()
    where = Where(Path(a.root)) if a.root else Where(ROOT)
    res = analyse(where)
    if a.root:
        out = [where.root / "results-v04.json"]
        out[0].write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    else:
        out = [ROOT / "results" / "v04" / "e1.json", ROOT / "results" / "v04" / "e2.json"]
        head = {k: res[k] for k in ("protocol", "tokens", "dry_run")}
        out[0].write_text(json.dumps(head | {"e1": res["e1"], "per_run": [r for r in res["per_run"] if r["experiment"] == "E1"]},
                                     indent=2, ensure_ascii=False) + "\n")
        out[1].write_text(json.dumps(head | {"e2": res["e2"], "per_run": [r for r in res["per_run"] if r["experiment"] == "E2"]},
                                     indent=2, ensure_ascii=False) + "\n")
    print(tables(res))
    print("\n" + ", ".join(str(p) for p in out))
