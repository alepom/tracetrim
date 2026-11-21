"""`--background-relearn` (1.4): tracetrim_done replies once the run is on disk; the relearning follows."""
import asyncio
import json
import re
import sys
import threading
import time
from pathlib import Path

import pytest
from mcp import Client

from tracetrim import auto, proxy

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = f"{sys.executable} {ROOT / 'tests' / 'fixtures' / 'meta_server.py'}"
ANSWER = "spa weekend has 1600 searches a month."


def options(work, **kw):
    return proxy.Options(upstream_cmd=UPSTREAM, auto_dir=str(work), quiet=True, min_runs=2, **kw)


@pytest.fixture
def slow(monkeypatch):
    """Relearning that takes `delay` seconds, counted, and the number of runs each call saw."""
    original = auto.Learner.relearn
    seen = {"calls": 0, "runs": [], "delay": 0.0}
    lock = threading.Lock()

    def relearn(self):
        time.sleep(seen["delay"])
        with lock:
            seen["calls"] += 1
            seen["runs"].append(len(list((self.dir / "traces").glob("run-*.jsonl"))))
        return original(self)
    monkeypatch.setattr(auto.Learner, "relearn", relearn)
    return seen


async def run(client):
    assert not (await client.call_tool("volumes", {})).is_error
    start = time.perf_counter()
    reply = await client.call_tool("tracetrim_done", {"answer": ANSWER})
    assert reply.content[0].text == auto.DONE_REPLY
    return time.perf_counter() - start


async def settled(learner):
    while learner.background.task is not None and not learner.background.task.done():
        await asyncio.sleep(0.01)


def test_off_by_default_and_the_cli_needs_auto(monkeypatch):
    assert proxy.Options().background_relearn is False
    from tracetrim import cli
    seen = []

    async def fake_serve(o):
        seen.append(o.background_relearn)
    monkeypatch.setattr(proxy, "serve", fake_serve)
    monkeypatch.setattr(cli, "serve", fake_serve, raising=False)
    cli.main(["proxy", "--upstream-cmd", "true", "--auto", "w"])
    cli.main(["proxy", "--upstream-cmd", "true", "--auto", "w", "--background-relearn"])
    assert seen == [False, True]
    with pytest.raises(SystemExit, match="add --auto"):
        cli.main(["proxy", "--upstream-cmd", "true", "--background-relearn"])


@pytest.mark.parametrize("on", [False, True])
def test_the_reply_no_longer_waits_for_the_relearning(tmp_path, slow, on):
    slow["delay"] = 1.0

    async def go():
        server, learner = proxy._server(options(tmp_path, background_relearn=on))
        async with Client(server) as client:
            seconds = await run(client)
            if on:
                assert learner.background.task is not None and not learner.background.task.done()
                await settled(learner)
        return seconds
    seconds = asyncio.run(go())
    assert (seconds < 0.6) if on else (seconds >= 1.0)
    assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == 1


def test_the_reply_is_early_but_the_next_request_waits_for_the_relearning(tmp_path, slow):
    """Only the reply to tracetrim_done is earlier: the next call is served after the relearning of the closing,
    with the policy learned from every run closed so far, as without the option."""
    slow["delay"] = 0.5

    async def go():
        server, learner = proxy._server(options(tmp_path, background_relearn=True))
        async with Client(server) as client:
            for i in range(3):
                assert await run(client) < 0.4                       # the reply does not wait
                start = time.perf_counter()
                assert not (await client.call_tool("volumes", {})).is_error
                assert time.perf_counter() - start >= 0.4            # the next request does
                assert learner.background.task.done()
                assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == i + 1
    asyncio.run(go())
    assert slow["calls"] == 3 and slow["runs"] == [1, 2, 3]


def test_requests_for_a_relearning_while_one_runs_fold_into_one_more(tmp_path, slow):
    slow["delay"] = 0.3

    async def go():
        server, learner = proxy._server(options(tmp_path, background_relearn=True))
        async with Client(server) as client:
            await run(client)
            for _ in range(3):                    # e.g. idle expiries and finished closings, while it runs
                learner.background.schedule()
            await learner.background.settled()
    asyncio.run(go())
    assert slow["calls"] == 2                     # the one running, and one more: never one per request


def engine_trims(learner):
    config = json.loads((learner.dir / "report.config.json").read_text())
    return bool(config["tools"])


class FixedClock:
    """Both modes at the same instant: the time of a relearning is written in the report and the config."""
    import datetime as _dt

    class datetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 21, 12, 0, 0, tzinfo=tz)


def deterministic(monkeypatch):
    from tracetrim import auto as auto_module
    from tracetrim import engine, evidence
    from tracetrim import report
    monkeypatch.setattr(auto_module, "datetime", FixedClock.datetime)
    monkeypatch.setattr(report, "datetime", FixedClock.datetime)
    monkeypatch.setattr(evidence, "datetime", FixedClock.datetime)     # the time of a promotion
    counter = iter(range(10**6))
    monkeypatch.setattr(engine.secrets, "token_hex", lambda n: f"{next(counter):0{2 * n}x}")


def snapshot(work: Path) -> dict:
    return {p.relative_to(work).as_posix(): p.read_bytes() for p in sorted(work.rglob("*")) if p.is_file()}


async def sequence(work, on, runs=6, expand_from=3, overlap=False):
    """`runs` runs; from run `expand_from` on, the agent expands a hidden field of its call. With `overlap` the
    next run starts right after the reply, while the relearning runs."""
    server, learner = proxy._server(options(work, background_relearn=on))
    async with Client(server) as client:
        for i in range(1, runs + 1):
            out = (await client.call_tool("volumes", {})).content[0].text
            m = re.search(r"call_id=(c[0-9a-f]{8})", out)
            if i >= expand_from and m:
                await client.call_tool("expand", {"call_id": m.group(1), "fields": ["items[].note_0"]})
            await client.call_tool("tracetrim_done", {"answer": ANSWER})
            if on and not overlap:
                await settled(learner)
        if on:
            await learner.background.drain()
    return learner


@pytest.mark.parametrize("overlap", [False, True])
def test_at_the_end_every_file_is_byte_identical_to_the_mode_without_the_option(tmp_path, monkeypatch, slow, overlap):
    slow["delay"] = 0.2 if overlap else 0.0
    results = {}
    for name, on in (("off", False), ("on", True)):
        deterministic(monkeypatch)
        learner = asyncio.run(sequence(tmp_path / name, on, overlap=overlap))
        results[name] = snapshot(tmp_path / name)
        results[name + "_off"] = learner.state["off"]
    assert results["off"].keys() == results["on"].keys()
    for f in results["off"]:
        assert results["off"][f] == results["on"][f], f
    # The sequence exercises learning from expand: the tool ends switched off in both modes.
    assert results["off_off"] == results["on_off"] == ["volumes"]


def test_two_relearnings_never_run_at_once_during_an_isolated_shutdown(tmp_path):
    """REVISIONE-14b: a run that expires while the proxy stops must not start a second writer."""
    async def scenario():
        server, learner = proxy._server(options(tmp_path, shared=True, isolate_sessions=True, idle_seconds=0.25,
                                                background_relearn=True))
        learn = learner.relearn
        lock = threading.Lock()
        first, second = threading.Event(), threading.Event()
        state = {"active": 0, "peak": 0}

        def tracked():
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
                is_first = not first.is_set()
                first.set()
            try:
                if is_first:
                    second.wait(1.5)
                else:
                    second.set()
                return learn()
            finally:
                with lock:
                    state["active"] -= 1

        learner.relearn = tracked
        async with Client(server) as client:
            await run(client)
            assert await asyncio.to_thread(first.wait, 2)
            assert not (await client.call_tool("volumes", {})).is_error
        return state["peak"], learner
    peak, learner = asyncio.run(scenario())
    assert peak == 1
    assert learner.state["runs"] == 2 and "report_pending" not in json.loads((tmp_path / "state.json").read_text())


def test_a_confirmed_closing_left_out_of_the_report_is_relearned_at_the_next_start(tmp_path, slow):
    """REVISIONE-14b: the closing is pending on disk before `Recorded.`; a copy taken before its relearning ends
    relearns at its first start."""
    import shutil
    copy = tmp_path / "copy"
    work = tmp_path / "work"
    hold = threading.Event()
    original = auto.Learner.relearn

    async def go():
        server, learner = proxy._server(options(work, background_relearn=True))

        def held():
            if learner.state["runs"] == 2:
                hold.wait(5)
            return original(learner)
        learner.relearn = held
        async with Client(server) as client:
            await run(client)
            await settled(learner)
            await run(client)                     # replied; its relearning is held
            shutil.copytree(work, copy)
            hold.set()
            await learner.background.drain()
    asyncio.run(go())
    assert json.loads((copy / "state.json").read_text())["report_pending"] is True
    assert json.loads((copy / "report.json").read_text())["auto"]["complete_runs"] == 1
    auto.Learner(copy, 2)                         # the next start relearns
    assert json.loads((copy / "report.json").read_text())["auto"]["complete_runs"] == 2
    assert "report_pending" not in json.loads((copy / "state.json").read_text())


def test_a_run_closed_during_a_relearning_keeps_the_report_pending(tmp_path, monkeypatch):
    from tracetrim import report
    learner = auto.Learner(tmp_path, 2)
    learner.events.append({"kind": "call", "tool": "t", "arguments": {}, "output": '{"a": 1, "b": 2}'})
    learner.done("1")
    assert json.loads((tmp_path / "state.json").read_text())["report_pending"] is True   # with the run itself
    entered, release = threading.Event(), threading.Event()
    compute = report.compute

    def slow_compute(*a, **kw):
        entered.set()
        release.wait(5)
        return compute(*a, **kw)
    monkeypatch.setattr(report, "compute", slow_compute)
    worker = threading.Thread(target=learner.relearn)
    worker.start()
    assert entered.wait(5)
    learner.events.append({"kind": "call", "tool": "t", "arguments": {}, "output": '{"a": 3, "b": 4}'})
    learner.done("3")                              # a closing while the relearning covers one run only
    release.set()
    worker.join(10)
    assert json.loads((tmp_path / "state.json").read_text())["report_pending"] is True
    monkeypatch.setattr(report, "compute", compute)
    auto.Learner(tmp_path, 2)
    assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == 2


def test_a_report_never_mixes_two_generations(tmp_path, monkeypatch):
    """REVISIONE-14b-bis: a run closed while a relearning computes, with its counts, stays out of that report:
    savings, counters and evidence are those of the runs the relearning listed."""
    from tracetrim import report
    from tracetrim.contract import ToolCall, ToolResult
    from tracetrim.engine import Engine
    learner = auto.Learner(tmp_path, 2)
    engine = Engine(learner)
    engine.observe(ToolCall("t", {}), ToolResult('{"a": 1, "b": 2}'))
    engine.close("1")
    entered, release = threading.Event(), threading.Event()
    compute = report.compute

    def slow_compute(*a, **kw):
        entered.set()
        release.wait(5)
        return compute(*a, **kw)
    monkeypatch.setattr(report, "compute", slow_compute)
    engine.observe(ToolCall("t", {}), ToolResult('{"a": 3, "b": 4}'))
    learner.done("3")
    monkeypatch.setattr(report, "compute", compute)
    learner.relearn()
    monkeypatch.setattr(report, "compute", slow_compute)
    worker = threading.Thread(target=learner.relearn)    # covers runs 1 and 2
    worker.start()
    assert entered.wait(5)
    engine.observe(ToolCall("t", {}), ToolResult('{"a": 5, "b": 6}'))
    learner.done("5")                                     # run 3 and its savings, while the report is computed
    release.set()
    worker.join(10)
    got = json.loads((tmp_path / "report.json").read_text())
    assert [r["run"] for r in got["savings"]["runs"]] == ["run-0001", "run-0002"]
    assert got["auto"]["complete_runs"] == 2 and learner.published["runs"] == 2
    assert json.loads((tmp_path / "state.json").read_text())["report_pending"] is True
    monkeypatch.setattr(report, "compute", compute)
    auto.Learner(tmp_path, 2)                             # the next start covers run 3
    got = json.loads((tmp_path / "report.json").read_text())
    assert [r["run"] for r in got["savings"]["runs"]] == ["run-0001", "run-0002", "run-0003"]


@pytest.mark.parametrize("on", [False, True])
def test_a_run_kept_at_shutdown_is_pending_until_its_report_is_written(tmp_path, monkeypatch, on):
    """REVISIONE-14b-bis: the run that the shutdown keeps is pending on disk with the trace, so a copy of the work
    directory taken between the config and the report relearns at its next start."""
    import shutil
    from tracetrim import store
    work, copy = tmp_path / "work", tmp_path / "copy"
    write_json = store.write_json

    def copying(path, data):
        if Path(path).name == "report.json" and not copy.exists() and len(list((work / "traces").iterdir())) == 2:
            shutil.copytree(work, copy)
        return write_json(path, data)
    monkeypatch.setattr(store, "write_json", copying)

    async def go():
        server, learner = proxy._server(options(work, background_relearn=on))
        async with Client(server) as client:
            await run(client)
            if on:
                await settled(learner)
            assert not (await client.call_tool("volumes", {})).is_error      # open at shutdown
        if on:                                    # what serve and serve_http do at shutdown
            await learner.background.drain()
            await learner.background.flush()
        else:
            learner.flush()
    asyncio.run(go())
    assert sorted(p.name for p in (copy / "traces").iterdir()) == ["run-0001.jsonl", "run-0002-noanswer.jsonl"]
    assert json.loads((copy / "state.json").read_text())["report_pending"] is True
    assert [r["run"] for r in json.loads((copy / "report.json").read_text())["savings"]["runs"]] == ["run-0001"]
    auto.Learner(copy, 2)                         # the next start relearns
    assert [r["run"] for r in json.loads((copy / "report.json").read_text())["savings"]["runs"]] == \
        ["run-0001", "run-0002"]
    assert "report_pending" not in json.loads((copy / "state.json").read_text())


def test_a_trace_written_before_the_state_is_relearned_at_the_next_start(tmp_path):
    """A process stopped between the trace and the state: the run on disk is numbered after the runs the state
    knows, and the next start relearns."""
    learner = auto.Learner(tmp_path, 2)
    learner.events.append({"kind": "call", "tool": "t", "arguments": {}, "output": '{"a": 1, "b": 2}'})
    learner.done("1")
    learner.relearn()
    (tmp_path / "traces" / "run-0002.jsonl").write_text(
        json.dumps({"kind": "call", "tool": "t", "arguments": {}, "output": '{"a": 3, "b": 4}'}) + "\n"
        + json.dumps({"kind": "message", "text": "3"}) + "\n")
    auto.Learner(tmp_path, 2)
    assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == 2


def test_the_projection_is_written_atomically(tmp_path, monkeypatch):
    from tracetrim import store
    from tracetrim.projection import Projection
    (tmp_path / "w").mkdir()
    path = tmp_path / "w" / "report.projection.json"
    Projection("prudent", "full", {"t": {"a"}}, {"t": {"a", "b"}}).save(path)
    before = path.read_bytes()

    def broken(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(store.os, "replace", broken)
    with pytest.raises(OSError):
        Projection("prudent", "full", {"t": {"b"}}, {"t": {"a", "b"}}).save(path)
    assert path.read_bytes() == before and [p.name for p in path.parent.iterdir()] == [path.name]


def test_shutdown_during_a_relearning_completes_it_and_a_restart_needs_nothing(tmp_path, slow):
    slow["delay"] = 0.5

    async def go():
        server, learner = proxy._server(options(tmp_path, background_relearn=True))
        async with Client(server) as client:
            await run(client)
            await run(client)
            assert not learner.background.task.done()
            await learner.background.drain()            # what serve and serve_http do on shutdown
            assert learner.background.task.done()
            # After the drain a closing relearns before replying, as without the option.
            assert await run(client) >= 0.5
        learner.flush()
    asyncio.run(go())
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["auto"]["complete_runs"] == 3
    state = json.loads((tmp_path / "state.json").read_text())
    assert "report_pending" not in state and state["complete_runs"] == 3
    assert not list(tmp_path.rglob("*.tmp")) and not list(tmp_path.rglob("*.journal*"))
    calls = slow["calls"]
    restarted = auto.Learner(tmp_path, 2)                 # a restart relearns only if something was left pending
    assert slow["calls"] == calls and restarted.state["complete_runs"] == 3


def test_isolated_sessions_still_work_with_the_option(tmp_path):
    async def go():
        server, learner = proxy._server(proxy.Options(upstream_cmd=UPSTREAM, auto_dir=str(tmp_path), quiet=True,
                                                      shared=True, isolate_sessions=True, idle_seconds=0.1,
                                                      background_relearn=True))
        async with Client(server) as client:
            assert not (await client.call_tool("volumes", {})).is_error
            for _ in range(100):
                if (tmp_path / "report.json").exists():
                    break
                await asyncio.sleep(0.05)
            assert learner.state["runs"] == 1 and (tmp_path / "traces" / "run-0001-noanswer.jsonl").exists()
            await settled(learner)
            assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == 0
    asyncio.run(go())


def assert_identical(results):
    assert results["off"].keys() == results["on"].keys()
    for f in results["off"]:
        assert results["off"][f] == results["on"][f], f


async def steps(client, names, pause=0.0):
    for name in names:
        args = {"answer": ANSWER} if name == "tracetrim_done" else {"answer": "  "} if name == "blank" else {}
        reply = await client.call_tool("tracetrim_done" if name == "blank" else name, args)
        assert reply.is_error == (name == "blank")
        if name == "tracetrim_done" and pause:
            await asyncio.sleep(pause)


def both_modes(tmp_path, monkeypatch, names, pause=0.0, **kw):
    results = {}
    for name, on in (("off", False), ("on", True)):
        deterministic(monkeypatch)

        async def go(work=tmp_path / name, on=on):
            server, learner = proxy._server(options(work, background_relearn=on, **kw))
            async with Client(server) as client:
                await steps(client, names, pause)
                if on:
                    await learner.background.drain()
        asyncio.run(go())
        results[name] = snapshot(tmp_path / name)
    assert_identical(results)
    return results


def test_waiting_for_a_relearning_is_not_inactivity(tmp_path, monkeypatch, slow):
    """REVISIONE-14b-bis: volumes, done, volumes, volumes, done with no pause, idle pause 0.5 s, relearning 0.85 s:
    two runs in both modes (the wait for the relearning counted as inactivity, and made three)."""
    slow["delay"] = 0.85
    results = both_modes(tmp_path, monkeypatch, ["volumes", "tracetrim_done", "volumes", "volumes", "tracetrim_done"],
                         idle_seconds=0.5)
    assert sorted(f for f in results["on"] if f.startswith("traces/")) == \
        ["traces/run-0001.jsonl", "traces/run-0002.jsonl"]


def test_a_refused_closing_during_a_relearning_is_counted_after_it(tmp_path, monkeypatch, slow):
    """REVISIONE-14b-bis: volumes, done, the same done again, done with a blank answer, the first relearning slow:
    the refusal is in the state and not in the report, in both modes."""
    slow["delay"] = 0.5
    results = both_modes(tmp_path, monkeypatch, ["volumes", "tracetrim_done", "tracetrim_done", "blank"])
    assert json.loads(results["on"]["state.json"])["refused_closings"] == 1
    assert json.loads(results["on"]["report.config.json"])["auto"]["refused_closings"] == 0


@pytest.mark.parametrize("pause,counted", [(0.3, 1), (0.75, 0)])
def test_the_pause_after_an_answer_is_the_agents_in_both_modes(tmp_path, monkeypatch, slow, pause, counted):
    """From the reply to tracetrim_done to the next call (until 1.3 from the closing, which counted the relearning
    the agent waited for without the option, and not with it): the same count in both modes."""
    slow["delay"] = 0.4
    results = both_modes(tmp_path, monkeypatch, ["volumes", "tracetrim_done", "volumes", "tracetrim_done"],
                         pause=pause, idle_seconds=0.5)
    assert json.loads(results["on"]["state.json"])["answered_runs_followed_by_calls"] == counted


def test_every_request_waits_for_the_relearning_in_progress(tmp_path, slow):
    """Only the reply to tracetrim_done is earlier: a listing, a refused closing, a repeated closing, an expand and
    a call that arrive while its relearning runs are served after it."""
    slow["delay"] = 0.5

    async def go():
        server, learner = proxy._server(options(tmp_path, background_relearn=True))
        async with Client(server) as client:
            requests = [lambda: client.list_tools(),
                        lambda: client.call_tool("tracetrim_done", {"answer": " "}),
                        lambda: client.call_tool("tracetrim_done", {"answer": ANSWER}),
                        lambda: client.call_tool("expand", {"call_id": "c00000000", "fields": ["x"]}),
                        lambda: client.call_tool("volumes", {})]
            for request in requests:
                assert await run(client) < 0.4
                start = time.perf_counter()
                await request()
                assert time.perf_counter() - start >= 0.4
                assert learner.background.task.done()
            await learner.background.drain()
    asyncio.run(go())


class Ticker:
    """The longest the event loop went without running a 15 ms timer, while it runs."""

    def __init__(self):
        self.longest = 0.0
        self.task = None

    async def _tick(self):
        last = time.perf_counter()
        while True:
            await asyncio.sleep(0.015)
            now = time.perf_counter()
            self.longest = max(self.longest, now - last)
            last = now

    def __enter__(self):
        self.task = asyncio.get_running_loop().create_task(self._tick())
        return self

    def __exit__(self, *exc):
        self.task.cancel()


def tracking(monkeypatch, hold_first=None, delay=0.45):
    """Every relearning: whether it ran on the main thread, and how many ran at once."""
    original = auto.Learner.relearn
    lock = threading.Lock()
    seen = {"active": 0, "peak": 0, "main": [], "calls": 0}

    def relearn(self):
        with lock:
            seen["active"] += 1
            seen["peak"] = max(seen["peak"], seen["active"])
            seen["calls"] += 1
            first = seen["calls"] == 1
            seen["main"].append(threading.current_thread() is threading.main_thread())
        try:
            if first and hold_first is not None:
                hold_first.wait(5)
            else:
                time.sleep(delay)
            return original(self)
        finally:
            with lock:
                seen["active"] -= 1
    monkeypatch.setattr(auto.Learner, "relearn", relearn)
    return seen


@pytest.mark.parametrize("isolated", [False, True])
def test_the_run_kept_at_shutdown_is_relearned_in_a_thread(tmp_path, monkeypatch, isolated):
    """REVISIONE-14b-bis: the open run that the shutdown keeps is relearned through the coordinator, in a thread:
    the event loop keeps running (it ran the 450 ms relearning itself, with isolated sessions and over HTTP)."""
    seen = tracking(monkeypatch)
    kw = {"shared": True, "isolate_sessions": True} if isolated else {}

    async def go():
        server, learner = proxy._server(options(tmp_path, background_relearn=True, **kw))
        with Ticker() as ticker:
            async with Client(server) as client:
                await run(client)
                await settled(learner)
                assert not (await client.call_tool("volumes", {})).is_error      # open at shutdown
            if not isolated:                      # what serve and serve_http do; isolated: the lifespan does it
                await learner.background.drain()
                await learner.background.flush()
        return ticker.longest, learner
    longest, learner = asyncio.run(go())
    assert seen["calls"] == 2 and not any(seen["main"]) and seen["peak"] == 1
    assert longest < 0.25
    assert learner.state["runs"] == 2 and "report_pending" not in json.loads((tmp_path / "state.json").read_text())


def free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_an_isolated_session_open_at_shutdown_over_http(tmp_path, monkeypatch):
    """REVISIONE-14b-bis, over HTTP with two clients: B has a call recorded, A closes with an answer and its
    relearning is held; the server stops while B is open. One relearning at a time, none on the event loop, and
    the loop keeps running; both runs are in the report."""
    import uvicorn
    servers = []
    init = uvicorn.Server.__init__

    def capture(self, *a, **kw):
        init(self, *a, **kw)
        servers.append(self)
    monkeypatch.setattr(uvicorn.Server, "__init__", capture)
    hold = threading.Event()
    seen = tracking(monkeypatch, hold_first=hold)
    port = free_port()

    async def go():
        o = options(tmp_path, isolate_sessions=True, background_relearn=True)
        serving = asyncio.create_task(proxy.serve_http(o, host="127.0.0.1", port=port, token=None, allowed_hosts=[]))
        for _ in range(200):
            if servers and servers[0].started:
                break
            await asyncio.sleep(0.05)
        url = f"http://127.0.0.1:{port}/mcp"
        # The initialize handshake: each client gets its own transport session (Mcp-Session-Id), so its own run.
        async with Client(url, mode="legacy") as a, Client(url, mode="legacy") as b:
            assert not (await b.call_tool("volumes", {})).is_error            # B: a call recorded, its run open
            assert not (await a.call_tool("volumes", {})).is_error
            reply = await a.call_tool("tracetrim_done", {"answer": ANSWER})   # A: replied, its relearning held
            assert reply.content[0].text == auto.DONE_REPLY
        with Ticker() as ticker:
            servers[0].should_exit = True
            asyncio.get_running_loop().call_later(0.3, hold.set)
            await asyncio.wait_for(serving, 30)
        return ticker.longest
    longest = asyncio.run(go())
    assert seen["calls"] == 2 and seen["peak"] == 1 and not any(seen["main"])
    assert longest < 0.25
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["runs"] == 2 and "report_pending" not in state
    runs = [r["run"] for r in json.loads((tmp_path / "report.json").read_text())["savings"]["runs"]]
    assert runs == ["run-0001", "run-0002"]


def test_a_relearning_never_reads_under_the_lock_and_never_blocks_the_loop(tmp_path, monkeypatch):
    """REVISIONE-14b-bis: with the reads of a relearning slowed to 850 ms, a call from an isolated session that
    arrives meanwhile waits without blocking the event loop; no file is read while the worker holds the lock."""
    from tracetrim import definitions, evidence
    owned = []
    learners = []
    load, tools = evidence.load, definitions.load

    def slow(original):
        def read(*a, **kw):
            if threading.current_thread() is not threading.main_thread():
                owned.append(learners[0].lock._is_owned())
                time.sleep(0.85)
            return original(*a, **kw)
        return read
    monkeypatch.setattr(evidence, "load", slow(load))
    monkeypatch.setattr(definitions, "load", slow(tools))

    async def go():
        server, learner = proxy._server(options(tmp_path, shared=True, isolate_sessions=True, background_relearn=True))
        learners.append(learner)
        async with Client(server) as client:
            await run(client)
            with Ticker() as ticker:
                start = time.perf_counter()
                assert not (await client.call_tool("volumes", {})).is_error
                waited = time.perf_counter() - start
            await settled(learner)
        return ticker.longest, waited
    longest, waited = asyncio.run(go())
    assert waited >= 1.0                          # it waited for the relearning
    assert longest < 0.25                         # without blocking the loop
    assert owned and not any(owned)


def test_a_promotion_is_identical_in_both_modes_and_read_outside_the_lock(tmp_path, monkeypatch):
    """The decisions of a relearning (here a promotion on evidence) are added to the evidence as it is when they
    are written, read outside the lock; the files end the same as without the option."""
    from tracetrim import evidence
    owned, learners = [], []
    load = evidence.load

    def read(*a, **kw):
        if threading.current_thread() is not threading.main_thread():
            owned.append(learners[-1].lock._is_owned())
        return load(*a, **kw)
    monkeypatch.setattr(evidence, "load", read)
    results = {}
    for name, on in (("off", False), ("on", True)):
        deterministic(monkeypatch)

        async def go(work=tmp_path / name, on=on):
            server, learner = proxy._server(options(work, background_relearn=on, promote=True))
            learners.append(learner)
            async with Client(server) as client:
                for i in range(1, 7):
                    out = (await client.call_tool("volumes", {})).content[0].text
                    m = re.search(r"call_id=(c[0-9a-f]{8})", out)
                    if i >= 2 and m:
                        await client.call_tool("expand", {"call_id": m.group(1), "fields": ["items[].note_0"]})
                    await client.call_tool("tracetrim_done", {"answer": ANSWER})
                if on:
                    await learner.background.drain()
        asyncio.run(go())
        results[name] = snapshot(tmp_path / name)
    assert_identical(results)
    assert json.loads(results["on"]["evidence.json"])["promotions"]       # the decision path was taken
    assert owned and not any(owned)


# -- REVISIONE-14b-ter -------------------------------------------------------------------------------------------

def capture_uvicorn(monkeypatch):
    import uvicorn
    servers = []
    init = uvicorn.Server.__init__

    def capture(self, *a, **kw):
        init(self, *a, **kw)
        servers.append(self)
    monkeypatch.setattr(uvicorn.Server, "__init__", capture)
    return servers


def capture_learners(monkeypatch):
    learners = []
    build = proxy._server

    def capture(o):
        server, learner = build(o)
        learners.append(learner)
        return server, learner
    monkeypatch.setattr(proxy, "_server", capture)
    return learners


async def start_http(o, servers):
    port = free_port()
    task = asyncio.create_task(proxy.serve_http(o, host="127.0.0.1", port=port, token=None, allowed_hosts=[]))
    for _ in range(200):
        if servers and servers[-1].started:
            break
        await asyncio.sleep(0.05)
    return task, f"http://127.0.0.1:{port}/mcp"


async def stop_http(task, servers):
    servers[-1].should_exit = True
    await asyncio.wait_for(task, 30)


@pytest.mark.parametrize("isolated", [False, True])
def test_a_call_already_in_the_upstream_is_recorded_before_the_relearning_reads(tmp_path, monkeypatch, isolated):
    """REVISIONE-14b-ter: B's call reaches the upstream, then A closes. The relearning reads its inputs once B's
    result is recorded, so they count it; and B's call, received before the answer, follows no answer."""
    servers, learners = capture_uvicorn(monkeypatch), capture_learners(monkeypatch)
    gate = tmp_path / "gate"
    gate.mkdir()
    reads = []
    inputs = auto.Learner._inputs

    def counted(self):
        got = inputs(self)
        reads.append(dict(got["state"]["trimmed"]))
        return got
    monkeypatch.setattr(auto.Learner, "_inputs", counted)

    async def go():
        # The upstream with its gate (an `env` of the servers file reaches the upstream process).
        o = proxy.Options(servers={"upstream": {"command": sys.executable, "args": [str(ROOT / "tests" / "fixtures" /
                                                                                     "meta_server.py")],
                                                "env": {"META_GATE": str(gate)}}},
                          auto_dir=str(tmp_path / "work"), quiet=True, min_runs=2, background_relearn=True,
                          isolate_sessions=isolated)
        task, url = await start_http(o, servers)
        learner = learners[-1]
        async with Client(url, mode="legacy") as a, Client(url, mode="legacy") as b:
            for _ in range(2):                                        # two runs: the policy applies
                await run(a)
                await settled(learner)
            assert "call_id=" in (await a.call_tool("volumes", {})).content[0].text
            followed = learner.state["answered_runs_followed_by_calls"]
            held = asyncio.create_task(b.call_tool("volumes", {"gate": "b"}))
            for _ in range(1000):
                if (gate / "b.arrived").exists():
                    break
                await asyncio.sleep(0.01)
            assert (gate / "b.arrived").exists()
            reply = await a.call_tool("tracetrim_done", {"answer": ANSWER})      # A closes, B in the upstream
            assert reply.content[0].text == auto.DONE_REPLY
            await asyncio.sleep(0.3)
            assert len(reads) == 2                                    # the third relearning waits for B
            (gate / "b.go").touch()
            assert "call_id=" in (await held).content[0].text        # the policy in place when it was sent
            await settled(learner)
            assert reads[-1] == {"volumes": 2}                        # B's filtered result is in the inputs
            assert learner.state["answered_runs_followed_by_calls"] == followed
        await stop_http(task, servers)
    asyncio.run(go())


@pytest.mark.parametrize("on", [False, True])
def test_with_two_clients_a_call_during_a_relearning(tmp_path, monkeypatch, on):
    """The order with several clients, as documented (BackgroundLearning): with the option, B's call that arrives
    while A's relearning runs waits for it and gets the new policy; without it, B is served at once with the
    policy in place (REVISIONE-14b-ter: 96 tokens against 75)."""
    servers, learners = capture_uvicorn(monkeypatch), capture_learners(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    relearn = auto.Learner.relearn

    def held(self):
        if self.state["runs"] == 2:                   # the relearning that makes the policy apply
            entered.set()
            release.wait(5)
        return relearn(self)
    monkeypatch.setattr(auto.Learner, "relearn", held)

    async def go():
        o = options(tmp_path / "work", isolate_sessions=True, background_relearn=on)
        task, url = await start_http(o, servers)
        learner = learners[-1]
        async with Client(url, mode="legacy") as a, Client(url, mode="legacy") as b:
            await run(a)
            if on:
                await settled(learner)
            assert not (await a.call_tool("volumes", {})).is_error
            closing = asyncio.create_task(a.call_tool("tracetrim_done", {"answer": ANSWER}))
            assert await asyncio.to_thread(entered.wait, 5)
            call = asyncio.create_task(b.call_tool("volumes", {}))
            await asyncio.sleep(0.4)
            assert call.done() is (not on)            # without the option, served during the relearning
            release.set()
            out = (await call).content[0].text
            await closing
            assert ("call_id=" in out) is on          # with it, after the relearning, with the new policy
            if on:
                await settled(learner)
        await stop_http(task, servers)
    asyncio.run(go())


class Stop(BaseException):
    """A process stopped at a precise write."""


def test_a_trace_ahead_of_the_state_is_taken_in_once(tmp_path, monkeypatch):
    """REVISIONE-14b-ter: a closing written file after file (1.3, or `durable_closes=False`) stops after the trace
    and the savings, before the state. The next start takes the run into the state, on disk, and relearns; the
    one after has nothing to do."""
    from tracetrim import store
    from tracetrim.contract import ToolCall, ToolResult
    from tracetrim.engine import Engine
    learner = auto.Learner(tmp_path, 2, durable_closes=False)
    engine = Engine(learner)
    for value in (1, 3):
        engine.observe(ToolCall("t", {}), ToolResult(json.dumps({"a": value, "b": value + 1})))
        if value == 1:
            engine.close("1")
    write_json = store.write_json

    def stop_at_state(path, data):
        if Path(path).name == "state.json":
            raise Stop
        return write_json(path, data)
    monkeypatch.setattr(store, "write_json", stop_at_state)
    with pytest.raises(Stop):
        learner.done("3")
    monkeypatch.setattr(store, "write_json", write_json)
    assert json.loads((tmp_path / "state.json").read_text())["runs"] == 1          # behind its traces
    relearnings = []
    relearn = auto.Learner.relearn
    monkeypatch.setattr(auto.Learner, "relearn", lambda self: relearnings.append(1) or relearn(self))
    auto.Learner(tmp_path, 2)
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["runs"] == 2 and state["complete_runs"] == 2 and "report_pending" not in state
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["auto"]["complete_runs"] == 2
    assert [r["run"] for r in report["savings"]["runs"]] == ["run-0001", "run-0002"]
    assert relearnings == [1]
    auto.Learner(tmp_path, 2)                                                      # the next start: nothing to do
    assert relearnings == [1]


def test_a_closing_stopped_in_its_journal_is_published_whole_once(tmp_path, monkeypatch):
    """Closings go through a journal in every mode (1.4): stopped after the journal and the trace, before the state,
    the next start publishes the whole closing (trace, savings, evidence, state) and the one after does nothing."""
    from tracetrim import store
    from tracetrim.contract import ToolCall, ToolResult
    from tracetrim.engine import Engine
    learner = auto.Learner(tmp_path, 2)
    engine = Engine(learner)
    engine.observe(ToolCall("t", {}), ToolResult('{"a": 1, "b": 2}'))
    engine.close("1")
    engine.observe(ToolCall("t", {}), ToolResult('{"a": 3, "b": 4}'))
    replace = store.os.replace

    def stop_at_state(src, dst):
        if Path(dst).name == "state.json":
            raise Stop
        return replace(src, dst)
    monkeypatch.setattr(store.os, "replace", stop_at_state)
    with pytest.raises(Stop):
        learner.done("3")
    monkeypatch.setattr(store.os, "replace", replace)
    assert (tmp_path / store.JOURNAL).exists() and (tmp_path / "traces" / "run-0002.jsonl").exists()
    relearnings = []
    relearn = auto.Learner.relearn
    monkeypatch.setattr(auto.Learner, "relearn", lambda self: relearnings.append(1) or relearn(self))
    auto.Learner(tmp_path, 2)
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["runs"] == state["complete_runs"] == 2 and "report_pending" not in state
    assert "run-0002" in json.loads((tmp_path / "savings.json").read_text())["runs"]
    assert not (tmp_path / store.JOURNAL).exists() and relearnings == [1]
    auto.Learner(tmp_path, 2)
    assert relearnings == [1]


@pytest.mark.parametrize("on", [False, True])
def test_a_copy_taken_in_the_middle_of_an_ordinary_closing_restarts_to_the_same_files(tmp_path, monkeypatch, on):
    """REVISIONE-14b-ter (0 copies out of 8): with sessions that are not isolated, a copy of the work directory taken
    right after the trace or the savings of a closing, before its state, restarts to the files of the closing let
    finish, byte for byte."""
    import shutil
    from tracetrim import store
    deterministic(monkeypatch)
    work = tmp_path / "work"
    copies = {}
    write_atomic = store.write_atomic

    def copying(path, text):
        write_atomic(path, text)
        name = Path(path).name
        if store._writes.get() is None and name in ("run-0002.jsonl", "savings.json") and name not in copies \
                and (work / "traces" / "run-0002.jsonl").exists():
            copies[name] = tmp_path / f"copy-{name}"
            shutil.copytree(work, copies[name])
    monkeypatch.setattr(store, "write_atomic", copying)

    async def go():
        server, learner = proxy._server(options(work, background_relearn=on))
        async with Client(server) as client:
            await run(client)
            if on:
                await settled(learner)
            await run(client)
            if on:
                await learner.background.drain()
    asyncio.run(go())
    monkeypatch.setattr(store, "write_atomic", write_atomic)
    assert sorted(copies) == ["run-0002.jsonl", "savings.json"]
    finished = snapshot(work)
    for name, copy in copies.items():
        assert json.loads((copy / "state.json").read_text())["runs"] == 1        # the state was not written yet
        proxy._server(options(copy))                                             # the next start
        assert snapshot(copy) == finished, name


def test_a_revert_from_another_process_is_kept(tmp_path, monkeypatch):
    """REVISIONE-14b-ter: `tracetrim promotions --revert`, run in another process while the relearning that adds a
    new promotion has read the evidence, is not written over; and with the directory in use the command does not
    relearn it from there."""
    import subprocess
    from tracetrim import evidence
    paused, release = threading.Event(), threading.Event()
    load, commit = evidence.load, auto.Learner._commit
    local = threading.local()

    def committing(self, runs_at_start, decided):
        local.on = runs_at_start == 6                 # the relearning that promotes note_1
        try:
            return commit(self, runs_at_start, decided)
        finally:
            local.on = False

    def read(*a, **kw):
        data = load(*a, **kw)
        if getattr(local, "on", False) and not paused.is_set():
            paused.set()                              # the commit has read the evidence: hold it here
            release.wait(30)
        return data
    monkeypatch.setattr(auto.Learner, "_commit", committing)
    monkeypatch.setattr(evidence, "load", read)
    work = tmp_path / "work"

    async def go():
        server, learner = proxy._server(options(work, background_relearn=True, promote=True))
        async with Client(server) as client:
            for i in range(1, 7):                     # filtered from run 3: note_0 asked in 3 and 4, note_1 in 5 and 6
                out = (await client.call_tool("volumes", {})).content[0].text
                m = re.search(r"call_id=(c[0-9a-f]{8})", out)
                field = "items[].note_0" if i in (3, 4) else "items[].note_1"
                if m:
                    await client.call_tool("expand", {"call_id": m.group(1), "fields": [field]})
                await client.call_tool("tracetrim_done", {"answer": ANSWER})
                if i < 6:
                    await settled(learner)
                if i == 4:
                    assert [p["field"] for p in json.loads((work / "evidence.json").read_text())["promotions"]] \
                        == ["items[].note_0"]
            # Run 6's relearning promotes note_1 and is held after reading the evidence.
            assert await asyncio.to_thread(paused.wait, 10)
            cli = await asyncio.to_thread(subprocess.run, [
                sys.executable, "-m", "tracetrim.cli", "promotions", str(work), "--revert", "volumes",
                "items[].note_0"], capture_output=True, text=True, env={**__import__("os").environ,
                                                                       "PYTHONPATH": str(ROOT / "src")})
            assert cli.returncode == 0, cli.stderr
            assert "in use" in cli.stdout
            release.set()
            await learner.background.drain()
    asyncio.run(go())
    status = {p["field"]: p["status"] for p in json.loads((work / "evidence.json").read_text())["promotions"]}
    assert status == {"items[].note_0": "reverted", "items[].note_1": "active"}


def test_writes_during_every_read_never_wait_for_the_relearning(tmp_path, monkeypatch):
    """REVISIONE-14b-ter (854 ms at the fourth attempt): another client's write during each read of a relearning
    makes it read again, never under the lock, and the write never waits for it."""
    from tracetrim import evidence
    learner = auto.Learner(tmp_path, 2)
    learner.events.append({"kind": "call", "tool": "t", "arguments": {}, "output": '{"a": 1, "b": 2}'})
    learner.done("1")
    reading = threading.Event()
    load = evidence.load
    owned = []

    def slow(*a, **kw):
        if threading.current_thread() is not threading.main_thread():
            owned.append(learner.lock._is_owned())
            reading.set()
            time.sleep(0.3)
        return load(*a, **kw)
    monkeypatch.setattr(evidence, "load", slow)
    worker = threading.Thread(target=learner.relearn)
    worker.start()
    waits = []
    for _ in range(5):                                   # a write in each of five reads
        assert reading.wait(5)
        reading.clear()
        start = time.perf_counter()
        learner.refused()
        waits.append(time.perf_counter() - start)
    worker.join(20)
    assert not worker.is_alive() and len(owned) >= 6 and not any(owned)
    assert max(waits) < 0.1
    assert json.loads((tmp_path / "report.json").read_text())["auto"]["refused_closings"] == 5


def test_the_recovery_at_start_runs_in_a_thread_with_the_option(tmp_path, monkeypatch):
    """REVISIONE-14b-ter (491 ms on the loop): with the option, the constructor leaves a run pending on disk to the
    coordinator, which relearns it in a thread; the first request waits for it."""
    learner = auto.Learner(tmp_path, 2)
    learner.events.append({"kind": "call", "tool": "volumes", "arguments": {}, "output": '{"a": 1, "b": 2}'})
    learner.done(ANSWER)                                 # recorded, not relearned: pending on disk
    assert json.loads((tmp_path / "state.json").read_text())["report_pending"] is True
    seen = tracking(monkeypatch)

    async def go():
        start = time.perf_counter()
        server, learner = proxy._server(options(tmp_path, background_relearn=True))
        built = time.perf_counter() - start
        with Ticker() as ticker:
            async with Client(server) as client:
                start = time.perf_counter()
                assert not (await client.call_tool("volumes", {})).is_error
                waited = time.perf_counter() - start
        return built, waited, ticker.longest
    built, waited, longest = asyncio.run(go())
    assert built < 0.3 and waited >= 0.3 and longest < 0.25
    assert seen["calls"] == 1 and not any(seen["main"])
    assert "report_pending" not in json.loads((tmp_path / "state.json").read_text())
