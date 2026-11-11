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

from .checks import check, final_json
from .coverage import calls_of, coverage, requirement
from .paths import ROOT
from .budget import V04
from .v04 import NEEDS, Where


# DataForSEO bills by API. The tools of the study, by the API they call.
API_OF = (("kw_data_", "keywords_data"), ("dataforseo_labs_", "labs"), ("serp_", "serp"), ("on_page_", "onpage"))


def api_of(tool: str) -> str:
    return next(api for prefix, api in API_OF if tool.startswith(prefix))


def dataforseo_rates(where: Where) -> dict | None:
    """USD per call, per API: what the author read on the provider's panel for the
    runs before the checkpoint, divided by the calls those runs made. An estimate:
    the price of a call also depends on its arguments. None when the panel reading
    is not there (a dry run, a checkout without `out/`)."""
    if not where.checkpoint.exists():
        return None
    spent = json.loads(where.checkpoint.read_text()).get("by_api_usd")
    if not spent:
        return None
    metas = sorted((json.loads(p.read_text()) for p in where.traces.glob("*/E[12]-*.meta.json")),
                   key=lambda m: m["started"])
    before = [m for m in metas if m["dataforseo_calls"]][:V04["dataforseo_checkpoint_runs"]]
    calls = Counter()
    for m in before:
        for tool, n in m["dataforseo_calls"].items():
            calls[api_of(tool)] += n
    return {api: {"usd": usd, "calls": calls[api], "usd_per_call": round(usd / calls[api], 5)}
            for api, usd in spent.items() if calls[api]}


def row(meta_path: Path) -> dict:
    """Everything the protocol measures on one run."""
    m = json.loads(meta_path.read_text())
    trace = meta_path.with_name(meta_path.name.replace(".meta.json", ".jsonl"))
    case = m["case"]
    gates = check(trace, case)
    calls = calls_of(trace)
    per_turn = [t["calls"] for t in m["usage_per_turn"] if t["calls"]]
    repeated = sum(n - 1 for n in Counter((c["tool"], json.dumps(c["arguments"], sort_keys=True)) for c in calls).values())
    messages = [e.text for e in load(trace).events if e.kind == "message"]
    lists = {}
    for c in element_usage(load(trace)):
        for path, v in c["lists"].items():
            lists.setdefault(f"{c['tool']} {path}", []).append(v)
    r = {
        "experiment": m["experiment"], "case": case, "arm": m["arm"], "run": m["run"], "sequence": m["sequence"],
        "n": m["n"], "dry_run": m["dry_run"], "flags": m["flags"],
        "gates_passed": bool(gates["json"] and gates["structure"] and gates["facts_ok"] == gates["facts"]),
        # The run did not do the task: it made no tool call, or its last message is not the JSON
        # the prompt asks for (a refusal, a question back to the user). A rule on the form of the
        # answer: what the message says is in the trace.
        "no_tool_call": m["calls"] == 0,
        "answer_is_not_the_json_asked": final_json(load(trace)) is None,
        "messages": len(messages), "final_answer_chars": len(messages[-1]) if messages else 0,
        "facts": [gates["facts_ok"], gates["facts"]],
        "coverage": coverage(trace, case),
        "turns": m["turns"], "calls": m["calls"], "expand_calls": m["expand_calls"],
        "calls_per_turn": {"median": median(per_turn) if per_turn else 0, "max": max(per_turn, default=0)},
        "calls_in_turns": per_turn,
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
                "did_not_do_the_task": sum(r["no_tool_call"] or r["answer_is_not_the_json_asked"] for r in rs),
                "dataforseo_usd_estimated": spread(r.get("dataforseo_usd_estimated") for r in rs),
                "coverage": spread(r["coverage"]["share"] for r in rs),
                "turns": spread(r["turns"] for r in rs), "calls": spread(r["calls"] for r in rs),
                "calls_per_turn_max": spread(r["calls_per_turn"]["max"] for r in rs),
                # Descriptive: how much the model already issues calls together. Over the turns of
                # the arm's runs that made at least one tool call (`expand` included).
                "turns_with_calls": sum(len(r["calls_in_turns"]) for r in rs),
                "share_of_turns_with_several_calls": round(
                    sum(n > 1 for r in rs for n in r["calls_in_turns"]) / max(1, sum(len(r["calls_in_turns"]) for r in rs)), 4),
                "calls_per_turn_median": median([n for r in rs for n in r["calls_in_turns"]] or [0]),
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


def hypotheses_e1(table: dict) -> dict:
    """H1, H2 and H4 of the protocol, as stated there, on the table of E1. (H3 was removed before freezing.)"""
    def arm(case, a):
        return table.get(case, {}).get(a)

    def holds_h1(case, treated, control):
        t, c = arm(case, treated), arm(case, control)
        if not t or not c:
            return None
        return {"gates": [t["gates_passed"], c["gates_passed"]],
                "coverage_median": t["coverage"]["median"], "control_coverage_range": [c["coverage"]["min"], c["coverage"]["max"]],
                "holds": t["gates_passed"] >= c["gates_passed"] and t["coverage"]["median"] >= c["coverage"]["min"]}

    h1 = {f"{case} {t} vs {c}": holds_h1(case, t, c) for case in table for t, c in (("S", "R"), ("ST", "T"))
          if arm(case, t) and arm(case, c)}
    h2 = None
    if arm("C4", "S") and arm("C4", "R"):
        h2 = {"turns_median_S": arm("C4", "S")["turns"]["median"], "turns_min_R": arm("C4", "R")["turns"]["min"],
              "holds": arm("C4", "S")["turns"]["median"] < arm("C4", "R")["turns"]["min"]}
    h4 = {case: {"billed_input_median_ST": arm(case, "ST")["billed_input"]["median"],
                 "billed_input_max_T": arm(case, "T")["billed_input"]["max"],
                 "holds": arm(case, "ST")["billed_input"]["median"] <= arm(case, "T")["billed_input"]["max"]}
          for case in ("C1", "C4") if arm(case, "ST") and arm(case, "T")}
    return {"H1": {"per_comparison": h1, "holds": all(v["holds"] for v in h1.values()) if h1 else None},
            "H2": h2, "H4": {"per_case": h4, "holds": all(v["holds"] for v in h4.values()) if h4 else None}}


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
    rates = dataforseo_rates(where)
    per_tool = Counter()
    for r in rows:
        per_tool.update(r["dataforseo_calls"])
        if rates and r["dataforseo_calls"]:
            r["dataforseo_usd_estimated"] = round(sum(n * rates[api_of(t)]["usd_per_call"]
                                                      for t, n in r["dataforseo_calls"].items()), 4)
    table = e1([r for r in rows if r["experiment"] == "E1"])
    return {"protocol": "protocol-v04.md", "tokens": "tiktoken o200k_base; billed tokens from the API usage",
            "dry_run": any(r["dry_run"] for r in rows), "runs": len(rows),
            "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
            "dataforseo": {"runs": sum(bool(r["dataforseo_calls"]) for r in rows), "calls": sum(per_tool.values()),
                           "calls_per_tool": dict(sorted(per_tool.items())),
                           "calls_per_api": dict(sorted(Counter({api: sum(n for t, n in per_tool.items() if api_of(t) == api)
                                                                 for api in {api_of(t) for t in per_tool}}).items())),
                           "rates": rates,
                           "usd_estimated": round(sum(r.get("dataforseo_usd_estimated", 0) for r in rows), 4) if rates else None},
            "e1": table, "e1_hypotheses": hypotheses_e1(table),
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
    h = res["e1_hypotheses"]
    if h["H1"]["holds"] is not None:
        lines.append(f"\nH1 (completeness holds): {'holds' if h['H1']['holds'] else 'FAILS'} · "
                     + ", ".join(f"{k}: {'ok' if v['holds'] else 'no'}" for k, v in h["H1"]["per_comparison"].items()))
        if h["H2"]:
            lines.append(f"H2 (fewer turns in C4): {'holds' if h['H2']['holds'] else 'FAILS'} · S median "
                         f"{h['H2']['turns_median_S']:g} vs R minimum {h['H2']['turns_min_R']:g}")
        lines.append(f"H4 (the strategy does not undo the saving): {'holds' if h['H4']['holds'] else 'FAILS'} · "
                     + ", ".join(f"{c}: ST median {v['billed_input_median_ST']:,.0f} vs T maximum {v['billed_input_max_T']:,.0f}"
                                 for c, v in h["H4"]["per_case"].items()))
    if res["e1"]:
        lines.append("\ncalls issued together (turns with at least one call):  case arm  turns  share with several  median per turn")
        for case, arms in res["e1"].items():
            for arm, a in arms.items():
                lines.append(f"     {case:<5}{arm:<4}{a['turns_with_calls']:>6}{a['share_of_turns_with_several_calls']:>12.0%}"
                             f"{a['calls_per_turn_median']:>10g}")
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
        head["dataforseo"] = res["dataforseo"]
        out[0].write_text(json.dumps(head | {"e1": res["e1"], "e1_hypotheses": res["e1_hypotheses"], "per_run": [r for r in res["per_run"] if r["experiment"] == "E1"]},
                                     indent=2, ensure_ascii=False) + "\n")
        out[1].write_text(json.dumps(head | {"e2": res["e2"], "per_run": [r for r in res["per_run"] if r["experiment"] == "E2"]},
                                     indent=2, ensure_ascii=False) + "\n")
    print(tables(res))
    print("\n" + ", ".join(str(p) for p in out))
