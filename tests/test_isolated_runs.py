"""Ownership and ordering inside an isolated HTTP proxy."""
import asyncio
import json
from pathlib import Path

import pytest
from unittest.mock import patch

from tracetrim import store
from tracetrim.auto import Learner, Run
from tracetrim.contract import ToolCall, ToolResult
from tracetrim.engine import Engine
from tracetrim.proxy import Live
from tracetrim.sessions import HttpRuns


def test_unknown_and_foreign_handles_take_the_same_path_before_recovery():
    engine = Engine(shared=True)
    runs = HttpRuns(engine, lambda: Live(True), 300, lambda msg: None)
    caller = runs.session(b"one")
    engine.trimmer.originals["fixture"] = {"name": "private fixture value"}
    runs.owners["fixture"] = (b"two", Run())
    with patch.object(engine.trimmer, "recover", side_effect=AssertionError("looked up original")):
        absent = runs.expand(caller, "absent", ["*"])
        foreign = runs.expand(caller, "fixture", ["*"])
    assert foreign == absent.replace("'absent'", "'fixture'")
    assert foreign.startswith("Error: unknown call_id")
    assert "private fixture value" not in foreign and "Most recent valid" not in foreign


def test_late_expansion_charges_the_run_that_produced_the_original(tmp_path):
    learner = Learner(tmp_path)
    engine = Engine(learner, shared=True)
    runs = HttpRuns(engine, lambda: Live(True), 300, lambda msg: None)
    session = runs.session(b"owner")
    with learner.use_run(session.run):
        engine.observe(ToolCall("example", {}), ToolResult("one"))
        original = {"name": "value"}
        cid = "c00000001"
        engine.trimmer.originals[cid] = original
        engine.trimmer.hidden[cid] = {"name"}
        engine.trimmer.tool_of[cid] = "example"
        runs.remember(session, cid)
        learner.done("one")
    session.run = Run()
    with learner.use_run(session.run):
        engine.observe(ToolCall("example", {}), ToolResult("two"))
        assert json.loads(runs.expand(session, cid, ["name"])) == original
        learner.done("two")
    sizes = json.loads((tmp_path / "savings.json").read_text())["runs"]
    assert sizes["run-0001"]["expand"]["calls"] == 1 and sizes["run-0002"]["expand"]["calls"] == 0
    notes = (tmp_path / "evidence" / "run-0001.jsonl").read_text().splitlines()
    assert [json.loads(n)["kind"] for n in notes] == ["expand"]
    engine.trimmer.forget(cid)
    assert cid not in runs.owners


def test_closing_waits_for_earlier_calls_and_holds_later_calls(tmp_path):
    async def go():
        learner = Learner(tmp_path)
        runs = HttpRuns(Engine(learner), lambda: Live(True), 300, lambda msg: None)
        session = runs.session(b"one")
        entered, release = asyncio.Event(), asyncio.Event()
        order = []

        async def step(action):
            async with runs.request(session, closing=action == "done"):
                if action == "first":
                    entered.set()
                    await release.wait()
                order.append(action)

        pending = asyncio.create_task(step("first"))
        await entered.wait()
        closing = asyncio.create_task(step("done"))
        later = asyncio.create_task(step("next"))
        await asyncio.sleep(0)
        assert not order
        release.set()
        await asyncio.gather(pending, closing, later)
        assert order == ["first", "done", "next"]
    asyncio.run(go())


def test_live_totals_cover_the_process_and_reset_only_the_closed_run(capsys):
    process = Live()
    left, right = Live(process=process), Live(process=process)
    left.per_run = right.per_run = True
    left.call("volumes", "one", "one")
    right.call("volumes", "two", "two")
    assert "this run: 1 calls" in capsys.readouterr().err
    assert (process.calls, process.raw, process.sent) == (2, 2, 2)
    assert left.new_run().startswith("1 calls")
    right.call("volumes", "three", "three")
    stderr = capsys.readouterr().err
    assert "this run: 2 calls" in stderr and "process: 3 calls" in stderr


@pytest.mark.parametrize("restart", [False, True])
def test_interrupted_isolated_close_is_retryable_and_published_once(tmp_path, monkeypatch, restart):
    learner = Learner(tmp_path, durable_closes=True)
    engine = Engine(learner)
    engine.observe(ToolCall("example", {}), ToolResult("one"))
    replace = store.os.replace

    def fail_at(name):
        def replace_unless_blocked(src, dst):
            if Path(dst).name == name:
                raise OSError("simulated write failure")
            return replace(src, dst)
        return replace_unless_blocked

    # Before the commit point the unanswered calls still belong to the open run.
    with monkeypatch.context() as context:
        context.setattr(store.os, "replace", fail_at(store.JOURNAL))
        with pytest.raises(OSError):
            learner.done("one")
    assert learner.state["runs"] == 0 and len(learner.events) == 1
    assert not list((tmp_path / "traces").glob("*.jsonl"))

    # The trace is published before the failed state write. Replaying the
    # snapshots, even after process restart, must neither duplicate nor lose it.
    with monkeypatch.context() as context:
        context.setattr(store.os, "replace", fail_at("state.json"))
        with pytest.raises(OSError):
            learner.done("one")
    assert (tmp_path / store.JOURNAL).is_file()
    assert (tmp_path / "traces/run-0001.jsonl").is_file() and not learner.events
    if restart:
        learner = Learner(tmp_path)
        engine = Engine(learner)
    # The retry: nothing left to close; the relearning the finished publication owes is made (Engine.close).
    assert engine.close("one") is None and not (tmp_path / store.JOURNAL).exists()
    assert len(list((tmp_path / "traces").glob("*.jsonl"))) == 1
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["runs"] == state["complete_runs"] == 1
    savings = json.loads((tmp_path / "savings.json").read_text())
    assert savings["runs"]["run-0001"]["tools"]["example"]["calls"] == 1
    assert "report_pending" not in state
    assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == 1
