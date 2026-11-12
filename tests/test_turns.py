"""Calls per turn in the telemetry report, and the hint on the skill's strategy (tracetrim.turns)."""
import json
from pathlib import Path

import pytest

from tracetrim import report as rp
from tracetrim import turns
from tracetrim.auto import AutoTrim
from tracetrim.cli import main
from tracetrim.traces import Event, Trace, load
from tracetrim.usage import analyse

ROOT = Path(__file__).resolve().parent.parent
E3 = ROOT / "traces"


def trace(name, calls, prompt="Report the failures on main."):
    """`calls`: (turn, tool, arguments, output)."""
    events = [Event("prompt", prompt, turn=-1)]
    events += [Event("call", json.dumps(a), tool, out, turn) for turn, tool, a, out in calls]
    return Trace(name, events + [Event("message", "done", turn=max(c[0] for c in calls) + 1)])


RUNS = '{"runs": [{"id": 111111}, {"id": 222222}]}'
ONE_AT_A_TIME = [(0, "list_runs", {"branch": "main"}, RUNS),
                 (1, "list_jobs", {"run_id": 111111}, '{"jobs": [{"id": 333333}]}'),
                 (2, "logs", {"job_id": 333333}, "boom"),
                 (3, "list_jobs", {"run_id": 222222}, '{"jobs": [{"id": 444444}]}'),      # could have gone with turn 1
                 (4, "logs", {"job_id": 444444}, "bang")]                                   # and this with turn 2
TOGETHER = [(0, "list_runs", {"branch": "main"}, RUNS),
            (1, "list_jobs", {"run_id": 111111}, '{"jobs": [{"id": 333333}]}'),
            (1, "list_jobs", {"run_id": 222222}, '{"jobs": [{"id": 444444}]}'),
            (2, "logs", {"job_id": 333333}, "boom"), (2, "logs", {"job_id": 444444}, "bang")]


def test_the_turns_of_a_run_and_the_calls_that_could_have_been_made_earlier():
    o = turns.organisation(trace("a", ONE_AT_A_TIME))
    assert (o["calls"], o["turns"], o["fewest_turns"], o["turns_with_calls"], o["turns_with_several_calls"]) == (5, 6, 4, 5, 0)
    assert (o["calls_that_depend_on_an_earlier_result"], o["calls_that_could_have_been_made_earlier"]) == (4, 2)
    o = turns.organisation(trace("b", TOGETHER))
    assert (o["turns"], o["fewest_turns"], o["turns_with_several_calls"], o["calls_that_could_have_been_made_earlier"]) == (4, 4, 2, 0)
    # An identifier given in the prompt is known from the start; expand is not a call of the task.
    o = turns.organisation(trace("c", [(0, "list_runs", {}, RUNS), (1, "list_jobs", {"run_id": 999999}, "{}"),
                                       (2, "expand", {"call_id": "c1", "fields": ["*"]}, "{}")], prompt="Look at run 999999."))
    assert (o["calls"], o["calls_that_could_have_been_made_earlier"], o["fewest_turns"]) == (2, 1, 2)


def test_without_turns_nothing_is_computed_and_nothing_is_suggested():
    t = trace("a", ONE_AT_A_TIME)
    for e in t.events:
        e.turn = None                                            # what an MCP proxy records
    assert turns.organisation(t) is None
    s = turns.summarise([t, t])
    assert (s["runs"], s["runs_without_turns"], s["suggest_strategy"], s["per_run"]) == (0, 2, False, [])


def test_the_hint_needs_one_call_per_turn_calls_that_could_be_earlier_and_enough_runs():
    slow = [trace(f"s{i}", ONE_AT_A_TIME) for i in range(3)]
    s = turns.summarise(slow)
    assert s["suggest_strategy"] and s["share_of_turns_with_several_calls"] == 0 and s["share_of_calls_that_could_have_been_made_earlier"] == 0.4
    assert not turns.summarise(slow[:1])["suggest_strategy"]                               # one run is not enough
    assert not turns.summarise([trace(f"t{i}", TOGETHER) for i in range(3)])["suggest_strategy"]
    # One call per turn, each needing the one before: nothing to gain, no hint.
    chain = [(0, "list_runs", {}, RUNS), (1, "list_jobs", {"run_id": 111111}, '{"jobs": [{"id": 333333}]}'),
             (2, "logs", {"job_id": 333333}, "boom")]
    assert not turns.summarise([trace(f"c{i}", chain) for i in range(3)])["suggest_strategy"]
    # The thresholds are arguments, and the report says which ones gave the answer.
    assert not turns.summarise(slow, min_earlier=0.5)["suggest_strategy"]
    assert turns.summarise(slow[:1], min_runs=1)["suggest_strategy"]
    assert turns.summarise(slow, max_several=0.1)["thresholds"] == {
        "max_share_of_turns_with_several_calls": 0.1, "min_share_of_calls_that_could_have_been_made_earlier": 0.25, "min_runs": 2}


def report(traces, **thresholds):
    d = rp.compute([t.name for t in traces], [analyse(t) for t in traces])
    d["turns"] = turns.summarise(traces, **thresholds)
    return d, rp.suggestions(d)


def test_the_hint_is_a_manual_suggestion_and_changes_no_config():
    slow = [trace(f"s{i}", ONE_AT_A_TIME) for i in range(3)]
    d, sugg = report(slow)
    hint = [s for s in sugg if s["rule"] == "strategy"]
    assert len(hint) == 1 and hint[0]["action"] == {"kind": "manual", "type": "skill_strategy"}
    assert "might reduce the turns" in hint[0]["en"] and "E3" in hint[0]["en"] and "potrebbe ridurre i turni" in hint[0]["it"]
    assert "an estimate" in hint[0]["en"] and "una stima" in hint[0]["it"]
    usages = [analyse(t) for t in slow]
    config, _ = rp.build_config(d, sugg, usages, "p.json")
    without, _ = rp.build_config(d, [s for s in sugg if s["rule"] != "strategy"], usages, "p.json")
    assert config == without and "strategy" not in json.dumps(config)
    page = rp.render_html(d, sugg)
    assert "MANUAL" in page and "tracetrim never turns it on by itself" in page and "Tool calls per turn" in page
    _, quiet = report([trace(f"t{i}", TOGETHER) for i in range(3)])
    assert not [s for s in quiet if s["rule"] == "strategy"]


def test_automatic_mode_reports_the_turns_and_never_switches_the_strategy_on(tmp_path):
    tt = AutoTrim(tmp_path, min_runs=2)
    for _ in range(3):
        for turn, tool, a, out in ONE_AT_A_TIME:
            tt.output(tool, a, out, turn=turn)
        tt.done("done")
    d = json.loads((tmp_path / "report.json").read_text())
    assert d["turns"]["runs"] == 3 and d["turns"]["suggest_strategy"]
    assert [s["action"]["kind"] for s in d["suggestions"] if s["rule"] == "strategy"] == ["manual"]
    assert tt.strategy is False and "Organising tool calls" not in tt.instructions


def test_the_thresholds_can_be_set_on_the_command_line(tmp_path, capsys):
    paths = []
    for i in range(3):
        p = tmp_path / f"r{i}.jsonl"
        t = trace(p.name, ONE_AT_A_TIME)
        p.write_text("".join(json.dumps({"kind": e.kind, "turn": e.turn, **({"tool": e.tool, "arguments": e.text, "output": e.output}
                                                                              if e.kind == "call" else {"text": e.text})}) + "\n"
                             for e in t.events))
        paths.append(str(p))
    rules = lambda extra: (main(["report", *paths, "-o", str(tmp_path / "out.html"), *extra]),      # noqa: E731
                           [s["rule"] for s in json.loads((tmp_path / "out.json").read_text())["suggestions"]])[1]
    assert "strategy" in rules([])
    assert "strategy" not in rules(["--strategy-min-earlier", "0.5"])
    assert "strategy" not in rules(["--strategy-min-runs", "4"])


@pytest.mark.skipif(not (E3 / "C5" / "E3-R-1.jsonl").exists(), reason="the runs of E3 are not in git")
@pytest.mark.parametrize("case, arm, suggested", [("C5", "R", True), ("C5", "S", False), ("C4", "R", False), ("C4", "S", False)])
def test_on_the_runs_of_e3_the_hint_is_given_where_the_model_made_one_call_per_turn(case, arm, suggested):
    traces = [load(p) for p in sorted((E3 / case).glob(f"E3-{arm}-*.jsonl"))]
    d, sugg = report(traces)
    assert d["turns"]["runs"] == len(traces) and d["turns"]["suggest_strategy"] is suggested
    assert bool([s for s in sugg if s["rule"] == "strategy"]) is suggested
    # The same rule as the post-hoc analysis of E3: the same numbers.
    ind = json.loads((ROOT / "results" / "v04" / "e3_independence.json").read_text())["cases"][case][arm]
    assert d["turns"]["share_of_calls_that_could_have_been_made_earlier"] == ind["share_that_could_have_been_made_earlier"]
    assert round(d["turns"]["share_of_turns_with_several_calls"], 4) == json.loads(
        (ROOT / "results" / "v04" / "e3.json").read_text())["e3"][case][arm]["share_of_turns_with_several_calls"]
