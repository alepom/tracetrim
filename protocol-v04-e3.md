# Protocol of E3: the skill's strategy on a model that issues fewer calls together

Frozen on 2026-11-12 at commit `4dd774b`, approved by the author (REVISIONE-02a).

Status: **frozen on 2026-11-12**, approved by the author, before any paid run. The commit that froze it is
recorded in the line above by the commit that follows it. From that commit on, every change goes in "Deviations"
at the bottom, dated, with the reason.

This protocol follows `protocol-v04.md`, which stays as it is with its results. There, experiment E1 found that the
*strategy* level of the tracetrim skill changed nothing measurable on `gpt-5.6-terra`. That model already issued
several calls in 77%, 68% and 77% of the turns that made a call: there was little left to organise
(`docs/measures-v04.md`). E3 asks the same question of a model said to issue calls one at a time more often.

## What is under test

- The strategy text: `tracetrim.skill.text(strategy=True, done=False, expand=False)`, version 1, appended to the
  case's system prompt after a blank line. The same text as in E1.
- Nothing is trimmed in E3: no projection, no `expand`, no promotion, no sampling. E3 measures the strategy alone.
- The loop is the study's own (`experiments/v04.py`), unchanged, with another model behind it.

## The model

`gemini-3.8-flash` (Google, generally available since 2026-09-02), on Vertex AI, global endpoint, through the
`google-genai` SDK (3.2.0) and the adapter `experiments/gemini.py`. Thinking is left at the model's default
level, medium: it is how the model is ordinarily used, and a lower level would change how much it plans, which is
what E3 measures. Temperature and the like are ignored by this model. Model id and date are stored with every run.

How this model issues and receives tool calls, checked on Google's documentation and on the SDK on 2026-11-11,
because it is what E3 measures:

- one response may carry several function calls; the calling mode is left at its default, so the model decides;
- the adapter passes every call of a response on as one turn, in the model's order, and returns all their
  results together, in one content, in that order, each with the id and name of its call;
- the model's own turn is sent back as it came (its parts carry thought signatures).

A test checks this on the adapter with a stand-in for the SDK's client.

## Cases

| Case | Tools | Cost of the tools | What is evaluated |
|---|---|---|---|
| C4, billing support | simulated server, live | none | turns, calls, gates, coverage |
| C5, CI triage | simulated server, live | none | turns, calls, gates, coverage |
| C1, editorial plan | **recorded outputs** | none | turns and calls only |
| C2, content-gap briefing | **recorded outputs** | none | turns and calls only |

Prompts are those of the original study, unchanged. C4 and C5 use the simulated worlds of the original study.

**Recorded outputs** (`experiments/recorded.py`). For C1 and C2 the provider's server is replaced by a stand-in
that offers the same tools, with the descriptions and schemas recorded in the E1 runs, and answers each call with
an output recorded in an earlier run of the case (Phase A of the original study and E1; they stay local).

- Same tool and same arguments, once normalised (keys sorted, strings trimmed and lower-cased): that output. `exact`.
- Otherwise the output of the recorded call of the **same tool** with the most similar arguments: arguments are
  flattened into `path=word` tokens, similarity is the Jaccard index of the two sets, a tie goes to the first call
  of the pool (ordered by source run and position). `nearest`.
- A tool with no recorded call: an error text. `none`.
- The study's domain gate is applied first, as in the real wrapper.
- **The provider is never called**: the stand-in has no connection to it. The provider calls of E3 are counted
  and must be zero.

With a `nearest` output the content is not what the provider would return for those arguments. So **on C1 and C2
gates and coverage of the answer are not evaluated**: only how the model organises its calls. The share of exact
matches is reported for every run. On the E1 runs, answered from the pool without the run itself, the median
share of exact matches is 48% on C1 and 78% on C2; with another model it may be lower.

## Design

- **Arms**: R (no text) and S (the strategy), same prompt, tools and model.
- **Runs per arm**: 8 on C4, 6 on C5, 3 on C2, 2 on C1: 38 runs. More where a run is cheap; on C1 and C2 the long
  tool outputs are re-read at every turn, and a model that makes one call per turn re-reads them many times.
- **Pilot first**: 2 runs of R on C4 and 2 on C5, used in no result. **After the pilot the runs stop, always**, for
  the author's decision, recorded before any measured run. The pilot gives three things to decide on: the real
  cost of a run, which corrects the estimate of the rest; the share of the turns that make a call that make
  several (above one half, the margin for the strategy would be missing here too); any limit of the project's quota.
- **Order**: after the pilot, the simulated cases in rounds (one run of each arm of C4 and of C5, and again, so
  that a drift of the service hits the two arms alike) to their end; then C2 and C1, in rounds too. If the cap
  stops the runs it takes them from the cases with recorded outputs, which say less: they measure only how calls
  are organised, while C4 and C5 also measure completeness.

## Measures

Per run, computed by script from the trace and its meta file (`python -m experiments.analyze_v04 --e3`):

1. turns and tool calls;
2. calls per turn, and the share of the turns that make a call that make several;
3. billed tokens (input, cached input, output with thinking tokens) and cost;
4. runs that did not do the task: no tool call, or (C4, C5) a last message that is not the JSON asked for. The rule of E1;
5. gates and coverage of the prompt's explicit requirements, **on C4 and C5 only**;
6. the argument values the prompts prescribe (C1: every `limit` 10, every SERP `depth` 10, at most 5 keywords per
   Trends call; C2: every SERP call with Italy, Italian and `depth` 10). C4 and C5 prescribe none;
7. on C1 and C2, how each call was answered: exact, nearest, none.

Reported as median [min–max] per arm and case.

## Hypotheses (stated before any run)

Each is evaluated per case, with the rule of E1: a difference inside the other arm's range is not a difference.

- **H8 — fewer turns.** The median number of turns of S is below R's minimum.
- **H9 — more calls issued together.** The share of turns with several calls, computed per run: the median of S
  is above R's maximum.
- **H10 — completeness holds**, on C4 and C5: S passes every gate in at least as many runs as R, and its coverage
  median is not below R's minimum.
- **H11 — the strategy does not override the prompts**: every prescribed argument value is respected in every run
  of S, on C1 and C2.

H8 and H9 are confirmed only if they hold in all four cases; otherwise the result is "holds in k of 4 cases".
A hypothesis that fails is reported as failed. No hypothesis is added after the pilot.

**Comparison with E1**, descriptive and not a hypothesis: for C1, C2 and C4, the arms R and S of `gpt-5.6-terra`
beside those of this model, on turns, calls and share of turns with several calls. The runs differ in more than
the model (on C1 and C2, E1's tools were live and E3's are recorded), and the comparison says so.

## What cannot be concluded

- **One Gemini model**, at one thinking level. Other Gemini models, and other models that issue calls one at a
  time, are not measured.
- **The content of C1 and C2 is not real** where the output is a nearest one. A model that sees an answer that does
  not fit its question may call again: the number of calls on those cases is that of this set-up, not of the
  provider's API. The same holds, to a lesser degree, for exact matches: the outputs are of earlier dates.
- **Few runs**: 2 to 8 per arm. No significance test.
- **Billed tokens are not comparable with E1's**: another tokenizer, another price list, another way of caching.
- **The gates** of C4 and C5 look for identifiers and keywords.
- Only the function-calling adapter is exercised, and nothing is trimmed: E3 says nothing about trimming on this model.

## Budget and stop rule

Estimate: `python -m experiments.run --e3 estimate`, from the runs of the other model already measured, at this
model's prices. Two figures: the cost if this model works as the other did, and the cost if it makes one call per
turn, which is the case E3 is run for; no cache discount in either, output tokens doubled as an allowance for
thinking tokens.

| Case | Runs, pilot included | As measured on the other model, USD | One call per turn, USD |
|---|---:|---:|---:|
| C4 | 18 | 1.01 | 2.32 |
| C5 | 14 | 1.82 | 5.84 |
| C2 | 6 | 1.38 | 4.25 |
| C1 | 4 | 2.36 | 5.40 |
| **Total** | **42** | **6.58** | **17.81** |

**The upper figure is above the cap.** It assumes that every call takes a turn of its own and that nothing is
cached; if it came true the last runs would not start. Those are runs of C2 and C1, the cases with recorded
outputs: the simulated cases would be complete. The cost of a run is known after the pilot, and
is reported before the measured runs go on.

- Prices: `experiments/prices-e3.json`, read on 2026-11-11 from Google Cloud's Agent Platform (Vertex AI) pricing
  page, Gemini 3.8 Flash, standard pay-as-you-go, global endpoint: 0.75 USD per 1M input tokens, 0.075 cached,
  3.75 output, thinking tokens included. Introductory prices, valid through 2026-12-31; confirmed by the author
  on the pricing page on 2026-11-12. `experiments/prices.json` of the original study is not touched.
- **Cap 15 USD, warning at 10**, on a ledger of its own (`out/ledger-e3.jsonl`). The total is computed from the
  API's usage after every run, aborted ones included; at the cap no run starts, and a run that crosses it stops.
- **The provider of C1 and C2 is not called**: zero calls, zero spend.

## Data rules

Those of `protocol-v04.md`. The recorded outputs of C1 and C2 stay local and are sent to the model under test as
tool results, as they were to the other model; nothing of them is published. The service-account file is never
opened, printed or copied by the study's code: the SDK finds it through a variable.

## Before the first paid run (no spend)

Done: the adapter, the stand-in with recorded outputs, the schedule, the stop rules, the stop after the pilot, the
analysis with H8 to H11, and a dry run of the pilot and of every arm and case with a stub that issues several
calls in a turn, repeated at the commit that freezes this protocol.

## AI assistance

As in `protocol-v04.md`.

## Deviations

None yet.
