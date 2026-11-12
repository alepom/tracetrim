"""Adaptive learning, offline: does tracetrim go back to keeping a field that becomes necessary?

  python -m experiments.adaptive_demo                 # both adapters, results/checks/adaptive_demo.json
  python -m experiments.adaptive_demo --adapter function --print

No model and no spend: the "agent" is a script, the tool is simulated
(experiments/simulated/orders.py). What is exercised is tracetrim's side of
the loop, through its two adapters: `AutoTrim` (function calling) and the MCP
proxy over stdio. It is a demo and a check of the mechanism, not evidence of
how a model behaves: a real agent decides by itself whether to call expand.

The workflow. In the first runs the agent reports an order's id, status,
total and date; tracetrim learns to remove the rest. Then the task changes and
one more field is needed (the shift). Three variants of the shift:

  visible     the agent asks the field back with expand and writes its value
              (`carrier`): the reuse can be seen in the answer
  invisible   the agent asks the field back and uses it without writing its
              value (`refund_note`, turned into a verdict): nothing to see
  lazy        the agent never calls expand: it uses `carrier` only when the
              result shows it, and otherwise answers without it

Each variant runs in four arms: promotion off/on, sampling off/on. Measured:
after how many runs of the new task the field is kept again (it reaches the
agent in a filtered result, with no expand), how many expand calls were made,
whether the tool was switched off, and what saving is left at the end.
Tokens: tiktoken o200k_base; what the agent received includes expand replies.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from experiments.simulated import orders
from tracetrim.auto import AutoTrim
from tracetrim.engine import MARK
from tracetrim.evidence import Adaptive
from tracetrim.measure import tokenizer
from tracetrim.projection import Projection

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "checks" / "adaptive_demo.json"
TOOL = "list_orders"
MIN_RUNS, LEARN_RUNS, SHIFT_RUNS, LIMIT = 3, 5, 10, 3
VARIANTS = {"visible": "carrier", "invisible": "refund_note", "lazy": "carrier"}
ARMS = {"baseline": {}, "promotion": {"promote": True}, "sampling": {"sample_rate": 0.34, "sample_seed": 7},
        "promotion+sampling": {"promote": True, "sample_rate": 0.34, "sample_seed": 7}}


class FunctionCalling:
    """The agent runs its own tool and passes results through AutoTrim."""
    name = "function"

    def __init__(self, work, adaptive):
        self.tt = AutoTrim(work, min_runs=MIN_RUNS, adaptive=adaptive, compact=False)  # serialisation it was run with (compact JSON came in 0.4)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.tt.learner.flush()

    async def call(self, arguments, tool=TOOL):
        raw = orders.OUTPUTS[tool](**arguments)
        return raw, self.tt.output(tool, arguments, raw)

    async def expand(self, call_id, fields):
        return self.tt.expand({"call_id": call_id, "fields": fields})

    async def done(self, answer):
        self.tt.done(answer)


class Mcp:
    """The agent reaches the simulated MCP server through the tracetrim proxy."""
    name = "mcp"

    def __init__(self, work, adaptive, call_log=None):
        from mcp.client.stdio import StdioServerParameters
        upstream = f"{sys.executable} -m experiments.simulated.server_orders" + (f" {call_log}" if call_log else "")
        flags = ["--promote"] * adaptive.promote + ["--promote-runs", str(adaptive.promote_requested_runs)]
        if adaptive.sample_rate:
            flags += ["--sample-rate", str(adaptive.sample_rate), "--sample-seed", str(adaptive.sample_seed),
                      "--sample-max-share", str(adaptive.sample_max_share)]
        self.params = StdioServerParameters(command=sys.executable, cwd=str(ROOT), args=[
            "-m", "tracetrim.cli", "proxy", "--upstream-cmd", upstream,
            "--auto", str(work), "--min-runs", str(MIN_RUNS), "--quiet", "--no-compact", *flags])

    async def __aenter__(self):
        from contextlib import AsyncExitStack

        from mcp import ClientSession
        from mcp.client.stdio import stdio_client
        self.stack = AsyncExitStack()
        read, write = await self.stack.enter_async_context(stdio_client(self.params))
        self.session = await self.stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        return self

    async def __aexit__(self, *exc):
        await self.stack.aclose()

    async def call(self, arguments, tool=TOOL):
        sent = (await self.session.call_tool(tool, arguments)).content[0].text
        return orders.OUTPUTS[tool](**arguments), sent

    async def expand(self, call_id, fields):
        return (await self.session.call_tool("expand", {"call_id": call_id, "fields": fields})).content[0].text

    async def done(self, answer):
        await self.session.call_tool("tracetrim_done", {"answer": answer})


ADAPTERS = {"function": FunctionCalling, "mcp": Mcp}


def _answer(order: dict, customer: str) -> str:
    day = datetime.fromtimestamp(order["created"], timezone.utc)
    return (f"Order {order['id']} of customer {customer}: {order['status']}, total {order['total'] / 100:.2f} EUR, "
            f"placed on {day.day} {day.strftime('%B %Y')}.")


async def one_run(session, k: int, variant: str | None, count) -> dict:
    """One scripted run: a call, perhaps an expand, the answer. `variant`: None while learning."""
    customer = f"cus_{k:03d}"
    raw, sent = await session.call({"customer": customer, "limit": LIMIT})
    body, _, line = sent.partition(MARK)
    order = json.loads(body)["data"][0]                     # the newest order is the one reported
    received, expands, field = count(sent), 0, VARIANTS.get(variant)
    answer = _answer(order, customer)
    if field:
        value = order.get(field)
        if value is None and line and variant != "lazy":
            back = await session.expand(line.split("call_id=")[1].split()[0], [f"data[].{field}"])
            received, expands = received + count(back), 1
            value = json.loads(back)["data"][0][field]
        if field == "carrier":
            answer += f" Carrier: {value}." if value else " Carrier: not available."
        else:                                               # used, never quoted: a verdict in the agent's words
            answer += " Refunds: " + ("to be approved by a person." if "review" in value or "approval" in value
                                      or "blocked" in value else "handled without approval.")
    await session.done(answer)
    return {"run": k, "phase": "shift" if variant else "learn", "raw": count(raw), "received": received,
            "trimmed_line": bool(line), "expands": expands,
            # Whether the field reached the agent in the result itself, and whether the answer has it.
            "field_in_result": bool(field) and field in order, "field_in_answer": not answer.endswith("not available.")}


async def scenario(adapter: str, variant: str, arm: str, count) -> dict:
    with tempfile.TemporaryDirectory() as work:
        # The demo's results were computed with the switch-off rule of the time (one run is
        # enough); the library's default became 2 runs afterwards (02c): pinned to keep them reproducible.
        async with ADAPTERS[adapter](work, Adaptive(switch_off_runs=1, **ARMS[arm])) as session:
            runs = [await one_run(session, k, None if k <= LEARN_RUNS else variant, count)
                    for k in range(1, LEARN_RUNS + SHIFT_RUNS + 1)]
        state = json.loads((Path(work) / "state.json").read_text())
        config = json.loads((Path(work) / "report.config.json").read_text())
        report = json.loads((Path(work) / "report.json").read_text())
        projection = json.loads((Path(work) / "report.projection.json").read_text())
    shift = [r for r in runs if r["phase"] == "shift"]
    field = f"data[].{VARIANTS[variant]}"
    # Kept again: from this run of the new task on, every FILTERED result carries the field, with no
    # expand. Sampled results are skipped: they carry everything, whatever the projection keeps.
    kept = next((n for n, r in enumerate(shift, 1) if r["trimmed_line"] and all(
        x["field_in_result"] and not x["expands"] for x in shift[n - 1:] if x["trimmed_line"])), None)
    sampled = [n for n, r in enumerate(shift, 1) if not r["trimmed_line"] and not state["off"]]
    last = shift[-1]
    return {
        "adapter": adapter, "variant": variant, "arm": arm, "field": field,
        "kept_again_from_shift_run": kept,
        "expand_calls": sum(r["expands"] for r in shift),
        "answers_without_the_field": sum(not r["field_in_answer"] for r in shift),
        "tool_switched_off": TOOL in state["off"],
        "field_kept_by_projection": field in projection["tools"].get(TOOL, []),
        "promotions": [{k: p[k] for k in ("field", "reason", "runs", "status")}
                       for p in report["adaptive"]["promotions"]],
        "sampled_results": state["sampling"].get(TOOL, {}).get("sampled", 0),
        "first_sampled_shift_run": sampled[0] if sampled and ARMS[arm].get("sample_rate") else None,
        "last_learn_run": {k: runs[LEARN_RUNS - 1][k] for k in ("raw", "received")},
        "last_shift_run": {k: last[k] for k in ("raw", "received")},
        "saving_left": round(1 - last["received"] / last["raw"], 4),
        "trimming_on_at_the_end": bool(config["tools"]),
        "runs": runs,
        # What decided: compared between adapters by the parity test.
        "decisions": {"state": {k: state[k] for k in ("runs", "complete_runs", "trimmed", "expanded", "off",
                                                      "expand_runs", "sampling")},
                      "kept": projection["tools"], "dropped": projection["dropped"],
                      "evidence": report["adaptive"]["evidence"], "resumed": report["adaptive"]["resumed"],
                      "exposure": [[r["trimmed_line"], r["field_in_result"], r["expands"]] for r in runs]},
    }


def overhead(count) -> list[dict]:
    """What the new functions add to a filtered result, on a small and a large output."""
    rows = []
    with tempfile.TemporaryDirectory() as work:
        tt = AutoTrim(work, min_runs=MIN_RUNS, compact=False)  # serialisation it was run with (compact JSON came in 0.4)
        for k in range(1, MIN_RUNS + 1):
            raw = orders.output(f"cus_{k:03d}", LIMIT)
            tt.output(TOOL, {"customer": f"cus_{k:03d}", "limit": LIMIT}, raw)
            tt.done(_answer(json.loads(raw)["data"][0], f"cus_{k:03d}"))
        for label, limit in (("small", 1), ("large", 50)):
            raw = orders.output("cus_900", limit)
            sent = tt.output(TOOL, {"customer": "cus_900", "limit": limit}, raw)
            body, _, line = sent.partition(MARK)
            cid = line.split("call_id=")[1].split()[0]
            whole = tt.trimmer.policy.apply(TOOL, raw, project=False)
            p = tt.trimmer.policy.projection
            wider = Projection(p.mode, p.source, {TOOL: p.keep[TOOL] | {"data[].carrier"}}, p.seen, p.unseen)
            rows.append({
                "output": label, "orders": limit, "raw": count(raw), "reserialised_whole": count(whole),
                "filtered_body": count(body), "trimmed_line": count(sent) - count(body), "filtered_sent": count(sent),
                "saving": round(1 - count(sent) / count(raw), 4),
                "expand_one_field": count(tt.expand({"call_id": cid, "fields": ["data[].carrier"]})),
                "expand_everything": count(tt.expand({"call_id": cid, "fields": ["*"]})),
                # A sampled result is the whole output: it costs what filtering would have removed.
                "sampled_instead_of_filtered": count(whole) - count(sent),
                # One promoted field: what it adds back to every filtered result.
                "promoted_field_carrier": count(wider.apply(TOOL, raw, keep_empty=True)) - count(body),
            })
    return rows


SEEDS = range(20)


async def seeds(count) -> dict:
    """Sampling depends on its seed: the lazy variant, sampling arm, over twenty seeds."""
    kept = []
    for seed in SEEDS:
        ARMS["_seed"] = {"sample_rate": ARMS["sampling"]["sample_rate"], "sample_seed": seed}
        kept.append((await scenario("function", "lazy", "_seed", count))["kept_again_from_shift_run"])
    del ARMS["_seed"]
    found = sorted(k for k in kept if k)
    return {"variant": "lazy", "adapter": "function", "sample_rate": ARMS["sampling"]["sample_rate"],
            "sample_max_share": Adaptive().sample_max_share, "seeds": len(kept), "shift_runs": SHIFT_RUNS,
            "kept_again_from_shift_run": kept, "kept_within_the_shift_runs": len(found),
            "median_of_those": found[len(found) // 2] if found else None, "earliest": found[0] if found else None}


def stable_count():
    """Tokens, with the random call id replaced by a fixed one: its tokens vary
    from run to run, and the results file must come out the same every time."""
    count = tokenizer()
    return lambda text: count(re.sub(r"call_id=c[0-9a-f]{8}", "call_id=c00000000", text or ""))


async def main(adapters, show) -> dict:
    count = stable_count()
    rows = [await scenario(a, v, arm, count) for a in adapters for v in VARIANTS for arm in ARMS]
    return {"what": "adaptive learning demo: scripted agent, simulated tool, no model (a check, not a measurement)",
            "tokens": "tiktoken o200k_base",
            "settings": {"min_runs": MIN_RUNS, "learn_runs": LEARN_RUNS, "shift_runs": SHIFT_RUNS, "limit": LIMIT,
                         "arms": ARMS},
            "scenarios": rows, "sampling_seeds": await seeds(count), "overhead": overhead(count)}


def table(result: dict) -> str:
    lines = [f"{'adapter':<9}{'variant':<10}{'arm':<20}{'kept again':>11}{'expand':>8}{'no field':>9}"
             f"{'tool off':>9}{'sampled':>8}{'saving left':>12}"]
    for r in result["scenarios"]:
        kept = f"run {r['kept_again_from_shift_run']}" if r["kept_again_from_shift_run"] else "never"
        lines.append(f"{r['adapter']:<9}{r['variant']:<10}{r['arm']:<20}{kept:>11}{r['expand_calls']:>8}"
                     f"{r['answers_without_the_field']:>9}{'yes' if r['tool_switched_off'] else 'no':>9}"
                     f"{r['sampled_results']:>8}{r['saving_left']:>12.0%}")
    s = result["sampling_seeds"]
    lines.append(f"\nlazy variant, sampling at {s['sample_rate']} over {s['seeds']} seeds: field kept again within "
                 f"{s['shift_runs']} runs in {s['kept_within_the_shift_runs']} of them (earliest run {s['earliest']}, "
                 f"median of those run {s['median_of_those']})")
    lines.append("")
    lines.append(f"{'output':<8}{'raw':>7}{'filtered':>10}{'line':>6}{'saving':>8}{'expand 1':>10}{'expand *':>10}"
                 f"{'sampled +':>11}{'promoted +':>12}")
    for o in result["overhead"]:
        lines.append(f"{o['output']:<8}{o['raw']:>7}{o['filtered_sent']:>10}{o['trimmed_line']:>6}{o['saving']:>8.0%}"
                     f"{o['expand_one_field']:>10}{o['expand_everything']:>10}{o['sampled_instead_of_filtered']:>11}"
                     f"{o['promoted_field_carrier']:>12}")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adapter", choices=[*ADAPTERS, "both"], default="both")
    ap.add_argument("--print", action="store_true", help="print the table, do not write the results file")
    a = ap.parse_args()
    res = asyncio.run(main(list(ADAPTERS) if a.adapter == "both" else [a.adapter], a.print))
    if not a.print:
        for r in res["scenarios"]:
            del r["decisions"]                      # only for the parity test (tests/test_adaptive.py)
        OUT.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
        print(OUT.relative_to(ROOT))
    print(table(res))
