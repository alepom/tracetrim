#!/usr/bin/env python3
"""The runs of protocol-v04-e3.md: the skill's strategy on a Gemini model (E3).

  python -m experiments.run --e3 schedule           # every run, in order, and which are done
  python -m experiments.run --e3 next               # the next run not done yet (PAID: the model only)
  python -m experiments.run --e3 pilot              # what the pilot showed, and whether the runs may go on
  python -m experiments.run --e3 dry-run DIR        # every arm and case with a stub in place of the model: no spend
  python -m experiments.run --e3 estimate [--json]  # the cost estimate, from E1's measured runs
  python -m experiments.run --e3 check              # can the service account use the model? (not billed)

The loop, the traces and the meta files are those of experiments/v04.py. What E3 has of its own:

  model     Gemini on Vertex AI, through experiments/gemini.py
  tools     C4 and C5: the simulated servers, live. C1 and C2: outputs recorded
            in earlier runs (experiments/recorded.py): the provider is never called
  arms      R (no text) and S (the strategy alone). Nothing is trimmed
  budget    its own ledger (out/ledger-e3.jsonl), prices (experiments/prices-e3.json), cap and warning
  pilot     two runs of R on each simulated case, first. The runs then stop, always,
            for a decision: the pilot gives the real cost of a run, the share of
            turns with several calls, and any limit of the project's quota
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from statistics import median

from .caps import caps
from .paths import PROMPTS, ROOT, STUDY_TRACES
from .v04 import HERE, Run, Where, execute, ledger, spent, stub_for

PRICES = json.loads((ROOT / "experiments" / "prices-e3.json").read_text())
WARN, CAP = caps("e3")           # local, never published: experiments/caps.py
ARMS = ("R", "S")
# Runs per arm. More on the simulated cases, whose runs are cheap; fewer on the two cases
# with recorded outputs, whose long tool outputs are re-read at every turn.
RUNS = {"C4": 8, "C5": 6, "C2": 3, "C1": 2}
# The simulated cases first, to their end, then the cases with recorded outputs: if the cap
# stops the runs, it takes them from the cases that say less (only how calls are organised).
ORDER = ("C4", "C5", "C2", "C1")
BLOCKS = (("C4", "C5"), ("C2", "C1"))
PILOT_CASES, PILOT_RUNS = ("C4", "C5"), 2
# The pilot's question: does the model already issue several calls in most of the turns that make a
# call? Reported with the pilot; the runs stop after it whatever the answer.
PILOT_MARGIN_SHARE = 0.5


def ledger_path(where: Where) -> Path:
    return where.root / "out" / "ledger-e3.jsonl"


def pilot_decision(where: Where) -> Path:           # written by hand after the pilot: the author's decision
    return where.root / "out" / "e3" / "pilot_decision.json"


def cost(usage: dict) -> float:
    cached = usage.get("cached", 0)
    return ((usage["input"] - cached) * PRICES["input_per_mtok"] + cached * PRICES["cached_input_per_mtok"]
            + usage["output"] * PRICES["output_per_mtok"]) / 1e6


def book(where: Where) -> dict:
    return {"ledger": ledger_path(where), "cap": CAP, "cost": cost, "model": PRICES["model"],
            "prices": "experiments/prices-e3.json", "may_start": may_start}


def schedule() -> list[Run]:
    """The pilot; then the simulated cases in rounds (one run of each arm of each, and
    again, so that a drift of the service hits the two arms alike) to their end; then
    the cases with recorded outputs, in rounds too."""
    runs = [Run("E3", c, "R", n, pilot=True) for c in PILOT_CASES for n in range(1, PILOT_RUNS + 1)]
    for block in BLOCKS:
        for n in range(1, max(RUNS[c] for c in block) + 1):
            runs += [Run("E3", c, arm, n) for c in block if n <= RUNS[c] for arm in ARMS]
    return runs


def done(where: Where, run: Run) -> bool:
    return (where.traces / run.case / f"{run.id}.meta.json").exists()


def pilot(where: Where) -> dict:
    """What the pilot runs showed about calls issued together."""
    runs = [r for r in schedule() if r.pilot]
    metas = [json.loads((where.traces / r.case / f"{r.id}.meta.json").read_text()) for r in runs if done(where, r)]
    per_turn = [t["calls"] for m in metas for t in m["usage_per_turn"] if t["calls"]]
    share = sum(n > 1 for n in per_turn) / len(per_turn) if per_turn else None
    return {"runs_done": len(metas), "runs": len(runs), "turns_with_calls": len(per_turn),
            "share_of_turns_with_several_calls": round(share, 4) if share is not None else None,
            "calls_per_turn_median": median(per_turn) if per_turn else None,
            "cost_usd": round(sum(m.get("cost_usd", 0) for m in metas), 4),
            "complete": len(metas) == len(runs),
            # Little for the strategy to organise: the model already issues several calls in most turns.
            "little_margin": bool(share is not None and share > PILOT_MARGIN_SHARE)}


def may_start(where: Where, run: Run) -> str | None:
    """Refuse (SystemExit) a run the stop rules of E3 forbid; return a warning to print, if any."""
    total = spent(where, ledger_path(where))
    if total >= CAP:
        raise SystemExit(f"stop rule: {total:.2f} USD already spent on E3, cap {CAP:.0f}")
    if not run.pilot:
        p = pilot(where)
        if not p["complete"]:
            raise SystemExit("E3: the pilot comes first (python -m experiments.run --e3 next)")
        if not pilot_decision(where).exists():
            share = p["share_of_turns_with_several_calls"]
            raise SystemExit(f"E3 pilot: done. Several calls in {share:.0%} of the turns that make a call"
                             + (f" (above {PILOT_MARGIN_SHARE:.0%}: little for the strategy to organise)"
                                if p["little_margin"] else "")
                             + f". Report the pilot, and have the author's decision recorded in "
                               f"{pilot_decision(where)} before going on.")
    return f"WARNING: {total:.2f} USD spent on E3, warning at {WARN:.0f}, cap {CAP:.0f}" if total >= WARN else None


# -- the estimate ------------------------------------------------------------------

def estimate() -> dict:
    """What E3 may cost, from the runs of `gpt-5.6-terra` already measured, at Gemini's prices.

    Two figures per run. `as_measured`: the same turns and tokens as the other
    model's raw runs. `one_call_per_turn`: the case E3 is run for, a model that
    makes its calls one at a time, so that the conversation is re-read once per
    call; the tool outputs are assumed to arrive evenly along the run. Neither
    takes a cache discount. Output tokens are doubled, an allowance for thinking
    tokens, which this model bills as output. Tokens are those of the other
    model's tokenizer: Gemini's differ."""
    from tracetrim.measure import tokenizer
    count = tokenizer()
    e1 = json.loads((ROOT / "results" / "v04" / "e1.json").read_text())["per_run"]
    per = {}
    for c in ORDER:
        rows = [r for r in e1 if r["case"] == c and r["arm"] == "R"]
        if rows:                                   # measured in E1
            m = {"calls": median(r["calls"] for r in rows), "input": median(r["billed"]["input"] for r in rows),
                 "output": median(r["billed"]["output"] for r in rows),
                 "tool_outputs": median(r["tokens"]["outputs_original"] for r in rows),
                 "tool_definitions": median(r["tokens"]["tool_definitions"] for r in rows), "source": "E1, arm R"}
        else:                                      # C5 was not in E1: the original study's Phase A runs
            metas = [json.loads(p.read_text()) for p in sorted((STUDY_TRACES / c).glob("A*.meta.json"))]
            if not metas:
                raise SystemExit(f"{c}: the original study's runs are needed ($TRACETRIM_TRACES)")
            outs = []
            for p in sorted((STUDY_TRACES / c).glob("A*.jsonl")):
                es = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
                outs.append(sum(count(e.get("output") or "") for e in es if e["kind"] == "call"))
            m = {"calls": median(x["calls"] for x in metas), "input": median(x["usage"]["input"] for x in metas),
                 "output": median(x["usage"]["output"] for x in metas), "tool_outputs": median(outs),
                 "tool_definitions": 700, "source": "original study, Phase A (tool definitions assumed 700 tokens)"}
        prompt = sum(count((PROMPTS / f"{c.lower()}-{k}.md").read_text()) for k in ("system", "user"))
        base = prompt + m["tool_definitions"]
        turns = m["calls"] + 1
        sequential_input = turns * base + m["tool_outputs"] * turns / 2
        out_usd = 2 * m["output"] * PRICES["output_per_mtok"] / 1e6
        per[c] = {**m, "prompt_tokens": prompt,
                  "usd_as_measured": round(m["input"] * PRICES["input_per_mtok"] / 1e6 + out_usd, 4),
                  "usd_one_call_per_turn": round(sequential_input * PRICES["input_per_mtok"] / 1e6 + out_usd, 4),
                  "input_one_call_per_turn": round(sequential_input)}
    strategy = 549                                  # tokens the strategy adds to the system prompt (experiments.skill_tokens)
    lines, low, high = [], 0.0, 0.0
    for c in ORDER:
        for arm in ARMS:
            n = RUNS[c] + (PILOT_RUNS if arm == "R" and c in PILOT_CASES else 0)
            extra = strategy * (per[c]["calls"] + 1) * PRICES["input_per_mtok"] / 1e6 if arm == "S" else 0
            a, b = n * (per[c]["usd_as_measured"] + extra), n * (per[c]["usd_one_call_per_turn"] + extra)
            lines.append({"case": c, "arm": arm, "runs": n, "usd_as_measured": round(a, 2), "usd_one_call_per_turn": round(b, 2)})
            low, high = low + a, high + b
    return {"prices": {k: PRICES[k] for k in ("model", "location", "input_per_mtok", "cached_input_per_mtok", "output_per_mtok")},
            "per_run": per, "design": {"runs_per_arm": RUNS, "pilot": {c: PILOT_RUNS for c in PILOT_CASES}, "arms": list(ARMS)},
            "lines": lines, "runs": sum(x["runs"] for x in lines),
            "usd_as_measured": round(low, 2), "usd_one_call_per_turn": round(high, 2), "cap_usd": CAP, "warn_usd": WARN}


def estimate_after_pilot(where: Where) -> dict:
    """The estimate of the runs still to make, corrected with what the pilot cost.

    On the cases of the pilot (C4, C5) a run is costed at the pilot's mean. The
    other two have no pilot: two figures, from how far the pilot fell from the
    earlier estimate. `low`: they behave like the pilot case that issued calls
    together, costed against the "as measured" figure. `high`: like the pilot
    case that made one call per turn, against the "one call per turn" figure."""
    before = estimate()["per_run"]
    cost_of = {}
    for c in PILOT_CASES:
        metas = [json.loads(p.read_text()) for p in sorted((where.traces / c).glob("E3-pilot-*.meta.json"))]
        if not metas:
            raise SystemExit("the pilot has not been run")
        per = [t["calls"] for m in metas for t in m["usage_per_turn"] if t["calls"]]
        cost_of[c] = {"usd": sum(m["cost_usd"] for m in metas) / len(metas),
                      "share_several": sum(n > 1 for n in per) / len(per) if per else 0.0,
                      "cached_share": sum(m["usage"]["cached"] for m in metas) / max(1, sum(m["usage"]["input"] for m in metas))}
    together = max(cost_of, key=lambda c: cost_of[c]["share_several"])       # the pilot case with more calls issued together
    one_by_one = min(cost_of, key=lambda c: cost_of[c]["share_several"])
    low_factor = cost_of[together]["usd"] / before[together]["usd_as_measured"]
    high_factor = cost_of[one_by_one]["usd"] / before[one_by_one]["usd_one_call_per_turn"]
    lines, low, high = [], 0.0, 0.0
    for c in ORDER:
        n = 2 * RUNS[c]
        if c in cost_of:
            a = b = n * cost_of[c]["usd"]
        else:
            a, b = n * before[c]["usd_as_measured"] * low_factor, n * before[c]["usd_one_call_per_turn"] * high_factor
        lines.append({"case": c, "runs": n, "usd_low": round(a, 2), "usd_high": round(b, 2)})
        low, high = low + a, high + b
    spent_now = spent(where, ledger_path(where))
    return {"pilot": {c: {k: round(v, 4) for k, v in x.items()} for c, x in cost_of.items()},
            "factors": {"low": round(low_factor, 3), "from": together, "high": round(high_factor, 3), "from_": one_by_one},
            "lines": lines, "runs_left": sum(x["runs"] for x in lines), "spent_usd": round(spent_now, 2),
            "left_usd_low": round(low, 2), "left_usd_high": round(high, 2),
            "total_usd_low": round(spent_now + low, 2), "total_usd_high": round(spent_now + high, 2),
            "cap_usd": CAP, "warn_usd": WARN}


# -- dry run -------------------------------------------------------------------------

async def dry_run(directory: str | Path) -> list[dict]:
    """The pilot and one run of every arm of every case, with the stub. Nothing is spent,
    no model and no provider is reached."""
    where = Where(Path(directory))
    if where.root.exists() and any(where.root.iterdir()):
        raise SystemExit(f"{where.root} is not empty: a dry run starts from an empty directory")
    metas = []
    for run in schedule():
        if not run.pilot and run.n != 1:
            continue
        print(f"{run.case} {run.id} (dry run)", flush=True)
        try:
            metas.append(await execute(run, where, client=stub_for(run), model="stub", dry=True))
        except SystemExit as stop:
            if "E3 pilot" not in str(stop) or pilot_decision(where).exists():
                raise
            # The stop after the pilot fired, as it always must. In a dry run the "decision" is recorded here.
            print(f"  stopped: {str(stop)[:86]}...", flush=True)
            pilot_decision(where).parent.mkdir(parents=True, exist_ok=True)
            pilot_decision(where).write_text(json.dumps({"dry_run": True, "pilot": pilot(where)}) + "\n")
            metas.append(await execute(run, where, client=stub_for(run), model="stub", dry=True))
    return metas


def main(argv=None):
    p = argparse.ArgumentParser(prog="experiments.run --e3", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("schedule", "next", "pilot", "check"):
        sub.add_parser(name)
    s = sub.add_parser("dry-run")
    s.add_argument("directory")
    s = sub.add_parser("estimate")
    s.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    runs = schedule()
    if a.cmd == "schedule":
        for n, r in enumerate(runs, 1):
            print(f"{n:>4}  {r.case}  {r.id:<16}{'pilot' if r.pilot else '':<8}{'done' if done(HERE, r) else ''}")
        print(f"\n{len(runs)} runs · spent {spent(HERE, ledger_path(HERE)):.2f} USD of {CAP:.0f} (warning at {WARN:.0f}) · "
              f"provider calls: {sum(e.get('dataforseo_calls', 0) for e in ledger(HERE, ledger_path(HERE)))}")
    elif a.cmd == "pilot":
        print(json.dumps(pilot(HERE), indent=2))
        if pilot(HERE)["complete"]:
            print(json.dumps(estimate_after_pilot(HERE), indent=2))
    elif a.cmd == "check":
        from .gemini import access
        r = access(PRICES["model"])
        print(json.dumps(r, indent=2))
        if not r["accepted"]:
            raise SystemExit("the service account cannot use the model: nothing can be run")
    elif a.cmd == "dry-run":
        metas = asyncio.run(dry_run(a.directory))
        print(f"\n{len(metas)} dry runs in {a.directory}: nothing spent")
    elif a.cmd == "estimate":
        r = estimate()
        if a.json:
            print(json.dumps(r, indent=2))
            return
        print(f"\n  {r['prices']['model']} on Vertex AI ({r['prices']['location']}): input {r['prices']['input_per_mtok']}, "
              f"cached {r['prices']['cached_input_per_mtok']}, output {r['prices']['output_per_mtok']} USD per 1M tokens")
        print(f"\n  {'case':<6}{'calls':>6}{'USD/run as measured':>22}{'one call per turn':>20}   source")
        for c, m in r["per_run"].items():
            print(f"  {c:<6}{m['calls']:>6g}{m['usd_as_measured']:>22.3f}{m['usd_one_call_per_turn']:>20.3f}   {m['source']}")
        print(f"\n  {'case':<6}{'arm':<5}{'runs':>5}{'as measured':>14}{'one call per turn':>20}")
        for x in r["lines"]:
            print(f"  {x['case']:<6}{x['arm']:<5}{x['runs']:>5}{x['usd_as_measured']:>14.2f}{x['usd_one_call_per_turn']:>20.2f}")
        print(f"\n  {r['runs']} runs: {r['usd_as_measured']:.2f} USD if the model works like the one already measured, "
              f"{r['usd_one_call_per_turn']:.2f} if it makes one call per turn   (cap {CAP:.0f}, warning at {WARN:.0f})\n")
    else:
        run = next((r for r in runs if not done(HERE, r)), None)
        if run is None:
            raise SystemExit("every run of E3 is done")
        from .gemini import access
        r = access(PRICES["model"])
        if not r["accepted"]:               # before anything is spent or written
            raise SystemExit(f"E3: the service account cannot use {PRICES['model']} ({r['error']}). "
                             "See `python -m experiments.run --e3 check`.")
        m = asyncio.run(execute(run))
        print(f"\n  {m['case']} {m['run']}: {m['turns']} turns, {m['calls']} calls, {m['cost_usd']:.3f} USD, flags {m['flags']}"
              + (f", replay {m['replay']}" if m["replay"] else ""))


if __name__ == "__main__":
    main()
