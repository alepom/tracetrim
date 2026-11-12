#!/usr/bin/env python3
"""What the tool definitions weigh in the recorded runs, and what deferring them could save (0.6, offline).

  python -m experiments.definitions list        # tools/list on the DataForSEO MCP server (free): out/v06/
  python -m experiments.definitions             # the measure: results/v06/definitions.json and the tables

No model call, no spend. Inputs, all read only:

  runs       <traces>/<case>/<run>.meta.json, .tools.json, .jsonl  of the v0.4 measures (E1, E2, E3).
             $TRACETRIM_V04_TRACES points to them (default: this checkout's traces/).
  ledgers    <out>/ledger.jsonl and ledger-e3.jsonl: every run's billed usage is checked against its
             ledger line before anything is computed. $TRACETRIM_V04_OUT (default: this checkout's out/).
  server     out/v06/dfs_tools_list.json, written by `list`: every tool of the DataForSEO server.

What is counted, and how:

  definitions   tiktoken o200k_base on the JSON of the schemas as the harness sent them to the model
                (function-calling format). It is a COUNT of ours, not what the provider billed: the
                provider renders the definitions its own way. `billed_static_minus_counted` says how far
                the two are, on each run's first turn.
  cache         per turn, from the billed usage. The definitions sit in the static head of the prompt,
                so on a turn they are billed as cached when the cached tokens cover that head (the
                first turn's whole input), as not cached when the turn has no cached token, and are
                counted apart ("unclear") in between; unclear turns are costed as not cached.
  cost          only as shares and ratios, at the providers' list prices (experiments/prices*.json).
                No amount is written anywhere.

The scenario "every tool of the server enabled" is COUNTERFACTUAL: no run was made with 79 tools. It
takes each recorded run of C1's arm R as it happened (same turns, same calls, same cache pattern) and
replaces the weight of its definitions. Whether a model facing 79 tools would work the same way is
exactly what the protocol of 0.6 has to measure.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from pathlib import Path

from tracetrim.measure import tokenizer

from .paths import ROOT
from .tools import CASE_TOOLS

TRACES = Path(os.environ.get("TRACETRIM_V04_TRACES") or ROOT / "traces")
OUT = Path(os.environ.get("TRACETRIM_V04_OUT") or ROOT / "out")
SERVER_LIST = ROOT / "out" / "v06" / "dfs_tools_list.json"
RESULTS = ROOT / "results" / "v06" / "definitions.json"
PRICE_FILES = ("prices.json", "prices-e3.json")
CASES = ("C1", "C2", "C4", "C5")
# A turn's cached tokens "cover the static head" when they reach this share of the first turn's input.
HEAD_SHARE = 0.9

# DRAFT definitions of the two meta-tools (design of 0.6, not in the library yet): here only to count
# what they would add to every turn.
META_TOOLS = [
    {"type": "function", "name": "find_tools", "strict": False,
     "description": ("Search the tools of this server that are not listed here. Returns, for each match, "
                     "its name, description and argument schema. Use it when no listed tool does what "
                     "you need; then call the tool you found with call_tool."),
     "parameters": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string", "description": "Words that describe what the tool should do, or part of its name."},
         "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5,
                   "description": "How many tools to return."}}}},
    {"type": "function", "name": "call_tool", "strict": False,
     "description": ("Call a tool returned by find_tools. The arguments are checked against that tool's "
                     "schema, exactly as if it were listed."),
     "parameters": {"type": "object", "required": ["name", "arguments"], "properties": {
         "name": {"type": "string", "description": "The tool's name, as find_tools returned it."},
         "arguments": {"type": "object", "description": "The tool's arguments, following its schema."}}}},
]


def prices() -> dict:
    out = {}
    for name in PRICE_FILES:
        p = json.loads((ROOT / "experiments" / name).read_text())
        out[p["model"]] = p
    return out


def as_sent(tool: dict) -> dict:
    """A tool as the harness sends it to the model (experiments/tools.py), from a `.tools.json`
    record or from an MCP `tools/list` entry."""
    params = dict(tool.get("parameters") or tool.get("inputSchema") or {"type": "object", "properties": {}})
    params.pop("$schema", None)
    return {"type": "function", "name": tool["name"], "description": tool.get("description") or "",
            "parameters": params, "strict": False}


def count_definitions(tools: list[dict], count) -> int:
    return count(json.dumps([as_sent(t) for t in tools], ensure_ascii=False))


def turn_cache(turns: list[dict]) -> list[str]:
    """For every turn: are the definitions billed as cached? "cached" | "not_cached" | "unclear"."""
    head = turns[0]["input"]
    return ["not_cached" if not t["cached"] else "cached" if t["cached"] >= HEAD_SHARE * head else "unclear"
            for t in turns]


def weights(states: list[str], price: dict) -> float:
    """Sum over the turns of the price of one input token, in units of the full input price."""
    ratio = price["cached_input_per_mtok"] / price["input_per_mtok"]
    return sum(ratio if s == "cached" else 1.0 for s in states)


def cost_units(usage: dict, price: dict) -> float:
    """A run's cost in units of one uncached input token: enough for every share and ratio."""
    full = price["input_per_mtok"]
    return ((usage["input"] - usage["cached"]) + usage["cached"] * price["cached_input_per_mtok"] / full
            + usage["output"] * price["output_per_mtok"] / full)


def ledger_lines() -> dict:
    lines = {}
    for name in ("ledger.jsonl", "ledger-e3.jsonl"):
        p = OUT / name
        if not p.exists():
            sys.exit(f"{p}: ledger not found (set $TRACETRIM_V04_OUT)")
        for raw in p.read_text().splitlines():
            if raw.strip():
                e = json.loads(raw)
                if not e.get("aborted") and not e.get("dry_run"):
                    lines[(e["case"], e["run"])] = e
    return lines


def read_run(meta_path: Path, ledger: dict, count, price_of: dict) -> dict:
    m = json.loads(meta_path.read_text())
    stem = meta_path.name.removesuffix(".meta.json")
    tools = json.loads(meta_path.with_name(stem + ".tools.json").read_text())
    prompts = [json.loads(line) for line in meta_path.with_name(stem + ".jsonl").read_text().splitlines()[:2]]
    turns = m["usage_per_turn"]
    problems = []
    # Check the input before reporting a result: every one of these would give plausible wrong numbers.
    if any(p.get("kind") != "prompt" for p in prompts):
        problems.append("the trace does not start with the two prompts")
    if len(turns) != m["turns"]:
        problems.append(f"turns {m['turns']} but {len(turns)} usage records")
    if sum(t["input"] for t in turns) != m["usage"]["input"] or sum(t["cached"] for t in turns) != m["usage"]["cached"]:
        problems.append("per-turn usage does not add up to the run's usage")
    # The run's own count is the reference. The recount from the recorded schemas must agree: to the
    # token for the tools of the case, within 2 tokens when `expand` is offered (the harness wrote its
    # keys in another order, and the record does not keep the order).
    definitions = m["tokens"]["tool_definitions"]
    recount = count_definitions(tools, count)
    if abs(recount - definitions) > (2 if any(t["name"] == "expand" for t in tools) else 0):
        problems.append(f"definitions recounted {recount}, recorded {definitions}")
    line = ledger.get((m["case"], m["run"]))
    if line is None:
        problems.append("no ledger line")
    elif line["usage"] != m["usage"]:
        problems.append("usage differs from the ledger's")
    if m["model"] not in price_of:
        problems.append(f"no price list for {m['model']}")
    if problems:
        sys.exit(f"{meta_path}: " + "; ".join(problems))

    price = price_of[m["model"]]
    states = turn_cache(turns)
    n = len(turns)
    w = weights(states, price)
    units = cost_units(m["usage"], price)
    prompt_tokens = sum(count(p["text"]) for p in prompts)
    return {
        "case": m["case"], "run": m["run"], "experiment": m["experiment"], "arm": m["arm"], "model": m["model"],
        "tools_offered": len(tools), "tools_called": sorted(k for k in set(_called(meta_path, stem)) if k != "expand"),
        "flags": m.get("flags") or [],
        "definitions_per_turn": definitions, "turns": n, "definitions_per_run": definitions * n,
        "input": m["usage"]["input"], "cached_input": m["usage"]["cached"], "output": m["usage"]["output"],
        "cached_share_of_input": m["usage"]["cached"] / m["usage"]["input"],
        "definitions_share_of_input": definitions * n / m["usage"]["input"],
        "turns_cached": states.count("cached"), "turns_not_cached": states.count("not_cached"),
        "turns_unclear": states.count("unclear"), "first_turn_cached": states[0] == "cached" or turns[0]["cached"] > 0,
        "definitions_cached_share": states.count("cached") / n,
        "definitions_share_of_cost": definitions * w / units,
        "definitions_share_of_cost_if_never_cached": definitions * n / (units + definitions * (n - w)),
        "input_per_turn_median": statistics.median(t["input"] for t in turns),
        "input_per_turn_max": max(t["input"] for t in turns),
        "definitions_share_of_peak_context": definitions / max(t["input"] for t in turns),
        "first_turn_input": turns[0]["input"], "prompt_tokens": prompt_tokens,
        "billed_static_minus_counted": turns[0]["input"] - prompt_tokens - definitions,
        # What the provider billed for the definitions, at most: the first turn's input less the two
        # prompts as we count them (it still holds the provider's own framing of the messages).
        "definitions_billed_estimate": turns[0]["input"] - prompt_tokens,
        "billed_vs_counted": (turns[0]["input"] - prompt_tokens) / definitions,
        # kept for the scenario, not written to the results: nothing here is an amount
        "_states": states, "_units": units, "_weight": w, "_price": price, "_turn_inputs": [t["input"] for t in turns],
    }


def _called(meta_path: Path, stem: str) -> list[str]:
    out = []
    for line in meta_path.with_name(stem + ".jsonl").read_text().splitlines():
        e = json.loads(line)
        if e.get("kind") == "call":
            out.append(e["tool"])
    return out


def spread(values) -> dict:
    v = list(values)
    return {"median": statistics.median(v), "min": min(v), "max": max(v)}


SUMMARY = ("definitions_per_turn", "turns", "definitions_per_run", "input", "definitions_share_of_input",
           "cached_share_of_input", "definitions_cached_share", "definitions_share_of_cost",
           "definitions_share_of_cost_if_never_cached", "input_per_turn_median", "input_per_turn_max",
           "definitions_share_of_peak_context", "billed_static_minus_counted", "definitions_billed_estimate",
           "billed_vs_counted")


def groups(runs: list[dict]) -> list[dict]:
    out = []
    keys = sorted({(r["case"], r["model"], r["experiment"], r["arm"]) for r in runs})
    for case, model, experiment, arm in keys:
        rs = [r for r in runs if (r["case"], r["model"], r["experiment"], r["arm"]) == (case, model, experiment, arm)]
        out.append({"case": case, "model": model, "experiment": experiment, "arm": arm, "runs": len(rs),
                    "tools_offered": spread(r["tools_offered"] for r in rs),
                    "first_turn_cached_runs": sum(r["first_turn_cached"] for r in rs),
                    "turns_unclear": sum(r["turns_unclear"] for r in rs),
                    **{k: spread(r[k] for r in rs) for k in SUMMARY}})
    return out


def scenario(runs: list[dict], server: list[dict], count) -> dict:
    """COUNTERFACTUAL. C1, arm R of E1, as recorded, with other definitions in place of its 12 tools:
    R79 every tool of the server listed; D the tools the agent used listed in full, the others behind
    the two meta-tools. Same turns, calls and cache pattern as recorded; no find_tools call in D (an
    upper bound of D's gain: every search adds a call, its reply and possibly a turn)."""
    base = [r for r in runs if (r["case"], r["experiment"], r["arm"]) == ("C1", "E1", "R")]
    if not base:
        sys.exit("no run of C1, E1, arm R: the scenario has nothing to stand on")
    by_name = {t["name"]: t for t in server}
    names = [t["name"] for t in server]
    used_any = sorted({n for r in base for n in r["tools_called"]})
    used_all = sorted(set.intersection(*(set(r["tools_called"]) for r in base)))
    unknown = [n for n in used_any if n not in by_name]
    if unknown:
        sys.exit(f"tools called in the runs but not on the server: {unknown}")
    d_all = count_definitions(server, count)
    d_case = count_definitions([by_name[n] for n in CASE_TOOLS["C1"]], count)
    d_used = count_definitions([by_name[n] for n in names if n in used_any], count)
    d_meta = count(json.dumps(META_TOOLS, ensure_ascii=False))
    d_deferred = count(json.dumps([as_sent(by_name[n]) for n in names if n in used_any] + META_TOOLS, ensure_ascii=False))
    recorded = {r["definitions_per_turn"] for r in base}
    if recorded != {d_case}:
        sys.exit(f"the server's definitions of C1's tools count {d_case}, the runs recorded {sorted(recorded)}: "
                 "the server changed since the runs")

    def variant(r: dict, definitions: float, cache: str) -> dict:
        """The run with `definitions` tokens of definitions per turn. cache: "recorded" (each turn as it
        was billed), "cold" (the first turn not cached, the others as recorded: a run started with no
        recent run before it), "warm" (the first turn cached too: a run that follows another within the
        cache's life), "none" (no turn cached, for the definitions and for the rest)."""
        price, n = r["_price"], r["turns"]
        ratio = price["cached_input_per_mtok"] / price["input_per_mtok"]
        delta = definitions - r["definitions_per_turn"]
        if cache == "none":
            units = r["input"] + r["output"] * price["output_per_mtok"] / price["input_per_mtok"] + delta * n
        else:
            w = r["_weight"]
            units = r["_units"] + delta * w
            # The first turn's static head (prompts and definitions) changes price as a whole.
            head = r["first_turn_input"] + delta
            if cache == "warm" and r["_states"][0] != "cached":
                units -= head * (1 - ratio)
            if cache == "cold" and r["_states"][0] == "cached":
                units += head * (1 - ratio)
        return {"units": units, "input": r["input"] + delta * n,
                "turn_max": max(r["_turn_inputs"]) + delta, "turn_first": r["first_turn_input"] + delta}

    # The provider bills the definitions for fewer tokens than our count of their JSON. `as_billed`
    # repeats the scenario with every definition weight scaled by the ratio measured on these runs'
    # first turn (billed estimate / counted), assuming the ratio holds for the other tools.
    ratio_billed = statistics.median(r["billed_vs_counted"] for r in base)
    rows = {}
    for cache, scale in (("recorded", 1.0), ("cold", 1.0), ("warm", 1.0), ("none", 1.0),
                         ("cold_as_billed", ratio_billed), ("warm_as_billed", ratio_billed)):
        mode = cache.removesuffix("_as_billed")
        per_run = []
        for r in base:
            price = r["_price"]
            cached_price = price["cached_input_per_mtok"] / price["input_per_mtok"]
            # scaled: the recorded run stays as billed, the other two change by the scaled difference
            rec = variant(r, r["definitions_per_turn"], mode)
            r79 = variant(r, r["definitions_per_turn"] + scale * (d_all - d_case), mode)
            d = variant(r, r["definitions_per_turn"] + scale * (d_deferred - d_case), mode)
            # One more turn re-reads the whole conversation: at the cached price when there is a cache.
            turn = statistics.median(r["_turn_inputs"]) * (1.0 if mode == "none" else cached_price)
            per_run.append({
                "extra_turns_that_cancel_the_gain": (r79["units"] - d["units"]) / turn,
                "R79_cost_vs_recorded": r79["units"] / rec["units"],
                "D_cost_vs_R79": d["units"] / r79["units"],
                "D_cost_vs_recorded": d["units"] / rec["units"],
                "R79_input_vs_recorded": r79["input"] / rec["input"],
                "D_input_vs_R79": d["input"] / r79["input"],
                "R79_definitions_share_of_input": (r79["input"] - r["input"] + r["definitions_per_turn"] * r["turns"]) / r79["input"],
                "R79_first_turn_input": r79["turn_first"], "D_first_turn_input": d["turn_first"],
                "R79_turn_max": r79["turn_max"], "D_turn_max": d["turn_max"]})
        rows[cache] = {"runs": len(per_run), **{k: spread(x[k] for x in per_run) for k in per_run[0]}}
    longest = max(max(r["_turn_inputs"]) for r in base) + d_all - d_case
    hidden = [count(json.dumps(as_sent(t), ensure_ascii=False)) for t in server if t["name"] not in used_any]
    return {
        "counterfactual": True,
        "base": "C1, E1, arm R, as recorded (12 tools listed)", "runs": len(base),
        "server_tools": len(server),
        "definitions": {"all_server_tools": d_all, "case_tools_12": d_case, "tools_used_in_any_run": d_used,
                        "meta_tools_draft": d_meta, "deferred_visible_plus_meta": d_deferred,
                        "hidden_tools": len(server) - len(used_any),
                        "saved_per_turn": d_all - d_deferred, "saved_per_turn_share": 1 - d_deferred / d_all},
        "tools_used": {"in_any_run": used_any, "in_every_run": used_all,
                       "offered_never_called": sorted(set(CASE_TOOLS["C1"]) - set(used_any))},
        "billed_vs_counted": ratio_billed,
        "hidden_tool_tokens": {**spread(hidden), "mean": statistics.mean(hidden)},
        "cache": rows,
        "largest_request_R79": longest,
        "per_tool": sorted(({"name": t["name"], "tokens": count(json.dumps(as_sent(t), ensure_ascii=False)),
                             "used": t["name"] in used_any} for t in server), key=lambda x: -x["tokens"]),
    }


def measure() -> dict:
    count = tokenizer()
    ledger, price_of = ledger_lines(), prices()
    runs, skipped = [], []
    for case in CASES:
        metas = sorted((TRACES / case).glob("*.meta.json"))
        if not metas:
            sys.exit(f"{TRACES / case}: no run found (set $TRACETRIM_V04_TRACES)")
        for p in metas:
            m = json.loads(p.read_text())
            if m.get("dry_run") or m.get("pilot") or not m.get("usage_per_turn"):
                skipped.append({"case": case, "run": m.get("run"), "why": "dry run" if m.get("dry_run") else
                                "pilot" if m.get("pilot") else "no usage"})
                continue
            runs.append(read_run(p, ledger, count, price_of))
    if not SERVER_LIST.exists():
        sys.exit(f"{SERVER_LIST} not found: run `python -m experiments.definitions list` first")
    server = json.loads(SERVER_LIST.read_text())
    public = [{k: v for k, v in r.items() if not k.startswith("_")} for r in runs]
    return {
        "what": "weight of the tool definitions in the recorded runs of the v0.4 measures; tokens are o200k_base "
                "counts of the definitions as sent, costs are shares and ratios at list prices",
        "head_share": HEAD_SHARE,
        "price_lists": {m: name for name in PRICE_FILES for m in [json.loads((ROOT / "experiments" / name).read_text())["model"]]},
        "read": {"runs": len(runs), "skipped": skipped,
                 "by_case": {c: sum(r["case"] == c for r in runs) for c in CASES}},
        "groups": groups(runs),
        "scenario_all_server_tools": scenario(runs, server, count),
        "runs": public,
    }


def _s(x: dict, pct: bool = False, digits: int = 0) -> str:
    f = (lambda v: f"{100 * v:.{digits}f}%") if pct else (lambda v: f"{v:,.{digits}f}")
    return f(x["median"]) if x["min"] == x["max"] else f"{f(x['median'])} [{f(x['min'])}–{f(x['max'])}]"


def tables(res: dict) -> str:
    """The tables of the report, as Markdown, from the results alone."""
    out = []
    rd = res["read"]
    out.append(f"Read: {rd['runs']} runs (" + ", ".join(f"{c} {n}" for c, n in rd["by_case"].items())
               + f"); skipped {len(rd['skipped'])} (pilots, dry runs).\n")
    out.append("| Case | Model | Exp. | Arm | Runs | Tools | Definitions/turn | Turns | Definitions/run | Share of input "
               "| Definition tokens billed as cached | Share of the run's cost | ...if nothing were cached |")
    out.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for g in res["groups"]:
        out.append(f"| {g['case']} | {g['model']} | {g['experiment']} | {g['arm']} | {g['runs']} | {_s(g['tools_offered'])} "
                   f"| {_s(g['definitions_per_turn'])} | {_s(g['turns'])} | {_s(g['definitions_per_run'])} "
                   f"| {_s(g['definitions_share_of_input'], True, 1)} | {_s(g['definitions_cached_share'], True)} "
                   f"| {_s(g['definitions_share_of_cost'], True, 1)} | {_s(g['definitions_share_of_cost_if_never_cached'], True, 1)} |")
    out.append("")
    out.append("| Case | Model | Exp. | Arm | Input/turn, median | Largest turn | Definitions in the largest turn "
               "| Runs whose first turn was cached | Unclear turns | Billed static head − counted | Definitions, billed estimate / counted |")
    out.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for g in res["groups"]:
        out.append(f"| {g['case']} | {g['model']} | {g['experiment']} | {g['arm']} | {_s(g['input_per_turn_median'])} "
                   f"| {_s(g['input_per_turn_max'])} | {_s(g['definitions_share_of_peak_context'], True, 1)} "
                   f"| {g['first_turn_cached_runs']} of {g['runs']} | {g['turns_unclear']} | {_s(g['billed_static_minus_counted'])} "
                   f"| {_s(g['billed_vs_counted'], digits=2)} |")
    sc = res["scenario_all_server_tools"]
    d = sc["definitions"]
    out.append("")
    out.append(f"Scenario, COUNTERFACTUAL: {sc['base']}, {sc['runs']} runs, with the server's {sc['server_tools']} tools.\n")
    out.append("| Definitions per turn | Tokens |")
    out.append("|---|---:|")
    out.append(f"| R79: all {sc['server_tools']} tools of the server | {d['all_server_tools']:,} |")
    out.append(f"| the 12 tools of the case, as recorded | {d['case_tools_12']:,} |")
    out.append(f"| the {len(sc['tools_used']['in_any_run'])} tools called in at least one run | {d['tools_used_in_any_run']:,} |")
    out.append(f"| the two meta-tools (draft) | {d['meta_tools_draft']:,} |")
    out.append(f"| D: tools called + meta-tools, {d['hidden_tools']} tools hidden | {d['deferred_visible_plus_meta']:,} |")
    out.append(f"| saved per turn, D against R79 | {d['saved_per_turn']:,} ({100 * d['saved_per_turn_share']:.0f}%) |")
    out.append("")
    out.append("| Cache assumed | R79 cost vs recorded | D cost vs R79 | D cost vs recorded | R79 input vs recorded "
               "| D input vs R79 | Definitions' share of R79 input | First turn, R79 → D | Largest turn, R79 → D "
               "| Extra turns that cancel D's gain |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    b = f"definitions as billed (×{sc['billed_vs_counted']:.2f})"
    label = {"recorded": "as recorded, turn by turn", "cold": "cold start: first turn not cached",
             "warm": "warm start: first turn cached too", "none": "no cache at all",
             "cold_as_billed": f"cold start; {b}", "warm_as_billed": f"warm start; {b}"}
    for k, c in sc["cache"].items():
        out.append(f"| {label[k]} | {_s(c['R79_cost_vs_recorded'], digits=2)} | {_s(c['D_cost_vs_R79'], digits=2)} "
                   f"| {_s(c['D_cost_vs_recorded'], digits=2)} | {_s(c['R79_input_vs_recorded'], digits=2)} "
                   f"| {_s(c['D_input_vs_R79'], digits=2)} | {_s(c['R79_definitions_share_of_input'], True)} "
                   f"| {c['R79_first_turn_input']['median']:,.0f} → {c['D_first_turn_input']['median']:,.0f} "
                   f"| {c['R79_turn_max']['median']:,.0f} → {c['D_turn_max']['median']:,.0f} "
                   f"| {_s(c['extra_turns_that_cancel_the_gain'], digits=1)} |")
    out.append("")
    h = sc["hidden_tool_tokens"]
    out.append(f"A hidden tool's definition: median {h['median']:,.0f} tokens, mean {h['mean']:,.0f} "
               f"[{h['min']:,}–{h['max']:,}]: what one result of find_tools adds to the conversation.")
    out.append(f"Largest request in R79: {sc['largest_request_R79']:,} tokens. Offered and never called: "
               f"{', '.join(sc['tools_used']['offered_never_called']) or 'none'}.")
    return "\n".join(out)


async def list_server() -> None:
    """tools/list on the DataForSEO MCP server: free, no tool is called. The address is read from .env
    and written nowhere."""
    from .tools import DataForSEO
    d = DataForSEO([])
    await d._connect()
    try:
        tools, cursor = [], None
        while True:
            r = await d.session.list_tools(cursor=cursor) if cursor else await d.session.list_tools()
            tools += [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in r.tools]
            cursor = getattr(r, "nextCursor", None) or getattr(r, "next_cursor", None)
            if not cursor:
                break
    finally:
        await d._close()
    SERVER_LIST.parent.mkdir(parents=True, exist_ok=True)
    SERVER_LIST.write_text(json.dumps(tools, ensure_ascii=False, indent=1))
    print(f"{len(tools)} tools listed -> {SERVER_LIST.relative_to(ROOT)}")


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("command", nargs="?", choices=["list"])
    a.add_argument("--no-write", action="store_true", help="print the tables without writing the results file")
    o = a.parse_args(argv)
    if o.command == "list":
        asyncio.run(list_server())
        return
    res = measure()
    if not o.no_write:
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        RESULTS.write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
        print(f"written {RESULTS.relative_to(ROOT)}\n")
    print(tables(res))


if __name__ == "__main__":
    main()
