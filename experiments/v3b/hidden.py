"""Check that every v3b task is hard by construction, and capture the judge's reference evidence.

  python -m experiments.v3b.hidden                 # the check, on every candidate task (no spend)
  python -m experiments.v3b.hidden --reference     # also write material/reference/C4b-NN.json

The check: for each task, the typical requests (search each ticket's email, then list the invoices,
charges and subscriptions of every customer found) go to the task's simulated server over MCP and
through a fresh engine with the frozen prudent policy P of protocol v3. A fact marked `hidden` is
verified when its value is in the original result at its path and that path is absent from what P
delivers. A task qualifies with at least one verified hidden fact.

The reference evidence: the original returns of every read endpoint for every customer of the
task (lists, details, refunds), the same for all arms, as in protocol v3.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re

from tracetrim.contract import ToolCall, ToolResult
from tracetrim.engine import Engine
from tracetrim.runtime import Policy
from tracetrim.usage import leaves, parse_output

from experiments.v3.worlds import DATA as V3_DATA

from .tools import TaskTools, task_data
from .worlds import CANDIDATES, DATA, eur, write

POLICY = V3_DATA / "policies" / "C4" / "P" / "config.json"


def matches(fact, value) -> bool:
    if fact["kind"] == "amount":
        return (isinstance(value, int) and abs(value) == fact["value"]) or \
               (isinstance(value, str) and eur(fact["value"]) in value)
    if fact["kind"] == "count":
        return isinstance(value, str) and re.search(rf"\b{fact['value']}\s*{fact['unit']}", value) is not None
    return value == fact["value"]


def typical_requests(task):
    return [("stripe_search_customers", {"email": e}) for e in re.findall(r"[\w.]+@example\.test", task_data(task)["prompt"])]


async def check(task) -> dict:
    data = task_data(task)
    engine = Engine(policy=Policy.load(POLICY), breakdown=False)
    seen = []                                  # (tool, original leaves, delivered leaves)
    async with TaskTools(task) as tools:
        for tool, args in typical_requests(task):
            pending = [(tool, args)]
            while pending:
                name, a = pending.pop(0)
                out = await tools.call(name, a)
                obs = engine.observe(ToolCall(name, a), ToolResult(out))
                seen.append((name, list(leaves(parse_output(out))),
                             list(leaves(parse_output(obs.text.split("\n[trimmed]")[0])))))
                if name == "stripe_search_customers":
                    for c in json.loads(out)["data"]:
                        pending += [(t, {"customer": c["id"]}) for t in
                                    ("stripe_list_invoices", "stripe_list_charges", "stripe_list_subscriptions")]
    hidden, verified = [], []
    for ticket in data["key"]["ticket"]:
        for fact in ticket["facts"]:
            if "hidden" not in fact:
                continue
            tool, path = fact["hidden"]
            hidden.append(f"{ticket['id']}:{fact['kind']}:{path}")
            ok = any(name == tool and any(p == path and matches(fact, v) for p, v in orig)
                     and not any(p == path for p, _ in sent) for name, orig, sent in seen)
            if ok:
                verified.append(hidden[-1])
    return {"task": task, "hidden_facts": len(hidden), "verified": len(verified),
            "unverified": sorted(set(hidden) - set(verified)), "calls": len(seen)}


async def reference(task):
    rows = {}

    async def call(tool, **args):
        out = await tools.call(tool, args)
        if tools.last_is_error or out.startswith("Error") or '"error"' in out[:20]:
            raise ValueError(f"{task}: failed reference call {tool}")
        rows[tool, json.dumps(args, sort_keys=True)] = out
        return json.loads(out)

    async with TaskTools(task) as tools:
        for _, args in typical_requests(task):
            for c in (await call("stripe_search_customers", **args))["data"]:
                for inv in (await call("stripe_list_invoices", customer=c["id"]))["data"]:
                    await call("stripe_retrieve_invoice", invoice=inv["id"])
                for ch in (await call("stripe_list_charges", customer=c["id"]))["data"]:
                    await call("stripe_retrieve_charge", charge=ch["id"])
                    await call("stripe_list_refunds", charge=ch["id"])
                await call("stripe_list_subscriptions", customer=c["id"])
    write(DATA / "reference" / f"{task}.json",
          [{"tool": t, "arguments": json.loads(a), "output": rows[t, a]} for t, a in sorted(rows)])
    return len(rows)


async def main_async(with_reference):
    results = []
    for i in range(1, CANDIDATES + 1):
        task = f"C4b-{i:02}"
        r = await check(task)
        if with_reference:
            r["reference_calls"] = await reference(task)
        results.append(r)
        print(json.dumps(r))
    bad = [r["task"] for r in results if not r["verified"] or r["unverified"]]
    print(f"tasks: {len(results)}; with at least one verified hidden fact: "
          f"{sum(r['verified'] > 0 for r in results)}; hidden facts {sum(r['hidden_facts'] for r in results)}, "
          f"verified {sum(r['verified'] for r in results)}; tasks with an unverified hidden fact: {bad}")
    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reference", action="store_true")
    asyncio.run(main_async(p.parse_args().reference))
