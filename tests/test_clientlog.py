"""`--log-client FILE` (0.4.1): one JSON line per request of the client, and nothing in it
that is not on the allow-list of tracetrim.clientlog."""
import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import Client, ClientSession
from mcp.client import advertise
from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from tracetrim import proxy, skill
from tracetrim.clientlog import ClientLog, entry

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = f"{sys.executable} {ROOT / 'tests' / 'fixtures' / 'meta_server.py'}"
SECRET_ARGUMENT = "ARGUMENT-b7f3-never-in-the-log"
SECRET_TOKEN = "TOKEN-9c41-never-in-the-log"
ALLOWED_KEYS = {"time", "session", "method", "clientInfo", "protocolVersion", "capabilities", "uri", "tool"}


def lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_client_that_declares_skills_and_reads_the_skill_leaves_both_in_the_log(tmp_path):
    log = tmp_path / "client.jsonl"
    o = proxy.Options(upstream_cmd=UPSTREAM, quiet=True, auto_dir=str(tmp_path / "work"), min_runs=2,
                      log_client=str(log))

    async def go():
        server, _ = proxy._server(o)
        # In memory, on the protocol with a handshake: the one in which a host declares its extensions in `initialize`.
        async with Client(server, mode="legacy", extensions=[advertise(skill.EXTENSION, {})]) as c:
            await c.list_tools()
            await c.read_resource(skill.URI)
            await c.call_tool("volumes", {"keyword": SECRET_ARGUMENT})
            await c.call_tool("tracetrim_done", {"answer": f"an answer with {SECRET_ARGUMENT}"})
    asyncio.run(go())

    got = lines(log)
    assert [x["method"] for x in got] == ["initialize", "tools/list", "resources/read", "tools/call", "tools/call"]
    init, listing, read, call, done = got
    assert skill.EXTENSION in init["capabilities"]["extensions"] and init["protocolVersion"] == "2025-11-25"
    assert set(init["clientInfo"]) <= {"name", "version", "title"} and init["clientInfo"]["name"]
    assert read["uri"] == skill.URI == "skill://tracetrim/SKILL.md"
    # A tool call leaves the name of the tool, and tools/list nothing but the method.
    assert (call["tool"], done["tool"]) == ("volumes", "tracetrim_done")
    assert set(listing) == {"time", "session", "method"} and set(call) == {"time", "session", "method", "tool"}
    assert len({x["session"] for x in got}) == 1 and len(init["session"]) == 12
    assert SECRET_ARGUMENT not in log.read_text() and all(set(x) <= ALLOWED_KEYS for x in got)


def test_only_what_is_on_the_allow_list_is_written():
    noise = {"arguments": {"q": SECRET_ARGUMENT}, "headers": {"Authorization": SECRET_TOKEN}, "token": SECRET_TOKEN,
             "_meta": {"progressToken": SECRET_TOKEN, "authorization": SECRET_TOKEN}, "cursor": SECRET_TOKEN}
    assert entry("tools/call", {"name": "search", **noise}) == {"method": "tools/call", "tool": "search"}
    assert entry("tools/list", noise) == {"method": "tools/list"}
    for method in ("resources/read", "skills/get"):
        assert entry(method, {"uri": skill.URI, **noise}) == {"method": method, "uri": skill.URI}
    for method in ("resources/list", "skills/list", "prompts/get", "ping", "made/up"):
        assert entry(method, {"uri": "x", "name": "y", **noise}) == {"method": method}
    # initialize: what the client declares, and of clientInfo only name, version and title.
    init = entry("initialize", {"protocolVersion": "2025-11-25", "capabilities": {"extensions": {skill.EXTENSION: {}}},
                                "clientInfo": {"name": "host", "version": "1", "title": "Host", "apiKey": SECRET_TOKEN},
                                **noise})
    assert init == {"method": "initialize", "protocolVersion": "2025-11-25", "clientInfo": {"name": "host", "version": "1", "title": "Host"},
                    "capabilities": {"extensions": {skill.EXTENSION: {}}}}
    # The protocol without a handshake declares the same in the `_meta` of server/discover.
    meta = {"io.modelcontextprotocol/clientInfo": {"name": "host", "version": "2"},
            "io.modelcontextprotocol/clientCapabilities": {"extensions": {skill.EXTENSION: {}}},
            "io.modelcontextprotocol/protocolVersion": "2026-07-28", "authorization": SECRET_TOKEN}
    assert entry("server/discover", {"_meta": meta, "token": SECRET_TOKEN}) == {
        "method": "server/discover", "clientInfo": {"name": "host", "version": "2"}, "protocolVersion": "2026-07-28",
        "capabilities": {"extensions": {skill.EXTENSION: {}}}}
    # Malformed or oversized: cut, never an error and never the whole thing.
    assert entry("tools/call", {"name": {"not": "a string"}}) == {"method": "tools/call"}
    assert entry("resources/read", {"uri": "skill://" + "x" * 5000})["uri"] == "skill://" + "x" * 192
    assert entry("initialize", None) == {"method": "initialize"}
    big = entry("initialize", {"capabilities": {"experimental": {"blob": "y" * 10_000}, "roots": {}}})
    assert big["capabilities"] == {"truncated": ["experimental", "roots"]}


def test_the_session_id_is_a_salted_hash_and_never_the_real_one(tmp_path):
    class Connection:
        def __init__(self, session_id=None):
            self.session_id = session_id

    one, other = ClientLog(str(tmp_path / "a.jsonl")), ClientLog(str(tmp_path / "b.jsonl"))
    http, stdio, stdio_2 = Connection("3f9a5d0c7e1b4a6f8d2c"), Connection(), Connection()
    assert one.session(http) == one.session(Connection("3f9a5d0c7e1b4a6f8d2c")) != one.session(Connection("another"))
    assert one.session(stdio) == one.session(stdio) != one.session(stdio_2)
    assert one.session(http) != other.session(http)                  # the salt is the process's own
    one.record(http, "tools/list", None)
    assert "3f9a5d0c7e1b4a6f8d2c" not in (tmp_path / "a.jsonl").read_text()
    assert (tmp_path / "a.jsonl").stat().st_mode & 0o077 == 0        # the owner's only


def test_without_the_option_no_request_is_observed():
    server, _ = proxy._server(proxy.Options(upstream_cmd=UPSTREAM, quiet=True))
    assert "log_request" not in [getattr(m, "__name__", "") for m in server.middleware]
    logged, _ = proxy._server(proxy.Options(upstream_cmd=UPSTREAM, quiet=True, log_client="/dev/null"))
    assert "log_request" in [getattr(m, "__name__", "") for m in logged.middleware]


def test_over_stdio_the_log_follows_a_whole_session(tmp_path):
    log = tmp_path / "client.jsonl"
    p = StdioServerParameters(command=sys.executable, cwd=str(ROOT), args=[
        "-m", "tracetrim.cli", "proxy", "--upstream-cmd", UPSTREAM, "--quiet", "--auto", str(tmp_path / "work"),
        "--log-client", str(log)],
        # The SDK starts the process with a reduced environment: name the source under test.
        env={**get_default_environment(), "PYTHONPATH": str(ROOT / "src")})

    async def go():
        async with stdio_client(p) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            await s.list_tools()
            await s.call_tool("volumes", {"keyword": SECRET_ARGUMENT})
    asyncio.run(go())
    got = lines(log)
    # (The SDK's client lists the tools again by itself after a call: a request like any other.)
    assert [x["method"] for x in got][:3] == ["initialize", "tools/list", "tools/call"]
    assert len({x["session"] for x in got}) == 1 and got[2]["tool"] == "volumes"
    assert SECRET_ARGUMENT not in log.read_text() and UPSTREAM not in log.read_text()
    # Still recorded where it belongs: the trace has the arguments, the diagnostics do not.
    assert SECRET_ARGUMENT in (tmp_path / "work" / "traces" / "run-0001-noanswer.jsonl").read_text()


def test_over_http_several_clients_write_whole_lines_and_no_secret(tmp_path):
    log = tmp_path / "client.jsonl"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen([sys.executable, "-m", "tracetrim.cli", "proxy", "--upstream-cmd", UPSTREAM, "--quiet",
                             "--auto", str(tmp_path / "work"), "--http", str(port), "--token", SECRET_TOKEN,
                             "--log-client", str(log)], cwd=str(ROOT), stderr=subprocess.DEVNULL)
    clients, calls = 6, 8

    async def one(n):
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {SECRET_TOKEN}", "X-Client": f"client-{n}"})
        async with http, streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http) as (r, w, *_):
            async with ClientSession(r, w) as s:
                await s.initialize()
                await s.read_resource(skill.URI)
                await asyncio.gather(*(s.call_tool("volumes", {"keyword": SECRET_ARGUMENT, "n": i}) for i in range(calls)))

    async def go():
        await asyncio.gather(*(one(n) for n in range(clients)))

    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        asyncio.run(go())
    finally:
        proc.terminate()
        proc.wait(timeout=30)

    text = log.read_text()
    got = lines(log)                                                  # every line is whole JSON
    assert text.endswith("\n") and len(text.splitlines()) == len(got) >= clients * (calls + 2)
    per_session: dict[str, list[str]] = {}
    for x in got:
        assert set(x) <= ALLOWED_KEYS
        per_session.setdefault(x["session"], []).append(x["method"])
    assert len(per_session) == clients
    for methods in per_session.values():
        # The SDK's client also lists the tools by itself, once per session, to check the results.
        assert sorted(m for m in methods if m != "tools/list") == sorted(["initialize", "resources/read"] + ["tools/call"] * calls)
    for secret in (SECRET_ARGUMENT, SECRET_TOKEN, "Bearer", "Authorization", "client-0", "127.0.0.1", "meta_server"):
        assert secret.lower() not in text.lower(), secret


@pytest.mark.parametrize("argv", [[], ["--log-client", "client.jsonl"]])
def test_the_cli_passes_the_option_and_it_is_off_by_default(argv, monkeypatch, tmp_path):
    from tracetrim import cli
    seen = []

    async def fake_serve(o):
        seen.append(o.log_client)

    monkeypatch.setattr(proxy, "serve", fake_serve)
    monkeypatch.setattr(cli, "serve", fake_serve, raising=False)
    cli.main(["proxy", "--upstream-cmd", "true", *argv])
    assert seen == [argv[1] if argv else None]
