"""Calibration, selection and campaign of protocol-v3b.md, on the loop of protocol v3 (PAID).

  python -m experiments.v3b.run calibrate      # R once per candidate task (PAID)
  python -m experiments.v3b.run select         # the preregistered selection rule and budget rule; writes
                                               # material/selection.json and material/schedule.json
  python -m experiments.v3b.run campaign [N]   # the next N slots of the frozen schedule (PAID)
  python -m experiments.v3b.run status

Private and git-ignored: out/v3b/calibration/ (traces, ledger, scores) and out/v3b/ (campaign traces,
ledger). Every paid step checks the private v3 caps against the spend of protocol v3, the calibration,
the campaign and the judge together.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import subprocess

from experiments.caps import caps
from experiments.paths import PRICES, ROOT
from experiments.v3 import coordinator, live

from . import facts
from .tools import TaskTools, task_data
from .worlds import CANDIDATES, DATA, write

PRIVATE = ROOT / "out/v3b"
CALIBRATION = PRIVATE / "calibration"
V3 = coordinator.PRIVATE
LEDGERS = {"v3_campaign": V3 / "ledger.jsonl", "v3_judge": V3 / "judge-ledger.jsonl",
           "calibration": CALIBRATION / "ledger.jsonl", "campaign": PRIVATE / "ledger.jsonl",
           "judge": PRIVATE / "judge-ledger.jsonl"}
SCHEDULE_SEED = 130422
REPETITIONS = 3
LOW, HIGH, TARGET = 0.40, 0.90, 0.65
KEEP_MAX, KEEP_MIN = 12, 10
V3_JUDGE_TOKENS = 806_602 / 20          # mean o200k input tokens of a v3 judge group (REPORT-12c)


def where(root):
    """A live.Where whose caps count every other v3 ledger too."""
    own = root / "ledger.jsonl"
    return live.Where(root, also=tuple(p for p in LEDGERS.values() if p != own))


def committed():
    """The protocol and the v3b material must be committed before any paid run."""
    r = subprocess.run(["git", "status", "--porcelain", "--", "protocol-v3b.md", "experiments/v3b"],
                       cwd=ROOT, capture_output=True, text=True)
    if r.stdout.strip():
        raise SystemExit("protocol-v3b.md or experiments/v3b has uncommitted changes: commit before a paid run")


def candidates():
    return [f"C4b-{i:02}" for i in range(1, CANDIDATES + 1)]


def calibration_scores() -> dict:
    w = where(CALIBRATION)
    out = {}
    for task in candidates():
        trace = w.trace(f"{task}/0/R")
        if trace.exists():
            out[task] = facts.score_trace(trace, task_data(task)["key"])
    return out


def rule(shares: dict[str, float]) -> tuple[list[str], list[str]]:
    """(eligible, kept) by the preregistered selection rule."""
    eligible = sorted(t for t, s in shares.items() if LOW <= s <= HIGH)
    ranked = sorted(eligible, key=lambda t: (round(abs(shares[t] - TARGET), 9), t))   # rounded: exact ties go by task number
    return eligible, sorted(ranked[:KEEP_MAX])


def schedule(tasks: list[str], reps: int) -> list[str]:
    rng = random.Random(SCHEDULE_SEED)
    blocks = [(t, r) for t in tasks for r in range(1, reps + 1)]
    rng.shuffle(blocks)
    orders = [("R", "P"), ("P", "R")] * ((len(blocks) + 1) // 2)
    orders = orders[:len(blocks)]
    rng.shuffle(orders)
    return [f"{t}/{r}/{arm}" for (t, r), order in zip(blocks, orders) for arm in order]


def estimate(tasks: list[str], reps: int, mean_run: float, mean_answer_tokens: float) -> dict:
    from tracetrim.measure import tokenizer
    count = tokenizer()
    spent_now = sum(sum(json.loads(x)["cost_usd"] for x in p.read_text().splitlines() if x.strip())
                    for p in LEDGERS.values() if p.exists())
    campaign = len(tasks) * reps * 2 * mean_run
    v3_call = sum(json.loads(x)["cost_usd"] for x in LEDGERS["v3_judge"].read_text().splitlines() if x.strip()) \
        / sum(1 for x in LEDGERS["v3_judge"].read_text().splitlines() if x.strip())
    judge = 0.0
    for t in tasks:
        ev = count(json.dumps(json.loads((DATA / "reference" / f"{t}.json").read_text()), ensure_ascii=False))
        group = ev + 2 * reps * mean_answer_tokens + 1500
        judge += 3 * v3_call * group / V3_JUDGE_TOKENS       # two passes and, at worst, a third reading of every task
    return {"spent": spent_now, "campaign": campaign, "judge_upper": judge, "total": spent_now + campaign + judge}


def select():
    scores = calibration_scores()
    if set(scores) != set(candidates()):
        raise SystemExit(f"calibration incomplete: {len(scores)} of {CANDIDATES} scored")
    shares = {t: s["share"] for t, s in scores.items()}
    eligible, kept = rule(shares)
    result = {"rule": f"{LOW} <= s <= {HIGH}; at most {KEEP_MAX} closest to {TARGET}; at least {KEEP_MIN}",
              "calibration_share_R": shares, "eligible": eligible, "kept": kept}
    if len(kept) < KEEP_MIN:
        print(json.dumps(result, indent=1))
        raise SystemExit(f"only {len(kept)} eligible tasks: stop and report, the tasks are revised")
    w = where(CALIBRATION)
    metas = [json.loads(w.trace(f"{t}/0/R").with_suffix(".meta.json").read_text()) for t in candidates()]
    mean_run = sum(m["cost_usd"] for m in metas) / len(metas)
    mean_answer = sum(m["tokens"]["final_answer"] for m in metas) / len(metas)
    warn, _ = caps("v3")
    reps, steps = REPETITIONS, []
    est = estimate(kept, reps, mean_run, mean_answer)
    while est["total"] > warn:
        if len(kept) > KEEP_MIN:
            kept = sorted(sorted(kept, key=lambda t: (round(abs(shares[t] - TARGET), 9), t))[:KEEP_MIN])
            steps.append("tasks reduced to 10")
        elif reps > 2:
            reps = 2
            steps.append("repetitions reduced to 2")
        else:
            print(json.dumps(result | {"budget_steps": steps}, indent=1))
            raise SystemExit("the estimate exceeds the warning even with 10 tasks and 2 repetitions: stop and ask")
        est = estimate(kept, reps, mean_run, mean_answer)
    result.update(kept=kept, repetitions=reps, budget_steps=steps, schedule_seed=SCHEDULE_SEED)
    write(DATA / "selection.json", result)
    write(DATA / "schedule.json", schedule(kept, reps))
    # The figures stay private: printed for the report, never written to a versioned file.
    print(json.dumps({k: round(v, 2) for k, v in est.items()}), "(private: USD at list prices)")
    print(json.dumps({"eligible": len(eligible), "kept": kept, "repetitions": reps, "steps": steps}))


def campaign_slots() -> list[str]:
    return json.loads((DATA / "schedule.json").read_text())


async def run(slots, root, client):
    return await live.campaign(where(root), client, PRICES["model"], slots=slots, tools_factory=TaskTools,
                               data=task_data)


def status():
    calib = where(CALIBRATION)
    camp = where(PRIVATE)
    out = {"calibration_done": sum(calib.trace(f"{t}/0/R").exists() for t in candidates()),
           "calibration_missing": len(live.missing(calib))}
    if (DATA / "schedule.json").exists():
        slots = campaign_slots()
        out.update(campaign_slots=len(slots), campaign_done=sum(camp.trace(s).exists() for s in slots),
                   campaign_missing=len(live.missing(camp)),
                   failed_attempts=sum(1 for e in live.lines(camp) if e.get("aborted")),
                   next=next((s for s in slots if not live.done(camp, s)), None))
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["calibrate", "select", "campaign", "status"])
    p.add_argument("count", nargs="?", type=int)
    a = p.parse_args(argv)
    if a.command == "status":
        print(json.dumps(status(), indent=2))
        return
    if a.command == "select":
        select()
        return
    committed()
    live.verify_frozen()                      # the v3 policy, prompts and tools this study reuses
    if a.command == "campaign":
        from .freeze import verify
        verify()
    from openai import AsyncOpenAI
    client = AsyncOpenAI(timeout=900.0, max_retries=0)
    if a.command == "calibrate":
        root, slots = CALIBRATION, [f"{t}/0/R" for t in candidates()]
    else:
        root, slots = PRIVATE, campaign_slots()
    w = where(root)
    todo = [s for s in slots if not live.done(w, s)]
    live.may_start(w)
    try:
        asyncio.run(run(todo[:a.count] if a.count else todo, root, client))
    finally:
        print(json.dumps(status(), indent=2))


if __name__ == "__main__":
    main()
