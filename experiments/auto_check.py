#!/usr/bin/env python3
"""End-to-end check of the automatic mode with a real agent (not part of the study).

  .venv/bin/python -m experiments.auto_check --runs 4 --min-runs 3                    # MCP: through the proxy
  .venv/bin/python -m experiments.auto_check --runs 4 --min-runs 3 --mode function    # function calling: AutoTrim

An agent (Responses API, the demo prompt for billing support) talks MCP to the
tracetrim proxy in automatic mode, which sits in front of the simulated
billing server. Expected: the first `min-runs` runs only teach (no trimming),
the next ones are trimmed; the agent calls tracetrim_done; answers stay right.

With --mode function there is no proxy: the loop runs the billing tools
itself (as an agent with function calling does) and passes every result
through tracetrim.auto.AutoTrim, and the final answer to AutoTrim.done.
Spend is printed, and is not counted in the study's ledger. The table is also
written to results/checks/auto_check_<mode>.json: the README quotes it. Tool
tokens are counted with tiktoken: "raw" from the run's trace in the work
directory (what the tools returned), "sent" from what the model received,
`expand` results included.
"""
import argparse
import asyncio
import json
import re
import shutil
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from openai import AsyncOpenAI

from .quality import q_c4
from .paths import PRICES, ROOT, cost

PROMPTS = (ROOT / "docs" / "demo_prompts_IT.md").read_text(encoding="utf-8")
SYSTEM, USER = re.findall(r"```\n(.*?)\n```", PROMPTS, re.S)[:2]


async def one_run(client, model, session, schemas, tt=None, count=None):
    usage = {"input": 0, "cached": 0, "output": 0, "tool_sent": 0, "expand_calls": 0}
    calls, done = 0, False
    kwargs = {"model": model, "instructions": SYSTEM, "tools": schemas, "input": [{"role": "user", "content": USER}]}
    answer = ""
    for _ in range(20):
        r = await client.responses.create(**kwargs)
        u = r.usage
        usage["input"] += u.input_tokens
        usage["output"] += u.output_tokens
        usage["cached"] += getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
        outs = []
        for item in r.output:
            if item.type == "message":
                answer = "".join(getattr(c, "text", "") or "" for c in item.content)
            if item.type == "function_call":
                calls += 1
                done |= item.name == "tracetrim_done"
                args = json.loads(item.arguments or "{}")
                if tt is not None and item.name == "expand":
                    text = tt.expand(args)
                else:
                    res = await session.call_tool(item.name, args)
                    text = "".join(getattr(c, "text", "") or "" for c in res.content)
                    if tt is not None:
                        text = tt.output(item.name, args, text)
                # What the model read from tools, expanded fields included.
                if item.name != "tracetrim_done":
                    usage["tool_sent"] += count(text)
                usage["expand_calls"] += item.name == "expand"
                outs.append({"type": "function_call_output", "call_id": item.call_id, "output": text})
        if not outs:
            break
        kwargs = {"model": model, "instructions": SYSTEM, "tools": schemas, "previous_response_id": r.id, "input": outs}
    if tt is not None:
        tt.done(answer)
        done = True
    return usage, calls, done, answer


def tickets_right(answer: str) -> int:
    # The demo prompt answers in readable text: rebuild the shape q_c4 reads.
    blocks = re.split(r"###\s*", answer)
    tickets = []
    for b in blocks:
        m = re.match(r"(T\d)", b.strip())
        if m:
            tickets.append({"id": m.group(1), "diagnosi": b, "risposta": b})
    return q_c4({"ticket": tickets})["correct_diagnoses"]


async def main():
    a = argparse.ArgumentParser()
    a.add_argument("--runs", type=int, default=4)
    a.add_argument("--min-runs", type=int, default=3)
    a.add_argument("--model", default=PRICES["model"])
    a.add_argument("--mode", choices=["mcp", "function"], default="mcp")
    o = a.parse_args()
    work = ROOT / "out" / ("auto_check" if o.mode == "mcp" else "auto_check_function")
    shutil.rmtree(work, ignore_errors=True)
    tt = None
    if o.mode == "mcp":
        params = StdioServerParameters(command=sys.executable, cwd=str(ROOT), args=[
            "-m", "tracetrim.cli", "proxy", "--upstream-cmd", f"{sys.executable} -m experiments.simulated.server_c4",
            "--auto", str(work), "--min-runs", str(o.min_runs)])
    else:
        from tracetrim.auto import AutoTrim
        params = StdioServerParameters(command=sys.executable, cwd=str(ROOT),
                                       args=["-m", "experiments.simulated.server_c4"])
        tt = AutoTrim(work, min_runs=o.min_runs)
    client = AsyncOpenAI(timeout=600.0, max_retries=1)
    from tracetrim.measure import tokenizer
    from tracetrim.traces import load
    count = tokenizer()
    rows = []
    total = 0.0
    print(f"\n  {'run':<5}{'input':>9}{'uncached':>10}{'output':>8}{'calls':>7}  done  right  trimming  USD")
    async with stdio_client(params, errlog=open(work.parent / f"{work.name}.log", "w")) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        for i in range(1, o.runs + 1):
            listed = (await s.list_tools()).tools
            schemas = [{"type": "function", "name": t.name, "description": t.description or "",
                        "parameters": dict(t.input_schema or {"type": "object", "properties": {}}), "strict": False}
                       for t in listed]
            if tt is not None:
                schemas.append({"type": "function", **tt.expand_tool, "strict": False})
            usage, calls, done, answer = await one_run(client, o.model, s, schemas, tt, count)
            c = cost(usage)
            last = sorted((work / "traces").glob("run-*.jsonl"))[-1]
            raw_tools = sum(count(e.output) for e in load(last).events if e.kind == "call" and e.output)
            total += c
            cfg = work / "report.config.json"
            trimming = bool(cfg.exists() and json.loads(cfg.read_text())["tools"])
            print(f"  {i:<5}{usage['input']:>9,}{usage['input'] - usage['cached']:>10,}{usage['output']:>8,}{calls:>7}"
                  f"  {'yes' if done else 'NO':<5} {tickets_right(answer)}/5    {'on' if trimming else 'off':<8}  {c:.3f}"
                  f"   tools {raw_tools:,} -> {usage['tool_sent']:,}",
                  flush=True)
            rows.append({"run": i, "input": usage["input"], "uncached": usage["input"] - usage["cached"],
                         "output": usage["output"], "calls": calls, "done": done, "right": tickets_right(answer),
                         "trimming_after": trimming, "tool_tokens_raw": raw_tools,
                         "tool_tokens_sent": usage["tool_sent"], "expand_calls": usage["expand_calls"],
                         "usd_estimate": round(c, 4)})
    out = ROOT / "results" / "checks" / f"auto_check_{o.mode}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"mode": o.mode, "model": o.model, "min_runs": o.min_runs,
                               "note": "an end-to-end check, not a measurement under a protocol",
                               "runs": rows}, indent=2) + "\n")
    print(f"\n  total {total:.3f} USD (list prices, experiments/prices.json; not in the study ledger)")
    print(f"  written: {out.relative_to(ROOT)}")
    print(f"  report: {work / 'report.html'}\n")


if __name__ == "__main__":
    asyncio.run(main())
