"""What tracetrim 0.3.5 does, pinned before the adaptive-learning work changes it.

Written on the untouched 0.3.5 code (commit 20ed6d4) and kept passing since:
closing a run (`tracetrim_done`, `AutoTrim.done`), identifier protection,
transformed values, `expand` and the switch-off of a tool expanded too often,
and loading what earlier versions wrote. `fixtures/workdir_035` is a work
directory exactly as 0.3.5 left it; `fixtures/formats` holds older files.
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest

from tracetrim import analyse, learn, load
from tracetrim.auto import EXPAND_MAX_RATE, EXPAND_MIN_CALLS, AutoTrim, Learner
from tracetrim.evidence import Adaptive
from tracetrim.projection import Projection
from tracetrim.runtime import Policy

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(FIXTURES))
import billing  # noqa: E402
from meta_server import PAYLOAD as VOLUMES  # noqa: E402

ANSWER = "spa weekend has 1600 searches a month."


def learned(tmp_path, runs=3, **kw) -> AutoTrim:
    tt = AutoTrim(tmp_path, min_runs=runs, **kw)
    for i in range(runs):
        tt.output(billing.TOOL, billing.arguments(i), billing.output(i))
        tt.done(billing.answer(i))
    assert tt.trimming
    return tt


def call_id(sent: str) -> str:
    return sent.rsplit("call_id=", 1)[1].split()[0]


# -- closing a run ---------------------------------------------------------

def proxy_session(work, script, min_runs=2):
    """Drive the proxy over stdio, in automatic mode, in front of the fixture server."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    params = StdioServerParameters(command=sys.executable, cwd=str(ROOT), args=[
        "-m", "tracetrim.cli", "proxy", "--upstream-cmd", f"{sys.executable} {FIXTURES / 'meta_server.py'}",
        "--auto", str(work), "--min-runs", str(min_runs), "--quiet"])

    async def go():
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            return await script(s)
    return asyncio.run(go())


def text(result) -> str:
    return result.content[0].text


def test_tracetrim_done_closes_the_run_and_learning_starts_after_min_runs(tmp_path):
    async def script(s):
        tools = {t.name: t for t in (await s.list_tools()).tools}
        sent = []
        for _ in range(3):
            sent.append(text(await s.call_tool("volumes", {})))
            done = text(await s.call_tool("tracetrim_done", {"answer": ANSWER}))
        return tools, sent, done

    tools, sent, done = proxy_session(tmp_path, script)
    assert {"volumes", "expand", "tracetrim_done"} == set(tools)
    assert tools["tracetrim_done"].input_schema["required"] == ["answer"]
    assert done == "Recorded."
    # Two runs to learn from: the first two outputs arrive whole, the third trimmed.
    assert "[trimmed]" not in sent[0] + sent[1] and "\n[trimmed] call_id=c" in sent[2]
    assert "note_0" not in sent[2].split("\n[trimmed] ")[0]
    run = [json.loads(line) for line in (tmp_path / "traces" / "run-0001.jsonl").open()]
    assert [e["kind"] for e in run] == ["call", "message"]
    assert run[0]["tool"] == "volumes" and json.loads(run[0]["output"]) == VOLUMES    # the original, not the trimmed
    assert run[1] == {"kind": "message", "text": ANSWER}                              # the answer, nothing else
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["runs"] == 3 and state["complete_runs"] == 3 and state["trimmed"] == {"volumes": 1}


def test_a_repeated_tracetrim_done_does_not_open_a_second_run(tmp_path):
    async def script(s):
        await s.call_tool("volumes", {})
        return [text(await s.call_tool("tracetrim_done", {"answer": ANSWER})) for _ in range(3)]

    assert proxy_session(tmp_path, script) == ["Recorded."] * 3
    assert [p.name for p in (tmp_path / "traces").iterdir()] == ["run-0001.jsonl"]
    assert json.loads((tmp_path / "state.json").read_text())["complete_runs"] == 1


def test_a_session_that_ends_without_tracetrim_done_is_kept_for_telemetry_only(tmp_path):
    async def script(s):
        await s.call_tool("volumes", {})

    proxy_session(tmp_path, script)
    assert [p.name for p in (tmp_path / "traces").iterdir()] == ["run-0001-noanswer.jsonl"]
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["runs"] == 1 and state["complete_runs"] == 0
    assert json.loads((tmp_path / "report.config.json").read_text())["tools"] == {}
    assert (tmp_path / "report.html").exists()


def test_autotrim_done_writes_the_answer_after_the_calls_and_only_once(tmp_path):
    tt = AutoTrim(tmp_path, min_runs=2)
    tt.output(billing.TOOL, json.dumps(billing.arguments(0)), billing.output(0), turn=0)   # arguments as JSON text
    tt.done(billing.answer(0))
    tt.done(billing.answer(0))                                  # nothing to close: ignored
    tt.done(None)
    [path] = (tmp_path / "traces").iterdir()
    run = [json.loads(line) for line in path.open()]
    assert run[0]["arguments"] == billing.arguments(0) and run[0]["turn"] == 0
    assert run[1] == {"kind": "message", "text": billing.answer(0), "turn": 1}
    assert tt.learner.state["runs"] == 1 and not tt.trimming

    tt.output(billing.TOOL, billing.arguments(1), billing.output(1))
    tt.done("  ")                                               # a blank answer is no answer
    assert sorted(p.name for p in (tmp_path / "traces").iterdir()) == ["run-0001.jsonl", "run-0002-noanswer.jsonl"]
    assert tt.learner.state["complete_runs"] == 1


# -- what the projection keeps ---------------------------------------------

def test_identifiers_are_kept_at_run_time_even_when_never_used(tmp_path):
    tt = learned(tmp_path)
    sent = tt.output(billing.TOOL, billing.arguments(3), billing.output(3))
    body = json.loads(sent.rsplit("\n[trimmed] ", 1)[0])
    # No answer ever names the account; `footer` is as unused, and goes.
    assert body["data"][0]["account_id"] == "acct_773" and "footer" not in body["data"][0]
    assert not any(billing.payload(i)["data"][0]["account_id"] in billing.answer(i) for i in range(4))


def test_transformed_values_count_as_used(tmp_path):
    tt = learned(tmp_path)
    kept = tt.trimmer.policy.projection.keep[billing.TOOL]
    # Cents written as euros, a timestamp written as a date.
    assert {"data[].amount_due", "data[].created"} <= kept
    usages = [analyse(load(p), normalize=False) for p in sorted((tmp_path / "traces").glob("run-*.jsonl"))]
    plain = learn(usages, "prudent").keep[billing.TOOL]
    assert not {"data[].amount_due", "data[].created"} & plain


def test_values_too_generic_to_attribute_are_kept_by_the_prudent_projection(tmp_path):
    kept = learned(tmp_path).trimmer.policy.projection.keep[billing.TOOL]
    assert "has_more" in kept


# -- expand -----------------------------------------------------------------

def test_expand_returns_hidden_fields_from_the_original_without_a_new_call(tmp_path):
    tt = learned(tmp_path)
    sent = tt.output(billing.TOOL, billing.arguments(3), billing.output(3))
    assert sent.rsplit("\n", 1)[1].startswith("[trimmed] call_id=c") and "data[].footer" in sent
    back = json.loads(tt.expand({"call_id": call_id(sent), "fields": ["data[].footer"]}))
    assert back == {"data": [{"footer": "Thank you for your business with us"}]}
    everything = json.loads(tt.expand(json.dumps({"call_id": call_id(sent), "fields": ["*"]})))
    assert set(everything["data"][0]) == {"footer", *billing.REMARKS}
    # (0.3.5 answered "Error: unknown call_id." and nothing else: changed on purpose, the reply now says what to do.)
    assert tt.expand({"call_id": "c0000", "fields": ["*"]}).startswith("Error: unknown call_id 'c0000'.")
    assert tt.expand("not json").startswith("Error")
    # Two requests on one trimmed output count once. (0.3.5 counted both: changed on purpose,
    # so that the switch-off of a tool counts results expanded, not requests.)
    assert tt.learner.state["expanded"] == {billing.TOOL: 1} and tt.learner.state["trimmed"] == {billing.TOOL: 1}
    # expand is not a tool call of the run: the trace holds the original call only.
    assert [e["tool"] for e in tt.learner.events] == [billing.TOOL]


@pytest.mark.parametrize("trimmed, expanded, off", [
    (EXPAND_MIN_CALLS, EXPAND_MIN_CALLS // 2, True),            # half of four trimmed calls: switched off
    (EXPAND_MIN_CALLS, EXPAND_MIN_CALLS // 2 - 1, False),       # below the rate
    (EXPAND_MIN_CALLS - 1, EXPAND_MIN_CALLS - 1, False),        # too few trimmed calls to judge
])
def test_a_tool_expanded_too_often_stops_being_trimmed(tmp_path, trimmed, expanded, off):
    assert (EXPAND_MIN_CALLS, EXPAND_MAX_RATE) == (4, 0.5)
    # Up to 0.3.5 one run was enough to switch a tool off; since 02c the default asks for two
    # runs, and switch_off_runs=1 is the documented way back to the 0.3.5 rule pinned here.
    tt = learned(tmp_path, adaptive=Adaptive(switch_off_runs=1))
    for n in range(trimmed):
        sent = tt.output(billing.TOOL, billing.arguments(3), billing.output(3))
        if n < expanded:
            tt.expand({"call_id": call_id(sent), "fields": ["data[].footer"]})
    tt.done(billing.answer(3))
    assert (billing.TOOL in tt.learner.state["off"]) is off
    assert tt.trimming is not off
    whole = tt.output(billing.TOOL, billing.arguments(3), billing.output(3))
    assert ("[trimmed]" not in whole and "footer" in whole) is off
    # The decision is in the files, so it survives a restart.
    assert (billing.TOOL in json.loads((tmp_path / "report.config.json").read_text())["auto"]["switched_off"]) is off
    assert AutoTrim(tmp_path, min_runs=3).trimming is not off


# -- files written by earlier versions --------------------------------------

@pytest.fixture
def workdir_035(tmp_path):
    work = tmp_path / "work"
    shutil.copytree(FIXTURES / "workdir_035", work, ignore=shutil.ignore_patterns("expected_trimmed.json"))
    (work / "traces").mkdir()
    for p in work.glob("run-*.jsonl"):
        p.rename(work / "traces" / p.name)
    return work


def expected_trimmed() -> dict:
    return json.loads((FIXTURES / "workdir_035" / "expected_trimmed.json").read_text())


def test_a_035_work_directory_is_picked_up_and_trims_as_before(workdir_035):
    tt = AutoTrim(workdir_035, min_runs=3)
    assert tt.trimming and tt.learner.state["complete_runs"] == 3
    sent = tt.output(billing.TOOL, billing.arguments(3), billing.output(3))
    body, line = sent.rsplit("\n[trimmed] ", 1)
    assert json.loads(body) == expected_trimmed()
    assert line.split("hidden: ")[1] == ", ".join(["data[].footer", *(f"data[].{r}" for r in billing.REMARKS)])


def test_035_traces_are_learned_from_again_with_the_same_result(workdir_035):
    lr = Learner(workdir_035, min_runs=3)
    before = json.loads((workdir_035 / "report.projection.json").read_text())
    policy = lr.relearn()
    after = json.loads((workdir_035 / "report.projection.json").read_text())
    assert policy is not None and after == before
    assert json.loads(policy.apply(billing.TOOL, billing.output(3))) == expected_trimmed()


def test_a_035_config_is_applied_by_policy_and_by_the_cli(workdir_035, capsys, monkeypatch):
    import io
    from tracetrim.cli import main
    policy = Policy.load(workdir_035 / "report.config.json")
    assert policy.expand_enabled(billing.TOOL) and policy.structural is None
    assert json.loads(policy.apply(billing.TOOL, billing.output(3))) == expected_trimmed()
    monkeypatch.setattr(sys, "stdin", io.StringIO(billing.output(3)))
    main(["apply", str(workdir_035 / "report.config.json"), billing.TOOL])
    assert json.loads(capsys.readouterr().out) == expected_trimmed()


def test_a_state_file_without_the_newer_keys_loads_with_defaults(tmp_path):
    shutil.copy(FIXTURES / "formats" / "state_v030.json", tmp_path / "state.json")
    state = Learner(tmp_path).state
    assert state["runs"] == 7 and state["off"] == ["search_customers"] and state["expanded"] == {"list_invoices": 2}
    assert state["config_from_runs"] == 0                       # absent from the file


def test_a_projection_saved_by_01_loads_and_passes_unseen_fields():
    p = Projection.load(FIXTURES / "formats" / "projection_v01.json")
    assert p.unseen == "keep" and p.seen == p.keep              # no `dropped` list: nothing known to remove
    out = json.loads(p.apply(billing.TOOL, billing.output(0)))
    assert out == billing.payload(0)


def test_a_config_without_projection_only_reserialises():
    policy = Policy.load(FIXTURES / "formats" / "config_no_projection.json")
    assert policy.projection is None
    assert policy.apply("any_tool", '{"citt\\u00e0": "Forl\\u00ec"}') == '{"città": "Forlì"}'
    with pytest.raises(ValueError):
        Policy.load(FIXTURES / "formats" / "projection_v01.json")


def test_traces_with_and_without_turns_and_with_text_arguments_load(tmp_path):
    lines = [{"kind": "prompt", "text": "go"},
             {"kind": "call", "tool": "t", "arguments": '{"q": "x"}', "output": '{"a": 1}'},
             {"kind": "call", "tool": "t", "arguments": {"q": "y"}, "output": None},
             {"kind": "message", "text": "done"}]
    p = tmp_path / "old.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in lines) + "\n\n")
    t = load(p)
    assert [e.kind for e in t.events] == ["prompt", "call", "call", "message"]
    assert t.events[1].text == '{"q": "x"}' and t.events[2].text == '{"q": "y"}'
    assert all(e.turn is None for e in t.events) and len(t.calls) == 1
    (tmp_path / "bad.jsonl").write_text('{"kind": "other"}\n')
    with pytest.raises(ValueError):
        load(tmp_path / "bad.jsonl")
