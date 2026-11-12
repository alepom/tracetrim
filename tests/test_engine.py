"""The core contract and the engine both adapters use (tracetrim.contract, tracetrim.engine)."""
import json
import subprocess
import sys
from pathlib import Path

from tracetrim.auto import AutoTrim, Learner
from tracetrim.contract import FILTERED, FULL, RECOVERED, Evidence, ToolCall, ToolResult  # noqa: F401
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
    obs = engine.observe(call(3), ToolResult(billing.output(3)))
    back = engine.expand(obs.call_id, ["data[].footer"])
    assert json.loads(back) == {"data": [{"footer": "Thank you for your business with us"}]}
    assert engine.expand("c0000", ["*"]).startswith("Error: unknown call_id 'c0000'.")
    [note] = [n for n in engine.learner.notes if n["kind"] == "expand"]
    assert (note["tool"], note["call_id"], note["recovered"]) == (billing.TOOL, obs.call_id, ["data[].footer"])
    from tracetrim.evidence import derive
    found, broad = derive(engine.learner.events, engine.learner.notes, None)
    assert Evidence(RECOVERED, billing.TOOL, obs.call_id, ("data[].footer",)) in found and not broad


def test_expand_takes_the_index_of_an_element_and_gives_that_element_only():
    from tracetrim.engine import Trimmer, generic
    t = Trimmer(None)
    payload = {"data": [{"id": "a", "footer": "one", "lines": [{"sku": "x"}, {"sku": "y"}]},
                        {"id": "b", "footer": "two", "lines": [{"sku": "z"}]},
                        {"id": "c", "footer": "three", "lines": []}], "has_more": False}
    t.originals["c1"], t.hidden["c1"], t.tool_of["c1"] = payload, {"data[].footer", "data[].lines[].sku"}, "invoices"
    # `data[1].footer` is `data[].footer` for the second element: the same field, of one element.
    text, paths = t.recover("c1", ["data[1].footer"])
    assert json.loads(text) == {"data": [{"footer": "two"}]} and paths == ("data[].footer",)
    # Without an index, as before: every element.
    assert json.loads(t.expand("c1", ["data[].footer"])) == {"data": [{"footer": "one"}, {"footer": "two"}, {"footer": "three"}]}
    # A whole element, a nested index, two elements: in the original order, the others left out.
    assert json.loads(t.expand("c1", ["data[2]"])) == {"data": [{"id": "c", "footer": "three"}]}
    assert json.loads(t.expand("c1", ["data[0].lines[1].sku"])) == {"data": [{"lines": [{"sku": "y"}]}]}
    assert json.loads(t.expand("c1", ["data[2].footer", "data[0].footer"])) == {"data": [{"footer": "one"}, {"footer": "three"}]}
    # An index and a plain path together: the plain one still means every element.
    assert json.loads(t.expand("c1", ["data[0].id", "data[].footer"]))["data"] == [
        {"id": "a", "footer": "one"}, {"footer": "two"}, {"footer": "three"}]
    # An element that is not there matches nothing, and the reply says what there is.
    text, paths = t.recover("c1", ["data[7].footer"])
    assert text.startswith("Error: no field matches data[7].footer. Hidden groups: data[].footer") and paths == ()
    assert generic("tasks[0].result[12].items[3].title") == "tasks[].result[].items[].title"


def test_an_index_in_a_request_is_evidence_about_the_field(tmp_path):
    engine = learned(tmp_path)
    obs = engine.observe(call(3), ToolResult(billing.output(3)))
    assert json.loads(engine.expand(obs.call_id, ["data[0].footer"])) == {"data": [{"footer": "Thank you for your business with us"}]}
    note = engine.learner.notes[-1]
    assert note["requested"] == ["data[].footer"] and note["recovered"] == ["data[].footer"]
    assert engine.learner.state["expanded"] == {billing.TOOL: 1}


def test_an_unknown_call_id_is_told_which_ids_exist_and_what_expand_is_for(tmp_path):
    engine = learned(tmp_path)
    # Before any result was trimmed in this session.
    first = engine.expand("c12", ["items"])
    assert first.startswith("Error: unknown call_id 'c12'.") and first.endswith("No result has been trimmed so far.")
    ids = [engine.observe(call(3), ToolResult(billing.output(3))).call_id for _ in range(4)]
    reply = engine.expand(ids[0][:5], ["items[10:]"])                    # an id cut short, asking for more rows
    assert "expand only works on a result that ends with a line '[trimmed] call_id=...'" in reply
    assert "expand cannot give more rows of it" in reply
    # The three most recent, the last first, each with its tool; the oldest is not named.
    assert reply.endswith("Most recent valid call_ids: " + ", ".join(f"{c} ({billing.TOOL})" for c in ids[:0:-1]) + ".")
    assert ids[0] not in reply.split("Most recent")[1]
    # Nothing is learned from it, and it counts against no tool.
    assert engine.learner.state["expanded"] == {} and not [n for n in engine.learner.notes if n["kind"] == "expand"]


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


def test_calls_after_an_answer_are_counted_only_within_the_idle_pause(tmp_path, monkeypatch):
    import tracetrim.auto as auto
    now = [1000.0]
    monkeypatch.setattr(auto.time, "time", lambda: now[0])
    engine = Engine(Learner(tmp_path, min_runs=2, idle_seconds=300))
    for pause in (10, 301, 300):
        engine.observe(call(0), ToolResult(billing.output(0)))
        engine.close(billing.answer(0))
        now[0] += pause
    engine.observe(call(0), ToolResult(billing.output(0)))
    assert engine.learner.state["answered_runs_followed_by_calls"] == 2      # after 10 s and 300 s, not 301
    # A function-calling loop closes its own runs: nothing to count.
    tt = AutoTrim(tmp_path / "fc", min_runs=2)
    for _ in range(3):
        tt.output(billing.TOOL, billing.arguments(0), billing.output(0))
        tt.done(billing.answer(0))
    assert tt.learner.state["answered_runs_followed_by_calls"] == 0


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


def test_a_shared_engine_never_names_the_call_ids_it_holds(tmp_path):
    # Over HTTP several clients can reach one proxy: an error naming valid ids would let one
    # client expand another's results. The reply still says what expand is for.
    engine = Engine(Learner(tmp_path, min_runs=3), shared=True)
    for k in (1, 2, 3):
        engine.observe(call(k), ToolResult(billing.output(k)))
        engine.close(billing.answer(k))
    ids = [engine.observe(call(3), ToolResult(billing.output(3))).call_id for _ in range(2)]
    assert all(ids)
    reply = engine.expand("c0000", ["*"])
    assert reply.startswith("Error: unknown call_id 'c0000'.") and "[trimmed] call_id=..." in reply
    assert reply.endswith("Copy the call_id exactly as that line writes it.")
    assert not any(c in reply for c in ids) and "Most recent" not in reply


def test_the_http_proxy_builds_a_shared_engine_and_stdio_does_not(monkeypatch):
    import asyncio

    import pytest
    from tracetrim import proxy

    seen = []

    class Built(Exception):
        pass

    def fake_server(o):
        seen.append(o.shared)
        raise Built

    monkeypatch.setattr(proxy, "_server", fake_server)
    with pytest.raises(Built):
        asyncio.run(proxy.serve_http(proxy.Options(), host="127.0.0.1", port=0, token=None, allowed_hosts=[]))
    with pytest.raises(Built):
        asyncio.run(proxy.serve(proxy.Options()))
    assert seen == [True, False]
    # The proxy's default for the switch-off is the library's.
    from tracetrim.evidence import Adaptive
    assert proxy.Options().switch_off_runs == Adaptive().switch_off_runs


# --- Review of 2026-11-12: defects found by running code, each pinned here. ---

def test_an_expanded_value_keeps_the_place_of_its_record():
    # The reply used to leave out the elements that lack the field: a model reading in order
    # took C3's price for B2's. Now an addressed element keeps its place, empty when it has nothing.
    from tracetrim.engine import Trimmer
    from tracetrim.projection import Projection
    from tracetrim.runtime import Policy
    a, c = "10 EUR, " + "list price " * 40, "30 EUR, " + "list price " * 40     # long enough to be worth trimming
    out = json.dumps({"data": [{"id": "A1", "price": a}, {"id": "B2"}, {"id": "C3", "price": c}]})
    proj = Projection("prudent", "full", {"t": {"data[].id"}}, {"t": {"data[].id", "data[].price"}})
    tr = Trimmer(Policy({"format": "tracetrim/config@1", "tools": {"t": {"trim": "auto", "expand": True}}}, proj))
    obs = tr.observe("t", out)
    assert obs.call_id
    assert json.loads(tr.expand(obs.call_id, ["data[].price"])) == {"data": [{"price": a}, {}, {"price": c}]}
    assert json.loads(tr.expand(obs.call_id, ["*"])) == {"data": [{"price": a}, {}, {"price": c}]}
    assert json.loads(tr.expand(obs.call_id, ["data[1].price", "data[2].price"])) == {"data": [{}, {"price": c}]}


def test_with_unseen_drop_every_removed_field_is_listed_and_can_be_expanded():
    from tracetrim.engine import FILTERED, Trimmer
    from tracetrim.projection import Projection
    from tracetrim.runtime import Policy
    out = json.dumps({"id": "a1", "x": "seen and dropped " * 5, "extra": "never seen " * 20})
    proj = Projection("prudent", "full", {"t": {"id"}}, {"t": {"id", "x"}}, unseen="drop")
    tr = Trimmer(Policy({"format": "tracetrim/config@1", "tools": {"t": {"trim": "auto", "expand": True}}}, proj))
    obs = tr.observe("t", out)
    assert obs.exposure == FILTERED and set(obs.hidden) == {"x", "extra"} and "extra" in obs.text
    assert json.loads(tr.expand(obs.call_id, ["extra"])) == {"extra": "never seen " * 20}


def test_a_top_level_list_trimmed_to_nothing_stays_a_list():
    from tracetrim.projection import Projection
    proj = Projection("prudent", "full", {"t": {"[].id"}}, {"t": {"[].id", "[].note"}})
    assert proj.apply("t", json.dumps([{"note": "n"}, {"note": "m"}])) == "[]"


def test_two_threads_can_write_the_same_file_atomically(tmp_path):
    import threading

    from tracetrim.store import write_atomic
    errors = []

    def work(tag):
        for i in range(200):
            try:
                write_atomic(tmp_path / "state.json", f'{{"{tag}": {i}}}')
            except Exception as e:       # noqa: BLE001
                errors.append(e)
    threads = [threading.Thread(target=work, args=(t,)) for t in "ab"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and json.loads((tmp_path / "state.json").read_text())
    assert not list(tmp_path.glob(".*.tmp"))
