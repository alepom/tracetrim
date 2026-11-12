"""How an agent spreads its tool calls over turns: a measure, and one hint.

Read from the traces alone, when every call says which model turn made it
(`turn`, as `AutoTrim.output(..., turn=...)` records it; an MCP proxy does not
see turns, and then nothing here is computed).

Per run:

  turns with several calls   among the turns that made a call, those that made more than one
  calls that could have      calls whose arguments were all available before the turn
  been made earlier          in which they were made

The second is a heuristic. An argument that names something a tool returned
(an id, a number: recognised by the name of the argument or by the shape of
the value) is available one turn after the first result that contains it;
any other argument is taken as known from the start. The longest chain of
such dependencies gives the fewest turns the run needed. It reads identifiers
only, and it cannot know that a model wanted to read one result before
deciding whether to ask for the next: a lower bound on the turns, not what a
careful agent should have done.

The hint: when the recorded runs make nearly always one call per turn, and a
fair share of their calls could have been made earlier, the strategy level of
the tracetrim skill (docs/skill.md) may shorten them. It is a suggestion for
a person, never an action: tracetrim does not switch the strategy on.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from .traces import Trace

# The hint is given when, over the recorded runs with turns:
MAX_SEVERAL = 0.2     # at most this share of the turns with calls made several calls, and
MIN_EARLIER = 0.25    # at least this share of the calls could have been made in an earlier turn,
MIN_RUNS = 2          # in at least this many runs.
# An argument whose value is something a tool returned: by name, or by shape (a long number, a prefixed id).
ID_NAME = re.compile(r"(^|_)(id|ids|number|run|job|issue|pull|invoice|charge|customer|subscription)$", re.I)
ID_SHAPE = re.compile(r"^(\d{5,}|[a-z]{2,4}_[A-Za-z0-9]{8,})$")
NOT_A_CALL = ("expand", "tracetrim_done")


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


def _arguments(text: str) -> dict:
    try:
        d = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {}
    return d if isinstance(d, dict) else {}


def organisation(trace: Trace) -> dict | None:
    """The turns of one run, or None when its calls do not say their turn."""
    cs = [e for e in trace.calls if e.tool not in NOT_A_CALL]
    if not cs or any(e.turn is None for e in cs):
        return None
    prompts = " ".join(e.text for e in trace.events if e.kind == "prompt")
    available: dict[str, int] = {}                  # value -> the first turn in which a call could use it
    earliest, earlier, dependent = [], 0, 0
    for c in cs:
        need = 0
        for ref in references(_arguments(c.text)):
            pattern = re.compile(rf"(?<![\w]){re.escape(ref)}(?![\w])")
            if pattern.search(prompts):
                continue                            # given in the prompt: known from the start
            if ref not in available:
                # The first call that returned it, and the earliest turn in which that call itself could be made.
                first = next((i for i, x in enumerate(cs[:len(earliest)])
                              if x.turn < c.turn and pattern.search(x.output)), None)
                available[ref] = earliest[first] + 1 if first is not None else 0   # never returned: the model's own choice
            need = max(need, available[ref])
        earliest.append(need)
        dependent += need > 0
        earlier += need < c.turn
    per_turn = Counter(c.turn for c in cs)
    return {"calls": len(cs), "turns": max(c.turn for c in cs) + 2, "fewest_turns": max(earliest) + 2,
            "turns_with_calls": len(per_turn), "turns_with_several_calls": sum(n > 1 for n in per_turn.values()),
            "calls_that_depend_on_an_earlier_result": dependent, "calls_that_could_have_been_made_earlier": earlier}


def summarise(traces: list[Trace], max_several: float = MAX_SEVERAL, min_earlier: float = MIN_EARLIER,
              min_runs: int = MIN_RUNS) -> dict:
    """Per run and over the runs; `suggest_strategy` is the hint, with the thresholds that gave it."""
    rows = [dict(o, run=t.name) for t in traces if (o := organisation(t)) is not None]
    out = {"runs": len(rows), "runs_without_turns": len(traces) - len(rows), "per_run": rows,
           "thresholds": {"max_share_of_turns_with_several_calls": max_several,
                          "min_share_of_calls_that_could_have_been_made_earlier": min_earlier, "min_runs": min_runs},
           "suggest_strategy": False}
    if not rows:
        return out
    with_calls, calls = sum(r["turns_with_calls"] for r in rows), sum(r["calls"] for r in rows)
    out["share_of_turns_with_several_calls"] = round(sum(r["turns_with_several_calls"] for r in rows) / with_calls, 4)
    out["share_of_calls_that_could_have_been_made_earlier"] = round(
        sum(r["calls_that_could_have_been_made_earlier"] for r in rows) / calls, 4)
    out["suggest_strategy"] = (len(rows) >= min_runs and out["share_of_turns_with_several_calls"] <= max_several
                               and out["share_of_calls_that_could_have_been_made_earlier"] >= min_earlier)
    return out
