# The tracetrim skill

What an agent needs to know to work behind tracetrim, written once and delivered three ways.
Unreleased: this page describes the branch `feat/adaptive-learning-mcp-skills`.

## Two levels

| Level | When | What it says | Tokens (`o200k_base`) |
| - | - | - | - |
| **base** | always | call `tracetrim_done` once with the complete final answer (the answer, not the conversation); get hidden fields back with `expand` | 221 |
| **strategy** | only when asked for | how to organise tool calls: independent calls together, grouped inputs, error feedback, progressive search, when to stop | +549 (770 in all) |

The strategy teaches a method and prescribes no parameter value: limits, thresholds, ranges and
budgets stay with your prompts and with the tools' schemas. It is off by default: see what was
measured, below.

The text lives in `src/tracetrim/skills/tracetrim/` (`done.md`, `expand.md`, `strategy.md`) and has
its own version, `tracetrim.skill.VERSION`; a test fails when a text changes and the version does not.
Token counts are those of the text alone; the published `SKILL.md` adds its frontmatter (312 and 884).

## What was measured on the strategy

Experiment E1 of `protocol-v04.md` (2026-11-11, `results/v04/e1.json`): 50 runs of `gpt-5.6-terra`, with and
without the strategy text, with and without trimming, on three agents (editorial plan and content-gap briefing
on a real search-data API, billing support on a simulated API), five runs per arm.

- **On this model the strategy changed nothing that could be measured**: turns, tool calls, billed tokens and
  cost stayed inside the ranges of the arms without it. The three hypotheses stated before the runs failed.
- **This model already issues calls together.** Without the strategy, 77%, 68% and 77% of the turns that
  made a call made more than one (median 2, 3 and 5 calls per turn in the three cases). There was little left
  for the strategy to organise. **Its effect on a model that issues calls one at a time was not measured.**
- **It did not override the prompts.** The values the prompts prescribe (`limit` 10, `depth` 10, at most five
  keywords per call) were respected in all 50 runs, with and without the strategy.
- **One run asked a question instead of working**: in the arm with strategy and trimming, the model
  answered that the reference date was in the future and asked for another. One run in 25 with the strategy,
  none in 25 without, and none in the 60 live runs of the original study: too few to blame the text, which
  does say to ask for information "when it is indispensable".

So the strategy stays off by default, and nothing here says it is useless on other models.

## Three ways to deliver it

**1. MCP Skills.** Whenever the proxy offers `expand` (`--auto` or `--config`), it publishes the
skill through the MCP Skills extension (`io.modelcontextprotocol/skills`, SEP-2640):

```bash
tracetrim proxy --servers mcp.json --auto tracetrim-data/                    # base
tracetrim proxy --servers mcp.json --auto tracetrim-data/ --skill-strategy   # base + strategy
tracetrim proxy --servers mcp.json --auto tracetrim-data/ --no-skill         # nothing published
```

A host that loads skills finds `skill://tracetrim/SKILL.md` with `skills/list`, reads it with
`resources/read` and checks it against the manifest. With `--config` there is no `tracetrim_done`
tool, and the skill leaves that part out.

**2. Function calling.** There is no MCP server to publish anything: put the text in the system
prompt. A function-calling loop passes the answer itself, so the text has no `tracetrim_done`.

```python
tt = AutoTrim("tracetrim-data/", strategy=False)      # strategy=True adds the strategy notes
system_prompt += "\n\n" + tt.instructions
```

**3. By hand.** For a host that does not load MCP skills:

```bash
tracetrim skill                       # print the text, to paste into the agent's instructions
tracetrim skill --strategy            # ... with the strategy notes
tracetrim skill --write DIR          # or write DIR/tracetrim/SKILL.md, for a host that reads skills from disk
```

Without any of these the agent still has the tool descriptions of `expand` and `tracetrim_done`,
which state the contract in one sentence each, and the one line for the instructions given in the README.

## What the proxy serves

| Request | Reply |
| - | - |
| `server/discover` | capabilities with `resources` and `extensions["io.modelcontextprotocol/skills"] = {}` (no `directoryRead`) |
| `initialize` | the same capabilities; `extensions` only when the client declared the extension in its own capabilities |
| either | `instructions`: one sentence that points to the skill (38 tokens), and `serverInfo.version` |
| `skills/list` | one entry: `uri`, the whole `frontmatter`, and `resources` with `uri`, `digest` (`sha256:` of the bytes served) and `size` |
| `skills/get` | the same entry, by URI |
| `resources/read` | the `SKILL.md`, `text/markdown` |
| `resources/list` | the `SKILL.md`, with the skill's name and description |

Results carry `resultType: "complete"`, `ttlMs` (5 minutes) and `cacheScope: "public"`. An unknown
skill, resource or cursor is `-32602`. The proxy's resources are this one file: the resources of the
upstream servers are not proxied (they were not before either).

**Updates.** The entry changes only when the proxy restarts with other options or a new version. The
digest then changes with it: a host that kept the old entry fails verification on its next read and
refreshes it with `skills/get`, as the extension prescribes; a skill already in the model's context
stays as it was loaded until the host reloads it. There is no change notification.

**Protocol versions.** Extensions are part of the protocol from `2026-07-28`, where the server
announces them in `server/discover`. On `2025-11-25` and earlier, hosts negotiate them in the
`initialize` handshake, in a `capabilities.extensions` field that is outside the published schema of
those versions (MCP Inspector does this). The proxy answers in kind, but only to a client that declared
the extension: the others never see a field they did not ask for. The `mcp` 2.3.0 SDK drops the field
from an `initialize` reply, so the proxy puts it back with a middleware.

**Hosts that do not load skills.** They see the `SKILL.md` as an ordinary resource, and the server's
`instructions` say in one sentence that it exists and what it is for. Whether a host shows a server's
instructions to its model is up to the host; the tool descriptions carry the contract in any case.

**SDK.** Written against `mcp` 2.3.0, which has no Skills support (it is merged upstream and not
released): `skills/list` and `skills/get` are registered as plain request handlers.

## What was tried, and what was not

| Situation | Tried with | Result |
| - | - | - |
| The client loads the skill | the `mcp` 2.3.0 Python client on protocol `2026-07-28`, following the host loading path of the specification (list, read, verify size, digest and frontmatter) | conforms |
| The client sees resources and has no way to load a skill | the `mcp` 2.3.0 Python client on protocol `2025-11-25` | the skill is listed as a resource; the run is closed and `expand` works from the tool descriptions alone |
| The session ends without `tracetrim_done` | both | the run is kept for telemetry only, nothing is learned from it |

| A host that negotiates extensions in `initialize` | the handshake of MCP Inspector CLI 2.13.0, replayed byte for byte in a test | the extension is announced, `skills/list` and `skills/get` answer |

These are tests of the server (`tests/test_proxy_skill.py`). **Activation in an agent host was not
tried**: no agent host was connected, so whether a given host loads the skill on its own, and whether
its model then follows it, is not shown here. Inspector verifies that the skill is served correctly; it
is not an agent and does not act on it.

**Verified as conformant by MCP Inspector CLI 2.13.0** (2026-11-11): outcome `verified`, the
`SKILL.md` matching the manifest in digest and size (1303 bytes), no conformance or frontmatter
violation. A first run, against an earlier state of this branch, had reported `skills_unsupported`
because the proxy did not announce the extension in `initialize`. The command:

```bash
npx -y @modelcontextprotocol/inspector --cli .venv/bin/python -m tracetrim.cli proxy --upstream-cmd ".venv/bin/python tests/fixtures/meta_server.py" --auto /tmp/tt-inspector --quiet --method skills/list --verify
```

## Closing a run

- `tracetrim_done` takes the final answer and nothing else. The proxy already has the calls and their
  results; the answer is stored as the run's last message and is never treated as a tool output.
- Closing is idempotent: a repeated or retried `tracetrim_done` (or `AutoTrim.done`) with no tool
  call since the last closing changes nothing, and the reply is the same `Recorded.`.
- `tracetrim_done` without an answer is refused, and the run stays open for the call that carries it.
  If the agent does not try again, nothing closes the run until the idle pause (5 minutes without a
  call) or the end of the session, and it is then kept without answer. **A task that starts before
  that lands in the same run**: its calls follow the earlier ones, and its answer closes both. The
  report counts the refused closings, so this can be seen.
- An answer followed by more calls opens a second run. The proxy cannot tell an agent that closed too
  early from the next task, so it does not merge them; the report counts the runs closed by an answer
  and followed by calls within the idle pause.
- A session that ends without `tracetrim_done` leaves a run without answer: telemetry, not learning.
- One run at a time per proxy or `AutoTrim`: calls that arrive between two closings belong to the
  same run, whoever sent them. Give each agent its own instance and work directory.
