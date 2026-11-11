# The tracetrim skill

What an agent needs to know to work behind tracetrim, written once and delivered three ways.
Unreleased: this page describes the branch `feat/adaptive-learning-mcp-skills`.

## Two levels

| Level | When | What it says | Tokens (`o200k_base`) |
| - | - | - | - |
| **base** | always | call `tracetrim_done` once with the complete final answer (the answer, not the conversation); get hidden fields back with `expand` | 221 |
| **strategy** | only when asked for | how to organise tool calls: independent calls together, grouped inputs, error feedback, progressive search, when to stop | +549 (770 in all) |

The strategy teaches a method and prescribes no parameter value: limits, thresholds, ranges and
budgets stay with your prompts and with the tools' schemas. It is off by default because its effect
has not been measured yet.

The text lives in `src/tracetrim/skills/tracetrim/` (`done.md`, `expand.md`, `strategy.md`) and has
its own version, `tracetrim.skill.VERSION`; a test fails when a text changes and the version does not.
Token counts are those of the text alone; the published `SKILL.md` adds its frontmatter (312 and 884).

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

**Protocol versions.** The extension is defined for protocol `2026-07-28` and later. With the earlier
`initialize` handshake the MCP SDK does not advertise extensions: the `SKILL.md` is then an ordinary
resource, and the tool descriptions carry the contract.

**SDK.** Written against `mcp` 2.3.0, which has no Skills support (it is merged upstream and not
released): `skills/list` and `skills/get` are registered as plain request handlers.

## What was tried, and what was not

| Situation | Tried with | Result |
| - | - | - |
| The client loads the skill | the `mcp` 2.3.0 Python client on protocol `2026-07-28`, following the host loading path of the specification (list, read, verify size, digest and frontmatter) | conforms |
| The client sees resources and has no way to load a skill | the `mcp` 2.3.0 Python client on protocol `2025-11-25` | the skill is listed as a resource; the run is closed and `expand` works from the tool descriptions alone |
| The session ends without `tracetrim_done` | both | the run is kept for telemetry only, nothing is learned from it |

These are tests of the server (`tests/test_proxy_skill.py`). **No agent host was tried**: whether a
given host loads the skill on its own, and whether its model then follows it, is not shown here.

## Closing a run

- `tracetrim_done` takes the final answer and nothing else. The proxy already has the calls and their
  results; the answer is stored as the run's last message and is never treated as a tool output.
- Closing is idempotent: a repeated or retried `tracetrim_done` (or `AutoTrim.done`) with no tool
  call since the last closing changes nothing, and the reply is the same `Recorded.`.
- `tracetrim_done` without an answer is refused, and the run stays open for the call that carries it.
- A session that ends without `tracetrim_done` leaves a run without answer: telemetry, not learning.
- One run at a time per proxy or `AutoTrim`: calls that arrive between two closings belong to the
  same run, whoever sent them. Give each agent its own instance and work directory.
