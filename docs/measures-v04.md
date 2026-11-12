# What was measured on the v0.4 work

The measures of `protocol-v04.md`, made on 2026-11-11: 100 runs of `gpt-5.6-terra` through tracetrim's
function-calling adapter. Results: `results/v04/e1.json`, `results/v04/e2.json`, recomputed from the runs by
`python -m experiments.analyze_v04`. The protocol was frozen before the first paid run; its five deviations, all
decided before the runs they concern, are at its end.

A further experiment, E3 of `protocol-v04-e3.md`, was made on 2026-11-12 on a second model, `gemini-3.8-flash`:
38 runs and 4 pilot runs, `results/v04/e3.json`, recomputed by `python -m experiments.analyze_v04 --e3`.

These are small experiments, on one model each. They say what happened in these runs, not what holds in general.

## The hypotheses, as stated before the runs

| | Hypothesis | Outcome | On how many cases |
|---|---|---|---|
| H1 | With the strategy text, gates and coverage of the prompt's requirements are no worse | **failed** | 4 comparisons of 5 hold; in C1, strategy with trimming passed the gates in 4 runs of 5 against 5 of 5 |
| H2 | On the billing case the strategy lowers the number of turns | **failed** | median 4 turns with it; minimum 3 without |
| H3 | — | removed before freezing | it concerned arms that are not run |
| H4 | The strategy does not undo the saving of trimming | **failed on C4**, holds on C1 | 2 cases |
| H5 | A field recovered with `expand` and written in the answer is kept from the next run | **holds** | 16 recoveries of 16, in 10 sequences |
| H6 | A field the agent uses and rewords needs promotion to come back | **not testable** | the model reworded it in 2 sequences only, both with promotion on, and wrote it at the next run |
| H7 | Sampling reaches a field the agent does not ask for | **failed** | one run per group: the requirement was met in both |
| H8 | On the second model, the median of turns with the strategy is below the fewest turns of any run without it | **failed** | holds on 2 cases of 4 (C1, C2); not on the simulated cases C4 and C5 |
| H9 | On the second model, the median share of turns with several calls is above the highest share of any run without it | **failed** | holds on 0 cases of 4: on each, a run without the strategy reached the same share or more |
| H10 | On the second model, gates and coverage are no worse with the strategy | **holds** | 2 cases of 2 (C4, C5) |
| H11 | On the second model, the strategy does not override the parameter values the prompts prescribe | **holds** | 9 checks of 9, in the 5 strategy runs of C1 and C2 |

## E1 — the strategy level of the skill

Four arms (strategy text × trimming), five runs each, on three agents: an editorial plan and a content-gap
briefing on a real search-data API (C1, C2), billing support on a simulated API (C4). On C2 only the two arms
without trimming: the configuration tracetrim builds there trims no tool. 50 runs.

- **On this model the strategy changed nothing that could be measured.** Turns, tool calls, billed tokens and cost
  stayed inside the ranges of the arms without it, in all three cases.
- **This model already issues calls together**: without the strategy, 77%, 68% and 77% of the turns that made a
  call made several. The effect of the strategy on a model that issues calls one at a time is the subject of E3.
- **The strategy did not override the prompts.** The parameter values the prompts prescribe were respected in all
  50 runs.
- **One run asked a question instead of working**, in the arm with strategy and trimming. One in 25 with the
  strategy, none in 25 without, none in the 60 live runs of the original study.
- **Trimming**, which E1 was not about: tool-output tokens down 44% on C1 and on C4, cost of a run down 30% and
  24%, gates passed in 5 runs of 5 in the trimmed arm of both.

The strategy stays off by default.

## E2 — adaptive learning

The task starts needing two fields the projection removes. Sequences of five runs in one work directory, seeded
with the eight runs of the original study. On the simulated case (a copy of its world, `experiments/simulated/v04`):
promotion off/on × sampling off/on, two sequences per arm, 40 runs. On the real case: the arms without promotion
(base, sampling), one sequence each, 10 runs.

- **The fields came back by themselves.** In every sequence the model got them once, wrote them, and from the
  next run they were in the filtered results with no `expand`: from the second run in 8 sequences of 10, from the
  third in 2. The requirement the variant adds was met in all 50 runs.
- **Promotion made no difference here**, because the model wrote the values it recovered: ordinary learning was
  enough. Whether promotion helps when a value is used and never written is still open.
- **Sampling only cost tokens here**: on the simulated case about 35% of tool-output tokens removed against 42%
  without it. The model never needed it: it asked with `expand` or found another way.
- **Another way to a removed field.** In two runs of the simulated case the model did not call `expand`: it called
  another tool, whose results are not trimmed, and read the fields there. That is a limit of the design on that
  case (H7 could hardly be tested), and a behaviour to know: on an API billed per call that route has a price.
  On the real case no other tool returns the two fields, and the model used `expand` in both sequences.
- **A tool was switched off once**, on the real case: in the first run of the base sequence the model asked back
  the values of the Trends tool, and the tool stopped being trimmed. The saving of that sequence went from 33% to
  between 26% and 38% in the later runs; in the sampling sequence, where it did not happen, it stayed at 35–40%.
- **`expand` requests that gave nothing back**: 10 of 38. Eight times the model made up or truncated the id of
  the result, asking for `items` or for the rows after the tenth, mostly on results that were not trimmed: it
  seemed to want more rows, not hidden fields. Twice it wrote an index (`data[0]`) where paths use `data[]`.

Promotion and sampling stay off by default.

## E3 — the strategy on a second model

`protocol-v04-e3.md`, frozen on 2026-11-12 before its first paid run; one deviation, on how the harness reaches
the model, changes no measure. 38 runs of `gemini-3.8-flash` after 4 pilot runs, with and without the strategy
text, no trimming. Four agents: billing support and a CI-failure report on simulated APIs (C4, C5), and the two
search-data agents of E1 (C1, C2) with **recorded outputs in place of the provider**, which was never called.
Results: `results/v04/e3.json`. Median and range per arm:

| Case | Arm | Runs | Turns | Tool calls | Turns with several calls, per run | Billed input tokens | Cost, USD |
|---|---|---:|---:|---:|---:|---:|---:|
| C4 | without | 8 | 3.5 [3–8] | 11.5 [10–14] | 88% [43–100] | 25,316 | 0.052 |
| C4 | strategy | 8 | 3 [3–3] | 10 [10–12] | 100% [100–100] | 17,196 | 0.042 |
| C5 | without | 6 | 17 [5–19] | 16 [13–18] | 0% [0–100] | 865,862 | 0.233 |
| C5 | strategy | 6 | 6 [5–8] | 16.5 [15–20] | 90% [57–100] | 391,018 | 0.198 |
| C2 | without | 3 | 6 [6–7] | 15 [14–16] | 100% [100–100] | 169,493 | 0.080 |
| C2 | strategy | 3 | 5 [4–7] | 14 [13–19] | 100% [100–100] | 131,489 | 0.082 |
| C1 | without | 2 | 14 [13–15] | 23 [21–25] | 47% [36–58] | 1,023,210 | 0.195 |
| C1 | strategy | 2 | 10.5 [10–11] | 18 [17–19] | 53% [50–56] | 658,204 | 0.192 |

The two hypotheses on the organisation of calls compare the median of one arm with the best run of the other,
and **both failed as stated**. What the runs show is different on the two simulated cases, and they are read apart.

- **C5, where this model issues one call per turn.** Without the strategy, five runs of six made every call in
  a turn of its own (14 to 19 turns); the sixth grouped them and took 5. With the strategy all six runs took
  5 to 8 turns, with the same number of calls. The median went from 17 turns to 6 and the billed input was
  more than halved; the cost of a run went down 15%, less than the tokens, because most of the input re-read at
  each turn is billed at the cached price. H8 fails here because of that sixth run: the strategy's median, 6,
  is not below the best run without it, 5.
- **C4, where it already issues calls together.** Little was left: three turns is the least this task needs
  (see below), and the runs without the strategy took 3 to 8, those with it always 3.
- **Completeness was not paid for it** (H10): gates passed in 8 runs of 8 and 6 of 6 in both arms of both cases,
  every requirement of the prompts covered.
- **The prompts were not overridden** (H11): the prescribed parameter values were respected in the 5 strategy
  runs of C1 and C2, 9 checks of 9.
- **C1 and C2** point the same way (14 turns to 10.5, 6 to 5) on 2 and 3 runs per arm, with recorded outputs:
  the model's call matched a recorded one exactly in 11% to 40% of the calls of a run on C1 and in 5% to 8% on
  C2, and got the nearest recorded output otherwise. They say how calls were organised, not whether the task
  was done well.

**How many calls could have been made earlier** (`results/v04/e3_independence.json`). This analysis is not in
the protocol: it was added after the runs, to tell a sequence the task imposes from one the model chose. A call
whose arguments name something a tool returned (a run, a job, a customer, an invoice) can only follow that
result; any other can be made at once. The longest such chain gives the fewest turns a run needed.

| Case | Arm | Turns, median | Fewest turns needed, median | Calls that could have been made in an earlier turn |
|---|---|---:|---:|---:|
| C4 | without | 3.5 | 3 | 19% |
| C4 | strategy | 3 | 3 | 0% |
| C5 | without | 17 | 4 | 77% |
| C5 | strategy | 6 | 4 | 44% |

On C5 seven calls in ten depend on an earlier result, but the chain is short (the runs, then their jobs, then
the logs): four turns were enough in every run. Without the strategy three calls in four were made later than
they could have been; with it, fewer than half. The rule reads identifiers only and cannot know that a model
wanted to read one log before asking for the next: it is a lower bound, not what a careful agent should do.

**Beside E1**, descriptively: two models, different prices, and on C1 and C2 live outputs there and recorded
ones here. Without the strategy `gpt-5.6-terra` took a median of 4 turns on C4, 5 on C2 and 9 on C1;
`gemini-3.8-flash` 3.5, 6 and 14. With it, 4, 4 and 10 against 3, 5 and 10.5. E1 had no C5: in the eight
runs of the original study that model took a median of 4 turns there.

## Spend

| | Runs | Model, USD |
|---|---:|---:|
| E1 | 50 | 12.46 |
| E2, simulated case | 40 | 2.63 |
| E2, real case | 10 | 3.99 |
| **Total** | **100** | **19.08** |

Three more attempts were cut short by errors of the model's service and repeated; their cost is in the totals. The
real cases made 813 calls to the search-data provider, billed apart.

E3 had its own cap, 15 USD: 42 runs, pilot included, for 5.24 USD of the second model, and no call to the
provider. One attempt was refused before anything was generated, at no cost; no request was repeated.

## What these measures do not show

- One model for trimming and adaptive learning; two for the strategy. Other models may organise calls
  differently again, call `expand` differently, reword more.
- E3 has 2 to 8 runs per arm, one task per case, and no trimming: whether the strategy and trimming work
  together on the second model was not measured.
- Few runs: five per arm in E1; in E2 two sequences per arm on the simulated case and one on the real case, which
  is a single trajectory per arm.
- The gates and the test of the added requirement look for identifiers, links, names and keywords.
- Only the function-calling adapter was exercised. That the MCP proxy takes the same decisions is covered by a
  test; whether a host loads the skill over MCP was not tried.
- Promotion where reuse cannot be seen, and sampling where the agent has no other way, were not put to the test:
  the model's behaviour did not create those situations.

## Commands

```bash
python -m experiments.analyze_v04                 # the tables, from the runs (they are not in git: see docs/traces-v04.md)
python -m experiments.analyze_v04 --e3            # the tables of E3
python -m experiments.e3_independence             # E3, post hoc: the calls that could have been made earlier
python -m experiments.run --e3 schedule           # the runs of protocol-v04-e3.md, in order
python -m experiments.run --v04 schedule          # the runs of the protocol, in order
python -m experiments.run --v04 dry-run /tmp/dry  # every arm with a stub in place of the model: no spend
python -m experiments.budget --v04                # the estimate made before the runs
python -m experiments.v04_preconditions           # what the protocol takes for granted
```

The last three need the traces of the original study (`TRACETRIM_TRACES`, or the Zenodo dataset at the repository root).
