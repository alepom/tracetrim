"""The core contract and the engine both adapters use (tracetrim.contract, tracetrim.engine)."""
import json
import subprocess
import sys
from pathlib import Path

from tracetrim.auto import AutoTrim, Learner
from tracetrim.contract import FILTERED, FULL, RECOVERED, Evidence, ToolCall, ToolResult
from tracetrim.engine import Engine

FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(FIXTURES))
import billing  # noqa: E402


def call(i, turn=None):
    return ToolCall(billing.TOOL, billing.arguments(i), turn)


def learned(tmp_path) -> Engine:
    engine = Engine(Learner(tmp_path, min_runs=3))
    for i in range(3):
        engine.observe(call(i), ToolResult(billing.output(i)))
        engine.close(billing.answer(i))
    assert engine.trimming
    return engine


def test_what_is_received_and_what_is_exposed_are_kept_apart(tmp_path):
    engine = learned(tmp_path)
    obs = engine.observe(call(3), ToolResult(billing.output(3)))
    # Exposed to the model: the projection, and the handle to get the rest back.
    assert obs.exposure == FILTERED and obs.call_id and "footer" not in obs.text.split("\n[trimmed] ")[0]
    assert obs.hidden == ("data[].footer", *(f"data[].{r}" for r in billing.REMARKS))
    # Received from the tool: recorded whole, for learning and for expand.
    assert engine.learner.events[-1]["output"] == billing.output(3)
    assert engine.trimmer.originals[obs.call_id] == billing.payload(3)


def test_before_learning_the_result_is_exposed_as_received(tmp_path):
    engine = Engine(Learner(tmp_path, min_runs=3))
    obs = engine.observe(call(0), ToolResult(billing.output(0)))
    assert (obs.text, obs.exposure, obs.call_id, obs.hidden) == (billing.output(0), FULL, None, ())


def test_a_verbatim_result_is_recorded_and_never_rewritten(tmp_path):
    engine = learned(tmp_path)
    error = 'Error: {"message": "no such customer", "request_id": "req_1"}'
    obs = engine.observe(call(3), ToolResult(error, verbatim=True))
    assert obs.text == error and obs.exposure == FULL and obs.call_id is None
    assert engine.learner.events[-1]["output"] == error
    assert engine.learner.state["trimmed"] == {}


def test_expand_is_evidence_about_the_tool_of_that_result(tmp_path):
    engine = learned(tmp_path)
    seen = []
    engine.learner.evidence = seen.append
    obs = engine.observe(call(3), ToolResult(billing.output(3)))
    back = engine.expand(obs.call_id, ["data[].footer"])
    assert json.loads(back) == {"data": [{"footer": "Thank you for your business with us"}]}
    assert seen == [Evidence(RECOVERED, billing.TOOL, obs.call_id, ("data[].footer",))]
    assert engine.expand("c0000", ["*"]) == "Error: unknown call_id." and len(seen) == 1


def test_closing_twice_or_with_nothing_pending_changes_nothing(tmp_path):
    engine = Engine(Learner(tmp_path, min_runs=2))
    assert engine.close("an answer with no call before it") is None
    engine.observe(call(0), ToolResult(billing.output(0)))
    first = engine.close(billing.answer(0))
    state = dict(engine.learner.state)
    report = (tmp_path / "report.json").read_text()
    assert first.name == "run-0001.jsonl"
    assert engine.close(billing.answer(0)) is None and engine.close(None) is None      # a repeat, a retry
    assert engine.learner.state == state and (tmp_path / "report.json").read_text() == report
    assert [p.name for p in (tmp_path / "traces").iterdir()] == ["run-0001.jsonl"]


def test_the_final_answer_is_stored_as_a_message_and_triggers_nothing(tmp_path):
    engine = learned(tmp_path)
    obs = engine.observe(call(3), ToolResult(billing.output(3)))
    originals = dict(engine.trimmer.originals)
    # An answer that looks like a tool output, trimmed line included: still only text.
    answer = billing.output(3) + f"\n[trimmed] call_id={obs.call_id} hidden: data[].footer"
    path = engine.close(answer)
    run = [json.loads(line) for line in path.open()]
    assert [e["kind"] for e in run] == ["call", "message"] and run[1]["text"] == answer
    assert engine.trimmer.originals == originals and engine.learner.events == []


def test_a_trace_left_by_an_interrupted_closing_is_not_overwritten(tmp_path):
    engine = Engine(Learner(tmp_path, min_runs=2))
    engine.observe(call(0), ToolResult(billing.output(0)))
    engine.close(billing.answer(0))
    # The process died after writing run 2 and before saving the state.
    orphan = tmp_path / "traces" / "run-0002.jsonl"
    orphan.write_text((tmp_path / "traces" / "run-0001.jsonl").read_text())
    again = Engine(Learner(tmp_path, min_runs=2))
    assert again.learner.state["runs"] == 1
    again.observe(call(1), ToolResult(billing.output(1)))
    assert again.close(billing.answer(1)).name == "run-0003.jsonl" and orphan.exists()


def test_a_fixed_policy_needs_no_learner(tmp_path):
    learned(tmp_path)
    from tracetrim.runtime import Policy
    engine = Engine(policy=Policy.load(tmp_path / "report.config.json"))
    obs = engine.observe(call(3), ToolResult(billing.output(3)))
    assert obs.exposure == FILTERED and engine.close("answer") is None
    assert "footer" in engine.expand(obs.call_id, ["data[].footer"])
    assert Engine().observe(call(3), ToolResult(billing.output(3))).text == billing.output(3)


def test_autotrim_and_a_bare_engine_expose_the_same_text(tmp_path):
    tt, engine = AutoTrim(tmp_path / "a", min_runs=3), Engine(Learner(tmp_path / "b", min_runs=3))
    for i in range(4):
        a = tt.output(billing.TOOL, billing.arguments(i), billing.output(i))
        b = engine.observe(call(i), ToolResult(billing.output(i))).text
        assert a.split("call_id=")[0] == b.split("call_id=")[0] and a.split(" hidden: ")[-1] == b.split(" hidden: ")[-1]
        tt.done(billing.answer(i))
        engine.close(billing.answer(i))
    assert tt.trimming and engine.trimming


def test_structural_applies_from_the_first_call_in_both_adapters(tmp_path):
    # Until 0.3.5 the proxy did this and AutoTrim waited for the first learned config.
    sent = AutoTrim(tmp_path, min_runs=3, structural="toon").output(billing.TOOL, {}, billing.output(0))
    assert sent != billing.output(0) and not sent.startswith("{")


def test_the_core_imports_no_sdk():
    code = ("import sys, tracetrim, tracetrim.contract, tracetrim.engine, tracetrim.auto, tracetrim.skill, "
            "tracetrim.proxy; bad = [m for m in ('mcp', 'mcp_types', 'openai', 'anthropic', 'pydantic', 'httpx') "
            "if m in sys.modules]; print(bad); sys.exit(bool(bad))")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
