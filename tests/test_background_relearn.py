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
    from tracetrim import engine
    from tracetrim import report
    monkeypatch.setattr(auto_module, "datetime", FixedClock.datetime)
    monkeypatch.setattr(report, "datetime", FixedClock.datetime)
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
    entered, release = threading.Event(), threading.Event()
    compute = report.compute

    def slow_compute(*a, **kw):
        entered.set()
        release.wait(5)
        return compute(*a, **kw)
    monkeypatch.setattr(report, "compute", slow_compute)
    learner.mark_report_pending()
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
