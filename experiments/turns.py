"""Model turns of a recorded run, rebuilt from its metadata.

The agent loop (run.py) records, for each turn, how many tool calls the model
emitted, and writes the events in order: within a turn the messages first,
then the calls, each followed by its output. Calls emitted in the same turn
were generated before any of their outputs existed, so usage detection must
compare turns, not positions (deviation 14).
"""
import json
from pathlib import Path

from tracetrim.traces import Trace


def with_turns(trace: Trace, meta_path: Path) -> Trace:
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    calls_per_turn = [t["calls"] for t in meta["usage_per_turn"]]
    turn, seen_calls = 0, 0
    for e in trace.events:
        if e.kind == "prompt":
            e.turn = -1
            continue
        if e.kind == "call":
            # Past this turn's calls: the next turn begins.
            while turn < len(calls_per_turn) and seen_calls >= calls_per_turn[turn]:
                turn, seen_calls = turn + 1, 0
            e.turn = turn
            seen_calls += 1
        else:
            # A message comes before its turn's calls: if calls of the current
            # turn were already seen, it belongs to a later turn.
            if seen_calls > 0:
                turn, seen_calls = turn + 1, 0
            e.turn = turn
    n_calls = sum(e.kind == "call" for e in trace.events)
    if n_calls != sum(calls_per_turn):
        raise ValueError(f"{meta_path}: {n_calls} calls in the trace, {sum(calls_per_turn)} in the metadata")
    return trace
