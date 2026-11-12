"""The proxy returns a server's result untouched when it does not trim it, and
keeps its metadata when it does (external review, round 2)."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "meta_server.py"
sys.path.insert(0, str(FIXTURE.parent))
from meta_server import PAYLOAD  # noqa: E402

SERVER = f"{sys.executable} {FIXTURE}"


def call(extra_args):
    params = StdioServerParameters(command=sys.executable, cwd=str(ROOT), args=[
        "-m", "tracetrim.cli", "proxy", "--upstream-cmd", SERVER, "--quiet", *extra_args])

    async def go():
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            return await s.call_tool("volumes", {})
    return asyncio.run(go())


def test_untouched_result_keeps_meta_and_structured_content():
    res = call([])
    assert (res.meta or {}).get("trace_id") == "abc-123"
    assert res.structured_content["items"][0]["note_0"].startswith("internal")


def test_trimmed_result_keeps_meta_and_trims_structured_content(tmp_path):
    # Three runs in which the agent writes only the keyword and its volume.
    for i in range(3):
        events = [{"kind": "prompt", "text": "Give me the search volumes."},
                  {"kind": "call", "tool": "volumes", "arguments": {}, "output": json.dumps(PAYLOAD)},
                  {"kind": "message", "text": "spa weekend has 1600 searches a month."}]
        (tmp_path / f"run{i}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    out = tmp_path / "report.html"
    subprocess.run([sys.executable, "-m", "tracetrim.cli", "report", *map(str, sorted(tmp_path.glob("run*.jsonl"))),
                    "-o", str(out)], cwd=ROOT, check=True, capture_output=True)
    res = call(["--config", str(out.with_suffix(".config.json"))])
    text = res.content[0].text
    assert "\n[trimmed] " in text and "note_0" not in text.split("\n[trimmed] ")[0]
    assert (res.meta or {}).get("trace_id") == "abc-123"
    # The structured copy is trimmed like the text, not passed through whole.
    assert "note_0" not in json.dumps(res.structured_content)


def test_a_result_passed_whole_on_purpose_keeps_its_structured_copy_whole(tmp_path, monkeypatch):
    # Review of 2026-11-12: a sampled result went out with the whole text and a trimmed
    # structuredContent, with no [trimmed] line to recover the difference.
    monkeypatch.setenv("META_INDENT", "2")
    params = StdioServerParameters(command=sys.executable, cwd=str(ROOT), args=[
        "-m", "tracetrim.cli", "proxy", "--upstream-cmd", SERVER, "--quiet", "--auto", str(tmp_path / "w"),
        "--min-runs", "2", "--sample-rate", "1", "--sample-max-share", "100"])
    seen = []

    async def go():
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            for _ in range(4):
                for _ in range(2):
                    res = await s.call_tool("volumes", {})
                    text = res.content[0].text.split("\n[trimmed]")[0]
                    seen.append(("internal remark" in text, "[trimmed]" in res.content[0].text,
                                 "internal remark" in json.dumps(res.structured_content)))
                await s.call_tool("tracetrim_done", {"answer": "spa weekend has 1600 searches a month."})
    asyncio.run(go())
    assert any(t for t, _, _ in seen) and any(line for _, line, _ in seen)    # some whole, some trimmed
    assert all(text == structured for text, _, structured in seen), seen
