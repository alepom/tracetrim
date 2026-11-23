"""tracetrim inside a LangGraph graph (1.5): tracetrim.langgraph.TraceTrim around ToolNode. No model, no spend:
each step is an AI message with fixed tool calls, run through a real compiled graph."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langgraph.graph import END, START, MessagesState, StateGraph  # noqa: E402
from langgraph.prebuilt import ToolNode  # noqa: E402

from tracetrim.langgraph import TraceTrim  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NOISE = {f"note_{i}": f"internal remark number {i} about the record" for i in range(12)}


@tool
def search_volume(keyword: str) -> str:
    """Search volumes for a keyword."""
    return json.dumps({"items": [dict(keyword=keyword, search_volume=1600, **NOISE)]})


@tool
def broken(keyword: str) -> str:
    """A tool that fails."""
    raise ValueError(f"upstream refused {keyword}")


def graph_for(tt, **node_options):
    g = StateGraph(MessagesState)
    g.add_node("tools", ToolNode([search_volume, broken, *tt.tools], wrap_tool_call=tt.wrap_tool_call,
                                 awrap_tool_call=tt.awrap_tool_call, **node_options))
    g.add_edge(START, "tools")
    g.add_edge("tools", END)
    return g.compile()


def message(thread, calls):
    return AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": f"{thread}-{n}-{i}-{id(a)}"}
                                             for i, (n, a) in enumerate(calls)])


def step(graph, thread, calls):
    out = graph.invoke({"messages": [message(thread, calls)]}, config={"configurable": {"thread_id": thread}})
    return out["messages"][1:]


def traces(workdir):
    return {p.name: [json.loads(line) for line in p.open()] for p in sorted((workdir / "traces").glob("run-*.jsonl"))}


def learn(graph, threads=("t1", "t2")):
    for t in threads:
        step(graph, t, [("search_volume", {"keyword": "spa weekend"})])
        step(graph, t, [("tracetrim_done", {"answer": "spa weekend has 1600 searches"})])


def test_results_are_trimmed_after_learning_and_expand_gives_the_original(tmp_path):
    tt = TraceTrim(tmp_path, min_runs=2)
    graph = graph_for(tt)
    learn(graph)
    [out] = step(graph, "t3", [("search_volume", {"keyword": "thermal baths"})])
    body, line = out.content.split("\n[trimmed] ")
    assert json.loads(body) == {"items": [{"keyword": "thermal baths", "search_volume": 1600}]}
    cid = line.split()[0].removeprefix("call_id=")
    [back] = step(graph, "t3", [("expand", {"call_id": cid, "fields": ["items[].note_0"]})])
    assert json.loads(back.content) == {"items": [{"note_0": NOISE["note_0"]}]}


def test_two_threads_interleaved_and_closed_in_reverse_order_keep_their_own_runs(tmp_path):
    tt = TraceTrim(tmp_path, min_runs=5)
    graph = graph_for(tt)
    step(graph, "A", [("search_volume", {"keyword": "from A"})])
    step(graph, "B", [("search_volume", {"keyword": "from B"})])
    [done_b] = step(graph, "B", [("tracetrim_done", {"answer": "answer of B"})])
    [done_a] = step(graph, "A", [("tracetrim_done", {"answer": "answer of A"})])
    assert done_a.content == done_b.content == "Recorded."
    got = [[(e.get("arguments"), e.get("text")) for e in run] for run in traces(tmp_path).values()]
    assert got == [[({"keyword": "from B"}, None), (None, "answer of B")],
                   [({"keyword": "from A"}, None), (None, "answer of A")]]


def test_a_thread_cannot_expand_another_threads_result(tmp_path):
    tt = TraceTrim(tmp_path, min_runs=2)
    graph = graph_for(tt)
    learn(graph)
    [out] = step(graph, "A", [("search_volume", {"keyword": "x"})])
    cid = out.content.split("call_id=")[1].split()[0]
    [other] = step(graph, "B", [("expand", {"call_id": cid, "fields": ["*"]})])
    assert other.content.startswith("Error: unknown call_id") and NOISE["note_0"] not in other.content


def test_errors_pass_untouched_and_are_recorded(tmp_path):
    # ToolNode turns a tool's exception into an error message only when asked to (handle_tool_errors);
    # without it the exception goes through tracetrim untouched, as LangGraph raises it.
    tt = TraceTrim(tmp_path, min_runs=2)
    with pytest.raises(ValueError, match="upstream refused"):
        step(graph_for(tt), "A", [("broken", {"keyword": "x"})])
    graph = graph_for(tt, handle_tool_errors=True)
    [err] = step(graph, "A", [("broken", {"keyword": "x"})])
    assert err.status == "error" and "upstream refused x" in err.content
    step(graph, "A", [("tracetrim_done", {"answer": "nothing"})])
    [run] = traces(tmp_path).values()
    assert run[0]["tool"] == "broken" and "upstream refused x" in run[0]["output"]


def test_a_closing_without_an_answer_is_refused_and_the_run_stays_open(tmp_path):
    tt = TraceTrim(tmp_path, min_runs=2)
    graph = graph_for(tt)
    step(graph, "A", [("search_volume", {"keyword": "x"})])
    [refused] = step(graph, "A", [("tracetrim_done", {"answer": "  "})])
    assert refused.status == "error" and not traces(tmp_path)
    step(graph, "A", [("tracetrim_done", {"answer": "now"})])
    assert len(traces(tmp_path)) == 1


def test_calls_of_one_ai_message_share_a_turn(tmp_path):
    tt = TraceTrim(tmp_path, min_runs=2)
    graph = graph_for(tt)
    step(graph, "A", [("search_volume", {"keyword": "a"}), ("search_volume", {"keyword": "b"})])
    step(graph, "A", [("tracetrim_done", {"answer": "a and b"})])
    [run] = traces(tmp_path).values()
    calls = [e for e in run if e["kind"] == "call"]
    assert len(calls) == 2 and calls[0]["turn"] == calls[1]["turn"]


def test_the_async_path_does_the_same(tmp_path):
    tt = TraceTrim(tmp_path, min_runs=2)
    graph = graph_for(tt)

    async def astep(thread, calls):
        out = await graph.ainvoke({"messages": [message(thread, calls)]},
                                  config={"configurable": {"thread_id": thread}})
        return out["messages"][1:]

    async def go():
        for t in ("t1", "t2"):
            await astep(t, [("search_volume", {"keyword": "spa weekend"})])
            await astep(t, [("tracetrim_done", {"answer": "spa weekend has 1600 searches"})])
        return await astep("t3", [("search_volume", {"keyword": "z"})])

    [out] = asyncio.run(go())
    assert "\n[trimmed] call_id=" in out.content


def test_close_and_flush_from_code(tmp_path):
    tt = TraceTrim(tmp_path, min_runs=2)
    graph = graph_for(tt)
    step(graph, "A", [("search_volume", {"keyword": "a"})])
    step(graph, "B", [("search_volume", {"keyword": "b"})])
    tt.close("A", "closed from code")
    tt.flush()
    names = list(traces(tmp_path))
    assert names == ["run-0001.jsonl", "run-0002-noanswer.jsonl"]
    assert traces(tmp_path)["run-0001.jsonl"][-1]["text"] == "closed from code"


def test_the_tools_and_instructions_offered(tmp_path):
    tt = TraceTrim(tmp_path)
    assert [t.name for t in tt.tools] == ["expand", "tracetrim_done"]
    assert "tracetrim_done" in tt.instructions and "expand" in tt.instructions


def test_tracetrim_does_not_import_langgraph():
    code = "import sys, tracetrim, tracetrim.auto, tracetrim.proxy; print('langgraph' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT,
                         env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert out.stdout.strip() == "False", out.stderr


# -- the cases of the independent review (REPORT-16a, revision) ---------------------------------------------------
import time  # noqa: E402

from langchain_core.tools import StructuredTool  # noqa: E402


@tool
def slow(keyword: str) -> str:
    """A slow tool."""
    time.sleep(0.4)
    return json.dumps({"items": [dict(keyword=keyword, search_volume=10, **NOISE)]})


def graph_with(tt, extra=(), **node_options):
    g = StateGraph(MessagesState)
    g.add_node("tools", ToolNode([search_volume, broken, slow, *extra, *tt.tools], wrap_tool_call=tt.wrap_tool_call,
                                 awrap_tool_call=tt.awrap_tool_call, **node_options))
    g.add_edge(START, "tools")
    g.add_edge("tools", END)
    return g.compile()


def test_a_closing_in_the_same_message_as_other_calls_is_refused(tmp_path):
    # Its sibling calls may not have started: the closing would leave them to the next task (second review, 3).
    tt = TraceTrim(tmp_path, min_runs=5)
    graph = graph_with(tt)
    for order in ([("slow", {"keyword": "s"}), ("tracetrim_done", {"answer": "x"})],
                  [("tracetrim_done", {"answer": "x"}), ("slow", {"keyword": "s"})]):
        out = {m.name: m for m in step(graph, "A", order)}
        assert out["tracetrim_done"].status == "error" and "on its own" in out["tracetrim_done"].content
    assert not traces(tmp_path)
    step(graph, "A", [("tracetrim_done", {"answer": "the slow ones"})])
    [run] = traces(tmp_path).values()
    assert [e["kind"] for e in run] == ["call", "call", "message"]


def test_a_closing_waits_for_calls_of_its_thread_still_running(tmp_path):
    import threading as th
    tt = TraceTrim(tmp_path, min_runs=5)
    graph = graph_with(tt)
    worker = th.Thread(target=step, args=(graph, "A", [("slow", {"keyword": "s"})]))
    worker.start()
    time.sleep(0.1)
    tt.close("A", "closed while slow ran")
    worker.join()
    [run] = traces(tmp_path).values()
    assert [e["kind"] for e in run] == ["call", "message"] and run[0]["tool"] == "slow"


def test_async_closings_of_many_threads_do_not_exhaust_the_worker_pool(tmp_path):
    # Second review, 1: waits held worker threads; with 8 conversations on a 2-worker pool it blocked for ever.
    import concurrent.futures
    tt = TraceTrim(tmp_path, min_runs=50)
    graph = graph_with(tt)

    async def one(n):
        cfg = {"configurable": {"thread_id": f"t{n}"}}
        await graph.ainvoke({"messages": [message(f"t{n}", [("slow", {"keyword": str(n)})])]}, config=cfg)
        await graph.ainvoke({"messages": [message(f"t{n}", [("tracetrim_done", {"answer": f"done {n}"})])]}, config=cfg)

    async def go():
        asyncio.get_running_loop().set_default_executor(concurrent.futures.ThreadPoolExecutor(2))
        await asyncio.wait_for(asyncio.gather(*(one(n) for n in range(8))), 30)

    asyncio.run(go())
    assert len(traces(tmp_path)) == 8


def test_a_cancelled_async_call_does_not_block_the_thread(tmp_path):
    # Second review, 2: a call cancelled while entering left its count up, and every later closing hung.
    tt = TraceTrim(tmp_path, min_runs=50)
    graph = graph_with(tt)

    async def go():
        cfg = {"configurable": {"thread_id": "A"}}
        task = asyncio.ensure_future(graph.ainvoke({"messages": [message("A", [("slow", {"keyword": "s"})])]},
                                                   config=cfg))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.wait_for(tt.aclose("A", None), 5)

    asyncio.run(go())
    assert tt._threads["A"].active == 0


def test_after_a_closing_the_thread_starts_a_new_run_and_a_late_expand_goes_to_the_old_one(tmp_path):
    tt = TraceTrim(tmp_path, min_runs=2)
    graph = graph_with(tt)
    learn(graph)
    [out] = step(graph, "A", [("search_volume", {"keyword": "x"})])
    cid = out.content.split("call_id=")[1].split()[0]
    step(graph, "A", [("tracetrim_done", {"answer": "x has 1600"})])
    step(graph, "A", [("search_volume", {"keyword": "y"})])
    [back] = step(graph, "A", [("expand", {"call_id": cid, "fields": ["items[].note_0"]})])
    assert NOISE["note_0"] in back.content
    open_run = tt._threads["A"].run
    assert len(open_run.events) == 1 and not [n for n in open_run.notes if n.get("kind") == "expand"]


def test_an_unknown_call_id_names_no_call_id_of_other_threads(tmp_path):
    tt = TraceTrim(tmp_path, min_runs=2)
    graph = graph_with(tt)
    learn(graph)
    [out] = step(graph, "C", [("search_volume", {"keyword": "x"})])
    cid = out.content.split("call_id=")[1].split()[0]
    [err] = step(graph, "D", [("expand", {"call_id": "c00000000", "fields": ["*"]})])
    assert err.content.startswith("Error: unknown call_id") and cid not in err.content


def test_ai_messages_in_turn_get_increasing_turns(tmp_path):
    tt = TraceTrim(tmp_path, min_runs=2)
    graph = graph_with(tt)
    first = message("A", [("search_volume", {"keyword": "a"})])
    out = graph.invoke({"messages": [first]}, config={"configurable": {"thread_id": "A"}})
    second = message("A", [("search_volume", {"keyword": "b"})])
    graph.invoke({"messages": out["messages"] + [second]}, config={"configurable": {"thread_id": "A"}})
    step(graph, "A", [("tracetrim_done", {"answer": "a and b"})])
    [run] = traces(tmp_path).values()
    turns = [e["turn"] for e in run if e["kind"] == "call"]
    assert turns[0] < turns[1]


def test_a_failed_closing_is_an_error_the_model_can_retry(tmp_path, monkeypatch):
    tt = TraceTrim(tmp_path, min_runs=2)
    graph = graph_with(tt)
    step(graph, "A", [("search_volume", {"keyword": "x"})])
    real = tt.auto.engine.done
    calls = []

    def failing(answer):
        calls.append(answer)
        if len(calls) <= 2:                       # the closing tries once more at once: both fail
            raise OSError("disk full")
        return real(answer)

    monkeypatch.setattr(tt.auto.engine, "done", failing)
    [first] = step(graph, "A", [("tracetrim_done", {"answer": "x"})])
    assert first.status == "error" and "again" in first.content and not traces(tmp_path)
    [second] = step(graph, "A", [("tracetrim_done", {"answer": "x"})])
    assert second.content == "Recorded." and len(traces(tmp_path)) == 1


def test_an_idle_thread_resumes_in_a_new_run(tmp_path):
    tt = TraceTrim(tmp_path, min_runs=5, idle_seconds=0.05)
    graph = graph_with(tt)
    step(graph, "A", [("search_volume", {"keyword": "old task"})])
    time.sleep(0.1)
    step(graph, "A", [("search_volume", {"keyword": "new task"})])
    step(graph, "A", [("tracetrim_done", {"answer": "new task done"})])
    got = {name: [e.get("arguments", {}).get("keyword") for e in run if e["kind"] == "call"]
           for name, run in traces(tmp_path).items()}
    assert got == {"run-0001-noanswer.jsonl": ["old task"], "run-0002.jsonl": ["new task"]}


def test_a_users_tool_with_tracetrims_name_is_refused(tmp_path):
    tt = TraceTrim(tmp_path)
    impostor = StructuredTool.from_function(lambda call_id, fields: "mine", name="expand", description="not ours")
    g = StateGraph(MessagesState)
    g.add_node("tools", ToolNode([impostor, tt.tools[1]], wrap_tool_call=tt.wrap_tool_call))
    g.add_edge(START, "tools")
    g.add_edge("tools", END)
    with pytest.raises(ValueError, match="collides"):
        step(g.compile(), "A", [("expand", {"call_id": "c1", "fields": ["*"]})])


def test_flush_keeps_every_open_run_and_skips_empty_ones(tmp_path):
    tt = TraceTrim(tmp_path, min_runs=5)
    graph = graph_with(tt)
    step(graph, "A", [("search_volume", {"keyword": "a"})])
    step(graph, "B", [("search_volume", {"keyword": "b"})])
    tt._thread("empty")
    tt.flush()
    assert sorted(traces(tmp_path)) == ["run-0001-noanswer.jsonl", "run-0002-noanswer.jsonl"]


def test_a_closing_does_not_modify_the_ai_message_in_the_state(tmp_path):
    # Third review, 2: the answer was normalised inside the AI message, altering the conversation's history.
    tt = TraceTrim(tmp_path, min_runs=5)
    graph = graph_with(tt)
    step(graph, "A", [("search_volume", {"keyword": "x"})])
    msg = message("A", [("tracetrim_done", {"answer": {"volume": 1600}})])
    out = graph.invoke({"messages": [msg]}, config={"configurable": {"thread_id": "A"}})
    assert out["messages"][0].tool_calls[0]["args"] == {"answer": {"volume": 1600}}
    assert out["messages"][1].content == "Recorded."
    [run] = traces(tmp_path).values()
    assert json.loads(run[-1]["text"]) == {"volume": 1600}


def test_a_refused_closing_with_siblings_is_counted(tmp_path):
    tt = TraceTrim(tmp_path, min_runs=5)
    graph = graph_with(tt)
    step(graph, "A", [("search_volume", {"keyword": "x"}), ("tracetrim_done", {"answer": "x"})])
    assert tt.auto.learner.state["refused_closings"] == 1
