"""The report's section on tool definitions, and the manual suggestion on tools never called (0.6).

The 79 tools are those of the DataForSEO MCP server as counted in 06a (results/v06/definitions.json:
names and tokens per definition, no description): each is rebuilt with a filler description of the
same weight. When the real list is on this machine (out/v06/, git-ignored) it is used as well.
"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tracetrim import definitions as defs
from tracetrim import report as rp
from tracetrim.auto import AutoTrim
from tracetrim.measure import tokenizer

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results" / "v06" / "definitions.json"
REAL = ROOT / "out" / "v06" / "dfs_tools_list.json"
C1 = ["kw_data_google_trends_explore", "kw_data_google_ads_search_volume",
      "dataforseo_labs_google_keyword_suggestions", "dataforseo_labs_google_related_keywords",
      "dataforseo_labs_google_keyword_ideas", "serp_organic_live_advanced",
      "serp_youtube_organic_live_advanced", "dataforseo_labs_google_ranked_keywords",
      "dataforseo_labs_google_relevant_pages", "dataforseo_labs_search_intent",
      "dataforseo_labs_bulk_keyword_difficulty", "on_page_content_parsing"]
count = tokenizer()


def server_79():
    """(tools in MCP form, the tools the agent of C1 called), from the measure of 06a."""
    per_tool = json.loads(RESULTS.read_text())["scenario_all_server_tools"]["per_tool"]
    tools = []
    for x in per_tool:
        t = {"name": x["name"], "description": "", "inputSchema": {"type": "object", "properties": {}}}
        t["description"] = "word " * max(0, x["tokens"] - count(json.dumps(t)))
        tools.append(t)
    return tools, [x["name"] for x in per_tool if x["used"]]


def run_agent(workdir, tools, called, runs, min_runs=5):
    """`runs` complete runs of a function-calling agent that calls `called` once each."""
    tt = AutoTrim(workdir, min_runs=min_runs)
    if tools is not None:
        tt.offered(tools + [{"type": "function", **tt.expand_tool}])
    outputs = []
    for r in range(runs):
        for i, name in enumerate(called):
            outputs.append(tt.output(name, {"q": f"topic {i}"},
                                     json.dumps({"items": [{"keyword": f"kw {r} {i}", "volume": 1000 + i}]})))
        tt.done(f"Plan {r}: kw {r} 0 has 1000 searches.")
    return json.loads((Path(workdir) / "report.json").read_text()), (Path(workdir) / "report.html").read_text(), outputs


def test_the_section_counts_what_is_published_and_what_was_called():
    tools = [{"name": "a", "description": "x " * 50}, {"name": "b", "description": "y"},
             {"name": "expand", "description": "z"}]
    s = defs.summarise(tools, [{"a"}, {"a", "expand"}], count, min_runs=2)
    assert s["tokens_per_turn"] == count(json.dumps(tools, ensure_ascii=False))
    assert s["offered"] == 2 and s["called"] == ["a"] and s["own_tools"] == ["expand"]
    assert s["never_called"] == [{"name": "b", "tokens": count(json.dumps(tools[1]))}]
    assert s["per_run"] is None and s["runs"] == 2
    assert s["suggest"] is False                  # b weighs far less than 5,000 tokens


def test_a_function_calling_name_is_found_where_the_provider_puts_it():
    assert defs.name_of({"type": "function", "function": {"name": "f"}}) == "f"
    assert defs.name_of({"type": "function", "name": "g"}) == "g"


@pytest.mark.parametrize("min_share,min_tokens", [(defs.NEVER_CALLED_MIN_SHARE, defs.NEVER_CALLED_MIN_TOKENS)])
def test_the_thresholds_are_the_documented_ones(min_share, min_tokens):
    assert (min_share, min_tokens, defs.LIST_MAX) == (0.5, 5_000, 20)


def test_with_the_79_tools_and_a_use_like_c1_the_suggestion_appears(tmp_path):
    tools, called = server_79()
    assert len(tools) == 79 and len(called) == 11
    d, page, _ = run_agent(tmp_path / "w", tools, called, runs=5)
    s = d["definitions"]
    assert s["offered"] == 79 and len(s["called"]) == 11 and len(s["never_called"]) == 68
    assert s["never_called_share"] >= 0.5 and s["never_called_tokens"] >= 5_000 and s["suggest"]
    [sugg] = [x for x in d["suggestions"] if x["rule"] == "definitions"]
    assert sugg["level"] == "review" and sugg["action"]["kind"] == "manual"
    listed = [x["name"] for x in sugg["action"]["tools"]]
    heaviest = [x["name"] for x in s["never_called"]][:20]
    assert listed == heaviest and len(listed) == 20          # heaviest first, truncated at 20
    for words in ("does not mean never needed", "Claude Code", "tool_search", "defer_loading",
                  "the first 20 of 68", "hides no tool"):
        assert words in sugg["en"], words
    for words in ("non vuol dire «mai necessario»", "Claude Code", "tool_search", "i primi 20 di 68",
                  "non nasconde nessun tool"):
        assert words in sugg["it"], words
    assert "Tool definitions (a measure)" in page and "Definizioni dei tool (una misura)" in page
    assert "not estimated here" in page
    # A manual action: nothing in the config is about it.
    assert "definitions" not in json.dumps(d["config"])


def test_with_the_12_tools_of_c1_it_does_not(tmp_path):
    tools, called = server_79()
    twelve = [t for t in tools if t["name"] in C1]
    d, page, _ = run_agent(tmp_path / "w", twelve, called, runs=5)
    s = d["definitions"]
    assert s["offered"] == 12 and [x["name"] for x in s["never_called"]] == ["on_page_content_parsing"]
    assert not s["suggest"] and not [x for x in d["suggestions"] if x["rule"] == "definitions"]
    assert "Tool definitions (a measure)" in page                 # the section is there, the suggestion is not


def test_not_before_min_runs_complete_runs(tmp_path):
    tools, called = server_79()
    d, _, _ = run_agent(tmp_path / "w", tools, called, runs=4)
    assert d["definitions"]["runs"] == 4 and not d["definitions"]["suggest"]


def test_without_a_tool_list_nothing_changes(tmp_path):
    """Non-regression: what the model receives is identical with and without the list, and with no list
    the report has no section and no new key."""
    tools, called = server_79()
    d_with, _, out_with = run_agent(tmp_path / "a", tools, called, runs=6)
    d_without, page, out_without = run_agent(tmp_path / "b", None, called, runs=6)
    assert out_with == out_without
    assert "definitions" not in d_without and "Tool definitions" not in page
    assert not (tmp_path / "b" / defs.FILE).exists()
    assert d_with["config"]["tools"] == d_without["config"]["tools"]


def test_the_list_is_written_only_when_it_changes(tmp_path):
    assert defs.record(tmp_path, [{"name": "a"}]) is True
    assert defs.record(tmp_path, [{"name": "a"}]) is False
    assert defs.record(tmp_path, [{"name": "a"}, {"name": "b"}]) is True
    assert defs.load(tmp_path) == [{"name": "a"}, {"name": "b"}]


def test_report_from_traces_takes_the_tool_list_from_a_file(tmp_path):
    tools, called = server_79()
    for r in range(5):
        events = [{"kind": "prompt", "text": "Plan."}]
        events += [{"kind": "call", "tool": n, "arguments": {}, "output": json.dumps({"keyword": f"kw{r}"})}
                   for n in called]
        events.append({"kind": "message", "text": f"kw{r}"})
        (tmp_path / f"run{r}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    listing = tmp_path / "tools.json"
    listing.write_text(json.dumps({"tools": tools}))
    out = tmp_path / "report.html"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    traces = sorted(map(str, tmp_path.glob("run*.jsonl")))
    p = subprocess.run([sys.executable, "-m", "tracetrim.cli", "report", *traces, "-o", str(out), "--tools", str(listing)],
                       cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    assert "tool list: 79 tools read" in p.stdout
    d = json.loads(out.with_suffix(".json").read_text())
    assert d["definitions"]["suggest"] and any(s["rule"] == "definitions" for s in d["suggestions"])
    subprocess.run([sys.executable, "-m", "tracetrim.cli", "report", *traces, "-o", str(tmp_path / "plain.html")],
                   cwd=ROOT, env=env, capture_output=True, check=True)
    assert "definitions" not in json.loads((tmp_path / "plain.json").read_text())


@pytest.mark.skipif(not REAL.exists(), reason="the real tool list is local (out/v06/, git-ignored)")
def test_with_the_real_list_of_the_server_the_weights_are_those_of_the_measure(tmp_path):
    real = json.loads(REAL.read_text())
    _, called = server_79()
    d, _, _ = run_agent(tmp_path / "w", real, called, runs=5)
    measured = {x["name"]: x for x in json.loads(RESULTS.read_text())["scenario_all_server_tools"]["per_tool"]}
    s = d["definitions"]
    assert s["suggest"] and len(s["never_called"]) == 68
    # MCP form as the server lists it (`inputSchema` with `$schema`, `execution`) instead of the function
    # form of the measure: the same 17 tokens more for every tool of this server.
    assert {x["tokens"] - measured[x["name"]]["tokens"] for x in s["never_called"]} == {17}


def test_the_proxy_records_the_list_it_publishes_and_publishes_the_same(tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    server = f"{sys.executable} {ROOT / 'tests' / 'fixtures' / 'meta_server.py'}"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

    def listing(extra):
        params = StdioServerParameters(command=sys.executable, cwd=str(ROOT), env=env, args=[
            "-m", "tracetrim.cli", "proxy", "--upstream-cmd", server, "--quiet", *extra])

        async def go():
            async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
                await s.initialize()
                return [t.model_dump(mode="json", by_alias=True, exclude_none=True)
                        for t in (await s.list_tools()).tools]
        return asyncio.run(go())

    work = tmp_path / "w"
    published = listing(["--auto", str(work)])
    assert defs.load(work) == published
    assert [t["name"] for t in published][-2:] == ["expand", "tracetrim_done"]
    # Without automatic mode there is no work directory, and nothing is written.
    plain = listing([])
    assert [t["name"] for t in plain] == [t["name"] for t in published[:-2]]
