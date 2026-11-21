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


def test_closings_in_quick_succession_coalesce_and_the_last_policy_sees_every_run(tmp_path, slow):
    slow["delay"] = 0.5

    async def go():
        server, learner = proxy._server(options(tmp_path, background_relearn=True))
        async with Client(server) as client:
            for _ in range(3):
                assert await run(client) < 0.4
            # One relearning running, one asked for: never one per closing.
            await settled(learner)
            assert learner.state["complete_runs"] == 3
            assert engine_trims(learner)
    asyncio.run(go())
    assert slow["calls"] == 2 and slow["runs"][-1] == 3
    assert json.loads((tmp_path / "report.json").read_text())["auto"]["complete_runs"] == 3


def engine_trims(learner):
    config = json.loads((learner.dir / "report.config.json").read_text())
    return bool(config["tools"])


def normal(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    # The time of the relearning is written in the report and the config (ISO, and "... UTC" in the page).
    text = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+00:00|Z| UTC)?", "<time>", text)
    return re.sub(r"\bc[0-9a-f]{8}\b", "<call_id>", text)


def test_at_the_end_report_config_and_projection_match_the_mode_without_the_option(tmp_path, monkeypatch):
    # The call ids are random, and their token counts reach the savings: the same sequence in both modes.
    from tracetrim import engine
    counter = iter(range(10**6))
    monkeypatch.setattr(engine.secrets, "token_hex", lambda n: f"{next(counter):0{2 * n}x}")

    async def sequence(work, on):
        server, learner = proxy._server(options(work, background_relearn=on))
        async with Client(server) as client:
            for _ in range(4):
                await run(client)
                if on:
                    await settled(learner)
            if on:
                await learner.background.drain()
    for name, on in (("off", False), ("on", True)):
        counter = iter(range(10**6))
        monkeypatch.setattr(engine.secrets, "token_hex", lambda n, c=counter: f"{next(c):0{2 * n}x}")
        asyncio.run(sequence(tmp_path / name, on))
    for f in ("report.json", "report.html", "report.config.json", "report.projection.json", "state.json", "evidence.json"):
        a, b = tmp_path / "off" / f, tmp_path / "on" / f
        assert a.exists() == b.exists(), f
        if a.exists():
            assert normal(a) == normal(b), f
    assert (tmp_path / "on" / "report.projection.json").read_bytes() == (tmp_path / "off" / "report.projection.json").read_bytes()


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
