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

    def committing(self, inputs, decided):
        local.on = inputs["state"]["runs"] == 6       # the relearning that promotes note_1
        try:
            return commit(self, inputs, decided)
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


# -- REVISIONE-14b-quater ----------------------------------------------------------------------------------------

def promote_sequence(work, on, promote_runs=4, expand_runs=(3, 4, 5, 6)):
    """Six runs; note_0 asked back in `expand_runs`: with promote_runs=4 the promotion comes at run 6, when the tool
    has 4 filtered results, all expanded, in 4 runs (the switch-off rule would apply without the promotion)."""
    async def go():
        server, learner = proxy._server(options(work, background_relearn=on, promote=True, promote_runs=promote_runs))
        async with Client(server) as client:
            for i in range(1, 7):
                out = (await client.call_tool("volumes", {})).content[0].text
                m = re.search(r"call_id=(c[0-9a-f]{8})", out)
                if m and i in expand_runs:
                    await client.call_tool("expand", {"call_id": m.group(1), "fields": ["items[].note_0"]})
                await client.call_tool("tracetrim_done", {"answer": ANSWER})
                if on:
                    await settled(learner)
            if on:
                await learner.background.drain()
    asyncio.run(go())


@pytest.mark.parametrize("on", [False, True])
def test_a_stop_while_a_promotion_is_written_restarts_to_the_same_files(tmp_path, monkeypatch, on):
    """REVISIONE-14b-quater: stopped after the promotion reached evidence.json and before the state (with the
    restart of the tool's counts), the next start switched the tool off. The two go through one journal."""
    import shutil
    from tracetrim import store
    deterministic(monkeypatch)
    work, copy = tmp_path / "work", tmp_path / "copy"
    write_atomic = store.write_atomic

    def copying(path, text):
        write_atomic(path, text)
        if (store._writes.get() is None and Path(path).name == "evidence.json" and not copy.exists()
                and (work / store.JOURNAL).exists() and '"promotions": [\n    {' in text):
            shutil.copytree(work, copy)                 # the commit's evidence is published, its state not yet
    monkeypatch.setattr(store, "write_atomic", copying)
    promote_sequence(work, on)
    monkeypatch.setattr(store, "write_atomic", write_atomic)
    assert copy.exists()
    finished = snapshot(work)
    assert json.loads(finished["state.json"])["off"] == []
    proxy._server(options(copy, promote=True, promote_runs=4))       # the next start
    assert snapshot(copy) == finished


@pytest.mark.parametrize("on", [False, True])
def test_a_write_after_a_failed_publication_finishes_it_first(tmp_path, monkeypatch, on):
    """REVISIONE-14b-quater: the publication of run 3 fails at the state; a late expand and a refused closing that
    follow are written, and the replay of that publication, at the next closing, no longer undoes them."""
    from tracetrim import store
    replace = store.os.replace
    fail = {"on": False}

    def failing(src, dst):
        if fail["on"] and Path(dst).name == "state.json":
            fail["on"] = False
            raise OSError("simulated")
        return replace(src, dst)
    monkeypatch.setattr(store.os, "replace", failing)

    async def go():
        server, learner = proxy._server(options(tmp_path, background_relearn=on))
        async with Client(server) as client:
            for _ in range(2):
                await run(client)
                if on:
                    await settled(learner)
            out = (await client.call_tool("volumes", {})).content[0].text
            cid = re.search(r"call_id=(c[0-9a-f]{8})", out).group(1)
            await client.call_tool("expand", {"call_id": cid, "fields": ["items[].note_0"]})
            fail["on"] = True
            from mcp.shared.exceptions import MCPError
            with pytest.raises(MCPError):                                      # published in part: the journal stays
                await client.call_tool("tracetrim_done", {"answer": ANSWER})
            assert (tmp_path / store.JOURNAL).exists()
            await client.call_tool("expand", {"call_id": cid, "fields": ["items[].note_1"]})   # late
            assert (await client.call_tool("tracetrim_done", {"answer": " "})).is_error        # refused
            await run(client)                                                  # the next closing
            if on:
                await learner.background.drain()
    asyncio.run(go())
    fields = json.loads((tmp_path / "evidence.json").read_text())["tools"]["volumes"]["fields"]
    assert "run-0003" in fields["items[].note_1"]["requested"]
    assert json.loads((tmp_path / "savings.json").read_text())["runs"]["run-0003"]["expand"]["calls"] == 2
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["refused_closings"] == 1 and state["runs"] == 4 and "report_pending" not in state


def test_two_promotions_commands_without_a_proxy_leave_the_last_choice(tmp_path, monkeypatch):
    """REVISIONE-14b-quater: with no proxy, a revert that is relearning and a restore in another process. The second
    waits for the first, then applies its own change: evidence and config agree on the last choice."""
    import gc
    import subprocess
    from tracetrim.cli import main
    work = tmp_path / "work"
    promote_sequence(work, False, promote_runs=2, expand_runs=(3, 4))
    gc.collect()                                          # no Learner of this process holds the directory
    env = {**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")}
    probe = subprocess.run([sys.executable, "-c", "import sys\nfrom tracetrim import store\n"
                            "with store.alone(sys.argv[1]) as a: print(a)", str(work)],
                           capture_output=True, text=True, env=env)
    assert probe.stdout.strip() == "True", probe.stderr
    inside = threading.Event()
    relearn = auto.Learner.relearn

    def slow(self):
        inside.set()
        time.sleep(1.5)
        return relearn(self)
    monkeypatch.setattr(auto.Learner, "relearn", slow)
    first = threading.Thread(target=main, args=(["promotions", str(work), "--revert", "volumes", "items[].note_0"],))
    first.start()
    assert inside.wait(10)
    second = subprocess.run([sys.executable, "-m", "tracetrim.cli", "promotions", str(work), "--restore", "volumes",
                             "items[].note_0"], capture_output=True, text=True, env=env)
    first.join(20)
    assert second.returncode == 0 and "in use" not in second.stdout, second.stdout + second.stderr
    status = [p["status"] for p in json.loads((work / "evidence.json").read_text())["promotions"]]
    promoted = json.loads((work / "report.config.json").read_text())["auto"]["promoted"]
    assert status == ["active"] and promoted == {"volumes": ["items[].note_0"]}


def test_counts_a_failed_savings_write_could_not_save_stay_with_their_run(tmp_path, monkeypatch):
    """REVISIONE-14b-quater: the savings of run 2 fail to be prepared; they are saved with the next closing, under
    run 2, not added to run 3."""
    from tracetrim.contract import ToolCall, ToolResult
    from tracetrim.engine import Engine
    learner = auto.Learner(tmp_path, 2)
    engine = Engine(learner)
    add = auto.Learner._add_sizes
    fail = {"on": False}

    def failing(self, *a, **kw):
        if fail["on"]:
            fail["on"] = False
            raise RuntimeError("simulated")
        return add(self, *a, **kw)
    monkeypatch.setattr(auto.Learner, "_add_sizes", failing)
    for i in range(3):
        engine.observe(ToolCall("t", {}), ToolResult(json.dumps({"a": i, "b": i + 1})))
        fail["on"] = i == 1
        if i == 1:
            with pytest.raises(RuntimeError):
                learner.done(str(i))
        else:
            learner.done(str(i))
    runs = json.loads((tmp_path / "savings.json").read_text())["runs"]
    assert {r: v["tools"]["t"]["calls"] for r, v in runs.items()} == {"run-0001": 1, "run-0002": 1, "run-0003": 1}


def alone_from_another_process(work):
    import subprocess
    env = {**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")}
    out = subprocess.run([sys.executable, "-c", "import sys\nfrom tracetrim import store\n"
                          "with store.alone(sys.argv[1]) as a: print(a)", str(work)],
                         capture_output=True, text=True, env=env)
    return out.stdout.strip() == "True"


def test_the_directory_stays_in_use_while_any_learner_of_the_process_lives(tmp_path):
    """REVISIONE-14b-quater: two Learners of one process; the presence went with the first one given back."""
    import gc
    one, two = auto.Learner(tmp_path, 2), auto.Learner(tmp_path, 2)
    assert not alone_from_another_process(tmp_path)
    del one
    gc.collect()
    assert not alone_from_another_process(tmp_path)
    del two
    gc.collect()
    assert alone_from_another_process(tmp_path)


def test_the_cleaning_at_start_never_removes_a_file_being_written_under_the_lock(tmp_path, monkeypatch):
    """REVISIONE-14b-quater: `tracetrim promotions` took the write lock between the replay of the journal and the
    cleaning of a Learner that was starting; the cleaning removed its temporary file, and the command failed."""
    import os
    import tempfile
    from tracetrim import store
    (tmp_path / "traces").mkdir()
    replayed, created, done = threading.Event(), threading.Event(), {}
    clean = store.clean

    def cleaning(directory):
        if not replayed.is_set():
            replayed.set()
            created.wait(0.5)                             # the writer's chance to come in before the cleaning
        return clean(directory)
    monkeypatch.setattr(store, "clean", cleaning)

    def writer():
        try:
            assert replayed.wait(5)
            with store.write_lock(tmp_path):
                fd, name = tempfile.mkstemp(dir=tmp_path, prefix=".evidence.json.", suffix=".tmp")
                os.write(fd, b"{}")
                os.close(fd)
                created.set()
                time.sleep(0.3)
                os.replace(name, tmp_path / "evidence.json")
            done["ok"] = True
        except Exception as e:                            # noqa: BLE001
            done["error"] = e
    t = threading.Thread(target=writer)
    t.start()
    auto.Learner(tmp_path, 2)
    t.join(5)
    assert done == {"ok": True}


def test_a_trace_cut_short_ahead_of_the_state_is_not_counted_complete(tmp_path):
    """REVISIONE-14b-quater: a 1.3 trace cut short, numbered after the state: its number is taken, it is not
    counted as a complete run."""
    learner = auto.Learner(tmp_path, 2, durable_closes=False)
    learner.events.append({"kind": "call", "tool": "t", "arguments": {}, "output": '{"a": 1, "b": 2}'})
    learner.done("1")
    learner.relearn()
    (tmp_path / "traces" / "run-0002.jsonl").write_text('{"kind": "call", "tool": "t", "outp')
    del learner
    auto.Learner(tmp_path, 2)
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["runs"] == 2 and state["complete_runs"] == 1
    assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == 1


def test_a_closing_that_finishes_a_publication_never_deadlocks_with_a_relearning(tmp_path, monkeypatch):
    """REVISIONE-14b-quater: `done` relearned after finishing an interrupted publication while holding the
    Learner's lock; a relearning in another thread held its own lock and waited for the Learner's."""
    from tracetrim import store
    learner = auto.Learner(tmp_path, 2)
    learner.events.append({"kind": "call", "tool": "t", "arguments": {}, "output": '{"a": 1, "b": 2}'})
    learner.done("1")
    paused, go_on = threading.Event(), threading.Event()
    inputs = auto.Learner._inputs

    def held(self):
        paused.set()
        go_on.wait(10)
        return inputs(self)
    monkeypatch.setattr(auto.Learner, "_inputs", held)
    worker = threading.Thread(target=learner.relearn)    # holds the relearning lock, paused before its inputs
    worker.start()
    assert paused.wait(5)
    replace = store.os.replace

    def failing(src, dst):
        if Path(dst).name == "state.json":
            raise OSError("simulated")
        return replace(src, dst)
    monkeypatch.setattr(store.os, "replace", failing)
    learner.events.append({"kind": "call", "tool": "t", "arguments": {}, "output": '{"a": 3, "b": 4}'})
    with pytest.raises(OSError):
        learner.done("3")                                 # published in part: pending
    monkeypatch.setattr(store.os, "replace", replace)
    learner.events.append({"kind": "call", "tool": "t", "arguments": {}, "output": '{"a": 5, "b": 6}'})
    closer = threading.Thread(target=learner.done, args=("5",))    # finishes it, then relearns
    closer.start()
    time.sleep(0.3)
    go_on.set()
    worker.join(10)
    closer.join(10)
    assert not worker.is_alive() and not closer.is_alive()
    assert json.loads((tmp_path / "state.json").read_text())["runs"] == 3


def test_with_two_clients_a_call_during_the_closing_follows_no_answer(tmp_path, monkeypatch):
    """REVISIONE-14b-quater: without the option, B's call while A's closing relearns came before A's reply: it does
    not count as a call after the answer, and A's next call still does."""
    servers, learners = capture_uvicorn(monkeypatch), capture_learners(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    relearn = auto.Learner.relearn

    def held(self):
        if self.state["runs"] == 1:
            entered.set()
            release.wait(5)
        return relearn(self)
    monkeypatch.setattr(auto.Learner, "relearn", held)

    async def go():
        o = options(tmp_path / "work")
        task, url = await start_http(o, servers)
        learner = learners[-1]
        async with Client(url, mode="legacy") as a, Client(url, mode="legacy") as b:
            assert not (await a.call_tool("volumes", {})).is_error
            closing = asyncio.create_task(a.call_tool("tracetrim_done", {"answer": ANSWER}))
            assert await asyncio.to_thread(entered.wait, 5)
            assert not (await b.call_tool("volumes", {})).is_error            # before A's reply
            assert learner.state["answered_runs_followed_by_calls"] == 0
            release.set()
            await closing
            assert not (await a.call_tool("volumes", {})).is_error            # after it
            assert learner.state["answered_runs_followed_by_calls"] == 1
        await stop_http(task, servers)
    asyncio.run(go())


# -- REVISIONE-14b-quinquies -------------------------------------------------------------------------------------

PAYLOAD = {"items": [{"keyword": "spa weekend", "search_volume": 1600,
                      **{f"note_{i}": f"internal remark number {i}" for i in range(8)}}]}


def promoting(work, promote_runs=2):
    """An engine in automatic mode with promotion on (AutoTrim's, without its adapter)."""
    from tracetrim.engine import Engine
    from tracetrim.evidence import Adaptive
    return Engine(auto.Learner(work, 2, adaptive=Adaptive(promote=True, promote_requested_runs=promote_runs)))


def observe(engine, payload=None, expand=None):
    """One call of `volumes`; with `expand`, the field asked back. True when the result was filtered."""
    from tracetrim.contract import ToolCall, ToolResult
    out = engine.observe(ToolCall("volumes", {}), ToolResult(json.dumps(payload or PAYLOAD))).text
    m = re.search(r"call_id=(c[0-9a-f]{8})", out)
    if m and expand:
        engine.expand(m.group(1), [expand])
    return bool(m)


def up_to_the_promotion(engine):
    """Runs 1 to 4: filtered from run 3, note_0 asked back in runs 3 and 4. Run 4 is closed, not relearned: its
    relearning promotes note_0 (two runs)."""
    for i in range(1, 5):
        assert observe(engine, expand="items[].note_0" if i >= 3 else None) is (i >= 3)
        if i < 4:
            engine.close(ANSWER)
        else:
            engine.learner.done(ANSWER)


def fail_once(monkeypatch, name):
    """The next replacement of the file `name` fails (store.write_atomic's os.replace), once, when armed."""
    from tracetrim import store
    replace = store.os.replace
    armed = {"on": False}

    def failing(src, dst):
        if armed["on"] and Path(dst).name == name:
            armed["on"] = False
            raise OSError("simulated")
        return replace(src, dst)
    monkeypatch.setattr(store.os, "replace", failing)
    return armed


def test_a_commit_never_writes_over_a_request_its_replay_brings_back(tmp_path, monkeypatch):
    """REVISIONE-14b-quinquies: the relearning that promotes note_0 is about to commit; run 5, closed meanwhile with
    a request for note_1, stops with its evidence still in the journal. The commit finished that publication after
    reading the evidence, and wrote its copy over it: the request of run 5 was lost from evidence.json."""
    engine = promoting(tmp_path)
    up_to_the_promotion(engine)
    learner = engine.learner
    paused, go_on = threading.Event(), threading.Event()
    commit = auto.Learner._commit

    def committing(self, *args):
        if args[1] and args[1]["new"]:
            paused.set()
            go_on.wait(10)
        return commit(self, *args)
    monkeypatch.setattr(auto.Learner, "_commit", committing)
    worker = threading.Thread(target=engine.learn_policy)
    worker.start()
    assert paused.wait(10)
    armed = fail_once(monkeypatch, "evidence.json")
    assert observe(engine, expand="items[].note_1")
    armed["on"] = True
    with pytest.raises(OSError):
        learner.done(ANSWER)                              # run 5: published in part, its evidence in the journal
    go_on.set()
    worker.join(10)
    assert not worker.is_alive()
    data = json.loads((tmp_path / "evidence.json").read_text())
    assert "run-0005" in data["tools"]["volumes"]["fields"]["items[].note_1"]["requested"]
    assert [(p["field"], p["status"]) for p in data["promotions"]] == [("items[].note_0", "active")]


def test_a_cancelled_closing_holds_the_next_writer_until_its_relearning_ends(tmp_path, monkeypatch):
    """REVISIONE-14b-quinquies: without the option, the request of a closing is cancelled while its relearning runs
    in a thread. The closing lock was given back at once, and the next closing wrote while the worker still did."""
    order, entered, release = [], threading.Event(), threading.Event()
    relearn, done = auto.Learner.relearn, auto.Learner.done

    def held(self):
        if self.state["runs"] == 2 and not entered.is_set():
            entered.set()
            release.wait(10)
        try:
            return relearn(self)
        finally:
            order.append(f"relearned {self.state['runs']}")

    def closing(self, answer):
        if self.events:
            order.append(f"closing {self.state['runs'] + 1}")
        return done(self, answer)
    monkeypatch.setattr(auto.Learner, "relearn", held)
    monkeypatch.setattr(auto.Learner, "done", closing)

    async def go():
        server, learner = proxy._server(options(tmp_path))
        async with Client(server) as client:
            await run(client)
            assert not (await client.call_tool("volumes", {})).is_error
            first = asyncio.create_task(client.call_tool("tracetrim_done", {"answer": ANSWER}))
            assert await asyncio.to_thread(entered.wait, 10)
            first.cancel()                                    # the client sends the cancellation to the proxy
            await asyncio.sleep(0.2)
            assert not (await client.call_tool("volumes", {})).is_error
            second = asyncio.create_task(client.call_tool("tracetrim_done", {"answer": ANSWER}))
            await asyncio.sleep(0.4)
            assert "closing 3" not in order                   # waits for the worker of the cancelled closing
            release.set()
            assert (await second).content[0].text == auto.DONE_REPLY
            with pytest.raises(asyncio.CancelledError):
                await first
    asyncio.run(go())
    assert order.index("relearned 2") < order.index("closing 3")
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["runs"] == 3 and "report_pending" not in state


@pytest.mark.parametrize("between", ["tool listing", "refused closing", "expand with an unknown id"])
@pytest.mark.parametrize("on", [False, True])
def test_a_retry_is_confirmed_with_the_policy_of_its_run(tmp_path, monkeypatch, on, between):
    """REVISIONE-14b-quinquies: the publication of run 2 fails at the state; another request finishes it; the retry
    of the closing replied `Recorded.` with the report of one run and the policy of before (the next call whole)."""
    from mcp.shared.exceptions import MCPError
    armed = fail_once(monkeypatch, "state.json")

    async def go():
        server, learner = proxy._server(options(tmp_path, background_relearn=on))
        async with Client(server) as client:
            await client.list_tools()
            await run(client)
            if on:
                await settled(learner)
            assert "call_id=" not in (await client.call_tool("volumes", {})).content[0].text
            armed["on"] = True
            with pytest.raises(MCPError):
                await client.call_tool("tracetrim_done", {"answer": ANSWER})
            if between == "tool listing":
                await client.list_tools()
            elif between == "refused closing":
                assert (await client.call_tool("tracetrim_done", {"answer": " "})).is_error
            else:
                await client.call_tool("expand", {"call_id": "c00000000", "fields": ["items[].note_0"]})
            reply = await client.call_tool("tracetrim_done", {"answer": ANSWER})     # the retry
            assert reply.content[0].text == auto.DONE_REPLY
            assert "call_id=" in (await client.call_tool("volumes", {})).content[0].text   # filtered
            if on:
                await learner.background.drain()
    asyncio.run(go())
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["complete_runs"] == 2 and "report_pending" not in state
    assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == 2


def holder(work):
    """Another process that owns the work directory, as a running proxy does (the presence lock)."""
    import subprocess
    env = {**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.Popen([sys.executable, "-c", "import sys\nfrom tracetrim import store\n"
                             "store.hold_open(sys.argv[1])\nprint('ready', flush=True)\nsys.stdin.read()", str(work)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
    assert proc.stdout.readline().strip() == "ready"
    return proc


@pytest.mark.parametrize("proxy_running", [True, False])
def test_an_interrupted_revert_is_completed_by_the_retry(tmp_path, monkeypatch, capsys, proxy_running):
    """REVISIONE-14b-quinquies: the revert is saved in the evidence, then the command stops (writing the mark, with a
    proxy on the directory; relearning, with none). Repeated, it found nothing to change and left note_0 promoted in
    the config, even after a restart."""
    import gc
    from tracetrim import store
    from tracetrim.cli import main
    work = tmp_path / "work"
    promote_sequence(work, False, promote_runs=2, expand_runs=(3, 4))
    gc.collect()
    assert json.loads((work / "report.config.json").read_text())["auto"]["promoted"] == {"volumes": ["items[].note_0"]}
    proc = holder(work) if proxy_running else None
    if proxy_running:
        write_atomic, seen = store.write_atomic, {"evidence": False, "failed": False}

        def failing(path, text):
            if Path(path).name == "evidence.json":
                seen["evidence"] = True
            elif Path(path).name == store.CHANGED and seen["evidence"] and not seen["failed"]:
                seen["failed"] = True
                raise OSError("simulated")
            return write_atomic(path, text)
        monkeypatch.setattr(store, "write_atomic", failing)
    else:
        fail_once(monkeypatch, "report.projection.json")["on"] = True
    command = ["promotions", str(work), "--revert", "volumes", "items[].note_0"]
    with pytest.raises(OSError):
        main(command)                                     # stopped after the evidence
    monkeypatch.undo()
    capsys.readouterr()
    main(command)                                         # repeated
    out = capsys.readouterr().out
    assert ("in use" in out) is proxy_running
    if proc is not None:
        proc.stdin.close()
        proc.wait(10)
    proxy._server(options(work, promote=True, promote_runs=2))          # the next start
    data = json.loads((work / "evidence.json").read_text())
    assert [p["status"] for p in data["promotions"]] == ["reverted"]
    assert json.loads((work / "report.config.json").read_text())["auto"]["promoted"] == {}
    assert not (work / store.CHANGED).exists()


@pytest.mark.parametrize("case", ["backlog lost", "counted twice"])
def test_savings_follow_the_journal_of_their_closing(tmp_path, monkeypatch, case):
    """REVISIONE-14b-quinquies: the savings that a closing prepared were taken out of the queue even when its journal
    was not written. "backlog lost": the savings of run 2, left over, were prepared by closing 3, whose journal
    failed. "counted twice": the savings of run 2 failed to be prepared, and its journal too."""
    from tracetrim import store
    from tracetrim.contract import ToolCall, ToolResult
    from tracetrim.engine import Engine
    learner = auto.Learner(tmp_path, 2)
    engine = Engine(learner)
    add, write_json = auto.Learner._add_sizes, store.write_json
    fail = {"sizes": False, "journal": False}

    def adding(self, *a, **kw):
        if fail["sizes"]:
            fail["sizes"] = False
            raise RuntimeError("simulated")
        return add(self, *a, **kw)

    def writing(path, data):
        if fail["journal"] and Path(path).name == store.JOURNAL:
            fail["journal"] = False
            raise OSError("simulated")
        return write_json(path, data)
    monkeypatch.setattr(auto.Learner, "_add_sizes", adding)
    monkeypatch.setattr(store, "write_json", writing)
    for i in range(1, 4):
        engine.observe(ToolCall("t", {}), ToolResult(json.dumps({"a": i, "b": i + 1})))
        if case == "backlog lost":
            fail["sizes"], fail["journal"] = i == 2, i == 3
        else:
            fail["sizes"] = fail["journal"] = i == 2
        if fail["sizes"] or fail["journal"]:
            with pytest.raises((RuntimeError, OSError)):
                learner.done(str(i))
            if learner.events:
                learner.done(str(i))                      # the retry of a closing that did not reach its journal
        else:
            learner.done(str(i))
    runs = json.loads((tmp_path / "savings.json").read_text())["runs"]
    assert {r: v["tools"]["t"]["calls"] for r, v in runs.items()} == {"run-0001": 1, "run-0002": 1, "run-0003": 1}


@pytest.mark.skipif(not hasattr(__import__("os"), "fork"), reason="fork() only")
def test_a_child_made_with_fork_holds_the_directory_on_its_own(tmp_path):
    """REVISIONE-14b-quinquies: a child made with fork() inherited the table of the directories held, took no lock of
    its own, and with the parent's Learner given back the directory looked free while the child's Learner lived. The
    child also gives back the parent's Learner it inherited, without giving up its own lock."""
    import gc
    import os
    import warnings
    parent = auto.Learner(tmp_path, 2)
    ready_r, ready_w = os.pipe()
    stop_r, stop_w = os.pipe()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)        # fork() with threads: the child only does this
        pid = os.fork()
    if pid == 0:
        try:
            os.close(ready_r)
            os.close(stop_w)
            child = auto.Learner(tmp_path, 2)             # noqa: F841 - held while it lives
            del parent                                    # the copy the child inherited, given back in the child
            gc.collect()
            os.write(ready_w, b"1")
            os.read(stop_r, 1)
        finally:
            os._exit(0)
    os.close(ready_w)
    os.close(stop_r)
    try:
        assert os.read(ready_r, 1) == b"1"                # b"" if the child failed
        del parent
        gc.collect()
        assert not alone_from_another_process(tmp_path)  # the child's Learner still lives
    finally:
        os.write(stop_w, b"1")
        os.waitpid(pid, 0)
    assert alone_from_another_process(tmp_path)


@pytest.mark.parametrize("content", ["", '{"kind": "call", "tool": "t", "arguments": {}, "output": "{}"}\n', "null\n"],
                         ids=["empty", "a call and no answer", "null"])
def test_a_legacy_trace_without_its_answer_is_not_complete(tmp_path, content):
    """REVISIONE-14b-quinquies: an empty trace, or one cut short after a call, numbered after the state, counted as a
    complete run in the state and in the report; a line `null` stopped the start with AttributeError."""
    learner = auto.Learner(tmp_path, 2, durable_closes=False)
    learner.events.append({"kind": "call", "tool": "t", "arguments": {}, "output": '{"a": 1, "b": 2}'})
    learner.done("1")
    learner.relearn()
    (tmp_path / "traces" / "run-0002.jsonl").write_text(content)
    del learner
    auto.Learner(tmp_path, 2)
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["runs"] == 2 and state["complete_runs"] == 1
    assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == 1


@pytest.mark.parametrize("on", [False, True])
def test_the_pause_after_an_answer_ends_at_the_first_call_that_arrived(tmp_path, monkeypatch, on):
    """REVISIONE-14b-quinquies: A's call arrives just after the reply and is held in the upstream; B's arrives after
    the pause and comes back first. The pause ends at A's arrival: the answer was followed by a call."""
    servers, learners = capture_uvicorn(monkeypatch), capture_learners(monkeypatch)
    gate = tmp_path / "gate"
    gate.mkdir()

    async def go():
        o = proxy.Options(servers={"upstream": {"command": sys.executable, "args": [str(ROOT / "tests" / "fixtures" /
                                                                                     "meta_server.py")],
                                                "env": {"META_GATE": str(gate)}}},
                          auto_dir=str(tmp_path / "work"), quiet=True, min_runs=2, background_relearn=on,
                          idle_seconds=0.9)
        task, url = await start_http(o, servers)
        learner = learners[-1]
        async with Client(url, mode="legacy") as a, Client(url, mode="legacy") as b:
            await run(a)
            held = asyncio.create_task(a.call_tool("volumes", {"gate": "a"}))
            for _ in range(1000):
                if (gate / "a.arrived").exists():
                    break
                await asyncio.sleep(0.01)
            await asyncio.sleep(1.0)
            assert not (await b.call_tool("volumes", {})).is_error          # after the pause, back first
            (gate / "a.go").touch()
            assert not (await held).is_error
            assert learner.state["answered_runs_followed_by_calls"] == 1
        await stop_http(task, servers)
    asyncio.run(go())


def test_a_result_filtered_by_a_policy_before_the_correction_does_not_count(tmp_path):
    """REVISIONE-14b-quinquies: between the relearning that promotes note_0 and the installation of its policy, two
    results are filtered with the old policy: one hides note_0, the other (note_0 left out by the upstream) hides
    other fields. Neither counts against the tool; the first result filtered by the new policy does."""
    engine = promoting(tmp_path)
    up_to_the_promotion(engine)
    policy = engine.learn_policy()                        # the promotion, not installed yet
    assert engine.learner.state["trimmed"] == {}
    without = {"items": [{k: v for k, v in PAYLOAD["items"][0].items() if k != "note_0"}]}
    assert observe(engine) and observe(engine, without)
    assert engine.learner.state["trimmed"] == {}
    engine.install(policy)
    assert observe(engine)
    assert engine.learner.state["trimmed"] == {"volumes": 1}


def test_a_retry_after_a_failed_relearning_relearns(tmp_path, monkeypatch):
    """Without the option, the closing of run 2 is recorded and its relearning fails (the report cannot be written):
    the client gets an error, and its retry, which finds nothing left to close, relearns before `Recorded.`."""
    from mcp.shared.exceptions import MCPError
    armed = fail_once(monkeypatch, "report.json")

    async def go():
        server, learner = proxy._server(options(tmp_path))
        async with Client(server) as client:
            await run(client)
            assert not (await client.call_tool("volumes", {})).is_error
            armed["on"] = True
            with pytest.raises(MCPError):
                await client.call_tool("tracetrim_done", {"answer": ANSWER})
            assert (await client.call_tool("tracetrim_done", {"answer": ANSWER})).content[0].text == auto.DONE_REPLY
            assert "call_id=" in (await client.call_tool("volumes", {})).content[0].text
    asyncio.run(go())
    assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == 2


def test_a_relearning_never_takes_as_covered_a_run_still_in_its_journal(tmp_path, monkeypatch):
    """A relearning reads its inputs while the publication of run 3 is left in the journal: the state in memory
    counted run 3, its trace was not on disk yet, and the report was marked as covering a run it had not read."""
    learner = auto.Learner(tmp_path, 2)
    for i in (1, 2):
        learner.events.append({"kind": "call", "tool": "t", "arguments": {}, "output": json.dumps({"a": i, "b": 0})})
        learner.done(str(i))
    learner.relearn()
    paused, go_on = threading.Event(), threading.Event()
    inputs = auto.Learner._inputs

    def held(self):
        paused.set()
        go_on.wait(10)
        return inputs(self)
    monkeypatch.setattr(auto.Learner, "_inputs", held)
    worker = threading.Thread(target=learner.relearn)
    worker.start()
    assert paused.wait(10)
    armed = fail_once(monkeypatch, "run-0003.jsonl")    # the first file of the publication: nothing published
    armed["on"] = True
    learner.events.append({"kind": "call", "tool": "t", "arguments": {}, "output": json.dumps({"a": 3, "b": 0})})
    with pytest.raises(OSError):
        learner.done("3")
    go_on.set()
    worker.join(10)
    state = json.loads((tmp_path / "state.json").read_text())
    report = json.loads((tmp_path / "report.json").read_text())["auto"]
    assert state["runs"] == 3
    assert state.get("report_pending") or report["complete_runs"] == 3


@pytest.mark.parametrize("on", [False, True])
def test_a_relearning_that_keeps_failing_never_stops_the_runs(tmp_path, monkeypatch, on):
    """Every relearning fails for a while (the report cannot be built): the closings are still recorded and the tool
    calls served, also after a publication finished by another request; once relearning works again, the next
    closing covers every run. (A relearning owed is made before a retry is confirmed, never before a closing is
    recorded, nor before a tool call is served.)"""
    from mcp.shared.exceptions import MCPError
    broken = {"on": False}
    relearn = auto.Learner._relearn

    def failing(self):
        if broken["on"]:
            raise RuntimeError("simulated")
        return relearn(self)
    monkeypatch.setattr(auto.Learner, "_relearn", failing)
    armed = fail_once(monkeypatch, "state.json")

    async def go():
        server, learner = proxy._server(options(tmp_path, background_relearn=on))
        async with Client(server) as client:
            await run(client)
            if on:
                await settled(learner)
            broken["on"] = True
            for i in range(3):
                assert not (await client.call_tool("volumes", {})).is_error
                armed["on"] = i == 1                      # run 3: its publication is left to the next request
                if on and i != 1:
                    assert (await client.call_tool("tracetrim_done", {"answer": ANSWER})).content[0].text \
                        == auto.DONE_REPLY
                    await settled(learner)
                else:
                    with pytest.raises(MCPError):
                        await client.call_tool("tracetrim_done", {"answer": ANSWER})
            broken["on"] = False
            await run(client)
            if on:
                await learner.background.drain()
    asyncio.run(go())
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["runs"] == 5 and state["complete_runs"] == 5 and "report_pending" not in state
    assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == 5


# -- REVISIONE-14b-sexies ----------------------------------------------------------------------------------------

def broken_relearning(monkeypatch):
    """Every relearning fails while `on` (the report cannot be built)."""
    broken = {"on": False}
    relearn = auto.Learner._relearn

    def failing(self):
        if broken["on"]:
            raise RuntimeError("simulated")
        return relearn(self)
    monkeypatch.setattr(auto.Learner, "_relearn", failing)
    return broken


@pytest.mark.parametrize("on", [False, True])
def test_a_retry_is_never_confirmed_while_its_relearning_fails(tmp_path, monkeypatch, on):
    """REVISIONE-14b-sexies: the publication of run 2 fails at the state, a tool listing finishes it, and the
    relearning keeps failing. With the option the retry replied `Recorded.` with the report of one run and the old
    policy. Now it fails in both modes, and the next retry, once relearning works, is confirmed and filtered."""
    from mcp.shared.exceptions import MCPError
    armed, broken = fail_once(monkeypatch, "state.json"), broken_relearning(monkeypatch)

    async def go():
        server, learner = proxy._server(options(tmp_path, background_relearn=on))
        async with Client(server) as client:
            await run(client)
            if on:
                await settled(learner)
            assert not (await client.call_tool("volumes", {})).is_error
            armed["on"] = True
            with pytest.raises(MCPError):
                await client.call_tool("tracetrim_done", {"answer": ANSWER})
            broken["on"] = True
            await client.list_tools()                                       # finishes the publication
            with pytest.raises(MCPError):
                await client.call_tool("tracetrim_done", {"answer": ANSWER})   # the retry: not confirmed
            assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == 1
            broken["on"] = False
            reply = await client.call_tool("tracetrim_done", {"answer": ANSWER})
            assert reply.content[0].text == auto.DONE_REPLY
            assert "call_id=" in (await client.call_tool("volumes", {})).content[0].text
            if on:
                await learner.background.drain()
    asyncio.run(go())
    assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == 2


def test_autotrim_installs_the_policy_after_a_transient_failure(tmp_path, monkeypatch):
    """REVISIONE-14b-sexies: AutoTrim, run 2's publication fails once at the state; the same `done` again finished
    it and relearned inside Learner.done, whose policy was dropped: report and config said trimming, the engine
    did not, and the next result went whole."""
    armed = fail_once(monkeypatch, "state.json")
    tt = auto.AutoTrim(tmp_path, min_runs=2)
    tt.output("volumes", {}, json.dumps(PAYLOAD))
    tt.done(ANSWER)
    tt.output("volumes", {}, json.dumps(PAYLOAD))
    armed["on"] = True
    with pytest.raises(OSError):
        tt.done(ANSWER)
    tt.done(ANSWER)                                       # the retry, the failure gone
    assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == 2
    assert tt.trimming and not tt.learner.relearn_owed
    assert "call_id=" in tt.output("volumes", {}, json.dumps(PAYLOAD))


def test_a_relearning_that_read_the_evidence_before_a_revert_keeps_its_mark(tmp_path, monkeypatch, capsys):
    """REVISIONE-14b-sexies: the command writes the mark; a relearning reads its stamp and the evidence still
    `active`; the command saves `reverted` and fails on its second mark. The relearning took the mark as covered and
    removed it: evidence reverted, config still promoting the field, even after repeating the command and a restart.
    The mark now names the change, and stays until a relearning has read it."""
    import gc
    from tracetrim import store
    from tracetrim.cli import main
    from tracetrim.evidence import Adaptive
    work = tmp_path / "work"
    promote_sequence(work, False, promote_runs=2, expand_runs=(3, 4))
    gc.collect()
    learner = auto.Learner(work, 2, adaptive=Adaptive(promote=True, promote_requested_runs=2))   # owns the directory
    first_mark, inputs_read = threading.Event(), threading.Event()
    write_atomic, seen = store.write_atomic, {"marks": 0}

    def writing(path, text):
        if Path(path).name == store.CHANGED:
            seen["marks"] += 1
            if seen["marks"] == 2:
                raise OSError("simulated")                # the second mark, after the evidence
            write_atomic(path, text)
            first_mark.set()
            assert inputs_read.wait(10)                   # the relearning reads before the evidence is saved
            return
        return write_atomic(path, text)
    monkeypatch.setattr(store, "write_atomic", writing)
    inputs = auto.Learner._inputs

    def reading(self):
        assert first_mark.wait(10)
        got = inputs(self)
        inputs_read.set()
        return got
    monkeypatch.setattr(auto.Learner, "_inputs", reading)
    worker = threading.Thread(target=learner.relearn)
    worker.start()
    command = ["promotions", str(work), "--revert", "volumes", "items[].note_0"]
    with pytest.raises(OSError):
        main(command)
    worker.join(10)
    monkeypatch.undo()
    assert (work / store.CHANGED).exists()                # the relearning did not read the change: the mark stays
    main(command)                                         # repeated: the proxy (here, the Learner) applies it
    assert "in use" in capsys.readouterr().out
    del learner
    gc.collect()
    proxy._server(options(work, promote=True, promote_runs=2))          # the next start
    assert [p["status"] for p in json.loads((work / "evidence.json").read_text())["promotions"]] == ["reverted"]
    assert json.loads((work / "report.config.json").read_text())["auto"]["promoted"] == {}
    assert not (work / store.CHANGED).exists()


@pytest.mark.skipif(__import__("importlib").util.find_spec("fcntl") is None,
                    reason="without fcntl the command cannot know, and relearns at once as up to 1.3")
def test_a_learner_of_the_same_process_owns_the_directory(tmp_path, capsys):
    """REVISIONE-14b-sexies: `alone` answered True whenever this process held the directory, so `tracetrim
    promotions` called by a program running an AutoTrim relearned from a second Learner, over the first one's files.
    Now the command leaves the change to that AutoTrim, whose next closing applies it."""
    from tracetrim import store
    from tracetrim.cli import main
    from tracetrim.evidence import Adaptive
    tt = auto.AutoTrim(tmp_path, min_runs=2, adaptive=Adaptive(promote=True, promote_requested_runs=2))
    for i in range(1, 5):
        out = tt.output("volumes", {}, json.dumps(PAYLOAD))
        m = re.search(r"call_id=(c[0-9a-f]{8})", out)
        if m and i >= 3:
            tt.expand({"call_id": m.group(1), "fields": ["items[].note_0"]})
        tt.done(ANSWER)
    assert json.loads((tmp_path / "report.config.json").read_text())["auto"]["promoted"] == \
        {"volumes": ["items[].note_0"]}
    with store.alone(tmp_path) as alone:
        assert alone is False
    main(["promotions", str(tmp_path), "--revert", "volumes", "items[].note_0"])
    assert "in use" in capsys.readouterr().out and (tmp_path / store.CHANGED).exists()
    tt.output("volumes", {}, json.dumps(PAYLOAD))
    tt.done(ANSWER)                                       # its relearning applies the revert
    assert json.loads((tmp_path / "report.config.json").read_text())["auto"]["promoted"] == {}
    assert not (tmp_path / store.CHANGED).exists()


def test_a_run_open_at_shutdown_is_kept_when_the_relearning_fails(tmp_path, monkeypatch):
    """REVISIONE-14b-sexies: without the option, A's closing is left in the journal; at shutdown B's open run is kept
    through Learner.flush, which finished A's publication and relearned before writing B's run: the relearning
    failed, and B's run was lost."""
    broken = broken_relearning(monkeypatch)
    armed = fail_once(monkeypatch, "state.json")
    learner = auto.Learner(tmp_path, 2)
    a, b = auto.Run(), auto.Run()
    with learner.use_run(a):
        learner.call("volumes", {}, json.dumps(PAYLOAD))
    with learner.use_run(b):
        learner.call("volumes", {}, json.dumps(PAYLOAD))
    armed["on"] = True
    with learner.use_run(a), pytest.raises(OSError):
        learner.done(ANSWER)                              # in the journal
    broken["on"] = True
    with learner.use_run(b), pytest.raises(RuntimeError):
        learner.flush()                                   # B's run written, then the relearning fails
    assert (tmp_path / "traces" / "run-0002-noanswer.jsonl").exists()
    assert json.loads((tmp_path / "state.json").read_text())["runs"] == 2


@pytest.mark.parametrize("isolated", [False, True])
def test_a_closing_by_inactivity_never_fails_the_call_that_follows(tmp_path, monkeypatch, isolated):
    """REVISIONE-14b-sexies: without the option, a call after the idle pause closed the run first; its relearning
    failed, and the call failed before reaching the upstream. Now the call is served and recorded in the new run."""
    servers, learners = capture_uvicorn(monkeypatch), capture_learners(monkeypatch)
    broken = broken_relearning(monkeypatch)

    async def go():
        task, url = await start_http(options(tmp_path / "work", idle_seconds=0.25, isolate_sessions=isolated),
                                     servers)
        learner = learners[-1]
        async with Client(url, mode="legacy") as client:
            assert not (await client.call_tool("volumes", {})).is_error
            broken["on"] = True                           # whichever closes the run by inactivity, it relearns in vain
            await asyncio.sleep(0.35)
            assert not (await client.call_tool("volumes", {})).is_error
            broken["on"] = False
            state = learner.state
            assert state["runs"] == 1
            runs = [s.run for s in learner_sessions(learner)] if isolated else [learner.run]
            assert sum(len(r.events) for r in runs) == 1
        await stop_http(task, servers)
    asyncio.run(go())


def learner_sessions(learner):
    """The isolated sessions of the proxy built with `learner` (tests only)."""
    import gc
    from tracetrim.sessions import HttpRuns
    return [s for h in gc.get_objects() if isinstance(h, HttpRuns) and h.engine.learner is learner
            for s in h.sessions.values()]


def test_a_legacy_trace_ending_with_a_blank_answer_is_not_complete(tmp_path):
    """REVISIONE-14b-sexies: a trace whose last message is blank counted as a complete run (a closing never
    records a blank answer)."""
    learner = auto.Learner(tmp_path, 2, durable_closes=False)
    learner.events.append({"kind": "call", "tool": "t", "arguments": {}, "output": '{"a": 1, "b": 2}'})
    learner.done("1")
    learner.relearn()
    (tmp_path / "traces" / "run-0002.jsonl").write_text(
        '{"kind": "call", "tool": "t", "arguments": {}, "output": "{}"}\n{"kind": "message", "text": "  "}\n')
    del learner
    auto.Learner(tmp_path, 2)
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["runs"] == 2 and state["complete_runs"] == 1
    assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == 1


def test_the_pause_starts_at_the_reply_also_after_a_failed_relearning(tmp_path, monkeypatch):
    """REVISIONE-14b-sexies: without the option, the closing of run 2 is recorded and its relearning fails; the retry
    replies `Recorded.`, but no reply had been marked, and the next call did not count as following the answer."""
    from mcp.shared.exceptions import MCPError
    broken = broken_relearning(monkeypatch)

    async def go():
        server, learner = proxy._server(options(tmp_path))
        async with Client(server) as client:
            await run(client)
            assert not (await client.call_tool("volumes", {})).is_error          # follows the answer of run 1
            broken["on"] = True
            with pytest.raises(MCPError):
                await client.call_tool("tracetrim_done", {"answer": ANSWER})
            broken["on"] = False
            assert (await client.call_tool("tracetrim_done", {"answer": ANSWER})).content[0].text == auto.DONE_REPLY
            assert not (await client.call_tool("volumes", {})).is_error          # follows the answer of run 2
            assert learner.state["answered_runs_followed_by_calls"] == 2
    asyncio.run(go())


def test_a_correction_made_at_start_counts_the_results_of_its_policy(tmp_path):
    """REVISIONE-14b-sexies: the relearning of the recovery at start promotes note_0; the engine then reads the policy
    it wrote from the file, taken as older than the correction: its filtered results never counted."""
    import gc
    engine = promoting(tmp_path)
    up_to_the_promotion(engine)                           # run 4 recorded, not relearned: pending on disk
    del engine
    gc.collect()
    engine = promoting(tmp_path)                          # the recovery promotes note_0
    assert [p["field"] for p in json.loads((tmp_path / "evidence.json").read_text())["promotions"]] == \
        ["items[].note_0"]
    assert observe(engine)
    assert engine.learner.state["trimmed"] == {"volumes": 1}


@pytest.mark.parametrize("on", [False, True])
def test_the_report_of_a_closing_reads_the_state_of_its_reply(tmp_path, monkeypatch, on):
    """REVISIONE-14b-sexies: volumes, done, volumes. With the option the second call arrived while the relearning had
    not read its inputs yet, and its arrival already counted it as following the answer: the report of run 1 said 1,
    without the option 0. The arrival is now counted when its call is recorded."""
    hold = threading.Event()
    inputs = auto.Learner._inputs

    def held(self):
        if on and self.state["runs"] == 1:
            hold.wait(5)
        return inputs(self)
    monkeypatch.setattr(auto.Learner, "_inputs", held)

    async def go():
        server, learner = proxy._server(options(tmp_path, background_relearn=on))
        async with Client(server) as client:
            await run(client)
            call = asyncio.create_task(client.call_tool("volumes", {}))
            await asyncio.sleep(0.3)
            hold.set()
            assert not (await call).is_error
            assert learner.state["answered_runs_followed_by_calls"] == 1
            if on:
                await learner.background.drain()
    asyncio.run(go())
    assert json.loads((tmp_path / "report.json").read_text())["auto"]["answered_runs_followed_by_calls"] == 0


def two_promotions(work):
    """note_0 asked back in runs 3 and 4, note_1 in runs 5 and 6: both promoted (two runs each)."""
    async def go():
        server, learner = proxy._server(options(work, promote=True, promote_runs=2))
        async with Client(server) as client:
            for i in range(1, 7):
                out = (await client.call_tool("volumes", {})).content[0].text
                m = re.search(r"call_id=(c[0-9a-f]{8})", out)
                if m and i >= 3:
                    field = "items[].note_0" if i < 5 else "items[].note_1"
                    await client.call_tool("expand", {"call_id": m.group(1), "fields": [field]})
                await client.call_tool("tracetrim_done", {"answer": ANSWER})
    asyncio.run(go())
    import gc
    gc.collect()


def test_a_mark_whose_change_was_never_saved_is_dropped(tmp_path, monkeypatch, capsys):
    """A restore stops after its mark and before the evidence: the mark names a change the evidence does not hold.
    It is dropped at the next start (under the write lock no command is between the two writes), and nothing is
    relearned for it, then or at later starts."""
    from tracetrim import evidence, store
    from tracetrim.cli import main
    work = tmp_path / "work"
    promote_sequence(work, False, promote_runs=2, expand_runs=(3, 4))
    import gc
    gc.collect()
    main(["promotions", str(work), "--revert", "volumes", "items[].note_0"])     # applied at once
    save = evidence.save
    monkeypatch.setattr(evidence, "save", lambda *a, **kw: (_ for _ in ()).throw(OSError("simulated")))
    with pytest.raises(OSError):
        main(["promotions", str(work), "--restore", "volumes", "items[].note_0"])
    monkeypatch.setattr(evidence, "save", save)
    assert (work / store.CHANGED).exists()
    capsys.readouterr()
    main(["promotions", str(work)])
    assert "not applied yet" not in capsys.readouterr().out     # the restore was never saved
    stamp = (work / "report.json").stat().st_mtime_ns
    for _ in range(2):
        proxy._server(options(work, promote=True, promote_runs=2))
        gc.collect()
    assert not (work / store.CHANGED).exists()
    assert (work / "report.json").stat().st_mtime_ns == stamp          # nothing to relearn
    assert [p["status"] for p in json.loads((work / "evidence.json").read_text())["promotions"]] == ["reverted"]


def test_a_second_change_keeps_the_first_one_named_in_the_mark(tmp_path, monkeypatch, capsys):
    """With a proxy on the directory, a revert of note_0 is saved and left to it; a revert of note_1 then stops before
    its evidence. Its mark names both: at the next start the first one, saved, is applied (a mark naming only the
    second, never saved, would have been dropped with the first still pending)."""
    from tracetrim import evidence, store
    from tracetrim.cli import main
    work = tmp_path / "work"
    two_promotions(work)
    assert json.loads((work / "report.config.json").read_text())["auto"]["promoted"] == \
        {"volumes": ["items[].note_0", "items[].note_1"]}
    proc = holder(work)
    main(["promotions", str(work), "--revert", "volumes", "items[].note_0"])
    assert "in use" in capsys.readouterr().out
    save = evidence.save
    monkeypatch.setattr(evidence, "save", lambda *a, **kw: (_ for _ in ()).throw(OSError("simulated")))
    with pytest.raises(OSError):
        main(["promotions", str(work), "--revert", "volumes", "items[].note_1"])
    monkeypatch.setattr(evidence, "save", save)
    proc.stdin.close()
    proc.wait(10)
    proxy._server(options(work, promote=True, promote_runs=2))          # the next start
    status = {p["field"]: p["status"] for p in json.loads((work / "evidence.json").read_text())["promotions"]}
    assert status == {"items[].note_0": "reverted", "items[].note_1": "active"}
    assert json.loads((work / "report.config.json").read_text())["auto"]["promoted"] == \
        {"volumes": ["items[].note_1"]}
    assert not (work / store.CHANGED).exists()


# -- REVISIONE-14b-septies ---------------------------------------------------------------------------------------

def failing_save(monkeypatch):
    """The next evidence.save raises (a command stopped between its first mark and its evidence)."""
    from tracetrim import evidence
    save = evidence.save

    def failing(*a, **kw):
        monkeypatch.setattr(evidence, "save", save)
        raise OSError("simulated")
    monkeypatch.setattr(evidence, "save", failing)


@pytest.mark.parametrize("order", ["revert then restore", "restore then revert"])
def test_a_change_of_the_same_field_stopped_before_its_evidence_keeps_the_saved_one(tmp_path, monkeypatch, capsys,
                                                                                     order):
    """REVISIONE-14b-septies: with a proxy on the directory, a change of note_0 is saved and left to it; the opposite
    change of the same field stops before its evidence. Its mark named only its own status, the start dropped it,
    and the saved change was never applied."""
    import gc
    from tracetrim import store
    from tracetrim.cli import main
    work = tmp_path / "work"
    promote_sequence(work, False, promote_runs=2, expand_runs=(3, 4))
    gc.collect()
    first, second = ("--revert", "--restore") if order == "revert then restore" else ("--restore", "--revert")
    if order == "restore then revert":
        main(["promotions", str(work), "--revert", "volumes", "items[].note_0"])     # reverted and applied
    proc = holder(work)
    main(["promotions", str(work), first, "volumes", "items[].note_0"])
    assert "in use" in capsys.readouterr().out
    failing_save(monkeypatch)
    with pytest.raises(OSError):
        main(["promotions", str(work), second, "volumes", "items[].note_0"])
    proc.stdin.close()
    proc.wait(10)
    proxy._server(options(work, promote=True, promote_runs=2))          # the next start
    saved = "reverted" if first == "--revert" else "active"
    assert [p["status"] for p in json.loads((work / "evidence.json").read_text())["promotions"]] == [saved]
    promoted = json.loads((work / "report.config.json").read_text())["auto"]["promoted"]
    assert promoted == ({} if saved == "reverted" else {"volumes": ["items[].note_0"]})
    assert not (work / store.CHANGED).exists()


def test_a_mark_with_no_note_is_kept_by_the_next_command(tmp_path, monkeypatch, capsys):
    """REVISIONE-14b-septies: a revert of note_0 saved and left to the proxy under a mark with no note (an earlier
    build of 1.4); a revert of note_1 then stops before its evidence. Its mark took the earlier one as naming
    nothing, the start dropped it, and the revert of note_0 was never applied."""
    from tracetrim import store
    from tracetrim.cli import main
    work = tmp_path / "work"
    two_promotions(work)
    proc = holder(work)
    main(["promotions", str(work), "--revert", "volumes", "items[].note_0"])
    assert "in use" in capsys.readouterr().out
    (work / store.CHANGED).write_text("2026-11-20T10:00:00.000000+00:00\n")         # the mark of an earlier build
    failing_save(monkeypatch)
    with pytest.raises(OSError):
        main(["promotions", str(work), "--revert", "volumes", "items[].note_1"])
    proc.stdin.close()
    proc.wait(10)
    proxy._server(options(work, promote=True, promote_runs=2))
    assert json.loads((work / "report.config.json").read_text())["auto"]["promoted"] == \
        {"volumes": ["items[].note_1"]}
    assert not (work / store.CHANGED).exists()


def test_a_learner_that_starts_during_a_command_waits_for_it(tmp_path):
    """REVISIONE-14b-septies: `tracetrim promotions` held the directory alone when an AutoTrim of the same process
    started: its shared lock was the process's lock, the end of the command gave it up, and a command in another
    process found the directory free while the AutoTrim lived."""
    from tracetrim import store
    (tmp_path / "traces").mkdir()
    inside, release, built = threading.Event(), threading.Event(), threading.Event()
    learners = []

    def command():
        with store.alone(tmp_path) as alone:
            assert alone
            inside.set()
            release.wait(10)

    def start():
        learners.append(auto.AutoTrim(tmp_path, min_runs=2))
        built.set()
    a = threading.Thread(target=command)
    a.start()
    assert inside.wait(10)
    b = threading.Thread(target=start)
    b.start()
    time.sleep(0.3)
    assert not built.is_set()                             # waits for the command, as from another process
    release.set()
    a.join(10)
    b.join(10)
    assert built.is_set()
    assert not alone_from_another_process(tmp_path)      # the AutoTrim holds the directory


def test_a_trace_counted_by_an_earlier_build_stops_the_policy_when_the_count_drops(tmp_path):
    """REVISIONE-14b-septies: min_runs 3, and an earlier build counted a trace ending with a blank answer: three
    complete runs, filter on. Reopened, a run closed without answer relearns two complete runs and writes a config
    that trims nothing, but the engine kept the policy and went on filtering. The state is set right as well."""
    import gc
    tt = auto.AutoTrim(tmp_path, min_runs=3)
    for _ in range(3):
        tt.output("volumes", {}, json.dumps(PAYLOAD))
        tt.done(ANSWER)
    assert tt.trimming
    del tt
    gc.collect()
    trace = tmp_path / "traces" / "run-0003.jsonl"
    lines = trace.read_text().splitlines()
    trace.write_text("\n".join(lines[:-1] + ['{"kind": "message", "text": "   "}']) + "\n")   # as the build had it
    tt = auto.AutoTrim(tmp_path, min_runs=3)
    assert tt.trimming                                    # the config on disk, until the next relearning
    tt.output("volumes", {}, json.dumps(PAYLOAD))
    tt.done(None)                                         # a run without answer: relearned
    assert json.loads((tmp_path / "report.config.json").read_text())["tools"] == {}
    assert not tt.trimming
    assert "call_id=" not in tt.output("volumes", {}, json.dumps(PAYLOAD))
    assert json.loads((tmp_path / "state.json").read_text())["complete_runs"] == 2


def test_the_listing_reads_the_note_of_the_mark_once(tmp_path, monkeypatch, capsys):
    """REVISIONE-14b-septies: the listing read the note twice; the proxy removed the mark in between, and the second
    read gave None to a function that iterates it (TypeError in a read-only command)."""
    from tracetrim import store
    from tracetrim.cli import main
    work = tmp_path / "work"
    promote_sequence(work, False, promote_runs=2, expand_runs=(3, 4))
    (work / store.CHANGED).write_text(json.dumps({"format": "tracetrim/evidence-changed@1", "changed": [
        {"tool": "volumes", "field": "items[].note_0", "status": "active"}]}))
    changes, calls = store.changes, []

    def once(directory):
        calls.append(directory)
        return changes(directory) if len(calls) == 1 else None          # consumed by the proxy after the first read
    monkeypatch.setattr(store, "changes", once)
    main(["promotions", str(work)])
    assert "not applied yet" in capsys.readouterr().out


def test_a_failed_retry_moves_the_start_of_the_pause(tmp_path, monkeypatch):
    """REVISIONE-14b-septies: without the option, the closing of run 2 and its retry both reply with an error (the
    relearning keeps failing); the call right after the retry did not count as following the answer, the pause
    being measured from the first error."""
    from mcp.shared.exceptions import MCPError
    broken = broken_relearning(monkeypatch)

    async def go():
        server, learner = proxy._server(options(tmp_path, idle_seconds=0.24))
        async with Client(server) as client:
            await run(client)
            assert not (await client.call_tool("volumes", {})).is_error          # follows the answer of run 1
            broken["on"] = True
            with pytest.raises(MCPError):
                await client.call_tool("tracetrim_done", {"answer": ANSWER})
            await asyncio.sleep(0.4)
            with pytest.raises(MCPError):
                await client.call_tool("tracetrim_done", {"answer": ANSWER})   # the retry, an error too
            assert not (await client.call_tool("volumes", {})).is_error          # right after it
            assert learner.state["answered_runs_followed_by_calls"] == 2
    asyncio.run(go())


@pytest.mark.parametrize("on", [False, True])
def test_a_late_repeat_of_a_confirmed_closing_leaves_the_pause(tmp_path, on):
    """REVISIONE-14b-septies: a repeat of tracetrim_done long after its `Recorded.` restarted the pause, and the next
    call counted as following the answer (0 in 1.3.1 and before, 1 then). The pause stays at the confirmation."""
    async def go():
        server, learner = proxy._server(options(tmp_path, idle_seconds=0.24, background_relearn=on))
        async with Client(server) as client:
            await run(client)
            if on:
                await settled(learner)
            await asyncio.sleep(0.5)
            assert (await client.call_tool("tracetrim_done", {"answer": ANSWER})).content[0].text == auto.DONE_REPLY
            assert not (await client.call_tool("volumes", {})).is_error
            assert learner.state["answered_runs_followed_by_calls"] == 0
            if on:
                await learner.background.drain()
    asyncio.run(go())


def test_a_legacy_message_that_is_not_text_is_set_aside(tmp_path):
    """REVISIONE-14b-septies: a trace numbered after the state, ending with a message whose text is a number, stopped
    the start with AttributeError."""
    learner = auto.Learner(tmp_path, 2, durable_closes=False)
    learner.events.append({"kind": "call", "tool": "t", "arguments": {}, "output": '{"a": 1, "b": 2}'})
    learner.done("1")
    learner.relearn()
    (tmp_path / "traces" / "run-0002.jsonl").write_text(
        '{"kind": "call", "tool": "t", "arguments": {}, "output": "{}"}\n{"kind": "message", "text": 42}\n')
    del learner
    auto.Learner(tmp_path, 2)
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["runs"] == 2 and state["complete_runs"] == 1


# -- REVISIONE-14b-octies ----------------------------------------------------------------------------------------

def visible(out: str) -> dict:
    """The record the model reads, without the `[trimmed]` line."""
    return json.loads(out.split("\n", 1)[0])["items"][0]


def promoting_autotrim(work):
    from tracetrim.evidence import Adaptive
    return auto.AutoTrim(work, min_runs=2, adaptive=Adaptive(promote=True, promote_requested_runs=2))


@pytest.mark.parametrize("then", ["repeated", "reopened"])
def test_a_revert_of_1_3_stopped_before_its_relearning_is_applied(tmp_path, capsys, then):
    """REVISIONE-14b-octies: `tracetrim promotions` of 1.3 saved the revert and stopped before relearning (no mark in
    1.3): evidence reverted, config still promoting note_0. Repeated, the command found nothing to change; reopened,
    AutoTrim served note_0 until a new closing. The disagreement is now owed, and the first output has no note_0."""
    import gc
    from tracetrim import evidence
    from tracetrim.cli import main
    work = tmp_path / "work"
    promote_sequence(work, False, promote_runs=2, expand_runs=(3, 4))
    gc.collect()
    data = evidence.load(work / "evidence.json")
    evidence.revert(data, "volumes", "items[].note_0")
    evidence.save(work / "evidence.json", data)                        # what 1.3 left: no mark, the config behind
    if then == "repeated":
        main(["promotions", str(work), "--revert", "volumes", "items[].note_0"])
        assert "not applied yet" in capsys.readouterr().out
    tt = promoting_autotrim(work)
    assert "note_0" not in visible(tt.output("volumes", {}, json.dumps(PAYLOAD)))
    assert json.loads((work / "report.config.json").read_text())["auto"]["promoted"] == {}


def test_a_failed_scan_of_the_traces_never_consumes_the_mark(tmp_path, monkeypatch):
    """REVISIONE-14b-octies: during the command's relearning the scan of traces/ failed once (EIO); glob gave no file,
    the relearning took it for a directory with no runs, consumed the mark, and the config kept note_0, also after a
    retry and a reopening. The scan now fails the relearning, and the mark stays."""
    import errno
    import gc
    import os
    from tracetrim.cli import main
    work = tmp_path / "work"
    promote_sequence(work, False, promote_runs=2, expand_runs=(3, 4))
    gc.collect()
    scandir, glob, inputs = os.scandir, Path.glob, auto.Learner._inputs
    armed = {"on": False, "done": False}

    def failing(path="."):
        if armed["on"] and str(path).rstrip("/").endswith("traces"):
            armed["on"] = False
            raise OSError(errno.EIO, "simulated")
        return scandir(path)

    def globbing(self, pattern, *a, **kw):
        if armed["on"] and self.name == "traces":
            armed["on"] = False
            return iter([])                              # glob gives up on an unreadable directory, silently
        return glob(self, pattern, *a, **kw)

    def reading(self):
        if not armed["done"]:
            armed["on"] = armed["done"] = True
        return inputs(self)
    monkeypatch.setattr(os, "scandir", failing)
    monkeypatch.setattr(Path, "glob", globbing)
    monkeypatch.setattr(auto.Learner, "_inputs", reading)
    try:
        main(["promotions", str(work), "--revert", "volumes", "items[].note_0"])
    except OSError:
        pass                                              # the relearning failed: the change waits under its mark
    monkeypatch.undo()
    gc.collect()
    tt = promoting_autotrim(work)
    assert "note_0" not in visible(tt.output("volumes", {}, json.dumps(PAYLOAD)))
    assert json.loads((work / "report.config.json").read_text())["auto"]["promoted"] == {}


def test_a_read_error_never_sets_a_sound_trace_aside(tmp_path, monkeypatch):
    """REVISIONE-14b-octies: one read of run-0001.jsonl failed (EIO) during a relearning: the trace became .corrupt,
    the complete runs fell to one and the filter went off. A read error now fails the relearning, which stays owed;
    the trace stays, and the retry relearns both runs."""
    import errno
    tt = auto.AutoTrim(tmp_path, min_runs=2)
    for _ in range(2):
        tt.output("volumes", {}, json.dumps(PAYLOAD))
        tt.done(ANSWER)
    assert tt.trimming
    read, armed = Path.read_text, {"on": False}

    def failing(self, *a, **kw):
        if armed["on"] and self.name == "run-0001.jsonl":
            armed["on"] = False
            raise OSError(errno.EIO, "simulated")
        return read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", failing)
    tt.output("volumes", {}, json.dumps(PAYLOAD))
    armed["on"] = True
    try:
        tt.done(None)
    except OSError:
        pass
    assert (tmp_path / "traces" / "run-0001.jsonl").exists() and not list(tmp_path.glob("traces/*.corrupt"))
    assert tt.trimming
    tt.done(None)                                         # the retry: nothing to close, the relearning owed
    assert tt.trimming and not tt.learner.relearn_owed
    assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == 2
    assert json.loads((tmp_path / "state.json").read_text())["complete_runs"] == 2


def test_an_answer_with_a_unicode_line_separator_is_a_complete_run(tmp_path):
    """REVISIONE-14b-octies: an answer holding U+2028 is written as is inside its JSON string; the reader split the
    record there (splitlines) and set the trace aside as corrupt."""
    tt = auto.AutoTrim(tmp_path, min_runs=2)
    for _ in range(2):
        tt.output("volumes", {}, json.dumps(PAYLOAD))
        tt.done("spa weekend has 1600 searches a month.")
    assert not list(tmp_path.glob("traces/*.corrupt"))
    assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == 2
    assert tt.trimming


@pytest.mark.skipif(__import__("importlib").util.find_spec("fcntl") is None, reason="fcntl only")
def test_a_learner_that_cannot_take_its_presence_lock_does_not_start(tmp_path, monkeypatch):
    """REVISIONE-14b-octies: the shared lock failed once (ENOLCK); the Learner started without it, and a command in
    another process relearned the directory under it. The failure now stops the Learner before it serves."""
    import errno
    import fcntl
    lockf, armed = fcntl.lockf, {"on": True}

    def failing(fd, cmd, *a):
        if armed["on"] and cmd == fcntl.LOCK_SH:
            armed["on"] = False
            raise OSError(errno.ENOLCK, "No locks available")
        return lockf(fd, cmd, *a)
    monkeypatch.setattr(fcntl, "lockf", failing)
    with pytest.raises(OSError, match="could not be locked"):
        auto.AutoTrim(tmp_path, min_runs=2)
    tt = auto.AutoTrim(tmp_path, min_runs=2)             # the next start, the lock working
    assert not alone_from_another_process(tmp_path) and tt.learner is not None


@pytest.mark.parametrize("on", [False, True])
def test_a_retry_after_a_failed_publication_moves_the_start_of_the_pause(tmp_path, monkeypatch, on):
    """REVISIONE-14b-octies: run 1 confirmed; the publication of run 2 fails at the state, and the run carried the
    confirmation of run 1. The retry 900 ms later replied `Recorded.`, and the call right after it did not count as
    following the answer (pause 650 ms)."""
    from mcp.shared.exceptions import MCPError
    armed = fail_once(monkeypatch, "state.json")

    async def go():
        server, learner = proxy._server(options(tmp_path, idle_seconds=0.65, background_relearn=on))
        async with Client(server) as client:
            await run(client)
            if on:
                await settled(learner)
            assert not (await client.call_tool("volumes", {})).is_error          # follows the answer of run 1
            armed["on"] = True
            with pytest.raises(MCPError):
                await client.call_tool("tracetrim_done", {"answer": ANSWER})
            await asyncio.sleep(0.9)
            assert (await client.call_tool("tracetrim_done", {"answer": ANSWER})).content[0].text == auto.DONE_REPLY
            assert not (await client.call_tool("volumes", {})).is_error          # right after the confirmation
            assert learner.state["answered_runs_followed_by_calls"] == 2
            if on:
                await learner.background.drain()
    asyncio.run(go())


# -- REVISIONE-14b-nonies (independent review) -------------------------------------------------------------------

def read_fails_once(monkeypatch, name):
    """The next read of the file `name` fails with EIO, once, when armed."""
    import errno
    read, armed = Path.read_text, {"on": False}

    def failing(self, *a, **kw):
        if armed["on"] and self.name == name:
            armed["on"] = False
            raise OSError(errno.EIO, "simulated")
        return read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", failing)
    return armed


@pytest.mark.parametrize("unread", ["report.config.json", "evidence.json"])
def test_a_read_error_at_start_is_no_agreement_of_config_and_evidence(tmp_path, monkeypatch, unread):
    """REVISIONE-14b-nonies: the revert of 1.3 stopped before its relearning (evidence reverted, config behind, no
    mark), and at the next start one read of the config or of the evidence failed: the check took the error for an
    agreement, and the first output had note_0. A read error now makes the relearning owed."""
    import gc
    from tracetrim import evidence
    work = tmp_path / "work"
    promote_sequence(work, False, promote_runs=2, expand_runs=(3, 4))
    gc.collect()
    data = evidence.load(work / "evidence.json")
    evidence.revert(data, "volumes", "items[].note_0")
    evidence.save(work / "evidence.json", data)
    read_fails_once(monkeypatch, unread)["on"] = True
    tt = promoting_autotrim(work)
    assert "note_0" not in visible(tt.output("volumes", {}, json.dumps(PAYLOAD)))
    assert json.loads((work / "report.config.json").read_text())["auto"]["promoted"] == {}


def test_a_read_error_of_the_savings_never_writes_their_history_over(tmp_path, monkeypatch):
    """REVISIONE-14b-nonies (also in 1.3.1): one read of savings.json failed during a closing; the file was written
    again from that run alone, and the savings of the runs before were lost for good. The counts now wait for the
    next save."""
    from tracetrim.contract import ToolCall, ToolResult
    from tracetrim.engine import Engine
    learner = auto.Learner(tmp_path, 2)
    engine = Engine(learner)
    armed = read_fails_once(monkeypatch, "savings.json")
    for i in range(1, 5):
        engine.observe(ToolCall("t", {}), ToolResult(json.dumps({"a": i, "b": i + 1})))
        armed["on"] = i == 3
        try:
            learner.done(str(i))
        except OSError:
            assert i == 3                                 # recorded, its savings left for the next save
    runs = json.loads((tmp_path / "savings.json").read_text())["runs"]
    assert {r: v["tools"]["t"]["calls"] for r, v in runs.items()} == \
        {"run-0001": 1, "run-0002": 1, "run-0003": 1, "run-0004": 1}
