"""The skill over MCP (extension io.modelcontextprotocol/skills, SEP-2640) and the closing of a run,
through a real proxy process over stdio.

Three situations, kept apart:

  1. the client loads the skill      it follows the host's loading path of the specification:
                                     list, read, verify size, digest and frontmatter
  2. the client only sees resources  no skill-loading path: the SKILL.md is an ordinary resource,
                                     and the tool descriptions must be enough
  3. the session ends without tracetrim_done

These tests show that the SERVER conforms. Whether a given host loads the
skill on its own is a property of that host, and is not tested here.
"""
import asyncio
import hashlib
import json
import subprocess
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError
from pydantic import TypeAdapter

from tracetrim import skill
from tracetrim.about import VERSION

ROOT = Path(__file__).resolve().parent.parent

# The proxy runs from this checkout's source, whatever tracetrim the interpreter
# has installed.
CHECKOUT_ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
FIXTURES = ROOT / "tests" / "fixtures"
ANSWER = "spa weekend has 1600 searches a month."
ANY = TypeAdapter(dict[str, Any])


class Raw(types.Request[dict, str]):
    """A request the SDK has no type for (it has no Skills support yet)."""


def params(*flags) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, cwd=str(ROOT), env=CHECKOUT_ENV, args=[
        "-m", "tracetrim.cli", "proxy", "--upstream-cmd", f"{sys.executable} {FIXTURES / 'meta_server.py'}",
        "--quiet", *map(str, flags)])


def modern(flags, script):
    """A client of the 2026-07-28 protocol: capabilities from server/discover."""
    async def go():
        async with Client(params(*flags)) as c:
            assert c.protocol_version == "2026-07-28"
            return await script(c, c.session)
    return asyncio.run(go())


def handshake(flags, script):
    """A client of the earlier protocol: capabilities from initialize."""
    async def go():
        async with stdio_client(params(*flags)) as (r, w), ClientSession(r, w) as s:
            init = await s.initialize()
            assert init.protocol_version == "2025-11-25"
            return await script(init, s)
    return asyncio.run(go())


async def request(session, method, **p):
    return await session.send_request(Raw(method=method, params=p), ANY)


def auto(tmp_path, *more):
    return ["--auto", tmp_path / "work", "--min-runs", "2", *more]


def state(tmp_path) -> dict:
    return json.loads((tmp_path / "work" / "state.json").read_text())


def traces(tmp_path) -> list[str]:
    return sorted(p.name for p in (tmp_path / "work" / "traces").iterdir())


# -- 1. the client loads the skill -------------------------------------------

def test_a_client_that_loads_skills_finds_reads_and_verifies_it(tmp_path):
    yaml = pytest.importorskip("yaml")

    async def script(c, s):
        caps = c.server_capabilities
        assert caps.resources is not None and caps.extensions == {skill.EXTENSION: {}}
        listing = await request(s, "skills/list")
        [entry] = listing["skills"]
        assert listing["resultType"] == "complete" and listing["ttlMs"] > 0 and listing["cacheScope"] == "public"
        assert "nextCursor" not in listing
        # Loading by URI alone must work too, and return the same entry.
        got = await request(s, "skills/get", uri=entry["uri"])
        assert got["skill"] == entry and got["resultType"] == "complete" and "ttlMs" in got and "cacheScope" in got
        # The host's loading path: read, then verify against the retained entry.
        [manifest] = entry["resources"]
        read = await c.read_resource(manifest["uri"])
        [content] = read.contents
        raw = content.text.encode("utf-8")
        assert content.mime_type == "text/markdown" and len(raw) == manifest["size"]
        assert "sha256:" + hashlib.sha256(raw).hexdigest() == manifest["digest"]
        front = yaml.safe_load(content.text[4:].split("\n---\n", 1)[0])
        assert front == entry["frontmatter"] and entry["uri"].split("/")[-2] == front["name"]
        # Then the agent works as the skill says.
        for _ in range(3):
            last = (await c.call_tool("volumes", {})).content[0].text
            done = (await c.call_tool("tracetrim_done", {"answer": ANSWER})).content[0].text
        return content.text, last, done

    text, last, done = modern(auto(tmp_path), script)
    assert text == skill.skill_md() and "Organising tool calls" not in text
    assert "\n[trimmed] call_id=" in last and done == "Recorded."
    assert state(tmp_path)["complete_runs"] == 3


def test_the_strategy_is_published_only_when_asked(tmp_path):
    async def script(c, s):
        [entry] = (await request(s, "skills/list"))["skills"]
        return entry, (await c.read_resource(entry["uri"])).contents[0].text

    entry, text = modern(auto(tmp_path, "--skill-strategy"), script)
    assert text == skill.skill_md(strategy=True) and "### Know when to stop" in text
    assert entry == skill.entry(strategy=True) and entry["frontmatter"]["metadata"]["strategy"] == "on"
    assert entry["resources"][0]["digest"] != skill.entry()["resources"][0]["digest"]


def test_unknown_skills_resources_and_cursors_are_invalid_params(tmp_path):
    async def script(c, s):
        codes = []
        for method, p in (("skills/get", {"uri": "skill://other/SKILL.md"}),
                          ("resources/read", {"uri": "skill://tracetrim/references/none.md"}),
                          ("skills/list", {"cursor": "x"}), ("skills/get", {})):
            with pytest.raises(MCPError) as e:
                await request(s, method, **p)
            codes.append(e.value.error.code)
        return codes

    assert modern(auto(tmp_path), script) == [types.INVALID_PARAMS] * 4


def test_with_a_fixed_config_the_skill_leaves_tracetrim_done_out(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"format": "tracetrim/config@1", "tools": {}}))

    async def script(c, s):
        names = [t.name for t in (await c.list_tools()).tools]
        [entry] = (await request(s, "skills/list"))["skills"]
        return names, (await c.read_resource(entry["uri"])).contents[0].text

    names, text = modern(["--config", tmp_path / "config.json"], script)
    assert "expand" in names and "tracetrim_done" not in names
    assert text == skill.skill_md(done=False) and "tracetrim_done" not in text


@pytest.mark.parametrize("flags", [["--no-skill"], []])
def test_no_skill_is_published_when_switched_off_or_when_nothing_is_trimmed(tmp_path, flags):
    async def script(c, s):
        assert c.server_capabilities.resources is None and not c.server_capabilities.extensions
        assert c.instructions is None
        with pytest.raises(MCPError) as e:
            await request(s, "skills/list")
        return e.value.error.code, [t.name for t in (await c.list_tools()).tools]

    code, names = modern((auto(tmp_path) if flags else []) + flags, script)
    assert code == types.METHOD_NOT_FOUND
    assert ("tracetrim_done" in names) is bool(flags)          # the tools do not depend on the skill


# -- 2. the client sees the resource but has no way to load a skill -------------

def test_a_client_without_skill_support_sees_a_resource_and_the_tools_are_enough(tmp_path):
    async def script(init, s):
        # This client did not declare the extension, so the handshake does not announce it;
        # the server's instructions are then the only pointer to the skill.
        assert init.capabilities.resources is not None and not init.capabilities.extensions
        assert init.instructions == skill.pointer() and skill.URI in init.instructions
        assert init.server_info.version == VERSION
        [res] = (await s.list_resources()).resources
        tools = {t.name: t.description for t in (await s.list_tools()).tools}
        # The client never reads the skill: the descriptions alone carry the contract.
        for _ in range(3):
            last = (await s.call_tool("volumes", {})).content[0].text
            await s.call_tool("tracetrim_done", {"answer": ANSWER})
        cid = last.rsplit("call_id=", 1)[1].split()[0]
        back = (await s.call_tool("expand", {"call_id": cid, "fields": ["items[].note_0"]})).content[0].text
        return res, tools, back

    res, tools, back = handshake(auto(tmp_path), script)
    front = skill.frontmatter()
    assert (res.uri, res.name, res.description, res.mime_type) == (skill.URI, "tracetrim", front["description"], "text/markdown")
    assert "once" in tools["tracetrim_done"] and "[trimmed]" in tools["expand"]
    assert json.loads(back) == {"items": [{"note_0": "internal remark number 0"}]}
    assert state(tmp_path)["complete_runs"] == 3 and state(tmp_path)["expanded"] == {"volumes": 1}


def test_reading_the_skill_as_a_plain_resource_is_not_part_of_a_run(tmp_path):
    async def script(init, s):
        text = (await s.read_resource(skill.URI)).contents[0].text
        await s.call_tool("volumes", {})
        await s.call_tool("tracetrim_done", {"answer": ANSWER})
        return text

    assert handshake(auto(tmp_path), script) == skill.skill_md()
    run = [json.loads(line) for line in (tmp_path / "work" / "traces" / "run-0001.jsonl").open()]
    assert [(e["kind"], e.get("tool")) for e in run] == [("call", "volumes"), ("message", None)]


def raw_handshake(flags, client_capabilities, then=()):
    """Speak JSON-RPC over stdio without the SDK: exactly what a host sends."""
    p = params(*flags)
    proc = subprocess.Popen([p.command, *p.args], cwd=p.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)

    def ask(message):
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline()) if "id" in message else None

    try:
        init = ask({"method": "initialize", "jsonrpc": "2.0", "id": 0, "params": {
            "protocolVersion": "2025-11-25", "capabilities": client_capabilities,
            "clientInfo": {"name": "inspector-cli", "version": "2.13.0"}}})
        ask({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return init["result"], [ask({"jsonrpc": "2.0", "id": n, **m}) for n, m in enumerate(then, 1)]
    finally:
        proc.stdin.close()
        proc.wait(timeout=30)


def test_the_extension_is_announced_in_the_initialize_handshake_to_a_client_that_declares_it(tmp_path):
    # The handshake MCP Inspector CLI 2.13.0 sends (recorded on 2026-11-11): protocol 2025-11-25,
    # extensions negotiated in `initialize`. The proxy answered without `extensions`, and
    # Inspector reported `skills_unsupported`.
    declared = {"roots": {"listChanged": True},
                "extensions": {"io.modelcontextprotocol/tasks": {}, "io.modelcontextprotocol/skills": {}}}
    init, [listing, got] = raw_handshake(auto(tmp_path), declared, then=[
        {"method": "skills/list", "params": {}}, {"method": "skills/get", "params": {"uri": skill.URI}}])
    assert init["protocolVersion"] == "2025-11-25"
    assert init["capabilities"]["extensions"] == {"io.modelcontextprotocol/skills": {}}      # ours, not `tasks`
    assert "resources" in init["capabilities"] and "tools" in init["capabilities"]
    assert init["serverInfo"] == {"name": "tracetrim-proxy", "version": VERSION} and VERSION
    assert init["instructions"] == skill.pointer()
    assert listing["result"]["skills"] == [skill.entry()] and got["result"]["skill"] == skill.entry()


@pytest.mark.parametrize("flags, capabilities", [([], {"roots": {"listChanged": True}}),
                                                 (["--no-skill"], {"extensions": {"io.modelcontextprotocol/skills": {}}})])
def test_the_handshake_does_not_announce_it_unasked_or_switched_off(tmp_path, flags, capabilities):
    init, _ = raw_handshake(auto(tmp_path, *flags), capabilities)
    assert "extensions" not in init["capabilities"]
    assert ("instructions" in init) is not flags and ("resources" in init["capabilities"]) is not flags


# -- 3. the session ends without tracetrim_done ---------------------------------

def test_a_session_that_loaded_the_skill_and_never_closed_is_telemetry_only(tmp_path):
    async def script(c, s):
        await c.read_resource(skill.URI)
        await c.call_tool("volumes", {})
        await c.call_tool("volumes", {})

    modern(auto(tmp_path), script)
    assert traces(tmp_path) == ["run-0001-noanswer.jsonl"]
    assert state(tmp_path)["runs"] == 1 and state(tmp_path)["complete_runs"] == 0
    config = json.loads((tmp_path / "work" / "report.config.json").read_text())
    assert config["tools"] == {} and config["auto"]["applied"] is False


# -- closing, robust to duplicates and retries -----------------------------------

def test_a_closing_without_an_answer_is_refused_and_the_run_stays_open(tmp_path):
    async def script(init, s):
        await s.call_tool("volumes", {})
        bad = [await s.call_tool("tracetrim_done", a) for a in ({}, {"answer": "  "}, {"answer": None})]
        good = await s.call_tool("tracetrim_done", {"answer": ANSWER})
        return bad, good

    bad, good = handshake(auto(tmp_path), script)
    assert all(r.is_error and r.content[0].text.startswith("Error: tracetrim_done needs") for r in bad)
    assert not good.is_error and good.content[0].text == "Recorded."
    # One complete run, with the call made before the refused closings.
    assert traces(tmp_path) == ["run-0001.jsonl"] and state(tmp_path)["complete_runs"] == 1
    # The report counts them, so the phenomenon can be seen.
    report = json.loads((tmp_path / "work" / "report.json").read_text())
    assert state(tmp_path)["refused_closings"] == 3 and report["auto"]["refused_closings"] == 3
    assert "closings refused for lack of an answer" in (tmp_path / "work" / "report.html").read_text()


def test_after_a_refused_closing_the_next_task_lands_in_the_same_run(tmp_path):
    # The agent does not try again. Nothing closes the run until the idle pause
    # or the end of the session: here the next task starts first, and the two
    # are recorded as ONE run, closed by the second task's answer.
    async def script(init, s):
        await s.call_tool("volumes", {"task": "first"})
        refused = await s.call_tool("tracetrim_done", {"answer": ""})
        await s.call_tool("volumes", {"task": "second"})
        await s.call_tool("tracetrim_done", {"answer": ANSWER})
        return refused

    assert handshake(auto(tmp_path), script).is_error
    assert traces(tmp_path) == ["run-0001.jsonl"]
    run = [json.loads(line) for line in (tmp_path / "work" / "traces" / "run-0001.jsonl").open()]
    assert [e.get("arguments") for e in run] == [{"task": "first"}, {"task": "second"}, None]
    assert state(tmp_path) | {"runs": 1, "complete_runs": 1, "refused_closings": 1} == state(tmp_path)


def test_an_answer_followed_by_more_calls_makes_two_runs_and_is_counted(tmp_path):
    # tracetrim_done too early, or simply the next task: the proxy cannot tell,
    # so it does not merge the runs and the report counts the case.
    async def script(init, s):
        await s.call_tool("volumes", {})
        await s.call_tool("tracetrim_done", {"answer": ANSWER})
        await s.call_tool("tracetrim_done", {"answer": ANSWER})         # a repeat: not counted
        await s.call_tool("volumes", {})
        await s.call_tool("volumes", {})                                # same run: counted once
        await s.call_tool("tracetrim_done", {"answer": ANSWER})

    handshake(auto(tmp_path), script)
    assert traces(tmp_path) == ["run-0001.jsonl", "run-0002.jsonl"]
    report = json.loads((tmp_path / "work" / "report.json").read_text())
    assert state(tmp_path)["answered_runs_followed_by_calls"] == 1
    assert report["auto"]["answered_runs_followed_by_calls"] == 1 and report["auto"]["refused_closings"] == 0
    page = (tmp_path / "work" / "report.html").read_text()
    assert "followed by calls within the idle pause" in page and "closings refused" not in page


def test_duplicate_and_concurrent_closings_record_one_run(tmp_path):
    async def script(init, s):
        await s.call_tool("volumes", {})
        together = await asyncio.gather(*(s.call_tool("tracetrim_done", {"answer": ANSWER}) for _ in range(4)))
        later = await s.call_tool("tracetrim_done", {"answer": ANSWER + " (retried)"})
        tools_after = [t.name for t in (await s.list_tools()).tools]
        return [r.content[0].text for r in (*together, later)], tools_after

    replies, tools_after = handshake(auto(tmp_path), script)
    assert replies == ["Recorded."] * 5                         # the same reply, and it asks for nothing
    assert traces(tmp_path) == ["run-0001.jsonl"] and state(tmp_path) | {"runs": 1, "complete_runs": 1} == state(tmp_path)
    run = [json.loads(line) for line in (tmp_path / "work" / "traces" / "run-0001.jsonl").open()]
    assert [e["kind"] for e in run] == ["call", "message"] and run[1]["text"] == ANSWER
    assert tools_after == ["volumes", "expand", "tracetrim_done"]


def test_a_structured_answer_is_kept_as_json_text(tmp_path):
    async def script(init, s):
        await s.call_tool("volumes", {})
        return await s.call_tool("tracetrim_done", {"answer": {"keyword": "spa weekend", "volume": 1600}})

    assert handshake(auto(tmp_path), script).content[0].text == "Recorded."
    run = [json.loads(line) for line in (tmp_path / "work" / "traces" / "run-0001.jsonl").open()]
    assert json.loads(run[1]["text"]) == {"keyword": "spa weekend", "volume": 1600}
