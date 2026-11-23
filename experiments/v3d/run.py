"""Schedule and campaign of protocol-v3d.md: arms R, P1, P2 on the hard tasks, agent `gemini-3.8-flash` (PAID).

  python -m experiments.v3d.run schedule       # material/schedule.json (no spend)
  python -m experiments.v3d.run campaign [N]   # the next N slots of the frozen schedule (PAID)
  python -m experiments.v3d.run status

The loop of protocol v3 (experiments/v3/live.py), with the Gemini adapter of v04-E3 (experiments/gemini.py) in place
of the OpenAI client, and the Vertex prices of experiments/prices-e3.json for the ledger and the caps. Private and
git-ignored: out/v3d/ (traces, ledger). Caps: the `v3d` entry of experiments/caps.local.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import subprocess
from itertools import permutations

from experiments.caps import caps
from experiments.paths import ROOT
from experiments.v3 import live
from experiments.v3b import tools as v3b_tools

PRIVATE = ROOT / "out/v3d"
DATA = ROOT / "experiments/v3d/material"
SEED = 231123
MODEL = "gemini-3.8-flash"
PRICES = json.loads((ROOT / "experiments/prices-e3.json").read_text())
ARMS = {"R": {}, "P1": {"policy": "P", "skill_version": "1"}, "P2": {"policy": "P", "skill_version": "2"}}
HARD = [f"C4b-{i:02}" for i in range(1, 13)]
REPS = 3

assert PRICES["model"] == MODEL


def cost(usage: dict) -> float:
    """The Vertex price of a run's usage (prices-e3.json), in place of the OpenAI prices of the v3 loop."""
    cached = usage.get("cached", 0)
    return ((usage["input"] - cached) * PRICES["input_per_mtok"] + cached * PRICES["cached_input_per_mtok"]
            + usage["output"] * PRICES["output_per_mtok"]) / 1e6


task_data = v3b_tools.task_data


def tools_factory(task):
    return v3b_tools.TaskTools(task)


def where():
    return live.Where(PRIVATE)


def limits():
    return caps("v3d")


def schedule() -> list[str]:
    rng = random.Random(SEED)
    blocks = [(t, r) for t in HARD for r in range(1, REPS + 1)]
    rng.shuffle(blocks)
    orders = (list(permutations(ARMS)) * (len(blocks) // 6 + 1))[:len(blocks)]
    rng.shuffle(orders)
    return [f"{t}/{r}/{arm}" for (t, r), order in zip(blocks, orders) for arm in order]


def slots() -> list[str]:
    return json.loads((DATA / "schedule.json").read_text())


def committed():
    r = subprocess.run(["git", "status", "--porcelain", "--", "protocol-v3d.md", "protocol-v3d.lock",
                        "experiments/v3d", "src"], cwd=ROOT, capture_output=True, text=True)
    if r.stdout.strip():
        raise SystemExit("protocol, lock, experiments/v3d or src have uncommitted changes: commit before a paid run")


def status():
    w = where()
    s = slots()
    return {"slots": len(s), "done": sum(w.trace(x).exists() for x in s), "missing": len(live.missing(w)),
            "failed_attempts": sum(1 for e in live.lines(w) if e.get("aborted")),
            "spent_usd": round(sum(e.get("cost_usd", 0) for e in live.lines(w)), 4),
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
    live.cost = cost                              # the v3 loop prices runs through this name
    w = where()
    live.may_start(w, limits())
    from experiments.gemini import Gemini, vertex_client
    client = Gemini(vertex_client())
    todo = [s for s in slots() if not live.done(w, s)]
    try:
        asyncio.run(live.campaign(w, client, MODEL, limits=limits(), tools_factory=tools_factory,
                                  slots=todo[:a.count] if a.count else todo, data=task_data, arms=ARMS))
    finally:
        print(json.dumps(status(), indent=2))


if __name__ == "__main__":
    main()
