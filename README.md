# tracetrim

**Agents read more than they use.** tracetrim learns, from your agent's own traces, which
fields of each tool output the agent actually uses, and helps you remove the rest — with a
telemetry report you can read, a configuration you can review, and an MCP proxy that applies it
in front of any server without changing your agent.

It comes with a [study](https://doi.org/10.5281/zenodo.23953934), with protocols frozen before every run, of
where this works and where it fails. In short:

| | |
|---|---|
| Tool-output tokens removed on unseen runs (4 of 5 agents) | 63–77% (0.1), 46–61% with the 0.2 safeguards |
| Same conversations, structural compression only (Headroom) | 17–33% |
| Live runs with `expand`: billed input tokens | −56% and −32%, every quality gate passed |
| Learning from tool calls only (what a gateway sees) | misses most of what agents use |
| Where it fails | values the agent transforms, fields used only to reason, and — without an id field — agents **inventing** ids |

Version 0.2 keeps identifying fields and recognises transformed values by default. Read the
[paper](https://doi.org/10.5281/zenodo.23953934) before trimming anything that matters.

## Install

```bash
pip install "tracetrim[measure,proxy] @ git+https://github.com/alepom/tracetrim"
pip install "tracetrim[headroom] @ git+https://github.com/alepom/tracetrim"   # + Headroom backend (experimental, heavy)
```

Python 3.10+. From a clone: `pip install -e ".[measure,proxy,test]"`. Not on PyPI yet.

## Quickstart: five runs, one report, one config

**1. Record a few runs with full tool outputs.** A trace is JSONL, one event per line:

```json
{"kind": "prompt", "text": "You are a billing support agent..."}
{"kind": "call", "tool": "stripe_list_charges", "arguments": {"customer": "cus_123"}, "output": "{\"object\": \"list\", ...}"}
{"kind": "message", "text": "{\"ticket\": [...]}"}
```

`output` is the raw text the tool returned; the last `message` is the agent's answer. The
answer matters: without it the learner cannot see what the agent used. OpenAI Playground Python
exports are also read directly (`.py`).

**2. Generate the report.** It changes nothing; it is useful on its own.

```bash
tracetrim report traces/*.jsonl -o report.html
```

You get `report.html` (bilingual EN/IT: which tools fill the context, which calls fail and with
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

Or in front of any MCP server, with no code change — locally (Claude Desktop, Claude Code,
Cursor):

```json
{"mcpServers": {"billing": {"command": "tracetrim", "args": [
  "proxy", "--upstream-cmd", "python -m my_billing_server", "--config", "/path/report.config.json"]}}}
```

or over HTTP, for hosted clients such as the OpenAI Playground:

```bash
tracetrim proxy --upstream-url https://mcp.example.com/mcp --header "Authorization=Bearer UPSTREAM_TOKEN" \
  --config report.config.json --http 8799 --token CHOOSE_A_SECRET --allowed-host your-tunnel.example
```

The proxy exposes the same tools plus `expand`: every trimmed output ends with a line listing the
hidden fields, and the agent can fetch any of them back. Add `--record traces/live.jsonl` to keep
recording, and regenerate the report from time to time.

How it wraps another server: the proxy is an MCP *server* towards your agent and an MCP *client*
towards the upstream. At start it asks the upstream for its tools and publishes the same names and
input schemas as its own (plus `expand`); each call is forwarded unchanged, and only the result is
rewritten on its way back. One proxy per upstream server.

While it runs, the proxy prints one line per call on stderr — tokens from the tool, tokens to the
agent, hidden fields, `expand` requests and the session total (`--quiet` to silence it). Add
`--structural toon` or `--structural headroom` to re-encode outputs after the projection
(experimental; Headroom needs `tracetrim[headroom]`).

**5. Measure before you trust it.**

```bash
tracetrim measure traces/*.jsonl --per-tool
```

Leave-one-out: each run is trimmed with a projection learned from the others; you see the tokens
removed and the *misses* (runs in which a field the agent used would have been removed).

## What it does not do

- It does not learn from a proxy alone: learn offline from complete traces, apply anywhere.
- It does not see reasoning: a field read only to decide something looks unused. Identifiers and
  titles are kept for this reason; other such fields need your review.
- It does not translate: a value the agent rewrites in another language (or form) that the
  normalisers do not cover looks unused.
- Structural re-encoding (`"structural": "toon"` or `"headroom"` in the config) is optional and was
  seen to change how a model resolves ambiguous data. Validate on your task first.

## Reproducing the study

`experiments/` contains the agent loop, the simulated MCP servers (Stripe- and GitHub-shaped,
generated from their public OpenAPI descriptions), the gates and thermometers, and every
analysis. `protocol.md` is the frozen protocol with all deviations. Every number of the paper is
generated by `python -m experiments.paper_numbers` from `results/`.

## Citation

Paper: Alessio Pomaro, *Agents Read More Than They Use: Learning Tool-Output Projections from Execution Traces,
and Where They Fail*, preprint, 2026. DOI [10.5281/zenodo.23953934](https://doi.org/10.5281/zenodo.23953934).

Software, protocols and dataset: DOI [10.5281/zenodo.23954125](https://doi.org/10.5281/zenodo.23954125).
See `CITATION.cff`.

## License

MIT for the code. Third-party materials and their terms: see `LICENSES.md`.
