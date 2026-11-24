# tracetrim

**Agents read more than they visibly reuse.** tracetrim learns, from your agent's own traces, which
fields of each tool output the agent reuses in what it writes, and helps you remove the rest — with a
telemetry report you can read, a configuration you can review, and an MCP proxy that applies it
in front of any server without changing your agent.

**tracetrim in two minutes** (video, no sound):

https://github.com/user-attachments/assets/ca29bcbb-f702-48b5-887e-14e0ac48ea5f


In short: the first runs are only observed; from then on each tool's output reaches the model without
the fields the agent never reused, and a hidden field is one `expand` away. JSON is also written
compactly from the first call. With MCP, nothing changes in your agent but one entry in its configuration.

## Install

```bash
pip install "tracetrim[measure,proxy]"
pip install "tracetrim[headroom]"   # + Headroom backend (experimental, heavy)
```

Python 3.10+. From GitHub: `pip install "tracetrim[measure,proxy] @ git+https://github.com/alepom/tracetrim"`.
From a clone: `pip install -e ".[measure,proxy,test]"`. To reproduce the
study's numbers, figures and analyses: `pip install -e ".[measure,proxy,test]"` is enough (they need no agent SDK);
to run new agent sessions, also `pip install -r requirements-study.txt` (the versions used, OpenAI SDK included).

## Put it in front of your agent

### With `tracetrim init` (Claude Desktop, Claude Code, Cursor, VS Code)

Your agent already uses one or more MCP servers. `tracetrim init` puts tracetrim in front of them:

```bash
tracetrim init --host cursor            # dry run: shows what would change, writes nothing
tracetrim init --host cursor --write    # backup, servers moved to a work directory, one entry in the host
```

It moves the servers it can stand in front of to `servers.json` in a work directory (readable only by
you), leaves one `tracetrim` entry in the host, and says which servers stay where they are and why. It
never prints a key, and `tracetrim init --undo BACKUP` puts the host's file back byte for byte. After
writing, it starts the new entry once, as the host will, and says whether it answers and how many tools
it lists (it calls none; `--no-check` skips it).
[docs/hosts.md](https://github.com/alepom/tracetrim/blob/master/docs/hosts.md) has the details, and
a table of what was verified host by host. Then restart the host: see *What happens next*, below.

### By hand, for any other host

Copy the `mcpServers` block of your servers — the same JSON
Claude Desktop, Claude Code and Cursor use — into a file:

```json
{"mcpServers": {
  "billing": {"url": "https://billing.example/mcp", "headers": {"Authorization": "Bearer ${BILLING_TOKEN}"}},
  "code":    {"command": "python", "args": ["-m", "my_code_server"], "env": {"API_KEY": "${CODE_KEY}"}}}}
```

Start one tracetrim in front of all of them:

```bash
tracetrim proxy --servers mcp.json --auto tracetrim-data/                       # stdio, for local clients
export TRACETRIM_TOKEN=CHOOSE_A_SECRET                                          # over HTTP: clients must send it
tracetrim proxy --servers mcp.json --auto tracetrim-data/ --http 8799
```

In the agent, **replace** those servers with tracetrim (one entry instead of N; keeping both would
show the agent two copies of every tool), and add one line to its instructions:

```text
Last step, required if the tool tracetrim_done is available: when your final answer is ready,
call tracetrim_done once with the complete answer, before you show it; then show the same answer.
```

The agent sees the same tools with the same names (prefixed with the server name only if two
servers share one), plus `expand` and `tracetrim_done`. The proxy also publishes these instructions
as an Agent Skill over MCP (since 0.4; see [docs/skill.md](https://github.com/alepom/tracetrim/blob/master/docs/skill.md)):
a host that loads MCP skills needs no added line, the others keep it
([which hosts do](https://github.com/alepom/tracetrim/blob/master/docs/hosts.md#what-was-verified-host-by-host)).
Then use the agent as usual.

### HTTP sessions

In automatic mode, the proxy warns on stderr (even with `--quiet`) when a run contains calls from multiple identifiable HTTP sessions.
The report counts affected runs and sessions, counting each session once per affected run.

Add `--isolate-sessions` to `proxy --http PORT --auto WORKDIR` to keep calls, answers and
recoverable originals separate for each HTTP transport session. Each session closes with its own
`tracetrim_done`, on idle expiry (five minutes by default), or on proxy shutdown. The learned
policy is shared across sessions. The live HTTP report includes closed runs. This option applies
only to the automatic HTTP proxy. The function-calling adapter uses its caller's run boundaries.

Requests without a transport session ID use one separate shared context, including for `expand`.
The proxy warns once on stderr when this happens. To separate those clients, distinguish them
upstream of the proxy, for example by routing conversations to separate proxy work directories.

### Hosted agents (OpenAI Playground, Responses API)

There is no `mcpServers` file in the Playground: each MCP server is added in the UI (*Tools → Add →
MCP server*) with a URL and, if needed, a header. Write `mcp.json` from those same values — one
entry per server, URL and header as you typed them — and run tracetrim over HTTP where OpenAI can
reach it (a server of yours, or a tunnel such as ngrok):

```bash
export TRACETRIM_TOKEN=CHOOSE_A_SECRET
tracetrim proxy --servers mcp.json --auto tracetrim-data/ --http 8799 --allowed-host your-tunnel.example
```

Then, in the Playground, switch off the servers tracetrim now stands in for and add one MCP server:
URL `https://your-tunnel.example/mcp`, header `Authorization: Bearer CHOOSE_A_SECRET`. The live
report is at `http://127.0.0.1:8799/report?token=CHOOSE_A_SECRET` on the machine running the proxy.
`docs/demo_playground.sh` does all of this for the demo.

### LangGraph

In a LangGraph agent, tracetrim goes around the `ToolNode`: one line changes, nothing else in the graph.

```python
from tracetrim.langgraph import TraceTrim          # pip install "tracetrim[langgraph,measure]"

tt = TraceTrim("tracetrim-data/", min_runs=5)
tools = my_tools + tt.tools                        # expand and tracetrim_done; tt.instructions in the system prompt
tools_node = ToolNode(tools, wrap_tool_call=tt.wrap_tool_call, awrap_tool_call=tt.awrap_tool_call)
```

Each conversation (`thread_id`) has its own run and recovers only its own results; the learned policy is shared.
Details and a complete graph that runs with no model and no key: [docs/langgraph.md](https://github.com/alepom/tracetrim/blob/master/docs/langgraph.md),
[examples/langgraph_agent.py](https://github.com/alepom/tracetrim/blob/master/examples/langgraph_agent.py).

### Function calling, without MCP

If your agent runs its own tools (function calling), there is no proxy to put in between: the
loop that runs the tools is yours, so tracetrim goes in there, in three places. No
`tracetrim_done` either: your code already has the final answer.

```python
from tracetrim.auto import AutoTrim

tt = AutoTrim("tracetrim-data/", min_runs=5)
tools = my_tools + [{"type": "function", **tt.expand_tool}]      # 1. offer expand

# 2. in the loop, for every tool call the model makes
if call.name == "expand":
    result = tt.expand(call.arguments)
else:
    result = tt.output(call.name, call.arguments, run_my_tool(call.name, call.arguments))

# 3. when the run is over
tt.done(final_answer)
```

`tt.output` records the call and returns what the model should receive: the whole result during the
first runs (as compact JSON), the trimmed one after. If `tt.done` raises (a disk error), call it again
with the same answer before the next call: the run is still open, or its report is still owed. `tt.done` closes the run, updates the report
(`tt.report`, `tracetrim-data/report.html`) and, once `min_runs` runs exist, starts trimming; what
was learned survives restarts. The rules are those of the proxy's automatic mode. Arguments can be
a dict or the JSON string most SDKs hand you; `tt.expand_tool` is a name, a description and a JSON
schema, to adapt to your provider's tool format (`{"type": "function", ...}` for OpenAI, `name` /
`description` / `input_schema` for Anthropic). `tt.instructions` is the text that explains `expand`
to the model, for your system prompt; `AutoTrim(..., strategy=True)` adds optional notes on how to
organise tool calls (off by default; what was measured on it: [docs/skill.md](https://github.com/alepom/tracetrim/blob/master/docs/skill.md)).
`tt.offered(tools)`, optional, takes the list as you send it to the model: the report then shows what the
tool definitions weigh per turn and which tools were never called (the proxy records its own list; for
`tracetrim report`, `--tools FILE`). Nothing the model receives changes, and tracetrim hides no tool:
[docs/definitions.md](https://github.com/alepom/tracetrim/blob/master/docs/definitions.md) says why.

Checked with a real agent on the billing case, function calling, 5 runs with 3 to learn: after learning,
tool outputs went from 33,862 to 18,947 tokens (−44%, fields asked back included), the agent asked for a
hidden field once, and all five tickets passed the keyword test in every run (a check, not a measurement:
`results/checks/auto_check_function.json`, from
`python -m experiments.auto_check --paid --runs 5 --min-runs 3 --mode function`).

## What happens next

Whichever way it is connected:

- **from the first run**, a telemetry report is kept up to date in `tracetrim-data/report.html`
  (over HTTP also live at `/report?token=...`, updating in place): which tools fill the context, which fields
  the agent never uses, with suggestions;
- **during the first runs** (5 by default, `--min-runs`) nothing is removed: the proxy only observes
  (JSON is already written compactly).
  `tracetrim_done` is how it sees the answer — in the study, learning from tool calls alone missed a
  used field in 49% of held-out calls (three agents on real APIs), against 4% with the answer;
- **after that**, it applies the report's automatic actions on its own: the prudent projection, with
  identifying fields kept, transformed values recognised and `expand` on. Everything else stays in the
  report as a manual action for you to review. Since 1.5 the line after a trimmed result also shows the first
  words of the hidden free-text fields (descriptions, memos, notes), so that one that holds the answer does not look
  empty (`--text-preview 0` turns them off). Since 1.1, `--protect-ids record` (off by default) keeps
  ids and links only when they belong to the record itself, and names and titles at any depth: on the
  study's traces it removed 10–13 more points of tool-output tokens on two agents, with no extra miss, and
  live 6–11 more points on three agents, with the same checks passed
  ([docs/measures-v1.md](https://github.com/alepom/tracetrim/blob/master/docs/measures-v1.md));
- **at every closing it checks the answer** (since 1.1): numbers and codes the answer cites that no tool
  output of the run contains are logged and listed in the report, and a MANUAL suggestion is given when
  they appear only where results were filtered. An agent that no longer sees a field can make one up; on
  the study's runs this check flags the identifiers made up that way, and nothing elsewhere;
- **it corrects itself**: a tool whose trimmed outputs the agent expands too often is no longer
  trimmed, and the report says so. Since 0.4, and off unless asked for: `--promote` keeps a single
  field the agent asked back often enough, before giving up on the tool, and `--sample-rate` passes a
  result whole now and then to see whether the agent uses what is removed
  ([docs/adaptive.md](https://github.com/alepom/tracetrim/blob/master/docs/adaptive.md)). Runs without `tracetrim_done` count for telemetry, not for learning.
  One agent at a time: calls that arrive together from several agents end up in the same run. Over MCP the
  proxy cannot see the model's turns, so calls emitted together are compared by arrival order: a later call's
  arguments can count as using an earlier sibling's output. In the study this changed no figure; where turns
  matter, record them (`AutoTrim` takes `turn`, JSONL traces accept a `"turn"` field).

**Closing without waiting** (1.4, off by default): relearning takes longer as runs accumulate (about 5 s on
the billing traces and 13 s on the CI traces at 100 runs), and by default the reply to `tracetrim_done` waits
for it. With `--background-relearn` the proxy replies `Recorded.` as soon as the run is recorded (under 0.02 s at 20, 100
and 200 runs), and relearns in a thread, never on its event loop. Only that reply is earlier: a request that arrives while the relearning
runs (a tool call, an `expand`, another closing, a tool listing) waits for it and is served with the new
policy, and a call already on its way to the upstream server is recorded before the relearning reads the runs.
With one agent at a time, whose requests follow one another, the model receives, and the proxy learns from,
exactly what it would without the option: every file of the work directory ends byte for byte the same. With
several clients the two modes differ by design: without the option, another client's tool call or `expand` is
served at once, with the policy in place, while a closing relearns (closings wait for one another in both
modes). One relearning runs at a time, each report reads one
moment of the work directory (the runs it lists, with their savings, counters and evidence), and shutdown and
the recovery at start relearn in a thread as well. One limit: a client that disappears without cancelling a
call it sent keeps the relearning waiting until the upstream server answers that call
([results/v1/closing_reply.json](https://github.com/alepom/tracetrim/blob/master/results/v1/closing_reply.json)).

Since 1.4, in every mode, a closing writes its files through a journal: a process stopped at any point leaves
all of them or none, and the next start relearns what no report covers yet. A closing that meets a disk error
before its journal (the evidence, the lock, the scan of the traces, the journal itself) fails as a whole, and the
run stays open for the retry (`AutoTrim.done` again, or the agent's `tracetrim_done`). Where no one retries, at
shutdown and when the pause closes a run without `tracetrim_done`, the closing is tried again at once and, if the
evidence still cannot be read, kept without it, as until 1.3: with one or two disk errors the run is never lost, and
never left open for the next task to join. Two
consequences: the work
directory needs file locks (`flock` and POSIX `lockf`; on a file system without them neither the proxy nor
`AutoTrim` starts, and they say so), and a trace the disk cannot read (an I/O or permission error) stops the relearning with that
error, to be tried again at the next closing; until 1.3 it was set aside as corrupt. A trace whose content is
not valid is still set aside.

Everything lives in the work directory (traces, report, config, state), so learning survives restarts;
delete it to start again. In a check with a real agent on the billing case (5 runs, 3 to learn), the
tool outputs of the two runs after learning went from 33,492 to 18,725 tokens (−44%, fields asked back
included), the agent asked for a hidden field once in each, and all five tickets passed the keyword test in every
run: a check, not a measurement (`results/checks/auto_check_mcp.json`) — the measurements are in the
paper.

**Mind the work directory.** To learn, tracetrim keeps the full tool outputs of every run in
`tracetrim-data/traces/`. If your servers return confidential data, treat that directory as you
treat the data: local, not shared, not committed.

## Compare two configurations offline

Run the same recorded task under two recipes before choosing how to trim its outputs.
With the public dataset unpacked at the repository root, compare identifier protection on C4:

```bash
tracetrim compare dataset/traces/C4/A*.jsonl --a protect_ids=all --b protect_ids=record
```

Each side starts with `protect_ids=all,learning=1.1,keep_positions=true,unseen=keep,compact=true,text_preview=40`.
Override one or more settings with comma-separated pairs, for example
`--b protect_ids=record,keep_positions=false`. See `tracetrim compare --help` for accepted values.
Supply distinct runs with the original tool outputs and a final answer. At least two eligible runs
are required. Two to four produce a small-sample warning.

Each side learns from the other runs and filters the held-out run with the prudent projection.
Both sides judge later field reuse with the 1.1 rules. The replay enables recovery for all learned
tools and runs without activation thresholds, promotions or sampling. The table compares served tokens
(including the `[trimmed]` line), calls hiding a visibly reused field, and calls served whole because the
annotated version costs at least as much. It lists the missed fields by name and shows examples of fields
visible on only one side. Production uses random annotation IDs, so token counts can vary slightly.

Add `--json` for a `tracetrim/compare@1` document on stdout. Deltas are B minus A. Warnings go to stderr.
Add `--html FILE` (1.4) to also write the comparison as one page in the style of the telemetry report, in its
five languages, with nothing loaded from the network: the same numbers as the JSON document.
Explicit `expand` calls in the supplied trace events are counted separately. A proxy can keep recovery
requests in separate evidence files. Replay cannot predict whether the model would have requested a
hidden field or changed its answer.

## Servers that need credentials

Two separate links, two separate credentials: **agent → tracetrim** is protected by the token you
choose, in `$TRACETRIM_TOKEN` (or `--token`, which other users of the machine can see in the process
list), only when tracetrim is reachable over HTTP; requests whose Host is not local or listed with
`--allowed-host` are refused. **tracetrim → your servers**
uses your servers' own credentials, which tracetrim holds and the agent no longer sees.

| how your server authenticates | in `mcp.json` |
|---|---|
| API key or bearer token in a header | `"headers": {"Authorization": "Bearer ${MY_TOKEN}"}` (any header name) |
| local server reading a key from the environment | `"env": {"API_KEY": "${MY_KEY}"}` |
| OAuth, with a browser login (as in Claude Code or Cursor) | just `"url"`: no headers needed |

`${VAR}` is read from the environment when the proxy starts, so keys stay out of the file. For
OAuth servers, log in once:

```bash
tracetrim login --servers mcp.json     # opens the login page of each OAuth server
```

Discovery, client registration, PKCE and token renewal follow the MCP authorization spec (through
the MCP SDK); tokens are kept in `~/.config/tracetrim/oauth/`, readable only by you, and renewed on
their own, so the proxy never waits for a browser. `tracetrim logout --servers mcp.json` forgets them.
Not supported: a different identity per end user (the proxy uses one identity for everyone) and the
legacy SSE transport.

The manual way, with a review step before anything is applied.

**1. Record a few runs with full tool outputs.** A trace is JSONL, one event per line:

```json
{"kind": "prompt", "text": "You are a billing support agent..."}
{"kind": "call", "tool": "stripe_list_charges", "arguments": {"customer": "cus_123"}, "output": "{\"object\": \"list\", ...}"}
{"kind": "message", "text": "{\"ticket\": [...]}"}
```

`output` is the raw text the tool returned; the last `message` is the agent's answer. The
answer matters: without it the learner cannot see what the agent used. An optional `"turn"` on
every event (the model turn that produced it; the prompt is `-1`) lets the learner tell calls
emitted together apart: their arguments were written before any of their outputs existed. OpenAI
Playground Python exports are also read directly (`.py`).

**2. Generate the report.** It changes nothing; it is useful on its own.

```bash
tracetrim report traces/*.jsonl -o report.html
```

You get `report.html` (in English, Italian, Spanish, French and Chinese: which tools fill the context, which calls fail and with
which provider error, which fields the agent never uses, with suggestions), `report.json`, and a
runtime config with the projection it uses: `report.config.json`, `report.projection.json`.

**3. Review the config.** It is plain JSON: which tools are trimmed, with `expand` on. The
projection lists, per tool, the fields kept and the fields dropped. Remove a tool from the config,
or a field from the dropped list, if you know the task needs it.

**4. Apply it.**

In your own code:

```python
from tracetrim.runtime import Policy
policy = Policy.load("report.config.json")
tool_message = policy.apply(tool_name, raw_output)
```

`Policy.apply` only trims: it adds no `[trimmed]` line and there is no `expand` behind it. For the way
back, use `AutoTrim` (function calling, above) or the proxy.

Or in front of any MCP server, with no code change — locally (Claude Desktop, Claude Code,
Cursor):

```json
{"mcpServers": {"billing": {"command": "tracetrim", "args": [
  "proxy", "--upstream-cmd", "python -m my_billing_server", "--config", "/path/report.config.json"]}}}
```

or over HTTP, for hosted clients such as the OpenAI Playground:

```bash
export TRACETRIM_TOKEN=CHOOSE_A_SECRET
tracetrim proxy --upstream-url https://mcp.example.com/mcp --header "Authorization=Bearer UPSTREAM_TOKEN" \
  --config report.config.json --http 8799 --allowed-host your-tunnel.example
```

(`--header` puts the upstream credential in the process list; `--servers mcp.json` with `${VAR}` does not.)

The proxy exposes the same tools plus `expand`: every trimmed output ends with a line listing the
hidden fields, and the agent can fetch any of them back (`"*"` for all of them) while the proxy keeps the original:
the last 256 trimmed results; at most 25 groups are listed, the rest summarised. `--record traces/live.jsonl`
records calls and outputs for telemetry; it does not record the agent's answer, so a report learned from it misses
most of what the agent uses (the study's central finding). To learn while running, use `--auto`, which receives
the answer through `tracetrim_done`.

How it wraps another server: the proxy is an MCP *server* towards your agent and an MCP *client*
towards the upstream. At start it asks the upstream for its tools and publishes the same names and
input schemas as its own (plus `expand`); each call is forwarded unchanged, and only the result is
rewritten on its way back. One proxy can stand in front of several servers (`--servers`).

While it runs, the proxy prints one line per call on stderr — tokens from the tool, tokens to the
agent, hidden fields, `expand` requests and the session total (`--quiet` to silence it). Each total
says where its saving comes from, as in `(-39%: -27% format, -12% fields)`: writing the same data as
compact JSON, and removing fields. In
automatic mode JSON outputs are written compactly (no indentation, no spaces after `,` and `:`) from
the first call, before anything is learned: nothing is removed, and on indented outputs it is often a
large part of the saving (`--no-compact` to keep the spaces). Add
`--structural toon` or `--structural headroom` to re-encode outputs after the projection
(experimental; Headroom needs `tracetrim[headroom]`).

**5. Measure before you trust it.**

```bash
tracetrim measure traces/*.jsonl --per-tool
```

Leave-one-out: each run is trimmed with a projection learned from the others; you see the tokens
removed and the *misses* (held-out calls in which a field the agent used would have been removed).

## What it does not do

- It does not learn from a proxy alone: learn offline from complete traces, apply anywhere.
- It does not see reasoning: a field read only to decide something looks unused. Identifiers and
  titles are kept for this reason; other such fields need your review.
- It does not translate: a value the agent rewrites in another language (or form) that the
  normalisers do not cover looks unused.
- The check on the answer knows whether a value appeared in a tool output, not whether it was given to the
  right item: a true volume attributed to the wrong keyword passes it.
- Structural re-encoding (`"structural": "toon"` or `"headroom"` in the config) is optional and was
  seen to change how a model resolves ambiguous data. Validate on your task first.

## The study behind it

tracetrim comes with a [study](https://doi.org/{PAPER_DOI}), with protocols frozen before every run, of
where this works, where it fails, and what it does to the quality of the answers. In short:

| | |
|---|---|
| Tool-output tokens removed on unseen runs (0.1) | real APIs: 63% and 70% (12% on the third agent); simulated: 69% and 77% |
| …of which re-serialising the JSON alone, removing nothing | up to 13% (real APIs), up to 17% (simulated) |
| Same conversations: tracetrim 0.1 / 0.2 / Headroom (stateless) / full minifier, no field removed | the same two real-API agents: 59–68% / 46–53% / 17–28% / 16–24%; simulated: 69–77% / 57–61% / 31–33% / 18–33% |
| Live runs with `expand`: billed input tokens | −56% (real API) and −32% (simulated); every run passed its gates; a later re-check found one unsupported volume among 180 reported keyword–volume pairs |
| Learning from tool calls only (what a gateway sees) | a used field missed in 49% of the held-out structured calls it trims (156/317, 3 agents on real APIs), against 4% with the answer |
| Where it fails | values the agent transforms, fields used only to reason, and — without an id field — agents **inventing** ids |
| Answer quality on held-out tasks, judged blind (1.3.1, two simulated agents) | ordinary tasks: no loss (+0.05 on a 0–4 scale, 95% interval −0.01 to +0.13, at a ceiling); a stress test built around the removed fields: 83.75% of those facts reported, against 100% with complete outputs |
| The fix, skill version 2 (the default from 1.4) | hidden facts reported: 85.1% → 92.3% (+0.072, 95% interval +0.003 to +0.144), at no extra cost |
| The same stress test with an agent of another model family (`gemini-3.8-flash`) | nothing lost: 100% of the hidden facts in every arm, with `expand` called in every run; the price is more turns (1.67 times the cost of complete outputs per run) |
| Text previews, the first words of hidden free text on the `[trimmed]` line (the default from 1.5) | hidden facts reported: 91.3% → 99.5% (+0.083, 95% interval +0.046 to +0.123), better in 9 of 12 tasks and worse in none, at no extra cost |

Version 0.2 keeps identifying fields and recognises transformed values by default; 0.3 adds the
automatic mode; 1.4 makes skill version 2 the default; 1.5 turns the text previews on. Read the
[paper](https://doi.org/{PAPER_DOI}) before
trimming anything that matters. Measures made after the paper, on the functions of 0.4 and later, are in
[docs/measures-v04.md](https://github.com/alepom/tracetrim/blob/master/docs/measures-v04.md),
[docs/definitions.md](https://github.com/alepom/tracetrim/blob/master/docs/definitions.md) and, for 1.1,
[docs/measures-v1.md](https://github.com/alepom/tracetrim/blob/master/docs/measures-v1.md).

## Documentation

- [docs/hosts.md](https://github.com/alepom/tracetrim/blob/master/docs/hosts.md): `tracetrim init`, host
  formats, and what was verified host by host.
- [docs/langgraph.md](https://github.com/alepom/tracetrim/blob/master/docs/langgraph.md): tracetrim in a
  LangGraph agent.
- [docs/skill.md](https://github.com/alepom/tracetrim/blob/master/docs/skill.md): the instructions
  tracetrim gives the model, and how to check whether a host loads them.
- [docs/adaptive.md](https://github.com/alepom/tracetrim/blob/master/docs/adaptive.md): how automatic
  mode corrects itself (`expand` evidence, promotion, sampling, switch-off).
- [docs/definitions.md](https://github.com/alepom/tracetrim/blob/master/docs/definitions.md): what tool
  definitions weigh, and why tracetrim hides no tool.
- [STABILITY.md](https://github.com/alepom/tracetrim/blob/master/STABILITY.md): what is promised not to
  break. [CHANGELOG.md](https://github.com/alepom/tracetrim/blob/master/CHANGELOG.md): what changed, version
  by version.

## Reproducing the study

`experiments/` contains the agent loop, the simulated MCP servers (Stripe- and GitHub-shaped,
generated from their public OpenAPI descriptions), the gates and thermometers, and every
analysis. `protocol.md` is the frozen protocol with all deviations. Every number of the paper is
generated by `python -m experiments.paper_numbers` from `results/`, and every chart by
`python -m experiments.paper_figures` from `results/` and the traces. Study 4 adds its frozen protocols with their
locks (`protocol-v3.md` to `protocol-v3e.md`), its synthetic worlds, prompts and rubric
(`experiments/v3/` to `experiments/v3e/`) and its aggregate results (`results/v3*`). Run them from the repository root
with `PYTHONPATH=src`. The traces are in the Zenodo dataset: unzip it at the root, and the scripts read
`dataset/traces/` (C3, C4 and C5; C1 and C2 are released as derived records only, since the search
data cannot be redistributed, so their analyses run where they were recorded and their results are in
`results/`; `python -m experiments.misses_from_derived` recomputes their misses from the records). The simulated
servers download their pinned OpenAPI descriptions on first use.

## Citation

Paper: Alessio Pomaro, *Agents Read More Than They Use: Learning Tool-Output Projections from Execution Traces
with tracetrim, from Cost to Answer Quality*, preprint, version 4, 2026. DOI [{PAPER_DOI}](https://doi.org/{PAPER_DOI}) (version 3: [10.5281/zenodo.24446740](https://doi.org/10.5281/zenodo.24446740); version 2: [10.5281/zenodo.24189959](https://doi.org/10.5281/zenodo.24189959); version 1: [10.5281/zenodo.23953934](https://doi.org/10.5281/zenodo.23953934); all versions: [10.5281/zenodo.23953933](https://doi.org/10.5281/zenodo.23953933)).

Software, protocols and dataset: DOI [{SOFTWARE_DOI}](https://doi.org/{SOFTWARE_DOI}) (version 1.5.0; all versions: [10.5281/zenodo.23954124](https://doi.org/10.5281/zenodo.23954124)).
See `CITATION.cff`.

## License

MIT for the code. Third-party materials and their terms: see `LICENSES.md`.
