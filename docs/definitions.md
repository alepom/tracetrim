# Tool definitions: what they weigh, and why tracetrim hides no tool

The model receives the definition of every listed tool (name, description, argument schema) on every
turn. tracetrim trims tool *outputs*; this page is about the *definitions*: what they weighed in the
recorded runs, what a server with many tools would add, and why tracetrim measures them and suggests,
but never removes a tool by itself.

Measured offline on 12 November 2026, with no new run and no spend:
`python -m experiments.definitions` (results: `results/v06/definitions.json`).

## Method

- **Runs**: the 138 recorded runs of the v0.4 measures (E1, E2, E3; `docs/traces-v04.md`): C1 34,
  C2 16, C4 76, C5 12; pilots skipped. Two models: `gpt-5.6-terra` (E1, E2) and `gemini-3.8-flash` (E3).
  Before anything is computed, each run is checked: turns and usage per turn add up, the usage equals
  its line in the spend ledger, the definitions recounted from the recorded schemas equal those
  recorded.
- **Definitions**: tiktoken `o200k_base` on the JSON of the schemas as the harness sent them to the
  model. This is our count, not what the provider billed: providers render definitions their own way.
  On the first turn of C1's runs the provider billed for them at most **0.78** of our count (C2 0.76,
  C4 0.57; for Gemini the two counts come from different tokenizers).
- **Cache**: definitions sit in the static head of the prompt. On a turn they are taken as billed at
  the cached price when the cached tokens cover the first turn's input (at least 90% of it), as not
  cached when the turn has no cached token, and as not cached in the 2 turns that were in between.
- **Cost**: only shares and ratios, at the providers' list prices (`experiments/prices.json`,
  `experiments/prices-e3.json`; cached input is a tenth of the uncached price for both). No amount.

## 1. What the definitions weighed in the recorded runs

Reference arm R of each case and model, median [min–max]:

| Case | Model | Runs | Tools | Definitions per turn | Turns | Definitions per run | Share of input tokens | Definitions billed as cached | Share of the run's cost | …if nothing were cached |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C1 | gpt-5.6-terra | 5 | 12 | 7,699 | 9 [8–10] | 69,291 [61,592–76,990] | 8.7% [7.3–11.7] | 100% [88–100] | 2.7% [2.6–7.0] | 21.7% [20.8–27.5] |
| C1 | gemini-3.8-flash | 2 | 12 | 7,699 | 14 [13–15] | 107,786 [100,087–115,485] | 10.9% [9.2–12.7] | 89% [85–93] | 8.5% [6.1–10.9] | 31.5% [28.9–34.0] |
| C2 | gpt-5.6-terra | 5 | 2 | 621 | 5 [3–6] | 3,105 [1,863–3,726] | 1.1% [1.0–1.3] | 100% [75–100] | 0.2% [0.2–0.5] | 2.1% [1.5–2.2] |
| C2 | gemini-3.8-flash | 3 | 2 | 621 | 6 [6–7] | 3,726 [3,726–4,347] | 2.5% [2.2–2.8] | 67% [67–71] | 1.4% [1.3–1.4] | 3.5% [3.4–3.6] |
| C4 | gpt-5.6-terra | 5 | 7 | 665 | 4 [3–4] | 2,660 [1,995–2,660] | 6.1% [4.3–7.0] | 50% [33–67] | 3.4% [2.2–3.7] | 5.4% [4.8–6.5] |
| C4 | gemini-3.8-flash | 8 | 7 | 665 | 3.5 [3–8] | 2,328 [1,995–5,320] | 9.2% [5.5–13.0] | 12% [0–62] | 2.9% [2.5–3.9] | 4.0% [2.8–5.6] |
| C5 | gemini-3.8-flash | 6 | 6 | 792 | 17 [5–19] | 13,464 [3,960–15,048] | 1.5% [1.4–1.7] | 88% [60–89] | 0.9% [0.7–1.0] | 4.1% [1.6–4.3] |

The other arms are in the results file, of the same order. **On the tool sets these agents actually had,
definitions were at most a few percent of a run's cost**: a sizeable share of the input tokens, but
billed mostly at the cached price. The share overstates them slightly: it puts our count over billed
tokens.

## 2. A whole server enabled: the counterfactual

C1's agent used 11 of the 12 tools it was given. The DataForSEO MCP server it calls lists **79 tools**
(`tools/list`, 12 November 2026), which a user who enables the whole server sends on every turn.

**No run was made with 79 tools.** The figures below take the 5 recorded runs of C1, arm R, *as they
happened* (same turns, calls and cache pattern) and change only the weight of the definitions:
**R79** lists all 79 tools; **D** lists the 11 tools called and puts the other 68 behind two meta-tools
(the design in the last section), **with no search at all**, so D is the most that hiding could gain.

| Definitions per turn | Tokens |
|---|---:|
| R79: the 79 tools of the server | 41,944 |
| the 12 tools of the case, as recorded | 7,699 |
| the 11 tools called in at least one run | 7,535 |
| the two meta-tools | 274 |
| D: 11 listed + meta-tools, 68 hidden | 7,808 |
| saved per turn, D against R79 | 34,136 (81%) |

| Cache assumed | Cost of R79 / recorded | Cost of D / R79 | Input of D / R79 | Extra turns that cancel D's gain |
|---|---:|---:|---:|---:|
| cold start: first turn not cached | 1.25 [1.22–1.31] | 0.80 [0.76–0.82] | 0.72 [0.66–0.76] | 5.2 [4.7–7.7] |
| warm start: first turn cached too | 1.12 [1.11–1.16] | 0.89 [0.86–0.90] | 0.72 [0.66–0.76] | 2.5 [2.4–3.8] |
| no cache at all | 1.36 [1.31–1.48] | 0.73 [0.68–0.76] | 0.72 [0.66–0.76] | 2.5 [2.4–3.8] |
| cold start, definitions as billed (×0.78) | 1.19 [1.17–1.24] | 0.84 [0.80–0.85] | 0.77 [0.71–0.80] | 4.1 [3.7–6.0] |
| warm start, definitions as billed (×0.78) | 1.09 [1.09–1.13] | 0.91 [0.89–0.92] | 0.77 [0.71–0.80] | 1.9 [1.8–3.0] |

In context: the first turn goes from 44,779 to 10,643 tokens, the largest turn from 171,929 to 137,793.
A hidden tool's definition weighs 409 tokens at the median (51 to 1,674): what each search result
would add back.

**Limits of this scenario, in plain words:**

- it assumes that a model facing 79 tools works as it did facing 12. It may choose worse, or call
  tools it did not have; offline this cannot be seen;
- it assumes that hiding costs no turn. Every search is at least one call, and the last column says
  how few extra turns cancel the gain;
- one case, one server, one task, five runs.

**What it says.** With a cache, hiding the unused tools of a large server would save between about 9%
and 20% of a run's cost, and a few extra turns undo it. The robust gain is in context: tens of
thousands of tokens fewer on every turn.

## 3. The mechanisms that already exist

Read on 12 November 2026:

| Where | What it does | Source |
|---|---|---|
| Claude Code | MCP tool search is **on by default**: "MCP tools are deferred and discovered on demand"; only tool names and server instructions load at session start. `ENABLE_TOOL_SEARCH=false` loads everything upfront; `alwaysLoad` exempts a server | <https://code.claude.com/docs/en/mcp> ("Scale with MCP tool search", "Configure tool search") |
| OpenAI Responses API | `tool_search` in the `tools` array, with `defer_loading: true` on functions, namespaces or an MCP server; hosted or client-executed search; `gpt-5.4` and later. **Off unless you add it** | <https://developers.openai.com/api/docs/guides/tools-tool-search> |
| Cursor | loads MCP tools on demand ("dynamic context discovery") **according to its blog; not verified** in the documentation | <https://cursor.com/blog/dynamic-context-discovery> |

So, behind `tracetrim init` in Claude Code, the definitions of the servers tracetrim fronts are
already deferred by the host: what the report counts is what tracetrim publishes, not what the model
reads on each turn.

## 4. What tracetrim does, and why it hides no tool

Since 0.6 the report has a section on tool definitions:

- **proxy, automatic mode**: tracetrim records the list it publishes (`tools/list`, `expand` and
  `tracetrim_done` included) in `WORKDIR/tools.json`, when it changes;
- **function calling**: only if you pass your list, `tt.offered(tools)`, as you send it to the model;
- **`tracetrim report`**: with `--tools FILE` (a saved `tools/list` result or a JSON list of tools).

It shows the tokens of the definitions per turn, the tools offered, those called in at least one
complete run, and those **never called**, with their weight. The weight per run is not given: it is the
weight per turn times the model's turns, which tracetrim does not see.

A **manual** suggestion appears when there are at least `min_runs` complete runs, the tools never called
weigh at least half of the definitions (`NEVER_CALLED_MIN_SHARE`) **and** at least 5,000 tokens per turn
(`NEVER_CALLED_MIN_TOKENS`; both in `tracetrim/definitions.py`). It names the tools never called,
heaviest first (at most 20), and proposes disabling them in the host or using native tool search where
there is one. Never called in N runs does not mean never needed. With the 79 tools of DataForSEO and
a use like C1's it appears; with C1's 12 tools it does not (`tests/test_report_definitions.py`).

**tracetrim does not hide tools**, for three reasons:

1. **Where it weighs most, it is already done.** Hosts and APIs with many tools have native search
   (section 3); a second search on top of theirs adds turns and nothing else.
2. **Host permissions.** Hosts approve, deny or switch off tools by name. A hidden tool called through
   a generic `call_tool` would inherit the permission given to `call_tool`: a hole in a system that is
   meant to be predictable.
3. **The gain is fragile.** Below 3% of the cost on the tool sets these agents had; 9–20% with a whole
   large server enabled, in a counterfactual that a few extra turns cancel.

## Note: the design that was not built

For a loop of your own, with many tools and no native search: list in full the tools the agent used
while learning; put the others behind `find_tools(query)` (a match on names and descriptions, returning
name, description and schema; when nothing matches, the names of every hidden tool) and
`call_tool(name, arguments)` (only tools that exist upstream, arguments validated against their schema).
Never change the list during a run, so as not to break the prompt cache; list a tool in full from the
run after it was called through `call_tool`. The two meta-tools weigh 274 tokens. Mind the permission
problem above, and measure the turns it costs before relying on it.
