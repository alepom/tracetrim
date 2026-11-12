#!/usr/bin/env python3
"""The tables of protocol-v04.md, from the traces and meta files of its runs.

  python -m experiments.analyze_v04                # this checkout's runs -> results/v04/e1.json, e2.json
  python -m experiments.analyze_v04 --root DIR     # another campaign (a dry run): DIR/results-v04.json
  python -m experiments.analyze_v04 --e3 [--root DIR]   # E3 of protocol-v04-e3.md -> results/v04/e3.json

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
from .coverage import PRESCRIBED, calls_of, coverage, requirement
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
        "share_of_turns_with_several_calls": round(sum(n > 1 for n in per_turn) / len(per_turn), 4) if per_turn else None,
        "pilot": m.get("pilot", False), "replay": m.get("replay"), "model": m["model"],
        "repeated_calls": repeated, "error_calls": sum(c["output"].startswith("Error") for c in calls),
        "calls_per_tool": dict(sorted(Counter(c["tool"] for c in calls).items())),
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
                "several_calls_share_per_run": spread(r["share_of_turns_with_several_calls"] for r in rs),
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


REWORDED = "data[].collection_method"        # the field E2's variant of C4 expects to be used and reworded


def _reused(r: dict, field: str) -> int:
    """Runs of the sequence so far in which the field was written after an expand gave it back."""
    for fields in r["e2"]["evidence"].values():
        if fields.get(field):
            return fields[field]["reused"]
    return 0


def hypotheses_e2(rows: list[dict]) -> dict:
    """H5, H6 and H7 of the protocol on the runs of E2, with the counts each rests on.
    H6 and H7 depend on what the model does: where the premise did not occur they are
    reported as not testable, as the protocol says."""
    seqs = {}
    for r in rows:
        seqs.setdefault((r["case"], r["arm"], r["sequence"]), []).append(r)
    h5, h6_premise, h6 = [], [], []
    for (case, arm, seq), rs in sorted(seqs.items()):
        rs.sort(key=lambda r: r["n"])
        for f in [f for fs in NEEDS[case].values() for f in fs]:
            before = 0
            for i, r in enumerate(rs):
                now = _reused(r, f)
                expanded = "expand" in r["e2"]["reached"][f]
                if expanded and now > before:           # recovered and written in this run
                    later = rs[i + 1:]
                    h5.append({"case": case, "arm": arm, "sequence": seq, "field": f, "written_in_run": r["n"],
                               "kept_in_every_later_run": all(x["e2"]["kept_before"][f] for x in later),
                               "expanded_again": any("expand" in x["e2"]["reached"][f] for x in later)})
                elif expanded and f == REWORDED:        # recovered, and its value not seen written: the premise of H6
                    h6_premise.append({"case": case, "arm": arm, "sequence": seq, "run": r["n"]})
                before = now
        if case == "C4":
            f = REWORDED
            kept = next((r["n"] for i, r in enumerate(rs) if all(x["e2"]["kept_before"][f] for x in rs[i:])), None)
            h6.append({"arm": arm, "sequence": seq, "promotion": "promotion" in arm,
                       "reworded_in_runs": [p["run"] for p in h6_premise if (p["arm"], p["sequence"], p["case"]) == (arm, seq, case)],
                       "kept_from_run": kept, "tool_switched_off": bool(rs[-1]["e2"]["switched_off"]),
                       "expand_in_every_run": all("expand" in r["e2"]["reached"][f] for r in rs),
                       "saving_last_run": rs[-1]["e2"]["saving"]})
    # H6 can be tested only where the field stayed reworded, with promotion off and with promotion on.
    off = [s for s in h6 if not s["promotion"] and s["reworded_in_runs"]]
    on = [s for s in h6 if s["promotion"] and s["reworded_in_runs"]]
    if off and on:
        h6_holds = (all(s["kept_from_run"] is not None and s["kept_from_run"] <= 3 and not s["tool_switched_off"] for s in on)
                    and all(s["kept_from_run"] is None and (s["tool_switched_off"] or s["expand_in_every_run"]) for s in off))
    else:
        h6_holds = None
    # H7: the runs that started without a needed field in the filtered results and made no expand.
    quiet = [r for r in rows if not all(r["e2"]["kept_before"].values()) and r["expand_calls"] == 0]
    group = lambda sampling: [r for r in quiet if ("sampling" in r["arm"]) is sampling]       # noqa: E731
    share = lambda rs: round(sum(r["e2"]["requirement"]["met"] for r in rs) / len(rs), 4) if rs else None    # noqa: E731
    with_s, without = group(True), group(False)
    return {
        "H5": {"recoveries_written": len(h5), "kept_in_every_later_run": sum(x["kept_in_every_later_run"] for x in h5),
               "holds": all(x["kept_in_every_later_run"] for x in h5) if h5 else None, "cases": h5},
        "H6": {"sequences_where_the_field_was_reworded": {"promotion_on": len(on), "promotion_off": len(off)},
               "holds": h6_holds, "testable": h6_holds is not None, "sequences": h6},
        "H7": {"runs_without_expand_while_a_field_was_removed": {"sampling_arms": len(with_s), "other_arms": len(without)},
               "requirement_met": {"sampling_arms": share(with_s), "other_arms": share(without)},
               "testable": bool(with_s and without),
               "holds": (share(with_s) > share(without)) if with_s and without else None,
               "runs": [{"arm": r["arm"], "sequence": r["sequence"], "n": r["n"], "met": r["e2"]["requirement"]["met"],
                         "calls_per_tool": r["calls_per_tool"], "exposure": r["exposure"]} for r in quiet]},
    }


REPLAYED = ("C1", "C2")        # E3: cases answered from recorded outputs, where content is not evaluated


def hypotheses_e3(table: dict, rows: list[dict]) -> dict:
    """H8 to H11 of protocol-v04-e3.md, per case, as stated there."""
    h8, h9, h10 = {}, {}, {}
    for case, arms in table.items():
        r, s = arms.get("R"), arms.get("S")
        if not r or not s:
            continue
        h8[case] = {"turns_median_S": s["turns"]["median"], "turns_min_R": r["turns"]["min"],
                    "holds": s["turns"]["median"] < r["turns"]["min"]}
        h9[case] = {"share_median_S": s["several_calls_share_per_run"]["median"],
                    "share_max_R": r["several_calls_share_per_run"]["max"],
                    "holds": s["several_calls_share_per_run"]["median"] > r["several_calls_share_per_run"]["max"]}
        if case not in REPLAYED:
            h10[case] = {"gates": [s["gates_passed"], r["gates_passed"]], "coverage_median_S": s["coverage"]["median"],
                         "coverage_min_R": r["coverage"]["min"],
                         "holds": s["gates_passed"] >= r["gates_passed"] and s["coverage"]["median"] >= r["coverage"]["min"]}
    # H11: the argument values the prompts prescribe, in the runs with the strategy.
    checked = [(r["case"], r["run"], name, r["coverage"]["checks"][name])
               for r in rows if r["arm"] == "S" for name in PRESCRIBED.get(r["case"], ())]
    whole = lambda h: all(v["holds"] for v in h.values()) if h else None      # noqa: E731
    return {"H8": {"per_case": h8, "holds": whole(h8), "cases_where_it_holds": sum(v["holds"] for v in h8.values())},
            "H9": {"per_case": h9, "holds": whole(h9), "cases_where_it_holds": sum(v["holds"] for v in h9.values())},
            "H10": {"per_case": h10, "holds": whole(h10)},
            "H11": {"checks": len(checked), "passed": sum(ok for *_, ok in checked),
                    "failed": [f"{c} {run}: {name}" for c, run, name, ok in checked if not ok],
                    "holds": all(ok for *_, ok in checked) if checked else None}}


def analyse_e3(where: Where) -> dict:
    """E3: the strategy on another model. On the cases answered from recorded outputs,
    gates and coverage of the answer are not evaluated: only how the calls are organised."""
    rows = [row(p) for p in sorted(where.traces.glob("*/E3-*.meta.json"))]
    measured = [r for r in rows if not r["pilot"]]
    table = e1(measured)
    for case in REPLAYED:
        for arm in table.get(case, {}).values():
            arm["gates_passed"] = arm["coverage"] = None          # not evaluated: the outputs are not the provider's
    replay = {}
    for r in rows:
        if r["replay"]:
            a = replay.setdefault(r["case"], {"exact": 0, "nearest": 0, "none": 0, "per_run_exact_share": []})
            for k in ("exact", "nearest", "none"):
                a[k] += r["replay"][k]
            n = sum(r["replay"].values())
            a["per_run_exact_share"].append(round(r["replay"]["exact"] / n, 4) if n else None)
    pilot = [r for r in rows if r["pilot"]]
    pt = [n for r in pilot for n in r["calls_in_turns"]]
    return {"protocol": "protocol-v04-e3.md", "tokens": "billed tokens from the model's API usage; other counts tiktoken o200k_base",
            "dry_run": any(r["dry_run"] for r in rows), "models": sorted({r["model"] for r in rows}),
            "runs": len(measured), "pilot_runs": len(pilot), "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
            "provider_calls": sum(sum(r["dataforseo_calls"].values()) for r in rows),
            "pilot": {"turns_with_calls": len(pt), "calls_per_turn_median": median(pt) if pt else None,
                      "share_of_turns_with_several_calls": round(sum(n > 1 for n in pt) / len(pt), 4) if pt else None},
            "replay": replay, "e3": table, "e3_hypotheses": hypotheses_e3(table, measured),
            "did_not_do_the_task": [f"{r['case']} {r['run']}" for r in measured
                                    if r["no_tool_call"] or (r["answer_is_not_the_json_asked"] and r["case"] not in REPLAYED)],
            "per_run": rows}


def tables_e3(res: dict) -> str:
    lines = [f"E3 · {', '.join(res['models'])} · {res['runs']} runs and {res['pilot_runs']} pilot runs · {res['cost_usd']:.2f} USD · "
             f"provider calls: {res['provider_calls']}" + (" · DRY RUN (stub in place of the model)" if res["dry_run"] else "")]
    p = res["pilot"]
    if p["turns_with_calls"]:
        lines.append(f"pilot: {p['share_of_turns_with_several_calls']:.0%} of {p['turns_with_calls']} turns with calls made several "
                     f"(median {p['calls_per_turn_median']:g} calls per turn)")
    lines.append(f"\n     {'case':<5}{'arm':<4}{'runs':>5}{'gates':>7}{'coverage':>18}{'turns':>12}{'calls':>12}{'several-call turns':>22}"
                 f"{'billed input':>26}{'USD':>20}")
    for case, arms in res["e3"].items():
        for arm, a in arms.items():
            gates = "—" if a["gates_passed"] is None else f"{a['gates_passed']}/{a['runs']}"
            lines.append(f"     {case:<5}{arm:<4}{a['runs']:>5}{gates:>7}{_s(a['coverage'], '{:.0%}'):>18}{_s(a['turns']):>12}"
                         f"{_s(a['calls']):>12}{_s(a['several_calls_share_per_run'], '{:.0%}'):>22}"
                         f"{_s(a['billed_input'], '{:,.0f}'):>26}{_s(a['cost_usd'], '{:.3f}'):>20}")
    h = res["e3_hypotheses"]
    say = lambda v: "no case" if v is None else "holds" if v else "FAILS"      # noqa: E731
    for name, text in (("H8", "fewer turns with the strategy"), ("H9", "more turns with several calls")):
        lines.append(f"\n{name} ({text}): {say(h[name]['holds'])} · holds in {h[name]['cases_where_it_holds']} of "
                     f"{len(h[name]['per_case'])} cases · " + ", ".join(f"{c}: {'yes' if v['holds'] else 'no'}"
                                                                        for c, v in h[name]["per_case"].items()))
    lines.append(f"H10 (completeness and gates no worse, simulated cases): {say(h['H10']['holds'])} · "
                 + ", ".join(f"{c}: {'yes' if v['holds'] else 'no'}" for c, v in h["H10"]["per_case"].items()))
    lines.append(f"H11 (prescribed argument values respected with the strategy): {say(h['H11']['holds'])} · "
                 f"{h['H11']['passed']} of {h['H11']['checks']} checks")
    for case, a in res["replay"].items():
        shares = [x for x in a["per_run_exact_share"] if x is not None]
        lines.append(f"replay {case}: exact {a['exact']}, nearest {a['nearest']}, none {a['none']} · exact share per run "
                     f"median {median(shares):.0%} [{min(shares):.0%}–{max(shares):.0%}]" if shares else f"replay {case}: no call")
    return "\n".join(lines)


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
            "e2_hypotheses": hypotheses_e2([r for r in rows if r["experiment"] == "E2"]),
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
    h = res["e2_hypotheses"]
    if res["e2"]:
        say = lambda v: "not testable in these runs" if v is None else "holds" if v else "FAILS"     # noqa: E731
        lines.append(f"\nH5 (written fields come back by themselves): {say(h['H5']['holds'])} · "
                     f"{h['H5']['kept_in_every_later_run']} of {h['H5']['recoveries_written']} recoveries written were kept in every later run")
        lines.append(f"H6 (a reworded field needs promotion): {say(h['H6']['holds'])} · sequences where the field was "
                     f"recovered and not written: {h['H6']['sequences_where_the_field_was_reworded']}")
        lines.append(f"H7 (sampling reaches what is not asked for): {say(h['H7']['holds'])} · runs with a field removed and no "
                     f"expand: {h['H7']['runs_without_expand_while_a_field_was_removed']}, requirement met {h['H7']['requirement_met']}")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="the directory of another campaign, e.g. a dry run")
    ap.add_argument("--e3", action="store_true", help="E3 of protocol-v04-e3.md")
    a = ap.parse_args()
    where = Where(Path(a.root)) if a.root else Where(ROOT)
    if a.e3:
        res = analyse_e3(where)
        path = where.root / "results-e3.json" if a.root else ROOT / "results" / "v04" / "e3.json"
        path.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
        print(tables_e3(res))
        print(f"\n{path}")
        raise SystemExit(0)
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
        out[1].write_text(json.dumps(head | {"e2": res["e2"], "e2_hypotheses": res["e2_hypotheses"], "per_run": [r for r in res["per_run"] if r["experiment"] == "E2"]},
                                     indent=2, ensure_ascii=False) + "\n")
    print(tables(res))
    print("\n" + ", ".join(str(p) for p in out))
