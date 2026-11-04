#!/usr/bin/env python3
"""One live run of a study agent: the loop, the trace, the spend ledger.

  .venv/bin/python -m experiments.run C1 pilot
  .venv/bin/python -m experiments.run C2 A01
  .venv/bin/python -m experiments.run --spend          # ledger total, no run

Writes:
  traces/<case>/<run>.jsonl       events in the tracetrim format
  traces/<case>/<run>.meta.json   model, timing, usage per turn, gates, cost
  out/ledger.jsonl                one line per run, the stop rule reads it

Our own loop, because hosted MCP never shows tool outputs to the client.
Stateful (`previous_response_id`): the reasoning stays on the server and the
client sends only the new tool results, as a hosted integration would.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import AsyncOpenAI

from .tools import ROOT, to_args, toolset

from .paths import ARMS, ARMS_D, LEDGER, PREFIX, PRICES, PROMPTS, TRACES, cost, keep_missing, spent  # noqa: E402,F401

# A safety guard, not an experimental cap: well above the 50 calls the C1
# prompt allows. If it fires, the run is flagged and excluded.
GUARD_CALLS = 80
GUARD_TURNS = 40


def text_of(item) -> str:
    return "".join(getattr(c, "text", "") or "" for c in (getattr(item, "content", None) or []))


def phase_c_projection(case: str):
    """Phase C: the prudent projection learned from ALL Phase A traces of the case."""
    from tracetrim.projection import learn
    from tracetrim.traces import load
    from tracetrim.usage import analyse
    paths = sorted((TRACES / case).glob("A*.jsonl"))
    if len(paths) < 2:
        sys.exit(f"{case}: Phase C needs Phase A traces, found {len(paths)}")
    # 0.1 behaviour pinned: Phase C tests the projection of Phases A-B.
    return (learn([analyse(load(p), normalize=False, comma_grouping=False) for p in paths], "prudent", protect_ids=False),
            [p.stem for p in paths])


async def run(case: str, run_id: str, model: str, arm: str = "raw") -> dict:
    before = spent()
    if before >= PRICES["stop_usd"]:
        sys.exit(f"stop rule: {before:.2f} USD already spent (cap {PRICES['stop_usd']})")
    if before >= PRICES["warn_usd"]:
        print(f"WARNING: {before:.2f} USD spent, cap {PRICES['stop_usd']}")

    system = (PROMPTS / f"{case.lower()}-system.md").read_text(encoding="utf-8")
    user = (PROMPTS / f"{case.lower()}-user.md").read_text(encoding="utf-8")
    out_dir = TRACES / case
    out_dir.mkdir(parents=True, exist_ok=True)
    trace_path, meta_path = out_dir / f"{run_id}.jsonl", out_dir / f"{run_id}.meta.json"
    if trace_path.exists():
        sys.exit(f"{trace_path} exists: run ids are never reused")

    events = [{"kind": "prompt", "text": system}, {"kind": "prompt", "text": user}]
    turns, flags = [], []
    total = {"input": 0, "cached": 0, "output": 0}
    client = AsyncOpenAI(timeout=900.0, max_retries=0)
    t0 = time.time()

    try:
        return await _loop(case, run_id, model, arm, before, system, user, trace_path, meta_path,
                           events, turns, total, flags, client, t0)
    except BaseException:
        # The tokens of an aborted run are spent all the same: the ledger must
        # see them, or the stop rule counts less than the bill.
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"case": case, "run": run_id, "model": model, "aborted": True,
                                 "cost_usd": round(cost(total), 4), "usage": total}) + "\n")
        raise


async def _loop(case, run_id, model, arm, before, system, user, trace_path, meta_path,
                events, turns, total, flags, client, t0):
    n_calls = 0
    async with toolset(case) as base:
        tools, learned_from = base, None
        if arm == "prudent":
            from .expand import Trimmed
            projection, learned_from = phase_c_projection(case)
            tools = Trimmed(base, projection)
        kwargs = {"model": model, "instructions": system, "tools": tools.schemas,
                  "input": [{"role": "user", "content": user}]}
        while True:
            r = await client.responses.create(**kwargs)
            u = r.usage
            turn = {"input": u.input_tokens, "output": u.output_tokens,
                    "cached": getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0}
            for k in total:
                total[k] += turn[k]
            pending = []
            for item in r.output:
                if item.type == "message":
                    events.append({"kind": "message", "text": text_of(item)})
                elif item.type == "function_call":
                    pending.append(item)
            # Executed in the order the model emitted them, so the trace
            # order is the order the outputs entered the context.
            outputs = []
            for fc in pending:
                args = to_args(fc.arguments)
                out = await tools.call(fc.name, args)
                n_calls += 1
                events.append({"kind": "call", "tool": fc.name, "arguments": fc.arguments, "output": out})
                outputs.append({"type": "function_call_output", "call_id": fc.call_id, "output": out})
            turn["calls"] = len(pending)
            turns.append(turn)
            run_cost = cost(total)
            print(f"  turn {len(turns):>2}: {len(pending):>2} calls · input {turn['input']:>7,} "
                  f"· output {turn['output']:>6,} · run {run_cost:.3f} USD", flush=True)
            if not pending:
                break
            if before + run_cost >= PRICES["stop_usd"]:
                flags.append("stopped_by_budget")
                break
            if n_calls >= GUARD_CALLS or len(turns) >= GUARD_TURNS:
                flags.append("safety_guard")
                break
            if getattr(tools, "quota_stop", False):
                flags.append("openalex_quota")
            # `instructions` is NOT carried over by previous_response_id: without
            # it, every turn after the first runs with no system prompt. The
            # pilot of 2026-11-04 lost its output format exactly this way.
            kwargs = {"model": model, "instructions": system, "tools": tools.schemas,
                      "previous_response_id": r.id, "input": outputs}
        rejections = tools.rejections

    with trace_path.open("w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    meta = {
        "case": case, "run": run_id, "model": model,
        "started": datetime.fromtimestamp(t0, timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.time() - t0, 1),
        "turns": len(turns), "calls": n_calls, "usage": total, "usage_per_turn": turns,
        "domain_rejections": rejections, "flags": flags + (["tool_failure"] if getattr(base, "tool_failures", None) else []),
        "tool_failures": getattr(base, "tool_failures", []), "retries": getattr(base, "retries", 0),
        "arm": arm, "learned_from": learned_from,
        "expand_calls": getattr(tools, "expand_calls", None),
        "expanded_fields": getattr(tools, "expanded_fields", None),
        "cost_usd": round(cost(total), 4), "prices": "experiments/prices.json",
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({k: meta[k] for k in ("case", "run", "model", "started", "cost_usd")}
                            | {"usage": total}) + "\n")
    return meta


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("case", nargs="?", choices=["C1", "C2", "C3", "C4", "C5"])
    p.add_argument("run_id", nargs="?")
    p.add_argument("--model", default=PRICES["model"])
    p.add_argument("--spend", action="store_true")
    p.add_argument("--arm", choices=["raw", "prudent"], default="raw",
                   help="Phase C: prudent = projection learned from Phase A, plus expand")
    a = p.parse_args()
    if a.spend or not a.case:
        print(f"spent: {spent():.3f} USD of {PRICES['stop_usd']} (list prices, experiments/prices.json)")
        return
    m = asyncio.run(run(a.case, a.run_id, a.model, a.arm))
    print(f"\n  {m['case']} {m['run']}: {m['turns']} turns, {m['calls']} calls, "
          f"input {m['usage']['input']:,} (cached {m['usage']['cached']:,}), output {m['usage']['output']:,}, "
          f"{m['cost_usd']:.3f} USD, {m['seconds']} s, rejections {m['domain_rejections']}, flags {m['flags']}")


if __name__ == "__main__":
    main()
