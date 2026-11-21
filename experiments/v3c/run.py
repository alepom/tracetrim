"""Schedule and campaign of protocol-v3c.md, on the loop of protocol v3 (PAID).

  python -m experiments.v3c.run schedule       # material/schedule.json (no spend)
  python -m experiments.v3c.run campaign [N]   # the next N slots of the frozen schedule (PAID)
  python -m experiments.v3c.run status

Arms P1 and P2: the frozen prudent policy P of protocol v3 with the skill text of version 1 and 2. Hard tasks
C4b-01..12 (protocol v3b, version 2), 3 repetitions; ordinary tasks C4-01..10 (protocol v3), 1 repetition.
Private and git-ignored: out/v3c/ (traces, ledger). Caps: the `v3c` entry of experiments/caps.local.json.
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
from experiments.v3 import tools as v3_tools
from experiments.v3b import tools as v3b_tools

PRIVATE = ROOT / "out/v3c"
DATA = ROOT / "experiments/v3c/material"
SEED = 130511
ARMS = {"P1": {"policy": "P", "skill_version": "1"}, "P2": {"policy": "P", "skill_version": "2"}}
HARD = [f"C4b-{i:02}" for i in range(1, 13)]
ORDINARY = [f"C4-{i:02}" for i in range(1, 11)]
REPS = {"hard": 3, "ordinary": 1}
LEDGERS = {"campaign": PRIVATE / "ledger.jsonl", "judge": PRIVATE / "judge-ledger.jsonl"}


def task_data(task):
    return (v3b_tools.task_data if task.startswith("C4b-") else v3_tools.task_data)(task)


def tools_factory(task):
    return v3b_tools.TaskTools(task) if task.startswith("C4b-") else v3_tools.TaskTools(task)


def where():
    """The campaign's ledger, under the v3c caps together with the judge's."""
    return live.Where(PRIVATE, also=(LEDGERS["judge"],))


def limits():
    return caps("v3c")


def schedule() -> list[str]:
    rng = random.Random(SEED)
    blocks = [(t, r) for t in HARD for r in range(1, REPS["hard"] + 1)] + \
             [(t, r) for t in ORDINARY for r in range(1, REPS["ordinary"] + 1)]
    rng.shuffle(blocks)
    orders = ([("P1", "P2"), ("P2", "P1")] * ((len(blocks) + 1) // 2))[:len(blocks)]
    rng.shuffle(orders)
    return [f"{t}/{r}/{arm}" for (t, r), order in zip(blocks, orders) for arm in order]


def slots() -> list[str]:
    return json.loads((DATA / "schedule.json").read_text())


def committed():
    r = subprocess.run(["git", "status", "--porcelain", "--", "protocol-v3c.md", "protocol-v3c.lock",
                        "experiments/v3c", "src"], cwd=ROOT, capture_output=True, text=True)
    if r.stdout.strip():
        raise SystemExit("protocol, lock, experiments/v3c or src have uncommitted changes: commit before a paid run")


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
