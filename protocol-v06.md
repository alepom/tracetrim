# Protocol of the measure on deferred tool definitions (v0.6) — DRAFT

Status: **DRAFT, not frozen.** Written on 2026-11-12 for review (ORDINE-05, phase 06a), before any code of the
function and before any run. Nothing here may be run for money until the protocol is frozen and the author has
approved the spend. When it is frozen, this paragraph is replaced by the date and the commit, and every later
change goes in "Deviations".

This protocol is separate from `protocol.md` (the original study), `protocol-v04.md` (E1, E2) and
`protocol-v04-e3.md` (E3): nothing here changes a number of theirs or of the paper. The experiment is **E4**; the
hypotheses continue the numbering (H12 onward).

No amount spent, estimated or capped is written in this document: costs appear as ratios to arm R (`cost_vs_R`),
at the provider's list prices.

## The question

At every turn the model receives the definitions of every enabled tool. tracetrim trims tool outputs; it does
nothing about definitions. **E4** — when an agent has a whole MCP server enabled and uses a small part of it,
does listing in full only the tools it uses, and putting the others behind a search, lower what a run costs and
carries in context **without lowering the quality of the answer**? And when a hidden tool becomes necessary, does
the agent find it, and does it come back?

A negative result ("it is not worth it") is a result and is reported as such.

## What is known before any run (offline, no spend)

`python -m experiments.definitions` reads the recorded runs of the v0.4 measures and writes
`results/v06/definitions.json`. On 2026-11-12, tokens counted with tiktoken `o200k_base` on the definitions as
the harness sends them:

- the DataForSEO MCP server lists **79 tools: 41,944 tokens** of definitions; the 12 of C1 weigh 7,699, the 11
  called in at least one recorded run 7,535;
- in the recorded runs of C1 (arm R of E1, 12 tools) the definitions are 8.7% of the billed input tokens and
  2.7% of the run's cost at the median, because the provider billed them as cached on nearly every turn;
- the provider bills the definitions for fewer tokens than our count of their JSON: on those runs' first turn,
  0.78 of it;
- **counterfactual**, the recorded runs with 79 tools in place of 12, same turns and cache pattern: cost 1.25
  times the recorded at a cold start (first turn not cached), 1.12 at a warm one; with the unused tools hidden,
  0.80 and 0.89 of that at best (0.84 and 0.91 with the definitions as billed); input tokens 0.72. Two to five
  more turns cancel the gain in cost.

These are estimates on runs made with 12 tools. Whether a model that sees 79 tools works as it did with 12, and
what the two meta-tools cost in turns, is what E4 measures. The hypotheses below were written knowing these
estimates and no measured run of E4.

## What is under test

The software at the commit recorded at freezing: tracetrim 0.6 with the option that defers tool definitions
(working name `--defer-tools`; in function calling, `AutoTrim(defer_tools=True)`), off by default.

| Function | E4 |
|---|---|
| Deferred definitions | the variable |
| Projection (output trimming), `expand` | **off** in every arm: one variable at a time |
| Skill, base level | only the paragraph on the two meta-tools, in arm D |
| Skill, strategy level | off |
| Promotion of fields, sampling | off |

Model: `gpt-5.6-terra`, default parameters, as in E1 and E2. Agent loop: the study's own (`experiments/run.py`,
OpenAI Responses API, function calling), with tracetrim through its function-calling adapter. The MCP proxy is
not in this measure; that the proxy and the adapter build the same list and answer the meta-tools in the same
way is covered by a test.

## Case

**C1**, the editorial plan on DataForSEO, prompts of the original study unchanged, with **every tool the server
lists enabled** (79 on 2026-11-12; the list and a hash of every definition are recorded with each run, and a run
whose list differs from the frozen one does not start).

Enabling 79 tools changes what the model can call. Three rules, in the tool wrapper and equal in every arm:

- **Domain gate, extended.** Today it covers `on_page_content_parsing`, the argument `target` and `site:`
  queries. Before any run it must cover every argument of the 79 tools that can name a site (`targets`,
  `target1`, `target2`, `url`, `pages`, `main_domain`, filters on domains): a table tool by tool, with a test.
  A call that names a site other than the author's is refused and counted.
- **Tools refused in the wrapper** (to be fixed at freezing, with the author): the tools whose calls the author
  does not want billed (for example those that query language models or need a separate subscription) stay
  **listed**, so that the definitions weigh the same, and answer with a fixed refusal, counted. The list is
  part of the frozen protocol.
- The guard of the earlier measures (80 calls, 40 turns): a run that hits it is flagged.

## Arms

| Arm | Tools listed in full | The others |
|---|---|---|
| R | all 79 | — |
| D | those the agent used while learning | behind `find_tools(query)` and `call_tool(name, arguments)` |

- **What D lists** is computed once, by the delivered rule, from the eight Phase A traces of C1 (the same
  starting point as E1's fixed configuration and E2's seed). On 2026-11-12 that is 11 tools, 68 hidden; the
  precondition script writes the list to `results/v06/preconditions.json`.
- The list never changes during a run.
- Arm D adds to the system prompt the skill paragraph that explains the two meta-tools; arm R adds nothing. The
  tokens added are counted.

## E4a — the task as it is

Does deferring cost anything when the listed tools are enough?

- Arms R and D, the original task, **5 runs per arm**, independent: in D nothing is learned between runs (a
  fixed list), as in E1. In rounds, one run of each arm, the order alternating.

## E4b — a hidden tool becomes necessary

- **Variant of the task**: the user prompt of C1 plus one requirement that needs a tool D hides, and that no
  listed tool can satisfy. Proposed, to be fixed at freezing: for the main trending topic, the split of the
  interest by age and gender, which only `kw_data_dfs_trends_demography` returns. The prompt
  (`experiments/prompts/c1d-user.md`) is written and fixed before freezing; the precondition script checks, on
  the recorded outputs, that no listed tool returns those fields.
- **R: 3 independent runs** of the variant, all tools listed: what the model does when it sees the tool.
- **D: one sequence of 5 consecutive runs** in one work directory, learning on: a tool called through
  `call_tool` is listed in full from the next run (the return). One sequence is a single trajectory: stated
  under "What cannot be concluded".

## Pilot

Before E4a, **one run of R and one of D** on the original task, used in no result. They answer what cannot be
known offline: whether a run with 79 tools completes, what it costs, which tools outside the 12 the model calls,
and whether the gate holds. After the pilot everything stops for a report.

## Measures

Per run, by script from the trace and its meta file; everything listed for E1 in `protocol-v04.md` (gates,
coverage, turns and calls, billed tokens and cost, tokens by category, DataForSEO calls per tool), plus:

1. **Definitions**: tokens listed per turn (counted), tools listed and hidden; billed input of the first turn.
2. **Cache**: billed cached tokens per turn; whether the first turn was cached.
3. **Meta-tools** (D): `find_tools` calls, with the query, the tools returned and their tokens; searches that
   returned nothing; `call_tool` calls by real tool, and how each ended (answered, arguments refused, unknown
   tool, error upstream); turns in which only meta-tools were called.
4. **Tools called outside the 12 of the original case**, per arm, with their outcome; refusals of the domain
   gate and of the wrapper.
5. **E4b**: requirement met (the answer carries an age and a gender split that appear in that run's outputs);
   how the tool was reached (listed, `find_tools` then `call_tool`, `call_tool` by name, not reached); in D,
   from which run the tool is listed in full.
6. `cost_vs_R`: the run's cost over the median cost of R in the same part.

Reported as median [min–max] per arm. A reduction in tokens or cost is never reported alone: it is shown beside
gates and coverage of the same runs.

## Hypotheses (stated before any run of E4)

- **H12 — quality holds.** In E4a, D passes every gate in at least as many runs as R, and its coverage median
  lies inside R's [min, max] or above it.
- **H13 — less input.** In E4a, the billed input of D's first turn is at least 20,000 tokens below R's in every
  run, and D's billed input per run has a median below R's minimum.
- **H14 — lower cost.** In E4a, D's median `cost_vs_R` is at most 0.95. A median between 0.95 and 1.05, or
  inside R's own range, is reported as **not distinguishable**; above, as a cost.
- **H15 — no detour when the list is enough.** In E4a, D makes no `find_tools` call in at least 4 runs of 5,
  and its median number of turns is not above R's maximum.
- **H16 — a hidden tool is found.** In E4b, in every run of D in which the needed tool is hidden and the model
  looks for it, the first or second `find_tools` returns it and the `call_tool` that follows is answered; the
  requirement is met in D in a share of runs not below R's.
- **H17 — it comes back.** In E4b, from the run after the first `call_tool` on the needed tool, the tool is
  listed in full and no `find_tools` call is made for it.

H16 and H17 depend on what the model does: if in R the model does not call the needed tool either, or in D it
never looks for it, they are reported as **not testable in these runs**, with the counts. With five runs per
arm no significance test is claimed: a difference inside the other arm's range is not distinguishable from
run-to-run variation.

### What counts as success

The option stays **off by default** whatever the outcome. It is described as recommended for agents with many
unused tools only if H12 holds, H13 holds, and H14 is not a cost. If H12 fails, the result is "deferring lowers
quality" and is reported as such, whatever the saving. If H12 and H13 hold and H14 is not distinguishable, the
result is "less context, no measurable saving in cost": a benefit for the context alone.

## What cannot be concluded

- **One model, one case, one server.** Other models may search differently or not at all.
- **Few runs.** Five per arm in E4a; in E4b three runs against one sequence of five: a single trajectory.
- **The provider's cache decides the cost.** The runs of an arm follow one another within minutes: first turns
  are often cached, which a user who runs once a day does not get. The first turn's cache is reported per run,
  and the cost is also recomputed with every first turn not cached.
- **Hosts that defer by themselves.** Some MCP hosts and some provider APIs already load tool definitions on
  demand. E4 measures tracetrim's mechanism against listing everything, in a loop that does not defer: it says
  nothing of what tracetrim adds where the host already does it, which should be nothing.
- **Search by name and description only.** No model or embedding ranks the tools: a tool whose description does
  not share words with what the model asks is not found, and E4b tests one such tool.
- **The gates** are those of the original study, with their limits; outputs are not trimmed in either arm, so E4
  says nothing about deferring and trimming together.
- **Counted and billed definitions differ**: token counts of definitions are ours; billed tokens the provider's.

## Runs, budget and stop rules

Estimate: `python -m experiments.budget --v06` (needs `results/v06/definitions.json` and the E1 runs of C1,
`$TRACETRIM_V04_TRACES`). Two figures per run: the recorded median times the counterfactual ratio for 79 tools
at a cold start; and the dearest recorded run plus the added definitions at the uncached price on every turn of
the longest run. Arm D is costed like R: the estimate does not count on the saving it is there to measure.
E4b's variant is assumed to cost 1.25 times the original task.

| | Runs |
|---|---:|
| Pilot: 1 run of R, 1 of D | 2 |
| E4a: 2 arms × 5 runs | 10 |
| E4b: R 3 runs, D 1 sequence × 5 runs | 8 |
| **Total** | **20** |

- **Spend cap and warning**, set by the author and not published (`experiments/caps.local.json`, key `v06`), on
  a ledger of its own (`out/ledger-v06.jsonl`). The total is computed from the API's usage after every run,
  aborted ones included; at the cap no run starts; at the warning everything stops until the author decides.
- Prices: `experiments/prices.json`. The largest request expected with 79 tools is under the threshold above
  which the price list changes; a run that crosses it is flagged and costed at the higher price.
- **DataForSEO** is billed apart. Expected calls: 440 (20 runs × 22; 480 at the maximum seen per run), plus any
  call to tools outside the 12, which only the pilot can show. **Stop rule**: after the first 4 runs that call
  DataForSEO (the pilot and the first round of E4a) every run stops; the calls per tool are reported, the author
  reads the spend on the provider's panel and records the decision (`out/v06/dataforseo_checkpoint.json`,
  written by hand).
- **E4b starts only after the report on E4a has been reviewed.**

## Data rules

Those of `CLAUDE.md` and of the earlier protocols: inputs name only the author's domain, with the gate in the
tool wrapper and every refusal counted; raw DataForSEO responses are not published, only derived per-call
records (field paths, no values); the server's address appears in no versioned file, report or log; traces and
`out/` stay local. The names and definitions' token counts of the server's tools are published; the tool list
itself stays in `out/v06/`.

## Before the first paid run (no spend)

1. The function, with its tests; the non-regression tests showing that nothing changes when the option is off.
2. `experiments/run.py --v06`: arms R and D, the pilot, the ledger, the cap, the DataForSEO stop rule; the
   extended domain gate and the wrapper's refusals, with their tests.
3. `experiments/v06_preconditions.py`: the list D shows, from the Phase A traces; the check on the variant.
4. A dry run of every arm with a stub in place of the model and of the provider.
5. The analysis script, with every number of the report recomputable.

## AI assistance

As in the earlier protocols: the author set the question and the constraints; an AI coding agent (Claude Code)
proposed this protocol, will write the code, run the experiments and draft the report, under the author's
direction and with a second AI session reviewing each step.

## Deviations

None: the protocol is a draft.
