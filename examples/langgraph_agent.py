"""tracetrim in a LangGraph agent, end to end, with no model and no key.

    pip install "tracetrim[langgraph,measure]"
    python examples/langgraph_agent.py

The `model` node below replays fixed tool calls, so the example runs anywhere. With a real model, replace it with
one that calls the model bound to the same tools, and put `tt.instructions` in its system prompt:

    llm = ChatOpenAI(model=...).bind_tools(tools)            # or any LangChain chat model with tool calling
    def model(state): return {"messages": [llm.invoke([SystemMessage(SYSTEM + tt.instructions), *state["messages"]])]}
"""
import json
import tempfile

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from tracetrim.langgraph import TraceTrim

NOISE = {f"note_{i}": f"internal remark number {i} about the record" for i in range(12)}


@tool
def search_volume(keyword: str) -> str:
    """Monthly search volume for a keyword."""
    return json.dumps({"items": [dict(keyword=keyword, search_volume=1600, **NOISE)]})


workdir = tempfile.mkdtemp(prefix="tracetrim-langgraph-")
tt = TraceTrim(workdir, min_runs=2)                       # learn from 2 complete runs, then trim
tools = [search_volume, *tt.tools]                        # your tools, plus expand and tracetrim_done


def model(state: MessagesState):
    """Stand-in for a chat model: one tool call, then tracetrim_done with the answer read from the result."""
    last = state["messages"][-1]
    if isinstance(last, HumanMessage):
        return {"messages": [AIMessage("", tool_calls=[{"name": "search_volume", "args": {"keyword": last.content},
                                                         "id": "call-1"}])]}
    if last.name == "search_volume":
        volume = json.loads(last.content.split("\n[trimmed]")[0])["items"][0]["search_volume"]
        answer = f"{state['messages'][0].content}: {volume} searches a month"
        return {"messages": [AIMessage("", tool_calls=[{"name": "tracetrim_done", "args": {"answer": answer},
                                                         "id": "call-2"}])]}
    return {"messages": [AIMessage("done")]}


graph = StateGraph(MessagesState)
graph.add_node("model", model)
graph.add_node("tools", ToolNode(tools, wrap_tool_call=tt.wrap_tool_call, awrap_tool_call=tt.awrap_tool_call))
graph.add_edge(START, "model")
graph.add_conditional_edges("model", tools_condition)
graph.add_edge("tools", "model")
app = graph.compile()

for n, keyword in enumerate(["spa weekend", "thermal baths", "mountain hotels"], start=1):
    out = app.invoke({"messages": [HumanMessage(keyword)]}, config={"configurable": {"thread_id": f"user-{n}"}})
    result = next(m for m in out["messages"] if getattr(m, "name", None) == "search_volume").content
    print(f"run {n}: the model received {len(result)} characters"
          + (" (trimmed: " + result.split("\n[trimmed] ")[1][:60] + "...)" if "[trimmed]" in result else " (whole)"))
print("report:", tt.report)
