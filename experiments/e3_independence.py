#!/usr/bin/env python3
"""How many turns did a run need, given what each call depended on? A post-hoc analysis of E3.

  python -m experiments.e3_independence          # results/v04/e3_independence.json

Not in protocol-v04-e3.md: added after the runs, to read H8 on the simulated
cases. A run that makes one call per turn is not wasting turns if every call
needs the result of the one before. So, for each call of a run, this asks
when its arguments were first available:

  - an argument that names something a tool returned (a run id, a job id, an
    issue or pull-request number, a customer or invoice id) is available one
    turn after the first tool output that contains it;
  - any other argument (a branch, a state, a page size, an email given in the
    prompt) is taken as known from the start.

A call "could have been made earlier" when all its arguments were available
before the turn in which it was made. The fewest turns a run needed is the
longest chain of such dependencies, plus the turn of the answer.

A heuristic: it reads identifiers only, by the name of the argument or by
their shape, and it cannot know that a model wanted to read one result
before deciding whether to ask for the next. It gives a lower bound on the
turns, not what a careful agent should have done.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from statistics import median

from .paths import ROOT
from .v04 import calls as read_calls

OUT = ROOT / "results" / "v04" / "e3_independence.json"
CASES = ("C4", "C5")
# An argument whose value is something a tool returned: by name, or by shape (a long number, a prefixed id).
ID_NAME = re.compile(r"(^|_)(id|ids|number|run|job|issue|pull|invoice|charge|customer|subscription)$", re.I)
ID_SHAPE = re.compile(r"^(\d{5,}|[a-z]{2,4}_[A-Za-z0-9]{8,})$")


def references(arguments: dict) -> list[str]:
    """The values of a call's arguments that name something returned by a tool."""
    out = []
    for name, value in (arguments or {}).items():
        for v in value if isinstance(value, list) else [value]:
            if isinstance(v, bool) or v is None or isinstance(v, (dict, list)):
                continue
            s = str(v)
            if ID_NAME.search(str(name)) or ID_SHAPE.match(s):
                out.append(s)
    return out


def run(trace: Path) -> dict:
    cs = [c for c in read_calls(trace) if c["tool"] != "expand"]
    prompts = " ".join(json.loads(line)["text"] for line in trace.read_text(encoding="utf-8").splitlines()
                       if '"kind": "prompt"' in line)
    available: dict[str, int] = {}                  # value -> the first turn in which a call could use it
    earliest, earlier, dependent = [], 0, 0
    for c in cs:
        need = 0
        for ref in references(c["arguments"]):
            pattern = re.compile(rf"(?<![\w]){re.escape(ref)}(?![\w])")
            if pattern.search(prompts):
                continue                            # given in the prompt: known from the start
            if ref not in available:
                # The first call that returned it, and the earliest turn in which that call itself could be made.
                first = next((i for i, x in enumerate(cs[:len(earliest)])
                              if x["turn"] < c["turn"] and pattern.search(x["original"])), None)
                available[ref] = earliest[first] + 1 if first is not None else 0   # never returned: the model's own choice
            need = max(need, available[ref])
        earliest.append(need)
        dependent += need > 0
        earlier += need < c["turn"]
    turns = (max(c["turn"] for c in cs) + 2) if cs else 1
    return {"calls": len(cs), "turns": turns, "fewest_turns": (max(earliest) + 2) if cs else 1,
            "calls_that_depend_on_an_earlier_result": dependent, "calls_that_could_have_been_made_earlier": earlier}


def analyse(traces: Path) -> dict:
    out = {}
    for case in CASES:
        for arm in ("R", "S"):
            rows = [dict(run(p), run=p.stem) for p in sorted((traces / case).glob(f"E3-{arm}-*.jsonl"))]
            if not rows:
                continue
            total = sum(r["calls"] for r in rows)
            out.setdefault(case, {})[arm] = {
                "runs": len(rows), "turns_median": median(r["turns"] for r in rows),
                "fewest_turns_median": median(r["fewest_turns"] for r in rows),
                "calls": total,
                "share_depending_on_an_earlier_result": round(sum(r["calls_that_depend_on_an_earlier_result"] for r in rows) / total, 4),
                "share_that_could_have_been_made_earlier": round(sum(r["calls_that_could_have_been_made_earlier"] for r in rows) / total, 4),
                "per_run": rows}
    return {"what": "post-hoc analysis of E3, not in its protocol: turns a run needed given what each call depended on",
            "rule": "identifiers only, by argument name or shape; a lower bound on the turns", "cases": out}


if __name__ == "__main__":
    res = analyse(ROOT / "traces")
    OUT.write_text(json.dumps(res, indent=2) + "\n")
    print(f"{'case':<5}{'arm':<4}{'runs':>5}{'turns':>7}{'fewest':>8}{'calls':>7}{'depend on a result':>20}{'could be earlier':>18}")
    for case, arms in res["cases"].items():
        for arm, a in arms.items():
            print(f"{case:<5}{arm:<4}{a['runs']:>5}{a['turns_median']:>7g}{a['fewest_turns_median']:>8g}{a['calls']:>7}"
                  f"{a['share_depending_on_an_earlier_result']:>20.0%}{a['share_that_could_have_been_made_earlier']:>18.0%}")
    print(OUT)
