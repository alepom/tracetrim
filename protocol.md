# Experimental protocol (frozen)

Status: **frozen on 2026-11-04**, approved by the author, committed and tagged `protocol-v1` before any run of this
protocol, pilot included. Changes after freezing go in "Deviations" at the bottom, dated, with the reason.

Pilot evidence already available, and declared as such in the paper: the 11 traces of the course project
(`accademia-server-mcp`, agent "PED", Oct 2026) and the replay of 2026-11-03. They motivated this protocol and are
reported as an exploratory study, not as a test of the hypotheses below. They contain a client domain and are never published.

## Research questions

| # | Question | Measured by |
|---|---|---|
| RQ1 | What share of tool-output tokens carries fields the agent visibly uses? | usage analysis on Phase A traces |
| RQ2 | How much does a projection learned from other traces trim, and how often does it hide a field the held-out trace uses? | leave-one-out, Phase A |
| RQ3 | Does the agent's output keep its quality when tool outputs are projected? | replay with one variable changed, Phase B |
| RQ4 | Where must the learner sit: does seeing only tool calls (MCP proxy) suffice? | leave-one-out with `source=calls` vs `full`, Phase A |
| RQ5 | Does a projection learned on one task transfer to another task using the same tools? | learn on C1, apply to C2, Phase A |
| RQ6 | With an `expand` tool for hidden fields, how often is it called, and what does the trimmed loop cost end to end? | live runs, Phase C |

## Hypotheses (stated before Phase A)

- H1: the prudent projection removes at least 30% of structured tool-output tokens in C1 (pilot: 36-56% per trace).
- H2: in Phase B the prudent arm passes every gate in at least as many runs as the raw arm, and every thermometer median
  lies inside the raw arm's [min, max].
- H3: `source=calls` hides a used field in more than three times as many calls as `source=full` (pilot: 124 vs 28 of 201).
- H4: cross-task transfer (C1 → C2) misses more fields than within-task learning on C2.

A hypothesis that fails is reported as failed. No hypothesis is added after Phase A starts.

## Cases

| Case | Agent | Tools | Input names only | Output checked against |
|---|---|---|---|---|
| C1 | editorial plan for a website | DataForSEO MCP: Labs, keyword data, SERP, Trends, on-page parsing | alessiopomaro.it | volumes and keywords in the tool outputs |
| C2 | content-gap research briefing | DataForSEO MCP: SERP, on-page parsing | alessiopomaro.it | URLs and titles in the SERP outputs |
| C3 | literature scan | OpenAlex API (CC0) | — | DOIs and titles in the tool outputs |

**Domain rule, enforced in code, not in the prompt.** Every input names only the author's domains. The tool wrapper
rejects `on_page_content_parsing` on any URL outside `alessiopomaro.it` and any Labs `target` outside it, returning a
fixed error message. SERP results naturally list third-party domains: they are measured, never published in clear.
Every rejection is logged and counted.

Prompts are written for this study, versioned in `experiments/prompts/`, and fixed at freezing.

## Agent loop

Our own loop with function calling (OpenAI Responses API), because hosted MCP never exposes tool outputs to the client.
The loop calls the same DataForSEO MCP server used in the pilot as an MCP client, so outputs are byte-identical to what a
hosted integration would receive. Every event is written to a trace in the tracetrim JSONL format.

- Model: `gpt-5.6-terra`, default parameters, for every phase. Model id and date are stored with every trace.
- No hard cap on tool calls; the budget instruction of the pilot's B5 prompt is kept in C1.

## Phases

**Pilot (P).** One run per case, to check the loop, the traces and the costs. Not used in any result.

**Phase A — collection.** 10 live runs per case with raw tool outputs. Used for RQ1, RQ2, RQ4, RQ5.
Leave-one-out within each case; transfer: learn on all 10 C1 traces, apply to C2 traces (shared tools only).

**Phase B — replay.** For each case, 2 traces chosen *before* looking at their content: the 3rd and the 7th of Phase A.
The conversation is replayed up to the final answer, changing only the tool messages. Arms:

| Arm | Tool messages |
|---|---|
| raw | as received |
| headroom | Headroom `compress` (version recorded), most aggressive configuration as in the pilot |
| prudent | learned projection, prudent, from the other 9 traces of the case |
| aggressive | learned projection, aggressive, same source |

3 runs per arm and trace, interleaved (raw, headroom, prudent, aggressive, then again) so that drifts of the service hit
all arms alike. TOON and the hand-written projection (C1, `serp_organic_live_advanced` only) are reported as token
baselines, not replayed.

**Phase C — live loop with `expand`.** C1 and C2, 5 runs per arm: raw vs prudent projection plus an `expand(call_id,
fields)` tool that returns hidden fields. Measures billed tokens (usage API, cache included), turns, `expand` calls,
gates and thermometers.

## Measures

- Tokens: tiktoken `o200k_base` for tool outputs; `usage` from the API for what is billed. Never estimated.
- **Gates** (binary): output parses; required structure present; domain rule never violated; **facts**: every number or
  identifier the output attributes to a tool result matches that result (C1: keyword ↔ volume; C2: URL ↔ title;
  C3: DOI ↔ title).
- **Thermometers** (graded): C1 reuses the pilot's metrics (`confronta-qualita.py`: volume median and mean, long-tail
  share, topics covered, items proposed); C2: distinct sources cited, own pages linked, gaps identified;
  C3: distinct works cited, share of recent works.
- Reporting: median [min-max] per arm. With 3-5 runs per arm no significance test is claimed; H2 is judged with the
  band rule above. An effect inside the raw arm's band is reported as "not distinguishable from run-to-run variation".

## Budget and stop rule

Model prices assumed for the estimate: 2 USD / 1M input tokens, 12 USD / 1M output tokens (author's listing for
gpt-5.6-terra, **to be verified**; no cache discount assumed, so the estimate is conservative). Run sizes from the pilot:
live C1 run 117,978 input / ~7,500 output tokens; C1 replay 106,509 / ~7,000; C2 live 54,644 / ~2,000 (course,
"Concetto 5"); C3 assumed equal to C2 until the pilot measures it.

| Phase | Runs | Estimate (USD) |
|---|---:|---:|
| P | 3 | 0.6 |
| A | 30 | 5.9 |
| B | 72 | 13.5 |
| C | 20 | 4.6 |
| **Total** | **125** | **24.6** |

Model spend cap: **30 USD**. The running total is computed from `usage` after every run; at 25 USD the author is
warned, at 30 USD everything stops. DataForSEO calls are billed separately on the provider account and estimated after
the pilot, before Phase A.

## Data and publication

Following the providers' terms read on 2026-11-03 (local copies in `legal/`):

- Published: code (MIT), paper (CC BY 4.0), aggregate results, field paths, per-run metrics.
- Traces are published only **pseudonymised**: every word token replaced by a keyed hash, identically in tool outputs
  and agent text, so that usage detection gives the same results; original token counts stored alongside. The key is
  not published. Raw traces stay local.
- C3 (OpenAlex, CC0) traces may be published in clear.
- No DataForSEO or SERP excerpt is reproduced in the paper beyond field names and structure.

## AI assistance

The author set the questions, the constraints and the domain rule. An AI coding agent (Claude Code) proposed the
protocol, writes the code, runs the experiments and drafts the manuscript under the author's direction. Declared in the
paper, as in the author's previous study.

## Deviations

*(none)*
