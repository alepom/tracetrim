"""Automatic mode without MCP: an agent that runs its own tools (tracetrim.auto.AutoTrim).

Recorded runs of the billing agent (C4) are replayed as a function-calling
loop would see them: every tool result goes through AutoTrim.output, the
final answer through AutoTrim.done.
"""
import json
from pathlib import Path

from tracetrim.auto import AutoTrim
from tracetrim.projection import dumps
from tracetrim.usage import parse_output

ROOT = Path(__file__).resolve().parent.parent


def _run(i, traces):
    events = [json.loads(line) for line in (traces / "C4" / f"A{i}.jsonl").open(encoding="utf-8")]
    return ([e for e in events if e["kind"] == "call"],
            "\n".join(e["text"] for e in events if e["kind"] == "message"))


def _loop(tt, i, traces):
    calls, answer = _run(i, traces)
    results = [(c["tool"], c["output"], tt.output(c["tool"], c["arguments"], c["output"])) for c in calls]
    tt.done(answer)
    return results


def test_learns_then_trims_and_expand_gives_back_the_hidden_fields(tmp_path, traces):
    tt = AutoTrim(tmp_path, min_runs=3)
    assert tt.expand_tool["name"] == "expand" and "call_id" in tt.expand_tool["parameters"]["properties"]
    for i in (1, 2, 3):
        results = _loop(tt, i, traces)
        # Before the third run closes nothing is trimmed: results reach the model whole, JSON compacted.
        assert all(sent == (raw if parse_output(raw) is None else dumps(parse_output(raw), compact=True))
                   for _, raw, sent in results)
    assert tt.trimming and tt.report.exists()

    results = _loop(tt, 4, traces)
    trimmed = [(tool, raw, sent) for tool, raw, sent in results if "\n[trimmed] " in sent]
    assert trimmed and sum(len(s) for _, _, s in results) < sum(len(r) for _, r, _ in results)

    tool, raw, sent = trimmed[0]
    line = sent.rsplit("\n[trimmed] ", 1)[1]
    cid = line.split("call_id=")[1].split()[0]
    group = line.split("hidden: ")[1].split(",")[0].split(" (")[0]
    back = tt.expand(json.dumps({"call_id": cid, "fields": [group]}))
    assert not back.startswith("Error") and group.split(".")[-1].split("[")[0] in back
    assert tt.learner.state["expanded"].get(tool) == 1


def test_a_restart_keeps_what_was_learned(tmp_path, traces):
    tt = AutoTrim(tmp_path, min_runs=2)
    for i in (1, 2):
        _loop(tt, i, traces)
    assert AutoTrim(tmp_path, min_runs=2).trimming


def test_min_runs_is_at_least_two(tmp_path):
    # One run leaves nothing to hold out: trimming could never be justified.
    assert AutoTrim(tmp_path, min_runs=1).learner.min_runs == 2


def test_calls_of_one_turn_do_not_use_each_other(tmp_path):
    from tracetrim.traces import load
    from tracetrim.usage import analyse
    tt = AutoTrim(tmp_path, min_runs=2)
    # Two calls emitted together: the second names a customer the first will
    # return, but it was written before that output existed.
    tt.output("find", {"q": "a"}, json.dumps({"id": "cus_ZZZ999", "plan": "gold"}), turn=0)
    tt.output("orders", {"customer": "cus_ZZZ999"}, json.dumps({"n": 3}), turn=0)
    tt.done("Customer found.")
    trace = load(next((tmp_path / "traces").glob("run-*.jsonl")))
    assert all(e.turn is not None for e in trace.events)
    assert analyse(trace)[0].used == set()
