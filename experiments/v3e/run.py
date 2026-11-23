"""Schedule and campaign of protocol-v3e.md: text previews on the [trimmed] line, agent `gpt-5.6-terra` (PAID).

  python -m experiments.v3e.run schedule       # material/schedule.json (no spend)
  python -m experiments.v3e.run campaign [N]   # the next N slots of the frozen schedule (PAID)
  python -m experiments.v3e.run status

Arms P2 (the frozen prudent policy P of protocol v3, skill version 2: the default of 1.4) and P2T (the same, with
`text_preview=40`). The loop of protocol v3 (experiments/v3/live.py) and the OpenAI prices of experiments.paths, as
in protocol v3c. Hard tasks C4b-01..12, 3 repetitions; ordinary tasks C4-01..10, 1 repetition. Private and
git-ignored: out/v3e/ (traces, ledger). Caps: the `v3e` entry of experiments/caps.local.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import subprocess

from experiments.caps import caps
from experiments.paths import PRICES, ROOT
from experiments.v3 import live
from experiments.v3c.run import tools_factory, task_data  # noqa: F401  (the tasks of v3c, both kinds)
from tracetrim.engine import Engine
from tracetrim.runtime import Policy

PRIVATE = ROOT / "out/v3e"
DATA = ROOT / "experiments/v3e/material"
SEED = 241123
PREVIEW = 40          # characters; chosen on the v3c traces (experiments/v3e/hints_offline.py), before any v3e run
# The v3 loop builds an arm's engine from the name of the policy it uses (live.engine_for): "PT" names policy P
# with the previews on. The arms differ only there.
ARMS = {"P2": {"policy": "P", "skill_version": "2"}, "P2T": {"policy": "PT", "skill_version": "2"}}
HARD = [f"C4b-{i:02}" for i in range(1, 13)]
ORDINARY = [f"C4-{i:02}" for i in range(1, 11)]
REPS = {"hard": 3, "ordinary": 1}

_engine_for = live.engine_for


def engine_for(policy: str, task: str) -> Engine | None:
    if policy != "PT":
        return _engine_for(policy, task)
    config = live.DATA / "policies" / task[:2] / "P" / "config.json"
    return Engine(policy=Policy.load(config), breakdown=False, text_preview=PREVIEW)


def where():
    return live.Where(PRIVATE)


def limits():
    return caps("v3e")


def schedule() -> list[str]:
    rng = random.Random(SEED)
    blocks = [(t, r) for t in HARD for r in range(1, REPS["hard"] + 1)] + \
             [(t, r) for t in ORDINARY for r in range(1, REPS["ordinary"] + 1)]
    rng.shuffle(blocks)
    orders = ([("P2", "P2T"), ("P2T", "P2")] * ((len(blocks) + 1) // 2))[:len(blocks)]
    rng.shuffle(orders)
    return [f"{t}/{r}/{arm}" for (t, r), order in zip(blocks, orders) for arm in order]


def slots() -> list[str]:
    return json.loads((DATA / "schedule.json").read_text())


def committed():
    r = subprocess.run(["git", "status", "--porcelain", "--", "protocol-v3e.md", "protocol-v3e.lock",
                        "experiments/v3e", "src"], cwd=ROOT, capture_output=True, text=True)
    if r.stdout.strip():
        raise SystemExit("protocol, lock, experiments/v3e or src have uncommitted changes: commit before a paid run")


def status():
    w = where()
    s = slots()
    return {"slots": len(s), "done": sum(w.trace(x).exists() for x in s), "missing": len(live.missing(w)),
            "failed_attempts": sum(1 for e in live.lines(w) if e.get("aborted")),
            "next": next((x for x in s if not live.done(w, x)), None)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["schedule", "campaign", "status"])
    p.add_argument("count", nargs="?", type=int)
    a = p.parse_args(argv)
    if a.command == "schedule":
        DATA.mkdir(parents=True, exist_ok=True)
        path = DATA / "schedule.json"
        if path.exists():
            raise FileExistsError(path)
        path.write_text(json.dumps(schedule(), indent=2) + "\n")
        print(f"{path}: {len(schedule())} slots")
        return
    if a.command == "status":
        print(json.dumps(status(), indent=2))
        return
    committed()
    from .freeze import verify
    verify()
    live.engine_for = engine_for
    w = where()
    live.may_start(w, limits())
    from openai import AsyncOpenAI
    client = AsyncOpenAI(timeout=900.0, max_retries=0)
    todo = [s for s in slots() if not live.done(w, s)]
    try:
        asyncio.run(live.campaign(w, client, PRICES["model"], limits=limits(), tools_factory=tools_factory,
                                  slots=todo[:a.count] if a.count else todo, data=task_data, arms=ARMS))
    finally:
        print(json.dumps(status(), indent=2))


if __name__ == "__main__":
    main()
