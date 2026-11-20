"""The paid campaign of protocol-v3.md: the 120 frozen slots with a real model (PAID).

  python -m experiments.v3.live status          # slots done, attempts, missing; no spend
  python -m experiments.v3.live next [N]        # run the next N slots not done, in the frozen order (PAID)
  python -m experiments.v3.live slot C4-05/1/I  # one slot (PAID)

Same loop as the study (experiments/run.py and experiments/v04.py: Responses API,
function calling, stateful, default inference settings), on the immutable task
worlds of the original simulated servers (experiments/v3/tools.py). Arms R, P, I as
in the dry run (experiments/v3/dry.py): P and I get a fresh engine with the frozen
policy, the `expand` tool and the base recovery instructions.

Everything a run writes is private and git-ignored, under out/v3/:
  traces/TASK/REP/ARM.jsonl       the final trace the coordinator packs
  traces/TASK/REP/ARM.meta.json   model returned, timing, usage, tokens, exposure, gates, cost
  attempts/TASK/REP/ARM.N.json    an attempt that failed for infrastructure reasons, with its reason
  ledger.jsonl                    one line per provider attempt; the caps read it
  missing.json                    a slot whose two attempts both failed (protocol: missing, not an outcome)
  warning-reviewed                written by hand once the spend at the warning has been reviewed

Caps: `caps("v3")` from experiments/caps.local.json. At `stop` nothing new starts and
a running slot is cut short; at `warn` nothing new starts until the review file exists.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from tracetrim import skill
from tracetrim.contract import FULL
from tracetrim.engine import Engine
from tracetrim.measure import tokenizer
from tracetrim.runtime import Policy

from experiments.caps import caps
from experiments.paths import PRICES, PROMPTS, cost
from experiments.tools import to_args
from experiments.v04 import (CLIENT_ERROR, EXPAND_ERROR, OK, TOOL_ERROR, Counted, Trimmed, schema_records,
                             text_of)
from . import coordinator
from .tools import TaskTools, task_data
from .worlds import DATA

PROTOCOL = "protocol-v3.md"
GUARD_CALLS = 80                 # the study's guards (protocol-v3.md, "Stopping")
GUARD_TURNS = 40
MAX_ATTEMPTS = 2                 # one retry of an infrastructure failure, under a new attempt id
LONG_CONTEXT = 272_000           # above this many input tokens the provider bills a higher tier (prices.json)


class Infrastructure(Exception):
    """A failure of the provider or of the local tools: the slot may be retried once."""


class Budget(SystemExit):
    """A cap stops the campaign: nothing new starts."""


class Where:
    def __init__(self, root: Path = coordinator.PRIVATE):
        self.root = Path(root)
        self.traces = self.root / "traces"
        self.attempts = self.root / "attempts"
        self.ledger = self.root / "ledger.jsonl"
        self.missing = self.root / "missing.json"
        self.reviewed = self.root / "warning-reviewed"

    def trace(self, slot: str) -> Path:
        task, rep, arm = slot.split("/")
        return self.traces / task / rep / f"{arm}.jsonl"


def lines(where: Where) -> list[dict]:
    if not where.ledger.exists():
        return []
    return [json.loads(x) for x in where.ledger.read_text().splitlines() if x.strip()]


def spent(where: Where) -> float:
    return sum(e["cost_usd"] for e in lines(where))


def missing(where: Where) -> dict:
    return json.loads(where.missing.read_text()) if where.missing.exists() else {}


def attempts(where: Where, slot: str) -> int:
    return sum(1 for e in lines(where) if e["slot"] == slot)


def done(where: Where, slot: str) -> bool:
    return where.trace(slot).exists() or slot in missing(where)


def may_start(where: Where, limits: tuple[float, float] | None = None) -> float:
    """The caps, before every slot. Returns the spend so far."""
    warn, stop = limits or caps("v3")
    if stop <= 0:
        raise Budget("no v3 caps in experiments/caps.local.json: paid runs are refused")
    total = spent(where)
    if total >= stop:
        raise Budget(f"stop rule: the v3 ledger is at or above the stop cap ({total:.2f} USD)")
    if total >= warn and not where.reviewed.exists():
        raise Budget(f"warning: the v3 ledger is at or above the warning ({total:.2f} USD). Report the spend "
                     f"and the slots left; record the review in {where.reviewed} before going on.")
    return total


def instructions(arm: str, task: str) -> tuple[str, str]:
    """(system prompt with the arm's addition, the addition). R adds nothing."""
    system = (PROMPTS / f"{task[:2].lower()}-system.md").read_text(encoding="utf-8")
    added = "" if arm == "R" else skill.text(strategy=False, done=False)
    return system + ("\n\n" + added if added else ""), added


def engine_for(arm: str, task: str) -> Engine | None:
    if arm == "R":
        return None
    return Engine(policy=Policy.load(DATA / "policies" / task[:2] / arm / "config.json"), breakdown=False)


def _append(path: Path, line: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


async def attempt(slot: str, where: Where, client, model: str, limits=None, tools_factory=TaskTools) -> dict:
    """One provider attempt of one slot. Writes the final trace and meta, or raises
    Infrastructure (after recording the attempt), or Budget."""
    before = may_start(where, limits)
    stop = (limits or caps("v3"))[1]
    task, rep, arm = slot.split("/")
    n = attempts(where, slot) + 1
    trace_path = where.trace(slot)
    if trace_path.exists():
        raise SystemExit(f"{trace_path} exists: a completed slot is never run again")
    count = tokenizer()
    system, added = instructions(arm, task)
    user = task_data(task)["prompt"]
    engine = engine_for(arm, task)
    events = [{"kind": "prompt", "text": system, "turn": -1}, {"kind": "prompt", "text": user, "turn": -1}]
    turns, flags, total = [], [], {"input": 0, "cached": 0, "output": 0}
    tokens = {"tool_definitions": 0, "instructions_added": count(added), "outputs_original": 0,
              "outputs_sent": {}, "expand_replies": 0, "final_answer": 0}
    exposure, outcomes, returned = {}, {}, set()
    n_calls = n_expand = 0
    t0 = time.time()
    stage = "model"
    try:
        stage = "tools"
        async with tools_factory(task) as base:
            counted = Counted(base)
            tools = Trimmed(counted, engine) if engine is not None else counted
            tokens["tool_definitions"] = count(json.dumps(tools.schemas, ensure_ascii=False))
            schemas = schema_records(tools.schemas)
            kwargs = {"model": model, "instructions": system, "tools": tools.schemas,
                      "input": [{"role": "user", "content": user}]}
            while True:
                stage = "model"
                r = await client.responses.create(**kwargs)
                stage = "tools"
                returned.add(getattr(r, "model", None))
                u = r.usage
                turn = {"input": u.input_tokens, "output": u.output_tokens,
                        "cached": getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0}
                for k in total:
                    total[k] += turn[k]
                if turn["input"] > LONG_CONTEXT:
                    flags.append("long_context_price")
                pending = []
                for item in r.output:
                    if item.type == "message":
                        events.append({"kind": "message", "text": text_of(item), "turn": len(turns)})
                    elif item.type == "function_call":
                        pending.append(item)
                if engine is not None:
                    tools.turn = len(turns)
                outputs = []
                for fc in pending:
                    args = to_args(fc.arguments)
                    failed_before = len(getattr(base, "tool_failures", []))
                    sent = await tools.call(fc.name, args)
                    e = {"kind": "call", "tool": fc.name, "arguments": fc.arguments, "output": sent, "turn": len(turns)}
                    if fc.name == "expand":
                        n_expand += 1
                        tokens["expand_replies"] += count(sent)
                        how = "expand"
                        e["outcome"] = EXPAND_ERROR if sent.startswith("Error") else OK
                    else:
                        n_calls += 1
                        obs = getattr(tools, "last", None)
                        how = obs.exposure if obs is not None else FULL
                        original = tools.original if engine is not None else sent
                        e["outcome"] = (CLIENT_ERROR if len(getattr(base, "tool_failures", [])) > failed_before
                                        else TOOL_ERROR if getattr(base, "last_is_error", False) else OK)
                        tokens["outputs_original"] += count(original)
                        tokens["outputs_sent"][how] = tokens["outputs_sent"].get(how, 0) + count(sent)
                        exposure[how] = exposure.get(how, 0) + 1
                        if original != sent:
                            e.update(output=original, sent=sent)
                        e["exposure"] = how
                    outcomes[e["outcome"]] = outcomes.get(e["outcome"], 0) + 1
                    events.append(e)
                    outputs.append({"type": "function_call_output", "call_id": fc.call_id, "output": sent})
                turn["calls"] = len(pending)
                turns.append(turn)
                run_cost = cost(total)
                print(f"  {slot} turn {len(turns):>2}: {len(pending):>2} calls · input {turn['input']:>7,} "
                      f"· output {turn['output']:>6,}", flush=True)
                if not pending:
                    break
                if before + run_cost >= stop:
                    raise Budget("stop rule reached during a slot: the slot is cut short and left not done")
                if n_calls >= GUARD_CALLS or len(turns) >= GUARD_TURNS:
                    flags.append("safety_guard")          # an outcome, not a failure (protocol-v3.md)
                    break
                kwargs = {"model": model, "instructions": system, "tools": tools.schemas,
                          "previous_response_id": r.id, "input": outputs}
            failures, retries = getattr(base, "tool_failures", []), getattr(base, "retries", 0)
    except BaseException as exc:
        # Every provider attempt is in the ledger, whatever ended it: the caps must see its tokens.
        reason = f"{type(exc).__name__} in {stage}: {str(exc)[:300]}"
        line = {"slot": slot, "attempt": n, "model": model, "aborted": True, "reason": reason,
                "cost_usd": round(cost(total), 6), "usage": total, "turns": len(turns),
                "started": datetime.fromtimestamp(t0, timezone.utc).isoformat(timespec="seconds")}
        _append(where.ledger, line)
        partial = where.attempts / task / rep / f"{arm}.{n}.json"
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_text(json.dumps(line | {"events": events, "traceback": traceback.format_exc()},
                                      indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        if isinstance(exc, (Budget, KeyboardInterrupt, asyncio.CancelledError)):
            raise
        raise Infrastructure(reason) from exc

    messages = [e["text"] for e in events if e["kind"] == "message"]
    answer = messages[-1] if messages else ""
    tokens["final_answer"] = count(answer)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    meta = {
        "protocol": PROTOCOL, "slot": slot, "task": task, "repetition": int(rep), "arm": arm, "attempt": n,
        "model": model, "model_returned": sorted(m for m in returned if m),
        "started": datetime.fromtimestamp(t0, timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.time() - t0, 1),
        "skill": {"version": skill.VERSION, "tokens": count(added)} if added else None,
        "tools_trimmed": sorted(engine.policy.tools) if engine is not None else [],
        "turns": len(turns), "calls": n_calls, "expand_calls": n_expand, "usage": total, "usage_per_turn": turns,
        "tokens": tokens, "exposure": exposure, "outcomes": outcomes,
        "tool_schemas": {s["name"]: s["sha256"] for s in schemas},
        "tool_failures": failures, "retries": retries, "flags": flags + (["tool_failure"] if failures else []),
        "cost_usd": round(cost(total), 6), "prices": "experiments/prices.json",
    }
    trace_path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
                                                    encoding="utf-8")
    _append(where.ledger, {k: meta[k] for k in ("slot", "attempt", "model", "started", "cost_usd", "usage", "turns")})
    return meta


async def run_slot(slot: str, where: Where, client, model: str, limits=None, tools_factory=TaskTools) -> dict | None:
    """A slot with at most one retry of an infrastructure failure; two failures make it missing."""
    while True:
        if done(where, slot):
            return None
        if attempts(where, slot) >= MAX_ATTEMPTS:
            gone = missing(where)
            gone[slot] = "; ".join(e["reason"] for e in lines(where) if e["slot"] == slot and e.get("aborted"))
            where.missing.write_text(json.dumps(gone, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"  {slot}: missing after {MAX_ATTEMPTS} attempts", flush=True)
            return None
        try:
            return await attempt(slot, where, client, model, limits, tools_factory)
        except Infrastructure as exc:
            print(f"  {slot}: infrastructure failure, attempt {attempts(where, slot)}: {exc}", flush=True)


def pending(where: Where) -> list[str]:
    return [s for s in coordinator.schedule() if not done(where, s)]


async def campaign(where: Where, client, model: str, limit: int | None = None, limits=None,
                   tools_factory=TaskTools, slots: list[str] | None = None) -> list[dict]:
    todo = slots if slots is not None else pending(where)
    out = []
    for slot in todo[:limit] if limit is not None else todo:
        meta = await run_slot(slot, where, client, model, limits, tools_factory)
        if meta is not None:
            out.append(meta)
            print(f"  {slot}: {meta['turns']} turns, {meta['calls']} calls, {meta['expand_calls']} expand, "
                  f"flags {meta['flags']}", flush=True)
    return out


def status(where: Where) -> dict:
    schedule = coordinator.schedule()
    return {"slots": len(schedule), "done": sum(where.trace(s).exists() for s in schedule),
            "missing": len(missing(where)), "attempts": len(lines(where)),
            "failed_attempts": sum(1 for e in lines(where) if e.get("aborted")),
            "next": next(iter(pending(where)), None)}


# The files added after the freeze, by ORDINE-12b. The lock's manifest takes every *.py of
# experiments/v3, so `freeze --verify` would refuse them; the check below asks instead that
# every frozen file is unchanged and that nothing else was added (deviation in REPORT-12b).
ADDED = {"experiments/v3/live.py", "experiments/v3/analysis.py", "experiments/v3/judging.py"}


def verify_frozen() -> int:
    from .freeze import manifest
    from experiments.paths import STUDY_TRACES
    expected, actual = coordinator.load(coordinator.LOCK), manifest()
    if actual["anonymous_mapping_sha256"] != expected["anonymous_mapping_sha256"]:
        raise ValueError("private mapping differs from the lock")
    changed = [f for f, h in expected["files"].items() if actual["files"].get(f) != h]
    added = set(actual["files"]) - set(expected["files"])
    if changed or not added <= ADDED:
        raise ValueError(f"frozen materials differ: changed {changed}, added {sorted(added - ADDED)}")
    training = coordinator.load(DATA / "training.json")
    if any(coordinator.digest(STUDY_TRACES / name) != value for name, value in training.items()):
        raise ValueError("Phase A inputs differ")
    return len(expected["files"])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    n = sub.add_parser("next")
    n.add_argument("count", nargs="?", type=int, default=1)
    s = sub.add_parser("slot")
    s.add_argument("slot")
    a = p.parse_args(argv)
    where = Where()
    if a.command == "status":
        print(json.dumps(status(where), indent=2))
        return
    verify_frozen()              # the lock, the private mapping and the training inputs, before any spend
    may_start(where)
    from openai import AsyncOpenAI
    client = AsyncOpenAI(timeout=900.0, max_retries=0)      # every provider attempt is the loop's own
    slots = [a.slot] if a.command == "slot" else None
    if slots and slots[0] not in coordinator.schedule():
        sys.exit("not a slot of the frozen schedule")
    try:
        asyncio.run(campaign(where, client, PRICES["model"], a.count if a.command == "next" else None,
                             slots=slots))
    finally:
        print(json.dumps(status(where), indent=2))


if __name__ == "__main__":
    main()
