# tracetrim in a LangGraph agent

`tracetrim.langgraph.TraceTrim` puts tracetrim's automatic mode around LangGraph's `ToolNode`, the node that runs
the tools of a graph. The graph, the tools and the model stay as they are.

```bash
pip install "tracetrim[langgraph,measure]"
```

```python
from langgraph.prebuilt import ToolNode
from tracetrim.langgraph import TraceTrim

tt = TraceTrim("tracetrim-data/", min_runs=5)        # the options of tracetrim.auto.AutoTrim
tools = my_tools + tt.tools                          # adds expand and tracetrim_done
llm = llm.bind_tools(tools)                          # and tt.instructions in the system prompt
tools_node = ToolNode(tools, wrap_tool_call=tt.wrap_tool_call, awrap_tool_call=tt.awrap_tool_call)
```

A complete graph that runs with no model and no key: [examples/langgraph_agent.py](../examples/langgraph_agent.py).

## What happens

- **Your tools** run as before; their text results go through tracetrim. During the first `min_runs`
  complete runs the model receives them whole, as compact JSON; after that, trimmed, with the
  `[trimmed] call_id=… hidden: …` line. A tool error (with `handle_tool_errors`) goes back as the tool sent it,
  recorded and never trimmed; results that are not text pass untouched.
- **`expand`** answers from the original held back for that call: the tool is not called again. A `call_id` that
  is not the conversation's own, or has been evicted (the last 256 are kept), gets an error that says so.
- **`tracetrim_done`** closes the run with the model's final answer; tracetrim learns from it. The skill in
  `tt.instructions` tells the model to call it, and, since 1.4, to check hidden fields before saying that
  something is missing. Without an answer the closing is refused and the run stays open.
- **Conversations.** Each `thread_id` in the graph's config has its own run: calls of two conversations never end
  up in one trace, and a conversation can expand only its own results (an error never names another
  conversation's call ids). The learned policy is shared. Calls without a `thread_id` share one run.
- **Closings and parallel calls.** A closing waits for the calls of its conversation still running (for example a
  tool called in the same AI message as `tracetrim_done`), and later calls wait for the closing. After a closing
  the conversation starts a new run; an `expand` of an older result is evidence of the run it came from. Trimming,
  recovering, closing and relearning are serialised, so a result is never trimmed half with an old policy.
- **A conversation resumed later.** With `TraceTrim(..., idle_seconds=600)`, a conversation that resumes after that
  pause starts a new run, and the calls of the old one are kept for telemetry, as the proxy does; an `expand` counts
  as activity. A `tracetrim_done` (or `tt.close`) that comes after the pause finds the old run already kept for
  telemetry, so its answer records nothing, as in the proxy. Conversations that never come back are closed the same
  way, in the background, when a call to one of your tools (in any conversation) finds them idle, with one
  relearning for all of them, and then forgotten: for servers with many conversations, set `idle_seconds`. Without
  it, close runs with `tracetrim_done` or `tt.close`: until then (or `tt.flush`) their calls stay in memory, and a
  small entry per conversation is kept until the process ends. If an idle run cannot be written (a disk error), its
  calls go on in it, with a warning, and the pause starts again.
- **`tracetrim_done` on its own.** A closing in the same AI message as other tool calls is refused with an error
  that asks the model to call it alone, after reading the other results: those calls might not have started yet.
- **Turns.** The calls of one AI message carry the same turn, and each later AI message of the run a new one, so
  parallel calls do not count as using each other's results. If your state keeps messages under another key, pass
  it: `TraceTrim(..., messages_key="history")`.
- **Errors.** A closing that fails (a disk error) answers the model with an error asking it to call
  `tracetrim_done` again; a failure while trimming gives the model the whole result. A user's tool named `expand`
  or `tracetrim_done` is refused with an error: rename it.

## Closing from code

If your graph ends without the model calling `tracetrim_done`, close the run yourself:

```python
tt.close(thread_id, final_answer)   # or None: kept for telemetry only, not learned from
tt.flush()                          # on shutdown: every open run kept for telemetry, then one relearning
await tt.aclose(thread_id, answer)  # the same, off the event loop, in async apps
await tt.aflush()
```

## What it does not do

It does not change which tools the model sees, and it does not wrap LangChain's `create_agent` middleware (the
`ToolNode` path covers agents built with LangGraph). The same limits as the rest of tracetrim apply: it learns from
*visible* reuse, so a field used only to reason can be removed (a `status` the model filters on but never
writes); `expand` brings it back. To stop paying for that `expand` in every run, turn promotion on: with
`TraceTrim(..., adaptive=Adaptive(promote=True))` (`from tracetrim.evidence import Adaptive`), a field the model
asks for by name in two runs is kept from then on. See the paper, Study 4,
for what that means for answer quality.
