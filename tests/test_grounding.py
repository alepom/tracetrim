"""The check on values of the answer that no tool output contains (tracetrim.grounding, since 1.1)."""
import json
import sys
from pathlib import Path

from tracetrim import grounding
from tracetrim.auto import AutoTrim
from tracetrim.cli import main
from tracetrim.traces import Event, Trace

FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(FIXTURES))
import billing  # noqa: E402


def trace(answer, outputs, arguments="{}"):
    t = Trace("run-0001.jsonl")
    for out in outputs:
        t.events.append(Event("call", text=arguments, tool="t", output=out))
    t.events.append(Event("message", text=answer))
    return t


# -- which values are checked ---------------------------------------------------

def test_attributable_values():
    assert grounding.attributable("4391234568")
    assert grounding.attributable("W4391234568")
    assert grounding.attributable("in_1001")
    assert grounding.attributable("29,00")
    assert not grounding.attributable("12")          # too generic
    assert not grounding.attributable("2027")        # a year: the agent writes it itself
    assert not grounding.attributable("1999")
    assert not grounding.attributable("0,00")        # zeros
    assert grounding.attributable("2100")            # not a year of this range: a number


# -- what counts as supported ----------------------------------------------------

def test_a_made_up_code_is_reported_once():
    out = json.dumps({"results": [{"id": "https://openalex.org/W100200300", "title": "A study"}]})
    n, missing = grounding.unsupported(trace("See W100200300, W4391234568 and again W4391234568.", [out]))
    assert n == 3 and missing == ["W4391234568"]


def test_values_under_the_forms_usage_detection_knows_are_supported():
    # amount in minor units, date from a timestamp, the invoice id, as in the billing fixture
    t = trace(billing.answer(0), [billing.output(0)])
    assert grounding.unsupported(t)[1] == []


def test_a_value_the_agent_wrote_in_its_arguments_is_supported():
    t = trace("Customer cus_A9123 has no invoice.", ['{"data": []}'], arguments='{"customer": "cus_A9123"}')
    assert grounding.unsupported(t)[1] == []


def test_a_filtered_result_with_its_trimmed_line_is_read_as_json():
    sent = '{"data":[{"amount_due":2900}]}\n[trimmed] call_id=c1 hidden: data[].footer'
    assert grounding.unsupported(trace("29.00 EUR are due.", [sent]))[1] == []


def test_a_run_without_answer_is_not_checked():
    t = Trace("run-0002-noanswer.jsonl", [Event("call", text="{}", tool="t", output='{"a": 1}')])
    d = grounding.summarise([t])
    assert d["runs_checked"] == 0 and d["runs"] == [] and not d["suggest"]


# -- in automatic mode -----------------------------------------------------------

def _learn(tt, runs=3):
    for i in range(runs):
        tt.output(billing.TOOL, billing.arguments(i), billing.output(i))
        tt.done(billing.answer(i))


def test_a_made_up_value_in_a_trimmed_run_is_logged_and_reported(tmp_path):
    tt = AutoTrim(tmp_path, min_runs=3)
    _learn(tt)
    sent = tt.output(billing.TOOL, billing.arguments(3), billing.output(3))
    assert "[trimmed]" in sent
    tt.done("Invoice in_1004 is open; it replaces invoice in_9917.")
    assert tt.learner.unsupported == ["in_9917"]
    report = json.loads((tmp_path / "report.json").read_text())
    g = report["grounding"]
    assert g["runs_checked"] == 4 and g["suggest"]
    assert g["runs"] == [{"run": "run-0004", "values": 2, "unsupported": ["in_9917"], "count": 1,
                          "filtered_tools": [billing.TOOL], "suspect": ["in_9917"]}]
    rule = [s for s in report["suggestions"] if s["rule"] == "grounding"]
    assert rule and rule[0]["level"] == "risk"
    assert rule[0]["action"] == {"kind": "manual", "type": "check_values", "runs": ["run-0004: in_9917"]}
    page = (tmp_path / "report.html").read_text()
    assert "Valori delle risposte che nessun tool ha restituito" in page and "in_9917" in page


def test_correct_answers_give_no_suggestion(tmp_path):
    tt = AutoTrim(tmp_path, min_runs=3)
    _learn(tt, 4)
    assert tt.learner.unsupported == []
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["grounding"]["runs"] == [] and not report["grounding"]["suggest"]
    assert not [s for s in report["suggestions"] if s["rule"] == "grounding"]
    assert "Ogni valore controllato compare" in (tmp_path / "report.html").read_text()


def test_a_made_up_value_while_learning_is_listed_but_not_suggested(tmp_path):
    # Nothing was filtered yet: the value is shown, the suggestion (about trimming) is not given.
    tt = AutoTrim(tmp_path, min_runs=3)
    tt.output(billing.TOOL, billing.arguments(0), billing.output(0))
    tt.done("Invoice in_5555 is open.")
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["grounding"]["runs"][0]["unsupported"] == ["in_5555"]
    assert report["grounding"]["runs"][0]["filtered_tools"] == [] and not report["grounding"]["suggest"]


def test_nothing_the_model_receives_changes(tmp_path):
    a, b = AutoTrim(tmp_path / "a", min_runs=3), AutoTrim(tmp_path / "b", min_runs=3)
    _learn(a)
    _learn(b)
    a.done("unused")                              # no open run: nothing happens
    same = [x.output(billing.TOOL, billing.arguments(3), billing.output(3)) for x in (a, b)]
    # The call ids are random: everything else is the same text.
    assert [s.split(" call_id=")[0] for s in same] == [same[0].split(" call_id=")[0]] * 2
    assert [s.split(" hidden: ")[1] for s in same] == [same[0].split(" hidden: ")[1]] * 2


# -- offline report ---------------------------------------------------------------

def test_the_offline_report_has_the_section(tmp_path):
    for i in range(3):
        lines = [{"kind": "call", "tool": billing.TOOL, "arguments": billing.arguments(i), "output": billing.output(i)},
                 {"kind": "message", "text": billing.answer(i) + (" See also in_7777." if i == 2 else "")}]
        (tmp_path / f"r{i}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    out = tmp_path / "report.html"
    main(["report", *map(str, sorted(tmp_path.glob("r*.jsonl"))), "-o", str(out)])
    d = json.loads(out.with_suffix(".json").read_text())
    assert d["grounding"]["runs_checked"] == 3
    assert [r["unsupported"] for r in d["grounding"]["runs"]] == [["in_7777"]]
    assert "in_7777" in out.read_text()


def test_a_value_unsupported_also_without_trimming_is_not_held_against_it(tmp_path):
    # The agent writes "600" (a default it knows) in a learning run and in a trimmed one: not a trimming problem.
    tt = AutoTrim(tmp_path, min_runs=3)
    for i in range(3):
        tt.output(billing.TOOL, billing.arguments(i), billing.output(i))
        tt.done(billing.answer(i) + (" The link expires after 600 seconds." if i == 0 else ""))
    tt.output(billing.TOOL, billing.arguments(3), billing.output(3))
    tt.done(billing.answer(3) + " The link expires after 600 seconds.")
    report = json.loads((tmp_path / "report.json").read_text())
    runs = {r["run"]: r for r in report["grounding"]["runs"]}
    assert runs["run-0001"]["unsupported"] == ["600"] and runs["run-0001"]["suspect"] == []
    assert runs["run-0004"]["unsupported"] == ["600"] and runs["run-0004"]["suspect"] == []
    assert not report["grounding"]["suggest"]
    assert not [s for s in report["suggestions"] if s["rule"] == "grounding"]


# -- through a real proxy process -------------------------------------------------------

def test_the_proxy_logs_values_no_output_contains(tmp_path):
    """The closing log line, over stdio, from a proxy in automatic mode (the fixture server of test_proxy_skill)."""
    import asyncio
    import os

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    root = Path(__file__).resolve().parent.parent
    params = StdioServerParameters(
        command=sys.executable, cwd=str(root), env={**os.environ, "PYTHONPATH": str(root / "src")},
        args=["-m", "tracetrim.cli", "proxy", "--upstream-cmd", f"{sys.executable} {FIXTURES / 'meta_server.py'}",
              "--auto", str(tmp_path / "work"), "--min-runs", "2"])
    log = tmp_path / "stderr.txt"

    async def go():
        with log.open("w") as err:
            async with stdio_client(params, errlog=err) as (r, w), ClientSession(r, w) as s:
                await s.initialize()
                await s.call_tool("volumes", {})
                await s.call_tool("tracetrim_done", {"answer": "spa weekend has 1600 searches a month."})
                await s.call_tool("volumes", {})
                await s.call_tool("tracetrim_done", {"answer": "spa weekend has 1600 searches, 4870 last year."})

    asyncio.run(go())
    lines = log.read_text().splitlines()
    flagged = [x for x in lines if "appear in no tool output" in x]
    assert len(flagged) == 1 and "4870" in flagged[0] and "1600" not in flagged[0]


# -- review of 1.1 -------------------------------------------------------------------------

def test_a_rounding_or_a_percentage_of_a_value_is_supported():
    out = json.dumps({"items": [{"keyword": "spa weekend", "search_volume": 1612, "ctr": 0.125}]})
    t = trace("About 1.600 searches (1600 in English), CTR 12.5%, rounded 13%.", [out])
    assert grounding.unsupported(t)[1] == []


def test_a_value_far_from_any_output_is_still_reported():
    out = json.dumps({"items": [{"search_volume": 1612}]})
    assert grounding.unsupported(trace("About 1.900 searches, exactly 1613.", [out]))[1] == ["1.900", "1613"]


def test_four_digit_numbers_read_as_years_are_not_checked():
    assert grounding.unsupported(trace("Volume 1950, plan for 2027.", ['{"a": 1}']))[0] == 0


def test_the_trimmed_line_is_split_with_the_engine_marker():
    from tracetrim.engine import MARK
    sent = '{"data":[{"amount_due":2900}]}' + MARK + "call_id=c1 hidden: data[].footer"
    assert grounding.unsupported(trace("29.00 EUR are due.", [sent]))[1] == []


def test_a_failing_check_never_stops_a_closing(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("broken measure")
    monkeypatch.setattr(grounding, "unsupported", boom)
    monkeypatch.setattr(grounding, "summarise", boom)
    tt = AutoTrim(tmp_path, min_runs=3)
    _learn(tt)
    assert tt.learner.state["complete_runs"] == 3 and tt.learner.unsupported == []
    report = json.loads((tmp_path / "report.json").read_text())
    assert "grounding" not in report and report["auto"]["complete_runs"] == 3


def test_a_number_matches_a_value_times_100_only_when_written_as_a_percentage():
    out = json.dumps({"rollout": {"replicas": 6}})
    assert grounding.unsupported(trace("It timed out after 600 seconds.", [out]))[1] == ["600"]
    assert grounding.unsupported(trace("Success rate 600%? No: 6 replicas.", [out]))[1] == []
